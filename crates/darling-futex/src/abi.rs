//! Narrow ABI boundary; pointer values are forwarded without dereferencing them.

use core::ffi::{c_int, c_long, c_void};

const NR_FUTEX: c_int = 202;

unsafe extern "C" {
    // Original common/base.h signature: syscall number is the seventh argument.
    fn linux_syscall(
        a1: c_long,
        a2: c_long,
        a3: c_long,
        a4: c_long,
        a5: c_long,
        a6: c_long,
        nr: c_int,
    ) -> c_long;
    fn cerror(error: c_int) -> c_long;
}

/// Execute a Linux futex operation and return negative Darwin errno directly.
/// This entry point never calls cerror or writes the caller's errno.
///
/// # Safety
/// The caller must satisfy Linux futex's operation-specific pointer, alignment,
/// lifetime, atomic-access and sharing requirements, including any requeued
/// waiters. `timeout` is an opaque pointer to the original timespec for WAIT,
/// or an integer encoded in a pointer for operations such as REQUEUE. Its raw
/// bits are forwarded. No Rust timespec or host libc layout is substituted.
/// The linked linux_syscall must provide Darling's raw Linux syscall ABI.
// SAFETY: This replaces the original global symbol; never link futex.c too.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn __linux_futex_reterr(
    uaddr: *mut c_int,
    op: c_int,
    val: c_int,
    timeout: *const c_void,
    uaddr2: *mut c_int,
    val3: c_int,
) -> c_int {
    // SAFETY: The caller guarantees the kernel's operation-specific contract.
    crate::result::direct(unsafe { raw(uaddr, op, val, timeout, uaddr2, val3) })
}

/// Execute a Linux futex operation, returning -1 and setting Darwin errno on error.
///
/// # Safety
/// The same kernel and linked-syscall contracts as __linux_futex_reterr apply.
/// The linked cerror must accept a Darwin errno, set the current thread's errno,
/// and return normally according to the original `long cerror(int)` C ABI.
// SAFETY: This replaces the original global symbol; never link futex.c too.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn __linux_futex(
    uaddr: *mut c_int,
    op: c_int,
    val: c_int,
    timeout: *const c_void,
    uaddr2: *mut c_int,
    val3: c_int,
) -> c_int {
    // SAFETY: The caller guarantees the kernel's operation-specific contract.
    match crate::result::with_errno(unsafe { raw(uaddr, op, val, timeout, uaddr2, val3) }) {
        Ok(result) => result,
        Err(error) => {
            // SAFETY: The linked cerror contract is part of the caller's ABI.
            unsafe { cerror(error) };
            -1
        }
    }
}

#[inline]
unsafe fn raw(
    uaddr: *mut c_int,
    op: c_int,
    val: c_int,
    timeout: *const c_void,
    uaddr2: *mut c_int,
    val3: c_int,
) -> c_long {
    // SAFETY: The original LINUX_SYSCALL6 macro casts each argument to long,
    // preserving pointers and sign-extending C ints. Caller owns kernel safety.
    unsafe {
        linux_syscall(
            uaddr as c_long,
            op as c_long,
            val as c_long,
            timeout as c_long,
            uaddr2 as c_long,
            val3 as c_long,
            NR_FUTEX,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::sync::atomic::{AtomicI64, Ordering::Relaxed};

    static ARGS: [AtomicI64; 7] = [const { AtomicI64::new(0) }; 7];
    static RETURN: AtomicI64 = AtomicI64::new(0);
    static ERROR: AtomicI64 = AtomicI64::new(0);
    static ERROR_CALLS: AtomicI64 = AtomicI64::new(0);

    // SAFETY: Test binary alone owns these symbols, with the declared C ABI.
    #[unsafe(no_mangle)]
    extern "C" fn linux_syscall(
        a1: c_long,
        a2: c_long,
        a3: c_long,
        a4: c_long,
        a5: c_long,
        a6: c_long,
        nr: c_int,
    ) -> c_long {
        for (slot, value) in ARGS.iter().zip([a1, a2, a3, a4, a5, a6, nr as c_long]) {
            slot.store(value, Relaxed);
        }
        RETURN.load(Relaxed)
    }

    // SAFETY: Test binary alone owns this symbol, with the declared C ABI.
    #[unsafe(no_mangle)]
    extern "C" fn cerror(error: c_int) -> c_long {
        ERROR.store(error as i64, Relaxed);
        ERROR_CALLS.fetch_add(1, Relaxed);
        987 // The adapter must return -1 independently of cerror's return.
    }

    #[test]
    fn original_seven_argument_abi_and_errno_side_effects() {
        let uaddr = 0x1234_5678_9abc_usize as *mut c_int;
        let uaddr2 = 0x3456_789a_bcde_usize as *mut c_int;
        // REQUEUE's integer val2 uses this same C pointer parameter.
        let val2 = 0x765_usize as *const c_void;
        // This one test owns the mock state. The mock accepts all pointer bits
        // without accessing memory; no actual kernel contract is invoked.
        for (raw, expected) in [(0, 0), (19, 19), (-11, -35), (-110, -60)] {
            RETURN.store(raw, Relaxed);
            // SAFETY: Our linked mock boundary only records raw argument bits.
            let actual = unsafe { __linux_futex_reterr(uaddr, -3, -7, val2, uaddr2, -9) };
            assert_eq!(actual, expected);
            assert_eq!(ERROR_CALLS.load(Relaxed), 0);
            assert_eq!(
                core::array::from_fn::<_, 7, _>(|i| ARGS[i].load(Relaxed)),
                [uaddr as c_long, -3, -7, 0x765, uaddr2 as c_long, -9, 202]
            );
        }
        RETURN.store(-11, Relaxed);
        // SAFETY: Both linked mock boundaries implement the declared contracts.
        assert_eq!(unsafe { __linux_futex(uaddr, 0, 0, val2, uaddr2, 0) }, -1);
        assert_eq!(ERROR.load(Relaxed), 35);
        assert_eq!(ERROR_CALLS.load(Relaxed), 1);
        RETURN.store(23, Relaxed);
        // SAFETY: Both linked mock boundaries implement the declared contracts.
        assert_eq!(unsafe { __linux_futex(uaddr, 0, 0, val2, uaddr2, 0) }, 23);
        assert_eq!(ERROR_CALLS.load(Relaxed), 1);
    }
}
