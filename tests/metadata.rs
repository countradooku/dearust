mod common;
use common::*;
use dearust::{Metadata, Uuid};

const UUID: [u8; 16] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];

#[test]
fn all_thin_classes_and_byte_orders() {
    for wide in [false, true] {
        for big in [false, true] {
            let image = thin(wide, big, Some(UUID), Some(b"libexample.so.1\0"));
            let parsed = Metadata::parse(&image).unwrap();
            assert_eq!(parsed.uuids, [Uuid(UUID)]);
            assert_eq!(parsed.elf_dependency, Some(b"libexample.so.1".as_slice()));
            let name = parsed.elf_dependency.unwrap();
            assert_eq!(name.as_ptr(), image[image.len() - 16..].as_ptr());
        }
    }
    assert_eq!(
        Uuid(UUID).to_string(),
        "00010203-0405-0607-0809-0A0B0C0D0E0F"
    );
}

#[test]
fn all_fat_classes_and_byte_orders() {
    for fat64 in [false, true] {
        for big in [false, true] {
            let images = [
                thin(false, false, Some(UUID), None),
                thin(true, true, Some([255; 16]), Some(b"last.so\0")),
            ];
            let file = fat(&images, big, fat64);
            let parsed = Metadata::parse(&file).unwrap();
            assert_eq!(parsed.uuids, [Uuid(UUID), Uuid([255; 16])]);
            assert_eq!(parsed.elf_dependency, Some(b"last.so".as_slice()));
        }
    }
}

#[test]
fn no_metadata_and_first_dependency_semantics() {
    let empty = thin(true, false, None, None);
    assert_eq!(
        Metadata::parse(&empty).unwrap(),
        Metadata {
            uuids: vec![],
            elf_dependency: None
        }
    );
    let file = fat(
        &[
            thin(true, false, None, Some(b"first\0")),
            thin(false, true, Some(UUID), Some(b"second\0")),
        ],
        true,
        false,
    );
    assert_eq!(
        Metadata::parse(&file).unwrap().elf_dependency,
        Some(b"first".as_slice())
    );
}

#[test]
fn every_truncation_is_rejected() {
    let thin = thin(true, false, Some(UUID), Some(b"test.so\0"));
    let fat = fat(&[thin.clone(), thin.clone()], true, true);
    for file in [&thin, &fat] {
        for end in 0..file.len() {
            assert!(
                Metadata::parse(&file[..end]).is_err(),
                "accepted prefix of length {end}"
            );
        }
    }
}

#[test]
fn rejects_invalid_command_sizes_counts_and_trailing_data() {
    for (offset, value) in [
        (16, u32::MAX),
        (20, u32::MAX),
        (36, 0),
        (36, 7),
        (36, 25),
        (36, u32::MAX),
    ] {
        let mut image = thin(true, false, Some(UUID), None);
        put32(&mut image, offset, value, false);
        assert!(
            Metadata::parse(&image).is_err(),
            "offset {offset}, value {value}"
        );
    }
    let mut image = thin(true, false, Some(UUID), None);
    put32(&mut image, 16, 0, false);
    assert!(Metadata::parse(&image).is_err());
}

#[test]
fn elf_name_must_stay_in_its_section() {
    let mut image = thin(true, false, None, Some(b"unterminated"));
    image.push(0); // NUL outside the section must not rescue it.
    assert!(Metadata::parse(&image).is_err());
    for size in [0, u64::MAX] {
        let mut image = thin(true, false, None, Some(b"foo\0"));
        put64(&mut image, 32 + 72 + 40, size, false);
        assert!(Metadata::parse(&image).is_err());
    }
    let mut image = thin(true, false, None, Some(b"foo\0"));
    put32(&mut image, 32 + 72 + 48, u32::MAX, false);
    assert!(Metadata::parse(&image).is_err());
}

#[test]
fn validates_segments_and_fixed_width_names() {
    for wide in [false, true] {
        let header = if wide { 32 } else { 28 };
        let segment = if wide { 72 } else { 56 };
        let count = if wide { 64 } else { 48 };
        let flags = if wide { 64 } else { 56 };
        let mut image = thin(wide, false, None, Some(b"foo\0"));
        image[header + 8..header + 24].fill(b'X'); // bounded non-NUL name
        assert_eq!(Metadata::parse(&image).unwrap().elf_dependency, None);
        put32(&mut image, header + count, u32::MAX, false);
        assert!(Metadata::parse(&image).is_err());
        for section_type in [1, 0xc, 0x12] {
            let mut image = thin(wide, false, None, Some(b"foo\0"));
            put32(&mut image, header + segment + flags, section_type, false);
            assert!(Metadata::parse(&image).is_err());
        }
    }
}

#[test]
fn validates_later_commands_and_architectures_before_success() {
    let mut image = thin(true, false, Some(UUID), Some(b"foo\0"));
    put32(&mut image, 32 + 24 + 4, 0, false);
    assert!(Metadata::parse(&image).is_err());
    let invalid = vec![0; 32];
    let file = fat(
        &[thin(true, false, Some(UUID), Some(b"foo\0")), invalid],
        false,
        false,
    );
    assert!(Metadata::parse(&file).is_err());
}

