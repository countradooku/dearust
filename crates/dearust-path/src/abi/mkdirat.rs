#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Guest TLS must be initialized. Foreign helpers must obey their ABI,
/// preserve path termination and must not retain pointers or unwind into Rust.
/// Guest addresses are validated by Linux. Kernel output must not alias live
/// wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_mkdirat(fd: i32, path: *const u8, mode: u32) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; 4096],
        flags: 0,
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: repr(C) matches original writable, terminated args. flags=0 keeps
    // the final symlink component; expansion must preserve termination.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: raw Linux x86_64 mkdirat receives the live terminated path,
    // unsigned mode and expanded descriptor; kernel applies umask/permissions.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            i64::from(mode),
            0,
            0,
            0,
            258,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        0
    }
}
