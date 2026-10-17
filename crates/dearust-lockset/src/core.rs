//! Safe ownership and handoff state machine. Identifiers are opaque server tokens.
#![forbid(unsafe_code)]
use std::collections::VecDeque;

pub const SUCCESS: i32 = 0;
pub const INVALID_ARGUMENT: i32 = 4;
pub const INVALID_RIGHT: i32 = 17;
pub const ABORTED: i32 = 14;
pub const ALREADY_WAITING: i32 = 30;
pub const DESTROYED: i32 = 38;
pub const UNSTABLE: i32 = 39;
pub const OWNED: i32 = 40;
pub const OWNED_SELF: i32 = 41;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation {
    Acquire,
    Try,
    Release,
    Stable,
    Handoff,
    Accept,
}
#[derive(Debug, PartialEq, Eq)]
pub enum Result {
    Done(i32),
    Wait,
}
#[derive(Default)]
pub struct Lock {
    owner: Option<usize>,
    unstable: bool,
    queue: VecDeque<(usize, i32)>,
    sender: Option<usize>,
    acceptor: Option<usize>,
}
impl Lock {
    #[cfg(test)]
    pub fn has_waiter(&self, operation: Operation) -> bool {
        match operation {
            Operation::Acquire => !self.queue.is_empty(),
            Operation::Accept => self.acceptor.is_some(),
            Operation::Handoff => self.sender.is_some(),
            _ => false,
        }
    }
    fn acquired(&self) -> Result {
        Result::Done(if self.unstable { UNSTABLE } else { SUCCESS })
    }
    pub fn step(
        &mut self,
        thread: usize,
        priority: i32,
        policy: i32,
        op: Operation,
        waiting: bool,
    ) -> Result {
        match op {
            Operation::Acquire | Operation::Try => {
                if self.owner == Some(thread) {
                    return Result::Done(OWNED_SELF);
                }
                if self.owner.is_none()
                    && (self.queue.is_empty() || self.queue.front().map(|v| v.0) == Some(thread))
                {
                    self.queue.retain(|v| v.0 != thread);
                    self.owner = Some(thread);
                    return self.acquired();
                }
                if op == Operation::Try {
                    return Result::Done(OWNED);
                }
                if !self.queue.iter().any(|v| v.0 == thread) {
                    // Fixed-priority ties retain FIFO ordering.
                    let index = if policy == 1 {
                        self.queue
                            .iter()
                            .position(|v| v.1 < priority)
                            .unwrap_or(self.queue.len())
                    } else {
                        self.queue.len()
                    };
                    self.queue.insert(index, (thread, priority));
                }
                Result::Wait
            }
            Operation::Release | Operation::Stable => {
                if self.owner != Some(thread) {
                    return Result::Done(INVALID_RIGHT);
                }
                if op == Operation::Release {
                    self.owner = None;
                } else {
                    self.unstable = false;
                }
                Result::Done(SUCCESS)
            }
            Operation::Handoff => {
                if waiting && self.sender != Some(thread) {
                    return Result::Done(SUCCESS);
                }
                if self.owner != Some(thread) {
                    return Result::Done(INVALID_RIGHT);
                }
                if let Some(acceptor) = self.acceptor {
                    self.owner = Some(acceptor);
                    self.sender = None;
                    return Result::Done(SUCCESS);
                }
                self.sender = Some(thread);
                Result::Wait
            }
            Operation::Accept => {
                if waiting && self.owner == Some(thread) {
                    self.acceptor = None;
                    return self.acquired();
                }
                if self.acceptor.is_some_and(|v| v != thread) {
                    return Result::Done(ALREADY_WAITING);
                }
                if self.owner == Some(thread) {
                    return Result::Done(OWNED_SELF);
                }
                if self.sender.take().is_some() {
                    self.owner = Some(thread);
                    self.acceptor = None;
                    return self.acquired();
                }
                self.acceptor = Some(thread);
                Result::Wait
            }
        }
    }
    pub fn interrupted(&mut self, thread: usize, operation: Operation) -> i32 {
        // Transfer may have won a concurrent interruption: report that ownership,
        // rather than returning an error while silently retaining the lock.
        if operation == Operation::Accept && self.owner == Some(thread) {
            self.acceptor = None;
            return if self.unstable { UNSTABLE } else { SUCCESS };
        }
        if operation == Operation::Handoff && self.sender != Some(thread) {
            return SUCCESS;
        }
        self.cancel(thread);
        ABORTED
    }
    pub fn cancel(&mut self, thread: usize) {
        self.queue.retain(|v| v.0 != thread);
        if self.sender == Some(thread) {
            self.sender = None;
        }
        if self.acceptor == Some(thread) {
            self.acceptor = None;
        }
    }
    pub fn thread_died(&mut self, thread: usize) {
        self.cancel(thread);
        if self.owner == Some(thread) {
            self.owner = None;
            self.unstable = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn call(l: &mut Lock, t: usize, op: Operation) -> Result {
        l.step(t, 0, 0, op, false)
    }
    #[test]
    fn ownership_and_unstable() {
        let mut l = Lock::default();
        assert_eq!(call(&mut l, 1, Operation::Acquire), Result::Done(SUCCESS));
        assert_eq!(call(&mut l, 1, Operation::Try), Result::Done(OWNED_SELF));
        assert_eq!(call(&mut l, 2, Operation::Try), Result::Done(OWNED));
        assert_eq!(
            call(&mut l, 2, Operation::Release),
            Result::Done(INVALID_RIGHT)
        );
        l.thread_died(1);
        assert_eq!(call(&mut l, 2, Operation::Acquire), Result::Done(UNSTABLE));
        assert_eq!(call(&mut l, 2, Operation::Stable), Result::Done(SUCCESS));
        assert_eq!(call(&mut l, 2, Operation::Release), Result::Done(SUCCESS));
        assert_eq!(call(&mut l, 3, Operation::Acquire), Result::Done(SUCCESS));
    }
    #[test]
    fn fifo_and_cancelled_waiter() {
        let mut l = Lock::default();
        call(&mut l, 1, Operation::Acquire);
        assert_eq!(call(&mut l, 2, Operation::Acquire), Result::Wait);
        assert_eq!(call(&mut l, 3, Operation::Acquire), Result::Wait);
        call(&mut l, 1, Operation::Release);
        assert_eq!(call(&mut l, 4, Operation::Try), Result::Done(OWNED));
        l.cancel(2);
        assert_eq!(call(&mut l, 3, Operation::Acquire), Result::Done(SUCCESS));
    }
    #[test]
    fn priority_and_fifo_ties() {
        let mut l = Lock::default();
        call(&mut l, 1, Operation::Acquire);
        for (t, p) in [(2, 10), (3, 20), (4, 20)] {
            assert_eq!(l.step(t, p, 1, Operation::Acquire, false), Result::Wait);
        }
        call(&mut l, 1, Operation::Release);
        assert_eq!(l.step(2, 10, 1, Operation::Acquire, true), Result::Wait);
        assert_eq!(
            l.step(3, 20, 1, Operation::Acquire, true),
            Result::Done(SUCCESS)
        );
        call(&mut l, 3, Operation::Release);
        assert_eq!(
            l.step(4, 20, 1, Operation::Acquire, true),
            Result::Done(SUCCESS)
        );
    }
    #[test]
    fn acceptor_first_and_second_acceptor() {
        let mut l = Lock::default();
        call(&mut l, 1, Operation::Acquire);
        assert_eq!(call(&mut l, 2, Operation::Accept), Result::Wait);
        assert_eq!(
            call(&mut l, 3, Operation::Accept),
            Result::Done(ALREADY_WAITING)
        );
        assert_eq!(call(&mut l, 1, Operation::Handoff), Result::Done(SUCCESS));
        assert_eq!(
            l.step(2, 0, 0, Operation::Accept, true),
            Result::Done(SUCCESS)
        );
        assert_eq!(call(&mut l, 2, Operation::Release), Result::Done(SUCCESS));
    }
    #[test]
    fn sender_first_and_interruption() {
        let mut l = Lock::default();
        call(&mut l, 1, Operation::Acquire);
        assert_eq!(call(&mut l, 1, Operation::Handoff), Result::Wait);
        assert_eq!(call(&mut l, 2, Operation::Accept), Result::Done(SUCCESS));
        assert_eq!(
            l.step(1, 0, 0, Operation::Handoff, true),
            Result::Done(SUCCESS)
        );
        assert_eq!(call(&mut l, 2, Operation::Handoff), Result::Wait);
        l.cancel(2);
        assert_eq!(call(&mut l, 2, Operation::Release), Result::Done(SUCCESS));
        assert_eq!(call(&mut l, 3, Operation::Accept), Result::Wait);
        l.cancel(3);
        assert_eq!(call(&mut l, 4, Operation::Accept), Result::Wait);
    }
}
