#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
unsafe extern "C" {
    fn atfd(fd: i32) -> i32;
    fn vchroot_expand(args: *mut Expanded) -> i32;
    fn linux_syscall(a1: i64, a2: i64, a3: i64, a4: i64, a5: i64, a6: i64, nr: i32) -> i64;
}

/// # Safety
/// Non-null link must be readable through NUL or 4096 bytes. Target is passed
/// unchanged to Linux and is subject to kernel pointer/path validation. Calling
/// thread must have initialized Darling TLS. Foreign calls must not unwind.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_symlinkat(target: *const u8, fd: i32, link: *const u8) -> i64 {
    if link.is_null() {
        return -14;
    }
    let path = match crate::path::copy(|index| {
        // SAFETY: caller supplies readable bytes through NUL or capacity;
        // the safe copier reads only indexes <4096 and stops immediately at NUL.
        unsafe { link.add(index).read() }
    }) {
        Ok(path) => path,
        Err(error) => return error,
    };
    let mut expanded = Expanded {
        path,
        flags: 0,
        // SAFETY: original helper resolves BSD_AT_FDCWD using guest thread TLS.
        // Unlike original symlinkat, this honors the actual passed descriptor.
        dfd: unsafe { atfd(fd) },
    };
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
