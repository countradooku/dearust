// SPDX-License-Identifier: GPL-3.0-or-later
#![forbid(unsafe_code)]
use dearust_coredump::{Options, convert, convert_file};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Dir(PathBuf);
impl Dir {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "darling-core-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn put(data: &mut [u8], at: usize, value: u64, width: usize) {
    data[at..at + width].copy_from_slice(&value.to_le_bytes()[..width]);
}
fn n(data: &[u8], at: usize, width: usize) -> u64 {
    let mut v = [0; 8];
    v[..width].copy_from_slice(&data[at..at + width]);
    u64::from_le_bytes(v)
}
fn push(data: &mut Vec<u8>, value: u64, width: usize) {
    data.extend_from_slice(&value.to_le_bytes()[..width]);
}
fn note(notes: &mut Vec<u8>, kind: u64, desc: &[u8]) {
    push(notes, 5, 4);
    push(notes, desc.len() as u64, 4);
    push(notes, kind, 4);
    notes.extend_from_slice(b"CORE\0\0\0\0");
    notes.extend_from_slice(desc);
    while notes.len() % 4 != 0 {
        notes.push(0);
    }
}
/// Synthetic Linux ABI core with one dumped segment, one PRSTATUS and NT_FILE.
fn fixture(wide: bool, mappings: &[(u64, u64, u64, &str)]) -> Vec<u8> {
    let w = if wide { 8 } else { 4 };
    let hs = if wide { 64 } else { 52 };
    let ps = if wide { 56 } else { 32 };
    let mut file = Vec::new();
    push(&mut file, mappings.len() as u64, w);
    push(&mut file, 4096, w);
    for &(start, end, offset, _) in mappings {
        for v in [start, end, offset] {
            push(&mut file, v, w);
        }
    }
    for &(_, _, _, name) in mappings {
        file.extend_from_slice(name.as_bytes());
        file.push(0);
    }
    let mut registers = vec![0; if wide { 336 } else { 144 }];
    let start = if wide { 112 } else { 72 };
    for i in 0..if wide { 27 } else { 17 } {
        put(&mut registers, start + i * w, 0x100 + i as u64, w);
    }
    let mut notes = Vec::new();
    note(&mut notes, 1, &registers);
    note(&mut notes, 0x46494c45, &file);
    let no = hs + 2 * ps;
    let payload = no + notes.len();
    let mut data = vec![0; payload + 4];
    data[..4].copy_from_slice(b"\x7fELF");
    put(&mut data, 4, if wide { 2 } else { 1 }, 1);
    put(&mut data, 5, 1, 1);
    put(&mut data, 6, 1, 1);
    put(&mut data, 16, 4, 2);
    put(&mut data, 18, if wide { 62 } else { 3 }, 2);
    put(&mut data, 20, 1, 4);
    put(&mut data, if wide { 32 } else { 28 }, hs as u64, w);
    put(&mut data, if wide { 52 } else { 40 }, hs as u64, 2);
    put(&mut data, if wide { 54 } else { 42 }, ps as u64, 2);
    put(&mut data, if wide { 56 } else { 44 }, 2, 2);
    for (i, kind, off, addr, len, size, flags) in [
        (
            0,
            4,
            no as u64,
            0,
            notes.len() as u64,
            notes.len() as u64,
            0,
        ),
        (1, 1, payload as u64, 0x1000, 4, 8, 6),
    ] {
        let at = hs + i * ps;
        put(&mut data, at, kind, 4);
        if wide {
            put(&mut data, at + 4, flags, 4);
            for (x, v) in [(8, off), (16, addr), (32, len), (40, size)] {
                put(&mut data, at + x, v, 8);
            }
        } else {
            for (x, v) in [(4, off), (8, addr), (16, len), (20, size), (24, flags)] {
                put(&mut data, at + x, v, 4);
            }
        }
    }
    data[no..payload].copy_from_slice(&notes);
    data[payload..].copy_from_slice(b"CORE");
    data
}
fn output(wide: bool, mappings: &[(u64, u64, u64, &str)]) -> Vec<u8> {
    let mut out = Vec::new();
    convert(&fixture(wide, mappings), &mut out, &Options::default()).unwrap();
    out
}
fn header(wide: bool) -> usize {
    if wide { 32 } else { 28 }
}
fn segment(wide: bool) -> usize {
    if wide { 72 } else { 56 }
}
fn normalize(data: &mut [u8]) {
    let wide = n(data, 0, 4) == 0xfeedfacf;
    let mut cursor = header(wide);
    for _ in 0..n(data, 16, 4) {
        let kind = n(data, cursor, 4);
        let size = n(data, cursor + 4, 4) as usize;
        if kind == 1 || kind == 0x19 {
            data[cursor + 9..cursor + 24].fill(0);
        }
        cursor += size;
    }
}
// The exact C reference invents a zero FP state even though this synthetic
// input supplies no FP note. Remove only that known defect, adjusting command
// lengths/file offsets, then compare every remaining output byte as before.
fn remove_fabricated_float_state(data: &mut Vec<u8>) {
    let wide = n(data, 0, 4) == 0xfeedfacf;
    let h = header(wide);
    let old_commands = n(data, 20, 4) as usize;
    let mut commands = Vec::new();
    let mut cursor = h;
    for _ in 0..n(data, 16, 4) {
        let kind = n(data, cursor, 4);
        let size = n(data, cursor + 4, 4) as usize;
        let command = &data[cursor..cursor + size];
        if kind == 4 {
            assert_eq!(n(command, size - 532, 4), if wide { 5 } else { 2 });
            assert_eq!(n(command, size - 528, 4), 131);
            assert!(command[size - 524..].iter().all(|b| *b == 0));
            let start = commands.len();
            commands.extend_from_slice(&command[..size - 532]);
            put(&mut commands, start + 4, (size - 532) as u64, 4);
        } else {
            commands.extend_from_slice(command);
        }
        cursor += size;
    }
    let delta = old_commands - commands.len();
    let mut cursor = 0;
    while cursor < commands.len() {
        let size = n(&commands, cursor + 4, 4) as usize;
        if matches!(n(&commands, cursor, 4), 1 | 0x19) {
            let width = if wide { 8 } else { 4 };
            let at = cursor + 24 + 2 * width;
            let offset = n(&commands, at, width);
            put(&mut commands, at, offset - delta as u64, width);
        }
        cursor += size;
    }
    let mut corrected = data[..h].to_vec();
    put(&mut corrected, 20, commands.len() as u64, 4);
    corrected.extend_from_slice(&commands);
    corrected.extend_from_slice(&data[h + old_commands..]);
    *data = corrected;
}

