#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn atflags_bsd_to_linux(flags: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Non-null path must be readable through NUL or 4096 bytes, whichever comes
/// first. Guest TLS must be initialized. Foreign
/// functions must preserve the declared ABI and must not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_faccessat(fd: i32, path: *const u8, mode: i32, flags: i32) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; 4096],
        flags: if flags & 0x20 == 0 { 1 } else { 0 },
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = crate::path::copy_into(&mut expanded.path, |index| {
        // SAFETY: caller supplies readable bytes through NUL or capacity; the
        // copier bounds every index and stops immediately at the first NUL.
        unsafe { path.add(index).read() }
    }) {
        return error;
    }
    // SAFETY: repr(C) matches original writable, terminated args. Expansion
    // follows the final link unless BSD_AT_SYMLINK_NOFOLLOW was selected.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: original pure converter preserves the accepted BSD flag policy.
    // Calling after expansion preserves original path/flag error ordering.
    let linux_flags = unsafe { atflags_bsd_to_linux(flags) };
    if linux_flags == 1 {
        return -22;
    }
    // SAFETY: raw Linux x86_64 faccessat 269 receives the live path, signed
    // mode and original fourth flags slot. Linux ignores that fourth slot;
    // preserving this limitation avoids inventing new credential/flag policy.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            i64::from(mode),
            i64::from(linux_flags),
            0,
            0,
            269,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
