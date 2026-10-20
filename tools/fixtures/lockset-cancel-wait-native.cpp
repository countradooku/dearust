// Exact generated bridge and XNU functions; native locks/scheduler callbacks modeled.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <stdexcept>
using event_t=uintptr_t; using event64_t=uintptr_t; using spl_t=int;
using kern_return_t=int; using wait_result_t=int;
constexpr int THREAD_AWAKENED=0,THREAD_INTERRUPTED=2,KERN_SUCCESS=0,KERN_FAILURE=1,KERN_NOT_WAITING=3;
constexpr int TH_WAIT=1,TH_UNINT=2,TH_TERMINATE=4,WQ_OPTION_NONE=0;
constexpr int LINK_WALK_FULL_DAG=0,WQL_WQS=0,WQ_ITERATE_FOUND=1;
constexpr uintptr_t NO_EVENT64=0,NO_EVENT=0;
#define __improbable(x) (x)
#define CAST_EVENT64_T(x) (x)
#define __private_extern__
struct waitq { std::mutex lock; unsigned waitq_eventmask=1; uintptr_t waitq_set_id=0; int count=0; } queue;
struct thread_state { std::mutex lock; struct waitq* waitq=nullptr; uintptr_t wait_event=0; int wait_result=-1,state=TH_WAIT; };
using thread_t=thread_state*;
constexpr thread_t THREAD_NULL=nullptr;
thread_local thread_t self=nullptr; thread_local bool holds_thread=false;
thread_t current_thread(){return self;}
spl_t splsched(){return 0;} void splx(spl_t){}
void thread_lock(thread_t t){t->lock.lock();holds_thread=true;}
void thread_unlock(thread_t t){holds_thread=false;t->lock.unlock();}
std::atomic<int> queue_waits{0};
void waitq_lock(waitq* q){assert(!holds_thread);++queue_waits;q->lock.lock();}
void waitq_unlock(waitq* q){q->lock.unlock();}
bool waitq_lock_try(waitq* q){return q->lock.try_lock();}
bool waitq_irq_safe(waitq*){return true;} bool waitq_valid(waitq*){return true;}
waitq* waitq_get_safeq(waitq* q){return q;} bool waitq_empty(waitq* q){return q->count==0;}
bool waitq_is_global(waitq*){return true;}
void waitq_thread_remove(waitq* q,thread_t){--q->count;assert(q->count>=0);}
void thread_clear_waitq_state(thread_t t){t->waitq=nullptr;t->wait_event=0;}
void waitq_stats_count_wakeup(waitq*){} void waitq_stats_count_fail(waitq*){}
void waitq_stats_count_clear_wakeup(waitq*){} void assert_thread_magic(thread_t t){assert(t);}
struct waitq_link{}; struct select_thread_ctx{thread_t thread;event64_t event;spl_t* spl;};
waitq_link* wql_get_link(uintptr_t){std::abort();} void wql_put_link(waitq_link*){std::abort();}
int waitq_select_thread_cb=0;
int walk_waitq_links(int,waitq*,uintptr_t,int,void*,int){std::abort();}
[[noreturn]] void panic(const char*,...){throw std::runtime_error("nested assert_wait with existing waitq");}
constexpr unsigned LockTimeOutUsec=1;
bool machine_timeout_suspended(){return false;} int cpu_number(){return 0;}
int delays=0;
void delay(int){++delays;if(self->waitq)panic("thread already waiting");}
kern_return_t thread_go(thread_t t,wait_result_t result,int){assert(!t->waitq);t->wait_result=result;t->state&=~TH_WAIT;return KERN_SUCCESS;}
waitq* global_eventq(event_t){return &queue;}
// @@EXACT_FUNCTIONS@@

void register_wait(thread_t t,uintptr_t event){std::scoped_lock lock(queue.lock,t->lock);t->waitq=&queue;t->wait_event=event;t->state=TH_WAIT;t->wait_result=-1;++queue.count;}
void interrupt_locked(thread_t t){std::lock_guard lock(t->lock);if(t->waitq){waitq_thread_remove(&queue,t);thread_clear_waitq_state(t);thread_go(t,THREAD_INTERRUPTED,0);}}
int main(){
 thread_state t,peer;self=&t;
 register_wait(&t,123);
 std::mutex barrier;std::condition_variable cv;bool ready=false,release=false;
 auto hold=[&](bool interrupt){
  queue.lock.lock();{std::lock_guard lock(barrier);ready=true;}cv.notify_all();
  {std::unique_lock lock(barrier);cv.wait(lock,[&]{return release;});}
  if(interrupt)interrupt_locked(&t);
  queue.lock.unlock();
 };
 std::thread holder(hold,false);
 {std::unique_lock lock(barrier);cv.wait(lock,[&]{return ready;});}
 thread_lock(&t);bool reproduced=false;
 try{clear_wait_internal(&t,THREAD_AWAKENED);}catch(const std::runtime_error&){reproduced=true;}
 assert(reproduced&&delays==1&&!holds_thread&&t.waitq==&queue);
 {std::lock_guard lock(barrier);release=true;}cv.notify_all();holder.join();
 assert(darling_lockset_cancel_wait(123)==0&&t.waitq==nullptr);
 // Different events sharing a hash queue, and other threads, are untouched.
 register_wait(&t,456);register_wait(&peer,123);
 assert(darling_lockset_cancel_wait(123)==2&&t.wait_event==456&&peer.wait_event==123);
 assert(darling_lockset_cancel_wait(456)==0);self=&peer;
 assert(darling_lockset_cancel_wait(123)==0);self=&t;
 // A previously recorded interruption must win over cancellation.
 register_wait(&t,123);{std::lock_guard lock(queue.lock);interrupt_locked(&t);}
 assert(darling_lockset_cancel_wait(123)==2&&t.wait_result==2);
 for(bool interrupt:{false,true}){
  register_wait(&t,123);ready=false;release=false;
  std::thread holder2(hold,interrupt);
  {std::unique_lock lock(barrier);cv.wait(lock,[&]{return ready;});}
  const int before=queue_waits.load();int result=-9;
  std::thread cancel([&]{self=&t;result=darling_lockset_cancel_wait(123);});
  while(queue_waits.load()==before)std::this_thread::yield();
  {std::lock_guard lock(barrier);release=true;}cv.notify_all();
  holder2.join();cancel.join();
  assert(result==(interrupt?2:0)&&t.waitq==nullptr&&queue.count==0);
 }
 for(int i=0;i<10000;++i){register_wait(&t,123);assert(darling_lockset_cancel_wait(123)==0);}
 assert(delays==1&&queue.count==0);
 puts("old nested-wait reproduced; 10006 targeted cancellations/event and interruption cases passed");
}