#[test]
fn both_architectures_segments_registers_and_zero_tails() {
    for wide in [false, true] {
        let out = output(wide, &[]);
        let h = header(wide);
        let s = segment(wide);
        let w = if wide { 8 } else { 4 };
        assert_eq!(n(&out, 0, 4), if wide { 0xfeedfacf } else { 0xfeedface });
        assert_eq!(n(&out, 16, 4), 2);
        assert_eq!(n(&out, h + 24, w), 0x1000);
        assert_eq!(n(&out, h + 24 + 3 * w, w), 8);
        assert_eq!(&out[out.len() - 8..], b"CORE\0\0\0\0");
        let t = h + s;
        assert_eq!(n(&out, t, 4), 4);
        assert_eq!(n(&out, t + 8, 4), if wide { 4 } else { 1 });
        assert_eq!(n(&out, t + 16, w), if wide { 0x10a } else { 0x106 });
        let expected: &[u64] = if wide {
            &[
                10, 5, 11, 12, 14, 13, 4, 19, 9, 8, 7, 6, 3, 2, 1, 0, 16, 18, 17, 25, 26,
            ]
        } else {
            &[6, 0, 1, 2, 4, 3, 5, 15, 16, 14, 12, 13, 7, 8, 9, 10]
        };
        for (i, &r) in expected.iter().enumerate() {
            assert_eq!(n(&out, t + 16 + i * w, w), 0x100 + r);
        }
        // No FP note exists in this fixture: do not fabricate a zero state.
        assert_eq!(n(&out, t + 4, 4) as usize, 16 + expected.len() * w);
    }
}
#[test]
fn keeps_all_extra_mappings_and_missing_files_are_zero() {
    for wide in [false, true] {
        let out = output(
            wide,
            &[
                (0x2000, 0x2003, 0, "/missing/core-a"),
                (0x3000, 0x3005, 0, "/missing/core-b"),
            ],
        );
        assert_eq!(n(&out, 16, 4), 4);
        assert_eq!(&out[out.len() - 16..], b"CORE\0\0\0\0\0\0\0\0\0\0\0\0");
    }
}
#[test]
fn reconstructs_file_mapping_subrange_and_short_backing_file() {
    let dir = Dir::new();
    let path = dir.0.join("image");
    let mut image = vec![0; 4096];
    image.extend_from_slice(b"DATA");
    std::fs::write(&path, &image).unwrap();
    for wide in [false, true] {
        let mut data = fixture(wide, &[(0x1000, 0x1008, 1, path.to_str().unwrap())]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        put(
            &mut data,
            hs + ps + if wide { 32 } else { 16 },
            0,
            if wide { 8 } else { 4 },
        );
        let mut out = Vec::new();
        convert(&data, &mut out, &Options::default()).unwrap();
        assert_eq!(&out[out.len() - 8..], b"DATA\0\0\0\0");
    }
}
#[test]
fn resolves_overlay_upper_and_lower_paths_and_copies_executable_flags() {
    let dir = Dir::new();
    let upper = dir.0.join("prefix");
    let lower = dir.0.join("lower");
    std::fs::create_dir(&upper).unwrap();
    std::fs::create_dir(&lower).unwrap();
    let options = Options {
        prefix: upper.clone(),
        libexec: lower.clone(),
    };
    let mut executable = vec![0; 40];
    put(&mut executable, 0, 0xfeedfacf, 4);
    put(&mut executable, 12, 2, 4);
    put(&mut executable, 24, 0x200004, 4);
    std::fs::write(lower.join("app"), &executable).unwrap();
    for path in [
        "/app".to_string(),
        upper.join("app").to_string_lossy().to_string(),
    ] {
        let mut out = Vec::new();
        convert(
            &fixture(true, &[(0x2000, 0x2028, 0, &path)]),
            &mut out,
            &options,
        )
        .unwrap();
        assert_eq!(n(&out, 24, 4), 0x200004);
        assert_eq!(&out[out.len() - 40..], &executable);
    }
    executable[32] = 99;
    std::fs::write(upper.join("app"), &executable).unwrap();
    let mut out = Vec::new();
    convert(
        &fixture(true, &[(0x2000, 0x2028, 0, "/app")]),
        &mut out,
        &options,
    )
    .unwrap();
    assert_eq!(&out[out.len() - 40..], &executable);
}
#[test]
fn every_truncated_prefix_is_rejected_without_writing() {
    for wide in [false, true] {
        let data = fixture(wide, &[]);
        for end in 0..data.len() {
            let mut out = Vec::new();
            assert!(
                convert(&data[..end], &mut out, &Options::default()).is_err(),
                "prefix {end}"
            );
            assert!(out.is_empty());
        }
    }
}
#[test]
fn malformed_counts_names_machine_and_sizes_are_rejected() {
    for wide in [false, true] {
        let original = fixture(wide, &[(0x1000, 0x1008, 0, "a")]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        for (at, value, w) in [
            (5, 2, 1),
            (18, 183, 2),
            (if wide { 54 } else { 42 }, 0, 2),
            (
                hs + ps + if wide { 32 } else { 16 },
                9,
                if wide { 8 } else { 4 },
            ),
        ] {
            let mut data = original.clone();
            put(&mut data, at, value, w);
            assert!(convert(&data, &mut Vec::new(), &Options::default()).is_err());
        }
        let mut data = original.clone();
        let notes = hs + 2 * ps;
        let second = notes + 20 + if wide { 336 } else { 144 };
        let desc = second + 20;
        put(&mut data, desc, u64::MAX, if wide { 8 } else { 4 });
        assert!(convert(&data, &mut Vec::new(), &Options::default()).is_err());
    }
}
#[test]
fn aliases_and_invalid_metadata_do_not_destroy_files() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let alias = dir.0.join("alias");
    let destination = dir.0.join("destination");
    let data = fixture(true, &[]);
    std::fs::write(&input, &data).unwrap();
    std::fs::hard_link(&input, &alias).unwrap();
    assert!(convert_file(&input, &alias, &Options::default()).is_err());
    assert_eq!(std::fs::read(&input).unwrap(), data);
    std::fs::write(&input, b"bad").unwrap();
    std::fs::write(&destination, b"keep").unwrap();
    assert!(convert_file(&input, &destination, &Options::default()).is_err());
    assert_eq!(std::fs::read(&destination).unwrap(), b"keep");
}
#[test]
fn cli_default_filename_and_error_status() {
    let dir = Dir::new();
    let input = dir.0.join("linux.core");
    std::fs::write(&input, fixture(true, &[])).unwrap();
    let bin = env!("CARGO_BIN_EXE_darling-coredump");
    assert!(
        std::process::Command::new(bin)
            .arg(&input)
            .status()
            .unwrap()
            .success()
    );
    assert_eq!(
        std::fs::read(dir.0.join("darlingcore-linux.core")).unwrap(),
        output(true, &[])
    );
    assert!(!std::process::Command::new(bin).status().unwrap().success());
}
#[test]
#[ignore = "set DARLING_COREDUMP_REFERENCE to a compiled pinned upstream converter"]
fn differential_pinned_upstream_both_architectures() {
    let binary = std::env::var_os("DARLING_COREDUMP_REFERENCE").expect("reference binary required");
    let dir = Dir::new();
    let executable = dir.0.join("app");
    let mut image = vec![0; 40];
    put(&mut image, 0, 0xfeedfacf, 4);
    put(&mut image, 12, 2, 4);
    put(&mut image, 24, 0x200004, 4);
    std::fs::write(&executable, image).unwrap();
    for (wide, mapped) in [(false, false), (true, false), (false, true), (true, true)] {
        let input = dir.0.join("input");
        let dest = dir.0.join("output");
        let mappings = if mapped {
            vec![(0x2000, 0x2028, 0, executable.to_str().unwrap())]
        } else {
            vec![]
        };
        let data = fixture(wide, &mappings);
        std::fs::write(&input, &data).unwrap();
        let run = std::process::Command::new(&binary)
            .arg(&input)
            .arg(&dest)
            .output()
            .unwrap();
        assert!(
            run.status.success(),
            "{}",
            String::from_utf8_lossy(&run.stderr)
        );
        let mut expected = std::fs::read(&dest).unwrap();
        let mut actual = Vec::new();
        convert(&data, &mut actual, &Options::default()).unwrap();
        remove_fabricated_float_state(&mut expected);
        normalize(&mut expected);
        normalize(&mut actual);
        assert_eq!(actual, expected);
    }
}

#[test]
fn backing_file_alias_does_not_destroy_image() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let image = dir.0.join("image");
    std::fs::write(&image, b"data").unwrap();
    std::fs::write(
        &input,
        fixture(true, &[(0x2000, 0x2004, 0, image.to_str().unwrap())]),
    )
    .unwrap();
    assert!(convert_file(&input, &image, &Options::default()).is_err());
    assert_eq!(std::fs::read(&image).unwrap(), b"data");
}

