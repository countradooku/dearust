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
}

unsafe fn fstatat(fd: i32, path: *const u8, output: *mut c_void, flags: i32, wide: bool) -> i64 {
    if path.is_null() || output.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; crate::path::CAPACITY],
        flags: if flags & 0x20 == 0 { 1 } else { 0 },
        dfd: 0,
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: resolve the original guest descriptor/TLS helper only after path
    // validation, preserving error ordering without a second path buffer.
    expanded.dfd = unsafe { atfd(fd) };
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
    // SAFETY: stat is a live initialized Linux stat144. The shared boundary
    // converts into local storage and lets Linux validate the guest output.
    let converted =
        unsafe { super::memory::copy_metadata((&stat as *const LinuxStat).cast(), output, wide) };
    if let Err(error) = converted {
        return error;
    }
    0
}

/// # Safety
/// Guest addresses are validated by Linux; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
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
/// Guest addresses are validated by Linux; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
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
