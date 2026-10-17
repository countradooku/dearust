//! The only pointer and callback boundary; table operations are safe Rust.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Options {
    pub close: Option<unsafe extern "C" fn(i32)>,
}
const _: () = assert!(core::mem::size_of::<Options>() == 8);
const _: () = assert!(core::mem::align_of::<Options>() == 8);
#[cfg(not(feature = "dyld"))]
mod runtime {
    use super::Options;
    use crate::table::{SLOT_COUNT, Table};
    use core::{
        cell::UnsafeCell,
        sync::atomic::{AtomicU32, Ordering},
    };
    struct Storage(UnsafeCell<Table>);
    // SAFETY: every table borrow is scoped inside with_table under LOCK. Fork
    // reset is called only in the single surviving child thread, before spawning.
    unsafe impl Sync for Storage {}
    static TABLE: Storage = Storage(UnsafeCell::new(Table::new()));
    static LOCK: AtomicU32 = AtomicU32::new(0);
    unsafe extern "C" {
        fn libsimple_lock_lock(lock: *mut u32);
        fn libsimple_lock_unlock(lock: *mut u32);
        fn close_internal(fd: i32) -> i64;
    }
    fn with_table<T>(action: impl FnOnce(&mut Table) -> T) -> T {
        // SAFETY: LOCK is aligned static storage with the C lock's zero layout.
        // Every access uses this same lock; no callbacks execute under the borrow.
        unsafe {
            libsimple_lock_lock(LOCK.as_ptr());
            let result = action(&mut *TABLE.0.get());
            libsimple_lock_unlock(LOCK.as_ptr());
            result
        }
    }
    unsafe fn options(pointer: *const Options) -> Options {
        if pointer.is_null() {
            Options { close: None }
        } else {
            // SAFETY: caller guarantees a live aligned C options struct for this
            // call, not concurrently modified. Copy does not retain its address.
            unsafe { *pointer }
        }
    }
    /// # Safety
    /// Non-null options must be a live aligned Options; callbacks obey SAFETY.txt.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn guard_table_add(fd: i32, flags: u32, pointer: *const Options) -> i32 {
        if fd < 0 {
            return crate::table::BAD_FD;
        }
        // SAFETY: the caller owns the options contract described above.
        let options = unsafe { options(pointer) };
        with_table(|table| table.add(fd, flags, options))
    }
    /// # Safety
    /// Non-null options must be a live aligned Options; callbacks obey SAFETY.txt.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn guard_table_modify(
        fd: i32,
        flags: u32,
        pointer: *const Options,
    ) -> i32 {
        if fd < 0 {
            return crate::table::BAD_FD;
        }
        // SAFETY: the caller owns the options contract described above.
        let options = unsafe { options(pointer) };
        with_table(|table| table.modify(fd, flags, options))
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_remove(fd: i32) -> i32 {
        with_table(|table| table.remove(fd))
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_check(fd: i32, flags: u32) -> i32 {
        with_table(|table| table.check(fd, flags))
    }
    /// # Safety
    /// Call in the sole surviving postfork child before creating any new threads.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn guard_table_postfork_child() {
        // SAFETY: caller guarantees the single-threaded fork-child invariant;
        // parent memory is copy-on-write and no child lock waiter can exist yet.
        LOCK.store(0, Ordering::Relaxed);
        for index in 0..SLOT_COUNT {
            if let Some(entry) = with_table(|table| table.take_fork_entry(index)) {
                // SAFETY: registered callbacks are live C functions and must not
                // unwind. The owned entry copy outlives the unlocked table borrow.
                unsafe {
                    if let Some(close) = entry.options.close {
                        close(entry.fd);
                    } else {
                        close_internal(entry.fd);
                    }
                }
            }
        }
    }
}
#[cfg(not(feature = "dyld"))]
pub use runtime::*;
// VARIANT_DYLD deliberately has no table: match the upstream bootstrap ABI.
#[cfg(feature = "dyld")]
mod dyld {
    use super::Options;
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_add(_: i32, _: u32, _: *const Options) -> i32 {
        -78
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_modify(_: i32, _: u32, _: *const Options) -> i32 {
        -78
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_remove(_: i32) -> i32 {
        -78
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_check(_: i32, _: u32) -> i32 {
        0
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn guard_table_postfork_child() {}
    #[test]
    fn bootstrap_table_is_inert() {
        assert_eq!(guard_table_add(3, 1, core::ptr::null()), -78);
        assert_eq!(guard_table_modify(3, 1, core::ptr::null()), -78);
        assert_eq!(guard_table_remove(3), -78);
        assert_eq!(guard_table_check(3, 1), 0);
        guard_table_postfork_child();
    }
}
#[cfg(feature = "dyld")]
pub use dyld::*;
