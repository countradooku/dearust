// SPDX-License-Identifier: GPL-3.0-or-later
#![forbid(unsafe_code)]
use darling_coredump::{Options, convert, convert_file};
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
        let fp = t + 16 + expected.len() * w;
        assert_eq!(n(&out, fp, 4), if wide { 5 } else { 2 });
        assert_eq!(n(&out, fp + 4, 4), 131);
        assert!(out[fp + 8..fp + 8 + 524].iter().all(|b| *b == 0));
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
        let first = data[no..no + first_size].to_vec();
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
        let thread_size = if wide { 716 } else { 612 };
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
