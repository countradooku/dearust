#include <darlingserver/registry.hpp>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <thread>
#include <system_error>
#include <chrono>
#include <ucontext.h>
#include <sys/syscall.h>
#include <unistd.h>
static std::atomic<bool> deny{false};static std::atomic<unsigned> denied{0};
void* operator new(size_t n){if(deny){++denied;throw std::bad_alloc();}if(auto*p=std::malloc(n?n:1))return p;throw std::bad_alloc();}
void operator delete(void*p) noexcept{std::free(p);}void operator delete(void*p,size_t) noexcept{std::free(p);}
using DarlingServer::EternalID;
struct Entry {
    using ID=int;using NSID=int;
    int key;EternalID eid;
    bool handles=false,published=false,denyCommit=false,failPublication=false;
    unsigned finished=0,aborted=0;
    std::function<void(Entry*)> foreign;
    Entry(int k,EternalID e):key(k),eid(e){}
    int id()const{return key;}int nsid()const{return key;}
    EternalID eternalID()const{return eid;}void _setEternalID(EternalID e){eid=e;}
    void prepareKernelHandles(){handles=true;if(foreign)foreign(this);if(denyCommit)deny=true;}
    void preparePublication(){if(failPublication)throw std::runtime_error("modeled publication failure");}
    void commitPublication()noexcept{published=true;if(denyCommit)deny=false;}
    void abortPublication()noexcept{published=false;}
    void abortKernelHandles()noexcept{handles=false;++aborted;}
    void finishPreparation()noexcept{++finished;}
};
using Registry=DarlingServer::Registry<Entry>;
#ifndef BEFORE_REGISTRATION_LOCK_SCOPE
template<class P>static void waitFor(P p){auto end=std::chrono::steady_clock::now()+std::chrono::seconds(5);while(!p()){assert(std::chrono::steady_clock::now()<end);std::this_thread::yield();}}
static ucontext_t fiber,returnA,returnB;alignas(16)static unsigned char stackMemory[256*1024];
static std::atomic<bool> parked{false},startB{false},nestedDone{false};
static std::atomic<long> acquiredNative{0},resumedNative{0};
static Registry migrated;
static std::shared_ptr<Entry> migratedEntry;
static void migrationHook(Entry*){acquiredNative=syscall(SYS_gettid);assert(swapcontext(&fiber,&returnA)==0);resumedNative=syscall(SYS_gettid);assert(acquiredNative!=resumedNative);}
static void fiberMain(){migratedEntry=migrated.registerPrepared([](EternalID eid){auto e=std::make_shared<Entry>(10,eid);e->foreign=migrationHook;return e;});assert(setcontext(&returnB)==0);std::abort();}
#endif
int main(){
    Registry registry;
    bool nestedSucceeded=false;
    try{
        registry.registerPrepared([&](EternalID eid){auto e=std::make_shared<Entry>(1,eid);e->foreign=[&](Entry*){assert(registry.size()==0);nestedSucceeded=true;};return e;});
    }catch(const std::system_error& e){assert(e.code().value()==EDEADLK);}
#ifdef BEFORE_REGISTRATION_LOCK_SCOPE
    assert(!nestedSucceeded&&registry.size()==0);
    std::puts("{\"native_registry_reentry_deadlock_reproduced\":true}");return 0;
#else
    assert(nestedSucceeded&&registry.size()==1);
    // Factory lookup/reentry uses ordinary locking with no TLS bypass.
    auto factoryReentry=registry.registerPrepared([&](EternalID eid){assert(registry.lookupEntryByNSID(1));return std::make_shared<Entry>(2,eid);});assert(factoryReentry);
    // Actual native migration happens inside the modeled foreign acquisition.
    assert(getcontext(&fiber)==0);fiber.uc_stack.ss_sp=stackMemory;fiber.uc_stack.ss_size=sizeof(stackMemory);fiber.uc_link=nullptr;makecontext(&fiber,fiberMain,0);
    std::thread a([]{assert(swapcontext(&returnA,&fiber)==0);parked=true;
        // Unrelated work on the original worker must lock normally and can
        // register while the first preparation is parked.
        assert(!migrated.lookupEntryByNSID(10));
        auto e=migrated.registerPrepared([](EternalID eid){return std::make_shared<Entry>(11,eid);});assert(e);nestedDone=true;});
    std::thread b([]{waitFor([]{return startB.load();});assert(swapcontext(&returnB,&fiber)==0);});
    waitFor([]{return parked.load()&&nestedDone.load();});startB=true;a.join();b.join();assert(migratedEntry&&migrated.size()==2);
    // Race two identical NSID preparations; exactly one publishes. The loser
    // returns the winner and releases its private handles after dropping locks.
    Registry racing;std::atomic<unsigned> ready{0};std::atomic<bool> release{false};
    std::shared_ptr<Entry> results[2],privateEntries[2];
    std::thread racers[2];
    for(unsigned i=0;i<2;++i)racers[i]=std::thread([&,i]{results[i]=racing.registerPreparedIfAbsent(50,[&,i](EternalID eid){auto e=std::make_shared<Entry>(50,eid);privateEntries[i]=e;e->foreign=[&](Entry*){++ready;waitFor([&]{return release.load();});};return e;});});
    waitFor([&]{return ready==2;});assert(racing.size()==0&&!racing.lookupEntryByNSID(50));release=true;for(auto&t:racers)t.join();
    assert(results[0]==results[1]&&racing.size()==1);
    unsigned winners=0,losers=0;for(auto&e:privateEntries){assert(e->finished==1);if(e->published){++winners;assert(e->handles);}else{++losers;assert(!e->handles&&e->aborted==1);}}assert(winners==1&&losers==1);
    // All bucket/node preparation precedes modeled foreign acquisition. A
    // legacy insertion while a reservation is outstanding also budgets for it.
    Registry budget;std::shared_ptr<Entry> outer;
    outer=budget.registerPrepared([&](EternalID eid){auto e=std::make_shared<Entry>(70,eid);e->foreign=[&](Entry*){
        for(int k=100;k<220;++k){auto other=std::make_shared<Entry>(k,0);assert(budget.registerEntry(other));}
    };e->denyCommit=true;return e;});assert(outer&&outer->published&&denied==0&&budget.size()==121);
    // Failure after C preparation releases the slot budget and references;
    // reserved EID is consumed. No partially published identity is visible.
    Registry failed;std::shared_ptr<Entry> rejected;EternalID rejectedEid=0;
    try{failed.registerPrepared([&](EternalID eid){rejectedEid=eid;rejected=std::make_shared<Entry>(90,eid);rejected->failPublication=true;return rejected;});assert(false);}catch(const std::runtime_error&){}
    assert(!failed.lookupEntryByNSID(90)&&rejected->finished==1&&rejected->aborted==1&&!rejected->handles);
    auto next=failed.registerPrepared([](EternalID eid){return std::make_shared<Entry>(91,eid);});assert(next->eid>rejectedEid);
    assert(denied==0);
    std::printf("{\"registry_reentry\":true,\"native_migration\":true,\"acquired_native\":%ld,\"resumed_native\":%ld,\"same_nsid_winner\":true,\"legacy_capacity_budget\":true,\"allocation_free_commit\":true,\"failure_unpublished_eid_consumed\":true,\"denied_allocations\":0}\n",acquiredNative.load(),resumedNative.load());
#endif
}
