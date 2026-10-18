#[repr(C)]
struct Expanded {
    path: super::memory::PathBuffer,
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Guest TLS must be initialized. Foreign helpers must obey their ABI,
/// preserve path termination and must not retain pointers or unwind into Rust.
/// Guest addresses use the shared fault-contained copy boundary. Kernel output must not alias live
/// wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_rmdir(path: *const u8) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: 0,
        // SAFETY: original C accessor reads initialized guest per-thread cwd TLS.
        dfd: unsafe { dearust_get_perthread_wd() },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: initialized writable repr(C) object matches vchroot ABI; its flags
    // preserve original final-symlink policy and callee retains no pointer.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: original raw Linux x86_64 syscall 263 receives a live terminated
    // path and the original scalar arguments. Kernel validates the operation.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            0x200,
            0,
            0,
            0,
            263,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        0
    }
}
