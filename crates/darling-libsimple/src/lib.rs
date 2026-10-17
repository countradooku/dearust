// SPDX-License-Identifier: GPL-3.0-or-later
//! Native Linux x86_64 C ABI for Darling libsimple synchronization.
//! See SAFETY.txt for the FFI contract. The atomic state machines forbid unsafe.
#![cfg_attr(not(feature = "std"), no_std)]
#![deny(unsafe_op_in_unsafe_fn)]
#![deny(clippy::undocumented_unsafe_blocks)]
#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
compile_error!(
    "Only native Linux x86_64 futex ABI is implemented; Darling/Darwin backend is unported"
);
#[cfg(all(not(feature = "std"), not(test), not(panic = "abort")))]
compile_error!("the freestanding static archive requires panic=abort");
#[cfg(all(test, not(feature = "std")))]
extern crate std;

// This handler belongs only to the final C-linked archive, never to a Rust
// consumer that supplies its own panic runtime. No formatting, allocator or TLS.
#[cfg(all(not(feature = "std"), not(test)))]
#[panic_handler]
fn panic(_: &::core::panic::PanicInfo<'_>) -> ! {
    terminate()
}
mod core;
use ::core::{
    ffi::{c_int, c_long, c_void},
    sync::atomic::AtomicU32,
};
use core::Futex;

const _: () = assert!(size_of::<AtomicU32>() == 4 && align_of::<AtomicU32>() == 4);
unsafe extern "C" {
    fn syscall(number: c_long, ...) -> c_long;
    fn abort() -> !;
}
fn terminate() -> ! {
    // SAFETY: C abort has no preconditions and does not return or unwind. The
    // final native C consumer supplies libc, just as for the futex syscall shim.
    unsafe { abort() }
}
struct Native;
impl Native {
    fn call(
        state: &AtomicU32,
        operation: c_int,
        value: u32,
        fourth: usize,
        target: Option<&AtomicU32>,
        mask: u32,
    ) -> i64 {
        let target = target.map_or(::core::ptr::null_mut(), AtomicU32::as_ptr);
        // SAFETY: x86_64 Linux SYS_futex=202. AtomicU32 is four-byte aligned and
        // remains alive throughout this synchronous kernel call. The kernel only
        // accesses atomic state through its futex protocol. REQUEUE can leave
        // other waits associated with the target after return; the FFI contract
        // requires both words to remain stable and live until all waiters finish.
        // fourth is null for untimed waits or INT_MAX for REQUEUE's integer val2.
        unsafe {
            syscall(
                202 as c_long,
                state.as_ptr(),
                operation,
                value,
                fourth,
                target,
                mask,
            )
        }
    }
}
impl Futex for Native {
    fn wait(&self, state: &AtomicU32, expected: u32, mask: Option<u32>) {
        Self::call(
            state,
            128 | if mask.is_some() { 9 } else { 0 },
            expected,
            0,
            None,
            mask.unwrap_or(0),
        );
    }
    fn wake(&self, state: &AtomicU32, count: u32, mask: Option<u32>) -> i64 {
        Self::call(
            state,
            128 | if mask.is_some() { 10 } else { 1 },
            count,
            0,
            None,
            mask.unwrap_or(0),
        )
    }
    fn requeue(&self, state: &AtomicU32, target: &AtomicU32) {
        Self::call(state, 128 | 3, 1, i32::MAX as usize, Some(target), 0);
    }
}
/// # Safety
/// `state` points to a live, initialized, four-byte aligned C state word. During
/// this call and concurrent calls all accesses must be atomic (except init before
/// publication). Storage cannot move or be destroyed while any waiter exists.
unsafe fn atomic<'a>(state: *mut u32) -> &'a AtomicU32 {
    // SAFETY: the exported C entry point forwards the caller's state contract.
    unsafe { AtomicU32::from_ptr(state) }
}
/// Atomic compare/exchange helper with external linkage in the original C source.
/// Returns the observed prior value on success and failure, using AcqRel/Acquire.
/// # Safety
/// The pointer must meet the live aligned atomic-state contract in SAFETY.txt.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmpxchg_wrapper_u32(state: *mut u32, expected: u32, desired: u32) -> u32 {
    // SAFETY: the caller guarantees a live, aligned atomic state word.
    core::cmpxchg(unsafe { atomic(state) }, expected, desired)
}

