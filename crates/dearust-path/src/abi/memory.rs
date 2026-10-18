//! Kernel-mediated access to untrusted guest addresses. No guest address is
//! dereferenced by Rust or passed to the retained C metadata converters.
use core::ffi::c_void;

#[repr(C)]
struct Iovec {
    base: *mut c_void,
    length: usize,
}

#[repr(C, align(8))]
struct DarwinStat([u8; 144]);

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn stat_linux_to_bsd(input: *const c_void, output: *mut c_void);
    fn stat_linux_to_bsd64(input: *const c_void, output: *mut c_void);
}

fn current_pid() -> i64 {
    // SAFETY: raw Linux getpid39 has no pointer arguments. This value is scoped
    // to one copy operation, never cached across wrappers or foreign helpers.
    unsafe { linux_syscall(0, 0, 0, 0, 0, 0, 39) }
}

fn transfer(pid: i64, local: *mut c_void, guest: *mut c_void, length: usize, write: bool) -> i64 {
    let local = Iovec {
        base: local,
        length,
    };
    let remote = Iovec {
        base: guest,
        length,
    };
    if pid < 0 {
        return pid;
    }
    // SAFETY: initialized LP64 iovecs remain live through the synchronous Linux
    // syscall. The kernel validates guest mappings and permissions; it never
    // exposes a guest reference to Rust. Callers provide live local storage.
    unsafe {
        linux_syscall(
            pid,
            &local as *const Iovec as i64,
            1,
            &remote as *const Iovec as i64,
            1,
            0,
            if write { 311 } else { 310 },
        )
    }
}

pub(super) fn copy_path(output: &mut [u8; 4096], guest: *const u8) -> Result<(), i64> {
    if guest.is_null() {
        return Err(-14);
    }
    // Most paths fit one cache line. Avoid reading/pinning the rest of a
    // page for these common inputs. A readable prefix containing NUL remains
    // sufficient even when the following page is inaccessible.
    const FIRST: usize = 64;
    let pid = current_pid();
    let first = transfer(
        pid,
        output.as_mut_ptr().cast(),
        guest.cast_mut().cast(),
        FIRST,
        false,
    );
    if first < 0 {
        return Err(i64::from(dearust_errno::linux_to_bsd(first as i32)));
    }
    let first = usize::try_from(first).unwrap_or(0).min(FIRST);
    if crate::path::finish_copy(output, first).is_ok() {
        return Ok(());
    }
    if first != FIRST {
        return Err(-14);
    }
    // Address arithmetic is checked integer arithmetic, never a guest pointer
    // offset or dereference. Reuse the current PID only within this synchronous
    // copy, with no retained C callback between its two bounded kernel reads.
    let next = (guest as usize).checked_add(FIRST).ok_or(-14_i64)?;
    let remaining = output.len() - FIRST;
    let count = transfer(
        pid,
        output[FIRST..].as_mut_ptr().cast(),
        next as *mut c_void,
        remaining,
        false,
    );
    if count < 0 {
        return Err(i64::from(dearust_errno::linux_to_bsd(count as i32)));
    }
    let count = usize::try_from(count).unwrap_or(0).min(remaining);
    crate::path::finish_copy(output, FIRST + count)
}

/// Input must point to a live initialized aligned Linux x86_64 stat144.
/// C converters must obey their original ABI, write at most120/144 bytes and
/// never retain their arguments or unwind. Guest output need not be valid.
pub(super) unsafe fn copy_metadata(
    input: *const c_void,
    output: *mut c_void,
    wide: bool,
) -> Result<(), i64> {
    // Initialize reserved fields and padding, which the original converters do
    // not all assign, before copying the exact guest ABI size to untrusted memory.
    let mut converted = DarwinStat([0; 144]);
    // SAFETY: both converter arguments refer only to initialized local storage;
    // input follows this function's Linux stat contract. The maximum wide
    // output fits144bytes; both structures require eight-byte alignment.
    unsafe {
        if wide {
            stat_linux_to_bsd64(input, converted.0.as_mut_ptr().cast());
        } else {
            stat_linux_to_bsd(input, converted.0.as_mut_ptr().cast());
        }
    }
    let length = if wide { 144 } else { 120 };
    let count = transfer(
        current_pid(),
        converted.0.as_mut_ptr().cast(),
        output,
        length,
        true,
    );
    if count == length as i64 {
        Ok(())
    } else if count < 0 {
        Err(i64::from(dearust_errno::linux_to_bsd(count as i32)))
    } else {
        // Linux can write a prefix before an inaccessible page. Never report
        // successful metadata output after a short write; no atomicity claim.
        Err(-14)
    }
}
