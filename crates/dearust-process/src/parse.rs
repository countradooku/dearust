//! Bounded parsing of untrusted, concurrently changing procfs records.
pub fn number(bytes: &[u8], radix: u32) -> Option<u64> {
    if bytes.is_empty() {
        return None;
    }
    bytes.iter().try_fold(0u64, |n, b| {
        let d = match b {
            b'0'..=b'9' => (b - b'0') as u32,
            b'a'..=b'f' => (b - b'a' + 10) as u32,
            b'A'..=b'F' => (b - b'A' + 10) as u32,
            _ => return None,
        };
        if d >= radix {
            return None;
        }
        n.checked_mul(radix as u64)?.checked_add(d as u64)
    })
}
pub fn field<'a>(bytes: &'a [u8], name: &[u8], index: usize) -> Option<&'a [u8]> {
    let line = bytes
        .split(|b| *b == b'\n')
        .find(|line| line.starts_with(name))?;
    line.get(name.len()..)?
        .split(|b| b.is_ascii_whitespace())
        .filter(|s| !s.is_empty())
        .nth(index)
}
pub fn stat_field(bytes: &[u8], field: usize) -> Option<&[u8]> {
    // comm can contain spaces, parentheses and newlines. The LAST ')' closes it.
    let close = bytes.iter().rposition(|b| *b == b')')?;
    bytes
        .get(close + 1..)?
        .split(|b| b.is_ascii_whitespace())
        .filter(|s| !s.is_empty())
        .nth(field.checked_sub(3)?)
}
pub fn dirent(bytes: &[u8]) -> Option<(usize, &[u8])> {
    let len = u16::from_ne_bytes(bytes.get(16..18)?.try_into().ok()?) as usize;
    if len < 20 {
        return None;
    }
    let name = bytes.get(19..len)?;
    let end = name.iter().position(|b| *b == 0)?;
    Some((len, name.get(..end)?))
}
pub struct Path {
    bytes: [u8; 96],
    len: usize,
}
impl Path {
    pub fn new() -> Self {
        Self {
            bytes: [0; 96],
            len: 0,
        }
    }
    pub fn push(&mut self, text: &[u8]) -> Option<()> {
        let end = self.len.checked_add(text.len())?;
        // Always reserve the terminating NUL.
        if end >= self.bytes.len() {
            return None;
        }
        self.bytes.get_mut(self.len..end)?.copy_from_slice(text);
        self.len = end;
        Some(())
    }
    pub fn decimal(&mut self, mut n: u32) -> Option<()> {
        let mut digits = [0; 10];
        let mut cursor = digits.len();
        loop {
            cursor = cursor.checked_sub(1)?;
            *digits.get_mut(cursor)? = b'0' + (n % 10) as u8;
            n /= 10;
            if n == 0 {
                break;
            }
        }
        self.push(digits.get(cursor..)?)
    }
    pub fn hex(&mut self, mut n: u64) -> Option<()> {
        let mut digits = [0; 16];
        let mut cursor = digits.len();
        loop {
            cursor = cursor.checked_sub(1)?;
            *digits.get_mut(cursor)? = b"0123456789abcdef"[(n % 16) as usize];
            n /= 16;
            if n == 0 {
                break;
            }
        }
        self.push(digits.get(cursor..)?)
    }
    pub fn proc(pid: u32, suffix: &[u8]) -> Option<Self> {
        let mut p = Self::new();
        p.push(b"/proc/")?;
        p.decimal(pid)?;
        p.push(suffix)?;
        Some(p)
    }
    pub fn as_ptr(&self) -> *const u8 {
        self.bytes.as_ptr()
    }
}
// seq_file_path in Linux proc maps escapes only newlines, as literal \012.
// This encoding is ambiguous with literal backslash names; prefer map_files
// readlink when available before using this bounded fallback.
pub fn maps_path(bytes: &[u8], out: &mut [u8]) -> Option<usize> {
    let mut used = 0;
    let mut rest = bytes;
    while !rest.is_empty() {
        let (byte, consumed) = if rest.starts_with(b"\\012") {
            (b'\n', 4)
        } else {
            (*rest.first()?, 1)
        };
        *out.get_mut(used)? = byte;
        used += 1;
        rest = rest.get(consumed..)?;
    }
    // The path is passed to C/syscalls; include the NUL in the bound.
    *out.get_mut(used)? = 0;
    Some(used)
}
pub struct Mapping<'a> {
    pub address: u64,
    pub size: u64,
    pub offset: u64,
    pub protection: u32,
    pub shared: bool,
    pub device: u32,
    pub inode: u64,
    pub path: &'a [u8],
}
impl Mapping<'_> {
    // Address-based queries cannot select earlier mappings. Skip them before
    // path decoding, overlay stat or map_files readlink. Device queries still
    // need each file's recovered overlay identity, so retain those candidates.
    pub fn search_candidate(&self, flavor: u32, arg: u64) -> bool {
        let file_backed = self.inode != 0 && self.path.first() == Some(&b'/');
        match flavor {
            8 => self.address.saturating_add(self.size) > arg,
            22 => file_backed && self.address.saturating_add(self.size) > arg,
            23 => file_backed,
            _ => false,
        }
    }
}
pub fn mapping(line: &[u8]) -> Option<Mapping<'_>> {
    let mut fields = line.splitn(6, |b| *b == b' ');
    let range = fields.next()?;
    let split = range.iter().position(|b| *b == b'-')?;
    let address = number(range.get(..split)?, 16)?;
    let end = number(range.get(split + 1..)?, 16)?;
    let perms = fields.next()?;
    let offset = number(fields.next()?, 16)?;
    let dev = fields.next()?;
    let colon = dev.iter().position(|b| *b == b':')?;
    let major = number(dev.get(..colon)?, 16)?;
    let minor = number(dev.get(colon + 1..)?, 16)?;
    // Linux's userspace dev_t encoding, identical to Darling stat's truncation.
    let device = ((major & 0xfff) << 8)
        | (minor & 0xff)
        | ((minor & !0xff) << 12)
        | ((major & !0xfff) << 32);
    let inode = number(fields.next()?, 10)?;
    let path = fields.next().unwrap_or(&[]);
    let first = path.iter().position(|b| *b != b' ').unwrap_or(path.len());
    Some(Mapping {
        address,
        size: end.checked_sub(address)?,
        offset,
        protection: u32::from(perms.first() == Some(&b'r'))
            | (u32::from(perms.get(1) == Some(&b'w')) << 1)
            | (u32::from(perms.get(2) == Some(&b'x')) << 2),
        shared: perms.get(3) == Some(&b's'),
        device: device as u32,
        inode,
        path: path.get(first..)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numbers_are_strict_and_bounded() {
        assert_eq!(number(b"42", 10), Some(42));
        assert_eq!(number(b"", 10), None);
        assert_eq!(number(b"1x", 10), None);
        assert_eq!(number(b"18446744073709551616", 10), None);
        assert_eq!(number(b"777", 8), Some(511));
    }
    #[test]
    fn comm_does_not_shift_stat_fields() {
        assert_eq!(
            stat_field(b"1 (a ) strange\n name) S 2 3 4 5", 4),
            Some(&b"2"[..])
        );
        assert_eq!(stat_field(b"broken", 4), None);
    }
    #[test]
    fn malformed_directory_entries_are_rejected() {
        let mut d = [0; 24];
        assert_eq!(dirent(&d), None);
        d[16] = 24;
        d[19] = b'1';
        assert_eq!(dirent(&d), Some((24, &b"1"[..])));
        d[16] = 25;
        assert_eq!(dirent(&d), None);
        d[16] = 19;
        assert_eq!(dirent(&d), None);
    }
    #[test]
    fn maximum_pid_path() {
        let p = Path::proc(u32::MAX, b"/fdinfo/2147483647").unwrap();
        assert_eq!(&p.bytes[..p.len], b"/proc/4294967295/fdinfo/2147483647");
    }
    #[test]
    fn mapped_newlines_decode_without_interpreting_other_backslashes() {
        let mut out = [0xa5; 64];
        let n = maps_path(b"/tmp/a\\012b\\040c\\\\d", &mut out).unwrap();
        assert_eq!(&out[..n + 1], b"/tmp/a\nb\\040c\\\\d\0");
        assert!(maps_path(b"abcd", &mut [0; 4]).is_none());
        let mut p = Path::proc(42, b"/map_files/").unwrap();
        p.hex(0x123abc).unwrap();
        p.push(b"-").unwrap();
        p.hex(u64::MAX).unwrap();
        assert_eq!(
            &p.bytes[..p.len],
            b"/proc/42/map_files/123abc-ffffffffffffffff"
        );
    }
    #[test]
    fn region_search_skips_only_impossible_candidates() {
        let file = mapping(b"1000-2000 r--p 0000 08:01 42 /tmp/file").unwrap();
        for flavor in [8, 22] {
            assert!(file.search_candidate(flavor, 0));
            assert!(file.search_candidate(flavor, 0x1000));
            assert!(file.search_candidate(flavor, 0x1fff));
            assert!(!file.search_candidate(flavor, 0x2000));
            assert!(!file.search_candidate(flavor, u64::MAX));
        }
        assert!(file.search_candidate(23, u64::MAX));
        let anonymous = mapping(b"1000-2000 rw-p 0000 00:00 0 ").unwrap();
        assert!(anonymous.search_candidate(8, 0));
        assert!(!anonymous.search_candidate(22, 0));
        assert!(!anonymous.search_candidate(23, 0));
    }
    #[test]
    fn mapping_tracks_identity_addresses_and_spaces() {
        let m = mapping(b"01230000-01234000 rw-p 00001000 08:01 42 /tmp/a file").unwrap();
        assert_eq!(
            (m.address, m.size, m.offset, m.protection, m.device, m.inode),
            (0x1230000, 0x4000, 0x1000, 3, 0x801, 42)
        );
        assert_eq!(m.path, b"/tmp/a file");
        assert!(!m.shared);
        assert!(mapping(b"ff-00 rw-p 0 00:00 0").is_none());
        assert!(mapping(b"not a map").is_none());
    }
}
