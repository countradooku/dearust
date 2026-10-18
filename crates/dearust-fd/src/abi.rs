unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

fn narrow_result(result: i64) -> i64 {
    let result = result as i32;
    i64::from(if result < 0 {
        dearust_errno::linux_to_bsd(result)
    } else {
        result
    })
}

/// Preserves the original raw fchmod syscall and errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_fchmod(fd: i32, mode: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 91 receives the original signed integer
    // arguments and validates them. No pointers or caller memory are accessed.
    // The foreign boundary must preserve the ABI and must not unwind into Rust.
    narrow_result(unsafe { linux_syscall(i64::from(fd), i64::from(mode), 0, 0, 0, 0, 91) })
}

/// Preserves the original raw ftruncate syscall and errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_ftruncate(fd: i32, length: i64) -> i64 {
    // SAFETY: Linux x86_64 syscall 77 receives the original signed integer
    // arguments and validates them. No pointers or caller memory are accessed.
    // The foreign boundary must preserve the ABI and must not unwind into Rust.
    narrow_result(unsafe { linux_syscall(i64::from(fd), length, 0, 0, 0, 0, 77) })
}

/// Preserves the original raw fsync_nocancel syscall and errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_fsync_nocancel(fd: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 74 receives the original signed integer
    // arguments and validates them. No pointers or caller memory are accessed.
    // The foreign boundary must preserve the ABI and must not unwind into Rust.
    narrow_result(unsafe { linux_syscall(i64::from(fd), 0, 0, 0, 0, 0, 74) })
}

/// Preserves the original raw fdatasync syscall and errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_fdatasync(fd: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 75 receives the original signed integer
    // arguments and validates them. No pointers or caller memory are accessed.
    // The foreign boundary must preserve the ABI and must not unwind into Rust.
    narrow_result(unsafe { linux_syscall(i64::from(fd), 0, 0, 0, 0, 0, 75) })
}

/// Preserves the original raw flock syscall and errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_flock(fd: i32, op: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 73 receives the original signed integer
    // arguments and validates them. No pointers or caller memory are accessed.
    // The foreign boundary must preserve the ABI and must not unwind into Rust.
    narrow_result(unsafe { linux_syscall(i64::from(fd), i64::from(op), 0, 0, 0, 0, 73) })
}

/// Retains the original direct delegation without cancellation hooks or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_fsync(fd: i32) -> i64 {
    sys_fsync_nocancel(fd)
}

/// Preserves the full signed 64-bit successful offset and original error narrowing.
#[unsafe(no_mangle)]
pub extern "C" fn sys_lseek(fd: i32, offset: i64, whence: i32) -> i64 {
    // SAFETY: Linux x86_64 lseek 8 validates signed fd, offset and whence. No
    // caller memory is touched; the foreign ABI boundary must not unwind.
    let result = unsafe { linux_syscall(i64::from(fd), offset, i64::from(whence), 0, 0, 0, 8) };
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result as i32))
    } else {
        result
    }
}