#[test]
fn multiple_threads_and_inaccessible_segments() {
    for wide in [false, true] {
        let mut data = fixture(wide, &[]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        let no = hs + 2 * ps;
        let first_size = 20 + if wide { 336 } else { 144 };
        let mut first = data[no..no + first_size].to_vec();
        // The second genuine thread needs a distinct Linux TID.
        put(&mut first, 20 + if wide { 32 } else { 24 }, 1, 4);
        let note_len = n(&data, hs + if wide { 32 } else { 16 }, w) + first_size as u64;
        let payload = n(&data, hs + ps + if wide { 8 } else { 4 }, w) + first_size as u64;
        data.splice(no..no, first);
        put(&mut data, hs + if wide { 32 } else { 16 }, note_len, w);
        put(&mut data, hs + if wide { 40 } else { 20 }, note_len, w);
        put(&mut data, hs + ps + if wide { 8 } else { 4 }, payload, w);
        put(&mut data, hs + ps + if wide { 4 } else { 24 }, 0, 4);
        let mut out = Vec::new();
        convert(&data, &mut out, &Options::default()).unwrap();
        assert_eq!(n(&out, 16, 4), 2);
        let thread_size = if wide { 184 } else { 80 };
        assert_eq!(out.len(), header(wide) + 2 * thread_size);
        assert_eq!(
            &out[header(wide)..header(wide) + thread_size],
            &out[header(wide) + thread_size..]
        );
    }
}

#[test]
fn overflowing_32_bit_layout_is_rejected_before_output() {
    let data = fixture(false, &[(0, u32::MAX as u64, 0, "/missing/huge")]);
    let mut out = Vec::new();
    assert!(convert(&data, &mut out, &Options::default()).is_err());
    assert!(out.is_empty());
}

#[test]
fn non_macho_backing_file_cannot_supply_macho_flags() {
    let dir = Dir::new();
    let path = dir.0.join("not-macho");
    let mut contents = vec![0; 32];
    put(&mut contents, 12, 2, 4);
    put(&mut contents, 24, 0xdeadbeef, 4);
    std::fs::write(&path, contents).unwrap();
    let out = output(true, &[(0x2000, 0x2020, 0, path.to_str().unwrap())]);
    assert_eq!(n(&out, 24, 4), 0);
}

#[test]
fn adversarial_elf_with_many_unmatched_mapping_ranges_converts() {
    // These legal but adversarially ordered ranges previously required 400
    // million area/mapping comparisons. No area can match any NT_FILE entry.
    let count = 20_000;
    for wide in [false, true] {
        let mappings: Vec<_> = (0..count)
            .rev()
            .map(|i| (i as u64, i as u64 + 1, 0, "/dev/unavailable"))
            .collect();
        let mut data = fixture(wide, &mappings);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        let extra_size = (count - 1) * ps;
        data.splice(hs + 2 * ps..hs + 2 * ps, vec![0; extra_size]);
        put(&mut data, if wide { 56 } else { 44 }, (count + 1) as u64, 2);
        put(
            &mut data,
            hs + if wide { 8 } else { 4 },
            (hs + (count + 1) * ps) as u64,
            w,
        );
        for i in 0..count {
            let ph = hs + (i + 1) * ps;
            data[ph..ph + ps].fill(0);
            put(&mut data, ph, 1, 4);
            put(
                &mut data,
                ph + if wide { 16 } else { 8 },
                (30_000 + i) as u64,
                w,
            );
            put(&mut data, ph + if wide { 40 } else { 20 }, 1, w);
        }
        let mut out = Vec::new();
        convert(&data, &mut out, &Options::default()).unwrap();
        assert_eq!(n(&out, 16, 4), (count + 1) as u64);
        assert!(out[out.len() - count..].iter().all(|byte| *byte == 0));
    }
}

#[test]
fn nonregular_core_input_is_rejected_before_destination_is_changed() {
    let dir = Dir::new();
    let destination = dir.0.join("preserve");
    std::fs::write(&destination, b"keep").unwrap();
    assert!(
        convert_file(
            std::path::Path::new("/dev/zero"),
            &destination,
            &Options::default()
        )
        .is_err()
    );
    assert_eq!(std::fs::read(destination).unwrap(), b"keep");
}

#[test]
fn file_and_borrowed_conversion_match_for_both_architectures() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("converted");
    let image = dir.0.join("image");
    std::fs::write(&image, b"mapped-data").unwrap();
    for wide in [false, true] {
        let data = fixture(wide, &[(0x2000, 0x200b, 0, image.to_str().unwrap())]);
        let mut expected = Vec::new();
        convert(&data, &mut expected, &Options::default()).unwrap();
        std::fs::write(&input, &data).unwrap();
        convert_file(&input, &destination, &Options::default()).unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), expected);
    }
}

