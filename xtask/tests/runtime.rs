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
    let names = [
        "libsimple",
        "futex",
        "errno",
        "guarded",
        "lstat",
        "fstatat",
        "process",
        "symlinkat",
        "readlinkat",
        "unlinkat",
        "linkat",
        "renameat",
    ];
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
            "unlinkat" => vec![format!("{prefix}sys_unlinkat")],
            "linkat" => vec![format!("{prefix}sys_linkat")],
            "renameat" => vec![format!("{prefix}sys_renameat")],
            "readlinkat" => vec![format!("{prefix}sys_readlinkat")],
            "symlinkat" => vec![format!("{prefix}sys_symlinkat")],
            "process" => vec![format!("{prefix}sys_proc_info")],
            "fstatat" => ["sys_fstatat", "sys_fstatat64"]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect(),
            "lstat" => ["sys_lstat", "sys_lstat64"]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect(),
            "guarded" => [
                "guard_table_add",
                "guard_table_modify",
                "guard_table_remove",
                "guard_table_check",
                "guard_table_postfork_child",
                "guard_table_prefork",
                "guard_table_postfork_parent",
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
            "lstat" | "fstatat" => {
                let mut imports = vec![
                    "linux_syscall",
                    "stat_linux_to_bsd",
                    "stat_linux_to_bsd64",
                    "vchroot_expand",
                    if triple.contains("apple") {
                        "__bzero"
                    } else {
                        "memset"
                    },
                ]
                .into_iter()
                .map(|s| format!("{prefix}{s}"))
                .collect::<Vec<_>>();
                if *name == "fstatat" {
                    imports.extend([
                        format!("{prefix}atfd"),
                        format!("{prefix}atflags_bsd_to_linux"),
                    ]);
                } else {
                    imports.push(format!("{prefix}dearust_get_perthread_wd"));
                }
                imports.sort();
                imports
            }
            "symlinkat" | "readlinkat" | "unlinkat" | "linkat" | "renameat" => {
                let mut imports = [
                    "atfd",
                    "vchroot_expand",
                    "linux_syscall",
                    if triple.contains("apple") {
                        "__bzero"
                    } else {
                        "memset"
                    },
                ]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect::<Vec<_>>();
                if matches!(*name, "unlinkat" | "linkat") {
                    imports.push(format!("{prefix}atflags_bsd_to_linux"));
                }
                imports.sort();
                imports
            }
            "process" => {
                let mut imports = [
                    "darling_legacy_proc_info",
                    "dserver_rpc_get_executable_path",
                    "dserver_rpc_task_is_64_bit",
                    "errno_linux_to_bsd",
                    "linux_syscall",
                    "mach_port_deallocate",
                    "mach_task_self",
                    if triple.contains("apple") {
                        "memcmp"
                    } else {
                        "bcmp"
                    },
                    "memcpy",
                    "native_sysconf",
                    "oflags_linux_to_bsd",
                    "sys_getgid",
                    "sys_getuid",
                    "task_info",
                    "task_name_for_pid",
                    "vchroot_expand",
                    "vchroot_unexpand",
                    if triple.contains("apple") {
                        "__bzero"
                    } else {
                        "memset"
                    },
                ]
                .iter()
                .map(|s| format!("{prefix}{s}"))
                .collect::<Vec<_>>();
                imports.sort();
                imports
            }
            "guarded" => vec![
                format!("{prefix}close_internal"),
                format!("{prefix}libsimple_lock_lock"),
                format!("{prefix}libsimple_lock_unlock"),
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
    assert_eq!(members.stdout, b"dearust_simple.o\n");
    let manifest = fs::read(stage.join("darling-runtime-artifacts.tsv")).unwrap();
    assert!(String::from_utf8_lossy(&manifest).starts_with(&format!("# target {triple}\n")));
    if triple.contains("apple") {
        for (name, count) in [
            ("errno", 2),
            ("guarded", 7),
            ("lstat", 2),
            ("fstatat", 2),
            ("process", 1),
            ("symlinkat", 1),
            ("readlinkat", 1),
            ("unlinkat", 1),
            ("linkat", 1),
            ("renameat", 1),
        ] {
            let private = Command::new("llvm-nm")
                .arg("-m")
                .arg(stage.join(format!("objects/{name}.o")))
                .output()
                .unwrap();
            assert!(private.status.success());
            assert_eq!(
                String::from_utf8_lossy(&private.stdout)
                    .lines()
                    .filter(|l| l.contains("private external"))
                    .count(),
                count
            );
        }
    }
    if !triple.contains("apple") {
        for name in [
            "lstat",
            "fstatat",
            "symlinkat",
            "readlinkat",
            "unlinkat",
            "linkat",
            "renameat",
        ] {
            let driver = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join(format!("../tools/fixtures/{name}-native.c"));
            let executable = temp.0.join(format!("{name}-native"));
            let compile = Command::new("clang")
                .args(["-O2", "-Wall", "-Wextra", "-Werror"])
                .arg(driver)
                .arg(stage.join(format!("objects/{name}.o")))
                .arg("-o")
                .arg(&executable)
                .output()
                .unwrap();
            assert!(
                compile.status.success(),
                "{}",
                String::from_utf8_lossy(&compile.stderr)
            );
            let result = Command::new(executable).output().unwrap();
            assert!(
                result.status.success(),
                "{}",
                String::from_utf8_lossy(&result.stderr)
            );
        }
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
