unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// The original raw syscall boundary must preserve its declared ABI and must
/// not unwind into Rust. Linux validates the descriptor and operation arguments.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_ftruncate(fd: i32, length: i64) -> i64 {
    // SAFETY: Linux x86_64 ftruncate 77 takes signed fd and original i64
    // length; no caller memory is read or written by Rust. Preserve C int narrowing.
    let result = unsafe { linux_syscall(i64::from(fd), length, 0, 0, 0, 0, 77) } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
