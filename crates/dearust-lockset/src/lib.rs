//! Server-side Mach lock sets. See SAFETY.txt for the narrow XNU bridge contract.
mod core;
use core::{Lock, Operation, Result as Step};
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

struct Set {
    _event: Box<u8>,
    owner: usize,
    port: usize,
    references: usize,
    active: bool,
    policy: i32,
    locks: Vec<Lock>,
}
struct Registry {
    sets: BTreeMap<usize, Set>,
}
static REGISTRY: Mutex<Registry> = Mutex::new(Registry {
    sets: BTreeMap::new(),
});
fn registry() -> MutexGuard<'static, Registry> {
    REGISTRY.lock().unwrap_or_else(|e| e.into_inner())
}

unsafe extern "C" {
    fn darling_lockset_current_thread() -> usize;
    fn darling_lockset_priority() -> i32;
    fn darling_lockset_task_lock_active(task: usize) -> bool;
    fn darling_lockset_task_unlock(task: usize);
    fn darling_lockset_prepare_wait(event: usize) -> i32;
    fn darling_lockset_block() -> i32;
    fn darling_lockset_wake(event: usize);
    fn darling_lockset_make_port(id: usize, port: *mut usize) -> bool;
    fn darling_lockset_drop_port(port: usize);
}
fn wake(id: usize) {
    // SAFETY: Live heap token provides a unique address, never dereferenced by XNU.
    unsafe { darling_lockset_wake(id) }
}

/// # Safety
/// `out` must be a writable lock_set_t output slot; task must be a live referenced XNU task.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn lock_set_create(
    task: usize,
    out: *mut usize,
    count: i32,
    policy: i32,
) -> i32 {
    if out.is_null() {
        return core::INVALID_ARGUMENT;
    }
    // SAFETY: Caller owns the writable output slot.
    unsafe { out.write(0) };
    if task == 0 || count <= 0 || !(0..=1).contains(&policy) {
        return core::INVALID_ARGUMENT;
    }
    // Bound a single request's memory before touching overcommitted pages.
    if count > 1_048_576 {
        return 6;
    }
    let mut locks = Vec::new();
    if locks.try_reserve_exact(count as usize).is_err() {
        return 6;
    }
    locks.resize_with(count as usize, Lock::default);
    // SAFETY: MIG supplies a referenced task. Bridge holds its task lock on success.
    if !unsafe { darling_lockset_task_lock_active(task) } {
        return 16;
    }
    let mut r = registry();
    let event = Box::new(0u8);
    let id = (&*event as *const u8) as usize;
    r.sets.insert(
        id,
        Set {
            _event: event,
            owner: task,
            port: 0,
            references: 1,
            active: true,
            policy,
            locks,
        },
    );
    drop(r);
    // SAFETY: Task lock is held; output slot belongs to caller.
    unsafe {
        darling_lockset_task_unlock(task);
        out.write(id);
    }
    core::SUCCESS
}
#[unsafe(no_mangle)]
pub extern "C" fn lock_set_reference(id: usize) {
    if let Some(set) = registry().sets.get_mut(&id) {
        set.references += 1;
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn lock_set_dereference(id: usize) {
    let mut r = registry();
    let Some(set) = r.sets.get_mut(&id) else {
        return;
    };
    set.references -= 1;
    if set.references != 0 {
        return;
    }
    let removed = r.sets.remove(&id);
    let port = removed.as_ref().map_or(0, |s| s.port);
    drop(r);
    if port != 0 {
        // SAFETY: Last reference is the no-senders notification, no send rights remain.
        unsafe { darling_lockset_drop_port(port) };
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn convert_lock_set_to_port(id: usize) -> usize {
    let mut r = registry();
    let Some(set) = r.sets.get_mut(&id) else {
        return 0;
    };
    // SAFETY: Registry mutex stabilizes port slot; bridge binds real IKOT_LOCK_SET port.
    let donated = unsafe { darling_lockset_make_port(id, &mut set.port) };
    if !donated {
        set.references -= 1;
    }
    set.port
}
#[unsafe(no_mangle)]
pub extern "C" fn lock_set_destroy(task: usize, id: usize) -> i32 {
    let mut r = registry();
    let Some(set) = r.sets.get_mut(&id) else {
        return core::INVALID_ARGUMENT;
    };
    if task == 0 {
        return core::INVALID_ARGUMENT;
    }
    if !set.active {
        return core::DESTROYED;
    }
    if set.owner != task {
        return core::INVALID_RIGHT;
    }
    set.active = false;
    wake(id);
    core::SUCCESS
}
#[unsafe(no_mangle)]
pub extern "C" fn darling_lockset_task_destroyed(task: usize) {
    let mut r = registry();
    for (&id, set) in &mut r.sets {
        if set.owner == task && set.active {
            set.active = false;
            wake(id);
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn darling_lockset_thread_destroyed(thread: usize) {
    let mut r = registry();
    for (&id, set) in &mut r.sets {
        for lock in &mut set.locks {
            lock.thread_died(thread);
        }
        wake(id);
    }
}
fn operate(id: usize, lock_id: i32, operation: Operation) -> i32 {
    // SAFETY: MIG runs on a live server microthread; bridge retrieves its XNU identity.
    let thread = unsafe { darling_lockset_current_thread() };
    // SAFETY: Same live server microthread as above.
    let priority = unsafe { darling_lockset_priority() };
    let mut waiting = false;
    loop {
        let mut r = registry();
        let Some(set) = r.sets.get_mut(&id) else {
            return core::INVALID_ARGUMENT;
        };
        if !set.active {
            return core::DESTROYED;
        }
        let Ok(index) = usize::try_from(lock_id) else {
            return core::INVALID_ARGUMENT;
        };
        let Some(lock) = set.locks.get_mut(index) else {
            return core::INVALID_ARGUMENT;
        };
        match lock.step(thread, priority, set.policy, operation, waiting) {
            Step::Done(result) => {
                if matches!(
                    operation,
                    Operation::Release | Operation::Handoff | Operation::Accept
                ) {
                    wake(id);
                }
                return result;
            }
            Step::Wait => {
                // SAFETY: Register wait while state is locked, preventing lost wakeups.
                let prepared = unsafe { darling_lockset_prepare_wait(id) };
                drop(r);
                // SAFETY: No Rust lock held across the server's stackful microthread suspension.
                let result = if prepared == -1 {
                    // SAFETY: No state lock is held across microthread suspension.
                    unsafe { darling_lockset_block() }
                } else {
                    prepared
                };
                if result != 0 {
                    let mut r = registry();
                    if let Some(set) = r.sets.get_mut(&id) {
                        if !set.active {
                            return core::DESTROYED;
                        }
                        if let Some(lock) = set.locks.get_mut(index) {
                            let result = lock.interrupted(thread, operation);
                            wake(id);
                            return result;
                        }
                    }
                    wake(id);
                    return core::ABORTED;
                }
                waiting = true;
            }
        }
    }
}
macro_rules! operation {
    ($name:ident, $op:ident) => {
        #[unsafe(no_mangle)]
        pub extern "C" fn $name(id: usize, lock_id: i32) -> i32 {
            operate(id, lock_id, Operation::$op)
        }
    };
}
operation!(lock_acquire, Acquire);
operation!(lock_try, Try);
operation!(lock_release, Release);
operation!(lock_make_stable, Stable);
operation!(lock_handoff, Handoff);
operation!(lock_handoff_accept, Accept);

#[cfg(test)]
mod tests;
