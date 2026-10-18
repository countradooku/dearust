#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
#[repr(C, align(8))]
struct LinuxStat {
    before_mode: [u8; 24],
    mode: u32,
    rest: [u8; 116],
}
const _: () = assert!(core::mem::size_of::<LinuxStat>() == 144);
const _: () = assert!(core::mem::align_of::<LinuxStat>() == 8);
const _: () = assert!(core::mem::offset_of!(LinuxStat, mode) == 24);
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn atflags_bsd_to_linux(flags: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Path is passed directly to Linux and must identify a valid kernel-readable
/// pathname, or may be null (the kernel returns EFAULT). Flags are Linux flags;
/// every nonzero value retains the original no-follow stat policy. Foreign
/// functions must preserve the ABI and must not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn do_linux_fchmodat(fd: i32, path: *const u8, mode: i32, flag: i32) -> i32 {
    if flag != 0 {
        let mut stat = LinuxStat {
            before_mode: [0; 24],
            mode: 0,
            rest: [0; 116],
        };
        // SAFETY: initialized 144-byte, eight-aligned output matches Linux x86_64
        // stat. Linux owns pathname validation and writes only within this object.
        let result = unsafe {
            linux_syscall(
                i64::from(fd),
                path as i64,
                (&mut stat as *mut LinuxStat) as i64,
                0x1100,
                0,
                0,
                262,
            )
        } as i32;
        if result != 0 {
            return result;
        }
        if stat.mode & 0o170000 == 0o120000 {
            return 0;
        }
    }
    // SAFETY: raw Linux fchmodat validates the caller's pathname and descriptor;
    // the original syscall receives mode as a sign-extended 32-bit C int.
    unsafe { linux_syscall(i64::from(fd), path as i64, i64::from(mode), 0, 0, 0, 268) as i32 }
}

/// # Safety
/// Non-null path must be readable through NUL or 4096 bytes, whichever comes
/// first. Guest TLS must be initialized. Foreign functions must preserve the
/// declared ABI, retain pathname termination, and must not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fchmodat(fd: i32, path: *const u8, mode: i32, flag: i32) -> i64 {
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; 4096],
        flags: if flag & 0x20 != 0 { 0 } else { 1 },
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = crate::path::copy_into(&mut expanded.path, |index| {
        // SAFETY: copier bounds indices and stops at first NUL; caller supplies
        // readable bytes through NUL or capacity.
        unsafe { path.add(index).read() }
    }) {
        return error;
    }
    // SAFETY: live initialized repr(C) input matches the original structure.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: retain original flag conversion after successful expansion.
    let linux_flags = unsafe { atflags_bsd_to_linux(flag) };
    if linux_flags == 1 {
        return -22;
    }
    // SAFETY: expansion provides a terminated path valid through this call.
    let result =
        unsafe { do_linux_fchmodat(expanded.dfd, expanded.path.as_ptr(), mode, linux_flags) };
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else {
        i64::from(result)
    }
}