#[test]
fn every_truncated_file_prefix_preserves_existing_destination() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("preserve");
    std::fs::write(&destination, b"keep existing destination").unwrap();
    for wide in [false, true] {
        let data = fixture(wide, &[]);
        for end in 0..data.len() {
            std::fs::write(&input, &data[..end]).unwrap();
            assert!(
                convert_file(&input, &destination, &Options::default()).is_err(),
                "wide={wide}, prefix={end}"
            );
            assert_eq!(
                std::fs::read(&destination).unwrap(),
                b"keep existing destination",
                "wide={wide}, prefix={end}"
            );
        }
    }
}

#[test]
fn sparse_distant_program_headers_and_late_notes_follow_file_order() {
    use std::os::unix::fs::FileExt;
    let dir = Dir::new();
    let input = dir.0.join("sparse-core");
    let destination = dir.0.join("converted");
    for wide in [false, true] {
        let mut original = fixture(wide, &[]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        let original_notes = hs + 2 * ps;
        let first_size = 20 + if wide { 336 } else { 144 };
        let mut second_thread = original[original_notes..original_notes + first_size].to_vec();
        put(&mut second_thread, 20 + if wide { 32 } else { 24 }, 123, 4);
        put(
            &mut second_thread,
            20 + if wide { 112 } else { 72 },
            0xabc,
            w,
        );
        original.splice(
            original_notes + first_size..original_notes + first_size,
            second_thread,
        );
        let notes_length = (original.len() - 4 - original_notes) as u64;
        put(
            &mut original,
            hs + if wide { 32 } else { 16 },
            notes_length,
            w,
        );
        put(
            &mut original,
            hs + if wide { 40 } else { 20 },
            notes_length,
            w,
        );
        let original_payload = original.len() - 4;
        put(
            &mut original,
            hs + ps + if wide { 8 } else { 4 },
            original_payload as u64,
            w,
        );
        let mut expected = Vec::new();
        convert(&original, &mut expected, &Options::default()).unwrap();
        let phoff = 8 * 1024 * 1024;
        let mut elf_header = original[..hs].to_vec();
        put(&mut elf_header, if wide { 32 } else { 28 }, phoff, w);
        put(&mut elf_header, if wide { 56 } else { 44 }, 3, 2);
        let mut first = original[hs..hs + ps].to_vec();
        put(&mut first, if wide { 8 } else { 4 }, 4096, w);
        put(&mut first, if wide { 32 } else { 16 }, first_size as u64, w);
        put(&mut first, if wide { 40 } else { 20 }, first_size as u64, w);
        let mut second = original[hs..hs + ps].to_vec();
        put(&mut second, if wide { 8 } else { 4 }, 8192, w);
        let second_size = original_payload - original_notes - first_size;
        put(
            &mut second,
            if wide { 32 } else { 16 },
            second_size as u64,
            w,
        );
        put(
            &mut second,
            if wide { 40 } else { 20 },
            second_size as u64,
            w,
        );
        let mut load = original[hs + ps..hs + 2 * ps].to_vec();
        put(&mut load, if wide { 8 } else { 4 }, hs as u64, w);
        let file = std::fs::File::create(&input).unwrap();
        file.set_len(phoff + 3 * ps as u64).unwrap();
        file.write_all_at(&elf_header, 0).unwrap();
        file.write_all_at(b"CORE", hs as u64).unwrap();
        file.write_all_at(&original[original_notes..original_notes + first_size], 4096)
            .unwrap();
        file.write_all_at(
            &original[original_notes + first_size..original_payload],
            8192,
        )
        .unwrap();
        // Header order intentionally disagrees with the physical note order.
        file.write_all_at(&second, phoff).unwrap();
        file.write_all_at(&load, phoff + ps as u64).unwrap();
        file.write_all_at(&first, phoff + 2 * ps as u64).unwrap();
        drop(file);
        let mut borrowed = Vec::new();
        convert(
            &std::fs::read(&input).unwrap(),
            &mut borrowed,
            &Options::default(),
        )
        .unwrap();
        assert_eq!(borrowed, expected);
        convert_file(&input, &destination, &Options::default()).unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), borrowed);
    }
}

