unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
}

/// # Safety
/// Guest TLS must be initialized. The retained TLS helper and expansion
/// callbacks must preserve their ABI and must not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_access(path: *const u8, mode: i32) -> i64 {
    // SAFETY: preserve the original TLS-first order, including invalid paths.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the canonical wrapper validates memory and owns path expansion.
    unsafe { super::faccessat::sys_faccessat(fd, path, mode, 0) }
}

/// # Safety
/// Guest TLS must be initialized. The retained TLS helper and expansion
/// callbacks must preserve their ABI and must not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_chmod(path: *const u8, mode: i32) -> i64 {
    // SAFETY: preserve the original TLS-first order, including invalid paths.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the canonical wrapper validates memory and owns path expansion.
    unsafe { super::fchmodat::sys_fchmodat(fd, path, mode, 0) }
}