#[test]
fn rejects_fat_out_of_bounds_alignment_table_overlap_and_nesting() {
    for fat64 in [false, true] {
        let file = fat(&[thin(true, false, Some(UUID), None)], true, fat64);
        for offset in [0, u32::MAX] {
            let mut bad = file.clone();
            if fat64 {
                put64(&mut bad, 16, offset.into(), true);
            } else {
                put32(&mut bad, 16, offset, true);
            }
            assert!(Metadata::parse(&bad).is_err());
        }
        let mut bad = file.clone();
        put32(&mut bad, if fat64 { 32 } else { 24 }, 64, true);
        assert!(Metadata::parse(&bad).is_err());
        let mut bad = file.clone();
        put32(&mut bad, 8, 42, true);
        assert!(Metadata::parse(&bad).is_err());
        if fat64 {
            let mut bad = file;
            put32(&mut bad, 36, 1, true);
            assert!(Metadata::parse(&bad).is_err());
        }
    }
    let nested = fat(&[thin(true, false, Some(UUID), None)], true, false);
    assert!(Metadata::parse(&fat(&[nested], true, false)).is_err());
}

#[test]
fn raw_non_utf8_and_empty_names_are_preserved() {
    for name in [b"\xff\xfe\0".as_slice(), b"\0padding".as_slice()] {
        let file = thin(false, true, None, Some(name));
        let end = name.iter().position(|&b| b == 0).unwrap();
        assert_eq!(
            Metadata::parse(&file).unwrap().elf_dependency,
            Some(&name[..end])
        );
    }
}

#[test]
fn deterministic_mutations_do_not_panic() {
    let base = fat(
        &[
            thin(true, false, Some(UUID), Some(b"example.so\0")),
            thin(false, true, Some(UUID), None),
        ],
        true,
        true,
    );
    for position in 0..base.len() {
        for byte in [0, 1, 0x7f, 0xff] {
            let mut input = base.clone();
            input[position] = byte;
            let _ = Metadata::parse(&input);
        }
    }
    let mut seed = 0x9143_fabd_u64;
    for size in 0..1024 {
        let mut input = vec![0; size];
        for byte in &mut input {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            *byte = seed as u8;
        }
        let _ = Metadata::parse(&input);
    }
}

#[test]
fn rejects_overlapping_fat_slices_even_with_unused_padding() {
    for fat64 in [false, true] {
        for big in [false, true] {
            let image = thin(true, false, Some(UUID), None);
            let mut file = fat(&[image.clone(), image], big, fat64);
            let second = 8 + if fat64 { 32 } else { 20 };
            // Point both architectures at the first image. The unused second
            // image still leaves enough space to pass a sum-of-sizes budget.
            if fat64 {
                let first_offset = file[16..24].to_vec();
                file[second + 8..second + 16].copy_from_slice(&first_offset);
            } else {
                let first_offset = file[16..20].to_vec();
                file[second + 8..second + 12].copy_from_slice(&first_offset);
            }
            assert_eq!(
                Metadata::parse(&file).unwrap_err().0,
                "overlapping fat slices"
            );
        }
    }
}

#[test]
fn fat_table_order_determines_uuid_order_not_slice_offsets() {
    for fat64 in [false, true] {
        let mut file = fat(
            &[
                thin(true, false, Some(UUID), None),
                thin(true, false, Some([255; 16]), None),
            ],
            true,
            fat64,
        );
        let stride = if fat64 { 32 } else { 20 };
        let first = file[8..8 + stride].to_vec();
        let second = file[8 + stride..8 + 2 * stride].to_vec();
        file[8..8 + stride].copy_from_slice(&second);
        file[8 + stride..8 + 2 * stride].copy_from_slice(&first);
        assert_eq!(
            Metadata::parse(&file).unwrap().uuids,
            [Uuid([255; 16]), Uuid(UUID)]
        );
    }
}

// Build several section records sharing one payload; range order is command
// order and may differ from file offset order.
fn name_sections(payload: &[u8], ranges: &[(usize, usize)]) -> Vec<u8> {
    assert!(!ranges.is_empty());
    let mut image = thin(true, false, None, Some(payload));
    let section = image[104..184].to_vec();
    let extra = section.repeat(ranges.len() - 1);
    image.splice(184..184, extra);
    let command_size = 72 + 80 * ranges.len();
    put32(&mut image, 20, command_size as u32, false);
    put32(&mut image, 36, command_size as u32, false);
    put32(&mut image, 96, ranges.len() as u32, false);
    let payload_start = 32 + command_size;
    for (index, &(start, size)) in ranges.iter().enumerate() {
        let section_start = 104 + 80 * index;
        put64(&mut image, section_start + 40, size as u64, false);
        put32(
            &mut image,
            section_start + 48,
            (payload_start + start) as u32,
            false,
        );
    }
    image
}

#[test]
fn overlapping_names_keep_command_order_and_validate_every_extent() {
    let image = name_sections(b"first\0second\0", &[(6, 7), (0, 13), (1, 12)]);
    assert_eq!(
        Metadata::parse(&image).unwrap().elf_dependency,
        Some(b"second".as_slice())
    );
    // A prior scan finding a NUL does not validate a shorter alias that ends
    // before it, including aliases beginning at exactly the same offset.
    for ranges in [&[(0, 6), (1, 3)][..], &[(0, 6), (0, 5)][..]] {
        let image = name_sections(b"first\0", ranges);
        assert!(Metadata::parse(&image).is_err());
    }
    let image = name_sections(b"first\0", &[(0, 6), (6, 0)]);
    assert!(Metadata::parse(&image).is_err());
}

#[test]
fn many_aliases_share_a_single_payload_scan() {
    let mut payload = vec![b'x'; 65536];
    payload.push(0);
    let ranges: Vec<_> = (0..8192)
        .rev()
        .map(|start| (start, payload.len() - start))
        .collect();
    let image = name_sections(&payload, &ranges);
    let parsed = Metadata::parse(&image).unwrap();
    assert_eq!(parsed.elf_dependency.unwrap(), &payload[8191..65536]);
}
