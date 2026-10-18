use core::ffi::c_void;

#[repr(C, align(8))]
struct LinuxStat([u64; 18]);

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn stat_linux_to_bsd(input: *const LinuxStat, output: *mut c_void);
    fn stat_linux_to_bsd64(input: *const LinuxStat, output: *mut c_void);
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
    if wide {
        // SAFETY: caller provides aligned writable Darwin stat64 storage with no
        // conflicting access; original converter reads the live LinuxStat only.
        unsafe { stat_linux_to_bsd64(&stat, output) };
    } else {
        // SAFETY: caller provides aligned writable Darwin stat storage with no
        // conflicting access; original converter reads the live LinuxStat only.
        unsafe { stat_linux_to_bsd(&stat, output) };
    }
    0
}

/// # Safety
/// Non-null output must be aligned writable Darwin stat storage with no
/// conflicting access. Foreign callees must obey their ABI and not unwind.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstat(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: forwards the caller's storage contract to the shared wrapper.
    unsafe { fstat(fd, output, false) }
}

/// # Safety
/// Non-null output must be aligned writable Darwin stat64 storage with no
/// conflicting access. Foreign callees must obey their ABI and not unwind.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstat64(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: forwards the caller's storage contract to the shared wrapper.
    unsafe { fstat(fd, output, true) }
}
