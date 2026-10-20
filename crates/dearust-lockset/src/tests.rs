//! ABI scheduler adapter tests use native threads; production uses XNU microthreads.
use super::*;
use std::cell::Cell;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, mpsc};
use std::time::Duration;
struct TestAllocator;
static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);
fn record_allocation() {
    if TRACK_ALLOCATIONS.try_with(Cell::get).unwrap_or(false) {
        ALLOCATIONS.fetch_add(1, Ordering::SeqCst);
    }
}
// SAFETY: All allocation operations delegate unchanged to the system allocator.
unsafe impl std::alloc::GlobalAlloc for TestAllocator {
    unsafe fn alloc(&self, layout: std::alloc::Layout) -> *mut u8 {
        record_allocation();
        // SAFETY: The caller provides a valid layout under GlobalAlloc's contract.
        unsafe { std::alloc::System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: std::alloc::Layout) {
        // SAFETY: Pointer/layout are from this allocator and delegated unchanged.
        unsafe { std::alloc::System.dealloc(ptr, layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: std::alloc::Layout) -> *mut u8 {
        record_allocation();
        // SAFETY: Valid layout is forwarded to the system allocator.
        unsafe { std::alloc::System.alloc_zeroed(layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: std::alloc::Layout, size: usize) -> *mut u8 {
        record_allocation();
        // SAFETY: Allocation identity/layout and requested size satisfy caller contract.
        unsafe { std::alloc::System.realloc(ptr, layout, size) }
    }
}
#[global_allocator]
static ALLOCATOR: TestAllocator = TestAllocator;
static TESTS: Mutex<()> = Mutex::new(());
static PREPARES: AtomicUsize = AtomicUsize::new(0);
static CANCELS: AtomicUsize = AtomicUsize::new(0);
static PORT_WAITS: AtomicUsize = AtomicUsize::new(0);
static MAKE_PAUSE: AtomicBool = AtomicBool::new(false);
static MAKE_ENTERED: AtomicUsize = AtomicUsize::new(0);
static MAKE_RELEASE: AtomicBool = AtomicBool::new(false);
static FIRST_CONVERSION_DONE: AtomicBool = AtomicBool::new(false);
static MAKE_ACTIVE: AtomicUsize = AtomicUsize::new(0);
static REDONATE: AtomicBool = AtomicBool::new(false);
static DROP_PAUSE: AtomicBool = AtomicBool::new(false);
static DROP_ENTERED: AtomicBool = AtomicBool::new(false);
static DROP_RELEASE: AtomicBool = AtomicBool::new(false);
static DROP_TOKEN: Mutex<Option<std::sync::Weak<u8>>> = Mutex::new(None);
static IDS: AtomicUsize = AtomicUsize::new(100);
static DROPS: AtomicUsize = AtomicUsize::new(0);
static EVENTS: Mutex<BTreeMap<usize, u64>> = Mutex::new(BTreeMap::new());
static CV: Condvar = Condvar::new();
thread_local! {
    static THREAD: usize = IDS.fetch_add(1, Ordering::SeqCst);
    static WAIT: Cell<(usize,u64)> = const { Cell::new((0,0)) };
    static PORT_ABORT: Cell<bool> = const { Cell::new(false) };
    static PORT_FINISH_BEFORE_ASSERT: Cell<bool> = const { Cell::new(false) };
    static TRACK_ALLOCATIONS: Cell<bool> = const { Cell::new(false) };
    static PREPARE_RETIRE_OWNER: Cell<usize> = const { Cell::new(0) };
    static INTERRUPT: Cell<bool> = const { Cell::new(false) };
}
fn reenter_registry() {
    // Every potentially suspending bridge callback must be able to reenter.
    let r = registry();
    std::hint::black_box(r.sets.len());
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_current_thread() -> usize {
    reenter_registry();
    THREAD.with(|t| *t)
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_priority() -> i32 {
    reenter_registry();
    10
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_task_lock_active(_: usize) -> bool {
    true
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_task_unlock(_: usize) {}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_prepare_wait(id: usize) -> i32 {
    reenter_registry();
    PREPARES.fetch_add(1, Ordering::SeqCst);
    let owner = PREPARE_RETIRE_OWNER.replace(0);
    if owner != 0 {
        darling_lockset_thread_destroyed(owner);
    }
    let mut e = EVENTS.lock().unwrap();
    WAIT.set((id, *e.entry(id).or_default()));
    -1
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_prepare_port_wait(id: usize) -> i32 {
    reenter_registry();
    PORT_WAITS.fetch_add(1, Ordering::SeqCst);
    if PORT_ABORT.replace(false) {
        return 2;
    }
    if PORT_FINISH_BEFORE_ASSERT.replace(false) {
        MAKE_RELEASE.store(true, Ordering::SeqCst);
        wait_for(|| FIRST_CONVERSION_DONE.load(Ordering::SeqCst));
    }
    darling_lockset_prepare_wait(id)
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_cancel_wait(id: usize) -> i32 {
    reenter_registry();
    CANCELS.fetch_add(1, Ordering::SeqCst);
    if WAIT.get().0 != 0 && WAIT.get().0 != id {
        return 2;
    }
    if WAIT.get().0 == id {
        WAIT.set((0, 0));
    }
    if INTERRUPT.replace(false) { 2 } else { 0 }
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_block() -> i32 {
    reenter_registry();
    let (id, generation) = WAIT.get();
    let e = EVENTS.lock().unwrap();
    let (_event_guard, timed) = CV
        .wait_timeout_while(e, Duration::from_secs(10), |e| {
            e.get(&id) == Some(&generation)
        })
        .unwrap();
    assert!(!timed.timed_out(), "lost wakeup");
    if INTERRUPT.replace(false) { 2 } else { 0 }
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_wake(id: usize) {
    reenter_registry();
    *EVENTS.lock().unwrap().entry(id).or_default() += 1;
    CV.notify_all();
}
#[unsafe(no_mangle)]
unsafe extern "C" fn darling_lockset_make_port(id: usize, port: *mut usize) -> bool {
    reenter_registry();
    assert_eq!(
        MAKE_ACTIVE.fetch_add(1, Ordering::SeqCst),
        0,
        "port conversions overlap"
    );
    MAKE_ENTERED.fetch_add(1, Ordering::SeqCst);
    while MAKE_PAUSE.load(Ordering::SeqCst) && !MAKE_RELEASE.load(Ordering::SeqCst) {
        std::thread::yield_now();
    }
    // SAFETY: Caller gives a live exclusive stack-local port slot and pins set.
    let donated = unsafe {
        let donated = *port == 0 || REDONATE.swap(false, Ordering::SeqCst);
        *port = id + 10000;
        donated
    };
    assert_eq!(MAKE_ACTIVE.fetch_sub(1, Ordering::SeqCst), 1);
    donated
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_drop_port(_: usize) {
    reenter_registry();
    if let Some(token) = &*DROP_TOKEN.lock().unwrap() {
        assert!(
            token.upgrade().is_some(),
            "event freed before C port teardown"
        );
    }
    DROP_ENTERED.store(true, Ordering::SeqCst);
    while DROP_PAUSE.load(Ordering::SeqCst) && !DROP_RELEASE.load(Ordering::SeqCst) {
        std::thread::yield_now();
    }
    DROPS.fetch_add(1, Ordering::SeqCst);
}

fn create() -> usize {
    let mut id = 0;
    // SAFETY: Test task adapter accepts token 1; id is writable output storage.
    assert_eq!(unsafe { lock_set_create(1, &mut id, 2, 0) }, 0);
    assert_eq!(convert_lock_set_to_port(id), id + 10000);
    id
}
fn wait_until_queued(id: usize, op: Operation) {
    let start = std::time::Instant::now();
    loop {
        let ready = {
            let r = registry();
            let lock = &r.sets[&id].locks[0];
            lock.has_waiter(op)
        };
        if ready {
            return;
        }
        assert!(start.elapsed() < Duration::from_secs(5));
        std::thread::yield_now();
    }
}
#[test]
fn exported_lifecycle_concurrency_and_handoff() {
    let _serial = TESTS.lock().unwrap();
    let drops_before = DROPS.load(Ordering::SeqCst);
    let mut invalid = 9;
    for (task, count, policy) in [(0, 1, 0), (1, 0, 0), (1, -1, 0), (1, 1, 2)] {
        // SAFETY: Mock task token and writable output slot.
        assert_eq!(
            // SAFETY: Mock task token and writable output slot.
            unsafe { lock_set_create(task, &mut invalid, count, policy) },
            core::INVALID_ARGUMENT
        );
        assert_eq!(invalid, 0);
    }
    assert_eq!(lock_try(999, 0), core::INVALID_ARGUMENT);
    let id = create();
    lock_set_reference(id);
    assert_eq!(registry().sets[&id].references, 2);
    assert_eq!(convert_lock_set_to_port(id), id + 10000);
    assert_eq!(registry().sets[&id].references, 1);
    assert_eq!(lock_try(id, -1), core::INVALID_ARGUMENT);
    assert_eq!(lock_try(id, 2), core::INVALID_ARGUMENT);
    assert_eq!(lock_set_destroy(2, id), core::INVALID_RIGHT);
    let protected = std::sync::Arc::new(AtomicUsize::new(0));
    let active = std::sync::Arc::new(AtomicUsize::new(0));
    let workers: Vec<_> = (0..8)
        .map(|_| {
            let protected = protected.clone();
            let active = active.clone();
            std::thread::spawn(move || {
                for _ in 0..1000 {
                    assert_eq!(lock_acquire(id, 0), 0);
                    assert_eq!(active.fetch_add(1, Ordering::SeqCst), 0);
                    protected.fetch_add(1, Ordering::SeqCst);
                    assert_eq!(active.fetch_sub(1, Ordering::SeqCst), 1);
                    assert_eq!(lock_release(id, 0), 0);
                }
            })
        })
        .collect();
    for t in workers {
        t.join().unwrap();
    }
    assert_eq!(protected.load(Ordering::SeqCst), 8000);
    assert_eq!(lock_acquire(id, 0), 0);
    let acceptor = std::thread::spawn(move || {
        INTERRUPT.set(true); // ownership transfer wins this simulated interruption.
        assert_eq!(lock_handoff_accept(id, 0), 0);
        assert_eq!(lock_release(id, 0), 0);
    });
    wait_until_queued(id, Operation::Accept);
    assert_eq!(lock_handoff(id, 0), 0);
    acceptor.join().unwrap();
    let (tx, rx) = mpsc::channel();
    let owner = std::thread::spawn(move || {
        assert_eq!(lock_acquire(id, 0), 0);
        tx.send(darling_lockset_current_thread()).unwrap();
    });
    let dead = rx.recv().unwrap();
    owner.join().unwrap();
    darling_lockset_thread_destroyed(dead);
    assert_eq!(lock_acquire(id, 0), core::UNSTABLE);
    assert_eq!(lock_make_stable(id, 0), 0);
    let blocked = std::thread::spawn(move || lock_acquire(id, 0));
    wait_until_queued(id, Operation::Acquire);
    assert_eq!(lock_set_destroy(1, id), 0);
    assert_eq!(blocked.join().unwrap(), core::DESTROYED);
    assert_eq!(lock_try(id, 0), core::DESTROYED);
    assert_eq!(lock_try(id, -1), core::DESTROYED);
    assert_eq!(lock_try(id, 2), core::DESTROYED);
    assert_eq!(lock_set_destroy(1, id), core::DESTROYED);
    lock_set_dereference(id);
    assert!(!registry().sets.contains_key(&id));
    let id2 = create();
    darling_lockset_task_destroyed(1);
    assert_eq!(lock_try(id2, 0), core::DESTROYED);
    lock_set_dereference(id2);
    assert_eq!(DROPS.load(Ordering::SeqCst), drops_before + 2);
}

fn raw_create(task: usize) -> usize {
    let mut id = 0;
    // SAFETY: Test task adapter accepts the token; output is writable.
    assert_eq!(unsafe { lock_set_create(task, &mut id, 1, 0) }, 0);
    id
}
fn wait_for(mut predicate: impl FnMut() -> bool) {
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while !predicate() {
        assert!(std::time::Instant::now() < deadline);
        std::thread::yield_now();
    }
}
#[test]
fn update_before_assert_is_cancelled_and_rechecked() {
    let _serial = TESTS.lock().unwrap();
    let id = create();
    assert_eq!(lock_acquire(id, 0), 0);
    let owner = darling_lockset_current_thread();
    let before = CANCELS.load(Ordering::SeqCst);
    let waiter = std::thread::spawn(move || {
        // The bridge performs a state change+wake before arming its C wait.
        PREPARE_RETIRE_OWNER.set(owner);
        assert_eq!(lock_acquire(id, 0), core::UNSTABLE);
        assert_eq!(WAIT.get().0, 0);
        assert_eq!(lock_release(id, 0), 0);
    });
    waiter.join().unwrap();
    assert!(CANCELS.load(Ordering::SeqCst) > before);
    lock_set_dereference(id);
}
#[test]
fn interruption_before_recheck_does_not_grant_acquire() {
    let _serial = TESTS.lock().unwrap();
    let id = create();
    assert_eq!(lock_acquire(id, 0), 0);
    let owner = darling_lockset_current_thread();
    let waiter = std::thread::spawn(move || {
        PREPARE_RETIRE_OWNER.set(owner);
        INTERRUPT.set(true);
        assert_eq!(lock_acquire(id, 0), core::ABORTED);
        assert_eq!(WAIT.get().0, 0);
    });
    waiter.join().unwrap();
    assert_eq!(lock_try(id, 0), core::UNSTABLE);
    assert_eq!(lock_release(id, 0), 0);
    lock_set_dereference(id);
}
#[test]
fn concurrent_port_conversion_serializes_and_balances_donations() {
    let _serial = TESTS.lock().unwrap();
    let id = raw_create(1);
    let entered = MAKE_ENTERED.load(Ordering::SeqCst);
    let waits = PORT_WAITS.load(Ordering::SeqCst);
    MAKE_RELEASE.store(false, Ordering::SeqCst);
    MAKE_PAUSE.store(true, Ordering::SeqCst);
    let first = std::thread::spawn(move || convert_lock_set_to_port(id));
    wait_for(|| MAKE_ENTERED.load(Ordering::SeqCst) == entered + 1);
    let others: Vec<_> = (0..8)
        .map(|_| {
            lock_set_reference(id);
            std::thread::spawn(move || convert_lock_set_to_port(id))
        })
        .collect();
    wait_for(|| PORT_WAITS.load(Ordering::SeqCst) >= waits + 8);
    assert_eq!(MAKE_ENTERED.load(Ordering::SeqCst), entered + 1);
    MAKE_RELEASE.store(true, Ordering::SeqCst);
    assert_eq!(first.join().unwrap(), id + 10000);
    for other in others {
        assert_eq!(other.join().unwrap(), id + 10000);
    }
    MAKE_PAUSE.store(false, Ordering::SeqCst);
    assert_eq!(registry().sets[&id].references, 1);
    // Existing right reaches no-senders while another converter owns a ref;
    // the next first send re-arms notification and donates that reference.
    lock_set_reference(id);
    lock_set_dereference(id);
    REDONATE.store(true, Ordering::SeqCst);
    assert_eq!(convert_lock_set_to_port(id), id + 10000);
    assert_eq!(registry().sets[&id].references, 1);
    lock_set_dereference(id);
    assert!(!registry().sets.contains_key(&id));
}
#[test]
fn conversion_pin_survives_task_cleanup_and_port_teardown() {
    let _serial = TESTS.lock().unwrap();
    let id = raw_create(42);
    let weak = Arc::downgrade(&registry().sets[&id].event);
    let entered = MAKE_ENTERED.load(Ordering::SeqCst);
    MAKE_RELEASE.store(false, Ordering::SeqCst);
    MAKE_PAUSE.store(true, Ordering::SeqCst);
    let conversion = std::thread::spawn(move || convert_lock_set_to_port(id));
    wait_for(|| MAKE_ENTERED.load(Ordering::SeqCst) == entered + 1);
    darling_lockset_task_destroyed(42);
    darling_lockset_thread_destroyed(999999);
    assert_eq!(lock_try(id, 0), core::DESTROYED);
    assert_eq!(registry().sets[&id].references, 2);
    MAKE_RELEASE.store(true, Ordering::SeqCst);
    assert_eq!(conversion.join().unwrap(), id + 10000);
    MAKE_PAUSE.store(false, Ordering::SeqCst);
    *DROP_TOKEN.lock().unwrap() = Some(weak.clone());
    DROP_RELEASE.store(false, Ordering::SeqCst);
    DROP_ENTERED.store(false, Ordering::SeqCst);
    DROP_PAUSE.store(true, Ordering::SeqCst);
    let removal = std::thread::spawn(move || lock_set_dereference(id));
    wait_for(|| DROP_ENTERED.load(Ordering::SeqCst));
    assert!(!registry().sets.contains_key(&id));
    assert!(weak.upgrade().is_some());
    let other = raw_create(43);
    assert_ne!(other, id);
    lock_set_dereference(other);
    DROP_RELEASE.store(true, Ordering::SeqCst);
    removal.join().unwrap();
    DROP_PAUSE.store(false, Ordering::SeqCst);
    *DROP_TOKEN.lock().unwrap() = None;
    assert!(weak.upgrade().is_none());
}
#[test]
fn cleanup_wakes_each_set_outside_registry() {
    let _serial = TESTS.lock().unwrap();
    let ids: Vec<_> = (0..32).map(|_| raw_create(99)).collect();
    for &id in &ids {
        assert_eq!(lock_try(id, 0), 0);
    }
    darling_lockset_thread_destroyed(darling_lockset_current_thread());
    for &id in &ids {
        assert_eq!(lock_try(id, 0), core::UNSTABLE);
    }
    darling_lockset_task_destroyed(99);
    for id in ids {
        assert_eq!(lock_try(id, 0), core::DESTROYED);
        lock_set_dereference(id);
    }
}

#[test]
fn port_update_before_assert_and_terminal_wait_reference_balance() {
    let _serial = TESTS.lock().unwrap();
    for abort in [false, true] {
        let id = raw_create(1);
        MAKE_RELEASE.store(false, Ordering::SeqCst);
        MAKE_PAUSE.store(true, Ordering::SeqCst);
        FIRST_CONVERSION_DONE.store(false, Ordering::SeqCst);
        let entered = MAKE_ENTERED.load(Ordering::SeqCst);
        let first = std::thread::spawn(move || {
            let port = convert_lock_set_to_port(id);
            FIRST_CONVERSION_DONE.store(true, Ordering::SeqCst);
            port
        });
        wait_for(|| MAKE_ENTERED.load(Ordering::SeqCst) == entered + 1);
        lock_set_reference(id);
        let cancels = CANCELS.load(Ordering::SeqCst);
        let second = std::thread::spawn(move || {
            PORT_ABORT.set(abort);
            PORT_FINISH_BEFORE_ASSERT.set(!abort);
            convert_lock_set_to_port(id)
        });
        if abort {
            assert_eq!(second.join().unwrap(), 0);
            assert_eq!(registry().sets[&id].references, 2);
            MAKE_RELEASE.store(true, Ordering::SeqCst);
        } else {
            assert_eq!(second.join().unwrap(), id + 10000);
            assert!(CANCELS.load(Ordering::SeqCst) > cancels);
        }
        assert_eq!(first.join().unwrap(), id + 10000);
        MAKE_PAUSE.store(false, Ordering::SeqCst);
        assert_eq!(registry().sets[&id].references, 1);
        lock_set_dereference(id);
        assert!(!registry().sets.contains_key(&id));
    }
}
#[test]
fn cancel_does_not_clear_another_event() {
    let _serial = TESTS.lock().unwrap();
    assert_eq!(darling_lockset_prepare_wait(123), -1);
    assert_eq!(darling_lockset_cancel_wait(456), 2);
    assert_eq!(WAIT.get().0, 123);
    assert_eq!(darling_lockset_cancel_wait(123), 0);
    assert_eq!(WAIT.get().0, 0);
}
#[test]
fn event_wake_and_batched_cleanup_allocate_nothing() {
    let _serial = TESTS.lock().unwrap();
    let ids: Vec<_> = (0..32).map(|_| raw_create(77)).collect();
    for &id in &ids {
        assert_eq!(lock_acquire(id, 0), 0);
        // Prime only the native adapter's event-generation map before counting.
        darling_lockset_wake(id);
    }
    let owner = darling_lockset_current_thread();
    ALLOCATIONS.store(0, Ordering::SeqCst);
    TRACK_ALLOCATIONS.set(true);
    darling_lockset_thread_destroyed(owner);
    darling_lockset_task_destroyed(77);
    for &id in &ids {
        lock_set_dereference(id);
    }
    TRACK_ALLOCATIONS.set(false);
    assert_eq!(ALLOCATIONS.load(Ordering::SeqCst), 0);
    for id in ids {
        assert!(!registry().sets.contains_key(&id));
    }
}
