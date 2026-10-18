// SPDX-License-Identifier: GPL-3.0-or-later
//! ELF core to Mach-O core conversion, adapted from Darling's hosttools coredump
//! converter at 60ba801decee7a00782f74f6be4c8ffb013f79ff (GPL-3.0-or-later).
//! Integer fields are decoded explicitly; core bytes never become native structs.
#![forbid(unsafe_code)]

mod backing;
mod fp;
mod payload;

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
    // Most calls pad a name or a reserved word. Do not initialize a 64KiB
    // buffer for each small padding field, or for an empty segment tail.
    if n <= 64 {
        return out.write_all(&[0; 64][..n as usize]);
    }
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

fn unavailable_backing(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::NotFound | io::ErrorKind::PermissionDenied
    )
}

fn open_read(path: &Path) -> io::Result<File> {
    // Opening a FIFO must not block before metadata can reject it. NONBLOCK
    // has no effect on regular-file reads; the descriptor always owns its fd.
    Ok(rustix::fs::open(
        path,
        rustix::fs::OFlags::RDONLY | rustix::fs::OFlags::NONBLOCK | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::empty(),
    )?
    .into())
}

fn open_candidate(path: &Path) -> io::Result<File> {
    let file = open_read(path).map_err(|error| {
        // Linux can reserve an fd before looking up a pathname. Under EMFILE,
        // prove absence without another descriptor; never reinterpret a real
        // source's resource or I/O failure as an unavailable zero-filled image.
        if error.raw_os_error() == Some(24) {
            if let Err(absent) = std::fs::metadata(path) {
                if unavailable_backing(&absent) {
                    return absent;
                }
            }
        }
        error
    })?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "backing must be a regular file",
        ));
    }
    Ok(file)
}

impl Options {
    fn resolve<T>(
        &self,
        path: &Path,
        mut attempt: impl FnMut(&Path) -> io::Result<T>,
    ) -> io::Result<T> {
        match attempt(path) {
            Ok(value) => return Ok(value),
            Err(error) if unavailable_backing(&error) => {}
            Err(error) => return Err(error),
        }
        if let Ok(relative) = path.strip_prefix(&self.prefix) {
            return attempt(&self.libexec.join(relative));
        }
        if let Ok(relative) = path.strip_prefix("/") {
            match attempt(&self.prefix.join(relative)) {
                Ok(value) => return Ok(value),
                Err(error) if unavailable_backing(&error) => {}
                Err(error) => return Err(error),
            }
            return attempt(&self.libexec.join(relative));
        }
        attempt(path)
    }
    fn open(&self, path: &Path) -> io::Result<File> {
        self.resolve(path, open_candidate)
    }
    fn metadata(&self, path: &Path) -> io::Result<std::fs::Metadata> {
        self.resolve(path, |candidate| std::fs::metadata(candidate))
    }
}

fn executable_flags(input: &mut impl Read) -> io::Result<Option<u64>> {
    let mut header = [0; 32];
    match input.read_exact(&mut header) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error),
    }
    if matches!(number(&header, 0, 4)?, 0xfeedface | 0xfeedfacf) && number(&header, 12, 4)? == 2 {
        Ok(Some(number(&header, 24, 4)?))
    } else {
        Ok(None)
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

struct Thread<'a> {
    registers: [u64; 21],
    tid: u32,
    floating: fp::State<'a>,
}
impl Thread<'_> {
    fn command_size(&self, wide: bool) -> u64 {
        16 + if wide { 168 } else { 64 }
            + self
                .floating
                .flavor_size()
                .map_or(0, |(_, size)| 8 + size as u64)
    }
}

struct Plan<'a> {
    wide: bool,
    align_large: bool,
    areas: Vec<Area>,
    threads: Vec<Thread<'a>>,
    floating_layout: Option<&'a [u8]>,
    flags: u64,
}

