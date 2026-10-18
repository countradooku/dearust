//! Narrow unsafe ABI boundary. No guest pointer is dereferenced by Rust.
use crate::resources;
use core::ffi::c_void;

unsafe extern "C" {
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
    fn dearust_copyin(local: *mut c_void, guest: *const c_void, length: usize) -> i64;
    fn dearust_copyout(local: *const c_void, guest: *mut c_void, length: usize) -> i64;
}
fn result(status: i64) -> i64 {
    if status < 0 {
        i64::from(dearust_errno::linux_to_bsd(status as i32))
    } else {
        status
    }
}

/// Maps Darwin resource identifiers, preserving the original unsigned C helper.
#[unsafe(no_mangle)]
pub extern "C" fn rlimit_bsd_to_linux(which: u32) -> u32 {
    resources::resource(which).unwrap_or(u32::MAX)
}

/// Reads a resource limit into exactly sixteen guest ABI bytes.
///
/// # Safety
/// Output must permit foreign writes without conflicting Rust ownership or
/// concurrent access; inaccessible mappings are diagnosed by the copy boundary.
/// Imported syscall/copy helpers obey their C ABI, retain no pointers and cannot
/// unwind. Kernel checks alone do not enforce Rust's aliasing requirements.
///
/// ```compile_fail,E0133
/// dearust_process::sys_getrlimit(8,core::ptr::null_mut());
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_getrlimit(which: u32, output: *mut c_void) -> i64 {
    let Some(which) = resources::resource(which) else {
        return -22;
    };
    let mut local = [0_u64; 2];
    // SAFETY: Linux prlimit64(302) only writes the live initialized16-byte local
    // pair. NULL new_limit queries atomically; guest output is never passed here.
    let status =
        unsafe { linux_syscall(0, i64::from(which), 0, local.as_mut_ptr() as i64, 0, 0, 302) };
    if status < 0 {
        return result(status);
    }
    let converted = match resources::from_linux(local) {
        Ok(converted) => converted,
        Err(error) => return error,
    };
    let encoded = resources::encode_limit(converted);
    // SAFETY: encoded is initialized and live; caller supplies output ownership.
    unsafe { dearust_copyout(encoded.as_ptr().cast(), output, encoded.len()) }
}

/// Sets a resource limit after an exact, fault-contained sixteen-byte copyin.
///
/// # Safety
/// Input permits foreign reads of initialized bytes without conflicting Rust
/// ownership or concurrent mutation. Inaccessible mappings are allowed for fault
/// reporting. Imported syscall/copy helpers retain no pointers and cannot unwind.
///
/// ```compile_fail,E0133
/// dearust_process::sys_setrlimit(8,core::ptr::null());
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_setrlimit(which: u32, input: *const c_void) -> i64 {
    let mut bytes = [0_u8; 16];
    // SAFETY: bytes is initialized live local storage; caller owns guest input.
    // XNU copyin precedes resource-id/limit validation, even for an invalid id.
    let status = unsafe { dearust_copyin(bytes.as_mut_ptr().cast(), input, bytes.len()) };
    if status < 0 {
        return status;
    }
    let Some(which) = resources::resource(which) else {
        return -22;
    };
    let local = match resources::to_linux(resources::decode_limit(&bytes)) {
        Ok(local) => local,
        Err(error) => return error,
    };
    // SAFETY: local is the initialized Linux LP64 two-u64 structure, borrowed
    // synchronously. No guest pointer or new descriptor enters the kernel.
    result(unsafe { linux_syscall(0, i64::from(which), local.as_ptr() as i64, 0, 0, 0, 302) })
}

/// Reads process or waited-child usage into the Darwin LP64144-byte layout.
///
/// # Safety
/// Output permits foreign writes without conflicting Rust ownership/concurrent
/// access. Invalid mappings are diagnosed without guest Rust references. Imported
/// helpers preserve the C ABI, retain no pointers and cannot unwind.
///
/// ```compile_fail,E0133
/// dearust_process::sys_getrusage(0,core::ptr::null_mut());
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_getrusage(who: i32, output: *mut c_void) -> i64 {
    if who != 0 && who != -1 {
        return -22;
    }
    let mut local = [0_i64; 18];
    // SAFETY: Linux x86_64 getrusage98 writes initialized local144-byte storage;
    // only Darwin SELF/CHILDREN reach it, not Linux's extra THREAD domain.
    let status =
        unsafe { linux_syscall(i64::from(who), local.as_mut_ptr() as i64, 0, 0, 0, 0, 98) };
    if status < 0 {
        return result(status);
    }
    resources::usage(&mut local);
    // SAFETY: all144 converted bytes, including timeval padding, are initialized
    // local storage live through synchronous copy. Caller owns guest output;
    // only the helper accesses that raw address and contains mapping faults.
    unsafe {
        dearust_copyout(
            local.as_ptr().cast(),
            output,
            core::mem::size_of_val(&local),
        )
    }
}
