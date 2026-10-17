//! OS and caller-buffer boundary. See SAFETY.txt.
use crate::info;
use crate::parse::{self, Path};
use core::ffi::c_void;
unsafe extern "C" {
    fn linux_syscall(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, n: i32) -> i64;
    fn errno_linux_to_bsd(error: i32) -> i32;
    fn darling_legacy_proc_info(
        call: u32,
        pid: i32,
        flavor: u32,
        arg: u64,
        buffer: *mut c_void,
        size: i32,
    ) -> i64;
    fn dserver_rpc_get_executable_path(pid: i32, path: *mut u8, size: u64, length: *mut u64)
    -> i32;
    fn dserver_rpc_task_is_64_bit(pid: i32, value: *mut bool) -> i32;
    fn native_sysconf(name: i32) -> i64;
    fn sys_getuid() -> i64;
    fn sys_getgid() -> i64;
    fn mach_task_self() -> u32;
    fn task_name_for_pid(task: u32, pid: i32, name: *mut u32) -> i32;
    fn task_info(task: u32, flavor: i32, info: *mut u32, count: *mut u32) -> i32;
    fn mach_port_deallocate(task: u32, name: u32) -> i32;
    fn vchroot_unexpand(path: *mut u8) -> i32;
    fn vchroot_expand(path: *mut Expanded) -> i32;
    fn oflags_linux_to_bsd(flags: i32) -> i32;
}
// Every call site provides live bounded storage or scalar arguments. Raw Linux
// syscalls neither unwind nor set Darwin errno. We translate negative results once.
#[inline(always)]
fn syscall(n: i32, a: i64, b: i64, c: i64, d: i64) -> Result<i64, i64> {
    // SAFETY: Arguments are constructed at the typed OS boundaries below.
    let result = unsafe { linux_syscall(a, b, c, d, 0, 0, n) };
    if result < 0 {
        // SAFETY: Pure C errno conversion, accepts every signed integer.
        Err(unsafe { errno_linux_to_bsd(result as i32) } as i64)
    } else {
        Ok(result)
    }
}
#[repr(C)]
struct Expanded {
    path: [u8; 4096],
    flags: u32,
    dfd: i32,
}
fn expand(p: &Path, follow: bool) -> Result<Expanded, i64> {
    let mut result = Expanded {
        path: [0; 4096],
        flags: u32::from(follow),
        dfd: -100,
    };
    // SAFETY: Path always owns a NUL-terminated 96-byte array. Both allocations
    // are live and nonoverlapping. The existing expansion ABI owns 4096 bytes.
    unsafe {
        core::ptr::copy_nonoverlapping(p.as_ptr(), result.path.as_mut_ptr(), 96);
        let rv = vchroot_expand(&mut result);
        if rv != 0 {
            return Err(errno_linux_to_bsd(rv) as i64);
        }
    }
    Ok(result)
}
struct Fd(i64);
impl Drop for Fd {
    #[inline(always)]
    fn drop(&mut self) {
        let _ = syscall(3, self.0, 0, 0, 0);
    }
}
fn open(path: &Path, flags: i64) -> Result<Fd, i64> {
    let expanded = expand(path, true)?;
    syscall(2, expanded.path.as_ptr() as i64, flags | 0x80000, 0, 0).map(Fd)
}
fn read(path: &Path, data: &mut [u8]) -> Result<usize, i64> {
    let fd = open(path, 0)?;
    loop {
        match syscall(0, fd.0, data.as_mut_ptr() as i64, data.len() as i64, 0) {
            Err(-4) => continue,
            result => return result.map(|v| v as usize),
        }
    }
}
fn path(pid: u32, suffix: &[u8]) -> Result<Path, i64> {
    Path::proc(pid, suffix).ok_or(-22)
}
fn enumerate(directory: Path, mut visit: impl FnMut(u32) -> Result<bool, i64>) -> Result<(), i64> {
    // Keep the path owned until open returns; no libc directory allocator.
    let fd = open(&directory, 0x10000)?;
    let mut data = [0; 4096];
    loop {
        let n = match syscall(217, fd.0, data.as_mut_ptr() as i64, data.len() as i64, 0) {
            Err(-4) => continue,
            other => other? as usize,
        };
        if n == 0 {
            return Ok(());
        }
        let mut remaining = data.get(..n).ok_or(-5)?;
        while !remaining.is_empty() {
            let (len, name) = parse::dirent(remaining).ok_or(-5)?;
            if let Some(value) = parse::number(name, 10).and_then(|v| u32::try_from(v).ok()) {
                if visit(value)? {
                    return Ok(());
                }
            }
            remaining = remaining.get(len..).ok_or(-5)?;
        }
    }
}
fn credentials(pid: u32) -> Option<[u32; 8]> {
    let mut name = 0;
    let mut token = [0; 8];
    let mut count = 8;
    // SAFETY: Existing MIG APIs receive live correctly sized stack storage.
    unsafe {
        let task = mach_task_self();
        if task_name_for_pid(task, pid as i32, &mut name) != 0 {
            return None;
        }
        let result = task_info(name, 15, token.as_mut_ptr(), &mut count);
        mach_port_deallocate(task, name);
        if result != 0 || count != 8 {
            None
        } else {
            Some(token)
        }
    }
}
fn matches(pid: u32, kind: i32, id: u32) -> bool {
    let Some(token) = credentials(pid) else {
        return false;
    };
    match kind {
        1 => true,
        // Darling currently represents real and effective UID with one server
        // credential (sys_getuid/sys_geteuid share it); Linux UID is unrelated.
        4 | 5 => token.get(1) == Some(&id),
        2 | 3 | 6 => {
            let mut bytes = [0; 4096];
            let Ok(p) = path(pid, b"/stat") else {
                return false;
            };
            let Ok(n) = read(&p, &mut bytes) else {
                return false;
            };
            let field = match kind {
                2 => 5,
                3 => 7,
                _ => 4,
            };
            bytes
                .get(..n)
                .and_then(|b| parse::stat_field(b, field))
                .and_then(|b| parse::number(b, 10))
                == Some(id as u64)
        }
        _ => false,
    }
}
struct Output {
    ptr: *mut u8,
    capacity: usize,
    used: usize,
    sizing: bool,
}
impl Output {
    fn new(ptr: *mut c_void, size: i32) -> Result<Self, i64> {
        if size < 0 {
            return Err(-22);
        }
        Ok(Self {
            ptr: ptr.cast(),
            capacity: size as usize,
            used: 0,
            sizing: ptr.is_null(),
        })
    }
    fn emit(&mut self, bytes: &[u8]) -> bool {
        if self.sizing {
            self.used = self.used.saturating_add(bytes.len());
            return false;
        }
        if self.capacity.saturating_sub(self.used) < bytes.len() {
            return true;
        }
        // SAFETY: Exported ABI requires a writable allocation of `size` bytes.
        // Capacity arithmetic guarantees this copy stays in it. Source is local.
        unsafe {
            core::ptr::copy_nonoverlapping(bytes.as_ptr(), self.ptr.add(self.used), bytes.len());
        }
        self.used += bytes.len();
        false
    }
}
fn list(kind: i32, id: u32, out: &mut Output) -> Result<i64, i64> {
    if !(1..=6).contains(&kind) {
        return Err(-22);
    }
    let mut root = Path::new();
    root.push(b"/proc").ok_or(-22)?;
    enumerate(root, |pid| {
        Ok(matches(pid, kind, id) && out.emit(&pid.to_ne_bytes()))
    })?;
    Ok(out.used.min(i32::MAX as usize) as i64)
}
fn put(bytes: &mut [u8], offset: usize, value: &[u8]) {
    if let Some(dst) = bytes.get_mut(offset..offset + value.len()) {
        dst.copy_from_slice(value);
    }
}
fn u64at(bytes: &[u8], offset: usize) -> u64 {
    bytes
        .get(offset..offset + 8)
        .and_then(|b| b.try_into().ok())
        .map(u64::from_ne_bytes)
        .unwrap_or(0)
}
fn u32at(bytes: &[u8], offset: usize) -> u32 {
    bytes
        .get(offset..offset + 4)
        .and_then(|b| b.try_into().ok())
        .map(u32::from_ne_bytes)
        .unwrap_or(0)
}
fn stat(p: &Path) -> Result<[u8; 144], i64> {
    let mut data = [0; 144];
    let expanded = expand(p, false)?;
    syscall(
        4,
        expanded.path.as_ptr() as i64,
        data.as_mut_ptr() as i64,
        0,
        0,
    )?;
    Ok(data)
}
fn vnode(p: &Path, out: &mut [u8], with_path: bool) -> Result<(), i64> {
    let s = stat(p)?;
    // x86_64 Linux stat -> fixed Darwin vinfo_stat, matching Darling st_dev.
    put(out, 0, &(u64at(&s, 0) as u32).to_ne_bytes());
    let mode = u32at(&s, 24);
    put(out, 4, &(mode as u16).to_ne_bytes());
    put(out, 6, &(u64at(&s, 16) as u16).to_ne_bytes());
    put(out, 8, &u64at(&s, 8).to_ne_bytes());
    // Match stat_linux_to_bsd64: the runtime presents virtual credentials.
    // SAFETY: Existing scalar credential APIs own their synchronization.
    let (uid, gid) = unsafe { (sys_getuid() as u32, sys_getgid() as u32) };
    put(out, 16, &uid.to_ne_bytes());
    put(out, 20, &gid.to_ne_bytes());
    for (dest, src) in [
        (24, 72),
        (32, 80),
        (40, 88),
        (48, 96),
        (56, 104),
        (64, 112),
        (88, 48),
        (96, 64),
    ] {
        put(out, dest, &u64at(&s, src).to_ne_bytes());
    }
    put(out, 104, &(u64at(&s, 56) as u32).to_ne_bytes());
    put(out, 116, &(u64at(&s, 40) as u32).to_ne_bytes());
    let kind: u32 = match mode & 0xf000 {
        0x8000 => 1,
        0x4000 => 2,
        0x6000 => 3,
        0x2000 => 4,
        0xa000 => 5,
        0xc000 => 6,
        0x1000 => 7,
        _ => 0,
    };
    put(out, 136, &kind.to_ne_bytes());
    if with_path {
        let mut target = [0; 4096];
        let expanded = expand(p, false)?;
        let n = syscall(
            89,
            expanded.path.as_ptr() as i64,
            target.as_mut_ptr() as i64,
            3999,
            0,
        )? as usize;
        if n >= 3999 {
            return Err(-63);
        }
        // SAFETY: vchroot_unexpand takes exactly a writable 4096-byte path.
        let rv = unsafe { vchroot_unexpand(target.as_mut_ptr()) };
        if rv != 0 {
            return Err(rv as i64);
        }
        let len = target.iter().position(|b| *b == 0).ok_or(-63)?;
        if len >= 1024 {
            return Err(-63);
        }
        if let Some(text) = target.get(..len) {
            put(out, 152, text);
        }
    }
    Ok(())
}
fn fdpath(pid: u32, fd: u32, suffix: &[u8]) -> Result<Path, i64> {
    let mut p = path(pid, suffix)?;
    p.decimal(fd).ok_or(-22)?;
    Ok(p)
}
fn fds(pid: u32, out: &mut Output) -> Result<i64, i64> {
    enumerate(path(pid, b"/fd")?, |fd| {
        let p = fdpath(pid, fd, b"/fd/")?;
        let s = match stat(&p) {
            Ok(s) => s,
            Err(-2) => return Ok(false),
            Err(e) => return Err(e),
        };
        let kind: u32 = match u32at(&s, 24) & 0xf000 {
            0xc000 => 2,
            0x1000 => 6,
            _ => 1,
        };
        let mut entry = [0; 8];
        put(&mut entry, 0, &fd.to_ne_bytes());
        put(&mut entry, 4, &kind.to_ne_bytes());
        Ok(out.emit(&entry))
    })?;
    Ok(out.used as i64)
}
fn threads(pid: u32, out: &mut Output) -> Result<i64, i64> {
    enumerate(path(pid, b"/task")?, |tid| {
        Ok(out.emit(&(tid as u64).to_ne_bytes()))
    })?;
    Ok(out.used as i64)
}
fn vnode_query(pid: u32, fd: u32, flavor: u32, out: &mut Output) -> Result<i64, i64> {
    let len = if flavor == 1 { 176 } else { 1200 };
    if out.sizing {
        return Err(-14);
    }
    if out.capacity < len {
        return Err(-28);
    }
    let mut bytes = [0; 1200];
    vnode(
        &fdpath(pid, fd, b"/fd/")?,
        bytes.get_mut(24..len).ok_or(-22)?,
        flavor == 2,
    )?;
    let mut info = [0; 512];
    let n = read(&fdpath(pid, fd, b"/fdinfo/")?, &mut info)?;
    let info = info.get(..n).ok_or(-5)?;
    if let Some(flags) = parse::field(info, b"flags:", 0).and_then(|s| parse::number(s, 8)) {
        // SAFETY: Pure original scalar flag conversion; no pointer arguments.
        put(&mut bytes, 0, &{
            // Darwin stores FREAD/FWRITE (1/2), not O_ACCMODE (0/1/2).
            // SAFETY: Pure scalar flag conversion from the existing ABI.
            let converted = unsafe { oflags_linux_to_bsd(flags as i32) };
            ((converted & !0x01000003) | ((converted & 3) + 1)).to_ne_bytes()
        });
        if flags & 0x80000 != 0 {
            put(&mut bytes, 4, &2u32.to_ne_bytes());
        }
    }
    if let Some(offset) = parse::field(info, b"pos:", 0).and_then(|s| parse::number(s, 10)) {
        put(&mut bytes, 8, &offset.to_ne_bytes());
    }
    put(&mut bytes, 16, &1u32.to_ne_bytes());
    out.emit(bytes.get(..len).ok_or(-22)?);
    Ok(len as i64)
}
fn directories(pid: u32, out: &mut Output) -> Result<i64, i64> {
    if out.sizing {
        return Err(-14);
    }
    if out.capacity < 2352 {
        return Err(-28);
    }
    let mut bytes = [0; 2352];
    vnode(
        &path(pid, b"/cwd")?,
        bytes.get_mut(..1176).ok_or(-22)?,
        true,
    )?;
    vnode(
        &path(pid, b"/root")?,
        bytes.get_mut(1176..).ok_or(-22)?,
        true,
    )?;
    out.emit(&bytes);
    Ok(2352)
}
// /proc/maps quotes newline as \012, which is also a legal literal filename.
// map_files symlinks retain the original bytes and disambiguate both names.
fn mapped_path(pid: u32, m: &parse::Mapping<'_>, native: &mut [u8; 4096]) -> Result<usize, i64> {
    if m.path.len() >= 3900 {
        return Err(-63);
    }
    put(native, 0, m.path);
    if !m.path.windows(4).any(|s| s == b"\\012") {
        return Ok(m.path.len());
    }
    let mut link = path(pid, b"/map_files/")?;
    link.hex(m.address).ok_or(-22)?;
    link.push(b"-").ok_or(-22)?;
    link.hex(m.address.checked_add(m.size).ok_or(-22)?)
        .ok_or(-22)?;
    let expanded = expand(&link, false)?;
    let mut target = [0; 4096];
    if let Ok(n) = syscall(
        89,
        expanded.path.as_ptr() as i64,
        target.as_mut_ptr() as i64,
        3999,
        0,
    ) {
        let n = n as usize;
        if n >= 3900 {
            return Err(-63);
        }
        *native = target;
        return Ok(n);
    }
    // Some proc mounts deny map_files readlink. Preserve a literal filename
    // when its inode confirms it; otherwise use the documented maps encoding.
    let mut metadata = [0; 144];
    if syscall(
        4,
        native.as_ptr() as i64,
        metadata.as_mut_ptr() as i64,
        0,
        0,
    )
    .is_ok()
        && u64at(&metadata, 8) == m.inode
    {
        return Ok(m.path.len());
    }
    parse::maps_path(m.path, native).ok_or(-63)
}
fn regions(pid: u32, flavor: u32, arg: u64, out: &mut Output) -> Result<i64, i64> {
    if out.sizing {
        return Err(-14);
    }
    if out.capacity < 1272 {
        return Err(-28);
    }
    let fd = open(&path(pid, b"/maps")?, 0)?;
    let mut line = [0; 8192];
    let mut used = 0;
    loop {
        let room = line.get_mut(used..).ok_or(-5)?;
        if room.is_empty() {
            return Err(-63);
        }
        let n = match syscall(0, fd.0, room.as_mut_ptr() as i64, room.len() as i64, 0) {
            Err(-4) => continue,
            other => other? as usize,
        };
        if n == 0 {
            return Err(if flavor == 8 { -3 } else { -22 });
        }
        used += n;
        let mut consumed = 0;
        while let Some(rest) = line.get(consumed..used) {
            let Some(end) = rest.iter().position(|b| *b == b'\n') else {
                break;
            };
            let item = rest.get(..end).ok_or(-5)?;
            if let Some(m) = parse::mapping(item) {
                // Overlayfs maps expose the backing device, while stat exposes
                // the mounted overlay device. Resolve the still-identical path
                // to recover the guest-visible identity. Never substitute a new
                // inode after unlink/rename/replacement.
                let mut device = m.device;
                let mut native = [0; 4096];
                let path_length = if m.path.first() == Some(&b'/') && m.path.len() < 3900 {
                    mapped_path(pid, &m, &mut native)?
                } else {
                    0
                };
                if path_length != 0 {
                    let mut metadata = [0; 144];
                    if syscall(
                        4,
                        native.as_ptr() as i64,
                        metadata.as_mut_ptr() as i64,
                        0,
                        0,
                    )
                    .is_ok()
                        && u64at(&metadata, 8) == m.inode
                    {
                        device = u64at(&metadata, 0) as u32;
                    }
                }
                let file_backed = m.inode != 0 && m.path.first() == Some(&b'/');
                let wanted = if flavor == 23 {
                    device == arg as u32 && file_backed
                } else {
                    m.address.saturating_add(m.size) > arg && (flavor == 8 || file_backed)
                };
                if wanted {
                    let mut bytes = [0; 1272];
                    put(&mut bytes, 0, &m.protection.to_ne_bytes());
                    put(&mut bytes, 4, &m.protection.to_ne_bytes());
                    put(&mut bytes, 16, &m.offset.to_ne_bytes());
                    put(
                        &mut bytes,
                        60,
                        &(if m.shared { 4u32 } else { 2u32 }).to_ne_bytes(),
                    );
                    put(&mut bytes, 80, &m.address.to_ne_bytes());
                    put(&mut bytes, 88, &m.size.to_ne_bytes());
                    put(&mut bytes, 96, &device.to_ne_bytes());
                    put(&mut bytes, 104, &m.inode.to_ne_bytes());
                    if m.path.first() == Some(&b'/') {
                        if path_length == 0 {
                            return Err(-63);
                        }
                        // SAFETY: 4096-byte path, bounded to leave expansion room.
                        let rv = unsafe { vchroot_unexpand(native.as_mut_ptr()) };
                        if rv != 0 {
                            return Err(rv as i64);
                        }
                        let len = native.iter().position(|b| *b == 0).ok_or(-63)?;
                        if len >= 1024 {
                            return Err(-63);
                        }
                        if let Some(p) = native.get(..len) {
                            put(&mut bytes, 248, p);
                        }
                    }
                    out.emit(&bytes);
                    return Ok(1272);
                }
            }
            consumed += end + 1;
        }
        // Safe overlap move; no slice indices from procfs are trusted unchecked.
        let remaining = used.saturating_sub(consumed);
        for i in 0..remaining {
            let b = line.get(consumed + i).copied().ok_or(-5)?;
            if let Some(dst) = line.get_mut(i) {
                *dst = b;
            }
        }
        used = remaining;
    }
}

