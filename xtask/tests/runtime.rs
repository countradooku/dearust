//! Build real runtime objects through Cargo, checking format, ABI and publication.
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};
struct Temp(PathBuf);
impl Temp {
    fn new(label: &str) -> Self {
        let path =
            std::env::temp_dir().join(format!("darling-runtime-{label}-{}", std::process::id()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn check(triple: &str, label: &str) {
    let temp = Temp::new(label);
    let stage = temp.0.join("runtime slice");
    let install = || {
        let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
            .args(["runtime", "--target", triple, "--destdir"])
            .arg(&stage)
            .env("CARGO_TARGET_DIR", temp.0.join("wrong-target-dir"))
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    };
    install();
    assert!(!temp.0.join("wrong-target-dir").exists());
    let names = ["libsimple", "futex", "errno"];
    let initial: Vec<_> = names
        .iter()
        .map(|name| fs::read(stage.join(format!("objects/{name}.o"))).unwrap())
        .collect();
    for (name, bytes) in names.iter().zip(&initial) {
        assert_eq!(
            &bytes[..4],
            if triple.contains("apple") {
                &[0xcf, 0xfa, 0xed, 0xfe]
            } else {
                b"\x7fELF"
            }
        );
        let object = stage.join(format!("objects/{name}.o"));
        let symbols = symbols(&object, "--defined-only");
        let prefix = if triple.contains("apple") { "_" } else { "" };
        let wanted: Vec<String> = match *name {
            "libsimple" => [
                "cmpxchg_wrapper_u32",
                "libsimple_lock_lock",
                "libsimple_lock_unlock",
                "libsimple_lock_try_lock",
                "libsimple_once",
                "libsimple_rwlock_lock_read",
                "libsimple_rwlock_try_lock_read",
                "libsimple_rwlock_unlock_read",
                "libsimple_rwlock_lock_write",
                "libsimple_rwlock_try_lock_write",
                "libsimple_rwlock_unlock_write",
                "libsimple_condvar_wait",
                "libsimple_condvar_notify_one",
                "libsimple_condvar_notify_all",
            ]
            .iter()
            .map(|s| format!("{prefix}{s}"))
            .collect(),
            "futex" => ["__linux_futex", "__linux_futex_reterr"]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect(),
            _ => ["errno_linux_to_bsd", "length_of_translation_array"]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect(),
        };
        let mut wanted = wanted;
        wanted.sort();
        assert_eq!(symbols, wanted);
        let wanted = match *name {
            "libsimple" => vec![
                format!("{prefix}__linux_futex_reterr"),
                format!("{prefix}__simple_abort"),
            ],
            "futex" => vec![format!("{prefix}cerror"), format!("{prefix}linux_syscall")],
            _ => vec![],
        };
        assert_eq!(symbols_for(&object, "--undefined-only"), wanted);
    }
    let archive = stage.join("lib/liblibsimple_darling.a");
    let members = Command::new("llvm-ar")
        .arg("t")
        .arg(&archive)
        .output()
        .unwrap();
    assert!(members.status.success());
    assert_eq!(members.stdout, b"darling_simple.o\n");
    let manifest = fs::read(stage.join("darling-runtime-artifacts.tsv")).unwrap();
    assert!(String::from_utf8_lossy(&manifest).starts_with(&format!("# target {triple}\n")));
    if triple.contains("apple") {
        let private = Command::new("llvm-nm")
            .arg("-m")
            .arg(stage.join("objects/errno.o"))
            .output()
            .unwrap();
        assert!(private.status.success());
        assert_eq!(
            String::from_utf8_lossy(&private.stdout)
                .lines()
                .filter(|l| l.contains("private external"))
                .count(),
            2
        );
    }
    install();
    for (name, bytes) in names.iter().zip(initial) {
        assert_eq!(
            fs::read(stage.join(format!("objects/{name}.o"))).unwrap(),
            bytes
        );
    }
    assert_eq!(
        fs::read(stage.join("darling-runtime-artifacts.tsv")).unwrap(),
        manifest
    );
}
fn symbols_for(path: &Path, mode: &str) -> Vec<String> {
    let output = Command::new("llvm-nm")
        .args(["--format=posix", "--extern-only", mode])
        .arg(path)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let mut names: Vec<_> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|l| l.split_whitespace().next().unwrap().to_owned())
        .collect();
    names.sort();
    names
}
fn symbols(path: &Path, mode: &str) -> Vec<String> {
    symbols_for(path, mode)
}
#[test]
fn native_darling_objects_preserve_exact_c_abi_and_staging() {
    check("x86_64-unknown-linux-gnu", "native");
}
#[test]
#[ignore = "requires installed x86_64-apple-darwin Rust target and LLVM tools"]
fn darwin_objects_preserve_exact_c_abi_and_staging() {
    check("x86_64-apple-darwin", "darwin");
}
