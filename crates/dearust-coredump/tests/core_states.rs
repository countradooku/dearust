// SPDX-License-Identifier: GPL-3.0-or-later
//! Independent ELF-note/Mach-O-command fixtures for per-thread register state.
#![forbid(unsafe_code)]
use dearust_coredump::{Options, convert};

fn put(data: &mut [u8], offset: usize, value: u64, width: usize) {
    data[offset..offset + width].copy_from_slice(&value.to_le_bytes()[..width]);
}
fn integer(data: &[u8], offset: usize, width: usize) -> u64 {
    let mut result = [0; 8];
    result[..width].copy_from_slice(&data[offset..offset + width]);
    u64::from_le_bytes(result)
}
fn note(notes: &mut Vec<u8>, owner: &[u8], kind: u32, payload: &[u8]) {
    notes.extend_from_slice(&(owner.len() as u32).to_le_bytes());
    notes.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    notes.extend_from_slice(&kind.to_le_bytes());
    notes.extend_from_slice(owner);
    while notes.len() % 4 != 0 {
        notes.push(0);
    }
    notes.extend_from_slice(payload);
    while notes.len() % 4 != 0 {
        notes.push(0);
    }
}
fn status(wide: bool, tag: u64) -> Vec<u8> {
    let mut bytes = vec![0; if wide { 336 } else { 144 }];
    let (start, width, count) = if wide { (112, 8, 27) } else { (72, 4, 17) };
    put(&mut bytes, if wide { 32 } else { 24 }, tag, 4);
    for index in 0..count {
        put(&mut bytes, start + index * width, tag + index as u64, width);
    }
    bytes
}
fn elf(wide: bool, mut notes: Vec<u8>) -> Vec<u8> {
    let width = if wide { 8 } else { 4 };
    let mut files = vec![0; width * 2];
    put(&mut files, width, 4096, width);
    note(&mut notes, b"CORE\0", 0x4649_4c45, &files);
    let header = if wide { 64 } else { 52 };
    let ph = if wide { 56 } else { 32 };
    let mut bytes = vec![0; header + ph];
    bytes[..7].copy_from_slice(if wide {
        b"\x7fELF\x02\x01\x01"
    } else {
        b"\x7fELF\x01\x01\x01"
    });
    put(&mut bytes, 16, 4, 2);
    put(&mut bytes, 18, if wide { 62 } else { 3 }, 2);
    put(&mut bytes, 20, 1, 4);
    put(&mut bytes, if wide { 32 } else { 28 }, header as u64, width);
    put(&mut bytes, if wide { 52 } else { 40 }, header as u64, 2);
    put(&mut bytes, if wide { 54 } else { 42 }, ph as u64, 2);
    put(&mut bytes, if wide { 56 } else { 44 }, 1, 2);
    put(&mut bytes, header, 4, 4);
    put(
        &mut bytes,
        header + if wide { 8 } else { 4 },
        (header + ph) as u64,
        width,
    );
    put(
        &mut bytes,
        header + if wide { 32 } else { 16 },
        notes.len() as u64,
        width,
    );
    put(
        &mut bytes,
        header + if wide { 40 } else { 20 },
        notes.len() as u64,
        width,
    );
    bytes.extend(notes);
    bytes
}
fn converted(wide: bool, notes: Vec<u8>) -> Vec<u8> {
    let mut output = Vec::new();
    convert(&elf(wide, notes), &mut output, &Options::default()).unwrap();
    output
}
#[derive(Debug)]
struct Flavor {
    kind: u32,
    count: u32,
    state: Vec<u8>,
}
fn threads(bytes: &[u8]) -> Vec<Vec<Flavor>> {
    let mut position = if integer(bytes, 0, 4) == 0xfeed_facf {
        32
    } else {
        28
    };
    let mut result = Vec::new();
    for _ in 0..integer(bytes, 16, 4) {
        let command = integer(bytes, position, 4);
        let size = integer(bytes, position + 4, 4) as usize;
        assert!(size >= 8 && position + size <= bytes.len());
        if command == 4 {
            let mut flavors = Vec::new();
            let mut cursor = position + 8;
            while cursor < position + size {
                let kind = integer(bytes, cursor, 4) as u32;
                let count = integer(bytes, cursor + 4, 4) as u32;
                let length = count as usize * 4;
                assert!(cursor + 8 + length <= position + size);
                flavors.push(Flavor {
                    kind,
                    count,
                    state: bytes[cursor + 8..cursor + 8 + length].to_vec(),
                });
                cursor += 8 + length;
            }
            assert_eq!(cursor, position + size);
            result.push(flavors);
        }
        position += size;
    }
    result
}
fn fxsave(tag: u8) -> Vec<u8> {
    let mut bytes = vec![0; 512];
    put(&mut bytes, 0, 0x077f, 2);
    put(&mut bytes, 2, 0x3800, 2);
    put(&mut bytes, 4, 0x80, 1);
    put(&mut bytes, 6, 0x345, 2);
    put(&mut bytes, 8, 0x8765_4321_1234_5678, 8);
    put(&mut bytes, 16, 0xfedc_ba98_7654_3210, 8);
    put(&mut bytes, 24, 0x3f80, 4);
    put(&mut bytes, 28, 0xffff, 4);
    for index in 0..8 {
        for byte in 0..10 {
            bytes[32 + index * 16 + byte] = tag.wrapping_add((index * 10 + byte) as u8);
        }
    }
    for index in 0..16 {
        for byte in 0..16 {
            bytes[160 + index * 16 + byte] = tag.wrapping_add((index * 16 + byte) as u8);
        }
    }
    bytes[416..].fill(0xa5);
    bytes
}
#[test]
fn absent_fp_notes_do_not_invent_zero_registers() {
    for wide in [false, true] {
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(wide, 0x1100));
        let output = converted(wide, notes);
        let states = threads(&output);
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].len(), 1);
        assert_eq!(states[0][0].kind, if wide { 4 } else { 1 });
        assert_eq!(states[0][0].count, if wide { 42 } else { 16 });
    }
}
#[test]
fn x64_fpregset_preserves_defined_fxsave_fields_and_clears_reserved_bytes() {
    let source = fxsave(0x31);
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x2200));
    note(&mut notes, b"CORE\0", 2, &source);
    let output = converted(true, notes);
    let states = threads(&output);
    assert_eq!(states[0].len(), 2);
    let floating = &states[0][1];
    assert_eq!((floating.kind, floating.count), (5, 131));
    assert_eq!(floating.state.len(), 524);
    assert_eq!(&floating.state[..8], &[0; 8]);
    assert_eq!(&floating.state[8..424], &source[..416]);
    assert!(floating.state[424..].iter().all(|byte| *byte == 0));
}
#[test]
fn distinct_thread_fp_images_remain_associated_with_their_prstatus() {
    let mut notes = Vec::new();
    for (tag, marker) in [(0x3300, 0x51), (0x4400, 0xa2)] {
        note(&mut notes, b"CORE\0", 1, &status(true, tag));
        note(&mut notes, b"CORE\0", 3, &[0; 32]); // process/global note cannot change ownership
        note(&mut notes, b"CORE\0", 2, &fxsave(marker));
    }
    let states = threads(&converted(true, notes));
    assert_eq!(states.len(), 2);
    for (index, (tag, marker)) in [(0x3300, 0x51), (0x4400, 0xa2)].into_iter().enumerate() {
        assert_eq!(integer(&states[index][0].state, 0, 8), tag + 10);
        assert_eq!(states[index][1].state[168], marker);
    }
}
#[test]
fn missing_fp_in_one_thread_does_not_reuse_its_neighbor() {
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x5500));
    note(&mut notes, b"CORE\0", 2, &fxsave(0x66));
    note(&mut notes, b"CORE\0", 1, &status(true, 0x7700));
    let states = threads(&converted(true, notes));
    assert_eq!(states[0].len(), 2);
    assert_eq!(states[1].len(), 1);
}

