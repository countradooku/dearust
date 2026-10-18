unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// Synchronizes the descriptor using the original Linux/Darwin errno contract.
#[unsafe(no_mangle)]
pub extern "C" fn sys_fdatasync(fd: i32) -> i64 {
    // SAFETY: raw Linux x86_64 fdatasync 75 takes only a signed descriptor;
    // the kernel validates it. The foreign boundary must preserve its ABI and
    // must not unwind into Rust. No pointers or caller storage are accessed.
    let result = unsafe { linux_syscall(i64::from(fd), 0, 0, 0, 0, 0, 75) } as i32;
    i64::from(if result < 0 {
        dearust_errno::linux_to_bsd(result)
    } else {
        result
    })
}
