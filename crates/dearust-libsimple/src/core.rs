// SPDX-License-Identifier: GPL-3.0-or-later
//! Atomic state machines adapted from Darling libsimple lock.c at
//! 60ba801decee7a00782f74f6be4c8ffb013f79ff. Only this module decides state changes.
#![forbid(unsafe_code)]

use ::core::sync::atomic::{
    AtomicU32,
    Ordering::{AcqRel, Acquire, Relaxed, Release},
};

/// Synchronous futex operations: implementations must not retain references.
/// Spurious wakeups and interrupted waits are expected and retried by callers.
pub trait Futex {
    fn wait(&self, state: &AtomicU32, expected: u32, mask: Option<u32>);
    fn wake(&self, state: &AtomicU32, count: u32, mask: Option<u32>) -> i64;
    fn requeue(&self, state: &AtomicU32, target: &AtomicU32);
}
pub fn cmpxchg(state: &AtomicU32, expected: u32, desired: u32) -> u32 {
    state
        .compare_exchange(expected, desired, AcqRel, Acquire)
        .unwrap_or_else(|value| value)
}
pub fn lock(state: &AtomicU32, futex: &impl Futex) {
    let mut previous = cmpxchg(state, 0, 1);
    if previous == 0 {
        return;
    }
    loop {
        if previous == 2 || cmpxchg(state, 1, 2) != 0 {
            futex.wait(state, 2, None);
        }
        previous = cmpxchg(state, 0, 2);
        if previous == 0 {
            return;
        }
    }
}
fn lock_slow(state: &AtomicU32, futex: &impl Futex) {
    while state.swap(2, Acquire) != 0 {
        futex.wait(state, 2, None);
    }
}
pub fn unlock(state: &AtomicU32, futex: &impl Futex) {
    if state.swap(0, AcqRel) == 2 {
        futex.wake(state, 1, None);
    }
}
pub fn try_lock(state: &AtomicU32) -> bool {
    cmpxchg(state, 0, 1) == 0
}
pub fn once(state: &AtomicU32, callback: impl FnOnce(), futex: &impl Futex) {
    let mut previous = match state.compare_exchange(0, 1, AcqRel, Acquire) {
        Ok(_) => {
            callback();
            if state.swap(3, AcqRel) == 2 {
                futex.wake(state, i32::MAX as u32, None);
            }
            return;
        }
        Err(value) => value,
    };
    loop {
        match previous {
            3 => return,
            1 => match state.compare_exchange(1, 2, AcqRel, Acquire) {
                Ok(_) => previous = 2,
                Err(value) => {
                    previous = value;
                    continue;
                }
            },
            _ => {}
        }
        futex.wait(state, previous, None);
        previous = state.load(Acquire);
    }
}
const WRITE: u32 = 1 << 30;
const WAITERS: u32 = 1 << 31;
pub fn read_lock(state: &AtomicU32, futex: &impl Futex) {
    let mut current = state.load(Relaxed);
    loop {
        if current & (WRITE | WAITERS) == 0 {
            match state.compare_exchange(current, current.wrapping_add(1), Acquire, Relaxed) {
                Ok(_) => return,
                Err(value) => {
                    current = value;
                    continue;
                }
            }
        }
        if current & WAITERS == 0 {
            match state.compare_exchange_weak(current, WRITE | WAITERS, Relaxed, Relaxed) {
                Ok(_) => current = WRITE | WAITERS,
                Err(value) => {
                    current = value;
                    continue;
                }
            }
        }
        futex.wait(state, current, Some(1));
        current = 0;
    }
}
pub fn try_read_lock(state: &AtomicU32) -> bool {
    let current = state.load(Relaxed);
    current & (WRITE | WAITERS) == 0
        && state
            .compare_exchange(current, current.wrapping_add(1), Acquire, Relaxed)
            .is_ok()
}
pub fn read_unlock(state: &AtomicU32, futex: &impl Futex) {
    let previous = state.fetch_sub(1, Release);
    if previous & !WAITERS == 1 && previous & WAITERS != 0 {
        let _ = state.compare_exchange_weak(WAITERS, 0, Relaxed, Relaxed);
        futex.wake(state, 1, Some(2));
    }
}
pub fn write_lock(state: &AtomicU32, futex: &impl Futex) {
    let mut current = match state.compare_exchange(0, WRITE, Acquire, Relaxed) {
        Ok(_) => return,
        Err(value) => value,
    };
    loop {
        if current & !WAITERS == 0 {
            match state.compare_exchange(current, WRITE | WAITERS, Acquire, Relaxed) {
                Ok(_) => return,
                Err(value) => {
                    current = value;
                    continue;
                }
            }
        }
        if current & WAITERS == 0 {
            match state.compare_exchange(current, current | WAITERS, Relaxed, Relaxed) {
                Ok(_) => current |= WAITERS,
                Err(value) => {
                    current = value;
                    continue;
                }
            }
        }
        futex.wait(state, current, Some(2));
        current = 0;
    }
}
pub fn try_write_lock(state: &AtomicU32) -> bool {
    state.compare_exchange(0, WRITE, Acquire, Relaxed).is_ok()
}
pub fn write_unlock(state: &AtomicU32, futex: &impl Futex) {
    if state.swap(0, Release) & WAITERS != 0 {
        futex.wake(state, i32::MAX as u32, Some(1));
        futex.wake(state, 1, Some(2));
    }
}
pub fn cond_wait(state: &AtomicU32, lock: &AtomicU32, futex: &impl Futex) {
    let expected = state.fetch_or(3, Relaxed) | 3;
    unlock(lock, futex);
    futex.wait(state, expected, None);
    lock_slow(lock, futex);
}
pub fn notify_one(state: &AtomicU32, futex: &impl Futex) {
    let current = state.fetch_add(4, Relaxed).wrapping_add(4);
    if current & 1 == 0 || state.fetch_and(!1, Relaxed) & 1 == 0 {
        return;
    }
    if futex.wake(state, 1, None) > 0 {
        state.fetch_or(1, Relaxed);
    }
}
pub fn notify_all(state: &AtomicU32, lock: &AtomicU32, futex: &impl Futex) {
    let current = state.fetch_add(4, Relaxed).wrapping_add(4);
    if current & 2 == 0 {
        return;
    }
    state.fetch_and(!3, Relaxed);
    futex.requeue(state, lock);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::{Cell, RefCell},
        vec,
        vec::Vec,
    };
    #[derive(Debug, PartialEq)]
    enum Event {
        Wait(u32, Option<u32>),
        Wake(u32, Option<u32>),
        Requeue,
    }
    struct Fake {
        events: RefCell<Vec<Event>>,
        waits: Cell<u32>,
        release: Option<u32>,
        woken: i64,
    }
    impl Fake {
        fn new(release: Option<u32>) -> Self {
            Self {
                events: RefCell::new(Vec::new()),
                waits: Cell::new(0),
                release,
                woken: 1,
            }
        }
    }
    impl Futex for Fake {
        fn wait(&self, state: &AtomicU32, expected: u32, mask: Option<u32>) {
            self.events.borrow_mut().push(Event::Wait(expected, mask));
            let waits = self.waits.get() + 1;
            self.waits.set(waits);
            // First return is spurious; second changes state as another thread would.
            if waits == 2 {
                if let Some(value) = self.release {
                    state.store(value, Release);
                }
            }
            assert!(
                waits <= 3,
                "state machine failed to make progress after simulated release"
            );
        }
        fn wake(&self, _: &AtomicU32, count: u32, mask: Option<u32>) -> i64 {
            self.events.borrow_mut().push(Event::Wake(count, mask));
            self.woken
        }
        fn requeue(&self, _: &AtomicU32, _: &AtomicU32) {
            self.events.borrow_mut().push(Event::Requeue);
        }
    }
    #[test]
    fn mutex_retries_spurious_wait_and_preserves_contended_wake_chain() {
        let state = AtomicU32::new(1);
        let futex = Fake::new(Some(0));
        lock(&state, &futex);
        assert_eq!(state.load(Relaxed), 2);
        assert_eq!(futex.waits.get(), 2);
        assert!(!try_lock(&state));
        unlock(&state, &futex);
        assert_eq!(state.load(Relaxed), 0);
        assert_eq!(futex.events.borrow().last(), Some(&Event::Wake(1, None)));
    }
    #[test]
    fn once_waiters_retry_until_acquiring_completed_state() {
        let state = AtomicU32::new(1);
        let futex = Fake::new(Some(3));
        once(
            &state,
            || panic!("waiter must not execute callback"),
            &futex,
        );
        assert_eq!(state.load(Acquire), 3);
        assert_eq!(futex.waits.get(), 2);
        once(
            &state,
            || panic!("completed once must not execute callback"),
            &futex,
        );
        assert_eq!(futex.waits.get(), 2);
    }
    #[test]
    fn once_initializer_publishes_and_wakes_every_waiter() {
        let state = AtomicU32::new(0);
        let futex = Fake::new(None);
        let value = Cell::new(0);
        once(
            &state,
            || {
                assert_eq!(state.load(Relaxed), 1);
                value.set(42);
                state.store(2, Relaxed);
            },
            &futex,
        );
        assert_eq!(state.load(Acquire), 3);
        assert_eq!(value.get(), 42);
        assert_eq!(
            *futex.events.borrow(),
            vec![Event::Wake(i32::MAX as u32, None)]
        );
    }
    #[test]
    fn rwlock_waiters_use_distinct_masks_and_keep_writer_preference() {
        let state = AtomicU32::new(WRITE);
        let futex = Fake::new(Some(0));
        assert!(!try_read_lock(&state));
        read_lock(&state, &futex);
        assert_eq!(state.load(Relaxed), 1);
        assert_eq!(
            *futex.events.borrow(),
            vec![
                Event::Wait(WRITE | WAITERS, Some(1)),
                Event::Wait(WRITE | WAITERS, Some(1))
            ]
        );
        read_unlock(&state, &futex);
        state.store(1, Relaxed);
        let futex = Fake::new(Some(0));
        write_lock(&state, &futex);
        assert_eq!(state.load(Relaxed), WRITE | WAITERS);
        assert!(!try_read_lock(&state));
        assert!(!try_write_lock(&state));
        write_unlock(&state, &futex);
        assert_eq!(state.load(Relaxed), 0);
        assert!(futex.events.borrow().ends_with(&[
            Event::Wake(i32::MAX as u32, Some(1)),
            Event::Wake(1, Some(2))
        ]));
    }
    #[test]
    fn last_reader_releases_waiting_writer() {
        let state = AtomicU32::new(WAITERS | 1);
        let futex = Fake::new(None);
        read_unlock(&state, &futex);
        assert_eq!(state.load(Relaxed), 0);
        assert_eq!(*futex.events.borrow(), vec![Event::Wake(1, Some(2))]);
    }
    #[test]
    fn condvar_spurious_return_reacquires_mutex_as_contended() {
        let condition = AtomicU32::new(0);
        let mutex = AtomicU32::new(1);
        let futex = Fake::new(None);
        cond_wait(&condition, &mutex, &futex);
        assert_eq!(condition.load(Relaxed), 3);
        assert_eq!(mutex.load(Relaxed), 2);
        unlock(&mutex, &futex);
        assert_eq!(
            *futex.events.borrow(),
            vec![Event::Wait(3, None), Event::Wake(1, None)]
        );
    }
    #[test]
    fn notifications_wrap_sequence_and_requeue_to_same_mutex() {
        let condition = AtomicU32::new(u32::MAX);
        let mutex = AtomicU32::new(1);
        let futex = Fake::new(None);
        notify_one(&condition, &futex);
        assert_eq!(condition.load(Relaxed), 3);
        notify_all(&condition, &mutex, &futex);
        assert_eq!(condition.load(Relaxed), 4);
        assert_eq!(
            *futex.events.borrow(),
            vec![Event::Wake(1, None), Event::Requeue]
        );
        notify_all(&condition, &mutex, &futex);
        assert_eq!(condition.load(Relaxed), 8);
        assert_eq!(futex.events.borrow().len(), 2);
    }
}
