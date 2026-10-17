// SPDX-License-Identifier: GPL-3.0-or-later
//! ELF core to Mach-O core conversion, adapted from Darling's hosttools coredump
//! converter at 60ba801decee7a00782f74f6be4c8ffb013f79ff (GPL-3.0-or-later).
//! Integer fields are decoded explicitly; core bytes never become native structs.
#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::ffi::OsStr;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn add(a: u64, b: u64) -> io::Result<u64> {
    a.checked_add(b).ok_or_else(|| invalid("integer overflow"))
}
fn bytes(data: &[u8], offset: u64, length: u64) -> io::Result<&[u8]> {
    let end = add(offset, length)?;
    let start = usize::try_from(offset).map_err(|_| invalid("offset too large"))?;
    let end = usize::try_from(end).map_err(|_| invalid("offset too large"))?;
    data.get(start..end)
        .ok_or_else(|| invalid("truncated ELF core"))
}
fn number(data: &[u8], offset: u64, width: u64) -> io::Result<u64> {
    let mut value = [0; 8];
    let field = bytes(data, offset, width)?;
    value[..field.len()].copy_from_slice(field);
    Ok(u64::from_le_bytes(value))
}
fn align4(n: u64) -> io::Result<u64> {
    Ok(add(n, 3)? & !3)
}
fn emit(out: &mut impl Write, n: u64, width: usize) -> io::Result<()> {
    if width == 4 && n > u32::MAX as u64 {
        return Err(invalid("32-bit Mach-O field overflow"));
    }
    out.write_all(&n.to_le_bytes()[..width])
}
fn zero(out: &mut impl Write, mut n: u64) -> io::Result<()> {
    let zeros = [0; 65536];
    while n != 0 {
        let take = n.min(zeros.len() as u64) as usize;
        out.write_all(&zeros[..take])?;
        n -= take as u64;
    }
    Ok(())
}

/// Overlay paths used to locate files omitted from Linux core dumps.
#[derive(Debug, Clone)]
pub struct Options {
    pub prefix: PathBuf,
    pub libexec: PathBuf,
}
impl Default for Options {
    fn default() -> Self {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .or_else(|| {
                // Upstream uses getuid(), the real UID. Decode procfs/passwd safely.
                home_for_real_uid(
                    &std::fs::read_to_string("/proc/self/status").ok()?,
                    &std::fs::read_to_string("/etc/passwd").ok()?,
                )
            })
            .unwrap_or_else(|| PathBuf::from("."));
        Self {
            prefix: std::env::var_os("DPREFIX")
                .map(PathBuf::from)
                .unwrap_or_else(|| home.join(".darling")),
            libexec: PathBuf::from(
                option_env!("DARLING_LIBEXEC_PATH").unwrap_or("/usr/libexec/darling"),
            ),
        }
    }
}
fn home_for_real_uid(status: &str, passwd: &str) -> Option<PathBuf> {
    let uid = status
        .lines()
        .find(|line| line.starts_with("Uid:"))?
        .split_whitespace()
        .nth(1)?;
    passwd.lines().find_map(|line| {
        let fields: Vec<_> = line.split(':').collect();
        (fields.len() > 5 && fields[2] == uid).then(|| PathBuf::from(fields[5]))
    })
}

