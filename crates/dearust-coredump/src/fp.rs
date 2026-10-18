// SPDX-License-Identifier: GPL-3.0-or-later
//! Borrowed Linux x86 floating-state notes and bounded Darwin state encoding.
//! Source layouts come from core metadata or proven historical Linux layouts,
//! never the converter host's CPUID. Raw notes remain available losslessly.
#![forbid(unsafe_code)]

use std::io::{self, Write};

pub(crate) const FPREGSET: u32 = 2;
pub(crate) const FPXREGSET: u32 = 0x46e6_2b7f;
pub(crate) const XSTATE: u32 = 0x202;
const HEADER: usize = 576;
const MAX_STATE: usize = 2444;
const ZMM: u64 = 0xe0;

fn bad(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn field(data: &[u8], offset: usize, length: usize) -> io::Result<&[u8]> {
    data.get(
        offset
            ..offset
                .checked_add(length)
                .ok_or_else(|| bad("FP offset overflow"))?,
    )
    .ok_or_else(|| bad("truncated floating-point state"))
}

fn u32_at(data: &[u8], offset: usize) -> io::Result<u32> {
    Ok(u32::from_le_bytes(
        field(data, offset, 4)?
            .try_into()
            .map_err(|_| bad("invalid FP field"))?,
    ))
}

fn u64_at(data: &[u8], offset: usize) -> io::Result<u64> {
    Ok(u64::from_le_bytes(
        field(data, offset, 8)?
            .try_into()
            .map_err(|_| bad("invalid FP field"))?,
    ))
}

fn known_size(kind: u32) -> Option<u32> {
    match kind {
        2 => Some(256),
        3 | 4 | 5 | 17 => Some(64),
        6 => Some(512),
        7 => Some(1024),
        9 => Some(8),
        18 => Some(8192),
        _ => None,
    }
}

/// Linux NT_X86_XSAVE_LAYOUT is a process-wide array of 16-byte LE records.
/// Unknown components remain opaque, but their declared ranges must be sound.
#[derive(Clone, Copy, Debug)]
pub(crate) struct ValidatedLayout<'a>(&'a [u8]);

pub(crate) fn validate_layout(data: &[u8]) -> io::Result<ValidatedLayout<'_>> {
    if data.len() % 16 != 0 || data.len() / 16 > 61 {
        return Err(bad("invalid XSAVE layout record count"));
    }
    let mut seen = 0_u64;
    for (index, item) in data.chunks_exact(16).enumerate() {
        let kind = u32_at(item, 0)?;
        let size = u32_at(item, 4)?;
        let start = u32_at(item, 8)?;
        let flags = u32_at(item, 12)?;
        if !(2..63).contains(&kind) || flags != 0 || size == 0 || start < HEADER as u32 {
            return Err(bad("unsupported XSAVE layout record"));
        }
        if known_size(kind).is_some_and(|expected| expected != size) {
            return Err(bad("incorrect XSAVE component size"));
        }
        let end = start
            .checked_add(size)
            .ok_or_else(|| bad("XSAVE component range overflow"))?;
        if seen & (1_u64 << kind) != 0 {
            return Err(bad("duplicate XSAVE component"));
        }
        seen |= 1_u64 << kind;
        for previous in data[..index * 16].chunks_exact(16) {
            let old_start = u32_at(previous, 8)?;
            let old_end = old_start
                .checked_add(u32_at(previous, 4)?)
                .ok_or_else(|| bad("XSAVE component range overflow"))?;
            if start < old_end && old_start < end {
                return Err(bad("overlapping XSAVE components"));
            }
        }
    }
    Ok(ValidatedLayout(data))
}

#[derive(Debug)]
pub(crate) struct State<'a> {
    wide: bool,
    fp: Option<&'a [u8]>,
    fpx: Option<&'a [u8]>,
    xstate: Option<&'a [u8]>,
    layout: Option<ValidatedLayout<'a>>,
}

#[derive(Default)]
struct Layout {
    avx: Option<usize>,
    mask: Option<usize>,
    high: Option<usize>,
    upper: Option<usize>,
}

