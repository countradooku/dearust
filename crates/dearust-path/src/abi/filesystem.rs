//! Descriptor-pinned filesystem snapshots and fault-contained Darwin copyout.
use super::memory::{PathBuffer, copy_path, path_buffer};
use core::ffi::c_void;

unsafe extern "C" {
    fn linux_syscall(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, nr: i32) -> i64;
    fn dearust_get_perthread_wd() -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn vchroot_unexpand(path: *mut u8) -> i32;
    fn dearust_filesystem_server_snapshot(
        fd: i32,
        directory: i32,
        path: *const u8,
        path_length: u32,
        output: *mut u8,
        mountpoint: *mut u8,
    ) -> i64;
}

#[repr(C)]
struct Expanded {
    path: PathBuffer,
    flags: u32,
    dfd: i32,
}

#[path = "../filesystem_io.rs"]
// Runtime and Linux-server builds share this source with distinct syscall
// providers; all-features development builds deliberately instantiate both.
#[allow(clippy::duplicate_mod)]
mod io;
use io::{Fd, call};

#[inline(always)]
unsafe fn filesystem_syscall(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, nr: i32) -> i64 {
    // SAFETY: shared backend callers provide valid local storage and the exact
    // raw Linux ABI; this existing runtime boundary must not unwind.
    unsafe { linux_syscall(a, b, c, d, e, f, nr) }
}

fn finish_point(point: &mut PathBuffer, output: &mut [u8; 2168]) -> Result<(), i64> {
    // SAFETY: vchroot_unexpand consumes the first4096 bytes of its repr(C) path
    // struct. The audited bridge bounds growth and preserves a terminated,
    // initialized prefix; it accesses no uninitialized suffix as Rust storage.
    let result = unsafe { vchroot_unexpand(point.as_mut_ptr().cast()) };
    if result < 0 {
        return Err(i64::from(dearust_errno::linux_to_bsd(result)));
    }
    for (byte, output) in point.iter().take(1023).zip(output[88..1111].iter_mut()) {
        // SAFETY: the parser/foreign helper contract initializes every byte
        // through the first NUL. Stop there; never reference the unused tail.
        let byte = unsafe { byte.assume_init() };
        if byte == 0 {
            break;
        }
        *output = byte;
    }
    Ok(())
}

unsafe fn path_query(path: *const u8, output: *mut c_void) -> Result<(), i64> {
    // SAFETY: the exported caller provides initialized Darling guest TLS and
    // foreign helpers. Capture thread cwd before pathname validation, as C did.
    let dfd = unsafe { dearust_get_perthread_wd() };
    let mut expanded = Expanded {
        path: path_buffer(),
        flags: 1, // XNU statfs follows the final symlink.
        dfd,
    };
    copy_path(&mut expanded.path, path)?;
    // SAFETY: initialized NUL prefix in full4096-byte expansion storage. The
    // original resolver owns virtual-root and per-thread cwd interpretation.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return Err(i64::from(dearust_errno::linux_to_bsd(result)));
    }
    let mut converted = [0_u8; 2168];
    // SAFETY: the resolver supplied a terminated local path. O_PATH pins
    // its description without opening devices or reading file contents.
    match unsafe {
        call(
            257,
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            0x280000,
            0,
        )
    } {
        Ok(fd) => {
            let fd = Fd(fd);
            let result = io::snapshot(&fd, &mut expanded.path, &mut converted, None);
            if result == Err(-24) {
                // SAFETY: retain the pin while transferring its reference.
                // Scratch may already be overwritten; never reuse it as a path.
                unsafe {
                    server_snapshot(
                        fd.0 as i32,
                        -1,
                        core::ptr::null(),
                        0,
                        &mut converted,
                        &mut expanded.path,
                    )
                }?;
            } else {
                result?;
            }
        }
        Err(-24) => {
            let length = initialized_length(&expanded.path)?;
            // SAFETY: the initialized prefix includes its first byte. Real vchroot
            // expansion produces host-absolute paths; never use a server cwd instead.
            if unsafe { expanded.path[0].assume_init() } != b'/' {
                return Err(-24);
            }
            let mut authorized = [0_u64; 15];
            // SAFETY: raw statfs137 validates the path using this client's actual
            // Linux credentials and LSM before a server-assisted pathname open.
            unsafe {
                call(
                    137,
                    expanded.path.as_ptr() as i64,
                    authorized.as_mut_ptr() as i64,
                    0,
                    0,
                )
            }?;
            // SAFETY: RPC reads the initialized path before reusing its allocation
            // for the mountpoint; result storage stays distinct and live.
            unsafe {
                server_snapshot(
                    -1,
                    expanded.dfd,
                    expanded.path.as_ptr().cast(),
                    length,
                    &mut converted,
                    &mut expanded.path,
                )
            }?;
            let fsid = u64::from_le_bytes(converted[48..56].try_into().map_err(|_| -5_i64)?);
            let kind = u32::from_le_bytes(converted[60..64].try_into().map_err(|_| -5_i64)?);
            if fsid != authorized[7] || kind != authorized[0] as u32 {
                return Err(-70);
            }
        }
        Err(error) => return Err(error),
    }
    finish_point(&mut expanded.path, &mut converted)?;
    // SAFETY: the exported caller guarantees output ownership. Every byte of
    // converted is initialized; the shared boundary contains guest faults.
    unsafe { super::memory::copy_output(&converted, output) }
}