// /proc/stat can be larger than a single buffer on high-core-count machines.
// Scan complete lines with bounded storage, skipping irrelevant oversized lines.
fn boot_time() -> Result<u64, i64> {
    let mut p = Path::new();
    p.push(b"/proc/stat").ok_or(-22)?;
    let fd = open(&p, 0)?;
    let mut chunk = [0; 4096];
    let mut line = [0; 64];
    let mut length = 0usize;
    loop {
        let n = match syscall(0, fd.0, chunk.as_mut_ptr() as i64, chunk.len() as i64, 0) {
            Err(-4) => continue,
            other => other? as usize,
        };
        if n == 0 {
            return Err(-5);
        }
        for &byte in chunk.get(..n).ok_or(-5)? {
            if byte == b'\n' {
                if let Some(value) = line
                    .get(..length)
                    .and_then(|s| s.strip_prefix(b"btime "))
                    .and_then(|s| parse::number(s, 10))
                {
                    return Ok(value);
                }
                length = 0;
            } else {
                if let Some(dst) = line.get_mut(length) {
                    *dst = byte;
                }
                length = length.saturating_add(1);
            }
        }
    }
}
fn process_info(pid: u32, flavor: u32, out: &mut Output) -> Result<i64, i64> {
    let length = match flavor {
        13 => info::SHORT_SIZE,
        3 => info::BSD_SIZE,
        4 => info::TASK_SIZE,
        _ => info::BSD_SIZE + info::TASK_SIZE,
    };
    if out.sizing {
        return Err(-14);
    }
    if out.capacity < length {
        return Err(-28);
    }
    let token = credentials(pid).ok_or(-3)?;
    let mut bytes = [0; 4096];
    let n = read(&path(pid, b"/stat")?, &mut bytes).map_err(|e| if e == -2 { -3 } else { e })?;
    let stat = info::Stat::parse(bytes.get(..n).ok_or(-5)?, pid).ok_or(-22)?;
    // Keep the complete result local until every requested field succeeds: a
    // failed TASKALLINFO must not leave a half-updated BSD record in the caller.
    let mut result = [0; info::BSD_SIZE + info::TASK_SIZE];
    if flavor != 4 {
        let mut lp64 = false;
        // SAFETY: Existing RPC writes one C bool into live local storage.
        let rv = unsafe { dserver_rpc_task_is_64_bit(pid as i32, &mut lp64) };
        if rv != 0 {
            return Err(if rv > 0 { -(rv as i64) } else { rv as i64 });
        }
        let uid = token.get(1).copied().ok_or(-5)?;
        let gid = token.get(2).copied().ok_or(-5)?;
        if flavor == 13 {
            put(&mut result, 0, &stat.short(uid, gid, lp64));
        } else {
            let mut name = [0; 4096];
            let mut full_length = 0;
            // SAFETY: Existing RPC receives a live, bounded path allocation.
            let rv = unsafe {
                dserver_rpc_get_executable_path(
                    pid as i32,
                    name.as_mut_ptr(),
                    name.len() as u64,
                    &mut full_length,
                )
            };
            if rv != 0 {
                return Err(if rv > 0 { -(rv as i64) } else { rv as i64 });
            }
            let end = name.iter().position(|b| *b == 0).ok_or(-63)?;
            // SAFETY: Scalar libc sysconf bridge, Linux _SC_CLK_TCK=2.
            let hz = unsafe { native_sysconf(2) };
            if hz <= 0 {
                return Err(-5);
            }
            let bsd = stat
                .bsd(
                    uid,
                    gid,
                    lp64,
                    name.get(..end).ok_or(-5)?,
                    boot_time()?,
                    hz as u64,
                )
                .ok_or(-22)?;
            put(&mut result, 0, &bsd);
        }
    }
    if flavor == 2 || flavor == 4 {
        // SAFETY: Scalar libc sysconf bridge; Linux CLK_TCK=2 and PAGESIZE=30.
        let (hz, pagesize) = unsafe { (native_sysconf(2), native_sysconf(30)) };
        if hz <= 0 || pagesize <= 0 {
            return Err(-5);
        }
        let task = stat.task(hz as u64, pagesize as u64).ok_or(-22)?;
        put(
            &mut result,
            if flavor == 2 { info::BSD_SIZE } else { 0 },
            &task,
        );
    }
    out.emit(result.get(..length).ok_or(-5)?);
    Ok(length as i64)
}

