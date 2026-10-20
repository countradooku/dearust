// SPDX-License-Identifier: GPL-3.0-or-later
// Native owned-child CPU-engine measurements. No RPC or guest ownership activation.
#define _GNU_SOURCE 1
#include DEARUST_CPU_HELPER
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <vector>

static uint64_t stamp(clockid_t clock) {
    timespec t{};
    if (clock_gettime(clock, &t)) throw std::runtime_error("clock_gettime");
    return uint64_t(t.tv_sec)*1000000000+uint64_t(t.tv_nsec);
}
struct Shared {
    std::atomic<unsigned> mode{0}, signals{0}, ready{0};
    std::atomic<uint64_t> signal_cpu{0};
};
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(std::atomic<unsigned>::is_always_lock_free);
static Shared* signal_shared;
static void caught(int) {
    timespec t{};
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t)) _exit(90);
    signal_shared->signal_cpu.store(uint64_t(t.tv_sec)*1000000000+uint64_t(t.tv_nsec),std::memory_order_relaxed);
    signal_shared->signals.fetch_add(1,std::memory_order_release);
}
static void require(bool value,const char* message) { if (!value) throw std::runtime_error(message); }
struct Descriptor {
    int value=-1;
    ~Descriptor(){if(value>=0)close(value);}
};
struct Child {
    pid_t pid=-1;
    Shared* shared;
    std::shared_ptr<Descriptor> fd;
    clockid_t clock{};
    Child() {
        shared=static_cast<Shared*>(mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0));
        require(shared!=MAP_FAILED,"shared memory");new(shared) Shared;
        pid=fork();require(pid>=0,"fork owned child");
        if (!pid) {
            signal_shared=shared;
            struct sigaction action{};action.sa_handler=caught;sigemptyset(&action.sa_mask);
            if (::sigaction(SIGXCPU,&action,nullptr)) _exit(91);
            shared->ready.store(1,std::memory_order_release);
            for (;;) {
                const unsigned mode=shared->mode.load(std::memory_order_acquire);
                if (mode==2) _exit(0);
                if (!mode) usleep(1000); else asm volatile("" ::: "memory");
            }
        }
        try {
            fd=std::make_shared<Descriptor>();
            fd->value=int(syscall(SYS_pidfd_open,pid,0));require(fd->value>=0,"owned pidfd");
            require(clock_getcpuclockid(pid,&clock)==0,"owned CPU clock");
            await([&]{return shared->ready.load(std::memory_order_acquire)!=0;});
        }catch(...) {
            shared->mode.store(2,std::memory_order_release);
            int status;while(waitpid(pid,&status,0)<0 && errno==EINTR) {}
            munmap(shared,4096);throw;
        }
    }
    template<class F> void await(F predicate) {
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(5);
        while(!predicate()) {
            require(std::chrono::steady_clock::now()<end,"bounded child wait");usleep(100);
        }
    }
    ~Child() {
        if (pid>0) {
            shared->mode.store(2,std::memory_order_release);
            int status;while(waitpid(pid,&status,0)<0 && errno==EINTR) {}
        }
        munmap(shared,4096);
    }
};
static std::string proc() {
    std::ifstream input("/proc/self/status");std::string line;
    std::ostringstream text;text<<"{";bool first=true;
    while(std::getline(input,line)) {
        const auto colon=line.find(':');if(colon==std::string::npos)continue;
        const auto key=line.substr(0,colon);
        if(key!="VmSize"&&key!="VmRSS"&&key!="VmData"&&key!="VmHWM"&&key!="Threads")continue;
        uint64_t value=0;std::istringstream(line.substr(colon+1))>>value;
        if(!first)text<<",";first=false;text<<"\""<<key<<"\":"<<value;
    }
    unsigned count=0;DIR* directory=opendir("/proc/self/fd");require(directory,"proc descriptors");
    while(auto* entry=readdir(directory)) if(entry->d_name[0]!='.')++count;
    closedir(directory);text<<",\"fds_including_observer\":"<<count<<"}";return text.str();
}
int main() {
    try {
        Child child;
        const auto before=proc();
        const uint64_t setup_cpu=stamp(CLOCK_PROCESS_CPUTIME_ID),setup_wall=stamp(CLOCK_MONOTONIC);
#if DEARUST_CPU_DISPATCH
        int error=0;
        auto dispatcher=DearustResources::CpuTimerDispatcher::create(SIGRTMAX,error);
        require(bool(dispatcher)&&error==0,"dispatcher preparation");
        auto timer=std::make_unique<DearustResources::CpuTimer>(child.pid,child.fd->value,child.fd,1,dispatcher);
#else
        auto timer=std::make_unique<DearustResources::CpuTimer>(child.pid,child.fd->value,child.fd,1);
#endif
        require(timer->set_deadline(stamp(child.clock)+60000000000,1)==0,"cold future arm");
        const auto setup_cpu_ns=stamp(CLOCK_PROCESS_CPUTIME_ID)-setup_cpu;
        const auto setup_wall_ns=stamp(CLOCK_MONOTONIC)-setup_wall;
        const auto steady=proc();
        const uint64_t rearm_cpu=stamp(CLOCK_PROCESS_CPUTIME_ID),rearm_wall=stamp(CLOCK_MONOTONIC);
        constexpr unsigned rearms=1000,expiries=64;
        for(unsigned i=0;i<rearms;++i)require(timer->set_deadline(stamp(child.clock)+60000000000,i+2)==0,"future rearm");
        const auto rearm_cpu_ns=stamp(CLOCK_PROCESS_CPUTIME_ID)-rearm_cpu;
        const auto rearm_wall_ns=stamp(CLOCK_MONOTONIC)-rearm_wall;
        child.shared->mode.store(1,std::memory_order_release);
        std::vector<uint64_t> overruns;overruns.reserve(expiries);
        const uint64_t expiry_cpu=stamp(CLOCK_PROCESS_CPUTIME_ID),expiry_wall=stamp(CLOCK_MONOTONIC),child_cpu=stamp(child.clock);
        for(unsigned i=0;i<expiries;++i) {
            const unsigned prior=child.shared->signals.load(std::memory_order_acquire);
            const uint64_t deadline=stamp(child.clock)+5000000;
            require(timer->set_deadline(deadline,1002+i)==0,"expiry arm");
            child.await([&]{return child.shared->signals.load(std::memory_order_acquire)>prior;});
            require(child.shared->signals.load(std::memory_order_acquire)==prior+1,"one shot delivery");
            const uint64_t delivered=child.shared->signal_cpu.load(std::memory_order_relaxed);
            require(delivered>=deadline,"no premature CPU delivery");overruns.push_back(delivered-deadline);
        }
        const auto expiry_cpu_ns=stamp(CLOCK_PROCESS_CPUTIME_ID)-expiry_cpu;
        const auto expiry_wall_ns=stamp(CLOCK_MONOTONIC)-expiry_wall;
        const auto child_cpu_ns=stamp(child.clock)-child_cpu;
        child.shared->mode.store(0,std::memory_order_release);
        require(timer->last_error()==0 && timer->disable()==0,"expiry diagnostics and cancel");
        const auto post_expiry=proc();
        const uint64_t destroy_cpu=stamp(CLOCK_PROCESS_CPUTIME_ID),destroy_wall=stamp(CLOCK_MONOTONIC);
        timer.reset();
#if DEARUST_CPU_DISPATCH
        dispatcher.reset();
#endif
        const auto destroy_cpu_ns=stamp(CLOCK_PROCESS_CPUTIME_ID)-destroy_cpu;
        const auto destroy_wall_ns=stamp(CLOCK_MONOTONIC)-destroy_wall;
        const auto after=proc();
        rusage usage{};require(getrusage(RUSAGE_SELF,&usage)==0,"self usage");
        std::cout<<"{\"setup_cpu_ns\":"<<setup_cpu_ns<<",\"setup_wall_ns\":"<<setup_wall_ns
                 <<",\"rearms\":"<<rearms<<",\"rearm_cpu_ns\":"<<rearm_cpu_ns<<",\"rearm_wall_ns\":"<<rearm_wall_ns
                 <<",\"expiries\":"<<expiries<<",\"expiry_cpu_ns\":"<<expiry_cpu_ns<<",\"expiry_wall_ns\":"<<expiry_wall_ns
                 <<",\"child_cpu_ns\":"<<child_cpu_ns<<",\"destroy_cpu_ns\":"<<destroy_cpu_ns<<",\"destroy_wall_ns\":"<<destroy_wall_ns
                 <<",\"rusage_self_maxrss_kib\":"<<usage.ru_maxrss<<",\"before\":"<<before<<",\"steady\":"<<steady
                 <<",\"post_expiry\":"<<post_expiry<<",\"after\":"<<after<<",\"cpu_overrun_ns\":[";
        for(unsigned i=0;i<overruns.size();++i){if(i)std::cout<<",";std::cout<<overruns[i];}
        std::cout<<"]}\n";return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