unsafe fn fd_query(fd: i32, output: *mut c_void) -> Result<(), i64> {
    let mut point = path_buffer();
    let mut converted = [0_u8; 2168];
    // SAFETY: F_DUPFD_CLOEXEC validates fd and pins its description for the
    // whole snapshot, including concurrent closes/reuse of the original fd.
    match unsafe { call(72, i64::from(fd), 1030, 0, 0) } {
        Ok(pinned) => {
            let pinned = Fd(pinned);
            let result = io::snapshot(&pinned, &mut point, &mut converted, None);
            if result == Err(-24) {
                // SAFETY: preserve the already-pinned fd until SCM_RIGHTS transfers
                // its reference. Both output allocations stay live through the RPC.
                unsafe {
                    server_snapshot(
                        pinned.0 as i32,
                        -1,
                        core::ptr::null(),
                        0,
                        &mut converted,
                        &mut point,
                    )
                }?;
            } else {
                result?;
            }
        }
        Err(-24 | -22) => {
            // Linux also returns EINVAL when the fixed minimum0 equals a
            // zero RLIMIT_NOFILE. The raw authorization probe below still
            // validates the existing descriptor before any delegation.
            // SAFETY: sendmsg validates and references the existing fd atomically;
            // sending SCM_RIGHTS requires no free client descriptor slot.
            unsafe { server_snapshot(fd, -1, core::ptr::null(), 0, &mut converted, &mut point) }?;
        }
        Err(error) => return Err(error),
    }
    finish_point(&mut point, &mut converted)?;
    // SAFETY: same initialized-local/output-ownership contract as path_query.
    unsafe { super::memory::copy_output(&converted, output) }
}

fn initialized_length(path: &PathBuffer) -> Result<u32, i64> {
    for (index, byte) in path.iter().enumerate() {
        // SAFETY: the resolver guarantees an initialized terminated prefix.
        // Stop at NUL without reading any uninitialized suffix.
        if unsafe { byte.assume_init() } == 0 {
            return Ok((index + 1) as u32);
        }
    }
    Err(-63)
}

unsafe fn server_snapshot(
    fd: i32,
    directory: i32,
    path: *const u8,
    length: u32,
    output: &mut [u8; 2168],
    point: &mut PathBuffer,
) -> Result<(), i64> {
    let mut authorized = [0_u64; 15];
    if fd >= 0 {
        // SAFETY: validate this client's credentials, LSM and seccomp policy
        // before transferring the existing descriptor. No free slot is needed.
        unsafe { call(138, i64::from(fd), authorized.as_mut_ptr() as i64, 0, 0) }?;
        if matches!(authorized[0], 0x5049_5045 | 0x534f_434b | 0x0904_1934) {
            return Err(-22);
        }
    }
    // SAFETY: caller supplies live descriptors or an initialized path, distinct
    // owned local result buffers, and the paired server/RPC implementation.
    let result = unsafe {
        dearust_filesystem_server_snapshot(
            fd,
            directory,
            path,
            length,
            output.as_mut_ptr(),
            point.as_mut_ptr().cast(),
        )
    };
    match result {
        0 => {
            if fd >= 0 {
                let fsid = u64::from_le_bytes(output[48..56].try_into().map_err(|_| -5_i64)?);
                let kind = u32::from_le_bytes(output[60..64].try_into().map_err(|_| -5_i64)?);
                if fsid != authorized[7] || kind != authorized[0] as u32 {
                    return Err(-70);
                }
            }
            Ok(())
        }
        error if error < 0 => Err(error),
        _ => Err(-5),
    }
}

/// # Safety
/// Guest TLS and foreign helpers must obey their ABI and not unwind. Output
/// must permit writes without conflicting Rust borrows, ownership or concurrent
/// accesses. Invalid guest mappings are diagnosed by fault-contained copies.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_statfs64(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: caller supplies the documented guest/TLS/output contracts.
    unsafe { path_query(path, output) }.err().unwrap_or(0)
}

/// # Safety
/// Same guest/TLS/output contracts as sys_statfs64. x86_64 uses its2168-byte ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_statfs(path: *const u8, output: *mut c_void) -> i64 {
    // SAFETY: x86_64 legacy name aliases the same layout, as original C does.
    unsafe { sys_statfs64(path, output) }
}

/// # Safety
/// Foreign helpers must obey their ABI and not unwind. Output must permit
/// writes without conflicting Rust borrows, ownership or concurrent accesses.
/// Invalid guest mappings are diagnosed by the shared fault-contained copy.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstatfs64(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: caller supplies the documented output ownership/ABI contracts.
    unsafe { fd_query(fd, output) }.err().unwrap_or(0)
}

/// # Safety
/// Same output/foreign ABI contracts and x86_64 layout as sys_fstatfs64.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_fstatfs(fd: i32, output: *mut c_void) -> i64 {
    // SAFETY: x86_64 legacy name aliases the same layout, as original C does.
    unsafe { sys_fstatfs64(fd, output) }
}