#[test]
fn invalid_file_ranges_and_overlapping_notes_preserve_destination() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("preserve");
    std::fs::write(&destination, b"keep").unwrap();
    for wide in [false, true] {
        let original = fixture(wide, &[]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        let offset = if wide { 8 } else { 4 };
        let length = if wide { 32 } else { 16 };
        let beyond = original.len() as u64 + 1;
        let mut cases = Vec::new();
        for (at, value, width) in [
            (if wide { 32 } else { 28 }, beyond, w),
            (hs + offset, beyond, w),
            (hs + ps + offset, beyond, w),
            (hs + length, u64::MAX, w),
        ] {
            let mut data = original.clone();
            put(&mut data, at, value, width);
            cases.push(data);
        }
        let mut overlap = original.clone();
        let note_header = overlap[hs..hs + ps].to_vec();
        overlap[hs + ps..hs + 2 * ps].copy_from_slice(&note_header);
        cases.push(overlap);
        // Corrupt the final note: all metadata must be validated before output.
        let mut late = original.clone();
        let second_note = hs + 2 * ps + 20 + if wide { 336 } else { 144 };
        put(&mut late, second_note + 4, u32::MAX as u64, 4);
        cases.push(late);
        for (case, data) in cases.iter().enumerate() {
            let mut borrowed = Vec::new();
            assert!(convert(data, &mut borrowed, &Options::default()).is_err());
            assert!(borrowed.is_empty());
            std::fs::write(&input, data).unwrap();
            assert!(
                convert_file(&input, &destination, &Options::default()).is_err(),
                "wide={wide}, case={case}"
            );
            assert_eq!(std::fs::read(&destination).unwrap(), b"keep");
        }
    }
}