impl Layout {
    fn put(&mut self, kind: u32, offset: usize) {
        match kind {
            2 => self.avx = Some(offset),
            5 => self.mask = Some(offset),
            6 => self.high = Some(offset),
            7 => self.upper = Some(offset),
            _ => {}
        }
    }
}

struct Prepared<'a> {
    bytes: &'a [u8],
    active: u64,
    enabled: u64,
    layout: Layout,
}

impl<'a> State<'a> {
    pub(crate) fn new(wide: bool) -> Self {
        Self {
            wide,
            fp: None,
            fpx: None,
            xstate: None,
            layout: None,
        }
    }

    pub(crate) fn note(&mut self, kind: u32, payload: &'a [u8]) -> io::Result<()> {
        let (slot, size) = match kind {
            FPREGSET => (&mut self.fp, if self.wide { 512 } else { 108 }),
            FPXREGSET if !self.wide => (&mut self.fpx, 512),
            XSTATE => (&mut self.xstate, 0),
            _ => return Err(bad("unsupported floating-point note kind/class")),
        };
        if slot.is_some() {
            return Err(bad("duplicate floating-point note"));
        }
        if (size != 0 && payload.len() != size)
            || (size == 0 && (payload.len() < HEADER || payload.len() % 8 != 0))
        {
            return Err(bad("incorrect floating-point note size"));
        }
        *slot = Some(payload);
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn set_layout(&mut self, payload: &'a [u8]) -> io::Result<()> {
        self.set_validated_layout(validate_layout(payload)?)
    }

    pub(crate) fn set_validated_layout(&mut self, layout: ValidatedLayout<'a>) -> io::Result<()> {
        if self.layout.is_some() {
            return Err(bad("duplicate process XSAVE layout"));
        }
        self.layout = Some(layout);
        Ok(())
    }

    pub(crate) fn raw_notes(&self) -> impl Iterator<Item = (u32, &'a [u8])> {
        [
            (FPREGSET, self.fp),
            (FPXREGSET, self.fpx),
            (XSTATE, self.xstate),
        ]
        .into_iter()
        .filter_map(|(kind, data)| data.map(|data| (kind, data)))
    }

    fn prepare(&self) -> io::Result<Option<Prepared<'a>>> {
        let Some(raw) = self.xstate else {
            // i386 FSAVE carries x87 only. A complete Darwin float flavor also
            // requires SSE/MXCSR; preserve partial FSAVE solely in its raw note.
            return Ok(self
                .fpx
                .or(if self.wide { self.fp } else { None })
                .map(|bytes| Prepared {
                    bytes,
                    active: 3,
                    enabled: 3,
                    layout: Layout::default(),
                }));
        };
        let enabled = u64_at(raw, 464)?;
        let active = u64_at(raw, 512)?;
        if u64_at(raw, 520)? != 0 {
            return Err(bad("compacted or nonstandard XSAVE input is unsupported"));
        }
        if field(raw, 528, 48)?.iter().any(|byte| *byte != 0) {
            return Err(bad("nonzero reserved XSAVE header"));
        }
        if enabled & 3 != 3 || active & !enabled != 0 || enabled >> 63 != 0 {
            return Err(bad("invalid XSAVE enabled/active feature masks"));
        }
        if enabled & 4 != 0 && enabled & 2 == 0 {
            return Err(bad("AVX requires SSE state"));
        }
        if enabled & ZMM != 0 && (enabled & ZMM != ZMM || enabled & 6 != 6) {
            return Err(bad("incomplete AVX512 enabled feature set"));
        }
        let mut layout = Layout::default();
        if let Some(records) = self.layout {
            let mut described = 3_u64;
            for item in records.0.chunks_exact(16) {
                let kind = u32_at(item, 0)?;
                let size = u32_at(item, 4)? as usize;
                let offset = u32_at(item, 8)? as usize;
                described |= 1_u64 << kind;
                if enabled & (1_u64 << kind) != 0 {
                    field(raw, offset, size)?;
                    layout.put(kind, offset);
                }
            }
            if enabled & !described != 0 {
                return Err(bad("enabled XSAVE component lacks layout metadata"));
            }
        } else if enabled & !3 == 0 {
            // Only the fixed legacy XSAVE portion is relevant.
        } else {
            // Historical GDB source-layout discrimination. MPX leaves holes on
            // Intel; AMD AVX512 has no such holes. Never guess from local CPUID.
            let historical_mask = match raw.len() {
                2696 => 0x2ff,
                11008 => 0x602ff,
                2688 => 0xff,
                2440 => 0x2e7,
                1088 => 0x1f,
                832 => 7,
                _ => 0,
            };
            if enabled & !historical_mask != 0 {
                return Err(bad("unknown XSAVE feature requires layout metadata"));
            }
            let intel = enabled & (1 << 9) != 0 && matches!(raw.len(), 2696 | 11008)
                || enabled & ZMM != 0 && raw.len() == 2688;
            let amd = enabled & (1 << 9) != 0 && raw.len() == 2440;
            if intel {
                layout.avx = Some(576);
                layout.mask = Some(1088);
                layout.high = Some(1152);
                layout.upper = Some(1664);
            } else if amd {
                layout.avx = Some(576);
                layout.mask = Some(832);
                layout.high = Some(896);
                layout.upper = Some(1408);
            } else if enabled & 4 != 0 && matches!(raw.len(), 832 | 1088) {
                layout.avx = Some(576);
            } else {
                return Err(bad("ambiguous XSAVE source layout without metadata"));
            }
        }
        for (bit, offset, length) in [
            (2, layout.avx, 256),
            (5, layout.mask, 64),
            (6, layout.high, 512),
            (7, layout.upper, 1024),
        ] {
            if enabled & (1 << bit) != 0 {
                field(
                    raw,
                    offset.ok_or_else(|| bad("missing represented XSAVE component"))?,
                    length,
                )?;
            }
        }
        Ok(Some(Prepared {
            bytes: raw,
            active,
            enabled,
            layout,
        }))
    }

