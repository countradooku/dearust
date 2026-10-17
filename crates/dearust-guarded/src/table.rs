//! Allocation-free state machine; no pointer dereferences or synchronization.
#![forbid(unsafe_code)]
use crate::Options;
const CAPACITY: usize = 1024;
const EMPTY: i32 = -1;
const DELETED: i32 = -2;
pub(crate) const BAD_FD: i32 = -9;
#[derive(Clone, Copy)]
pub(crate) struct Entry {
    pub fd: i32,
    pub flags: u32,
    pub options: Options,
}
const VACANT: Entry = Entry {
    fd: EMPTY,
    flags: 0,
    options: Options { close: None },
};
pub(crate) struct Table {
    slots: [Entry; CAPACITY],
}
impl Table {
    pub const fn new() -> Self {
        Self {
            slots: [VACANT; CAPACITY],
        }
    }
    fn find(&self, fd: i32) -> Option<usize> {
        if fd < 0 {
            return None;
        }
        let start = fd as usize % CAPACITY;
        for offset in 0..CAPACITY {
            let index = (start + offset) % CAPACITY;
            let entry = &self.slots[index];
            if entry.fd == fd {
                return Some(index);
            }
            if entry.fd == EMPTY {
                return None;
            }
        }
        None
    }
    pub fn add(&mut self, fd: i32, flags: u32, options: Options) -> i32 {
        if fd < 0 {
            return BAD_FD;
        }
        if self.find(fd).is_some() {
            return -17;
        }
        let start = fd as usize % CAPACITY;
        for offset in 0..CAPACITY {
            let entry = &mut self.slots[(start + offset) % CAPACITY];
            if entry.fd < 0 {
                *entry = Entry { fd, flags, options };
                return 0;
            }
        }
        -12
    }
    pub fn modify(&mut self, fd: i32, flags: u32, options: Options) -> i32 {
        if fd < 0 {
            return BAD_FD;
        }
        if let Some(entry) = self.find(fd).and_then(|index| self.slots.get_mut(index)) {
            entry.flags = flags;
            entry.options = options;
            0
        } else {
            -2
        }
    }
    pub fn remove(&mut self, fd: i32) -> i32 {
        if fd < 0 {
            return BAD_FD;
        }
        if let Some(entry) = self.find(fd).and_then(|index| self.slots.get_mut(index)) {
            *entry = Entry {
                fd: DELETED,
                ..VACANT
            };
            0
        } else {
            -2
        }
    }
    pub fn check(&self, fd: i32, flags: u32) -> i32 {
        i32::from(
            self.find(fd)
                .and_then(|index| self.slots.get(index))
                .is_some_and(|entry| entry.flags & flags != 0),
        )
    }
    // Retire before the external callback. No borrow survives into that callback.
    pub fn take_fork_entry(&mut self, index: usize) -> Option<Entry> {
        let slot = self.slots.get_mut(index)?;
        if slot.fd < 0 || slot.flags & 2 == 0 {
            return None;
        }
        let entry = *slot;
        *slot = Entry {
            fd: DELETED,
            ..VACANT
        };
        Some(entry)
    }
}
pub(crate) const SLOT_COUNT: usize = CAPACITY;
#[cfg(test)]
mod tests {
    use super::*;
    const NONE: Options = Options { close: None };
    #[test]
    fn rejects_negative_descriptors_even_with_no_empty_slots() {
        let mut table = Table::new();
        for fd in 0..1024 {
            assert_eq!(table.add(fd, 1, NONE), 0);
        }
        for fd in [-1, -2, -3, i32::MIN] {
            assert_eq!(table.add(fd, 1, NONE), BAD_FD);
            assert_eq!(table.modify(fd, 1, NONE), BAD_FD);
            assert_eq!(table.remove(fd), BAD_FD);
            assert_eq!(table.check(fd, 1), 0);
        }
    }
    #[test]
    fn tombstones_preserve_collisions_and_duplicate_detection() {
        let mut table = Table::new();
        for fd in [1023, 2047, 3071] {
            assert_eq!(table.add(fd, 1, NONE), 0);
        }
        assert_eq!(table.remove(1023), 0);
        assert_eq!(table.add(3071, 2, NONE), -17);
        assert_eq!(table.check(3071, 1), 1);
        assert_eq!(table.add(i32::MAX, 3, NONE), 0);
        assert_eq!(table.modify(2047, 2, NONE), 0);
        assert_eq!(table.check(2047, 1), 0);
        assert_eq!(table.check(2047, 2), 1);
    }
    #[test]
    fn full_table_can_be_reused() {
        let mut table = Table::new();
        for fd in 0..1024 {
            assert_eq!(table.add(fd * 1024, 0, NONE), 0);
        }
        assert_eq!(table.add(1, 0, NONE), -12);
        assert_eq!(table.remove(1024), 0);
        assert_eq!(table.add(1, 0, NONE), 0);
        assert_eq!(table.modify(2, 0, NONE), -2);
        assert_eq!(table.remove(2), -2);
    }
    #[test]
    fn fork_retirement_allows_reentrant_replacement() {
        let mut table = Table::new();
        assert_eq!(table.add(5, 2, NONE), 0);
        assert_eq!(table.add(6, 1, NONE), 0);
        assert_eq!(table.take_fork_entry(5).map(|entry| entry.fd), Some(5));
        assert_eq!(table.remove(5), -2);
        assert_eq!(table.add(5, 1, NONE), 0);
        assert!(table.take_fork_entry(6).is_none());
        assert_eq!(table.check(5, 1), 1);
        assert_eq!(table.check(6, 1), 1);
    }
}