impl Options {
    fn open(&self, path: &Path) -> io::Result<File> {
        if let Ok(file) = File::open(path) {
            return Ok(file);
        }
        if let Ok(relative) = path.strip_prefix(&self.prefix) {
            return File::open(self.libexec.join(relative));
        }
        if let Ok(relative) = path.strip_prefix("/") {
            if let Ok(file) = File::open(self.prefix.join(relative)) {
                return Ok(file);
            }
            return File::open(self.libexec.join(relative));
        }
        File::open(path)
    }
}
#[derive(Debug)]
struct Mapping {
    start: u64,
    end: u64,
    offset: u64,
    path: PathBuf,
}
#[derive(Debug)]
struct Area {
    address: u64,
    size: u64,
    offset: u64,
    length: u64,
    prot: u64,
    path: Option<PathBuf>,
}
/// Match each area to the first original NT_FILE entry covering it, including
/// arbitrary overlapping/unsorted entries. Sweep ascending addresses, insert
/// mapping ends into a reversed Fenwick prefix-min tree, then query end >= area
/// end. This preserves upstream first-entry precedence in O((A+M) log M) work.
fn covering_mappings(areas: &[Area], mappings: &[Mapping]) -> Vec<Option<usize>> {
    let mut matches = vec![None; areas.len()];
    if mappings.is_empty() {
        return matches;
    }
    let mut ends: Vec<_> = mappings.iter().map(|mapping| mapping.end).collect();
    ends.sort_unstable();
    ends.dedup();
    let mut mapping_order: Vec<_> = (0..mappings.len()).collect();
    mapping_order.sort_unstable_by_key(|&index| mappings[index].start);
    let mut area_order: Vec<_> = areas
        .iter()
        .enumerate()
        .filter(|(_, area)| area.length == 0)
        .map(|(index, _)| index)
        .collect();
    area_order.sort_unstable_by_key(|&index| areas[index].address);
    let mut tree = vec![usize::MAX; ends.len() + 1];
    let mut inserted = 0;
    for area_index in area_order {
        let area = &areas[area_index];
        while inserted < mapping_order.len()
            && mappings[mapping_order[inserted]].start <= area.address
        {
            let index = mapping_order[inserted];
            let mut position = ends.len() - ends.partition_point(|&end| end < mappings[index].end);
            while position < tree.len() {
                tree[position] = tree[position].min(index);
                position += position & position.wrapping_neg();
            }
            inserted += 1;
        }
        let end = area.address + area.size; // validated during ELF parsing
        let mut position = ends.len() - ends.partition_point(|&mapping_end| mapping_end < end);
        let mut first = usize::MAX;
        while position != 0 {
            first = first.min(tree[position]);
            position &= position - 1;
        }
        if first != usize::MAX {
            matches[area_index] = Some(first);
        }
    }
    matches
}

