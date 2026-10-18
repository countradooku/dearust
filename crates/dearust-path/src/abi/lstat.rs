use core::ffi::c_void;
#[repr(C)]
struct Expanded {
    path: super::memory::PathBuffer,
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
}
unsafe fn lstat(path: *const u8, output: *mut c_void, wide: bool) -> i64 {
    if path.is_null() || output.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: 0,
        dfd: 0,
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
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
    // SAFETY: stat is a live initialized Linux stat144. The shared boundary
    // converts initialized local storage and fault-contains guest output.
    let converted =
        unsafe { super::memory::copy_metadata((&stat as *const LinuxStat).cast(), output, wide) };
    if let Err(error) = converted {
        return error;
    }
    0
}
/// # Safety
/// Guest addresses use the shared fault-contained copy boundary; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_lstat(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: caller supplies initialized guest TLS and ABI-compatible helpers.
    unsafe { lstat(path, output, false) }
}
/// # Safety
/// Guest addresses use the shared fault-contained copy boundary; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_lstat64(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: caller supplies initialized guest TLS and ABI-compatible helpers.
    unsafe { lstat(path, output, true) }
}
