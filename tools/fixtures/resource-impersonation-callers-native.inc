static std::atomic<int> foreignFailure{0};
static void foreignWork(){if(foreignFailure==1)throw std::system_error(EIO,std::generic_category());if(foreignFailure==2)throw std::bad_alloc();}
static void dtape_kqchan_mach_port_modify(void*,uint64_t,uint64_t,uint64_t){foreignWork();}
static bool dtape_kqchan_mach_port_fill(void*,dserver_kqchan_reply_mach_port_read_t*,uint64_t,uint64_t){foreignWork();return false;}
static bool dtape_kqchan_mach_port_has_events(void*){return false;}
namespace DarlingServer {
class Kqchan {public:class MachPort;};
class Kqchan::MachPort:public std::enable_shared_from_this<Kqchan::MachPort>{
public:
    std::mutex _sendingMutex;
    union Reply {dserver_kqchan_reply_mach_port_modify_t modify;dserver_kqchan_reply_mach_port_read_t read;Reply():read{}{};} _preparedMachReply;
    size_t _preparedMachReplyLength=0;
    bool _preparedMachRequestInFlight=true;
    std::atomic<int> _preparedMachTransportError{0};
    void* _dtapeKqchan=nullptr;void* _monitor=nullptr;
    int sockets[2];
    MachPort(){assert(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,sockets)==0);}
    ~MachPort(){close(sockets[0]);close(sockets[1]);}
    bool _flushPreparedMachOutputLocked() noexcept {
        assert(send(sockets[0],&_preparedMachReply,_preparedMachReplyLength,MSG_DONTWAIT)==static_cast<ssize_t>(_preparedMachReplyLength));
        _preparedMachReplyLength=0;return true;
    }
    void _notify(){}
    void _completePreparedMachModify(int error=0) noexcept;
    void _completePreparedMachRead(const dserver_kqchan_reply_mach_port_read_t&) noexcept;
    void _modify(uint64_t,uint64_t,uint64_t,pid_t);
    void _read(uint64_t,uint64_t,pid_t);
    int receiveCode(bool read){
        dserver_kqchan_reply_mach_port_read_t reply={};
        const ssize_t count=recv(sockets[1],&reply,sizeof(reply),0);
        assert(count==static_cast<ssize_t>(read?sizeof(reply):sizeof(dserver_kqchan_reply_mach_port_modify_t)));
        assert(reply.header.number==(read?dserver_kqchan_msgnum_mach_port_read:dserver_kqchan_msgnum_mach_port_modify));
        assert(!_preparedMachRequestInFlight);return reply.header.code;
    }
};
}
// @TESTS@
#ifndef BEFORE_IMPERSONATION_FIX
    for(bool read:{false,true}){
        // Missing registry target is an error reply, without enqueuing work.
        auto channel=std::make_shared<Kqchan::MachPort>();threadRegistry().target.reset();
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        assert(channel->receiveCode(read)==-ESRCH);
    }
    for(bool read:{false,true}){
        // A retired target still retained by a captured registry lookup must
        // reject before touching foreign state and contain the new exception.
        auto target=std::make_shared<Thread>();target->notifyDead();
        auto channel=std::make_shared<Kqchan::MachPort>();threadRegistry().target=target;
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        assert(channel->receiveCode(read)==-ESRCH&&target->releases==1);
        threadRegistry().target.reset();
    }
    for(bool read:{false,true}){
        // A second claimant blocked behind A wakes on logical death and sends
        // ESRCH. A's established claim still pins C until its later release.
        auto target=std::make_shared<Thread>(),a=std::make_shared<Thread>();a->impersonate(target);
        auto channel=std::make_shared<Kqchan::MachPort>();threadRegistry().target=target;
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        waitFor([&]{return target->_runningCondvar.waiters.load()==1;});
        target->notifyDead();assert(target->releases==0&&target->_dtapeThread);
        // Death publishes to the same claim condition-variable predicate.
        @WAKE_WAITERS@
        assert(channel->receiveCode(read)==-ESRCH);
        a->impersonate(nullptr);assert(target->releases==1);
        threadRegistry().target.reset();
    }
    for(bool read:{false,true})for(int failure:{1,2}){
        auto target=std::make_shared<Thread>();threadRegistry().target=target;
        auto channel=std::make_shared<Kqchan::MachPort>();foreignFailure=failure;
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        assert(channel->receiveCode(read)==-(failure==1?EIO:ENOMEM));foreignFailure=0;
        {std::shared_lock lock(target->_rwlock);assert(!target->_running&&!target->_impersonationActive);}
        threadRegistry().target.reset();
    }
    for(bool read:{false,true}){
        auto target=std::make_shared<Thread>();threadRegistry().target=target;
        auto channel=std::make_shared<Kqchan::MachPort>();failAsync=true;
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        assert(channel->receiveCode(read)==-ENOMEM);failAsync=false;
        threadRegistry().target.reset();
    }
    for(bool read:{false,true}){
        auto target=std::make_shared<Thread>();threadRegistry().target=target;
        auto channel=std::make_shared<Kqchan::MachPort>();
        if(read)channel->_read(0,0,123);else channel->_modify(0,0,0,123);
        assert(channel->receiveCode(read)==(read?0xdead:0));
        threadRegistry().target.reset();
    }
#endif
