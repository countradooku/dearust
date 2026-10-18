use core::ffi::c_void;
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
}

/// # Safety
/// Guest TLS must be initialized even for invalid/null path or output. Non-null
/// path must be readable through NUL or 4096 bytes, output must be a writable,
/// aligned Darwin stat structure without conflicting access. Foreign ABI
/// helpers must not unwind, retain pointers, or break successful termination.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_stat(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: original stat reads guest cwd before delegating/path validation.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: exported caller supplies the sibling fstatat path/output contract.
    unsafe { super::fstatat::sys_fstatat(fd, path, output, 0) }
}

/// # Safety
/// Same contract as sys_stat, except output must be a Darwin stat64 structure.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_stat64(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: original stat64 reads guest cwd before delegating/path validation.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: caller supplies the sibling fstatat64 path/output contract.
    unsafe { super::fstatat::sys_fstatat64(fd, path, output, 0) }
}
