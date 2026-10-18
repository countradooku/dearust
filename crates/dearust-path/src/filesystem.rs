//! Allocation-free filesystem accounting conversion and procfs record parsing.
#![forbid(unsafe_code)]

use core::mem::MaybeUninit;

const EIO: i64 = -5;
const ENAMETOOLONG: i64 = -63;
const TYPE: usize = 72;
const SOURCE: usize = 1112;

/// Preserve Darling's host filesystem-magic identity and virtual root mount owner.
/// Only flags with an actual Darwin equivalent cross the boundary.
pub(crate) fn convert(input: &[u64; 15], output: &mut [u8; 2168]) {
    output.fill(0);
    // Linux block counts use the fundamental fragment size, while f_bsize is
    // the optimal transfer size. glibc statvfs retains this distinction.
    let fundamental = if input[9] == 0 { input[1] } else { input[9] };
    output[..4].copy_from_slice(&(fundamental as u32).to_ne_bytes());
    output[4..8].copy_from_slice(&(input[1] as u32).to_ne_bytes());
    for (index, value) in input[2..8].iter().enumerate() {
        let offset = 8 + index * 8;
        output[offset..offset + 8].copy_from_slice(&value.to_ne_bytes());
    }
    output[60..64].copy_from_slice(&(input[0] as u32).to_ne_bytes());
    let linux = input[10];
    let flags = ((linux & 1) as u32)
        | (((linux >> 1) & 1) as u32 * 8)
        | (((linux >> 2) & 1) as u32 * 16)
        | (((linux >> 3) & 1) as u32 * 4)
        | (((linux >> 4) & 1) as u32 * 2)
        | (((linux >> 10) & 1) as u32 * 0x1000_0000);
    output[64..68].copy_from_slice(&flags.to_ne_bytes());
}

/// Decode a successful Linux statmount result. Strings are already unescaped.
/// Only validated results mutate output; the terminated mountpoint is moved to
/// the start of `bytes`, and its length excluding NUL is returned.
pub(crate) fn convert_statmount(bytes: &mut [u8], output: &mut [u8; 2168]) -> Result<usize, ()> {
    fn u32_at(bytes: &[u8], offset: usize) -> Result<u32, ()> {
        Ok(u32::from_ne_bytes(
            bytes
                .get(offset..offset.checked_add(4).ok_or(())?)
                .ok_or(())?
                .try_into()
                .map_err(|_| ())?,
        ))
    }
    fn string(bytes: &[u8], size: usize, field: usize) -> Result<(usize, usize), ()> {
        let start = 512_usize
            .checked_add(u32_at(bytes, field)? as usize)
            .ok_or(())?;
        let tail = bytes.get(start..size).ok_or(())?;
        let length = tail.iter().position(|byte| *byte == 0).ok_or(())?;
        Ok((start, start.checked_add(length).ok_or(())?))
    }
    let size = u32_at(bytes, 0)? as usize;
    if size < 512 || size > bytes.len() {
        return Err(());
    }
    let mask = u64::from_ne_bytes(bytes.get(8..16).ok_or(())?.try_into().map_err(|_| ())?);
    if mask & 0x230 != 0x230 {
        return Err(());
    }
    let (point_start, point_end) = string(bytes, size, 108)?;
    let point_length = point_end.checked_sub(point_start).ok_or(())?;
    if point_length == 0 || point_length > 4095 || bytes.get(point_start) != Some(&b'/') {
        return Err(());
    }
    let (type_start, type_end) = string(bytes, size, 36)?;
    let (source_start, source_end) = string(bytes, size, 124)?;
    if type_start == type_end || source_start == source_end {
        return Err(());
    }
    let subtype = if mask & 0x100 != 0 {
        let (start, end) = string(bytes, size, 120)?;
        bytes.get(start..end).ok_or(())?
    } else {
        &[]
    };
    let kind = bytes.get(type_start..type_end).ok_or(())?;
    let source = bytes.get(source_start..source_end).ok_or(())?;
    output[TYPE..TYPE + 16].fill(0);
    let typename = kind
        .iter()
        .copied()
        .chain(core::iter::once(b'.').take(usize::from(!subtype.is_empty())))
        .chain(subtype.iter().copied());
    for (output, byte) in output[TYPE..TYPE + 15].iter_mut().zip(typename) {
        *output = byte;
    }
    output[SOURCE..SOURCE + 1024].fill(0);
    for (output, byte) in output[SOURCE..SOURCE + 1023].iter_mut().zip(source) {
        *output = *byte;
    }
    // All indices were checked against returned size before mutating anything.
    // The destination begins before the strings, so overlap is intentional.
    for index in 0..=point_length {
        let source = point_start.checked_add(index).ok_or(())?;
        let byte = *bytes.get(source).ok_or(())?;
        *bytes.get_mut(index).ok_or(())? = byte;
    }
    Ok(point_length)
}