    pub(crate) fn validate(&self) -> io::Result<()> {
        self.prepare().map(|_| ())
    }

    pub(crate) fn flavor_size(&self) -> Option<(u32, usize)> {
        let prepared = self.prepare().ok()??;
        Some(self.shape(prepared.enabled))
    }

    fn shape(&self, enabled: u64) -> (u32, usize) {
        if enabled & ZMM != 0 {
            if self.wide { (20, 2444) } else { (19, 1036) }
        } else if enabled & 4 != 0 {
            if self.wide { (17, 844) } else { (16, 716) }
        } else {
            (if self.wide { 5 } else { 2 }, 524)
        }
    }

    pub(crate) fn write(&self, out: &mut impl Write) -> io::Result<()> {
        let Some(prepared) = self.prepare()? else {
            return Ok(());
        };
        let (_, size) = self.shape(prepared.enabled);
        let mut encoded = [0_u8; MAX_STATE];
        let source = prepared.bytes;
        // MXCSR_MASK is capability metadata, independent of active state.
        encoded[36..40].copy_from_slice(field(source, 28, 4)?);
        if prepared.active & 1 != 0 {
            encoded[8..12].copy_from_slice(field(source, 0, 4)?);
            encoded[12] = field(source, 4, 1)?[0];
            encoded[14..16].copy_from_slice(field(source, 6, 2)?);
            if self.wide {
                // XNU copies both complete64-bit hardware pointers despite the
                // legacy cs/ds field names in its public64-bit structure.
                encoded[16..32].copy_from_slice(field(source, 8, 16)?);
            } else {
                encoded[16..22].copy_from_slice(field(source, 8, 6)?);
                encoded[24..30].copy_from_slice(field(source, 16, 6)?);
            }
            for index in 0..8 {
                encoded[40 + index * 16..50 + index * 16].copy_from_slice(field(
                    source,
                    32 + index * 16,
                    10,
                )?);
            }
        } else {
            encoded[8..10].copy_from_slice(&0x037f_u16.to_le_bytes());
        }
        if prepared.active & 6 != 0 {
            encoded[32..36].copy_from_slice(field(source, 24, 4)?);
        } else {
            encoded[32..36].copy_from_slice(&0x1f80_u32.to_le_bytes());
        }
        let registers = if self.wide { 16 } else { 8 };
        if prepared.active & 2 != 0 {
            encoded[168..168 + registers * 16].copy_from_slice(field(source, 160, registers * 16)?);
        }
        let avx = 588;
        let mask = avx + registers * 16;
        let high = mask + 64;
        for (bit, input, output, length) in [
            (2, prepared.layout.avx, avx, registers * 16),
            (5, prepared.layout.mask, mask, 64),
            (6, prepared.layout.high, high, registers * 32),
            (
                7,
                prepared.layout.upper,
                high + registers * 32,
                if self.wide { 1024 } else { 0 },
            ),
        ] {
            if prepared.active & (1 << bit) != 0 && length != 0 {
                encoded[output..output + length].copy_from_slice(field(
                    source,
                    input.ok_or_else(|| bad("missing active XSAVE component"))?,
                    length,
                )?);
            }
        }
        out.write_all(&encoded[..size])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put64(data: &mut [u8], offset: usize, value: u64) {
        data[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }
    fn xsave(size: usize, enabled: u64, active: u64) -> Vec<u8> {
        let mut data = vec![0; size];
        put64(&mut data, 464, enabled);
        put64(&mut data, 512, active);
        data
    }
    fn records(entries: &[(u32, u32, u32, u32)]) -> Vec<u8> {
        let mut result = Vec::new();
        for &(kind, size, offset, flags) in entries {
            for value in [kind, size, offset, flags] {
                result.extend_from_slice(&value.to_le_bytes());
            }
        }
        result
    }
    fn encode(state: &State<'_>) -> Vec<u8> {
        state.validate().unwrap();
        let mut output = Vec::new();
        state.write(&mut output).unwrap();
        output
    }

    #[test]
    fn missing_state_is_not_invented() {
        let state = State::new(true);
        state.validate().unwrap();
        assert_eq!(state.flavor_size(), None);
        assert_eq!(state.raw_notes().count(), 0);
        assert!(encode(&state).is_empty());
    }

    #[test]
    fn fsave_is_lossless_partial_raw_state_without_fabricated_sse() {
        let data: Vec<u8> = (0..108).collect();
        let mut state = State::new(false);
        state.note(FPREGSET, &data).unwrap();
        state.validate().unwrap();
        assert_eq!(
            state.raw_notes().collect::<Vec<_>>(),
            vec![(FPREGSET, data.as_slice())]
        );
        assert_eq!(state.flavor_size(), None);
        assert!(encode(&state).is_empty());
    }

    #[test]
    fn float64_preserves_complete_pointers_and_only_architectural_fields() {
        let mut data = [0xa5; 512];
        put64(&mut data, 8, 0xfedc_ba98_7654_3210);
        put64(&mut data, 16, 0xdead_beef_0123_4567);
        let mut state = State::new(true);
        state.note(FPREGSET, &data).unwrap();
        assert_eq!(state.flavor_size(), Some((5, 524)));
        let output = encode(&state);
        assert_eq!(&output[16..32], &data[8..24]);
        assert_eq!(&output[168..424], &data[160..416]);
        assert_eq!(&output[..8], &[0; 8]);
        assert_eq!(output[13], 0);
        for index in 0..8 {
            assert_eq!(
                &output[40 + index * 16..50 + index * 16],
                &data[32 + index * 16..42 + index * 16]
            );
            assert_eq!(&output[50 + index * 16..56 + index * 16], &[0; 6]);
        }
        assert!(output[424..].iter().all(|byte| *byte == 0));
    }

    #[test]
    fn float32_fpx_keeps_selectors_and_clears_reserved_and_upper_xmm() {
        let data = [0x9b; 512];
        let mut state = State::new(false);
        state.note(FPXREGSET, &data).unwrap();
        assert_eq!(state.flavor_size(), Some((2, 524)));
        let output = encode(&state);
        assert_eq!(&output[16..22], &data[8..14]);
        assert_eq!(&output[22..24], &[0; 2]);
        assert_eq!(&output[24..30], &data[16..22]);
        assert_eq!(&output[30..32], &[0; 2]);
        assert_eq!(&output[168..296], &data[160..288]);
        assert!(output[296..].iter().all(|byte| *byte == 0));
    }

    #[test]
    fn duplicate_class_and_exact_size_checks() {
        for (wide, kind, length) in [
            (true, FPREGSET, 512),
            (false, FPREGSET, 108),
            (false, FPXREGSET, 512),
            (true, XSTATE, 576),
        ] {
            let data = vec![0; length];
            let mut state = State::new(wide);
            state.note(kind, &data).unwrap();
            assert!(state.note(kind, &data).is_err());
            let mut state = State::new(wide);
            assert!(state.note(kind, &data[..length - 1]).is_err());
        }
        assert!(State::new(true).note(FPXREGSET, &[0; 512]).is_err());
        assert!(State::new(false).note(FPREGSET, &[0; 512]).is_err());
        assert!(State::new(true).note(17, &[]).is_err());
        for length in 577..584 {
            assert!(State::new(true).note(XSTATE, &vec![0; length]).is_err());
        }
    }

    #[test]
    fn inactive_xsave_uses_architectural_initial_state_and_preserves_mask() {
        let mut data = xsave(576, 3, 0);
        data[..464].fill(0x5a);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        let output = encode(&state);
        assert_eq!(&output[8..10], &0x037f_u16.to_le_bytes());
        assert!(output[10..32].iter().all(|byte| *byte == 0));
        assert_eq!(&output[32..36], &0x1f80_u32.to_le_bytes());
        assert_eq!(&output[36..40], &[0x5a; 4]);
        assert!(output[40..].iter().all(|byte| *byte == 0));
    }

    #[test]
    fn ymm_active_preserves_mxcsr_even_when_sse_is_initial() {
        let mut data = xsave(832, 7, 4);
        data[24..28].copy_from_slice(&0x1234_u32.to_le_bytes());
        data[160..416].fill(0xa5);
        data[576..832].fill(0x47);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        let output = encode(&state);
        assert_eq!(&output[32..36], &0x1234_u32.to_le_bytes());
        assert!(output[168..424].iter().all(|byte| *byte == 0));
        assert_eq!(&output[588..844], &[0x47; 256]);
    }

    #[test]
    fn both_avx_flavors_have_sdk_lengths() {
        let data = xsave(832, 7, 7);
        for (wide, flavor, size) in [(false, 16, 716), (true, 17, 844)] {
            let mut state = State::new(wide);
            state.note(XSTATE, &data).unwrap();
            assert_eq!(state.flavor_size(), Some((flavor, size)));
            assert_eq!(encode(&state).len(), size);
        }
    }

    #[test]
    fn intel_and_amd_avx512_layouts_map_all_represented_registers() {
        for (length, mask, high, upper) in [(2696, 1088, 1152, 1664), (2440, 832, 896, 1408)] {
            let mut data = xsave(length, 0x2e7, 0x2e7);
            data[576..832].fill(0x22);
            data[mask..mask + 64].fill(0x55);
            data[high..high + 512].fill(0x66);
            data[upper..upper + 1024].fill(0x77);
            for (wide, flavor, size, destination_mask, destination_high) in
                [(false, 19, 1036, 716, 780), (true, 20, 2444, 844, 908)]
            {
                let mut state = State::new(wide);
                state.note(XSTATE, &data).unwrap();
                assert_eq!(state.flavor_size(), Some((flavor, size)));
                let output = encode(&state);
                assert_eq!(
                    &output[588..destination_mask],
                    vec![0x22; destination_mask - 588]
                );
                assert_eq!(&output[destination_mask..destination_high], &[0x55; 64]);
                let high_length = if wide { 512 } else { 256 };
                assert_eq!(
                    &output[destination_high..destination_high + high_length],
                    vec![0x66; high_length]
                );
                if wide {
                    assert_eq!(&output[1420..2444], &[0x77; 1024]);
                }
                assert_eq!(
                    state
                        .raw_notes()
                        .find(|(kind, _)| *kind == XSTATE)
                        .map(|(_, bytes)| bytes),
                    Some(data.as_slice())
                );
            }
        }
    }

    #[test]
    fn explicit_layout_decodes_declared_offsets_without_host_cpuid() {
        let layout = records(&[
            (2, 256, 600, 0),
            (5, 64, 900, 0),
            (6, 512, 1000, 0),
            (7, 1024, 1600, 0),
        ]);
        let mut data = xsave(3000, 0xe7, 0xe7);
        data[600..856].fill(2);
        data[900..964].fill(5);
        data[1000..1512].fill(6);
        data[1600..2624].fill(7);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        state.set_layout(&layout).unwrap();
        let output = encode(&state);
        assert_eq!(&output[588..844], &[2; 256]);
        assert_eq!(&output[844..908], &[5; 64]);
        assert_eq!(&output[908..1420], &[6; 512]);
        assert_eq!(&output[1420..2444], &[7; 1024]);
    }

    #[test]
    fn layout_rejects_duplicate_overlap_flags_sizes_and_overflow() {
        for input in [
            records(&[(2, 256, 576, 1)]),
            records(&[(2, 256, 575, 0)]),
            records(&[(2, 255, 576, 0)]),
            records(&[(2, 256, u32::MAX, 0)]),
            records(&[(2, 256, 576, 0), (2, 256, 832, 0)]),
            records(&[(2, 256, 576, 0), (5, 64, 800, 0)]),
            records(&[(63, 8, 576, 0)]),
            records(&[(1, 256, 576, 0)]),
            records(&[(8, 0, 576, 0)]),
            vec![0; 15],
        ] {
            assert!(validate_layout(&input).is_err());
        }
        assert!(validate_layout(&[]).is_ok());
    }

    #[test]
    fn enabled_components_require_declared_in_bounds_storage() {
        let layout = records(&[(2, 256, 576, 0)]);
        let data = xsave(576, 7, 0);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        state.set_layout(&layout).unwrap();
        assert!(state.validate().is_err());
        let data = xsave(832, 7, 0);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        state.set_layout(&[]).unwrap();
        assert!(state.validate().is_err());
    }

    #[test]
    fn compact_reserved_bad_masks_and_ambiguous_layout_fail_before_writes() {
        let mut cases = vec![
            xsave(840, 7, 7),
            xsave(576, 0, 0),
            xsave(576, 1, 1),
            xsave(576, 1, 2),
            xsave(832, 5, 0),
            xsave(2688, 0x27, 0),
            xsave(2696, 0x2e7 | (1 << 40), 0),
            xsave(2440, 0x2e7 | (1 << 17), 0),
        ];
        let mut compact = xsave(832, 7, 7);
        put64(&mut compact, 520, 1 << 63);
        cases.push(compact);
        let mut reserved = xsave(576, 3, 0);
        reserved[575] = 1;
        cases.push(reserved);
        for data in cases {
            let mut state = State::new(true);
            state.note(XSTATE, &data).unwrap();
            let mut output = vec![1, 2, 3];
            assert!(state.validate().is_err());
            assert!(state.write(&mut output).is_err());
            assert_eq!(output, [1, 2, 3]);
        }
    }

    #[test]
    fn richer_state_supersedes_normalized_legacy_but_all_raw_notes_survive() {
        let fp = [0xab; 512];
        let extended = xsave(576, 3, 0);
        let mut state = State::new(true);
        state.note(FPREGSET, &fp).unwrap();
        state.note(XSTATE, &extended).unwrap();
        assert_eq!(
            state.raw_notes().map(|(kind, _)| kind).collect::<Vec<_>>(),
            vec![FPREGSET, XSTATE]
        );
        assert_eq!(&encode(&state)[8..10], &0x037f_u16.to_le_bytes());
    }

    #[test]
    fn unknown_components_are_preserved_raw_with_valid_explicit_layout() {
        let data = xsave(704, 3 | (1 << 40), 1 << 40);
        let layout = records(&[(40, 128, 576, 0)]);
        let mut state = State::new(true);
        state.note(XSTATE, &data).unwrap();
        state.set_layout(&layout).unwrap();
        assert_eq!(state.flavor_size(), Some((5, 524)));
        assert_eq!(encode(&state).len(), 524);
        assert_eq!(
            state
                .raw_notes()
                .find(|(kind, _)| *kind == XSTATE)
                .map(|(_, bytes)| bytes),
            Some(data.as_slice())
        );
    }
}
