#[repr(C)]
struct Expanded {
    path: super::memory::PathBuffer,
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Guest TLS must be initialized. Foreign helpers must obey their ABI,
/// preserve path termination and must not retain pointers or unwind into Rust.
/// Guest addresses use the shared fault-contained copy boundary. Kernel output must not alias live
/// wrapper storage.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_readlinkat(
    fd: i32,
    path: *const u8,
    buf: *mut u8,
    count: usize,
) -> i64 {
    // XNU checks its size_t bound before pathname/descriptor lookup.
    if count > i32::MAX as usize {
        return -22;
    }
    if path.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: 0,
        // SAFETY: original helper resolves BSD_AT_FDCWD using initialized TLS.
        dfd: unsafe { atfd(fd) },
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, path) {
        return error;
    }
    // SAFETY: repr(C) matches original writable, terminated args. flags=0 keeps
    // the final symlink component; expansion must preserve termination.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // XNU permits a zero-byte request on a symlink, including NULL output.
    // Linux rejects zero before lookup. Probe one local byte to preserve lookup,
    // type and permission errors, while never accessing the guest output.
    let mut probe = 0_u8;
    let output = if count == 0 { &mut probe } else { buf };
    let transfer_count = if count == 0 { 1 } else { count };
    // SAFETY: Linux readlinkat267 validates actual output access. Path storage
    // is live and terminated; the zero-size path uses one initialized local byte.
    // No Rust reference is formed over guest memory; no NUL byte is appended.
    let result = unsafe {
        linux_syscall(
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            output as i64,
            transfer_count as i64,
            0,
            0,
            267,
        )
    } as i32;
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result))
    } else if count == 0 {
        0
    } else {
        i64::from(result)
    }
}
