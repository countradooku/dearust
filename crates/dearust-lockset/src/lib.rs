//! Server-side Mach lock sets. See SAFETY.txt for the narrow XNU bridge contract.
mod core;
use core::{Lock, Operation, Result as Step};
use std::collections::BTreeMap;
use std::ops::Bound::{Excluded, Included};
use std::sync::{Arc, Mutex, MutexGuard};

struct Set {
    event: Arc<u8>,
    port_converting: bool,
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
    fn darling_lockset_prepare_port_wait(event: usize) -> i32;
    fn darling_lockset_cancel_wait(event: usize) -> i32;
    fn darling_lockset_block() -> i32;
    fn darling_lockset_wake(event: usize);
    fn darling_lockset_make_port(id: usize, port: *mut usize) -> bool;
    fn darling_lockset_drop_port(port: usize);
}
fn event_id(event: &Arc<u8>) -> usize {
    Arc::as_ptr(event) as usize
}
fn wake(event: &Arc<u8>) {
    // SAFETY: The Arc pins a unique live event address throughout this call.
    // No registry guard is held; the bridge may suspend or reenter the registry.
    unsafe { darling_lockset_wake(event_id(event)) }
}
// Temporary conversion ownership includes both a registry reference and event
// pin. This value is only dropped after every registry guard has been released.
struct ConversionReference {
    id: usize,
    event: Arc<u8>,
}
impl Drop for ConversionReference {
    fn drop(&mut self) {
        lock_set_dereference(self.id);
    }
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
    let event = Arc::new(0u8);
    let id = event_id(&event);
    r.sets.insert(
        id,
        Set {
            event,
            port_converting: false,
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
    let removed = {
        let mut r = registry();
        let Some(set) = r.sets.get_mut(&id) else {
            return;
        };
        set.references -= 1;
        if set.references != 0 {
            return;
        }
        r.sets.remove(&id)
    };
    if let Some(set) = removed {
        // Keep the token live through wake and kernel port teardown.
        wake(&set.event);
        if set.port != 0 {
            // SAFETY: Last reference is no-senders; no send rights remain.
            // No registry guard is held across the foreign teardown.
            unsafe { darling_lockset_drop_port(set.port) };
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn convert_lock_set_to_port(id: usize) -> usize {
    let pin = {
        let mut r = registry();
        let Some(set) = r.sets.get_mut(&id) else {
            return 0;
        };
        set.references += 1;
        ConversionReference {
            id,
            event: set.event.clone(),
        }
    };
    loop {
        let port = {
            let mut r = registry();
            let set = r.sets.get_mut(&id).expect("conversion reference pins set");
            if set.port_converting {
                None
            } else {
                set.port_converting = true;
                Some(set.port)
            }
        };
        if let Some(mut port) = port {
            // SAFETY: The local port slot is exclusive to this conversion.
            // The temporary set reference and event pin survive all callouts.
            // Lazy IPC allocation explicitly requires that nothing is locked.
            let donated = unsafe { darling_lockset_make_port(id, &mut port) };
            {
                let mut r = registry();
                let set = r.sets.get_mut(&id).expect("conversion reference pins set");
                set.port = port;
                set.port_converting = false;
                if !donated {
                    set.references -= 1;
                }
            }
            wake(&pin.event);
            return port;
        }
        // SAFETY: This thread owns a referenced stable token. Registering an
        // uninterruptible conversion wait may suspend; no Rust guard is held.
        let prepared = unsafe { darling_lockset_prepare_port_wait(event_id(&pin.event)) };
        let result = if prepared == -1 {
            let converting = registry()
                .sets
                .get(&id)
                .is_some_and(|set| set.port_converting);
            if !converting {
                // SAFETY: Cancel only this thread's matching event registration.
                unsafe { darling_lockset_cancel_wait(event_id(&pin.event)) }
            } else {
                // SAFETY: No registry guard crosses the foreign suspension.
                unsafe { darling_lockset_block() }
            }
        } else {
            prepared
        };
        if result != 0 {
            // A terminal interruption consumes the converter's original input
            // reference as well as the temporary pin; no send right was made.
            lock_set_dereference(id);
            return 0;
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn lock_set_destroy(task: usize, id: usize) -> i32 {
    let event = {
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
        set.event.clone()
    };
    wake(&event);
    core::SUCCESS
}

// Pin one event at a time in a bounded ID-ordered pass. No wake-list allocation
// and no registry guard crosses a foreign call. Retired tasks/threads cannot
// initiate new operations while their destruction hook is running.
fn update_sets(mut update: impl FnMut(&mut Set) -> bool) {
    let Some(last) = registry().sets.last_key_value().map(|(&id, _)| id) else {
        return;
    };
    let mut cursor = 0;
    loop {
        let next = {
            let mut r = registry();
            r.sets
                .range_mut((Excluded(cursor), Included(last)))
                .next()
                .map(|(&id, set)| {
                    let event = if update(set) {
                        Some(set.event.clone())
                    } else {
                        None
                    };
                    (id, event)
                })
        };
        let Some((id, event)) = next else {
            return;
        };
        cursor = id;
        if let Some(event) = event {
            wake(&event);
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn darling_lockset_task_destroyed(task: usize) {
    update_sets(|set| {
        if set.owner != task || !set.active {
            return false;
        }
        set.active = false;
        true
    });
}
#[unsafe(no_mangle)]
pub extern "C" fn darling_lockset_thread_destroyed(thread: usize) {
    update_sets(|set| {
        for lock in &mut set.locks {
            lock.thread_died(thread);
        }
        true
    });
}
fn interrupted(id: usize, index: usize, thread: usize, operation: Operation) -> i32 {
    let mut r = registry();
    let Some(set) = r.sets.get_mut(&id) else {
        return core::ABORTED;
    };
    if !set.active {
        return core::DESTROYED;
    }
    set.locks
        .get_mut(index)
        .map_or(core::ABORTED, |lock| lock.interrupted(thread, operation))
}
fn operate(id: usize, lock_id: i32, operation: Operation) -> i32 {
    // SAFETY: MIG runs on a live server microthread; bridge retrieves its identity.
    let thread = unsafe { darling_lockset_current_thread() };
    // SAFETY: Same live server microthread; no registry lock held.
    let priority = unsafe { darling_lockset_priority() };
    let (event, index) = {
        let r = registry();
        let Some(set) = r.sets.get(&id) else {
            return core::INVALID_ARGUMENT;
        };
        if !set.active {
            return core::DESTROYED;
        }
        let Ok(index) = usize::try_from(lock_id) else {
            return core::INVALID_ARGUMENT;
        };
        (set.event.clone(), index)
    };
    let mut waiting = false;
    loop {
        {
            let mut r = registry();
            let Some(set) = r.sets.get_mut(&id) else {
                return core::INVALID_ARGUMENT;
            };
            if !set.active {
                return core::DESTROYED;
            }
            let Some(lock) = set.locks.get_mut(index) else {
                return core::INVALID_ARGUMENT;
            };
            let step = lock.step(thread, priority, set.policy, operation, waiting);
            match step {
                Step::Done(result) => {
                    drop(r);
                    if matches!(
                        operation,
                        Operation::Release | Operation::Handoff | Operation::Accept
                    ) {
                        wake(&event);
                    }
                    return result;
                }
                Step::Wait => {}
            }
        };
        // SAFETY: Token is pinned; assertion may suspend/reenter, so no guard
        // crosses it. Rechecking the wait condition closes wake-before-assert loss.
        let prepared = unsafe { darling_lockset_prepare_wait(event_id(&event)) };
        let result = if prepared == -1 {
            let required = registry().sets.get(&id).is_some_and(|set| {
                set.active
                    && set
                        .locks
                        .get(index)
                        .is_some_and(|lock| lock.wait_required(thread, operation))
            });
            if required {
                // SAFETY: Wait is registered and no Rust guard is held.
                unsafe { darling_lockset_block() }
            } else {
                // SAFETY: Cancel only the calling thread's matching wait and
                // retain its result if an interruption already cleared it.
                unsafe { darling_lockset_cancel_wait(event_id(&event)) }
            }
        } else {
            prepared
        };
        if result != 0 {
            let result = interrupted(id, index, thread, operation);
            wake(&event);
            return result;
        }
        waiting = true;
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
