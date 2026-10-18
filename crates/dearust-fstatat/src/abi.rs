use core::ffi::c_void;

#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
#[repr(C, align(8))]
struct LinuxStat([u64; 18]);

unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn atflags_bsd_to_linux(flags: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn stat_linux_to_bsd(input: *const LinuxStat, output: *mut c_void);
    fn stat_linux_to_bsd64(input: *const LinuxStat, output: *mut c_void);
}

unsafe fn fstatat(fd: i32, path: *const u8, output: *mut c_void, flags: i32, wide: bool) -> i64 {
    if path.is_null() || output.is_null() {
        return -14;
    }
    let path = match crate::path::copy(|index| {
        // SAFETY: caller supplies readable bytes through NUL or 4096 bytes;
        // the safe copier requests only in-bounds indexes and stops at NUL.
        unsafe { path.add(index).read() }
    }) {
        Ok(path) => path,
        Err(error) => return error,
    };
    let mut expanded = Expanded {
        path,
        flags: if flags & 0x20 == 0 { 1 } else { 0 },
        // SAFETY: original helper reads initialized guest TLS for BSD_AT_FDCWD.
        dfd: unsafe { atfd(fd) },
    };
    // SAFETY: repr(C) matches vchroot_expand_args and holds a terminated path.
    // The callee must preserve termination on success and must not unwind.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: original pure converter preserves existing accepted flag policy.
    // Deliberately called after expansion, matching the original error ordering.
    let linux_flags = unsafe { atflags_bsd_to_linux(flags) };
    if linux_flags == 1 {
        return -22;
    }
    let mut stat = LinuxStat([0; 18]);
    // SAFETY: raw Linux x86_64 newfstatat syscall 262 receives initialized,
    // aligned 144-byte output and a live NUL-terminated expanded pathname.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            &mut stat as *mut LinuxStat as i64,
            i64::from(linux_flags),
            0,
            0,
            262,
        )
    };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result as i32));
    }
    // SAFETY: caller supplies the matching writable Darwin output layout.
    // Conversion runs only on success, from fully initialized Linux storage.
    unsafe {
        if wide {
            stat_linux_to_bsd64(&stat, output);
        } else {
            stat_linux_to_bsd(&stat, output);
        }
    }
    0
}

/// # Safety
/// Non-null path must be readable through NUL or 4096 bytes. Non-null output
/// must be a writable, aligned Darwin stat structure without conflicting access.
/// Calling thread must have initialized Darling TLS; foreign calls cannot unwind.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstatat(
    fd: i32,
    path: *const u8,
    output: *mut c_void,
    flags: i32,
) -> i64 {
    // SAFETY: caller promises the documented path, output and TLS contracts.
    unsafe { fstatat(fd, path, output, flags, false) }
}

/// # Safety
/// Same contract as sys_fstatat, except output must be a Darwin stat64 structure.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstatat64(
    fd: i32,
    path: *const u8,
    output: *mut c_void,
    flags: i32,
) -> i64 {
    // SAFETY: caller promises the documented path, output and TLS contracts.
    unsafe { fstatat(fd, path, output, flags, true) }
}
