#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn atflags_bsd_to_linux(flags: i32) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Non-null input pointers must be readable through NUL or 4096 bytes, whichever
/// comes first. Initialized guest TLS and non-unwinding ABI-compatible foreign
/// helpers are required. Helpers must preserve path termination on success.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_linkat(
    fd: i32,
    path: *const u8,
    newfd: i32,
    newpath: *const u8,
    flags: i32,
) -> i64 {
    if path.is_null() || newpath.is_null() {
        return -14;
    }
    let mut source = Expanded {
        path: [0; 4096],
        flags: u32::from(flags & 0x40 != 0),
        // SAFETY: original atfd preserves descriptor and per-thread cwd policy.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = crate::path::copy_into(&mut source.path, |index| {
        // SAFETY: caller guarantees readability; bounded copier stops at NUL.
        unsafe { path.add(index).read() }
    }) {
        return error;
    }
    let mut destination = Expanded {
        path: [0; 4096],
        flags: 0,
        // SAFETY: same original descriptor/TLS contract as source.
        dfd: unsafe { atfd(newfd) },
    };
    if let Err(error) = crate::path::copy_into(&mut destination.path, |index| {
        // SAFETY: caller guarantees readability; copier reads at most 4096 bytes.
        unsafe { newpath.add(index).read() }
    }) {
        return error;
    }
    // SAFETY: initialized repr(C) buffers match the original ABI. Source expands
    // first, preserving error ordering; flags retain the original follow policy.
    let result = unsafe { vchroot_expand(&mut source) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: initialized destination is terminated and must not follow its link.
    let result = unsafe { vchroot_expand(&mut destination) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: retains original C flag policy after both expansion calls.
    let linux_flags = unsafe { atflags_bsd_to_linux(flags) };
    if linux_flags == 1 {
        return -22;
    }
    // SAFETY: live terminated paths and expanded fds are passed through the
    // original seven-argument Linux ABI. Result narrows to int as in upstream C.
    let result = unsafe {
        linux_syscall(
            i64::from(source.dfd),
            source.path.as_ptr() as i64,
            i64::from(destination.dfd),
            destination.path.as_ptr() as i64,
            i64::from(linux_flags),
            0,
            265,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