#[derive(Debug)]
struct Plan {
    wide: bool,
    areas: Vec<Area>,
    threads: Vec<Vec<u64>>,
    flags: u64,
}
impl Plan {
    fn parse(data: &[u8], options: &Options) -> io::Result<Self> {
        if bytes(data, 0, 4)? != b"\x7fELF"
            || number(data, 5, 1)? != 1
            || number(data, 6, 1)? != 1
            || number(data, 16, 2)? != 4
            || number(data, 20, 4)? != 1
        {
            return Err(invalid("not a little-endian ELF core"));
        }
        let wide = match number(data, 4, 1)? {
            1 => false,
            2 => true,
            _ => return Err(invalid("invalid ELF class")),
        };
        if number(data, 18, 2)? != if wide { 62 } else { 3 } {
            return Err(invalid("unsupported ELF machine/class"));
        }
        let width = if wide { 8 } else { 4 };
        bytes(data, 0, if wide { 64 } else { 52 })?;
        let phoff = number(data, 28 + if wide { 4 } else { 0 }, width)?;
        let entsize = number(data, if wide { 54 } else { 42 }, 2)?;
        let count = number(data, if wide { 56 } else { 44 }, 2)?;
        if entsize < if wide { 56 } else { 32 } {
            return Err(invalid("short ELF program header"));
        }
        bytes(data, phoff, entsize * count)?;
        let mut areas = Vec::new();
        let mut notes = None;
        for i in 0..count {
            let ph = bytes(data, add(phoff, i * entsize)?, entsize)?;
            let kind = number(ph, 0, 4)?;
            let (offset, address, length, size, flags) = if wide {
                (
                    number(ph, 8, 8)?,
                    number(ph, 16, 8)?,
                    number(ph, 32, 8)?,
                    number(ph, 40, 8)?,
                    number(ph, 4, 4)?,
                )
            } else {
                (
                    number(ph, 4, 4)?,
                    number(ph, 8, 4)?,
                    number(ph, 16, 4)?,
                    number(ph, 20, 4)?,
                    number(ph, 24, 4)?,
                )
            };
            if kind == 4 && notes.is_none() {
                notes = Some(bytes(data, offset, length)?);
            }
            if kind == 1 {
                if length > size {
                    return Err(invalid("PT_LOAD file size exceeds memory size"));
                }
                add(address, size)?;
                if length != 0 {
                    bytes(data, offset, length)?;
                }
                areas.push(Area {
                    address,
                    size,
                    offset,
                    length,
                    prot: ((flags & 4) >> 2) | (flags & 2) | ((flags & 1) << 2),
                    path: None,
                });
            }
        }
        let notes = notes.ok_or_else(|| invalid("missing PT_NOTE segment"))?;
        let mut cursor = 0;
        let mut files = None;
        let mut threads = Vec::new();
        while cursor < notes.len() as u64 {
            let name = number(notes, cursor, 4)?;
            let size = number(notes, add(cursor, 4)?, 4)?;
            let kind = number(notes, add(cursor, 8)?, 4)?;
            let desc = add(add(cursor, 12)?, align4(name)?)?;
            let payload = bytes(notes, desc, size)?;
            cursor = add(desc, align4(size)?)?;
            bytes(notes, 0, cursor)?;
            if kind == 0x46494c45 {
                files = Some(payload);
            }
            if kind == 1 {
                let start = if wide { 112 } else { 72 };
                let indices: &[u64] = if wide {
                    &[
                        10, 5, 11, 12, 14, 13, 4, 19, 9, 8, 7, 6, 3, 2, 1, 0, 16, 18, 17, 25, 26,
                    ]
                } else {
                    &[6, 0, 1, 2, 4, 3, 5, 15, 16, 14, 12, 13, 7, 8, 9, 10]
                };
                bytes(payload, start, if wide { 216 } else { 68 })?;
                let mut registers = Vec::new();
                for &index in indices {
                    let mut value = number(payload, start + index * width, width)?;
                    if !wide && matches!(index, 7 | 8 | 9 | 10 | 13 | 16) {
                        value &= 0xffff;
                    }
                    registers.push(value);
                }
                threads.push(registers);
            }
        }
        let files = files.ok_or_else(|| invalid("missing NT_FILE note"))?;
        let count = number(files, 0, width)?;
        let page = number(files, width, width)?;
        if page == 0 {
            return Err(invalid("zero NT_FILE page size"));
        }
        let entries_size = count
            .checked_mul(3 * width)
            .ok_or_else(|| invalid("NT_FILE count overflow"))?;
        let names_offset = add(2 * width, entries_size)?;
        let names_length = (files.len() as u64)
            .checked_sub(names_offset)
            .ok_or_else(|| invalid("truncated NT_FILE entries"))?;
        let mut names = bytes(files, names_offset, names_length)?;
        let mut mappings = Vec::new();
        let mut main_flags = None;
        for i in 0..count {
            let off = 2 * width + i * 3 * width;
            let start = number(files, off, width)?;
            let end = number(files, off + width, width)?;
            if end < start {
                return Err(invalid("reversed NT_FILE mapping"));
            }
            let offset = number(files, off + 2 * width, width)?
                .checked_mul(page)
                .ok_or_else(|| invalid("NT_FILE offset overflow"))?;
            let end_name = names
                .iter()
                .position(|b| *b == 0)
                .ok_or_else(|| invalid("unterminated NT_FILE filename"))?;
            let path = PathBuf::from(OsStr::from_bytes(&names[..end_name]));
            names = &names[end_name + 1..];
            if main_flags.is_none() && !path.starts_with("/dev") {
                if let Ok(mut file) = options.open(&path) {
                    let mut header = [0; 32];
                    if file.read_exact(&mut header).is_ok()
                        && matches!(number(&header, 0, 4)?, 0xfeedface | 0xfeedfacf)
                        && number(&header, 12, 4)? == 2
                    {
                        main_flags = Some(number(&header, 24, 4)?);
                    }
                }
            }
            mappings.push(Mapping {
                start,
                end,
                offset,
                path,
            });
        }
        let starts: BTreeSet<_> = areas.iter().map(|a| a.address).collect();
        let matches = covering_mappings(&areas, &mappings);
        for (area, mapping) in areas.iter_mut().zip(matches) {
            if let Some(index) = mapping {
                let mapping = &mappings[index];
                area.path = Some(mapping.path.clone());
                area.offset = add(mapping.offset, area.address - mapping.start)?;
                area.length = area.size;
            }
        }
        // Upstream stopped after the first extra mapping; preserve all omitted mappings.
        for m in mappings {
            if !starts.contains(&m.start) {
                areas.push(Area {
                    address: m.start,
                    size: m.end - m.start,
                    offset: m.offset,
                    length: m.end - m.start,
                    prot: 1,
                    path: Some(m.path),
                });
            }
        }
        areas.retain(|a| a.prot != 0);
        let plan = Self {
            wide,
            areas,
            threads,
            flags: main_flags.unwrap_or(0),
        };
        plan.layout()?;
        Ok(plan)
    }
    fn layout(&self) -> io::Result<(u64, u64)> {
        let header_size = if self.wide { 32 } else { 28 };
        let segment_size = if self.wide { 72 } else { 56 };
        let thread_size = 8 + 8 + if self.wide { 168 } else { 64 } + 8 + 524;
        let commands =
            self.areas.len() as u64 * segment_size + self.threads.len() as u64 * thread_size;
        let mut offset = add(header_size, commands)?;
        if commands > u32::MAX as u64 || self.areas.len() + self.threads.len() > u32::MAX as usize {
            return Err(invalid("too many Mach-O commands"));
        }
        for area in &self.areas {
            offset = add(offset, area.size)?;
            if !self.wide
                && (offset > u32::MAX as u64
                    || area.address > u32::MAX as u64
                    || area.size > u32::MAX as u64)
            {
                return Err(invalid("32-bit Mach-O overflow"));
            }
        }
        Ok((commands, thread_size))
    }
    fn write(self, data: &[u8], options: &Options, out: &mut impl Write) -> io::Result<()> {
        let width = if self.wide { 8 } else { 4 };
        let header_size = if self.wide { 32 } else { 28 };
        let segment_size = if self.wide { 72 } else { 56 };
        // Upstream emits a zeroed 524-byte floating-point state (its own TODO).
        let (commands, thread_size) = self.layout()?;
        let mut offset = add(header_size, commands)?;
        for n in [
            if self.wide { 0xfeedfacf } else { 0xfeedface },
            if self.wide { 0x1000007 } else { 7 },
            3,
            4,
            self.areas.len() as u64 + self.threads.len() as u64,
            commands,
            self.flags,
        ] {
            emit(out, n, 4)?;
        }
        if self.wide {
            emit(out, 0, 4)?;
        }
        for area in &self.areas {
            emit(out, if self.wide { 0x19 } else { 1 }, 4)?;
            emit(out, segment_size, 4)?;
            zero(out, 16)?;
            for n in [area.address, area.size, offset, area.size] {
                emit(out, n, width)?;
            }
            for n in [area.prot, area.prot, 0, 0] {
                emit(out, n, 4)?;
            }
            offset = add(offset, area.size)?;
        }
        for registers in self.threads {
            for n in [
                4,
                thread_size,
                if self.wide { 4 } else { 1 },
                if self.wide { 42 } else { 16 },
            ] {
                emit(out, n, 4)?;
            }
            for n in registers {
                emit(out, n, width)?;
            }
            emit(out, if self.wide { 5 } else { 2 }, 4)?;
            emit(out, 131, 4)?;
            zero(out, 524)?;
        }
        for area in self.areas {
            let written = if area.length == 0 {
                0
            } else if let Some(path) = area.path {
                if path.starts_with("/dev") {
                    0
                } else if let Ok(mut file) = options.open(&path) {
                    file.seek(SeekFrom::Start(area.offset))?;
                    io::copy(&mut file.take(area.length), out)?
                } else {
                    0
                }
            } else {
                out.write_all(bytes(data, area.offset, area.length)?)?;
                area.length
            };
            zero(out, area.size - written)?;
        }
        Ok(())
    }
}

