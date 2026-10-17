mod common;
use common::*;
use darling_buildtools::wrapped_elf::names;
use std::{
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};

fn image(wide: bool, big: bool, names: &[&[u8]]) -> Vec<u8> {
    if names.is_empty() {
        return thin(wide, big, None, None);
    }
    let mut data = thin(wide, big, None, Some(names[0]));
    let h = if wide { 32 } else { 28 };
    let sh = if wide { 72 } else { 56 };
    let ss = if wide { 80 } else { 68 };
    let original = data[h + sh..h + sh + ss].to_vec();
    data.truncate(h + sh);
    for _ in names {
        data.extend_from_slice(&original);
    }
    put32(&mut data, 20, (sh + ss * names.len()) as u32, big);
    put32(&mut data, h + 4, (sh + ss * names.len()) as u32, big);
    put32(
        &mut data,
        h + if wide { 64 } else { 48 },
        names.len() as u32,
        big,
    );
    for (i, name) in names.iter().enumerate() {
        let section = h + sh + i * ss;
        let offset = data.len();
        put32(
            &mut data,
            section + if wide { 48 } else { 40 },
            offset as u32,
            big,
        );
        if wide {
            put64(&mut data, section + 40, name.len() as u64, big);
        } else {
            put32(&mut data, section + 36, name.len() as u32, big);
        }
        data.extend_from_slice(name);
    }
    data
}

struct Input(PathBuf);
impl Input {
    fn new(bytes: &[u8]) -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "wrapped-elf-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        file.write_all(bytes).unwrap();
        Self(path)
    }
    fn run(&self, executable: impl AsRef<std::ffi::OsStr>) -> Output {
        Command::new(executable).arg(&self.0).output().unwrap()
    }
}
impl Drop for Input {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
const TOOL: &str = env!("CARGO_BIN_EXE_print_wrapped_elf");

#[test]
fn every_matching_section_in_order_raw_bytes_and_embedded_nul() {
    for wide in [false, true] {
        for big in [false, true] {
            let data = image(wide, big, &[b"first\0ignored\0", b"lib\xff.so\0", b"\0"]);
            assert_eq!(
                names(&data).unwrap(),
                vec![b"first".as_slice(), b"lib\xff.so", b""]
            );
            let output = Input::new(&data).run(TOOL);
            assert!(output.status.success());
            assert_eq!(output.stdout, b"first\nlib\xff.so\n\n");
        }
    }
}

#[test]
fn first_fat_architecture_only() {
    for big in [false, true] {
        for wide in [false, true] {
            let data = fat(
                &[
                    image(wide, false, &[b"first\0", b"second\0"]),
                    image(!wide, false, &[b"ignored\0"]),
                ],
                big,
                false,
            );
            assert_eq!(names(&data).unwrap(), vec![b"first".as_slice(), b"second"]);
            // A later architecture's malformed content is deliberately irrelevant.
            let mut malformed_later = data.clone();
            *malformed_later.last_mut().unwrap() = b'x';
            assert_eq!(names(&malformed_later), names(&data));
        }
    }
}

#[test]
fn section_length_and_final_nul_rules() {
    let maximum = [b'x'; 99].into_iter().chain([0]).collect::<Vec<_>>();
    assert_eq!(
        names(&image(true, false, &[&maximum])).unwrap()[0].len(),
        99
    );
    for bad in [
        vec![],
        vec![0; 101],
        b"no nul".to_vec(),
        b"early\0bad".to_vec(),
    ] {
        let data = image(true, false, &[b"valid\0", &bad]);
        assert!(names(&data).is_err());
        let output = Input::new(&data).run(TOOL);
        assert!(!output.status.success());
        assert!(
            output.stdout.is_empty(),
            "invalid later section must not print earlier names"
        );
    }
}

#[test]
fn malformed_commands_offsets_and_truncations_fail_without_panics() {
    let original = image(true, false, &[b"hello\0"]);
    for end in 0..original.len() {
        assert!(names(&original[..end]).is_err(), "length {end}");
    }
    for (offset, value) in [
        (16, u32::MAX),
        (20, u32::MAX),
        (36, 0),
        (36, 7),
        (96, u32::MAX),
        (152, u32::MAX),
        (168, 1),
    ] {
        let mut data = original.clone();
        put32(&mut data, offset, value, false);
        assert!(names(&data).is_err(), "offset {offset}");
    }
    let mut data = fat(&[original], true, false);
    put32(&mut data, 16, u32::MAX, true);
    assert!(names(&data).is_err());
}

#[test]
fn missing_sections_and_cli_errors() {
    assert_eq!(
        names(&image(true, false, &[])).unwrap(),
        Vec::<&[u8]>::new()
    );
    assert!(!Command::new(TOOL).output().unwrap().status.success());
    let mut data = image(true, false, &[b"ignored\0"]);
    data[40] = b'_';
    data[41] = b'_';
    data[42] = b'D'; // __DATA instead of __TEXT
    assert!(names(&data).unwrap().is_empty());
}

#[test]
#[ignore = "requires pinned upstream binary in UPSTREAM_PRINT_WRAPPED_ELF"]
fn differential_against_pinned_upstream() {
    let reference = std::env::var_os("UPSTREAM_PRINT_WRAPPED_ELF")
        .expect("UPSTREAM_PRINT_WRAPPED_ELF required");
    let mut cases = Vec::new();
    for wide in [false, true] {
        for payload in [
            vec![b"one\0".as_slice()],
            vec![b"one\0".as_slice(), b"two\xff\0"],
            vec![b"embedded\0tail\0".as_slice()],
            vec![b"\0".as_slice()],
        ] {
            let thin = image(wide, false, &payload);
            cases.push(thin.clone());
            for big in [false, true] {
                cases.push(fat(
                    &[thin.clone(), image(!wide, false, &[b"other\0"])],
                    big,
                    false,
                ));
            }
        }
        cases.push(image(wide, false, &[]));
        cases.push(image(wide, false, &[&[b'x'; 101]]));
        let mut maximum = vec![b'x'; 100];
        maximum[99] = 0;
        cases.push(image(wide, false, &[&maximum]));
        cases.push(image(wide, false, &[b"missing final nul"]));
    }
    cases.extend([vec![], vec![0xcf, 0xfa, 0xed, 0xfe]]);
    for bytes in cases {
        let input = Input::new(&bytes);
        let expected = input.run(&reference);
        let actual = input.run(TOOL);
        assert_eq!(
            actual.status.success(),
            expected.status.success(),
            "length {}",
            bytes.len()
        );
        assert_eq!(actual.stdout, expected.stdout, "length {}", bytes.len());
    }
}
