// Exact selected C methods; native locks and queue-removal retry modeled.
#include <cassert>
#include <condition_variable>
#include <cstdio>
#include <cstdint>
#include <mutex>
#include <initializer_list>
#include <thread>
using wait_result_t=int;using boolean_t=bool;
constexpr int THREAD_WAITING=-1,THREAD_AWAKENED=0,THREAD_INTERRUPTED=2,THREAD_TIMED_OUT=3;
constexpr int TH_WAIT=1,TH_UNINT=2,TH_RUN=4,TH_TERMINATE=8;
constexpr bool FALSE=false,TRUE=true;
struct XThread {std::mutex lock;int state=TH_WAIT,wait_result=THREAD_WAITING;bool wait_timer_is_set=true;int wait_timer=0,wait_timer_active=1;void* waitq=(void*)1;};
using thread_t=XThread*;
using libsimple_lock_t=std::mutex;
struct dtape_thread_t{XThread xnu_thread;libsimple_lock_t block_suspend_gate;void* context=this;int resumes=0,timer_cancels=0,retire=0,destroyed=0;};
thread_local thread_t locked=nullptr;
void thread_lock(thread_t t){t->lock.lock();assert(!locked);locked=t;}
void thread_unlock(thread_t t){assert(locked==t);locked=nullptr;t->lock.unlock();}
dtape_thread_t* dtape_thread_for_xnu_thread(thread_t t){return reinterpret_cast<dtape_thread_t*>(t);}
void libsimple_lock_lock(libsimple_lock_t* l){l->lock();}
void libsimple_lock_unlock(libsimple_lock_t* l){l->unlock();}
bool timer_call_cancel(int* timer){auto* x=reinterpret_cast<XThread*>(reinterpret_cast<char*>(timer)-offsetof(XThread,wait_timer));auto* d=dtape_thread_for_xnu_thread(x);assert(locked==x);++d->timer_cancels;return true;}
struct Hooks{void(*thread_resume)(void*);} hooks;
auto* dtape_hooks=&hooks;
void dearust_dtape_thread_retire_active(dtape_thread_t* t){++t->retire;}
void darling_lockset_thread_destroyed(uintptr_t t){++dtape_thread_for_xnu_thread(reinterpret_cast<thread_t>(t))->destroyed;}
std::mutex barrier;std::condition_variable cv;bool dropped=false,removed=false;
int clear_wait_internal(thread_t t,int result){
 assert(locked==t&&result==THREAD_INTERRUPTED&&t->wait_result==THREAD_INTERRUPTED);
 thread_unlock(t);
 {std::lock_guard l(barrier);dropped=true;}cv.notify_all();
 {std::unique_lock l(barrier);cv.wait(l,[]{return removed;});}
 thread_lock(t);return 1;
}
void resume(void* p){auto* t=static_cast<dtape_thread_t*>(p);assert(locked==&t->xnu_thread);++t->resumes;}
// @@EXACT_FUNCTIONS@@
void race(bool old,bool dying){
 dtape_thread_t t;dropped=false;removed=false;
 std::thread interrupt([&]{if(dying)dtape_thread_dying(&t);else dtape_thread_sigexc_enter(&t);});
 {std::unique_lock l(barrier);cv.wait(l,[]{return dropped;});}
 // Model queue-first wake selecting the still-linked target after the exact
 // interruption method drops its C thread lock to retry queue removal.
 thread_lock(&t.xnu_thread);assert(t.xnu_thread.waitq);
 t.xnu_thread.waitq=nullptr;
 (old?old_thread_unblock:thread_unblock)(&t.xnu_thread,THREAD_AWAKENED);
 assert(t.xnu_thread.wait_result==(old?THREAD_AWAKENED:THREAD_INTERRUPTED));
 assert(t.xnu_thread.state&TH_RUN);
 assert(bool(t.xnu_thread.state&TH_TERMINATE)==dying);
 assert(t.resumes==1&&t.timer_cancels==1&&!t.xnu_thread.wait_timer_is_set&&t.xnu_thread.wait_timer_active==0);
 thread_unlock(&t.xnu_thread);
 {std::lock_guard l(barrier);removed=true;}cv.notify_all();interrupt.join();
 assert(t.retire==int(dying)&&t.destroyed==int(dying));
}
int main(){hooks.thread_resume=resume;
 race(true,false);race(true,true);
 for(int i=0;i<1000;++i){race(false,false);race(false,true);}
 for(auto result:{THREAD_AWAKENED,THREAD_INTERRUPTED,THREAD_TIMED_OUT}){
  dtape_thread_t t;thread_lock(&t.xnu_thread);t.xnu_thread.waitq=nullptr;
  assert(thread_unblock(&t.xnu_thread,result));assert(t.xnu_thread.wait_result==result);
  assert(t.resumes==1&&t.timer_cancels==1);thread_unlock(&t.xnu_thread);
 }
 // Even timeout cannot replace the committed interruption of a terminal wait.
 dtape_thread_t t;thread_lock(&t.xnu_thread);t.xnu_thread.waitq=nullptr;
 t.xnu_thread.state=TH_TERMINATE|TH_WAIT;t.xnu_thread.wait_result=THREAD_INTERRUPTED;
 thread_unblock(&t.xnu_thread,THREAD_TIMED_OUT);
 assert(t.xnu_thread.wait_result==THREAD_INTERRUPTED&&(t.xnu_thread.state&TH_TERMINATE));thread_unlock(&t.xnu_thread);
 puts("old result overwrite reproduced; 2000 interruption/removal races + genuine wake/timer cases passed");
}