fn archived(bytes: &[u8]) -> Vec<(Vec<u8>, Vec<u8>)> {
    let mut at = if integer(bytes, 0, 4) == 0xfeed_facf {
        32
    } else {
        28
    };
    let mut records = Vec::new();
    for _ in 0..integer(bytes, 16, 4) {
        let size = integer(bytes, at + 4, 4) as usize;
        if integer(bytes, at, 4) == 0x31 {
            assert_eq!(size, 40);
            let owner = bytes[at + 8..at + 24]
                .split(|b| *b == 0)
                .next()
                .unwrap()
                .to_vec();
            let offset = integer(bytes, at + 24, 8) as usize;
            let length = integer(bytes, at + 32, 8) as usize;
            records.push((owner, bytes[offset..offset + length].to_vec()));
        }
        at += size;
    }
    records
}
fn assert_rejected(wide: bool, notes: Vec<u8>) {
    let mut destination = vec![0x79; 19];
    let before = destination.clone();
    assert!(convert(&elf(wide, notes), &mut destination, &Options::default()).is_err());
    assert_eq!(destination, before);
}
fn xstate(marker: u8, enabled: u64, active: u64, length: usize) -> Vec<u8> {
    let mut payload = fxsave(marker);
    payload.resize(length, 0);
    put(&mut payload, 464, enabled, 8);
    put(&mut payload, 512, active, 8);
    payload
}
#[test]
fn raw_archive_has_exact_tid_class_kind_lengths_and_all_input_bytes() {
    for wide in [false, true] {
        let source = if wide {
            fxsave(0x82)
        } else {
            (0..108).map(|i| i as u8).collect()
        };
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(wide, 0x123456));
        note(&mut notes, b"CORE\0", 2, &source);
        let output = converted(wide, notes);
        let raw = archived(&output);
        assert_eq!(raw.len(), 1);
        assert_eq!(raw[0].0, b"DEARUST.XSTATE");
        let record = &raw[0].1;
        assert_eq!(integer(record, 0, 4), 1);
        assert_eq!(integer(record, 4, 4), if wide { 2 } else { 1 });
        assert_eq!(integer(record, 8, 8), 0x123456);
        assert_eq!(integer(record, 16, 4), 2);
        assert_eq!(integer(record, 20, 4), 0);
        assert_eq!(integer(record, 24, 8), source.len() as u64);
        assert_eq!(&record[32..], source);
        if !wide {
            assert_eq!(threads(&output)[0].len(), 1);
        }
    }
}
#[test]
fn raw_archives_follow_mapped_bytes_and_zero_tail_at_exact_offsets() {
    for wide in [false, true] {
        let legacy = fxsave(0x71);
        let extended = xstate(0x92, 3, 3, 576);
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(wide, 0x1942));
        note(
            &mut notes,
            if wide { b"CORE\0" } else { b"LINUX\0" },
            if wide { 2 } else { 0x46e6_2b7f },
            &legacy,
        );
        note(&mut notes, b"LINUX\0", 0x202, &extended);
        let mut input = elf(wide, notes);
        let (header, ph, width) = if wide { (64, 56, 8) } else { (52, 32, 4) };
        input.splice(header + ph..header + ph, vec![0; ph]);
        put(&mut input, if wide { 56 } else { 44 }, 2, 2);
        put(
            &mut input,
            header + if wide { 8 } else { 4 },
            (header + 2 * ph) as u64,
            width,
        );
        let payload = input.len();
        let at = header + ph;
        put(&mut input, at, 1, 4); // PT_LOAD
        if wide {
            put(&mut input, at + 4, 6, 4);
            for (offset, value) in [(8, payload as u64), (16, 0x5000), (32, 5), (40, 13)] {
                put(&mut input, at + offset, value, 8);
            }
        } else {
            for (offset, value) in [(4, payload as u64), (8, 0x5000), (16, 5), (20, 13), (24, 6)] {
                put(&mut input, at + offset, value, 4);
            }
        }
        input.extend_from_slice(b"IMAGE");
        let mut output = Vec::new();
        convert(&input, &mut output, &Options::default()).unwrap();
        let header = if wide { 32 } else { 28 };
        let commands_end = header + integer(&output, 20, 4) as usize;
        assert_eq!(integer(&output, header, 4), if wide { 0x19 } else { 1 });
        assert_eq!(integer(&output, header + 24, width), 0x5000);
        assert_eq!(integer(&output, header + 24 + width, width), 13);
        assert_eq!(
            integer(&output, header + 24 + 2 * width, width) as usize,
            commands_end
        );
        assert_eq!(integer(&output, header + 24 + 3 * width, width), 13);
        assert_eq!(
            &output[commands_end..commands_end + 13],
            b"IMAGE\0\0\0\0\0\0\0\0"
        );
        let mut command = header;
        let mut expected_offset = commands_end + 13;
        let mut raw_count = 0;
        for _ in 0..integer(&output, 16, 4) {
            if integer(&output, command, 4) == 0x31 {
                assert_eq!(integer(&output, command + 24, 8) as usize, expected_offset);
                let length = integer(&output, command + 32, 8) as usize;
                let source = if raw_count == 0 { &legacy } else { &extended };
                assert_eq!(length, 32 + source.len());
                assert_eq!(
                    &output[expected_offset + 32..expected_offset + length],
                    source
                );
                expected_offset += length;
                raw_count += 1;
            }
            command += integer(&output, command + 4, 4) as usize;
        }
        assert_eq!(raw_count, 2);
        assert_eq!(expected_offset, output.len());
        assert_eq!(threads(&output)[0][1].state[168], 0x92);
    }
}
#[test]
fn i386_fpx_preserves_eight_sse_registers_and_canonical_reserved_fields() {
    let source = fxsave(0x26);
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(false, 0x1122));
    note(&mut notes, b"LINUX\0", 0x46e6_2b7f, &source);
    let output = converted(false, notes);
    let list = threads(&output);
    let state = &list[0][1];
    assert_eq!((state.kind, state.count), (2, 131));
    assert_eq!(&state.state[168..296], &source[160..288]);
    assert!(state.state[296..].iter().all(|b| *b == 0));
    assert_eq!(&state.state[16..22], &source[8..14]);
    assert_eq!(&state.state[22..24], &[0; 2]);
    assert_eq!(&state.state[24..30], &source[16..22]);
    assert_eq!(&state.state[30..32], &[0; 2]);
    for index in 0..8 {
        assert_eq!(
            &state.state[40 + index * 16..50 + index * 16],
            &source[32 + index * 16..42 + index * 16]
        );
        assert_eq!(&state.state[50 + index * 16..56 + index * 16], &[0; 6]);
    }
    assert_eq!(integer(&archived(&output)[0].1, 16, 4), 0x46e6_2b7f);
}
#[test]
fn xstate_is_authoritative_while_all_sources_remain_lossless() {
    for wide in [false, true] {
        let old = if wide { fxsave(0x17) } else { vec![0x17; 108] };
        let extended = xstate(0x92, 3, 3, 576);
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(wide, 0x4455));
        note(&mut notes, b"CORE\0", 2, &old);
        note(&mut notes, b"LINUX\0", 0x202, &extended);
        let output = converted(wide, notes);
        let list = threads(&output);
        assert_eq!(list[0][1].state[168], 0x92);
        let raw = archived(&output);
        assert_eq!(raw.len(), 2);
        assert_eq!(&raw[0].1[32..], old);
        assert_eq!(&raw[1].1[32..], extended);
    }
}
#[test]
fn inactive_xstate_uses_architectural_init_instead_of_stale_bytes() {
    let source = xstate(0xee, 3, 0, 576);
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x5566));
    note(&mut notes, b"LINUX\0", 0x202, &source);
    let list = threads(&converted(true, notes));
    let state = &list[0][1].state;
    assert_eq!(integer(state, 8, 2), 0x037f);
    assert_eq!(integer(state, 32, 4), 0x1f80);
    assert!(state[40..].iter().all(|b| *b == 0));
    assert_eq!(integer(state, 36, 4), 0xffff);
}
#[test]
fn duplicate_fp_formats_or_orphan_fp_notes_are_rejected_before_output() {
    for wide in [false, true] {
        for (owner, kind, payload) in [
            (
                b"CORE\0".as_slice(),
                2,
                if wide { fxsave(0x44) } else { vec![0; 108] },
            ),
            (b"LINUX\0".as_slice(), 0x202, xstate(0x55, 3, 3, 576)),
        ] {
            let mut orphan = Vec::new();
            note(&mut orphan, owner, kind, &payload);
            assert_rejected(wide, orphan);
            let mut notes = Vec::new();
            note(&mut notes, b"CORE\0", 1, &status(wide, 0x11));
            note(&mut notes, owner, kind, &payload);
            note(&mut notes, owner, kind, &payload);
            assert_rejected(wide, notes);
        }
    }
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(false, 0x99));
    note(&mut notes, b"LINUX\0", 0x46e6_2b7f, &fxsave(0x88));
    note(&mut notes, b"LINUX\0", 0x46e6_2b7f, &fxsave(0x88));
    assert_rejected(false, notes);
}
#[test]
fn malformed_provided_formats_cannot_hide_behind_a_valid_authoritative_note() {
    for wide in [false, true] {
        for size in [
            0,
            if wide { 108 } else { 512 },
            if wide { 511 } else { 107 },
            if wide { 513 } else { 109 },
        ] {
            let mut notes = Vec::new();
            note(&mut notes, b"CORE\0", 1, &status(wide, 0x10));
            note(&mut notes, b"CORE\0", 2, &vec![0; size]);
            note(&mut notes, b"LINUX\0", 0x202, &xstate(0x20, 3, 3, 576));
            assert_rejected(wide, notes);
        }
    }
    for size in [0, 511, 513] {
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(false, 0x10));
        note(&mut notes, b"LINUX\0", 0x46e6_2b7f, &vec![0; size]);
        assert_rejected(false, notes);
    }
}
#[test]
fn unknown_owner_notes_do_not_forge_threads_or_floating_state() {
    let mut notes = Vec::new();
    note(&mut notes, b"VENDOR\0", 1, &status(true, 0x9999));
    note(&mut notes, b"VENDOR\0", 2, &[0; 3]);
    note(&mut notes, b"CORE\0", 1, &status(true, 0x6789));
    note(&mut notes, b"LINUX\0", 2, &[0; 3]);
    note(&mut notes, b"CORE\0", 0x202, &[0; 3]);
    note(&mut notes, b"CORE-extra\0", 2, &[0; 3]);
    let output = converted(true, notes);
    let list = threads(&output);
    assert_eq!(list.len(), 1);
    assert_eq!(list[0].len(), 1);
    assert!(archived(&output).is_empty());
}
#[test]
fn duplicate_thread_ids_and_invalid_xsave_headers_are_rejected() {
    for wide in [false, true] {
        let mut notes = Vec::new();
        for _ in 0..2 {
            note(&mut notes, b"CORE\0", 1, &status(wide, 0x7777));
        }
        assert_rejected(wide, notes);
        for (at, value, width) in [(464, 0, 8), (512, 4, 8), (520, 1u64 << 63, 8), (528, 1, 1)] {
            let mut source = xstate(0x70, 3, 3, 576);
            put(&mut source, at, value, width);
            let mut notes = Vec::new();
            note(&mut notes, b"CORE\0", 1, &status(wide, 0x70));
            note(&mut notes, b"LINUX\0", 0x202, &source);
            assert_rejected(wide, notes);
        }
    }
}
fn multiple_note_segments(
    wide: bool,
    mut segments: Vec<Vec<u8>>,
    reverse_headers: bool,
) -> Vec<u8> {
    let width = if wide { 8 } else { 4 };
    let hs = if wide { 64 } else { 52 };
    let ps = if wide { 56 } else { 32 };
    let original = elf(wide, Vec::new());
    segments
        .last_mut()
        .unwrap()
        .extend_from_slice(&original[hs + ps..]);
    let mut bytes = original[..hs].to_vec();
    bytes.resize(hs + ps * segments.len(), 0);
    put(
        &mut bytes,
        if wide { 56 } else { 44 },
        segments.len() as u64,
        2,
    );
    for (index, segment) in segments.iter().enumerate() {
        let header = hs
            + if reverse_headers {
                (segments.len() - 1 - index) * ps
            } else {
                index * ps
            };
        let offset = bytes.len();
        put(&mut bytes, header, 4, 4);
        put(
            &mut bytes,
            header + if wide { 8 } else { 4 },
            offset as u64,
            width,
        );
        put(
            &mut bytes,
            header + if wide { 32 } else { 16 },
            segment.len() as u64,
            width,
        );
        put(
            &mut bytes,
            header + if wide { 40 } else { 20 },
            segment.len() as u64,
            width,
        );
        bytes.extend_from_slice(segment);
    }
    bytes
}
#[test]
fn note_segments_follow_file_order_and_global_layout_can_follow_threads() {
    for wide in [false, true] {
        let mut first = Vec::new();
        note(&mut first, b"CORE\0", 1, &status(wide, 0x1020));
        let mut second = Vec::new();
        let source = xstate(0x77, 7, 7, 832);
        note(&mut second, b"LINUX\0", 0x202, &source);
        let mut layout = vec![0; 16];
        put(&mut layout, 0, 2, 4);
        put(&mut layout, 4, 256, 4);
        put(&mut layout, 8, 576, 4);
        note(&mut second, b"LINUX\0", 0x205, &layout);
        let input = multiple_note_segments(wide, vec![first, second], true);
        let mut output = Vec::new();
        convert(&input, &mut output, &Options::default()).unwrap();
        let list = threads(&output);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0][1].kind, if wide { 17 } else { 16 });
        let raw = archived(&output);
        assert_eq!(raw.last().unwrap().0, b"DEARUST.XLAYOUT");
        assert_eq!(raw.last().unwrap().1, layout);
        let mut overlap = input.clone();
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let width = if wide { 8 } else { 4 };
        let first_offset = integer(&overlap, hs + ps + if wide { 8 } else { 4 }, width);
        put(
            &mut overlap,
            hs + if wide { 8 } else { 4 },
            first_offset + 4,
            width,
        );
        let mut out = Vec::new();
        assert!(convert(&overlap, &mut out, &Options::default()).is_err());
        assert!(out.is_empty());
    }
}
#[test]
fn malformed_global_layouts_and_32bit_cross_boundary_segments_are_rejected() {
    for payload in [vec![0; 1], vec![0; 16]] {
        let mut notes = Vec::new();
        note(&mut notes, b"CORE\0", 1, &status(true, 0x30));
        note(&mut notes, b"LINUX\0", 0x205, &payload);
        assert_rejected(true, notes);
    }
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x30));
    note(&mut notes, b"LINUX\0", 0x205, &[]);
    note(&mut notes, b"LINUX\0", 0x205, &[]);
    assert_rejected(true, notes);
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(false, 0x21));
    let mut input = elf(false, notes);
    input.splice(84..84, std::iter::repeat_n(0, 32));
    put(&mut input, 56, 116, 4);
    put(&mut input, 44, 2, 2);
    put(&mut input, 84, 1, 4);
    put(&mut input, 92, 0xffff_fff0, 4);
    put(&mut input, 104, 32, 4);
    put(&mut input, 108, 4, 4);
    let mut output = Vec::new();
    assert!(convert(&input, &mut output, &Options::default()).is_err());
    assert!(output.is_empty());
}
#[test]
fn truncated_fp_note_prefixes_never_emit_partial_output() {
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x80));
    note(&mut notes, b"CORE\0", 2, &fxsave(0x90));
    let input = elf(true, notes);
    for end in 0..input.len() {
        let mut output = vec![0x55; 8];
        assert!(
            convert(&input[..end], &mut output, &Options::default()).is_err(),
            "prefix{end}"
        );
        assert_eq!(output, [0x55; 8]);
    }
}
#[test]
fn malformed_fp_preserves_an_existing_destination_file() {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let root = std::env::temp_dir().join(format!(
        "dearust-fp-file-test-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir(&root).unwrap();
    let input = root.join("input");
    let destination = root.join("output");
    let mut notes = Vec::new();
    note(&mut notes, b"CORE\0", 1, &status(true, 0x40));
    note(&mut notes, b"CORE\0", 2, &[0; 511]);
    std::fs::write(&input, elf(true, notes)).unwrap();
    std::fs::write(&destination, b"preserve-destination").unwrap();
    assert!(dearust_coredump::convert_file(&input, &destination, &Options::default()).is_err());
    assert_eq!(
        std::fs::read(&destination).unwrap(),
        b"preserve-destination"
    );
    std::fs::remove_dir_all(root).unwrap();
}