#[test]
fn zero_length_load_may_point_beyond_input_file() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("converted");
    for wide in [false, true] {
        let mut data = fixture(wide, &[]);
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        put(&mut data, hs + ps + if wide { 8 } else { 4 }, u64::MAX, w);
        put(&mut data, hs + ps + if wide { 32 } else { 16 }, 0, w);
        let mut expected = Vec::new();
        convert(&data, &mut expected, &Options::default()).unwrap();
        assert_eq!(&expected[expected.len() - 8..], &[0; 8]);
        std::fs::write(&input, data).unwrap();
        convert_file(&input, &destination, &Options::default()).unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), expected);
    }
}

#[test]
fn destination_symlink_and_hardlink_backing_aliases_preserve_image() {
    use std::os::unix::fs::symlink;
    let dir = Dir::new();
    let input = dir.0.join("core");
    let image = dir.0.join("image");
    let hardlink = dir.0.join("hardlink");
    let symlink_path = dir.0.join("symlink");
    std::fs::write(&image, b"backing data").unwrap();
    std::fs::hard_link(&image, &hardlink).unwrap();
    symlink(&image, &symlink_path).unwrap();
    for wide in [false, true] {
        std::fs::write(
            &input,
            fixture(wide, &[(0x2000, 0x200c, 0, image.to_str().unwrap())]),
        )
        .unwrap();
        for destination in [&hardlink, &symlink_path] {
            assert!(convert_file(&input, destination, &Options::default()).is_err());
            assert_eq!(std::fs::read(&image).unwrap(), b"backing data");
            assert_eq!(std::fs::read(destination).unwrap(), b"backing data");
        }
    }
}

