// This file is part of Darling, GPL-3.0-or-later.
// Rust port of src/buildtools/{elfdep,getuuid}.c, originally
// Copyright (C) 2018-2020 Lubos Dolezel.

//! Bounds-checked Mach-O metadata extraction. All returned names borrow the input.
//! This is a metadata reader, not a validator for executing a Mach-O image.
#![forbid(unsafe_code)]

use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Error(pub &'static str);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Copy)]
enum Endian {
    Little,
    Big,
}

impl Endian {
    fn u32(self, data: &[u8], offset: usize) -> Result<u32, Error> {
        let bytes = range(data, offset, 4)?
            .try_into()
            .map_err(|_| Error("invalid u32"))?;
        Ok(match self {
            Self::Little => u32::from_le_bytes(bytes),
            Self::Big => u32::from_be_bytes(bytes),
        })
    }

    fn u64(self, data: &[u8], offset: usize) -> Result<u64, Error> {
        let bytes = range(data, offset, 8)?
            .try_into()
            .map_err(|_| Error("invalid u64"))?;
        Ok(match self {
            Self::Little => u64::from_le_bytes(bytes),
            Self::Big => u64::from_be_bytes(bytes),
        })
    }
}

fn range(data: &[u8], start: usize, size: usize) -> Result<&[u8], Error> {
    let end = start.checked_add(size).ok_or(Error("range overflow"))?;
    data.get(start..end).ok_or(Error("range outside input"))
}

fn host_size(value: u64) -> Result<usize, Error> {
    usize::try_from(value).map_err(|_| Error("offset or size exceeds address space"))
}

fn field_name(field: &[u8], name: &[u8]) -> bool {
    let end = field.iter().position(|&b| b == 0).unwrap_or(field.len());
    &field[..end] == name
}

/// UUID as stored in LC_UUID, formatted like the upstream getuuid tool.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Uuid(pub [u8; 16]);

impl fmt::Display for Uuid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, byte) in self.0.iter().enumerate() {
            if matches!(i, 4 | 6 | 8 | 10) {
                f.write_str("-")?;
            }
            write!(f, "{byte:02X}")?;
        }
        Ok(())
    }
}

/// One UUID per architecture, in file order, and the first ELF dependency.
#[derive(Debug, PartialEq, Eq)]
pub struct Metadata<'a> {
    pub uuids: Vec<Uuid>,
    /// Bytes before the first NUL in __TEXT,__elfname; no UTF-8 requirement.
    pub elf_dependency: Option<&'a [u8]>,
}

