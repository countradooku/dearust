mod common;
use common::*;
use std::{
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};

struct Input(PathBuf);
impl Input {
    fn new(data: &[u8]) -> Self {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "darling-test-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        // create_new keeps test files from overwriting an existing path.
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        file.write_all(data).unwrap();
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

const ELFDEP: &str = env!("CARGO_BIN_EXE_elfdep");
const GETUUID: &str = env!("CARGO_BIN_EXE_getuuid");

#[test]
fn cli_format_exit_codes_and_raw_bytes() {
    let file = Input::new(&thin(true, false, Some([0xab; 16]), Some(b"lib\xff.so\0")));
    let uuid = file.run(GETUUID);
    assert!(uuid.status.success());
    assert_eq!(uuid.stdout, b"ABABABAB-ABAB-ABAB-ABAB-ABABABABABAB");
    assert!(uuid.stderr.is_empty());
    let dep = file.run(ELFDEP);
    assert!(dep.status.success());
    assert_eq!(dep.stdout, b"lib\xff.so\n");
    let empty = Input::new(&thin(true, false, None, None));
    assert!(!empty.run(GETUUID).status.success());
    assert!(empty.run(GETUUID).stdout.is_empty());
    assert!(empty.run(ELFDEP).status.success());
    assert!(empty.run(ELFDEP).stdout.is_empty());
}

#[test]
fn cli_rejects_corruption_without_partial_output() {
    let file = Input::new(&fat(
        &[thin(true, false, Some([1; 16]), Some(b"ok\0")), vec![0; 32]],
        true,
        false,
    ));
    for exe in [GETUUID, ELFDEP] {
        let result = file.run(exe);
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
        assert!(!result.stderr.is_empty());
        let result = Command::new(exe).output().unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
        let result = Command::new(exe).args(["a", "b"]).output().unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
    }
}

#[test]
fn cli_multi_architecture_uuid_separator() {
    let file = Input::new(&fat(
        &[
            thin(false, false, Some([1; 16]), None),
            thin(true, true, Some([2; 16]), None),
        ],
        true,
        true,
    ));
    let result = file.run(GETUUID);
    assert!(result.status.success());
    assert_eq!(
        result.stdout,
        b"01010101-0101-0101-0101-010101010101;02020202-0202-0202-0202-020202020202"
    );
}

#[test]
#[ignore = "requires compiled upstream tools in UPSTREAM_GETUUID and UPSTREAM_ELFDEP"]
fn differential_against_pinned_upstream() {
    let original_uuid = std::env::var_os("UPSTREAM_GETUUID").expect("UPSTREAM_GETUUID required");
    let original_dep = std::env::var_os("UPSTREAM_ELFDEP").expect("UPSTREAM_ELFDEP required");
    for wide in [false, true] {
        for has_uuid in [false, true] {
            for has_name in [false, true] {
                let image = thin(
                    wide,
                    false,
                    has_uuid.then_some([42; 16]),
                    has_name.then_some(b"libtest.so\0"),
                );
                for bytes in [
                    image.clone(),
                    fat(&[image.clone(), image.clone()], true, false),
                    fat(&[image.clone(), image], false, false),
                ] {
                    let file = Input::new(&bytes);
                    for (ours, original) in [(GETUUID, &original_uuid), (ELFDEP, &original_dep)] {
                        let actual = file.run(ours);
                        let expected = file.run(original);
                        assert_eq!(actual.status.code(), expected.status.code());
                        assert_eq!(actual.stdout, expected.stdout);
                    }
                }
            }
        }
    }
}

#[cfg(unix)]
#[test]
fn special_inputs_cannot_cause_unbounded_reads() {
    for exe in [ELFDEP, GETUUID, env!("CARGO_BIN_EXE_print_wrapped_elf")] {
        let result = Command::new(exe).arg("/dev/zero").output().unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
        assert!(String::from_utf8_lossy(&result.stderr).contains("regular file"));
    }
    assert!(dearust::read_input(std::path::Path::new(".")).is_err());
}
