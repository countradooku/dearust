use core::ffi::c_void;

#[repr(C, align(8))]
struct LinuxStat([u64; 18]);

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

unsafe fn fstat(fd: i32, output: *mut c_void, wide: bool) -> i64 {
    let mut stat = LinuxStat([0; 18]);
    // SAFETY: raw Linux x86_64 fstat5 validates the signed descriptor and writes
    // within this live initialized144-byte, eight-aligned Linux stat buffer.
    // The foreign ABI must be preserved and must not unwind into Rust.
    let result = unsafe {
        linux_syscall(
            i64::from(fd),
            (&mut stat as *mut LinuxStat) as i64,
            0,
            0,
            0,
            0,
            5,
        )
    } as i32;
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // Kernel errors retain precedence: fstat(-1,NULL) must remain EBADF.
    if output.is_null() {
        return -14;
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
pub unsafe extern "C" fn sys_fstat(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: caller provides the non-aliasing and foreign ABI contract.
    unsafe { fstat(fd, output, false) }
}

/// # Safety
/// Guest addresses use the shared fault-contained copy boundary; output uses the matching Darwin
/// stat layout. Output must not alias live wrapper storage. Guest TLS must be
/// initialized where used. Foreign helpers must obey their ABI, preserve path
/// termination, and must not retain pointers or unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstat64(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: caller provides the non-aliasing and foreign ABI contract.
    unsafe { fstat(fd, output, true) }
}
