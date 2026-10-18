#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Non-null path must be readable through NUL or 4096 bytes, whichever is shorter.
/// Original guest TLS must be initialized; foreign callees must preserve their
/// ABI, keep successful expanded paths terminated, and never unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_truncate(path: *const u8, length: i64) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; crate::path::CAPACITY],
        flags: 1,
        // SAFETY: original C accessor reads initialized guest per-thread cwd TLS.
        dfd: unsafe { dearust_get_perthread_wd() },
    };
    if let Err(error) = crate::path::copy_into(&mut expanded.path, |index| {
        // SAFETY: caller supplies readable bytes through first NUL or capacity;
        // bounded copier stops at NUL and never requests an out-of-range index.
        unsafe { path.add(index).read() }
    }) {
        return error;
    }
    // SAFETY: initialized writable repr(C) object matches vchroot ABI; its flags
    // preserve original final-symlink policy and callee retains no pointer.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: original raw Linux x86_64 syscall 76 receives a live terminated
    // path and the original scalar arguments. Kernel validates the operation.
    let result =
        unsafe { linux_syscall(expanded.path.as_ptr() as i64, length, 0, 0, 0, 0, 76) } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