/// Validate the complete ELF metadata before emitting any output. Mappings whose
/// backing file is unavailable or shorter than the mapping are zero filled.
/// Floating-point states remain zero, matching the pinned upstream converter.
pub fn convert(data: &[u8], output: &mut impl Write, options: &Options) -> io::Result<()> {
    Plan::parse(data, options)?.write(data, options, output)
}

/// Convert files, rejecting input/output aliases before truncating the output.
/// Invalid ELF metadata leaves an existing destination untouched.
pub fn convert_file(input: &Path, output: &Path, options: &Options) -> io::Result<()> {
    use std::os::unix::fs::MetadataExt;
    let input_file = File::open(input)?;
    let input_meta = input_file.metadata()?;
    if let Ok(output_meta) = std::fs::metadata(output) {
        if input_meta.dev() == output_meta.dev() && input_meta.ino() == output_meta.ino() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "input and output are the same file",
            ));
        }
    }
    let mut data = Vec::new();
    input_file.take(u64::MAX).read_to_end(&mut data)?;
    let plan = Plan::parse(&data, options)?;
    if let Ok(output_meta) = std::fs::metadata(output) {
        for area in &plan.areas {
            if let Some(path) = &area.path {
                if let Ok(file) = options.open(path) {
                    let metadata = file.metadata()?;
                    if metadata.dev() == output_meta.dev() && metadata.ino() == output_meta.ino() {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidInput,
                            "output aliases a backing file",
                        ));
                    }
                }
            }
        }
    }
    let mut output = io::BufWriter::new(File::create(output)?);
    plan.write(&data, options, &mut output)?;
    output.flush()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn area(address: u64, size: u64) -> Area {
        Area {
            address,
            size,
            offset: 0,
            length: 0,
            prot: 1,
            path: None,
        }
    }
    fn mapping(start: u64, end: u64) -> Mapping {
        Mapping {
            start,
            end,
            offset: 0,
            path: PathBuf::new(),
        }
    }
    #[test]
    fn real_uid_home_fallback_does_not_select_effective_uid() {
        let status = "Name:\tconverter\nUid:\t1000\t2000\t3000\t4000\n";
        let passwd = "effective:x:2000:2000::/effective:/bin/sh\nreal:x:1000:1000::/real:/bin/sh\n";
        assert_eq!(
            home_for_real_uid(status, passwd),
            Some(PathBuf::from("/real"))
        );
        assert_eq!(home_for_real_uid("Uid:\t9\t2000", passwd), None);
    }
    #[test]
    fn ordered_lookup_preserves_first_overlap_for_unsorted_mappings() {
        let mappings = vec![
            mapping(40, 100),
            mapping(0, 200),
            mapping(20, 40),
            mapping(0, 200),
            mapping(9, 20),
        ];
        let areas: Vec<_> = (0..120)
            .flat_map(|address| (0..30).map(move |size| area(address, size)))
            .collect();
        let result = covering_mappings(&areas, &mappings);
        for (area, found) in areas.iter().zip(result) {
            assert_eq!(
                found,
                mappings
                    .iter()
                    .position(|m| m.start <= area.address && m.end >= area.address + area.size)
            );
        }
    }
    #[test]
    fn adversarial_nonmatching_mapping_lookup_is_bounded() {
        // A nested search would perform 400 million failed comparisons here.
        // All starts qualify, but no end reaches any area: the prefix-min query
        // returns immediately after the O(M log M) insertion sweep.
        let mappings: Vec<_> = (0..20000).map(|i| mapping(i, i + 1)).rev().collect();
        let areas: Vec<_> = (0..20000).map(|i| area(30000 + i, 1)).collect();
        assert!(
            covering_mappings(&areas, &mappings)
                .iter()
                .all(Option::is_none)
        );
    }
}
