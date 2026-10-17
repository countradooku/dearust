//! ABI scheduler adapter tests use native threads; production uses XNU microthreads.
use super::*;
use std::cell::Cell;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, mpsc};
use std::time::Duration;
static IDS: AtomicUsize = AtomicUsize::new(100);
static DROPS: AtomicUsize = AtomicUsize::new(0);
static EVENTS: Mutex<BTreeMap<usize, u64>> = Mutex::new(BTreeMap::new());
static CV: Condvar = Condvar::new();
thread_local! {
    static THREAD: usize = IDS.fetch_add(1, Ordering::SeqCst);
    static WAIT: Cell<(usize,u64)> = const { Cell::new((0,0)) };
    static INTERRUPT: Cell<bool> = const { Cell::new(false) };
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_current_thread() -> usize {
    THREAD.with(|t| *t)
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_priority() -> i32 {
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
    let mut e = EVENTS.lock().unwrap();
    WAIT.set((id, *e.entry(id).or_default()));
    -1
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_block() -> i32 {
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
    *EVENTS.lock().unwrap().entry(id).or_default() += 1;
    CV.notify_all();
}
#[unsafe(no_mangle)]
unsafe extern "C" fn darling_lockset_make_port(id: usize, port: *mut usize) -> bool {
    // SAFETY: Caller gives a live exclusive slot while holding registry mutex.
    unsafe {
        let donated = *port == 0;
        *port = id + 10000;
        donated
    }
}
#[unsafe(no_mangle)]
extern "C" fn darling_lockset_drop_port(_: usize) {
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
    assert_eq!(lock_set_destroy(1, id), core::DESTROYED);
    lock_set_dereference(id);
    assert!(!registry().sets.contains_key(&id));
    let id2 = create();
    darling_lockset_task_destroyed(1);
    assert_eq!(lock_try(id2, 0), core::DESTROYED);
    lock_set_dereference(id2);
    assert_eq!(DROPS.load(Ordering::SeqCst), 2);
}