#[test]
fn large_unaligned_dumped_payload_keeps_page_offsets_and_raw_note_locations() {
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("converted");
    for wide in [false, true] {
        let hs = if wide { 64 } else { 52 };
        let ps = if wide { 56 } else { 32 };
        let w = if wide { 8 } else { 4 };
        let mut data = fixture(wide, &[]);
        let no = hs + 2 * ps;
        let mut notes = data[no..data.len() - 4].to_vec();
        let mut floating = vec![0; if wide { 512 } else { 108 }];
        floating[50] = 0x7b;
        note(&mut notes, 2, &floating);
        let source_offset = 2 * 4096 + 123;
        let length = 65536 + 2 * 4096 + 117;
        let memory_size = length + 37;
        let payload: Vec<_> = (0..length).map(|i| (i % 251) as u8).collect();
        data.resize(source_offset, 0);
        data[no..no + notes.len()].copy_from_slice(&notes);
        data.extend_from_slice(&payload);
        // A source tail before EOF must neither copy this sentinel nor use the
        // EOF-only kernel-copy behavior as a substitute for the exact range.
        data.extend_from_slice(b"unused input sentinel after selected payload");
        put(
            &mut data,
            hs + if wide { 32 } else { 16 },
            notes.len() as u64,
            w,
        );
        put(
            &mut data,
            hs + if wide { 40 } else { 20 },
            notes.len() as u64,
            w,
        );
        put(
            &mut data,
            hs + ps + if wide { 8 } else { 4 },
            source_offset as u64,
            w,
        );
        put(
            &mut data,
            hs + ps + if wide { 32 } else { 16 },
            length as u64,
            w,
        );
        put(
            &mut data,
            hs + ps + if wide { 40 } else { 20 },
            memory_size as u64,
            w,
        );
        let mut borrowed = Vec::new();
        convert(&data, &mut borrowed, &Options::default()).unwrap();
        std::fs::write(&input, &data).unwrap();
        convert_file(&input, &destination, &Options::default()).unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), borrowed);
        let h = header(wide);
        let output_offset = n(&borrowed, h + 24 + 2 * w, w) as usize;
        let commands_end = h + n(&borrowed, 20, 4) as usize;
        assert_eq!(output_offset % 4096, source_offset % 4096);
        assert!(output_offset >= commands_end);
        assert!(
            borrowed[commands_end..output_offset]
                .iter()
                .all(|byte| *byte == 0)
        );
        assert_eq!(&borrowed[output_offset..output_offset + length], payload);
        assert!(
            borrowed[output_offset + length..output_offset + memory_size]
                .iter()
                .all(|byte| *byte == 0)
        );
        let mut cursor = h;
        let mut raw = None;
        for _ in 0..n(&borrowed, 16, 4) {
            if n(&borrowed, cursor, 4) == 0x31 {
                assert!(raw.is_none());
                raw = Some((
                    n(&borrowed, cursor + 24, 8) as usize,
                    n(&borrowed, cursor + 32, 8) as usize,
                ));
            }
            cursor += n(&borrowed, cursor + 4, 4) as usize;
        }
        let (raw_offset, raw_length) = raw.expect("provided FP state has a raw archive");
        assert_eq!(raw_offset, output_offset + memory_size);
        assert_eq!(raw_length, 32 + floating.len());
        assert_eq!(raw_offset + raw_length, borrowed.len());
        assert_eq!(n(&borrowed, raw_offset, 4), 1);
        assert_eq!(n(&borrowed, raw_offset + 4, 4), if wide { 2 } else { 1 });
        assert_eq!(n(&borrowed, raw_offset + 16, 4), 2);
        assert_eq!(&borrowed[raw_offset + 32..], floating);
    }
}

#[test]
fn i386_large_payload_uses_packed_layout_when_alignment_would_overflow() {
    struct StopAtFirstWrite;
    impl std::io::Write for StopAtFirstWrite {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "stop after layout validation",
            ))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut data = fixture(false, &[]);
    let source_offset = data.len() - 4;
    data.resize(source_offset + 65536, 0x7b);
    let load = 52 + 32;
    put(&mut data, load + 8, 0, 4);
    put(&mut data, load + 16, 65536, 4);
    let packed_header = 28 + 56 + 80;
    put(&mut data, load + 20, u32::MAX as u64 - packed_header, 4);
    let error = convert(&data, &mut StopAtFirstWrite, &Options::default()).unwrap_err();
    assert_eq!(error.kind(), std::io::ErrorKind::BrokenPipe);
    assert_eq!(error.to_string(), "stop after layout validation");
}