/// Incrementally finds the exact `mnt_id:` field, skipping unrelated long lines.
pub(crate) struct MountIdParser {
    prefix: usize,
    skip: bool,
    value: u64,
    digits: bool,
    trailing: bool,
    found: Option<u64>,
}

impl MountIdParser {
    pub(crate) fn new() -> Self {
        Self {
            prefix: 0,
            skip: false,
            value: 0,
            digits: false,
            trailing: false,
            found: None,
        }
    }

    pub(crate) fn feed(&mut self, bytes: &[u8]) -> Result<Option<u64>, i64> {
        for &byte in bytes {
            if self.found.is_some() {
                break;
            }
            if byte == b'\n' {
                self.end_line()?;
            } else if !self.skip {
                const PREFIX: &[u8] = b"mnt_id:";
                if self.prefix < PREFIX.len() {
                    if byte == PREFIX[self.prefix] {
                        self.prefix += 1;
                    } else {
                        self.skip = true;
                    }
                } else if byte.is_ascii_digit() && !self.trailing {
                    self.value = self
                        .value
                        .checked_mul(10)
                        .and_then(|value| value.checked_add(u64::from(byte - b'0')))
                        .ok_or(EIO)?;
                    self.digits = true;
                } else if matches!(byte, b' ' | b'\t' | b'\r') {
                    self.trailing |= self.digits;
                } else {
                    return Err(EIO);
                }
            }
        }
        Ok(self.found)
    }

    fn end_line(&mut self) -> Result<(), i64> {
        if !self.skip && self.prefix == b"mnt_id:".len() {
            if !self.digits {
                return Err(EIO);
            }
            self.found = Some(self.value);
        }
        self.prefix = 0;
        self.skip = false;
        self.value = 0;
        self.digits = false;
        self.trailing = false;
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> Result<Option<u64>, i64> {
        if self.found.is_none() {
            self.end_line()?;
        }
        Ok(self.found)
    }
}

/// Selects one mountinfo record by mount ID, without buffering complete lines.
/// Only the mountpoint prefix through `point_len() + 1` is initialized.
pub(crate) struct MountParser<'a> {
    id: u64,
    point: &'a mut [MaybeUninit<u8>; 4096],
    output: &'a mut [u8; 2168],
    point_len: usize,
    saved_len: usize,
    field: usize,
    token_len: usize,
    token_dash: bool,
    number: u64,
    number_valid: bool,
    selected: bool,
    separator: bool,
    tail_fields: usize,
    escape_digits: u8,
    escape_value: u8,
    absolute: bool,
    done: bool,
}

impl<'a> MountParser<'a> {
    pub(crate) fn new(
        id: u64,
        point: &'a mut [MaybeUninit<u8>; 4096],
        output: &'a mut [u8; 2168],
    ) -> Self {
        point[0].write(0);
        Self {
            id,
            point,
            output,
            point_len: 0,
            saved_len: 0,
            field: 0,
            token_len: 0,
            token_dash: false,
            number: 0,
            number_valid: true,
            selected: false,
            separator: false,
            tail_fields: 0,
            escape_digits: 0,
            escape_value: 0,
            absolute: false,
            done: false,
        }
    }

