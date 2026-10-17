// SPDX-License-Identifier: GPL-3.0-or-later
//! Linux and Darling x86_64 C ABI for libsimple synchronization.
//! See SAFETY.txt for the FFI contract. The atomic state machines forbid unsafe.
#![cfg_attr(not(feature = "std"), no_std)]
#![deny(unsafe_op_in_unsafe_fn)]
#![deny(clippy::undocumented_unsafe_blocks)]
#[cfg(not(all(
    target_arch = "x86_64",
    any(target_os = "linux", all(target_os = "macos", feature = "darling"))
)))]
compile_error!("supported backends: Linux x86_64, or Darling x86_64 with feature darling");
#[cfg(all(not(feature = "std"), not(test), not(panic = "abort")))]
compile_error!("the freestanding runtime requires panic=abort");
#[cfg(all(feature = "runtime-object", feature = "std"))]
compile_error!(
    "runtime-object requires --no-default-features; the final consumer owns the panic runtime"
);
#[cfg(all(test, not(feature = "std")))]
extern crate std;

// This handler belongs only to the final C-linked archive, never to a Rust
// consumer that supplies its own panic runtime. No formatting, allocator or TLS.
#[cfg(all(not(feature = "std"), not(test), not(feature = "runtime-object")))]
#[panic_handler]
fn panic(_: &::core::panic::PanicInfo<'_>) -> ! {
    terminate()
}
mod core;
use ::core::{
    ffi::{c_int, c_void},
    sync::atomic::AtomicU32,
};
use core::Futex;

const _: () = assert!(size_of::<AtomicU32>() == 4 && align_of::<AtomicU32>() == 4);
#[cfg(not(feature = "darling"))]
unsafe extern "C" {
    fn syscall(number: ::core::ffi::c_long, ...) -> ::core::ffi::c_long;
    fn abort() -> !;
}
#[cfg(feature = "darling")]
unsafe extern "C" {
    // The timespec is deliberately opaque: none of this API's operations passes
    // a timeout. REQUEUE instead carries its integer val2 in this pointer slot.
    fn __linux_futex_reterr(
        state: *mut c_int,
        operation: c_int,
        value: c_int,
        timeout_or_val2: *const c_void,
        target: *mut c_int,
        mask: c_int,
    ) -> c_int;
    fn __simple_abort() -> !;
}
fn terminate() -> ! {
    #[cfg(not(feature = "darling"))]
    // SAFETY: native C abort has no preconditions, never returns or unwinds.
    unsafe {
        abort()
    }
    #[cfg(feature = "darling")]
    // SAFETY: Darling's bootstrap abort has no preconditions, never returns or
    // unwinds, and does not require an initialized libc or Rust runtime.
    unsafe {
        __simple_abort()
    }
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
        #[cfg(not(feature = "darling"))]
        // SAFETY: x86_64 Linux SYS_futex=202. Addresses come from live aligned
        // atomic words. REQUEUE's integer val2 occupies the fourth argument;
        // both words remain live until all requeued waiters finish (SAFETY.txt).
        unsafe {
            syscall(
                202 as ::core::ffi::c_long,
                state.as_ptr(),
                operation,
                value,
                fourth,
                target,
                mask,
            )
        }
        #[cfg(feature = "darling")]
        // SAFETY: this is the original Darling C ABI, with 32-bit int arguments
        // and result. The fourth slot is null or REQUEUE's integer val2, never
        // dereferenced by Rust. Addresses obey the same futex lifetime contract.
        unsafe {
            i64::from(__linux_futex_reterr(
                state.as_ptr().cast(),
                operation,
                value as c_int,
                fourth as *const c_void,
                target.cast(),
                mask as c_int,
            ))
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

#[cfg(all(test, feature = "darling"))]
mod darling_abi_tests {
    use super::*;
    use std::sync::Mutex;

    // This shim records only the ABI; real syscall/concurrency coverage belongs
    // to the C consumer harness, not these deliberately synthetic return values.
    type Call = (usize, c_int, c_int, usize, usize, c_int);
    static CALLS: Mutex<Vec<Call>> = Mutex::new(Vec::new());

    #[unsafe(no_mangle)]
    extern "C" fn __linux_futex_reterr(
        state: *mut c_int,
        operation: c_int,
        value: c_int,
        fourth: *const c_void,
        target: *mut c_int,
        mask: c_int,
    ) -> c_int {
        CALLS.lock().unwrap().push((
            state as usize,
            operation,
            value,
            fourth as usize,
            target as usize,
            mask,
        ));
        -37
    }

    #[unsafe(no_mangle)]
    extern "C" fn __simple_abort() -> ! {
        std::process::abort()
    }

    #[test]
    fn darling_arguments_preserve_requeue_slot_and_signed_error() {
        let state = AtomicU32::new(0);
        let target = AtomicU32::new(0);
        Native.wait(&state, u32::MAX, None);
        Native.wait(&state, 7, Some(1));
        assert_eq!(Native.wake(&state, 3, None), -37);
        assert_eq!(Native.wake(&state, u32::MAX, Some(2)), -37);
        Native.requeue(&state, &target);
        let address = state.as_ptr() as usize;
        assert_eq!(
            *CALLS.lock().unwrap(),
            vec![
                (address, 128, -1, 0, 0, 0),
                (address, 137, 7, 0, 0, 1),
                (address, 129, 3, 0, 0, 0),
                (address, 138, -1, 0, 0, 2),
                (
                    address,
                    131,
                    1,
                    i32::MAX as usize,
                    target.as_ptr() as usize,
                    0
                ),
            ]
        );
    }
}
