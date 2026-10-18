unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// The original raw syscall boundary must preserve its declared ABI and must
/// not unwind into Rust. Linux validates the descriptor and operation arguments.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fchmod(fd: i32, mode: i32) -> i64 {
    // SAFETY: Linux x86_64 fchmod 91 takes signed fd and original i32
    // mode; no caller memory is read or written by Rust. Preserve C int narrowing.
    let result = unsafe { linux_syscall(i64::from(fd), i64::from(mode), 0, 0, 0, 0, 91) } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
