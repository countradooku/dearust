//! Fault-contained access to untrusted guest addresses. Guest memory is accessed
//! only by guarded assembly or Linux, never by Rust references or C converters.
use core::ffi::c_void;
use core::mem::MaybeUninit;

// C may inspect only the initialized NUL-terminated prefix after copy_path
// succeeds. No Rust byte reference ever covers the uninitialized suffix.
pub(super) type PathBuffer = [MaybeUninit<u8>; crate::path::CAPACITY];
pub(super) const fn path_buffer() -> PathBuffer {
    [MaybeUninit::uninit(); crate::path::CAPACITY]
}

/// The first `copied` bytes must have been initialized by a successful kernel
/// transfer. Both callers clamp counts to the exact transfer capacity.
unsafe fn finish_initialized(output: &PathBuffer, copied: usize) -> Result<(), i64> {
    // SAFETY: caller proves this prefix initialized; MaybeUninit<u8> has the
    // same layout as u8. The uninitialized suffix never becomes a Rust slice.
    let prefix = unsafe { core::slice::from_raw_parts(output.as_ptr().cast::<u8>(), copied) };
    crate::path::finish_copy(prefix, copied)
}

#[repr(C)]
struct Iovec {
    base: *mut c_void,
    length: usize,
}

#[repr(C, align(8))]
struct DarwinStat([u8; 144]);

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn __getuidgid(uid: *mut i32, gid: *mut i32);
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

// Block catchable signals across PID lookup and the entire kernel transfer.
// A handler cannot fork and return in the child with a stale parent PID. SIGKILL
// and SIGSTOP have no userspace handlers and cannot create that window.
fn with_signals_blocked(operation: impl FnOnce() -> Result<(), i64>) -> Result<(), i64> {
    let all = u64::MAX;
    let mut previous = 0_u64;
    // SAFETY: both masks are initialized live eight-byte Linux signal masks.
    let blocked = unsafe {
        linux_syscall(
            0,
            &all as *const u64 as i64,
            &mut previous as *mut u64 as i64,
            8,
            0,
            0,
            14,
        )
    };
    if blocked < 0 {
        return Err(i64::from(dearust_errno::linux_to_bsd(blocked as i32)));
    }
    let result = operation();
    // SAFETY: restore exactly the mask returned by the successful block call.
    let restored = unsafe { linux_syscall(2, &previous as *const u64 as i64, 0, 8, 0, 0, 14) };
    if restored < 0 {
        // Continuing with all catchable signals blocked would corrupt the
        // calling thread's observable state. A policy denying restoration is
        // fatal rather than a normal syscall error; do not depend on libc or
        // signal delivery to terminate this inconsistent process.
        // SAFETY: raw Linux exit_group231 has no pointer arguments.
        unsafe { linux_syscall(127, 0, 0, 0, 0, 0, 231) };
        // SAFETY: if a syscall policy also denies exit_group, terminate at a
        // defined machine trap instead of continuing or invoking Rust UB.
        unsafe { core::arch::asm!("ud2", options(noreturn)) };
    }
    result
}

pub(super) fn copy_path(output: &mut PathBuffer, guest: *const u8) -> Result<(), i64> {
    if guest.is_null() {
        return Err(-14);
    }
    if let Some(result) = super::guarded::copy_path(output, guest) {
        return result;
    }
    with_signals_blocked(|| copy_path_blocked(output, guest))
}

fn copy_path_blocked(output: &mut PathBuffer, guest: *const u8) -> Result<(), i64> {
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
    // SAFETY: the successful first transfer initialized exactly this prefix.
    if unsafe { finish_initialized(output, first) }.is_ok() {
        return Ok(());
    }
    if first != FIRST {
        return Err(-14);
    }
    // Address arithmetic is checked integer arithmetic, never a guest pointer
    // offset or dereference. Catchable signals remain blocked across both
    // bounded kernel reads, so the PID cannot become stale through a handler.
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
    // SAFETY: the first full64 bytes and next reported count are initialized.
    unsafe { finish_initialized(output, FIRST + count) }
}

/// Input must point to a live initialized aligned Linux x86_64 stat144.
/// The credential helper must not retain pointers or unwind. Guest output need
/// not be valid. Conversion accesses initialized local storage only.
pub(super) unsafe fn copy_metadata(
    input: *const c_void,
    output: *mut c_void,
    wide: bool,
) -> Result<(), i64> {
    // Initialize reserved fields and padding, which the original converters do
    // not all assign, before copying the exact guest ABI size to untrusted memory.
    let mut converted = DarwinStat([0; 144]);
    let mut uid = 0;
    let mut gid = 0;
    // SAFETY: input is a live initialized 144-byte local Linux stat under this
    // function's contract. No guest address becomes a Rust reference. Original
    // __getuidgid synchronously writes both local i32 values under one lock and
    // retains Darling's process credential policy without caching in Rust.
    unsafe {
        __getuidgid(&mut uid, &mut gid);
        super::conversion::convert(
            &*input.cast::<[u8; 144]>(),
            &mut converted.0,
            wide,
            uid,
            gid,
        );
    }
    let length = if wide { 144 } else { 120 };
    // SAFETY: converted storage is initialized and live for the exact ABI size.
    let guarded = unsafe { super::guarded::copy_output(converted.0.as_ptr(), output, length) };
    if let Some(result) = guarded {
        return result;
    }
    with_signals_blocked(|| {
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
            // A prefix may be written before a fault; never report success.
            Err(-14)
        }
    })
}