fn visit_notes<'a>(
    segments: &[(u64, &'a [u8])],
    mut visit: impl FnMut(&'a [u8], u64, &'a [u8]) -> io::Result<()>,
) -> io::Result<()> {
    for &(_, notes) in segments {
        let mut cursor = 0;
        while cursor < notes.len() as u64 {
            let name_size = number(notes, cursor, 4)?;
            let size = number(notes, add(cursor, 4)?, 4)?;
            let kind = number(notes, add(cursor, 8)?, 4)?;
            let name_start = add(cursor, 12)?;
            let name = bytes(notes, name_start, name_size)?;
            let desc = add(name_start, align4(name_size)?)?;
            let payload = bytes(notes, desc, size)?;
            cursor = add(desc, align4(size)?)?;
            bytes(notes, 0, cursor)?;
            visit(name, kind, payload)?;
        }
    }
    Ok(())
}
fn owner(name: &[u8], expected: &[u8]) -> bool {
    name.strip_suffix(&[0]).unwrap_or(name) == expected
}

struct ElfHeader {
    wide: bool,
    phoff: u64,
    entsize: u64,
    count: u64,
}
impl ElfHeader {
    fn parse(data: &[u8]) -> io::Result<Self> {
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
        Ok(Self {
            wide,
            phoff,
            entsize,
            count,
        })
    }
}

fn check_range(input_len: u64, offset: u64, length: u64) -> io::Result<()> {
    if add(offset, length)? > input_len {
        return Err(invalid("truncated ELF core"));
    }
    Ok(())
}

struct CoreHeaders {
    wide: bool,
    areas: Vec<Area>,
    notes: Vec<(u64, u64)>,
}
impl CoreHeaders {
    fn parse(header: &ElfHeader, table: &[u8], input_len: u64) -> io::Result<Self> {
        let wide = header.wide;
        let mut areas = Vec::new();
        let mut notes = Vec::new();
        for i in 0..header.count {
            let ph = bytes(table, i * header.entsize, header.entsize)?;
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
            if kind == 4 && length != 0 {
                check_range(input_len, offset, length)?;
                notes.push((offset, length));
            }
            if kind == 1 {
                if length > size {
                    return Err(invalid("PT_LOAD file size exceeds memory size"));
                }
                let end = add(address, size)?;
                if !wide && end > (1_u64 << 32) {
                    return Err(invalid("32-bit ELF virtual range overflow"));
                }
                if length != 0 {
                    check_range(input_len, offset, length)?;
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
        if notes.is_empty() {
            return Err(invalid("missing PT_NOTE segment"));
        }
        // Aliased note segments could multiply parsing work and attach one
        // register image repeatedly. Validate disjoint ranges before any scan.
        notes.sort_unstable_by_key(|(offset, _)| *offset);
        let mut previous_end = 0;
        for &(offset, length) in &notes {
            if offset < previous_end {
                return Err(invalid("overlapping PT_NOTE segments"));
            }
            previous_end = add(offset, length)?;
        }
        Ok(Self { wide, areas, notes })
    }
}

// Match the source page offset for large dumped ranges. On CoW filesystems
// this permits kernel-copy reflinks instead of copying every byte through RAM.
// Mach-O segment commands describe the padding explicitly; small segments and
// reconstructed/zero-filled mappings keep their previous compact layout.
fn payload_offset(offset: u64, area: &Area, align_large: bool) -> io::Result<u64> {
    if align_large && area.path.is_none() && area.length >= 65536 {
        add(offset, area.offset.wrapping_sub(offset) & 4095)
    } else {
        Ok(offset)
    }
}

impl<'a> Plan<'a> {
    fn parse(
        headers: CoreHeaders,
        notes: &[(u64, &'a [u8])],
        options: &Options,
    ) -> io::Result<Self> {
        let wide = headers.wide;
        let width = if wide { 8 } else { 4 };
        let mut areas = headers.areas;
        // Linux emits process-wide XSAVE layout metadata after thread regsets.
        // Pre-scan all segments, so foreign cores never depend on host CPUID.
        let mut floating_layout = None;
        let mut validated_layout = None;
        visit_notes(notes, |name, kind, payload| {
            if owner(name, b"LINUX") && kind == 0x205 {
                validated_layout = Some(fp::validate_layout(payload)?);
                if floating_layout.replace(payload).is_some() {
                    return Err(invalid("duplicate XSAVE layout note"));
                }
            }
            Ok(())
        })?;
        let mut files = None;
        let mut threads: Vec<Thread<'a>> = Vec::new();
        visit_notes(notes, |name, kind, payload| {
            if owner(name, b"CORE") && kind == 0x46494c45 && files.replace(payload).is_some() {
                return Err(invalid("duplicate NT_FILE note"));
            }
            if owner(name, b"CORE") && kind == 1 {
                let start = if wide { 112 } else { 72 };
                let indices: &[u64] = if wide {
                    &[
                        10, 5, 11, 12, 14, 13, 4, 19, 9, 8, 7, 6, 3, 2, 1, 0, 16, 18, 17, 25, 26,
                    ]
                } else {
                    &[6, 0, 1, 2, 4, 3, 5, 15, 16, 14, 12, 13, 7, 8, 9, 10]
                };
                bytes(payload, start, if wide { 216 } else { 68 })?;
                let mut registers = [0; 21];
                for (output, &index) in registers.iter_mut().zip(indices) {
                    let mut value = number(payload, start + index * width, width)?;
                    if !wide && matches!(index, 7 | 8 | 9 | 10 | 13 | 16) {
                        value &= 0xffff;
                    }
                    *output = value;
                }
                threads.push(Thread {
                    registers,
                    tid: number(payload, if wide { 32 } else { 24 }, 4)? as u32,
                    floating: fp::State::new(wide),
                });
            } else if (owner(name, b"CORE") && kind == 2)
                || (owner(name, b"LINUX") && matches!(kind, 0x202 | 0x46e62b7f))
            {
                threads
                    .last_mut()
                    .ok_or_else(|| invalid("orphan floating-point note"))?
                    .floating
                    .note(kind as u32, payload)?;
            }
            Ok(())
        })?;
        // One compact temporary index makes raw note TIDs unambiguous without
        // an allocator call for every thread's register vector or tree node.
        let mut tids: Vec<_> = threads.iter().map(|thread| thread.tid).collect();
        tids.sort_unstable();
        if tids.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(invalid("duplicate PRSTATUS thread ID"));
        }
        drop(tids);
        for thread in &mut threads {
            if let Some(layout) = validated_layout {
                thread.floating.set_validated_layout(layout)?;
            }
            thread.floating.validate()?;
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
                match options.open(&path) {
                    Ok(mut file) => main_flags = executable_flags(&mut file)?,
                    Err(error) if unavailable_backing(&error) => {}
                    Err(error) => return Err(error),
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
        let mut plan = Self {
            wide,
            align_large: false,
            areas,
            threads,
            floating_layout,
            flags: main_flags.unwrap_or(0),
        };
        // Padding is an optimization, never a reason to reject a valid packed
        // core close to the architecture's file-offset limit.
        plan.layout()?;
        plan.align_large = true;
        if plan.layout().is_err() {
            plan.align_large = false;
        }
        Ok(plan)
    }
    fn note_count(&self) -> u64 {
        u64::from(self.floating_layout.is_some())
            + self
                .threads
                .iter()
                .map(|thread| thread.floating.raw_notes().count() as u64)
                .sum::<u64>()
    }
    fn layout(&self) -> io::Result<u64> {
        let header_size = if self.wide { 32 } else { 28 };
        let segment_size = if self.wide { 72 } else { 56 };
        let mut commands = (self.areas.len() as u64)
            .checked_mul(segment_size)
            .ok_or_else(|| invalid("Mach-O command size overflow"))?;
        for thread in &self.threads {
            commands = add(commands, thread.command_size(self.wide))?;
        }
        commands = add(
            commands,
            self.note_count()
                .checked_mul(40)
                .ok_or_else(|| invalid("Mach-O note command size overflow"))?,
        )?;
        let mut offset = add(header_size, commands)?;
        let count = add(self.areas.len() as u64, self.threads.len() as u64)?;
        if commands > u32::MAX as u64 || add(count, self.note_count())? > u32::MAX as u64 {
            return Err(invalid("too many Mach-O commands"));
        }
        for area in &self.areas {
            offset = add(payload_offset(offset, area, self.align_large)?, area.size)?;
            if !self.wide
                && (offset > u32::MAX as u64
                    || area.address > u32::MAX as u64
                    || area.size > u32::MAX as u64
                    || add(area.address, area.size)? > (1_u64 << 32))
            {
                return Err(invalid("32-bit Mach-O overflow"));
            }
        }
        // LC_NOTE uses64-bit file offsets even in32-bit Mach-O.
        for thread in &self.threads {
            for (_, payload) in thread.floating.raw_notes() {
                offset = add(offset, add(32, payload.len() as u64)?)?;
            }
        }
        if let Some(layout) = self.floating_layout {
            add(offset, layout.len() as u64)?;
        }
        Ok(commands)
    }
    fn write<W: Write>(
        self,
        out: &mut W,
        mut payload: impl FnMut(&Area, &mut W) -> io::Result<u64>,
        mut zero_payload: impl FnMut(&mut W, u64) -> io::Result<()>,
    ) -> io::Result<()> {
        let width = if self.wide { 8 } else { 4 };
        let header_size = if self.wide { 32 } else { 28 };
        let segment_size = if self.wide { 72 } else { 56 };
        let commands = self.layout()?;
        let mut offset = add(header_size, commands)?;
        for n in [
            if self.wide { 0xfeedfacf } else { 0xfeedface },
            if self.wide { 0x1000007 } else { 7 },
            3,
            4,
            self.areas.len() as u64 + self.threads.len() as u64 + self.note_count(),
            commands,
            self.flags,
        ] {
            emit(out, n, 4)?;
        }
        if self.wide {
            emit(out, 0, 4)?;
        }
        let mut data_offset = offset;
        for area in &self.areas {
            offset = payload_offset(offset, area, self.align_large)?;
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
        let mut note_offset = offset;
        for thread in &self.threads {
            for n in [
                4,
                thread.command_size(self.wide),
                if self.wide { 4 } else { 1 },
                if self.wide { 42 } else { 16 },
            ] {
                emit(out, n, 4)?;
            }
            for &n in thread
                .registers
                .iter()
                .take(if self.wide { 21 } else { 16 })
            {
                emit(out, n, width)?;
            }
            if let Some((flavor, size)) = thread.floating.flavor_size() {
                emit(out, u64::from(flavor), 4)?;
                emit(out, (size / 4) as u64, 4)?;
                thread.floating.write(out)?;
            }
            for (_, payload) in thread.floating.raw_notes() {
                let size = add(32, payload.len() as u64)?;
                emit_note_command(out, b"DEARUST.XSTATE", note_offset, size)?;
                note_offset = add(note_offset, size)?;
            }
        }
        if let Some(layout) = self.floating_layout {
            emit_note_command(out, b"DEARUST.XLAYOUT", note_offset, layout.len() as u64)?;
        }
        for area in self.areas {
            let aligned = payload_offset(data_offset, &area, self.align_large)?;
            zero(out, aligned - data_offset)?;
            let written = payload(&area, out)?;
            zero_payload(out, area.size - written)?;
            data_offset = add(aligned, area.size)?;
        }
        for thread in &self.threads {
            for (kind, payload) in thread.floating.raw_notes() {
                emit(out, 1, 4)?; // extension schema version
                emit(out, if self.wide { 2 } else { 1 }, 4)?; // ELF class
                emit(out, u64::from(thread.tid), 8)?;
                emit(out, u64::from(kind), 4)?;
                emit(out, 0, 4)?;
                emit(out, payload.len() as u64, 8)?;
                out.write_all(payload)?;
            }
        }
        if let Some(layout) = self.floating_layout {
            out.write_all(layout)?;
        }
        Ok(())
    }
}

fn emit_note_command(out: &mut impl Write, owner: &[u8], offset: u64, size: u64) -> io::Result<()> {
    emit(out, 0x31, 4)?; // LC_NOTE
    emit(out, 40, 4)?;
    out.write_all(owner)?;
    zero(out, 16 - owner.len() as u64)?;
    emit(out, offset, 8)?;
    emit(out, size, 8)
}

/// Validate the complete ELF metadata before emitting any output. Mappings whose
/// backing file is unavailable or shorter than the mapping are zero filled.
/// Only provided register state is emitted; raw Linux images are retained in
/// per-thread LC_NOTE records, including components without a Darwin flavor.
pub fn convert(data: &[u8], output: &mut impl Write, options: &Options) -> io::Result<()> {
    let header = ElfHeader::parse(data)?;
    let table = bytes(data, header.phoff, header.entsize * header.count)?;
    let headers = CoreHeaders::parse(&header, table, data.len() as u64)?;
    let notes: Vec<_> = headers
        .notes
        .iter()
        .map(|&(offset, length)| Ok((offset, bytes(data, offset, length)?)))
        .collect::<io::Result<_>>()?;
    let plan = Plan::parse(headers, &notes, options)?;
    plan.write(
        output,
        |area, out| {
            if area.length == 0 {
                Ok(0)
            } else if let Some(path) = &area.path {
                if let Some(mut file) = open_backing(options, path)? {
                    file.seek(SeekFrom::Start(area.offset))?;
                    io::copy(&mut file.take(area.length), out)
                } else {
                    Ok(0)
                }
            } else {
                out.write_all(bytes(data, area.offset, area.length)?)?;
                Ok(area.length)
            }
        },
        zero,
    )
}

// An unavailable mapping is reconstructed as zeros. Resource exhaustion and I/O
// errors are not absence: silently accepting them would fabricate missing data.
fn open_backing(options: &Options, path: &Path) -> io::Result<Option<File>> {
    if path.starts_with("/dev") {
        return Ok(None);
    }
    match options.open(path) {
        Ok(file) => Ok(Some(file)),
        Err(error) if unavailable_backing(&error) => Ok(None),
        Err(error) => Err(error),
    }
}

fn read_range(file: &mut File, input_len: u64, offset: u64, length: u64) -> io::Result<Vec<u8>> {
    check_range(input_len, offset, length)?;
    let length = usize::try_from(length).map_err(|_| invalid("metadata too large"))?;
    let mut data = Vec::new();
    data.try_reserve_exact(length).map_err(io::Error::other)?;
    // Bounded read_to_end avoids initializing metadata twice. take() cannot
    // consume a growing file beyond the already validated range.
    file.seek(SeekFrom::Start(offset))?;
    file.take(length as u64).read_to_end(&mut data)?;
    if data.len() != length {
        return Err(invalid("truncated ELF core"));
    }
    Ok(data)
}

/// Convert a regular file with owned metadata and bounded payload buffering.
/// Invalid metadata and source/destination aliases fail before truncation. The
/// input is pinned, but concurrent writers do not provide snapshot isolation.
/// Truncation and later I/O errors are reported, and may leave partial output.
pub fn convert_file(input: &Path, output: &Path, options: &Options) -> io::Result<()> {
    let mut input_file = open_read(input)?;
    let input_meta = input_file.metadata()?;
    if !input_meta.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "input must be a regular core file",
        ));
    }
    let input_len = input_meta.len();
    // The 52-byte common prefix identifies the class; an ELF64 header needs
    // twelve more bytes. No assumption about the program table's location.
    let elf = read_range(&mut input_file, input_len, 0, input_len.min(64))?;
    let header = ElfHeader::parse(&elf)?;
    drop(elf);
    let table = read_range(
        &mut input_file,
        input_len,
        header.phoff,
        header.entsize * header.count,
    )?;
    let headers = CoreHeaders::parse(&header, &table, input_len)?;
    drop(table);
    let storage = headers
        .notes
        .iter()
        .map(|&(offset, length)| {
            Ok((
                offset,
                read_range(&mut input_file, input_len, offset, length)?,
            ))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let notes: Vec<_> = storage
        .iter()
        .map(|(offset, data)| (*offset, data.as_slice()))
        .collect();
    let plan = Plan::parse(headers, &notes, options)?;
    if input_file.metadata()?.len() != input_len {
        return Err(invalid("input file changed length while reading"));
    }
    let destination = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .open(output)?;
    let output_meta = destination.metadata()?;
    let regular_output = output_meta.is_file();
    if backing::aliases(&input_meta, &output_meta) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "input and output are the same file",
        ));
    }
    // Even skipped device/empty mappings must not alias the destination. They
    // are not read later, so these identity-only selections need no held fd.
    let identity_only: BTreeSet<_> = plan
        .areas
        .iter()
        .filter_map(|area| {
            area.path
                .as_deref()
                .filter(|path| area.length == 0 || path.starts_with("/dev"))
        })
        .collect();
    for path in identity_only {
        // No data is selected from skipped/empty ranges. Descriptor-free
        // identity selection also works with no spare fd, and is never reopened.
        match options.metadata(path) {
            Ok(metadata) if backing::aliases(&metadata, &output_meta) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "output aliases a backing file",
                ));
            }
            Ok(_) => {}
            Err(error) if unavailable_backing(&error) => {}
            Err(error) => return Err(error),
        }
    }
    let mut backing =
        backing::BackingFiles::prepare(&plan.areas, options, &output_meta, &input_file)?;
    if input_file.metadata()?.len() != input_len {
        return Err(invalid(
            "input file changed length while reading backing files",
        ));
    }
    if regular_output {
        destination.set_len(0)?;
    }
    let mut output = io::BufWriter::with_capacity(65536, destination);
    let mut copy_buffer = payload::CopyBuffer::default();
    let mut sparse_output = false;
    plan.write(
        &mut output,
        |area, out| {
            if area.length == 0 {
                Ok(0)
            } else if area.path.is_some() {
                backing.copy(area, out)
            } else {
                copy_buffer.copy(&input_file, area.offset, area.length, out, regular_output)
            }
        },
        |out, length| {
            if regular_output && length >= 65536 {
                // Truncation removed every old extent. Seeking over a large zero
                // range avoids allocating/writing pages; subsequent writes or the
                // final set_len materialize its exact logical size.
                let position = out.stream_position()?;
                out.seek(SeekFrom::Start(add(position, length)?))?;
                sparse_output = true;
                Ok(())
            } else {
                zero(out, length)
            }
        },
    )?;
    if input_file.metadata()?.len() != input_len {
        return Err(invalid("input file changed length while converting"));
    }
    output.flush()?;
    if sparse_output {
        let length = output.stream_position()?;
        output.get_ref().set_len(length)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn backing_absence_does_not_include_resource_or_io_errors() {
        for errno in [5, 23, 24, 40] {
            assert!(!unavailable_backing(&io::Error::from_raw_os_error(errno)));
        }
        for kind in [io::ErrorKind::NotFound, io::ErrorKind::PermissionDenied] {
            assert!(unavailable_backing(&io::Error::from(kind)));
        }
    }

    #[test]
    fn backing_open_does_not_hide_candidate_error_with_overlay() {
        use std::os::unix::fs::symlink;
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "dearust-backing-open-{}-{unique}",
            std::process::id()
        ));
        std::fs::create_dir(&directory).unwrap();
        let overlay = directory.join("overlay");
        std::fs::create_dir(&overlay).unwrap();
        let path = directory.join("loop");
        symlink("loop", &path).unwrap();
        std::fs::write(overlay.join("loop"), b"must not replace failed source").unwrap();
        let options = Options {
            prefix: directory.clone(),
            libexec: overlay,
        };
        let result = options.open(&path);
        std::fs::remove_dir_all(directory).unwrap();
        assert_eq!(result.unwrap_err().raw_os_error(), Some(40));
    }

    #[test]
    fn executable_flags_accept_short_files_but_propagate_read_errors() {
        struct Failing;
        impl Read for Failing {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::from_raw_os_error(5))
            }
        }
        assert_eq!(executable_flags(&mut &b"short"[..]).unwrap(), None);
        assert_eq!(
            executable_flags(&mut Failing).unwrap_err().raw_os_error(),
            Some(5)
        );
        for magic in [0xfeedface_u32, 0xfeedfacf] {
            let mut header = [0; 32];
            header[..4].copy_from_slice(&magic.to_le_bytes());
            header[12..16].copy_from_slice(&2_u32.to_le_bytes());
            header[24..28].copy_from_slice(&0x1234_u32.to_le_bytes());
            assert_eq!(executable_flags(&mut &header[..]).unwrap(), Some(0x1234));
            header[12..16].copy_from_slice(&1_u32.to_le_bytes());
            assert_eq!(executable_flags(&mut &header[..]).unwrap(), None);
        }
    }

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
