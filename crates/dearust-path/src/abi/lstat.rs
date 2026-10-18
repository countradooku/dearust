use core::ffi::c_void;
#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
// Linux x86_64 stat is 144 bytes, aligned to eight. The actual field conversion
// remains in the existing C conversion module; this is kernel-owned output.
#[repr(C, align(8))]
struct LinuxStat([u64; 18]);
unsafe extern "C" {
    fn dearust_get_perthread_wd() -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn stat_linux_to_bsd(input: *const LinuxStat, output: *mut c_void);
    fn stat_linux_to_bsd64(input: *const LinuxStat, output: *mut c_void);
}
unsafe fn lstat(path: *const u8, output: *mut c_void, wide: bool) -> i64 {
    if path.is_null() || output.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: [0; crate::path::CAPACITY],
        flags: 0,
        dfd: 0,
    };
    if let Err(error) = crate::path::copy_into(&mut expanded.path, |index| {
        // SAFETY: caller provides readable bytes through NUL or 4096 bytes,
        // whichever is shorter. The safe copier only requests indexes <4096.
        unsafe { path.add(index).read() }
    }) {
        return error;
    }
    // SAFETY: resolve the original guest descriptor/TLS helper only after path
    // validation, preserving error ordering without a second path buffer.
    expanded.dfd = unsafe { dearust_get_perthread_wd() };
    // SAFETY: repr(C) argument matches original vchroot_expand_args with writable
    // NUL-terminated storage; callee follows no final symlink because flags=0.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    let mut stat = LinuxStat([0; 18]);
    // SAFETY: Linux x86_64 lstat syscall 6 writes 144 bytes into aligned storage.
    // Expanded path is live and NUL-terminated by the vchroot boundary contract.
    let result = unsafe {
        linux_syscall(
            expanded.path.as_ptr() as i64,
            &mut stat as *mut LinuxStat as i64,
            0,
            0,
            0,
            0,
            6,
        )
    };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result as i32));
    }
    // SAFETY: caller provides the correct writable Darwin stat/stat64 ABI layout.
    // Conversion consumes a fully initialized Linux layout and cannot unwind.
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
/// Non-null path must be readable through its first NUL or 4096 bytes. Non-null
/// output must be a writable Darwin stat struct. Guest TLS must be initialized.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_lstat(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: exported caller promises the path, output and guest TLS contracts.
    unsafe { lstat(path, output, false) }
}
/// # Safety
/// Same contract as sys_lstat, except output must be a Darwin stat64 struct.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_lstat64(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: exported caller promises the path, output and guest TLS contracts.
    unsafe { lstat(path, output, true) }
}
