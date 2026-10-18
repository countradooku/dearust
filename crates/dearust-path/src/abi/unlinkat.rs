#[repr(C)]
struct Expanded {
    path: super::memory::PathBuffer,
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn atflags_bsd_to_linux(flags: i32) -> i32;
    fn vchroot_expand_at(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Guest TLS must be initialized. Foreign helpers must obey their ABI,
/// preserve path termination and must not retain pointers or unwind into Rust.
/// Guest addresses use the shared fault-contained copy boundary. Kernel output must not alias live
/// wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_unlinkat(fd: i32, path: *const u8, flags: i32) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: 0,
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: repr(C) matches writable args with an initialized NUL prefix.
    // Exact existing single-component nofollow paths may retain their original
    // directory fd through unlinkat; all other paths use the full resolver.
    let result = unsafe { vchroot_expand_at(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: original pure converter preserves the accepted BSD flag policy.
    // Calling after expansion preserves original path/flag error ordering.
    let linux_flags = unsafe { atflags_bsd_to_linux(flags) };
    if linux_flags == 1 {
        return -22;
    }
    // SAFETY: raw Linux x86_64 unlinkat 263 receives the live, terminated path
    // and original converted flags. Kernel validates the deletion operation.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            i64::from(linux_flags),
            0,
            0,
            0,
            263,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
