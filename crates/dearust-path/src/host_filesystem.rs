//! Linux server provider, sharing the exact client filesystem parser/backend.
use core::ffi::c_void;
use core::mem::{ManuallyDrop, MaybeUninit};

#[path = "filesystem_io.rs"]
// The runtime instance uses the Mach-O raw Linux provider; the server uses libc.
#[allow(clippy::duplicate_mod)]
mod io;

unsafe extern "C" {
    fn syscall(number: i64, ...) -> i64;
    fn __errno_location() -> *mut i32;
}

unsafe fn filesystem_syscall(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, nr: i32) -> i64 {
    // SAFETY: shared backend callers supply the Linux x86_64 argument layout
    // and live local storage. This provider runs on normal Linux server threads.
    let result = unsafe { syscall(i64::from(nr), a, b, c, d, e, f) };
    if result < 0 {
        // SAFETY: libc supplies the current server thread's initialized errno.
        -i64::from(unsafe { *__errno_location() })
    } else {
        result
    }
}

/// Produce a host filesystem snapshot without changing virtual-root names.
///
/// # Safety
/// Output and point must be distinct initialized writable2168/4096-byte buffers
/// with exclusive ownership. A nonnegative fd/directory is borrowed and must
/// stay open throughout this call; the RPC request owns received descriptors.
/// Otherwise path is a live initialized NUL-terminated local pathname. A
/// non-NULL mountinfo path is initialized, terminated and at most4095 bytes.
/// Foreign libc calls must obey their ABI and not unwind into Rust.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dearust_filesystem_host_snapshot(
    fd: i32,
    directory: i32,
    path: *const u8,
    output: *mut c_void,
    point: *mut c_void,
    mountinfo: *const u8,
) -> i64 {
    // SAFETY: the server supplies distinct fully initialized local allocations.
    let output = unsafe { &mut *output.cast::<[u8; 2168]>() };
    // SAFETY: MaybeUninit has identical layout; the server initialized the buffer.
    let point = unsafe { &mut *point.cast::<[MaybeUninit<u8>; 4096]>() };
    let mut mountinfo_length = 0;
    let mountinfo = if mountinfo.is_null() {
        None
    } else {
        loop {
            if mountinfo_length >= 4096 {
                return -63;
            }
            // SAFETY: caller guarantees an initialized terminated local prefix;
            // only read through its first NUL, within the declared bound.
            let byte = unsafe { *mountinfo.add(mountinfo_length) };
            mountinfo_length += 1;
            if byte == 0 {
                break;
            }
        }
        // SAFETY: the checked prefix includes its terminator and is initialized.
        Some(unsafe { core::slice::from_raw_parts(mountinfo, mountinfo_length) })
    };
    let result = if fd >= 0 {
        let fd = ManuallyDrop::new(io::Fd(i64::from(fd)));
        io::snapshot(&fd, point, output, mountinfo)
    } else if path.is_null() {
        Err(-14)
    } else {
        // SAFETY: the caller supplies a terminated local path. O_PATH pins its
        // description without opening devices or reading file contents.
        unsafe { io::call(257, i64::from(directory), path as i64, 0x280000, 0) }
            .and_then(|fd| io::snapshot(&io::Fd(fd), point, output, mountinfo))
    };
    result.err().unwrap_or(0)
}
