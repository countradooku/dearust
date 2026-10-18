//! Stream I/O: guest buffers stay raw and Linux validates actual accesses.
use core::ffi::c_void;

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn sys_pthread_canceled(action: i32) -> i64;
    fn __real_ioctl(fd: i32, request: i32, argument: *mut c_void) -> i64;
}

fn result(value: i64) -> i64 {
    if value < 0 {
        i64::from(dearust_errno::linux_to_bsd(value as i32))
    } else {
        value
    }
}

// Darwin validates descriptor access before rejecting nbyte > INT_MAX. Keep
// this exceptional path out of ordinary I/O, and never touch its guest buffer.
fn oversized(fd: i32, writing: bool) -> i64 {
    // SAFETY: raw Linux fcntl72/F_GETFL3 takes only integer arguments, performs
    // no I/O, and does not access caller memory. The imported ABI cannot unwind.
    let flags = unsafe { linux_syscall(i64::from(fd), 3, 0, 0, 0, 0, 72) };
    if flags < 0 {
        return result(flags);
    }
    let access = flags & 3;
    if flags & 0x20_0000 != 0 || access == 3 || access == i64::from(!writing) {
        -9
    } else {
        -22
    }
}

/// Read at most INT_MAX bytes. Linux checks the raw guest address at access.
///
/// # Safety
/// Guest output must permit writes without conflicting Rust references,
/// ownership restrictions or concurrent access. Kernel address checks do not
/// enforce Rust ownership.
/// Inaccessible mappings are permitted and diagnosed by the kernel. Foreign
/// helpers must obey their ABI and not unwind into Rust.
///
/// ```compile_fail,E0133
/// dearust_fd::sys_read_nocancel(0, core::ptr::null_mut(), 1);
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_read_nocancel(fd: i32, buffer: *mut c_void, count: usize) -> i64 {
    if count > i32::MAX as usize {
        return oversized(fd, false);
    }
    // SAFETY: raw Linux read0 validates fd and the guest buffer/count; Rust
    // forms no reference to that memory. The foreign ABI must not unwind.
    let value = unsafe { linux_syscall(i64::from(fd), buffer as i64, count as i64, 0, 0, 0, 0) };
    if value == -5 && pty_master(fd) {
        return 0;
    }
    result(value)
}

// Neither probe affects ordinary I/O. Legacy BSD masters have character-device
// major2 and no TIOCGPTN implementation; retain their hangup EOF as well.
#[cold]
#[inline(never)]
fn pty_master(fd: i32) -> bool {
    let mut number = 0_u32;
    // SAFETY: Linux TIOCGPTN writes exactly one initialized local u32. This
    // master-only probe excludes slave/background EIO and uses no shared data.
    if unsafe { __real_ioctl(fd, 0x8004_5430_u32 as i32, (&mut number as *mut u32).cast()) } == 0 {
        return true;
    }
    legacy_pty_master(fd)
}

#[cold]
#[inline(never)]
fn legacy_pty_master(fd: i32) -> bool {
    let mut stat = [0_u64; 18];
    // SAFETY: Linux x86_64 fstat5 writes its144-byte layout into initialized,
    // eight-byte-aligned local storage. No guest memory or retained pointer.
    if unsafe { linux_syscall(i64::from(fd), stat.as_mut_ptr() as i64, 0, 0, 0, 0, 5) } < 0 {
        return false;
    }
    let mode = stat[3] as u32;
    let device = stat[5];
    let major = ((device >> 8) & 0xfff) | ((device >> 32) & 0xffff_f000);
    mode & 0xf000 == 0x2000 && major == 2
}

/// Perform the original cancellation check once, before descriptor or memory use.
///
/// # Safety
/// The caller must uphold sys_read_nocancel's output ownership and foreign ABI
/// contracts, and initialize the guest cancellation helper's thread state.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_read(fd: i32, buffer: *mut c_void, count: usize) -> i64 {
    // SAFETY: original thread cancellation helper accepts action0, preserves
    // guest TLS/server policy and must obey its ABI without unwinding into Rust.
    if unsafe { sys_pthread_canceled(0) } == 0 {
        return -4;
    }
    // SAFETY: the caller supplies the same raw output ownership/ABI contract.
    unsafe { sys_read_nocancel(fd, buffer, count) }
}

/// Write at most INT_MAX bytes without retries or a cancellation check.
///
/// # Safety
/// Guest input must permit reads without violating live Rust ownership or
/// racing writes, and must not disclose uninitialized Rust storage. Inaccessible
/// mappings are diagnosed by the kernel. Foreign helpers must obey their ABI
/// and not unwind into Rust.
///
/// ```compile_fail,E0133
/// dearust_fd::sys_write_nocancel(1, core::ptr::null(), 1);
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_write_nocancel(fd: i32, buffer: *const c_void, count: usize) -> i64 {
    if count > i32::MAX as usize {
        return oversized(fd, true);
    }
    // SAFETY: raw Linux write1 validates the raw guest address/count; Rust never
    // dereferences it. SIGPIPE, short writes and EINTR remain kernel decisions.
    result(unsafe { linux_syscall(i64::from(fd), buffer as i64, count as i64, 0, 0, 0, 1) })
}

/// Perform the original cancellation check once, before descriptor or memory use.
///
/// # Safety
/// The caller must uphold sys_write_nocancel's input ownership and foreign ABI
/// contracts, and initialize the guest cancellation helper's thread state.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_write(fd: i32, buffer: *const c_void, count: usize) -> i64 {
    // SAFETY: same original cancellation-helper contract as sys_read.
    if unsafe { sys_pthread_canceled(0) } == 0 {
        return -4;
    }
    // SAFETY: the caller supplies the same raw input ownership/ABI contract.
    unsafe { sys_write_nocancel(fd, buffer, count) }
}

/// Preserve the public tracing boundary, which deliberately skips cancellation.
///
/// # Safety
/// The caller must uphold sys_write_nocancel's input ownership and foreign ABI
/// contracts. This raw-buffer boundary does not validate Rust aliasing rules.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn __write_for_xtrace(fd: i32, buffer: *const c_void, count: usize) -> i64 {
    // SAFETY: the tracing caller supplies the same raw input contract.
    unsafe { sys_write_nocancel(fd, buffer, count) }
}
