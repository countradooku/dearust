//! Small process services with kernel-validated guest buffers and no cached IDs.
use core::ffi::c_void;

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

#[inline]
unsafe fn random(buffer: *mut c_void, size: usize) -> i64 {
    // SAFETY: Linux x86_64 getrandom (318), flags 0, validates the raw guest
    // address. No Rust reference is formed, including for inaccessible memory.
    // The imported syscall boundary preserves the ABI and must not unwind.
    unsafe { linux_syscall(buffer as i64, size as i64, 0, 0, 0, 0, 318) }
}

/// Supplies at most 256 bytes of kernel entropy, following XNU's copyout errors.
///
/// # Safety
/// Output bytes must permit writes without conflicting Rust references,
/// ownership restrictions or concurrent access. Inaccessible mappings are
/// permitted and
/// diagnosed by the kernel; kernel address validation does not enforce Rust
/// aliasing or ownership rules. The foreign syscall helper must obey its ABI.
///
/// ```compile_fail,E0133
/// dearust_process::sys_getentropy(core::ptr::null_mut(), 1);
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_getentropy(buffer: *mut c_void, size: usize) -> i64 {
    // Pinned XNU randomdev.c rejects oversize requests before copyout. Its
    // zero-length copyout succeeds without validating the address.
    if size > 256 {
        return -22; // Darwin EINVAL
    }
    if size == 0 {
        return 0;
    }
    // SAFETY: the caller supplies the output ownership and foreign ABI contract.
    let result = unsafe { random(buffer, size) };
    if result == size as i64 {
        return 0;
    }
    // SAFETY: the cold path retains the same bounded output/ABI contract.
    unsafe { finish_entropy(buffer, size, result) }
}

#[cold]
unsafe fn finish_entropy(buffer: *mut c_void, size: usize, mut result: i64) -> i64 {
    loop {
        if result == -4 {
            // Startup CRNG initialization can be interrupted. XNU's internal
            // entropy generation does not expose EINTR to this interface.
            // SAFETY: the caller retains output permission across bounded retries.
            result = unsafe { random(buffer, size) };
            if result == size as i64 {
                return 0;
            }
            continue;
        }
        if result < 0 {
            return i64::from(dearust_errno::linux_to_bsd(result as i32));
        }
        // For <=256 bytes Linux does not split a copy for signal delivery. A
        // positive short copy is a guest-memory fault, matching XNU copyout.
        // Do not retry it: the prefix may already have been written.
        return if result < size as i64 { -14 } else { -5 };
    }
}

/// Returns the current namespace-local thread ID, including after thread/fork.
#[unsafe(no_mangle)]
pub extern "C" fn sys_thread_selfid() -> i64 {
    // SAFETY: Linux x86_64 gettid (186) has no arguments and accesses no Rust
    // storage. The imported syscall boundary preserves the ABI and cannot unwind.
    let result = unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 186) } as i32;
    // Preserve the original C int result and sign extension at the public ABI.
    i64::from(if result < 0 {
        dearust_errno::linux_to_bsd(result)
    } else {
        result
    })
}
