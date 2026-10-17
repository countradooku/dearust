// Safe Rust port of src/libelfloader/wrapgen/print_wrapped_elf.cpp.
// SPDX-License-Identifier: GPL-3.0-or-later
//! Names in every __TEXT,__elfname section of the first Mach-O architecture.
use super::{Endian, Error, Metadata, host_size, range, thin_kind};

/// Parse all matching sections before exposing any output. FAT files deliberately
/// select only their first architecture, matching the upstream wrapper tool.
/// Each section must contain 1..=100 bytes and end with NUL. Names are raw bytes,
/// stopping at the first NUL, as with upstream's C-string stream insertion.
pub fn names(data: &[u8]) -> Result<Vec<&[u8]>, Error> {
    let data = match range(data, 0, 4)? {
        [0xca, 0xfe, 0xba, 0xbe] => first_architecture(data, Endian::Big)?,
        [0xbe, 0xba, 0xfe, 0xca] => first_architecture(data, Endian::Little)?,
        _ => data,
    };
    let (endian, wide) = thin_kind(data)?;
    let header_size = if wide { 32 } else { 28 };
    range(data, 0, header_size)?;
    let count = host_size(endian.u32(data, 16)?.into())?;
    let size = host_size(endian.u32(data, 20)?.into())?;
    let commands = range(data, header_size, size)?;
    if count > size / 8 {
        return Err(Error("load command count exceeds command region"));
    }
    let mut cursor = 0;
    let mut sections = Vec::new();
    for _ in 0..count {
        let header = range(commands, cursor, 8)?;
        let kind = endian.u32(header, 0)?;
        let size = host_size(endian.u32(header, 4)?.into())?;
        if size < 8 || size % if wide { 8 } else { 4 } != 0 {
            return Err(Error("invalid load command size or alignment"));
        }
        let command = range(commands, cursor, size)?;
        if kind == 1 || kind == 0x19 {
            if (kind == 0x19) != wide {
                return Err(Error("segment class disagrees with image"));
            }
            // Reuse checked section bounds and zero-fill rejection. Preserve
            // section order instead of Metadata's first-name selection policy.
            Metadata::segment(data, command, endian, wide, &mut sections)?;
        }
        cursor += size;
    }
    if cursor != commands.len() {
        return Err(Error("load command sizes disagree with header"));
    }
    sections
        .into_iter()
        .map(|(start, end)| {
            let text = &data[start..end];
            if text.is_empty() || text.len() > 100 {
                return Err(Error("__TEXT,__elfname section has unusual length"));
            }
            if text.last() != Some(&0) {
                return Err(Error(
                    "__TEXT,__elfname section contents don't look like a string",
                ));
            }
            let end = text
                .iter()
                .position(|&b| b == 0)
                .ok_or(Error("missing NUL"))?;
            Ok(&text[..end])
        })
        .collect()
}

fn first_architecture(data: &[u8], endian: Endian) -> Result<&[u8], Error> {
    let count = host_size(endian.u32(data, 4)?.into())?;
    if count == 0 {
        return Err(Error("empty fat container"));
    }
    let table_size = count.checked_mul(20).ok_or(Error("fat table overflow"))?;
    let table = range(data, 8, table_size)?;
    let offset = host_size(endian.u32(table, 8)?.into())?;
    let size = host_size(endian.u32(table, 12)?.into())?;
    if offset < 8 + table_size {
        return Err(Error("fat slice overlaps architecture table"));
    }
    let alignment = 1u64
        .checked_shl(endian.u32(table, 16)?)
        .ok_or(Error("invalid fat alignment"))?;
    if offset as u64 % alignment != 0 {
        return Err(Error("misaligned fat slice"));
    }
    let image = range(data, offset, size)?;
    let (inner_endian, _) = thin_kind(image)?;
    if inner_endian.u32(image, 4)? != endian.u32(table, 0)?
        || inner_endian.u32(image, 8)? != endian.u32(table, 4)?
    {
        return Err(Error("fat architecture disagrees with thin header"));
    }
    Ok(image)
}