impl<'a> Metadata<'a> {
    /// Parses thin 32/64-bit images or FAT/FAT64 containers, in either byte order.
    /// Every architecture and command is checked before metadata is returned.
    /// Nested fat containers are rejected. Total slice parsing is bounded by
    /// input size, preventing overlapping slices from amplifying parsing work.
    pub fn parse(data: &'a [u8]) -> Result<Self, Error> {
        let mut metadata = Self {
            uuids: Vec::new(),
            elf_dependency: None,
        };
        let (endian, fat64) = match range(data, 0, 4)? {
            [0xca, 0xfe, 0xba, 0xbe] => (Endian::Big, false),
            [0xbe, 0xba, 0xfe, 0xca] => (Endian::Little, false),
            [0xca, 0xfe, 0xba, 0xbf] => (Endian::Big, true),
            [0xbf, 0xba, 0xfe, 0xca] => (Endian::Little, true),
            _ => {
                metadata.thin(data)?;
                return Ok(metadata);
            }
        };
        let count = host_size(endian.u32(data, 4)?.into())?;
        if count == 0 {
            return Err(Error("empty fat container"));
        }
        let stride = if fat64 { 32 } else { 20 };
        let table_size = count
            .checked_mul(stride)
            .ok_or(Error("fat table overflow"))?;
        let table = range(data, 8, table_size)?;
        let table_end = 8 + table_size; // checked by range above
        let mut slices = Vec::new();
        for (index, arch) in table.chunks_exact(stride).enumerate() {
            let (offset, size, align) = if fat64 {
                if endian.u32(arch, 28)? != 0 {
                    return Err(Error("nonzero fat64 reserved field"));
                }
                (
                    endian.u64(arch, 8)?,
                    endian.u64(arch, 16)?,
                    endian.u32(arch, 24)?,
                )
            } else {
                (
                    endian.u32(arch, 8)?.into(),
                    endian.u32(arch, 12)?.into(),
                    endian.u32(arch, 16)?,
                )
            };
            let alignment = 1u64
                .checked_shl(align)
                .ok_or(Error("invalid fat alignment"))?;
            if offset % alignment != 0 {
                return Err(Error("misaligned fat slice"));
            }
            let offset = host_size(offset)?;
            let size = host_size(size)?;
            if offset < table_end {
                return Err(Error("fat slice overlaps architecture table"));
            }
            range(data, offset, size)?;
            slices.push((offset, offset + size, index));
        }
        // A sum-of-sizes budget alone allows overlapping slices when a file
        // also contains unused padding. Reject overlap before parsing a slice.
        slices.sort_unstable_by_key(|&(start, _, _)| start);
        for pair in slices.windows(2) {
            if pair[0].1 > pair[1].0 {
                return Err(Error("overlapping fat slices"));
            }
        }
        slices.sort_unstable_by_key(|&(_, _, index)| index);
        for (start, end, index) in slices {
            let arch = &table[index * stride..(index + 1) * stride];
            let image = &data[start..end];
            // Cross-check architecture fields against the actual thin header.
            let (inner_endian, _) = thin_kind(image)?;
            if inner_endian.u32(image, 4)? != endian.u32(arch, 0)?
                || inner_endian.u32(image, 8)? != endian.u32(arch, 4)?
            {
                return Err(Error("fat architecture disagrees with thin header"));
            }
            metadata.thin(image)?;
        }
        Ok(metadata)
    }

    fn thin(&mut self, data: &'a [u8]) -> Result<(), Error> {
        let (endian, wide) = thin_kind(data)?;
        let header_size = if wide { 32 } else { 28 };
        range(data, 0, header_size)?;
        let count = host_size(endian.u32(data, 16)?.into())?;
        let commands_size = host_size(endian.u32(data, 20)?.into())?;
        let commands = range(data, header_size, commands_size)?;
        if count > commands_size / 8 {
            return Err(Error("load command count exceeds command region"));
        }
        let mut cursor = 0;
        let mut uuid = None;
        let mut names = Vec::new();
        for _ in 0..count {
            let header = range(commands, cursor, 8)?;
            let kind = endian.u32(header, 0)?;
            let size = host_size(endian.u32(header, 4)?.into())?;
            if size < 8 || size % if wide { 8 } else { 4 } != 0 {
                return Err(Error("invalid load command size or alignment"));
            }
            let command = range(commands, cursor, size)?;
            match kind {
                0x1b => {
                    if size != 24 {
                        return Err(Error("invalid UUID command size"));
                    }
                    if uuid.is_none() {
                        uuid = Some(Uuid(
                            range(command, 8, 16)?
                                .try_into()
                                .map_err(|_| Error("invalid UUID"))?,
                        ));
                    }
                }
                1 | 0x19 => {
                    if (kind == 0x19) != wide {
                        return Err(Error("segment class disagrees with image"));
                    }
                    Self::segment(data, command, endian, wide, &mut names)?;
                }
                _ => {} // Unknown commands are skipped using their validated size.
            }
            cursor += size; // bounded by the command slice
        }
        if cursor != commands.len() {
            return Err(Error("load command sizes disagree with header"));
        }
        // Sections may alias the same payload. Scan in offset order so each
        // byte is examined at most once, even for hostile overlapping names.
        // Keep the first section in command order for upstream compatibility.
        let first_name = names.first().copied();
        names.sort_unstable();
        let mut next_nul = None;
        for (start, end) in names {
            let nul = match next_nul.filter(|&nul| nul >= start) {
                Some(nul) if nul < end => nul,
                Some(_) => return Err(Error("ELF name lacks a NUL inside its section")),
                None => {
                    start
                        + data[start..end]
                            .iter()
                            .position(|&b| b == 0)
                            .ok_or(Error("ELF name lacks a NUL inside its section"))?
                }
            };
            next_nul = Some(nul);
            if self.elf_dependency.is_none()
                && first_name.is_some_and(|(first_start, _)| first_start == start)
            {
                self.elf_dependency = Some(&data[start..nul]);
            }
        }
        if let Some(uuid) = uuid {
            self.uuids.push(uuid);
        }
        Ok(())
    }

