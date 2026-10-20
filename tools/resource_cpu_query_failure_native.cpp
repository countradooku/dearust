// SPDX-License-Identifier: GPL-3.0-or-later
// Reuse unchanged owned-child adapters; execute only the new CPU query failure.
#define main resource_cpu_existing_fixture_main
#define __wrap_timer_gettime resource_cpu_existing_gettime
#include "resource_cpu_dispatch_native.cpp"
#undef __wrap_timer_gettime
#undef main
static std::atomic<bool> fail_query{false};
static timer_t failed_timer;
extern "C" int __wrap_timer_gettime(timer_t timer,struct itimerspec* value) noexcept {
    if(fail_query.exchange(false)){
        failed_timer=timer;errno=EPERM;return -1;
    }
    return resource_cpu_existing_gettime(timer,value);
}
int main(){
    Child child;const auto initial_threads=threads(),initial_fds=fds();
    int error=0;auto service=DearustResources::CpuTimerDispatcher::create(SIGRTMIN+7,error);
    check(service&&error==0,"query test receiver");
    {
        DearustResources::CpuTimer timer(child.pid,child.owner->fd,child.owner,900,service);
        check(timer.set_deadline(cpu(child.pid)+20000000,1)==0,"arm query failure deadline");
        const auto token=armed_token.load();const auto created=timer_creations.load();
        fail_query.store(true,std::memory_order_release);child.busy();
        child.await([&]{return timer.last_error()==-EPERM;});
        check(child.signals()==0,"query failure cannot signal a deadline without proof");
        struct itimerspec physical{};
        check(__real_timer_gettime(failed_timer,&physical)==0&&!physical.it_value.tv_sec&&!physical.it_value.tv_nsec,"failed observation leaves actual expired one-shot schedule intact");
        const auto later=cpu(child.pid)+60000000;child.await([&]{return cpu(child.pid)>=later;});
        check(timer.last_error()==-EPERM&&child.signals()==0,"failure remains observable without false delivery");
        check(timer.set_deadline(cpu(child.pid)+30000000,2)==0,"rearm after failed observation");
        check(timer.last_error()==0&&armed_token.load()==token&&timer_creations.load()==created,"recovery uses same pinned binding and clears error");
        child.await([&]{return child.signals()==1;});
        check(timer.last_error()==0,"retained registry entry delivers recovered deadline");
        const auto end=cpu(child.pid)+50000000;child.await([&]{return cpu(child.pid)>=end;});
        check(child.signals()==1,"recovered generation delivers once");
    }
    service.reset();check(threads()==initial_threads&&fds()==initial_fds,"query failure owner completely retired");
    puts("CPU query failure: negative EPERM, unchanged expired kernel schedule, no false delivery, same-binding rearm/registry recovery and teardown pass");
    return 0;
}
