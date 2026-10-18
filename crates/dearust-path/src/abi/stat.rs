use core::ffi::c_void;
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
}

/// # Safety
/// Guest addresses use the shared fault-contained copy boundary; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_stat(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: original stat reads guest cwd before delegating/path validation.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: exported caller supplies the sibling fstatat path/output contract.
    unsafe { super::fstatat::sys_fstatat(fd, path, output, 0) }
}

/// # Safety
/// Guest addresses use the shared fault-contained copy boundary; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_stat64(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: original stat64 reads guest cwd before delegating/path validation.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: caller supplies the sibling fstatat64 path/output contract.
    unsafe { super::fstatat::sys_fstatat64(fd, path, output, 0) }
}
