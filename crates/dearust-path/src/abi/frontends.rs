//! Traditional pathname entries share the canonical directory-relative backend.
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_readlink(path: *const u8, output: *mut u8, count: i32) -> i64 {
    // SAFETY: preserve original TLS-first ordering, including invalid arguments.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the sibling validates the pathname/output. The original signed
    // int count converts to size_t exactly as XNU's traditional readlink entry.
    unsafe { super::readlinkat::sys_readlinkat(fd, path, output, count as usize) }
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_symlink(target: *const u8, link: *const u8) -> i64 {
    // SAFETY: same initialized TLS contract; one snapshot per operation.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the sibling owns destination validation/expansion and lets Linux
    // validate the unchanged target pointer at its actual access.
    unsafe { super::symlinkat::sys_symlinkat(target, fd, link) }
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_link(source: *const u8, destination: *const u8) -> i64 {
    // SAFETY: read TLS once so source and destination share the same cwd.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the canonical backend validates both raw paths; flags0 preserve
    // the original final-component nofollow behavior.
    unsafe { super::linkat::sys_linkat(fd, source, fd, destination, 0) }
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_unlink(path: *const u8) -> i64 {
    // SAFETY: preserve original TLS-first ordering, including invalid paths.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the canonical backend validates and expands the raw pathname.
    unsafe { super::unlinkat::sys_unlinkat(fd, path, 0) }
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_rename(source: *const u8, destination: *const u8) -> i64 {
    // SAFETY: one TLS snapshot preserves both original directory arguments.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the canonical backend validates both paths and owns expansion.
    unsafe { super::renameat::sys_renameat(fd, source, fd, destination) }
}

/// # Safety
/// Guest TLS must be initialized; foreign helpers must obey their ABI and not
/// unwind. Guest addresses must not alias live wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_mkdir(path: *const u8, mode: u32) -> i64 {
    // SAFETY: preserve original TLS-first ordering; mode retains all32 bits.
    let fd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: the sibling validates/expands path; Linux applies mode and umask.
    unsafe { super::mkdirat::sys_mkdirat(fd, path, mode) }
}