    pub(crate) fn point_len(&self) -> usize {
        self.point_len
    }

    pub(crate) fn feed(&mut self, bytes: &[u8]) -> Result<bool, i64> {
        for &byte in bytes {
            if self.done {
                break;
            }
            if byte == b'\n' {
                self.end_line()?;
            } else if byte == b' ' || byte == b'\t' {
                self.end_token()?;
            } else {
                if self.token_len == 0 {
                    self.token_dash = byte == b'-';
                    self.saved_len = 0;
                } else {
                    self.token_dash = false;
                }
                self.token_len = self.token_len.saturating_add(1);
                if self.field == 0 {
                    self.number_valid &= byte.is_ascii_digit();
                    if self.number_valid {
                        if let Some(value) = self
                            .number
                            .checked_mul(10)
                            .and_then(|value| value.checked_add(u64::from(byte - b'0')))
                        {
                            self.number = value;
                        } else {
                            self.number_valid = false;
                        }
                    }
                } else if self.selected && self.saved_field() {
                    self.decode(byte)?;
                }
            }
        }
        Ok(self.done)
    }

    fn saved_field(&self) -> bool {
        self.field == 4 || (self.separator && self.tail_fields < 2)
    }

    fn decode(&mut self, byte: u8) -> Result<(), i64> {
        if self.escape_digits > 0 {
            if !(b'0'..=b'7').contains(&byte) {
                return Err(EIO);
            }
            // Three octal digits represent one byte; reject values above 255.
            if self.escape_digits == 3 && byte > b'3' {
                return Err(EIO);
            }
            self.escape_value = (self.escape_value << 3) | (byte - b'0');
            self.escape_digits -= 1;
            if self.escape_digits != 0 {
                return Ok(());
            }
            self.save(self.escape_value)
        } else if byte == b'\\' {
            self.escape_digits = 3;
            self.escape_value = 0;
            Ok(())
        } else {
            self.save(byte)
        }
    }

    fn save(&mut self, byte: u8) -> Result<(), i64> {
        if byte == 0 {
            return Err(EIO);
        }
        if self.field == 4 {
            if self.point_len == 0 {
                self.absolute = byte == b'/';
            }
            if self.point_len >= self.point.len() - 1 {
                return Err(ENAMETOOLONG);
            }
            self.point[self.point_len].write(byte);
            self.point_len += 1;
        } else {
            let (start, capacity) = if self.tail_fields == 0 {
                (TYPE, 16)
            } else {
                (SOURCE, 1024)
            };
            if self.saved_len < capacity - 1 {
                self.output[start + self.saved_len] = byte;
                self.saved_len += 1;
            }
        }
        Ok(())
    }

    fn end_token(&mut self) -> Result<(), i64> {
        if self.token_len == 0 {
            return Ok(());
        }
        if self.field == 0 {
            self.selected = self.number_valid && self.number == self.id;
            if self.selected {
                self.output[TYPE..TYPE + 16].fill(0);
                self.output[SOURCE..SOURCE + 1024].fill(0);
            }
        } else if self.selected {
            if self.escape_digits != 0 {
                return Err(EIO);
            }
            if self.field == 4 {
                if !self.absolute || self.point_len == 0 {
                    return Err(EIO);
                }
                self.point
                    .get_mut(self.point_len)
                    .ok_or(ENAMETOOLONG)?
                    .write(0);
            }
            if self.separator {
                self.tail_fields = self.tail_fields.saturating_add(1);
            } else if self.field >= 6 && self.token_dash && self.token_len == 1 {
                self.separator = true;
            }
        }
        self.field = self.field.saturating_add(1);
        self.token_len = 0;
        Ok(())
    }

