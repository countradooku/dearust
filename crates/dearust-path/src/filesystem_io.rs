//! Shared Linux filesystem snapshot backend; no guest pointers or virtual-root calls.
use crate::filesystem::{MountIdParser, MountParser, convert, convert_statmount};
use core::mem::MaybeUninit;
pub(super) type PathBuffer = [MaybeUninit<u8>; 4096];

// This boundary is private: callers pass live local buffers or initialized
// NUL-terminated paths. It never accesses a raw guest pointer.
#[inline(always)]
pub(super) unsafe fn call(nr: i32, a: i64, b: i64, c: i64, d: i64) -> Result<i64, i64> {
    // SAFETY: callers establish the syscall-specific local storage contract;
    // linux_syscall preserves its ABI and cannot unwind into Rust.
    let value = unsafe { super::filesystem_syscall(a, b, c, d, 0, 0, nr) };
    if value < 0 {
        Err(i64::from(dearust_errno::linux_to_bsd(value as i32)))
    } else {
        Ok(value)
    }
}

pub(super) struct Fd(pub(super) i64);
impl Drop for Fd {
    #[inline(always)]
    fn drop(&mut self) {
        // SAFETY: close3 consumes only this privately owned descriptor. Never
        // retry close: Linux may already have released its number after EINTR.
        let _ = unsafe { call(3, self.0, 0, 0, 0) };
    }
}

struct Reader {
    fd: Fd,
    offset: i64,
    bytes: [MaybeUninit<u8>; 4096],
}
impl Reader {
    fn open(path: &[u8]) -> Result<Self, i64> {
        // SAFETY: all call sites provide an initialized local NUL-terminated
        // pathname. openat257 uses host procfs, not guest path expansion.
        let fd = unsafe { call(257, -100, path.as_ptr() as i64, 0x80000, 0) }?;
        Ok(Self {
            fd: Fd(fd),
            offset: 0,
            bytes: [MaybeUninit::uninit(); 4096],
        })
    }

    fn read(&mut self) -> Result<&[u8], i64> {
        let count = loop {
            // SAFETY: pread64(17) writes at most4096 bytes into live local
            // storage. Explicit offsets avoid shared file-position races when
            // a signal handler forks and returns in the child mid-query.
            match unsafe {
                call(
                    17,
                    self.fd.0,
                    self.bytes.as_mut_ptr() as i64,
                    self.bytes.len() as i64,
                    self.offset,
                )
            } {
                Err(-4) => continue,
                other => break other?,
            }
        };
        if count > self.bytes.len() as i64 {
            return Err(-5);
        }
        self.offset = self.offset.checked_add(count).ok_or(-84_i64)?;
        // SAFETY: successful pread initialized exactly count bytes, bounded by
        // this allocation. The uninitialized tail never becomes a Rust slice.
        Ok(unsafe { core::slice::from_raw_parts(self.bytes.as_ptr().cast(), count as usize) })
    }
}

fn statx_mount_id(fd: &Fd, mask: u32) -> Option<u64> {
    let mut statx = [0_u64; 32];
    // SAFETY: statx332 AT_EMPTY_PATH|AT_STATX_DONT_SYNC queries the pinned
    // object into its256-byte local layout. MNT_ID is a field of this snapshot,
    // not a cache. Older kernels/policies retain the exact procfs backend below.
    let result = unsafe {
        super::filesystem_syscall(
            fd.0,
            c"".as_ptr() as i64,
            0x5000,
            i64::from(mask),
            statx.as_mut_ptr() as i64,
            0,
            332,
        )
    };
    if result == 0 && statx[0] as u32 & mask != 0 {
        Some(statx[18])
    } else {
        None
    }
}

fn mount_id(fd: &Fd) -> Result<u64, i64> {
    if let Some(id) = statx_mount_id(fd, 0x1000) {
        return Ok(id);
    }
    let mut path = [0_u8; 64];
    let prefix = b"/proc/thread-self/fdinfo/";
    path[..prefix.len()].copy_from_slice(prefix);
    let mut digits = [0_u8; 20];
    let mut number = fd.0 as u64;
    let mut start = digits.len();
    loop {
        start = start.checked_sub(1).ok_or(-5_i64)?;
        *digits.get_mut(start).ok_or(-5_i64)? = b'0' + (number % 10) as u8;
        number /= 10;
        if number == 0 {
            break;
        }
    }
    for (output, byte) in path[prefix.len()..]
        .iter_mut()
        .zip(digits.get(start..).ok_or(-5_i64)?)
    {
        *output = *byte;
    }
    let mut reader = Reader::open(&path)?;
    let mut parser = MountIdParser::new();
    loop {
        let bytes = reader.read()?;
        if bytes.is_empty() {
            return parser.finish()?.ok_or(-5);
        }
        if let Some(id) = parser.feed(bytes)? {
            return Ok(id);
        }
    }
}

