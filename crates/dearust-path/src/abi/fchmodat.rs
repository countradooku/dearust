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
/// Linux validates the raw pathname. Flags use the Linux ABI. Foreign callbacks
/// must preserve the ABI and must not unwind. Nonzero flags use fchmodat2 so
/// checking a link and changing its mode are a single kernel operation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn do_linux_fchmodat(fd: i32, path: *const u8, mode: i32, flag: i32) -> i32 {
    // SAFETY: Linux validates descriptor, pathname and flags. No metadata
    // check/use fallback is attempted when fchmodat2 is unavailable.
    unsafe {
        linux_syscall(
            i64::from(fd),
            path as i64,
            i64::from(mode),
            i64::from(flag),
            0,
            0,
            if flag == 0 { 268 } else { 452 },
        ) as i32
    }
}

/// # Safety
/// Guest TLS must be initialized. Foreign functions must preserve the declared
/// ABI, retain pathname termination, and must not unwind into Rust. Linux
/// validates guest memory while copying the bounded path.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fchmodat(fd: i32, path: *const u8, mode: i32, flag: i32) -> i64 {
    if flag & !0x20 != 0 {
        return -22;
    }
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; 4096],
        flags: if flag & 0x20 != 0 { 0 } else { 1 },
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: live initialized repr(C) input matches the original structure.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    let linux_flags = if flag & 0x20 != 0 { 0x100 } else { 0 };
    // SAFETY: expansion provides a terminated path valid through this call.
    let result =
        unsafe { do_linux_fchmodat(expanded.dfd, expanded.path.as_ptr(), mode, linux_flags) };
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