#[test]
fn large_zero_tails_clear_reused_destination_and_preserve_following_raw_notes() {
    use std::os::unix::fs::MetadataExt;
    let dir = Dir::new();
    let input = dir.0.join("core");
    let destination = dir.0.join("converted");
    let memory_size = 1024 * 1024 + 4;
    let probe = std::fs::File::create(dir.0.join("sparse-probe")).unwrap();
    probe.set_len(memory_size as u64).unwrap();
    let probe_meta = probe.metadata().unwrap();
    let supports_holes = probe_meta.blocks() * 512 < probe_meta.len();
    for wide in [false, true] {
        for with_floating in [false, true] {
            let hs = if wide { 64 } else { 52 };
            let ps = if wide { 56 } else { 32 };
            let w = if wide { 8 } else { 4 };
            let mut data = fixture(wide, &[]);
            let floating = vec![0x5a; if wide { 512 } else { 108 }];
            if with_floating {
                let no = hs + 2 * ps;
                let mut notes = data[no..data.len() - 4].to_vec();
                note(&mut notes, 2, &floating);
                data.truncate(no);
                data.extend_from_slice(&notes);
                let payload_offset = data.len();
                data.extend_from_slice(b"CORE");
                put(
                    &mut data,
                    hs + if wide { 32 } else { 16 },
                    notes.len() as u64,
                    w,
                );
                put(
                    &mut data,
                    hs + if wide { 40 } else { 20 },
                    notes.len() as u64,
                    w,
                );
                put(
                    &mut data,
                    hs + ps + if wide { 8 } else { 4 },
                    payload_offset as u64,
                    w,
                );
            }
            put(
                &mut data,
                hs + ps + if wide { 40 } else { 20 },
                memory_size as u64,
                w,
            );
            let mut expected = Vec::new();
            convert(&data, &mut expected, &Options::default()).unwrap();
            std::fs::write(&input, &data).unwrap();
            // Old nonzero bytes must not survive either the hole or the end of
            // the newly converted output when reusing an existing destination.
            std::fs::write(&destination, vec![0xa5; memory_size * 2]).unwrap();
            convert_file(&input, &destination, &Options::default()).unwrap();
            let actual = std::fs::read(&destination).unwrap();
            assert_eq!(actual, expected, "wide={wide}, FP={with_floating}");
            let metadata = std::fs::metadata(&destination).unwrap();
            assert_eq!(metadata.len(), expected.len() as u64);
            let offset = n(&actual, header(wide) + 24 + 2 * w, w) as usize;
            assert_eq!(&actual[offset..offset + 4], b"CORE");
            assert!(
                actual[offset + 4..offset + memory_size]
                    .iter()
                    .all(|byte| *byte == 0)
            );
            if with_floating {
                let mut cursor = header(wide);
                let mut raw = None;
                for _ in 0..n(&actual, 16, 4) {
                    if n(&actual, cursor, 4) == 0x31 {
                        raw = Some((
                            n(&actual, cursor + 24, 8) as usize,
                            n(&actual, cursor + 32, 8) as usize,
                        ));
                    }
                    cursor += n(&actual, cursor + 4, 4) as usize;
                }
                let (raw_offset, raw_length) = raw.unwrap();
                assert_eq!(raw_offset, offset + memory_size);
                assert_eq!(raw_length, 32 + floating.len());
                assert_eq!(n(&actual, raw_offset + 24, 8), floating.len() as u64);
                assert_eq!(&actual[raw_offset + 32..], floating);
            } else {
                assert_eq!(actual.len(), offset + memory_size);
            }
            if supports_holes {
                assert!(
                    metadata.blocks() * 512 < metadata.len() / 2,
                    "large zero tail should retain a sparse hole"
                );
            }
        }
    }
}

#[test]
fn inaccessible_elf32_segment_still_requires_a_valid_virtual_range() {
    let dir = Dir::new();
    let mut data = fixture(false, &[]);
    put(&mut data, 52 + 32 + 8, u32::MAX as u64, 4);
    put(&mut data, 52 + 32 + 24, 0, 4);
    let mut output = Vec::new();
    assert_eq!(
        convert(&data, &mut output, &Options::default())
            .unwrap_err()
            .kind(),
        std::io::ErrorKind::InvalidData
    );
    assert!(output.is_empty());
    let input = dir.0.join("invalid-inaccessible.core");
    let destination = dir.0.join("preserve");
    std::fs::write(&input, data).unwrap();
    std::fs::write(&destination, b"keep").unwrap();
    assert!(convert_file(&input, &destination, &Options::default()).is_err());
    assert_eq!(std::fs::read(destination).unwrap(), b"keep");
}