    fn segment(
        image: &'a [u8],
        command: &[u8],
        endian: Endian,
        wide: bool,
        names: &mut Vec<(usize, usize)>,
    ) -> Result<(), Error> {
        let (header_size, section_size, count_offset) =
            if wide { (72, 80, 64) } else { (56, 68, 48) };
        range(command, 0, header_size)?;
        let count = host_size(endian.u32(command, count_offset)?.into())?;
        let size = count
            .checked_mul(section_size)
            .ok_or(Error("section table overflow"))?;
        let sections = range(command, header_size, size)?;
        if header_size + size != command.len() {
            return Err(Error("segment section count disagrees with command size"));
        }
        if !field_name(range(command, 8, 16)?, b"__TEXT") {
            return Ok(());
        }
        for section in sections.chunks_exact(section_size) {
            if !field_name(range(section, 0, 16)?, b"__elfname") {
                continue;
            }
            // A zero-fill section has no corresponding bytes in the file.
            let flags = endian.u32(section, if wide { 64 } else { 56 })?;
            if matches!(flags & 0xff, 1 | 0xc | 0x12) {
                return Err(Error("ELF name is in a zero-fill section"));
            }
            let size = if wide {
                endian.u64(section, 40)?
            } else {
                endian.u32(section, 36)?.into()
            };
            let offset = endian.u32(section, if wide { 48 } else { 40 })?;
            let start = host_size(offset.into())?;
            let size = host_size(size)?;
            range(image, start, size)?;
            names.push((start, start + size));
        }
        Ok(())
    }
}

fn thin_kind(data: &[u8]) -> Result<(Endian, bool), Error> {
    match range(data, 0, 4)? {
        [0xce, 0xfa, 0xed, 0xfe] => Ok((Endian::Little, false)),
        [0xfe, 0xed, 0xfa, 0xce] => Ok((Endian::Big, false)),
        [0xcf, 0xfa, 0xed, 0xfe] => Ok((Endian::Little, true)),
        [0xfe, 0xed, 0xfa, 0xcf] => Ok((Endian::Big, true)),
        _ => Err(Error(
            "unrecognized thin Mach-O magic (nested fat files are unsupported)",
        )),
    }
}

/// Shared command-line implementation. Returns only after parsing the complete
/// input, so a corrupt later architecture cannot leave partial success output.
pub fn run(tool: &str) -> Result<bool, Box<dyn std::error::Error>> {
    use std::io::Write;
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or_else(|| format!("Usage: {tool} <macho-file>"))?;
    if args.next().is_some() {
        return Err(format!("Usage: {tool} <macho-file>").into());
    }
    let data = std::fs::read(&path)
        .map_err(|e| format!("Cannot read {}: {e}", std::path::Path::new(&path).display()))?;
    let metadata = Metadata::parse(&data)?;
    let mut stdout = std::io::stdout().lock();
    if tool == "getuuid" {
        for (i, uuid) in metadata.uuids.iter().enumerate() {
            if i != 0 {
                stdout.write_all(b";")?;
            }
            write!(stdout, "{uuid}")?;
        }
        stdout.flush()?;
        Ok(!metadata.uuids.is_empty())
    } else {
        if let Some(name) = metadata.elf_dependency {
            stdout.write_all(name)?;
            stdout.write_all(b"\n")?;
        }
        stdout.flush()?;
        Ok(true)
    }
}

pub mod elf_wrapper;

pub mod wrapped_elf;
