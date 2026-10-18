#[repr(C)]
struct Expanded {
    path: super::memory::PathBuffer,
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Guest TLS must be initialized. Foreign functions must preserve the declared
/// ABI and must not unwind into Rust. Linux validates guest memory while
/// copying the bounded path.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_faccessat(fd: i32, path: *const u8, mode: i32, flags: i32) -> i64 {
    if flags & !0x30 != 0 {
        return -22;
    }
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: if flags & 0x20 == 0 { 1 } else { 0 },
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: repr(C) matches original writable, terminated args. Expansion
    // follows the final link unless BSD_AT_SYMLINK_NOFOLLOW was selected.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // Darwin AT_EACCESS is 0x10; Linux uses 0x200. The generic Darling
    // converter drops this operation-specific flag, so map the accepted mask.
    let linux_flags =
        (if flags & 0x10 != 0 { 0x200 } else { 0 }) | (if flags & 0x20 != 0 { 0x100 } else { 0 });
    // SAFETY: both Linux entries validate descriptor, path and mode. faccessat2
    // implements credential/no-follow flags in the kernel; raw faccessat ignores
    // them. Keep the established one-call fast path when no flags are requested.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            i64::from(mode),
            i64::from(linux_flags),
            0,
            0,
            if linux_flags == 0 { 269 } else { 439 },
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
