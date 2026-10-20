// SPDX-License-Identifier: GPL-3.0-or-later
// Native CPU-only process/dispatcher regression. No runtime, RPC or exec tests.
#define _GNU_SOURCE 1
#include <cstdint>
static void cpu_test_event(uintptr_t) noexcept;
#define DEARUST_RESOURCE_CPU_TEST_EVENT cpu_test_event
#ifndef CPU_NO_SIGNAL_ADAPTER
static int cpu_test_signal(int,int) noexcept;
#define DEARUST_RESOURCE_CPU_TEST_SIGNAL cpu_test_signal
#endif
#include "resource_cpu_dispatch.inc"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <vector>
static std::atomic<int> deny_allocations{0}, fail_create{0}, fail_settime{0}, fail_thread{0}, fail_signalfd{0}, fail_eventfd{0};
static std::atomic<unsigned> allocations_denied{0}, thread_calls{0};
[[maybe_unused]] static std::atomic<int> fail_identity{0};
static std::atomic<int> validate_stage{0}, exit_pidfd{-1};
static std::atomic<pid_t> exit_after_bind{0};
static std::atomic<unsigned> timer_deletions{0};
static std::atomic<unsigned> timer_creations{0}, future_wake_checks{0};
static std::atomic<bool> pause_event{false}, event_paused{false}, release_event{false};
static void cpu_test_event(uintptr_t) noexcept {
    if(pause_event.exchange(false)){
        event_paused.store(true,std::memory_order_release);
        while(!release_event.load(std::memory_order_acquire))usleep(100);
    }
}
static pthread_t receiver_thread;
#ifndef CPU_NO_MALLOC_INTERPOSE
extern "C" void* __libc_malloc(size_t) noexcept;
extern "C" void* malloc(size_t size) noexcept {
    if(deny_allocations.load(std::memory_order_relaxed)){
        allocations_denied.fetch_add(1,std::memory_order_relaxed);errno=ENOMEM;return nullptr;
    }
    return __libc_malloc(size);
}
#endif
extern "C" int __real_pthread_create(pthread_t*,const pthread_attr_t*,void*(*)(void*),void*) noexcept;
extern "C" int __wrap_pthread_create(pthread_t* thread,const pthread_attr_t* attr,void*(*entry)(void*),void* context) noexcept {
    thread_calls.fetch_add(1);if(fail_thread.exchange(0))return EAGAIN;
    const int result=__real_pthread_create(thread,attr,entry,context);
    if(!result)receiver_thread=*thread;
    return result;
}
extern "C" int __real_signalfd(int,const sigset_t*,int) noexcept;
extern "C" int __wrap_signalfd(int fd,const sigset_t* mask,int flags) noexcept {
    if(fail_signalfd.exchange(0)){errno=EMFILE;return -1;}return __real_signalfd(fd,mask,flags);
}
extern "C" int __real_eventfd(unsigned,int) noexcept;
extern "C" int __wrap_eventfd(unsigned initial,int flags) noexcept {
    if(fail_eventfd.exchange(0)){errno=EMFILE;return -1;}return __real_eventfd(initial,flags);
}
extern "C" int __real_timer_create(clockid_t,struct sigevent*,timer_t*) noexcept;
extern "C" int __real_timer_settime(timer_t,int,const struct itimerspec*,struct itimerspec*) noexcept;
extern "C" int __real_timer_delete(timer_t) noexcept;
extern "C" int __wrap_timer_delete(timer_t timer) noexcept {
    timer_deletions.fetch_add(1);return __real_timer_delete(timer);
}
static std::atomic<pid_t> receiver_tid{0};
static std::atomic<uintptr_t> armed_token{0};
extern "C" int __wrap_timer_create(clockid_t clock,struct sigevent* event,timer_t* timer) noexcept {
    timer_creations.fetch_add(1);
    if(const int error=fail_create.exchange(0)){errno=error;return -1;}
    if(event->sigev_notify!=SIGEV_THREAD_ID)std::abort();
    receiver_tid.store(event->_sigev_un._tid);armed_token.store(reinterpret_cast<uintptr_t>(event->sigev_value.sival_ptr));
    const int result=__real_timer_create(clock,event,timer);
    if(!result){
        validate_stage.store(1);
        if(const auto child=exit_after_bind.exchange(0)){
            if(syscall(SYS_pidfd_send_signal,exit_pidfd.load(),SIGKILL,nullptr,0))std::abort();
            while(waitpid(child,nullptr,0)<0&&errno==EINTR){}
        }
    }
    return result;
}
extern "C" int __real_timer_gettime(timer_t,struct itimerspec*) noexcept;
extern "C" int __wrap_timer_gettime(timer_t timer,struct itimerspec* value) noexcept {
    const int result=__real_timer_gettime(timer,value);
    if(!result&&(value->it_value.tv_sec||value->it_value.tv_nsec))future_wake_checks.fetch_add(1);
    return result;
}
#ifndef CPU_NO_SIGNAL_ADAPTER
static int cpu_test_signal(int fd,int signal) noexcept {
    if(!signal){
        if(validate_stage.exchange(2)!=1)std::abort();
        if(const int error=fail_identity.exchange(0))return -error;
    }
    const auto result=syscall(SYS_pidfd_send_signal,fd,signal,nullptr,0);
    return result<0?-errno:0;
}
#endif
extern "C" int __wrap_timer_settime(timer_t timer,int flags,const struct itimerspec* value,struct itimerspec* old) noexcept {
#ifndef CPU_NO_SIGNAL_ADAPTER
    if(validate_stage.load()!=2)std::abort();
#endif
    if(const int error=fail_settime.exchange(0)){errno=error;return -1;}
    return __real_timer_settime(timer,flags,value,old);
}
struct State { std::atomic<unsigned> busy{0},signals{0},ready{0}; };
static State* child_state;
static void caught(int) { child_state->signals.fetch_add(1,std::memory_order_relaxed); }
static void check(bool value,const char* message){if(!value){std::fprintf(stderr,"%s\n",message);std::abort();}}
static uint64_t cpu(pid_t pid){clockid_t id;check(clock_getcpuclockid(pid,&id)==0,"CPU clock lookup");timespec t{};check(clock_gettime(id,&t)==0,"CPU read");return static_cast<uint64_t>(t.tv_sec)*1000000000+t.tv_nsec;}
struct Descriptor { int fd;~Descriptor(){close(fd);} };
struct Child {
    pid_t pid; State* state;std::shared_ptr<Descriptor> owner;
    Child(){
        state=static_cast<State*>(mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0));check(state!=MAP_FAILED,"shared state");new(state)State;
        const auto parent=getpid();pid=fork();check(pid>=0,"owned child");
        if(!pid){if(prctl(PR_SET_PDEATHSIG,SIGKILL)||getppid()!=parent)_exit(92);child_state=state;struct sigaction a{};a.sa_handler=caught;sigemptyset(&a.sa_mask);if(sigaction(SIGXCPU,&a,nullptr))_exit(91);state->ready.store(1,std::memory_order_release);for(;;){if(state->busy.load(std::memory_order_acquire))asm volatile("" ::: "memory");else usleep(1000);}}
        const int fd=static_cast<int>(syscall(SYS_pidfd_open,pid,0));check(fd>=0,"pidfd");owner=std::make_shared<Descriptor>();owner->fd=fd;
        await([&]{return state->ready.load(std::memory_order_acquire)!=0;});
    }
    ~Child(){(void)syscall(SYS_pidfd_send_signal,owner->fd,SIGKILL,nullptr,0);while(waitpid(pid,nullptr,0)<0&&errno==EINTR){}munmap(state,4096);}
    unsigned signals(){return state->signals.load(std::memory_order_acquire);}
    void busy(){state->busy.store(1,std::memory_order_release);}
    template<class F>void await(F fn){for(unsigned n=0;n<5000;n++){if(fn())return;usleep(1000);}check(false,"bounded wait");}
};
static size_t threads(){DIR* dir=opendir("/proc/self/task");check(dir,"task inventory");size_t n=0;while(auto* entry=readdir(dir))if(entry->d_name[0]!='.')n++;closedir(dir);return n;}
static size_t fds(){DIR* dir=opendir("/proc/self/fd");check(dir,"FD inventory");size_t n=0;while(auto* entry=readdir(dir))if(entry->d_name[0]!='.')n++;closedir(dir);return n;}
static long rss(){FILE* file=fopen("/proc/self/smaps_rollup","r");check(file,"RSS inventory");char line[256];long result=-1;while(fgets(line,sizeof(line),file))if(sscanf(line,"Rss: %ld kB",&result)==1)break;check(fclose(file)==0&&result>=0,"RSS read");return result;}
int main(){
    Child first,second;const auto initial_threads=threads(),initial_fds=fds();const auto initial_rss=rss();
    sigset_t before{},after{};check(pthread_sigmask(SIG_SETMASK,nullptr,&before)==0,"mask snapshot");
    int error=0;auto invalid=DearustResources::CpuTimerDispatcher::create(SIGUSR1,error);check(!invalid&&error==-EINVAL,"explicit RT signal required");
    struct sigaction ignored{},previous{};ignored.sa_handler=SIG_IGN;sigemptyset(&ignored.sa_mask);
    check(sigaction(SIGRTMIN+7,&ignored,&previous)==0,"owned ignored disposition");
    auto discarded=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);check(!discarded&&error==-EINVAL,"ignored reservation rejected");
    check(sigaction(SIGRTMIN+7,&previous,nullptr)==0,"restore owned disposition");
    fail_thread.store(1);auto failed=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);check(!failed&&error==-EAGAIN,"receiver creation failure reported");check(threads()==initial_threads,"failed receiver leaves no thread");
    check(fds()==initial_fds,"failed receiver rolls back both FDs");
    for(bool first_fd:{true,false}){
        if(first_fd)fail_signalfd.store(1);else fail_eventfd.store(1);
        auto no_fd=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);
        check(!no_fd&&error==-EMFILE,"FD preparation failure reported");check(fds()==initial_fds,"FD preparation failure rollback");
    }
    auto service=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);check(service&&error==0,"prepare receiver");
    check(pthread_sigmask(SIG_SETMASK,nullptr,&after)==0&&memcmp(&before,&after,sizeof(before))==0,"caller mask unchanged");
    check(threads()==initial_threads+1,"exactly one receiver");
    check(fds()==initial_fds+2,"exactly two dispatcher FDs");
    pthread_attr_t receiver_attr;check(pthread_getattr_np(receiver_thread,&receiver_attr)==0,"receiver attributes");
    size_t stack_bytes=0,guard_bytes=0;check(pthread_attr_getstacksize(&receiver_attr,&stack_bytes)==0&&pthread_attr_getguardsize(&receiver_attr,&guard_bytes)==0,"receiver stack size");check(pthread_attr_destroy(&receiver_attr)==0,"attributes release");
    {
        std::vector<std::unique_ptr<DearustResources::CpuTimer>> many;
        for(unsigned n=0;n<128;n++){
            many.push_back(std::make_unique<DearustResources::CpuTimer>(first.pid,first.owner->fd,first.owner,n+100,service));
            check(many.back()->set_deadline(cpu(first.pid)+5000000000,n+1)==0,"shared owner arm");
        }
        check(threads()==initial_threads+1,"128 owners share one receiver");
        check(fds()==initial_fds+2,"128 owners share the same two dispatcher FDs");
        std::printf("CPU resources:initial_rss_kib=%ld, armed128_rss_kib=%ld, receiver_threads=1, service_fds=2, receiver_stack_bytes=%zu, receiver_guard_bytes=%zu\n",initial_rss,rss(),stack_bytes,guard_bytes);
    }
    std::weak_ptr<DearustResources::CpuTimerDispatcher> weak=service;
    rlimit signal_limit{};check(getrlimit(RLIMIT_SIGPENDING,&signal_limit)==0,"signal limit snapshot");
    {
    DearustResources::CpuTimer timer(first.pid,first.owner->fd,first.owner,1,service);
    check(timer.set_deadline(cpu(first.pid)+40000000,1)==0,"arm idle CPU");usleep(80000);check(first.signals()==0,"wall sleep does not consume CPU deadline");
    // User/tgkill signals carrying the same token must not impersonate SI_TIMER.
    siginfo_t spoof{};spoof.si_signo=SIGRTMIN+7;spoof.si_code=SI_QUEUE;spoof.si_pid=getpid();spoof.si_uid=getuid();spoof.si_value.sival_ptr=reinterpret_cast<void*>(armed_token.load());
    check(syscall(SYS_rt_tgsigqueueinfo,getpid(),receiver_tid.load(),SIGRTMIN+7,&spoof)==0,"queue unrelated token signal");
    check(syscall(SYS_tgkill,getpid(),receiver_tid.load(),SIGRTMIN+7)==0,"unrelated thread wake");usleep(10000);check(first.signals()==0,"non timer origins ignored");
    const auto creates=thread_calls.load();
    deny_allocations.store(1,std::memory_order_release);fail_thread.store(1);first.busy();
    first.await([&]{return first.signals()==1;});
    deny_allocations.store(0,std::memory_order_release);fail_thread.store(0);
    const auto expiry_denied=allocations_denied.load();
    check(expiry_denied==0,"expiry attempted no allocations");check(thread_calls.load()==creates,"expiry attempted no thread creation");check(timer.last_error()==0,"expiry success reported");
    auto end=cpu(first.pid)+80000000;first.await([&]{return cpu(first.pid)>=end;});check(first.signals()==1,"one-shot delivery");
    for(uint64_t n=2;n<102;n++){check(timer.set_deadline(cpu(first.pid)+10000000,n)==0,"rearm");check(timer.disable()==0,"cancel");}
    auto count=first.signals();end=cpu(first.pid)+50000000;first.await([&]{return cpu(first.pid)>=end;});check(first.signals()==count,"no sends after cancellation returns");
    {
        DearustResources::CpuTimer fresh(first.pid,first.owner->fd,first.owner,90,service);
        fail_create.store(EAGAIN);check(fresh.set_deadline(cpu(first.pid)+300000000,1)==-EAGAIN,"initial creation error");
        count=first.signals();check(fresh.set_deadline(cpu(first.pid)+30000000,2)==0,"retry private failed creation");first.await([&]{return first.signals()==count+1;});
    }
    count=first.signals();check(timer.set_deadline(cpu(first.pid)+30000000,110)==0,"old arm before failed rearm");
    fail_settime.store(EPERM);check(timer.set_deadline(cpu(first.pid)+300000000,111)==-EPERM,"rearm error retains old schedule");first.await([&]{return first.signals()==count+1;});
#ifndef CPU_NO_SIGNAL_ADAPTER
    for(int failure:{ESRCH,EPERM}){
        DearustResources::CpuTimer fresh(first.pid,first.owner->fd,first.owner,91,service);
        count=first.signals();check(timer.set_deadline(cpu(first.pid)+30000000,112)==0,"old independent binding before identity check denial");
        fail_identity.store(failure);
        check(fresh.set_deadline(cpu(first.pid)+300000000,113)==-failure,"post binding identity check failure");
        first.await([&]{return first.signals()==count+1;});
    }
#endif
#ifndef CPU_NO_MALLOC_INTERPOSE
    {
        DearustResources::CpuTimer fresh(first.pid,first.owner->fd,first.owner,92,service);
        count=first.signals();check(timer.set_deadline(cpu(first.pid)+30000000,115)==0,"old independent arm before OOM");
        deny_allocations.store(1);const auto oom=fresh.set_deadline(cpu(first.pid)+300000000,116);deny_allocations.store(0);
        check(oom==-ENOMEM,"initial allocation failure reported");first.await([&]{return first.signals()==count+1;});
    }
#endif
    const auto rearm_denied=allocations_denied.load(),rearm_created=timer_creations.load();
    deny_allocations.store(1);
    for(uint64_t n=0;n<200;n++)check(timer.set_deadline(cpu(first.pid)+5000000000,1000+n)==0,"allocation-free reuse");
    deny_allocations.store(0);
    check(allocations_denied.load()==rearm_denied&&timer_creations.load()==rearm_created,"reuse allocates and creates no kernel timer");
    count=first.signals();check(timer.set_deadline(cpu(first.pid)+30000000,1200)==0,"old arm before unrepresentable deadline");
    check(timer.set_deadline(uint64_t(INT64_MAX)+1,1201)==-EOVERFLOW&&timer.set_deadline(UINT64_MAX,1202)==-EOVERFLOW,"oversized physical CPU deadlines rejected");
    first.await([&]{return first.signals()==count+1;});
    check(timer.set_deadline(INT64_MAX,1203)==0,"maximum representable absolute deadline");
    {
        DearustResources::CpuTimer delayed(first.pid,first.owner->fd,first.owner,93,service);
        count=first.signals();event_paused.store(false);release_event.store(false);pause_event.store(true);
        check(delayed.set_deadline(cpu(first.pid)+20000000,1)==0,"real old notification arm");
        first.await([&]{return event_paused.load(std::memory_order_acquire);});
        first.state->busy.store(0,std::memory_order_release);usleep(3000);
        const auto token=armed_token.load();const auto checked=future_wake_checks.load();
        check(delayed.set_deadline(cpu(first.pid)+80000000,2)==0&&armed_token.load()==token,"same bound timer rearmed later");
        release_event.store(true,std::memory_order_release);
        first.await([&]{return future_wake_checks.load()>checked;});
        check(first.signals()==count,"delayed old wake cannot deliver new future deadline");
        first.busy();first.await([&]{return first.signals()==count+1;});
        count=first.signals();event_paused.store(false);release_event.store(false);pause_event.store(true);
        check(delayed.set_deadline(cpu(first.pid)+20000000,3)==0,"second real old notification");
        first.await([&]{return event_paused.load(std::memory_order_acquire);});
        const auto due=cpu(first.pid)+30000000;check(delayed.set_deadline(due,4)==0,"rearm while old notification is captured");
        first.await([&]{return cpu(first.pid)>=due+20000000;});
        release_event.store(true,std::memory_order_release);first.await([&]{return first.signals()==count+1;});
        end=cpu(first.pid)+50000000;first.await([&]{return cpu(first.pid)>=end;});
        check(first.signals()==count+1,"captured old plus queued current wake deliver current deadline exactly once");
    }
    check(timer.set_deadline(cpu(first.pid)+150000000,120)==0,"old future arm");check(timer.set_deadline(cpu(first.pid)+20000000,121)==0,"replacement arm");count=first.signals();first.await([&]{return first.signals()==count+1;});
    end=cpu(first.pid)+180000000;first.await([&]{return cpu(first.pid)>=end;});check(first.signals()==count+1,"old generation cannot send");
    second.busy();{
        DearustResources::CpuTimer other(second.pid,second.owner->fd,second.owner,2,service);
        check(other.set_deadline(cpu(second.pid)+30000000,1)==0,"independent owner");second.await([&]{return second.signals()==1;});check(first.signals()==count+1,"independent pidfd delivery");
    }
    {
        DearustResources::CpuTimer dead(second.pid,second.owner->fd,second.owner,3,service);
        const auto deadline=cpu(second.pid)+500000000;const auto deleted=timer_deletions.load();
        exit_pidfd.store(second.owner->fd);exit_after_bind.store(second.pid);
        check(dead.set_deadline(deadline,1)==-ESRCH,"pidfd liveness after real timer binding and owned target death");
        check(timer_deletions.load()==deleted+1,"rejected unarmed timer deleted");
        check(dead.disable()==0&&dead.set_deadline(0,2)<0,"retire and reject dead CPU clock");
    }
    count=first.signals();check(timer.set_deadline(0,130)==0,"already consumed immediate deadline");first.await([&]{return first.signals()==count+1;});
    timer.shutdown();check(timer.set_deadline(0,131)==-ESRCH,"terminal shutdown");timer.disable();check(timer.set_deadline(0,132)==-ESRCH,"disable cannot revive shutdown");
    check(threads()==initial_threads+1,"receiver count stable after churn");
    std::printf("CPU dispatcher:128 owners, one receiver, allocation/thread-free expiry, real CPU/one-shot, origins, failure retention, replacement/cancellation and shutdown pass; expiry_allocations_denied=%u, preparation_allocations_denied=%u\n",expiry_denied,allocations_denied.load());
    // Timer still pins service after explicit creator ownership is released.
    service.reset();check(!weak.expired(),"timer pins service");
    rlimit exhausted=signal_limit;exhausted.rlim_cur=0;check(setrlimit(RLIMIT_SIGPENDING,&exhausted)==0,"own signal queue limit");
    errno=0;check(syscall(SYS_rt_tgsigqueueinfo,getpid(),receiver_tid.load(),SIGRTMIN+7,&spoof)==-1&&errno==EAGAIN,"actual RT signal queue exhaustion");
    }
    check(weak.expired(),"last timer joins and releases dispatcher");
    check(setrlimit(RLIMIT_SIGPENDING,&signal_limit)==0,"restore own signal queue limit");
    check(threads()==initial_threads&&fds()==initial_fds,"complete receiver and FD teardown under queue exhaustion");
    const auto old_token=armed_token.load();
    auto recreated=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);check(recreated&&error==0,"recreate shared service");
    {
        DearustResources::CpuTimer fresh(first.pid,first.owner->fd,first.owner,200,recreated);
        check(fresh.set_deadline(cpu(first.pid)+500000000,1)==0&&armed_token.load()>old_token,"tokens not reused across service lifetimes");
    }
    recreated.reset();check(threads()==initial_threads&&fds()==initial_fds,"recreated service completely retired");
    std::printf("CPU teardown:thread_count=%zu, fd_count=%zu, rss_kib=%ld, RT queue exhausted during nonallocating service shutdown\n",threads(),fds(),rss());
    return 0;
}
