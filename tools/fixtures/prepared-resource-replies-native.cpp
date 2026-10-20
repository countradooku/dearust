#include <darlingserver/message.hpp>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <new>
#include <thread>
#include <unistd.h>

using namespace DarlingServer;
static std::atomic<bool> denied{false};
static std::atomic<unsigned> attempts{0};
static bool fail() { if (!denied.load()) return false; ++attempts; return true; }
extern "C" void* __real_malloc(size_t);
extern "C" void* __real_calloc(size_t,size_t);
extern "C" void* __real_realloc(void*,size_t);
extern "C" void* __wrap_malloc(size_t n) { return fail() ? nullptr : __real_malloc(n); }
extern "C" void* __wrap_calloc(size_t n,size_t s) { return fail() ? nullptr : __real_calloc(n,s); }
extern "C" void* __wrap_realloc(void* p,size_t n) { return fail() ? nullptr : __real_realloc(p,n); }
void* operator new(size_t n) { if (fail()) throw std::bad_alloc(); if (auto p=__real_malloc(n)) return p; throw std::bad_alloc(); }
void* operator new[](size_t n) { return ::operator new(n); }
void operator delete(void* p) noexcept { free(p); }
void operator delete[](void* p) noexcept { free(p); }
void operator delete(void* p,size_t) noexcept { free(p); }
void operator delete[](void* p,size_t) noexcept { free(p); }
struct Reply {
    PreparedReplyNode node{sizeof(int)};
    std::atomic<unsigned>* deaths;
    explicit Reply(std::atomic<unsigned>& d): deaths(&d) {}
    ~Reply() { ++*deaths; }
};
static Message message(const std::shared_ptr<Reply>& reply,int value) {
    Message result=reply->node.take(reply);
    memcpy(result.data().data(), &value, sizeof(value));
    return result;
}
static Message ordinary(int value) {
    Message result(sizeof(value),0);
    memcpy(result.data().data(),&value,sizeof(value)); return result;
}
static int value(const Message& m) { int result; memcpy(&result,m.data().data(),sizeof(result)); return result; }
static void moves_and_drop() {
    std::atomic<unsigned> deaths{0};
    auto reply=std::make_shared<Reply>(deaths);
    int pipes[2]; assert(pipe2(pipes,O_CLOEXEC)==0);
    Message original=message(reply,7);
    original.pushDescriptor(pipes[0]);
    reply.reset(); denied=true;
    { std::optional<Message> saved(std::move(original));
      Message moved(std::move(*saved)); saved.reset();
      std::optional<Message> deferred(std::move(moved));
      assert(value(*deferred)==7); deferred.reset(); }
    denied=false;
    assert(deaths==1); assert(fcntl(pipes[0],F_GETFD)==-1 && errno==EBADF); close(pipes[1]);
}
static void fifo_and_shutdown() {
    std::atomic<unsigned> deaths{0};
    auto a=std::make_shared<Reply>(deaths), b=std::make_shared<Reply>(deaths), c=std::make_shared<Reply>(deaths);
    MessageQueue queue;
    queue.push(ordinary(1)); queue.push(message(a,2)); queue.push(ordinary(3));
    queue.push(message(b,4)); queue.push(message(c,5)); queue.push(ordinary(6));
    a.reset(); b.reset(); c.reset();
    denied=true;
    for (int i=1;i<=6;++i) { auto m=queue.pop(); assert(m && value(*m)==i); }
    assert(queue.empty()); denied=false; assert(deaths==3);
    auto d=std::make_shared<Reply>(deaths);
    { MessageQueue shutdown; denied=true; shutdown.push(message(d,9)); d.reset(); }
    denied=false; assert(deaths==4);
}
static void backpressure_and_fd() {
    constexpr size_t count=128;
    std::atomic<unsigned> deaths{0};
    std::shared_ptr<Reply> replies[count];
    for (auto& reply:replies) reply=std::make_shared<Reply>(deaths);
    int sockets[2]; assert(socketpair(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0,sockets)==0);
    int small=2048; assert(setsockopt(sockets[0],SOL_SOCKET,SO_SNDBUF,&small,sizeof(small))==0);
    MessageQueue queue;
    std::atomic<unsigned> arrivals{0}; queue.setMessageArrivalNotificationCallback([&]{++arrivals;});
    int pipes[2]; assert(pipe2(pipes,O_CLOEXEC)==0);
    Message first=message(replies[0],0); first.pushDescriptor(pipes[0]);
    denied=true;
    queue.push(std::move(first)); replies[0].reset();
    for (size_t i=1;i<count;++i) { queue.push(message(replies[i],i)); replies[i].reset(); }
    assert(!queue.sendMany(sockets[0])); assert(!queue.empty());
    size_t received=0; bool gotfd=false;
    while (received<count) {
        int number=-1; char control[CMSG_SPACE(sizeof(int))]={};
        struct iovec io{&number,sizeof(number)}; struct msghdr hdr{};
        hdr.msg_iov=&io; hdr.msg_iovlen=1; hdr.msg_control=control; hdr.msg_controllen=sizeof(control);
        ssize_t result=recvmsg(sockets[1],&hdr,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);
        if (result<0) { assert(errno==EAGAIN); queue.sendMany(sockets[0]); continue; }
        assert(result==sizeof(number) && number==int(received));
        if (!received) { auto* c=CMSG_FIRSTHDR(&hdr); assert(c && c->cmsg_type==SCM_RIGHTS);
          int fd; memcpy(&fd,CMSG_DATA(c),sizeof(fd)); assert(fcntl(fd,F_GETFD)&FD_CLOEXEC); close(fd); gotfd=true; }
        ++received;
    }
    denied=false;
    assert(gotfd && queue.empty() && deaths==count && arrivals==count);
    close(pipes[1]); close(sockets[0]); close(sockets[1]);
}
static void peer_gone_and_direct() {
    std::atomic<unsigned> deaths{0}; auto reply=std::make_shared<Reply>(deaths);
    MessageQueue queue; std::atomic<unsigned> callbacks{0};
    queue.setMessageArrivalNotificationCallback([&]{++callbacks;});
    int sockets[2]; assert(socketpair(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0,sockets)==0);
    close(sockets[1]); denied=true;
    queue.pushAndSend(message(reply,1),sockets[0]); reply.reset();
    denied=false; assert(queue.empty() && deaths==1); close(sockets[0]);
}
static void concurrent_producers() {
    constexpr size_t count=1024, producers=4;
    std::atomic<unsigned> deaths{0}, done{0}, arrivals{0};
    std::atomic<bool> start{false};
    std::shared_ptr<Reply> replies[count];
    bool seen[count]={};
    for (auto& reply:replies) reply=std::make_shared<Reply>(deaths);
    MessageQueue queue;
    // The large function capture would allocate if copied during enqueue.
    std::array<int,128> padding{}; padding[17]=23;
    queue.setMessageArrivalNotificationCallback([&,padding] {
        assert(padding[17]==23); (void)queue.empty(); ++arrivals;
    });
    std::thread workers[producers];
    for (size_t p=0;p<producers;++p) workers[p]=std::thread([&,p] {
        while (!start.load()) std::this_thread::yield();
        for (size_t i=p;i<count;i+=producers) queue.push(message(replies[i],i));
        ++done;
    });
    denied=true; start=true;
    size_t received=0;
    while (received<count) {
        auto next=queue.pop();
        if (!next) { std::this_thread::yield(); continue; }
        const int id=value(*next); assert(id>=0 && id<int(count) && !seen[id]);
        seen[id]=true; ++received;
    }
    for (auto& worker:workers) worker.join();
    for (auto& reply:replies) reply.reset();
    assert(done==producers && arrivals==count && deaths==count && queue.empty());
    denied=false;
}
static void callback_replacement() {
    MessageQueue queue;
    struct Capture {
        MessageQueue* queue;
        unsigned* destroyed;
        ~Capture() { assert(queue->empty()); ++*destroyed; }
    };
    unsigned destroyed=0;
    auto capture=std::make_shared<Capture>(); capture->queue=&queue; capture->destroyed=&destroyed;
    queue.setMessageArrivalNotificationCallback([capture] {});
    capture.reset();
    queue.setMessageArrivalNotificationCallback(nullptr);
    assert(destroyed==1);
}
static void mixed_batch() {
    std::atomic<unsigned> deaths{0};
    auto a=std::make_shared<Reply>(deaths), b=std::make_shared<Reply>(deaths), c=std::make_shared<Reply>(deaths);
    MessageQueue queue;
    queue.push(ordinary(1)); queue.push(message(a,2)); queue.push(ordinary(3));
    queue.push(message(b,4)); queue.push(message(c,5)); queue.push(ordinary(6));
    a.reset(); b.reset(); c.reset();
    int sockets[2]; assert(socketpair(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0,sockets)==0);
    denied=true; assert(queue.sendMany(sockets[0]));
    for (int expected=1;expected<=6;++expected) {
        int received=0; assert(recv(sockets[1],&received,sizeof(received),0)==sizeof(received));
        assert(received==expected);
    }
    assert(queue.empty() && deaths==3); denied=false;
    close(sockets[0]); close(sockets[1]);
}
int main() {
    Message::initializeServerCredentials();
    moves_and_drop(); fifo_and_shutdown(); backpressure_and_fd(); peer_gone_and_direct(); concurrent_producers(); callback_replacement(); mixed_batch();
    assert(attempts==0);
    puts("PASS exact Message moves, dropped owners, mixed FIFO, shutdown, EAGAIN, SCM_RIGHTS, direct send, dead peer, 1024 concurrent replies, reentrant large callback; zero denied allocations");
}