    fn end_line(&mut self) -> Result<(), i64> {
        self.end_token()?;
        if self.selected {
            if !self.separator || self.tail_fields < 3 || self.point_len == 0 {
                return Err(EIO);
            }
            self.done = true;
        } else {
            self.field = 0;
            self.number = 0;
            self.number_valid = true;
        }
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> Result<bool, i64> {
        if !self.done {
            self.end_line()?;
        }
        Ok(self.done)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn word(bytes: &[u8], offset: usize) -> u64 {
        u64::from_ne_bytes(bytes[offset..offset + 8].try_into().unwrap())
    }

    #[test]
    fn conversion_has_exact_layout_and_clears_all_unspecified_bytes() {
        let input = [
            0x1_ffff_ef53,
            0x1_8000_1000,
            0x1234_5678_9abc_def0,
            2,
            3,
            4,
            5,
            0xfedc_ba98_7654_3210,
            255,
            512,
            u64::MAX,
            11,
            12,
            13,
            14,
        ];
        let mut output = [0xa5; 2168];
        convert(&input, &mut output);
        assert_eq!(&output[..4], &512_u32.to_ne_bytes());
        assert_eq!(&output[4..8], &0x8000_1000_u32.to_ne_bytes());
        for (index, value) in input[2..8].iter().enumerate() {
            assert_eq!(word(&output, 8 + index * 8), *value);
        }
        assert_eq!(&output[56..60], &[0; 4]);
        assert_eq!(&output[60..64], &0xffff_ef53_u32.to_ne_bytes());
        assert_eq!(&output[64..68], &0x1000_001f_u32.to_ne_bytes());
        assert!(output[68..].iter().all(|byte| *byte == 0));
    }

    #[test]
    fn fragment_units_preserve_capacity_and_optimal_io_size_independently() {
        let mut input = [0; 15];
        input[1] = 65536;
        input[9] = 1024;
        input[2] = 9000;
        input[3] = 8000;
        input[4] = 7000;
        let mut output = [0; 2168];
        convert(&input, &mut output);
        assert_eq!(&output[..4], &1024_u32.to_ne_bytes());
        assert_eq!(&output[4..8], &65536_i32.to_ne_bytes());
        for (offset, count) in [(8, 9000), (16, 8000), (24, 7000)] {
            assert_eq!(word(&output, offset), count);
            assert_eq!(word(&output, offset) * 1024, count * input[9]);
        }
        input[9] = 0;
        convert(&input, &mut output);
        assert_eq!(&output[..4], &65536_u32.to_ne_bytes());
        assert_eq!(&output[4..8], &65536_i32.to_ne_bytes());
        input[9] = 0x1_0000_0200;
        convert(&input, &mut output);
        assert_eq!(&output[..4], &512_u32.to_ne_bytes());
    }

    #[test]
    fn flags_translate_individually_and_drop_every_unmapped_bit() {
        for (linux, darwin) in [
            (1, 1),
            (2, 8),
            (4, 16),
            (8, 4),
            (16, 2),
            (1024, 0x1000_0000),
        ] {
            let mut input = [0; 15];
            let mut output = [0; 2168];
            input[10] = linux;
            convert(&input, &mut output);
            assert_eq!(&output[64..68], &(darwin as u32).to_ne_bytes());
        }
        let mut input = [0; 15];
        let mut output = [0; 2168];
        input[10] = !(1 | 2 | 4 | 8 | 16 | 1024);
        convert(&input, &mut output);
        assert_eq!(&output[64..68], &[0; 4]);
    }

    #[test]
    fn fdinfo_accepts_every_chunk_boundary_and_skips_long_other_lines() {
        let text = b"pos:\t0\nflags:\t0100000\nmnt_id:\t18446744073709551615 \t\nino:\t7\n";
        for width in 1..=text.len() {
            let mut parser = MountIdParser::new();
            for chunk in text.chunks(width) {
                parser.feed(chunk).unwrap();
            }
            assert_eq!(parser.finish(), Ok(Some(u64::MAX)));
        }
        let mut parser = MountIdParser::new();
        parser.feed(b"unknown:").unwrap();
        for _ in 0..10_000 {
            parser.feed(b"mnt_id: malformed-but-unrelated").unwrap();
        }
        assert_eq!(parser.feed(b"\nmnt_id: 45"), Ok(None));
        assert_eq!(parser.finish(), Ok(Some(45)));
    }

    #[test]
    fn fdinfo_rejects_bad_values_and_does_not_match_substrings() {
        for text in [
            &b"mnt_id:\n"[..],
            b"mnt_id: -1\n",
            b"mnt_id: 18446744073709551616\n",
            b"mnt_id: 4x\n",
            b"mnt_id: 4 5\n",
        ] {
            assert_eq!(MountIdParser::new().feed(text), Err(EIO));
        }
        let mut parser = MountIdParser::new();
        assert_eq!(parser.feed(b"not_mnt_id: 4\nxmnt_id: 5\n"), Ok(None));
        assert_eq!(parser.finish(), Ok(None));
        assert_eq!(MountIdParser::new().feed(b"mnt_id: 0\n"), Ok(Some(0)));
    }

    #[test]
    fn mount_identity_avoids_prefix_and_bind_mount_ambiguities() {
        let records = b"8 1 0:1 / /dir rw - wrong wrong rw\n9 1 0:1 / /directory rw - wrong wrong rw\n10 1 0:1 /bind /same rw - selected right rw\n11 1 0:1 /other /same rw - wrong wrong rw\n";
        for width in 1..=records.len() {
            let mut point = [MaybeUninit::uninit(); 4096];
            let mut output = [0; 2168];
            let mut parser = MountParser::new(10, &mut point, &mut output);
            for chunk in records.chunks(width) {
                parser.feed(chunk).unwrap();
            }
            assert_eq!(parser.finish(), Ok(true));
            assert_eq!(parser.point_len(), 5);
            assert_eq!(&output[TYPE..TYPE + 9], b"selected\0");
            assert_eq!(&output[SOURCE..SOURCE + 6], b"right\0");
        }
    }

    #[test]
    fn mount_escapes_decode_across_boundaries_without_utf8_assumptions() {
        let record = b"42 1 0:2 / /space\\040tab\\011line\\012slash\\134\xff rw shared:4 master:2 - test\\134fs src\\040\\011\\012\\134\xfe rw\n";
        for width in 1..=record.len() {
            let mut point = [MaybeUninit::uninit(); 4096];
            let mut output = [0xa5; 2168];
            let mut parser = MountParser::new(42, &mut point, &mut output);
            for chunk in record.chunks(width) {
                parser.feed(chunk).unwrap();
            }
            assert_eq!(parser.finish(), Ok(true));
            assert_eq!(parser.point_len(), b"/space tab\tline\nslash\\\xff".len());
            assert_eq!(&output[TYPE..TYPE + 8], b"test\\fs\0");
            assert_eq!(&output[SOURCE..SOURCE + 9], b"src \t\n\\\xfe\0");
        }
    }

    #[test]
    fn arbitrarily_long_optional_fields_and_names_are_streamed_and_truncated() {
        let mut point = [MaybeUninit::uninit(); 4096];
        let mut output = [0xa5; 2168];
        let mut parser = MountParser::new(42, &mut point, &mut output);
        parser.feed(b"42 1 0:2 / / rw optional:").unwrap();
        for _ in 0..10_000 {
            parser.feed(b"0123456789").unwrap();
        }
        parser.feed(b" - ").unwrap();
        for _ in 0..100 {
            parser.feed(b"filesystem").unwrap();
        }
        parser.feed(b" ").unwrap();
        for _ in 0..10_000 {
            parser.feed(b"source").unwrap();
        }
        assert_eq!(parser.feed(b" rw\n"), Ok(true));
        assert_eq!(parser.point_len(), 1);
        assert_eq!(&output[TYPE..TYPE + 16], b"filesystemfiles\0");
        assert_eq!(output[SOURCE + 1023], 0);
        assert!(output[SOURCE..SOURCE + 1023].iter().all(|byte| *byte != 0));
    }

    #[test]
    fn malformed_selected_records_fail_without_accepting_partial_metadata() {
        for text in [
            &b"42\n"[..],
            b"42 1 0:2 / relative rw - fs source rw\n",
            b"42 1 0:2 / / rw fs source rw\n",
            b"42 1 0:2 / / rw - fs source\n",
            b"42 1 0:2 / /bad\\04 rw - fs source rw\n",
            b"42 1 0:2 / /bad\\999 rw - fs source rw\n",
            b"42 1 0:2 / /bad\\400 rw - fs source rw\n",
            b"42 1 0:2 / /bad\\000 rw - fs source rw\n",
            b"42 1 0:2 / / rw - fs src\\00\n",
        ] {
            let mut point = [MaybeUninit::uninit(); 4096];
            let mut output = [0; 2168];
            assert_eq!(
                MountParser::new(42, &mut point, &mut output).feed(text),
                Err(EIO)
            );
        }
        let mut point = [MaybeUninit::uninit(); 4096];
        let mut output = [0; 2168];
        let mut parser = MountParser::new(42, &mut point, &mut output);
        assert_eq!(
            parser.feed(b"43 malformed\\999\n99999999999999999999999999999 bad\n"),
            Ok(false)
        );
        assert_eq!(parser.finish(), Ok(false));
    }

    fn statmount_record(bytes: &mut [u8], point: &[u8], kind: &[u8], source: &[u8]) -> usize {
        bytes.fill(0);
        bytes[8..16].copy_from_slice(&0x230_u64.to_ne_bytes());
        let mut cursor = 513;
        for (field, value) in [(108, point), (36, kind), (124, source)] {
            bytes[field..field + 4].copy_from_slice(&((cursor - 512) as u32).to_ne_bytes());
            bytes[cursor..cursor + value.len()].copy_from_slice(value);
            cursor += value.len() + 1;
        }
        bytes[..4].copy_from_slice(&(cursor as u32).to_ne_bytes());
        cursor
    }

    #[test]
    fn statmount_validates_offsets_and_preserves_already_unescaped_non_utf8() {
        let mut bytes = [0; 4096];
        let point = b"/space tab\tline\nslash\\040\xff";
        let source = b"source \t\n\\040\xfe";
        let size = statmount_record(&mut bytes, point, b"ext4", source);
        let mut output = [0xa5; 2168];
        assert_eq!(
            convert_statmount(&mut bytes[..size], &mut output),
            Ok(point.len())
        );
        assert_eq!(&bytes[..point.len()], point);
        assert_eq!(bytes[point.len()], 0);
        assert_eq!(&output[TYPE..TYPE + 5], b"ext4\0");
        assert_eq!(&output[SOURCE..SOURCE + source.len()], source);
        assert_eq!(output[SOURCE + source.len()], 0);
        assert!(output[..TYPE].iter().all(|byte| *byte == 0xa5));
        assert!(output[88..SOURCE].iter().all(|byte| *byte == 0xa5));
        assert!(output[2136..].iter().all(|byte| *byte == 0xa5));
    }

    #[test]
    fn statmount_subtypes_match_procfs_names_and_optional_absence() {
        for (base, subtype, expected) in [
            (&b"fuse"[..], &b"sshfs"[..], &b"fuse.sshfs"[..]),
            (b"12345678901234", b"long", b"12345678901234."),
            (b"123456789012345", b"long", b"123456789012345"),
            (b"fuse", b"", b"fuse"),
        ] {
            let mut bytes = [0; 4096];
            let size = statmount_record(&mut bytes, b"/mount", base, b"source");
            bytes[size..size + subtype.len()].copy_from_slice(subtype);
            bytes[120..124].copy_from_slice(&((size - 512) as u32).to_ne_bytes());
            bytes[8..16].copy_from_slice(&0x330_u64.to_ne_bytes());
            let size = size + subtype.len() + 1;
            bytes[..4].copy_from_slice(&(size as u32).to_ne_bytes());
            let mut output = [0xa5; 2168];
            assert_eq!(convert_statmount(&mut bytes[..size], &mut output), Ok(6));
            assert_eq!(&output[TYPE..TYPE + expected.len()], expected);
            assert_eq!(output[TYPE + expected.len()], 0);
        }
        let mut bytes = [0; 4096];
        let size = statmount_record(&mut bytes, b"/mount", b"ext4", b"source");
        bytes[120..124].copy_from_slice(&u32::MAX.to_ne_bytes());
        let mut output = [0xa5; 2168];
        assert_eq!(convert_statmount(&mut bytes[..size], &mut output), Ok(6));
        assert_eq!(&output[TYPE..TYPE + 5], b"ext4\0");
    }

    #[test]
    fn statmount_rejects_malformed_metadata_without_mutation() {
        let mut original = [0; 4096];
        let size = statmount_record(&mut original, b"/mount", b"ext4", b"source");
        for case in 0..10 {
            let mut bytes = original;
            match case {
                0 => bytes[..4].copy_from_slice(&511_u32.to_ne_bytes()),
                1 => bytes[..4].copy_from_slice(&4097_u32.to_ne_bytes()),
                2 => bytes[8..16].copy_from_slice(&0x220_u64.to_ne_bytes()),
                3 => bytes[108..112].copy_from_slice(&u32::MAX.to_ne_bytes()),
                4 => bytes[36..40].copy_from_slice(&((size - 512) as u32).to_ne_bytes()),
                5 => bytes[124..128].copy_from_slice(&((size - 513) as u32).to_ne_bytes()),
                6 => bytes[size - 1] = b'x',
                7 => bytes[513] = b'x',
                8 => bytes[513] = 0,
                9 => bytes[36..40].copy_from_slice(&0_u32.to_ne_bytes()),
                _ => unreachable!(),
            }
            let before = bytes;
            let mut output = [0xa5; 2168];
            assert_eq!(
                convert_statmount(&mut bytes, &mut output),
                Err(()),
                "case {case}"
            );
            assert_eq!(bytes, before);
            assert!(output.iter().all(|byte| *byte == 0xa5));
        }
        for length in [0, 1, 3, 4, 8, 15, 511] {
            let mut bytes = original;
            let mut output = [0xa5; 2168];
            assert_eq!(
                convert_statmount(&mut bytes[..length], &mut output),
                Err(())
            );
        }
    }

    #[test]
    fn statmount_bounds_long_point_and_truncates_names_without_allocating() {
        let mut bytes = [0; 8192];
        let mut point = [b'x'; 4096];
        point[0] = b'/';
        let kind = [b'k'; 100];
        let source = [b's'; 2000];
        let size = statmount_record(&mut bytes, &point[..4095], &kind, &source);
        let mut output = [0xa5; 2168];
        assert_eq!(convert_statmount(&mut bytes[..size], &mut output), Ok(4095));
        assert_eq!(&bytes[..4095], &point[..4095]);
        assert_eq!(bytes[4095], 0);
        assert_eq!(&output[TYPE..TYPE + 16], b"kkkkkkkkkkkkkkk\0");
        assert!(
            output[SOURCE..SOURCE + 1023]
                .iter()
                .all(|byte| *byte == b's')
        );
        assert_eq!(output[SOURCE + 1023], 0);
        let size = statmount_record(&mut bytes, &point, b"ext4", b"source");
        assert_eq!(convert_statmount(&mut bytes[..size], &mut output), Err(()));
    }

    #[test]
    fn point_capacity_reserves_nul_and_eof_can_finish_complete_record() {
        let mut point = [MaybeUninit::uninit(); 4096];
        let mut output = [0; 2168];
        let mut parser = MountParser::new(42, &mut point, &mut output);
        parser.feed(b"42 1 0:2 / /").unwrap();
        for _ in 0..4094 {
            parser.feed(b"x").unwrap();
        }
        assert_eq!(parser.point_len(), 4095);
        parser.feed(b" rw - fs source rw").unwrap();
        assert_eq!(parser.finish(), Ok(true));
        let mut parser = MountParser::new(42, &mut point, &mut output);
        parser.feed(b"42 1 0:2 / /").unwrap();
        for _ in 0..4094 {
            parser.feed(b"x").unwrap();
        }
        assert_eq!(parser.feed(b"x"), Err(ENAMETOOLONG));
    }
}
