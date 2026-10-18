//! Pure Linux procfs to Darwin x86_64 BSD/task-info conversion.
use crate::parse;

pub const SHORT_SIZE: usize = 64;
pub const BSD_SIZE: usize = 136;
pub const TASK_SIZE: usize = 96;
pub const THREAD_SIZE: usize = 112;

// Require capacity for the terminating NUL before exposing any pathname bytes.
pub fn path_length(bytes: &[u8], capacity: usize) -> Option<usize> {
    let end = bytes.iter().position(|b| *b == 0)?;
    (end.checked_add(1)? <= capacity).then_some(end)
}

fn put(dst: &mut [u8], offset: usize, value: &[u8]) {
    if let Some(dst) = dst.get_mut(offset..offset + value.len()) {
        dst.copy_from_slice(value);
    }
}
fn unsigned(stat: &[u8], field: usize) -> Option<u64> {
    parse::number(parse::stat_field(stat, field)?, 10)
}
fn signed(stat: &[u8], field: usize) -> Option<i64> {
    let text = parse::stat_field(stat, field)?;
    if let Some(rest) = text.strip_prefix(b"-") {
        let n = parse::number(rest, 10)?;
        if n == 1u64 << 63 {
            Some(i64::MIN)
        } else {
            Some(-i64::try_from(n).ok()?)
        }
    } else {
        i64::try_from(parse::number(text, 10)?).ok()
    }
}
// Linux reports USER_HZ ticks, while Darwin's x86_64 absolute time is nanoseconds.
// Divide first so long-lived processes do not overflow in the multiplication.
pub fn ticks(value: u64, rate: u64, scale: u64) -> Option<u64> {
    if rate == 0 {
        return None;
    }
    (value / rate)
        .checked_mul(scale)?
        .checked_add((value % rate).checked_mul(scale)? / rate)
}
pub struct Stat<'a> {
    pid: u32,
    ppid: u32,
    pgid: u32,
    status: u32,
    thread_state: i32,
    comm: &'a [u8],
    nice: i32,
    start: u64,
    tty: u32,
    tpgid: i32,
    virtual_size: u64,
    resident_pages: u64,
    user: u64,
    system: u64,
    faults: u64,
    major_faults: u64,
    threads: i32,
    priority: i32,
}
impl<'a> Stat<'a> {
    pub fn parse(bytes: &'a [u8], pid: u32) -> Option<Self> {
        let open = bytes.iter().position(|b| *b == b'(')?;
        let close = bytes.iter().rposition(|b| *b == b')')?;
        let leader = bytes.get(..open)?.strip_suffix(b" ")?;
        if parse::number(leader, 10)? != u64::from(pid) {
            return None;
        }
        let status = match parse::stat_field(bytes, 3)? {
            b"R" => 2,               // SRUN
            b"T" | b"t" => 4,        // SSTOP
            b"Z" | b"X" | b"x" => 5, // SZOMB
            _ => 3,                  // SSLEEP
        };
        Some(Self {
            pid,
            ppid: unsigned(bytes, 4)?.try_into().ok()?,
            pgid: unsigned(bytes, 5)?.try_into().ok()?,
            status,
            thread_state: match parse::stat_field(bytes, 3)? {
                b"R" => 1,
                b"T" | b"t" => 2,
                b"S" | b"I" => 3,
                b"D" => 4,
                b"Z" | b"X" | b"x" => 5,
                _ => 0,
            },
            comm: bytes.get(open + 1..close)?,
            nice: signed(bytes, 19)?.try_into().ok()?,
            start: unsigned(bytes, 22)?,
            tty: signed(bytes, 7)? as u32,
            tpgid: signed(bytes, 8)?.try_into().ok()?,
            virtual_size: unsigned(bytes, 23)?,
            resident_pages: u64::try_from(signed(bytes, 24)?.max(0)).ok()?,
            user: unsigned(bytes, 14)?,
            system: unsigned(bytes, 15)?,
            faults: unsigned(bytes, 10)?.saturating_add(unsigned(bytes, 12)?),
            major_faults: unsigned(bytes, 12)?,
            threads: unsigned(bytes, 20)?.try_into().ok()?,
            priority: signed(bytes, 18)?.try_into().ok()?,
        })
    }
    pub fn short(&self, uid: u32, gid: u32, lp64: bool) -> [u8; SHORT_SIZE] {
        let mut out = [0; SHORT_SIZE];
        for (offset, value) in [
            (0, self.pid),
            (4, self.ppid),
            (8, self.pgid),
            (12, self.status),
            // Public PROC_FLAG_LP64 is 0x10, NOT the kernel's P_LP64 (4).
            (32, if lp64 { 0x10 } else { 0 }),
            (36, uid),
            (40, gid),
            (44, uid),
            (48, gid),
            (52, uid),
            (56, gid),
        ] {
            put(&mut out, offset, &value.to_ne_bytes());
        }
        put(
            &mut out,
            16,
            self.comm.get(..self.comm.len().min(15)).unwrap_or(&[]),
        );
        out
    }
    pub fn bsd(
        &self,
        uid: u32,
        gid: u32,
        lp64: bool,
        name: &[u8],
        boot: u64,
        hz: u64,
    ) -> Option<[u8; BSD_SIZE]> {
        let short = self.short(uid, gid, lp64);
        let mut out = [0; BSD_SIZE];
        for (dest, src) in [
            (0, 32),
            (4, 12),
            (12, 0),
            (16, 4),
            (20, 36),
            (24, 40),
            (28, 44),
            (32, 48),
            (36, 52),
            (40, 56),
            (100, 8),
        ] {
            put(&mut out, dest, short.get(src..src + 4)?);
        }
        put(&mut out, 48, short.get(16..32)?);
        let base = name.rsplit(|b| *b == b'/').next()?;
        put(&mut out, 64, base.get(..base.len().min(31))?);
        put(&mut out, 108, &self.tty.to_ne_bytes());
        put(&mut out, 112, &self.tpgid.to_ne_bytes());
        put(&mut out, 116, &self.nice.to_ne_bytes());
        if hz == 0 {
            return None;
        }
        put(
            &mut out,
            120,
            &boot.checked_add(self.start / hz)?.to_ne_bytes(),
        );
        put(
            &mut out,
            128,
            &ticks(self.start % hz, hz, 1_000_000)?.to_ne_bytes(),
        );
        Some(out)
    }
    pub fn thread(&self, hz: u64) -> Option<[u8; THREAD_SIZE]> {
        let mut out = [0; THREAD_SIZE];
        put(
            &mut out,
            0,
            &ticks(self.user, hz, 1_000_000_000)?.to_ne_bytes(),
        );
        put(
            &mut out,
            8,
            &ticks(self.system, hz, 1_000_000_000)?.to_ne_bytes(),
        );
        put(&mut out, 24, &self.thread_state.to_ne_bytes());
        put(&mut out, 48, self.comm.get(..self.comm.len().min(63))?);
        // Linux scheduler priorities/policies and cumulative CPU time cannot
        // reconstruct Mach's decayed usage, scheduler policy or priorities.
        Some(out)
    }
    pub fn task(&self, hz: u64, pagesize: u64) -> Option<[u8; TASK_SIZE]> {
        if pagesize == 0 {
            return None;
        }
        let mut out = [0; TASK_SIZE];
        for (offset, value) in [
            (0, self.virtual_size),
            (8, self.resident_pages.checked_mul(pagesize)?),
            (16, ticks(self.user, hz, 1_000_000_000)?),
            (24, ticks(self.system, hz, 1_000_000_000)?),
        ] {
            put(&mut out, offset, &value.to_ne_bytes());
        }
        for (offset, value) in [
            (52, self.faults.min(i32::MAX as u64) as i32),
            (56, self.major_faults.min(i32::MAX as u64) as i32),
            (84, self.threads),
            (92, self.priority),
        ] {
            put(&mut out, offset, &value.to_ne_bytes());
        }
        Some(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const SAMPLE: &[u8] =
        b"42 (a ) tricky\nname) R 2 7 1 0 -1 0 100 0 3 0 123 45 0 0 -5 -7 3 0 1234 999999 123 0";
    fn word(bytes: &[u8], n: usize) -> u32 {
        u32::from_ne_bytes(bytes[n..n + 4].try_into().unwrap())
    }
    fn wide(bytes: &[u8], n: usize) -> u64 {
        u64::from_ne_bytes(bytes[n..n + 8].try_into().unwrap())
    }
    #[test]
    fn process_info_has_correct_units_and_signed_fields() {
        let stat = Stat::parse(SAMPLE, 42).unwrap();
        let task = stat.task(100, 4096).unwrap();
        assert_eq!(wide(&task, 0), 999999);
        assert_eq!(wide(&task, 8), 123 * 4096);
        assert_eq!(wide(&task, 16), 1_230_000_000);
        assert_eq!(wide(&task, 24), 450_000_000);
        assert_eq!(word(&task, 52), 103);
        assert_eq!(word(&task, 56), 3);
        assert_eq!(word(&task, 84), 3);
        assert_eq!(word(&task, 92) as i32, -5);
        let bsd = stat
            .bsd(501, 20, true, b"/Applications/My App", 1000, 100)
            .unwrap();
        assert_eq!(wide(&bsd, 120), 1012);
        assert_eq!(wide(&bsd, 128), 340000);
        assert_eq!(word(&bsd, 116) as i32, -7);
        assert_eq!(&bsd[64..71], b"My App\0");
    }
    #[test]
    fn bsd_credentials_flags_and_comm_are_not_caller_cached() {
        let stat = Stat::parse(SAMPLE, 42).unwrap();
        let a = stat.short(501, 20, true);
        let b = stat.short(502, 80, false);
        assert_eq!(word(&a, 32), 0x10);
        assert_eq!(word(&b, 32), 0);
        for n in [36, 44, 52] {
            assert_eq!(word(&a, n), 501);
            assert_eq!(word(&b, n), 502);
        }
        for n in [40, 48, 56] {
            assert_eq!(word(&a, n), 20);
            assert_eq!(word(&b, n), 80);
        }
        assert_eq!(&a[16..32], b"a ) tricky\nname\0");
        assert_eq!(word(&a, 60), 0);
    }
    #[test]
    fn executable_paths_require_nul_and_never_accept_truncation() {
        assert_eq!(path_length(b"/long/path\0", 11), Some(10));
        assert_eq!(path_length(b"/long/path\0", 10), None);
        assert_eq!(path_length(b"/unterminated", 4096), None);
        assert_eq!(path_length(b"\0tail", 1), Some(0));
        assert_eq!(path_length(b"\0", 0), None);
        // RPC paths must leave headroom for original vchroot EXIT_PATH prepend.
        let mut rpc = [b'x'; 4096];
        rpc[3899] = 0;
        assert_eq!(path_length(&rpc, 3900), Some(3899));
        rpc[3899] = b'x';
        rpc[3900] = 0;
        assert_eq!(path_length(&rpc, 3900), None);
    }
    #[test]
    fn thread_units_name_and_state_match_public_layout() {
        let stat = Stat::parse(SAMPLE, 42).unwrap();
        let thread = stat.thread(250).unwrap();
        assert_eq!(wide(&thread, 0), 492_000_000);
        assert_eq!(wide(&thread, 8), 180_000_000);
        assert_eq!(word(&thread, 24), 1);
        assert_eq!(&thread[48..64], b"a ) tricky\nname\0");
        for offset in [16, 20, 28, 32, 36, 40, 44] {
            assert_eq!(word(&thread, offset), 0);
        }
        for (state, expected) in [(b'S', 3), (b'D', 4), (b'T', 2), (b'Z', 5)] {
            let mut bytes = [0; SAMPLE.len()];
            bytes.copy_from_slice(SAMPLE);
            let position = bytes.windows(3).position(|s| s == b" R ").unwrap();
            bytes[position + 1] = state;
            assert_eq!(
                word(&Stat::parse(&bytes, 42).unwrap().thread(100).unwrap(), 24),
                expected
            );
        }
        assert!(stat.thread(0).is_none());
    }
    #[test]
    fn malformed_stat_and_conversion_overflow_fail_closed() {
        assert!(Stat::parse(SAMPLE, 43).is_none());
        assert!(Stat::parse(b"42 (truncated) S 2", 42).is_none());
        let stat = Stat::parse(SAMPLE, 42).unwrap();
        assert!(stat.task(0, 4096).is_none());
        assert!(stat.task(100, 0).is_none());
        assert!(stat.task(100, u64::MAX).is_none());
        assert!(stat.bsd(0, 0, true, b"", u64::MAX, 100).is_none());
        assert_eq!(
            ticks(u64::MAX, 1_000_000_000, 1_000_000_000),
            Some(u64::MAX)
        );
        assert_eq!(ticks(u64::MAX, 1, 1_000_000_000), None);
    }
}