/// Kernel syscall adapter. Unported flavors retain the original implementation.
///
/// # Safety
/// `buffer` must be null or writable for `size` bytes, exclusively for this call.
/// The linked legacy implementation and existing OS/MIG APIs must match Darling's
/// x86_64 ABI and must not unwind through Rust. See SAFETY.txt.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn sys_proc_info(
    call: u32,
    pid: i32,
    flavor: u32,
    arg: u64,
    buffer: *mut c_void,
    size: i32,
) -> i64 {
    let supported = call == 1
        || (call == 2 && matches!(flavor, 1 | 2 | 3 | 4 | 6 | 8 | 9 | 13 | 22 | 23))
        || (call == 3 && matches!(flavor, 1 | 2));
    if !supported {
        // Prevent the legacy signed-to-size_t paths from overflowing caller buffers.
        if size < 0 {
            return -22;
        }
        // SAFETY: Caller guarantees original syscall ABI and output buffer validity.
        return unsafe { darling_legacy_proc_info(call, pid, flavor, arg, buffer, size) };
    }
    let result = (|| {
        let mut out = Output::new(buffer, size)?;
        if call == 1 {
            return list(pid, flavor, &mut out);
        }
        if pid <= 0 {
            return Err(-3);
        }
        match (call, flavor) {
            (2, 2 | 3 | 4 | 13) => process_info(pid as u32, flavor, &mut out),
            (2, 1) => fds(pid as u32, &mut out),
            (2, 6) => threads(pid as u32, &mut out),
            (2, 9) => directories(pid as u32, &mut out),
            (2, 8 | 22 | 23) => regions(pid as u32, flavor, arg, &mut out),
            (3, _) => vnode_query(pid as u32, arg as u32, flavor, &mut out),
            _ => Err(-45),
        }
    })();
    result.unwrap_or_else(|e| e)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn records_never_overwrite_truncated_buffers() {
        let mut bytes = [0xa5; 16];
        let mut out = Output::new(bytes.as_mut_ptr().cast(), 7).unwrap();
        assert!(out.emit(&123u64.to_ne_bytes()));
        assert_eq!(out.used, 0);
        assert_eq!(bytes, [0xa5; 16]);
        assert!(!out.emit(&42u32.to_ne_bytes()));
        assert_eq!(out.used, 4);
        assert!(out.emit(&55u32.to_ne_bytes()));
        assert_eq!(out.used, 4);
        assert_eq!(&bytes[4..], &[0xa5; 12]);
        assert!(Output::new(bytes.as_mut_ptr().cast(), -1).is_err());
    }
    #[test]
    fn null_buffer_counts_complete_records() {
        let mut out = Output::new(core::ptr::null_mut(), 0).unwrap();
        for value in [1u32, 2, 3] {
            assert!(!out.emit(&value.to_ne_bytes()));
        }
        assert_eq!(out.used, 12);
    }
}
