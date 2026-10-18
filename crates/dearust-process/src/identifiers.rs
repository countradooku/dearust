//! Uncached namespace-local process identifiers preserving the original C ABI.
unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}
fn convert(result: i64) -> i32 {
    let result = result as i32;
    if result < 0 {
        dearust_errno::linux_to_bsd(result)
    } else {
        result
    }
}

/// Returns the original namespace-local getpid result without caching or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_getpid() -> i64 {
    // SAFETY: Linux x86_64 syscall 39 takes no arguments and accesses no Rust
    // storage. The foreign ABI must be preserved and must not unwind into Rust.
    let result = convert(unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 39) });
    i64::from(result)
}

/// Returns the original namespace-local getppid result without caching or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_getppid() -> i64 {
    // SAFETY: Linux x86_64 syscall 110 takes no arguments and accesses no Rust
    // storage. The foreign ABI must be preserved and must not unwind into Rust.
    let result = convert(unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 110) });
    i64::from(if result == 0 { 1 } else { result })
}

/// Returns the original namespace-local getpgrp result without caching or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_getpgrp() -> i64 {
    // SAFETY: Linux x86_64 syscall 111 takes no arguments and accesses no Rust
    // storage. The foreign ABI must be preserved and must not unwind into Rust.
    let result = convert(unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 111) });
    i64::from(result)
}

/// Returns the original namespace-local getpgid result without caching or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_getpgid(pid: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 121 takes a signed PID and accesses no Rust
    // storage. The foreign ABI must be preserved and must not unwind into Rust.
    let result = convert(unsafe { linux_syscall(i64::from(pid), 0, 0, 0, 0, 0, 121) });
    i64::from(result)
}

/// Returns the original namespace-local getsid result without caching or retry.
#[unsafe(no_mangle)]
pub extern "C" fn sys_getsid(pid: i32) -> i64 {
    // SAFETY: Linux x86_64 syscall 124 takes a signed PID and accesses no Rust
    // storage. The foreign ABI must be preserved and must not unwind into Rust.
    let result = convert(unsafe { linux_syscall(i64::from(pid), 0, 0, 0, 0, 0, 124) });
    i64::from(result)
}

/// Sets the namespace-local process group using the original signed arguments.
#[unsafe(no_mangle)]
pub extern "C" fn sys_setpgid(pid: i32, pgid: i32) -> i64 {
    // SAFETY: Linux x86_64 setpgid 109 validates signed pid/pgid and mutates
    // kernel process state. The foreign ABI must be preserved and not unwind.
    i64::from(convert(unsafe {
        linux_syscall(i64::from(pid), i64::from(pgid), 0, 0, 0, 0, 109)
    }))
}

/// Creates a session according to the original namespace-local kernel policy.
#[unsafe(no_mangle)]
pub extern "C" fn sys_setsid() -> i64 {
    // SAFETY: Linux x86_64 setsid 112 takes no arguments. The kernel validates
    // session policy; the foreign boundary must preserve its ABI and not unwind.
    i64::from(convert(unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 112) }))
}