pub(super) fn snapshot(
    fd: &Fd,
    point: &mut PathBuffer,
    output: &mut [u8; 2168],
    mountinfo: Option<&[u8]>,
) -> Result<(), i64> {
    let mut stat = [0_u64; 15];
    // SAFETY: Linux x86_64 fstatfs138 writes its120-byte layout into initialized,
    // aligned local storage. The privately pinned descriptor stays live.
    unsafe { call(138, fd.0, stat.as_mut_ptr() as i64, 0, 0) }?;
    // Linux anonymous pipes, sockets and anon-inode objects have no vnode;
    // Darwin file_vnode rejects them. Named FIFOs retain their real filesystem.
    if matches!(stat[0], 0x5049_5045 | 0x534f_434b | 0x0904_1934) {
        return Err(-22);
    }
    convert(&stat, output);
    if let Some(id) = mountinfo
        .is_none()
        .then(|| statx_mount_id(fd, 0x4000))
        .flatten()
    {
        // Look up the pinned mount's UNIQUE ID in this caller's namespace/root.
        // BY_FD would instead return names in a foreign mount namespace.
        // Reuse the existing
        // scratch allocation; long results or older kernels retain streaming
        // procfs below. A delegated server must use its explicit client table.
        let request = [24_u64, id, 0x330];
        // A seccomp policy can synthesize success without executing the call.
        // A zero size safely selects procfs if it leaves this buffer untouched.
        for byte in point.iter_mut().take(4) {
            byte.write(0);
        }
        // SAFETY: initialized24-byte mnt_id_req, writable4096-byte local scratch;
        // Linux x86_64 statmount457, flags0. No guest pointers are exposed.
        let result = unsafe {
            call(
                457,
                request.as_ptr() as i64,
                point.as_mut_ptr() as i64,
                4096,
                0,
            )
        };
        if result == Ok(0) {
            // SAFETY: successful statmount writes its complete512-byte fixed
            // header, including size, then exactly size-512 initialized strings.
            let size = u32::from_le_bytes(core::array::from_fn(|index| unsafe {
                point[index].assume_init()
            })) as usize;
            if (512..=4096).contains(&size) {
                // SAFETY: only the initialized returned prefix becomes a slice;
                // the parser moves its terminated mountpoint to scratch offset0.
                let bytes =
                    unsafe { core::slice::from_raw_parts_mut(point.as_mut_ptr().cast(), size) };
                if convert_statmount(bytes, output).is_ok() {
                    return Ok(());
                }
            }
        }
    }
    proc_snapshot(fd, point, output, mountinfo)
}

// Keep streaming storage off the direct-query frame. This remains the complete
// backend on older kernels and for oversized strings or delegated namespaces.
#[inline(never)]
fn proc_snapshot(
    fd: &Fd,
    point: &mut PathBuffer,
    output: &mut [u8; 2168],
    mountinfo: Option<&[u8]>,
) -> Result<(), i64> {
    let id = mount_id(fd)?;
    let point_length = {
        let mut reader = Reader::open(mountinfo.unwrap_or(b"/proc/thread-self/mountinfo\0"))?;
        let mut parser = MountParser::new(id, point, output);
        loop {
            let bytes = reader.read()?;
            if bytes.is_empty() {
                if !parser.finish()? {
                    // A detached or inaccessible mount must not be replaced
                    // with unrelated metadata or synthesized empty names.
                    return Err(-70); // Darwin ESTALE
                }
                break;
            }
            if parser.feed(bytes)? {
                break;
            }
        }
        parser.point_len()
    };
    // The parser initialized the prefix; establish its checked terminator at
    // the boundary before letting the foreign namespace helper mutate it.
    point.get_mut(point_length).ok_or(-63_i64)?.write(0);
    Ok(())
}
