// SPDX-License-Identifier: GPL-3.0-or-later
// Exact preserved CPU helper under a real glibc expiry-allocation failure.
#define _GNU_SOURCE 1
#include "resource-cpu-timer.inc"
#include <cstdio>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/wait.h>
extern "C" void* __libc_malloc(size_t) noexcept;
static std::atomic<bool> deny_callback_allocation{false};
static std::atomic<unsigned> denied{0};
extern "C" void* malloc(size_t n) noexcept {
    if (deny_callback_allocation.load(std::memory_order_relaxed) && n == 16) {
        denied.fetch_add(1, std::memory_order_relaxed); errno = ENOMEM; return nullptr;
    }
    return __libc_malloc(n);
}
struct State { std::atomic<unsigned> signals{0}, ready{0}; };
static State* state;
static void caught(int) { state->signals.fetch_add(1, std::memory_order_relaxed); }
struct Descriptor { int fd; ~Descriptor() { close(fd); } };
static uint64_t cpu(pid_t pid) {
    clockid_t id; if (clock_getcpuclockid(pid, &id)) std::abort();
    timespec now{}; if (clock_gettime(id, &now)) std::abort();
    return static_cast<uint64_t>(now.tv_sec)*1000000000 + now.tv_nsec;
}
int main() {
    state=static_cast<State*>(mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0));
    if(state==MAP_FAILED)return 1;new(state)State;
    const auto parent=getpid();const pid_t child=fork();if(child<0)return 2;
    if(!child){
        if(prctl(PR_SET_PDEATHSIG,SIGKILL)||getppid()!=parent)_exit(10);
        struct sigaction action{};action.sa_handler=caught;sigemptyset(&action.sa_mask);
        if(sigaction(SIGXCPU,&action,nullptr))_exit(3);
        state->ready.store(1,std::memory_order_release);
        for(;;)asm volatile("" ::: "memory");
    }
    const int fd=static_cast<int>(syscall(SYS_pidfd_open,child,0));if(fd<0)return 4;
    auto owner=std::make_shared<Descriptor>();owner->fd=fd;
    while(!state->ready.load(std::memory_order_acquire))usleep(1000);
    int result=0;
    {
        DearustResources::CpuTimer timer(child,fd,owner,1);
        const auto deadline=cpu(child)+30000000;
        if(timer.set_deadline(deadline,1))result=5;
        deny_callback_allocation.store(true,std::memory_order_release);
        while(cpu(child)<deadline+100000000)usleep(1000);
        deny_callback_allocation.store(false,std::memory_order_release);
        const auto lost=state->signals.load(std::memory_order_acquire);
        const auto failures=denied.load();
        if(!failures||lost!=0||timer.last_error()!=0)result=6;
        // The dropped one-shot callback does not reappear after memory recovers.
        while(cpu(child)<deadline+200000000)usleep(1000);
        if(state->signals.load()!=0)result=7;
        if(timer.set_deadline(cpu(child)+30000000,2))result=8;
        for(unsigned n=0;n<3000&&state->signals.load()!=1;n++)usleep(1000);
        if(state->signals.load()!=1)result=9;
        std::printf("Original glibc expiry: denied_allocations=%u, signals_during_failed_expiry=%u, last_error=%d; recovered arm delivered=%u\n",failures,lost,timer.last_error(),state->signals.load());
    }
    (void)syscall(SYS_pidfd_send_signal,fd,SIGKILL,nullptr,0);
    while(waitpid(child,nullptr,0)<0&&errno==EINTR){}
    munmap(state,4096);return result;
}
