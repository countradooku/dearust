// SPDX-License-Identifier: GPL-3.0-or-later
// Exact generated lazy adapter, real own-process CPU timers, no RPC.
#define _GNU_SOURCE 1
#include DEARUST_CPU_HELPER
#include <dirent.h>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <iostream>
#include <chrono>
#include <sys/stat.h>

@FACTORY@

static std::atomic<int> create_failure{0}, set_failure{0};
extern "C" int __real_timer_create(clockid_t, sigevent*, timer_t*) noexcept;
extern "C" int __real_timer_settime(timer_t, int, const itimerspec*, itimerspec*) noexcept;
extern "C" int __wrap_timer_create(clockid_t clock, sigevent* event, timer_t* result) noexcept {
    if(int error=create_failure.exchange(0)) {errno=error;return -1;}
    return __real_timer_create(clock,event,result);
}
extern "C" int __wrap_timer_settime(timer_t timer,int flags,const itimerspec* value,itimerspec* old) noexcept {
    if(int error=set_failure.exchange(0)) {errno=error;return -1;}
    return __real_timer_settime(timer,flags,value,old);
}
static std::atomic<unsigned> signals{0};
static_assert(std::atomic<unsigned>::is_always_lock_free);
static void caught(int) {signals.fetch_add(1,std::memory_order_relaxed);}
static void check(bool good,const char* text){if(!good)throw std::runtime_error(text);}
struct Descriptor {int value=-1;~Descriptor(){if(value>=0)close(value);}};
static uint64_t cpu(){timespec t{};check(clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&t)==0,"CPU clock");return uint64_t(t.tv_sec)*1000000000+t.tv_nsec;}
static unsigned threads(){std::ifstream input("/proc/self/status");std::string line;while(std::getline(input,line)){if(line.rfind("Threads:",0)==0){unsigned n=0;std::istringstream(line.substr(8))>>n;return n;}}throw std::runtime_error("thread count");}
static unsigned descriptors(){DIR* dir=opendir("/proc/self/fd");check(dir,"descriptor directory");unsigned count=0;while(auto* entry=readdir(dir))if(entry->d_name[0]!='.')++count;closedir(dir);return count;}
static void burn(uint64_t nanoseconds){uint64_t end=cpu()+nanoseconds;while(cpu()<end)asm volatile("" ::: "memory");}
static void delivered(unsigned expected){auto end=std::chrono::steady_clock::now()+std::chrono::seconds(3);while(signals.load(std::memory_order_relaxed)<expected){check(std::chrono::steady_clock::now()<end,"CPU delivery wait");asm volatile("" ::: "memory");}check(signals.load()==expected,"one signal");}
static void restored(unsigned count,unsigned fds){auto end=std::chrono::steady_clock::now()+std::chrono::seconds(3);while(threads()!=count){check(std::chrono::steady_clock::now()<end,"receiver teardown");usleep(100);}check(descriptors()==fds,"prepared descriptors returned");}
int main(){try{
    struct sigaction action{};action.sa_handler=caught;sigemptyset(&action.sa_mask);check(sigaction(SIGXCPU,&action,nullptr)==0,"signal handler");
    auto fd=std::make_shared<Descriptor>();fd->value=int(syscall(SYS_pidfd_open,getpid(),0));check(fd->value>=0,"own pidfd");
    const unsigned before_threads=threads(),before_fds=descriptors();
    {ResourceLazyCpuTimer never(getpid(),fd->value,fd,1);check(never.last_error()==0&&never.disable()==0,"unused timer");check(threads()==before_threads&&descriptors()==before_fds,"no idle service");never.shutdown();check(never.set_deadline(0,1)==-ESRCH,"terminal before first arm");check(never.disable()==0&&never.set_deadline(0,2)==-ESRCH,"disable cannot reopen terminal");}
    {ResourceLazyCpuTimer failing(getpid(),fd->value,fd,2);create_failure=EAGAIN;check(failing.set_deadline(cpu()+60000000000,1)==-EAGAIN,"first arm failure");check(failing.last_error()==0,"unpublished failure leaves empty state");}
    restored(before_threads,before_fds);
    {
        ResourceLazyCpuTimer first(getpid(),fd->value,fd,3),second(getpid(),fd->value,fd,4);
        check(first.set_deadline(cpu()+60000000000,1)==0,"first prepared arm");
        check(threads()==before_threads+1&&descriptors()==before_fds+2,"one shared prepared service");
        check(second.set_deadline(cpu()+60000000000,1)==0,"second arm");
        check(threads()==before_threads+1&&descriptors()==before_fds+2,"second owner reuses service");
        check(first.set_deadline(cpu()+10000000,2)==0,"earlier live arm");
        set_failure=EPERM;check(first.set_deadline(cpu()+1000000000,3)==-EPERM,"failed replacement");delivered(1);
        check(first.set_deadline(cpu()+10000000,4)==0&&first.disable()==0,"cancel prepared deadline");burn(50000000);check(signals.load()==1,"no canceled generation signal");
        first.shutdown();check(first.set_deadline(0,5)==-ESRCH&&first.disable()==0,"terminal active owner");
        check(second.disable()==0&&second.last_error()==0,"independent owner");
    }
    restored(before_threads,before_fds);
    std::cout<<"lazy CPU adapter: no idle worker, terminal startup, first-arm rollback, shared receiver, replacement retention, cancel and teardown passed\n";return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}}
