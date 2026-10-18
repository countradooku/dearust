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
pub unsafe extern "C" fn sys_symlinkat(target: *const u8, fd: i32, link: *const u8) -> i64 {
    if link.is_null() {
        return -14;
    }
    let mut expanded = Expanded {
        path: super::memory::path_buffer(),
        flags: 0,
        dfd: 0,
    };
    if let Err(error) = super::memory::copy_path(&mut expanded.path, link) {
        return error;
    }
    // SAFETY: resolve the original guest descriptor/TLS helper only after path
    // validation, preserving error ordering without a second path buffer.
    expanded.dfd = unsafe { atfd(fd) };
    // SAFETY: repr(C) matches original args, with writable terminated storage.
    // flags=0 preserves a final link component and callee preserves termination.
    let result = unsafe { vchroot_expand(&mut expanded) };
    if result < 0 {
        return i64::from(dearust_errno::linux_to_bsd(result));
    }
    // SAFETY: Linux x86_64 symlinkat syscall 266 receives target unchanged for
    // kernel validation; destination path is live and terminated. No output
    // storage is shared with Rust, and neither guest path may alias stack state.
    let result = unsafe {
        linux_syscall(
            target as i64,
            i64::from(expanded.dfd),
            expanded.path.as_ptr() as i64,
            0,
            0,
            0,
            266,
        )
    };
    if result < 0 {
        i64::from(dearust_errno::linux_to_bsd(result as i32))
    } else {
        result
    }
}