macro_rules! unary {
    ($name:ident,$function:ident) => {
        /// # Safety
        /// The pointer must meet the live aligned atomic-state contract in SAFETY.txt.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(state: *mut u32) {
            // SAFETY: guaranteed by this exported function's caller contract.
            let state = unsafe { atomic(state) };
            core::$function(state, &Native);
        }
    };
}
macro_rules! try_unary {
    ($name:ident,$function:ident) => {
        /// # Safety
        /// The pointer must meet the live aligned atomic-state contract in SAFETY.txt.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(state: *mut u32) -> bool {
            // SAFETY: guaranteed by this exported function's caller contract.
            core::$function(unsafe { atomic(state) })
        }
    };
}
unary!(libsimple_lock_lock, lock);
unary!(libsimple_lock_unlock, unlock);
try_unary!(libsimple_lock_try_lock, try_lock);
unary!(libsimple_rwlock_lock_read, read_lock);
unary!(libsimple_rwlock_unlock_read, read_unlock);
try_unary!(libsimple_rwlock_try_lock_read, try_read_lock);
unary!(libsimple_rwlock_lock_write, write_lock);
unary!(libsimple_rwlock_unlock_write, write_unlock);
try_unary!(libsimple_rwlock_try_lock_write, try_write_lock);
/// # Safety
/// State obeys SAFETY.txt. If initialization may run, `callback` must be non-null,
/// callable with `context`, and return normally without unwinding or longjmp, and must not recursively enter this once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn libsimple_once(
    state: *mut u32,
    callback: Option<unsafe extern "C" fn(*mut c_void)>,
    context: *mut c_void,
) {
    // SAFETY: the caller guarantees live aligned atomic state.
    let state = unsafe { atomic(state) };
    core::once(
        state,
        || {
            if let Some(callback) = callback {
                // SAFETY: callback/context validity and normal return are caller obligations.
                unsafe { callback(context) }
            } else {
                // A null callback is valid for an already-completed once. It
                // cannot initialize a new once; abort rather than invoke null.
                terminate();
            }
        },
        &Native,
    );
}
/// # Safety
/// Both pointers obey SAFETY.txt. Caller owns the mutex. All operations on this
/// condvar use this same mutex. Recheck the predicate after every (spurious) wake.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn libsimple_condvar_wait(state: *mut u32, lock: *mut u32) {
    // SAFETY: both state pointers are live, aligned and accessed atomically.
    let (state, lock) = unsafe { (atomic(state), atomic(lock)) };
    core::cond_wait(state, lock, &Native);
}
/// # Safety
/// The condvar state obeys SAFETY.txt. The mutex parameter is unused, as in
/// upstream; it is never dereferenced. Predicate coordination still uses the
/// mutex associated with the condition variable; the validated native protocol
/// requires the notifying caller to hold that mutex.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn libsimple_condvar_notify_one(state: *mut u32, _lock: *mut u32) {
    // SAFETY: state validity is guaranteed by the caller.
    core::notify_one(unsafe { atomic(state) }, &Native);
}
/// # Safety
/// Both pointers obey SAFETY.txt; the mutex is the one used by all condvar waits.
/// The validated native protocol requires the notifying caller to hold it.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn libsimple_condvar_notify_all(state: *mut u32, lock: *mut u32) {
    // SAFETY: both state pointers are live, aligned and accessed atomically.
    let (state, lock) = unsafe { (atomic(state), atomic(lock)) };
    core::notify_all(state, lock, &Native);
}
