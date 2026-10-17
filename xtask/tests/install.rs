//! Exercises the actual Cargo build graph and resulting runnable artifacts.
use std::{fs, path::PathBuf, process::Command};

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn cargo_graph_stages_runnable_tools_and_a_repeatable_manifest() {
    let directory = std::env::temp_dir().join(format!(
        "darling-install-integration-{}",
        std::process::id()
    ));
    fs::create_dir(&directory).unwrap();
    let temp = Temp(directory);
    // Spaces exercise native argument handling, without a shell.
    let stage = temp.0.join("host tools");
    let install = || {
        Command::new(env!("CARGO_BIN_EXE_xtask"))
            .args(["install", "--destdir"])
            .arg(&stage)
            .env("CARGO_TARGET_DIR", temp.0.join("wrong-target-directory"))
            .output()
            .unwrap()
    };
    let output = install();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!temp.0.join("wrong-target-directory").exists());
    let manifest = fs::read(stage.join("darling-host-artifacts.tsv")).unwrap();
    assert_eq!(manifest, include_bytes!("../host-artifacts.tsv"));

    let source = temp.0.join("source");
    fs::write(&source, b"linked contents").unwrap();
    let linked = temp.0.join("linked");
    assert!(
        Command::new(stage.join("src/bsdln/bsdln"))
            .arg(&source)
            .arg(&linked)
            .status()
            .unwrap()
            .success()
    );
    assert_eq!(fs::read(&linked).unwrap(), b"linked contents");

    let object = temp.0.join("uuid.macho");
    let mut macho = Vec::new();
    for word in [0xfeed_facfu32, 0x0100_0007, 3, 2, 1, 24, 0, 0, 0x1b, 24] {
        macho.extend_from_slice(&word.to_le_bytes());
    }
    macho.extend(0u8..16);
    fs::write(&object, macho).unwrap();
    let uuid = Command::new(stage.join("src/buildtools/getuuid"))
        .arg(&object)
        .output()
        .unwrap();
    assert!(uuid.status.success());
    assert!(String::from_utf8_lossy(&uuid.stdout).contains("00010203-0405-0607-0809-0A0B0C0D0E0F"));
    let dependencies = Command::new(stage.join("src/buildtools/elfdep"))
        .arg(&object)
        .output()
        .unwrap();
    assert!(dependencies.status.success());
    assert!(dependencies.stdout.is_empty());

    let names = Command::new(stage.join("src/libelfloader/wrapgen/print_wrapped_elf"))
        .arg(&object)
        .output()
        .unwrap();
    assert!(names.status.success());
    assert!(names.stdout.is_empty());
    for tool in ["src/libelfloader/wrapgen/wrapgen", "bin/darling-coredump"] {
        let result = Command::new(stage.join(tool)).output().unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(String::from_utf8_lossy(&result.stderr).contains("Usage:"));
    }

    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    check_staged_runtime(&stage, &temp.0);

    let errno = fs::read(stage.join("lib/darling/native/dearust_errno.o")).unwrap();
    assert!(install().status.success());
    assert_eq!(
        fs::read(stage.join("lib/darling/native/dearust_errno.o")).unwrap(),
        errno
    );
    assert_eq!(
        fs::read(stage.join("darling-host-artifacts.tsv")).unwrap(),
        manifest
    );
}

#[cfg(unix)]
#[test]
fn failed_cargo_build_propagates_status_without_creating_stage() {
    use std::os::unix::fs::PermissionsExt;
    let directory =
        std::env::temp_dir().join(format!("darling-failed-install-{}", std::process::id()));
    fs::create_dir(&directory).unwrap();
    let temp = Temp(directory);
    let cargo = temp.0.join("cargo");
    // Test helper emulates Cargo's version query and a failed build. Production
    // orchestration invokes programs directly and never invokes a shell.
    fs::write(&cargo, "#!/bin/sh\nif [ \"$1\" = '-vV' ]; then printf 'host: x86_64-unknown-linux-gnu\\n'; exit 0; fi\nexit 42\n").unwrap();
    fs::set_permissions(&cargo, fs::Permissions::from_mode(0o755)).unwrap();
    let stage = temp.0.join("stage");
    let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["install", "--destdir"])
        .arg(&stage)
        .env("CARGO", cargo)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(42));
    assert!(!stage.exists());
}

#[cfg(unix)]
#[test]
fn late_object_build_failure_preserves_the_stage() {
    use std::os::unix::fs::PermissionsExt;
    let directory =
        std::env::temp_dir().join(format!("darling-object-failure-{}", std::process::id()));
    fs::create_dir(&directory).unwrap();
    let temp = Temp(directory);
    let cargo = temp.0.join("cargo");
    // Ordinary package builds succeed; the later runtime object build fails.
    fs::write(&cargo, "#!/bin/sh\nif [ \"$1\" = '-vV' ]; then printf 'host: x86_64-unknown-linux-gnu\\n'; exit 0; fi\nif [ \"$1\" = 'rustc' ]; then exit 43; fi\nexit 0\n").unwrap();
    fs::set_permissions(&cargo, fs::Permissions::from_mode(0o755)).unwrap();
    let stage = temp.0.join("stage");
    fs::create_dir(&stage).unwrap();
    let manifest = stage.join("darling-host-artifacts.tsv");
    fs::write(&manifest, "previous manifest").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["install", "--destdir"])
        .arg(&stage)
        .env("CARGO", cargo)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(43), "{output:?}");
    assert_eq!(fs::read_to_string(manifest).unwrap(), "previous manifest");
    assert_eq!(fs::read_dir(stage).unwrap().count(), 1);
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn check_staged_runtime(stage: &std::path::Path, temporary: &std::path::Path) {
    let xtask = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let executable = temporary.join("libsimple-c-stress");
    let archive = stage.join("src/libsimple-linux/liblibsimple_darlingserver.a");
    let members = Command::new("ar").arg("t").arg(&archive).output().unwrap();
    assert!(members.status.success());
    for member in String::from_utf8(members.stdout).unwrap().lines() {
        for runtime in ["std-", "alloc-", "panic_unwind-", "unwind-"] {
            assert!(
                !member.starts_with(runtime),
                "freestanding archive contains {member}"
            );
        }
    }
    let errno = stage.join("lib/darling/native/dearust_errno.o");
    let bytes = fs::read(&errno).unwrap();
    assert_eq!(&bytes[..4], b"\x7fELF", "stage native ELF, not LTO bitcode");
    let undefined = Command::new("nm").arg("-u").arg(&errno).output().unwrap();
    assert!(undefined.status.success());
    assert!(
        undefined.stdout.is_empty(),
        "errno object must need no runtime: {undefined:?}"
    );
    let symbols = Command::new("readelf")
        .arg("-sW")
        .arg(&errno)
        .output()
        .unwrap();
    assert!(symbols.status.success());
    let symbols = String::from_utf8(symbols.stdout).unwrap();
    let globals: std::collections::BTreeSet<_> = symbols
        .lines()
        .filter(|line| line.split_whitespace().nth(4) == Some("GLOBAL"))
        .filter_map(|line| line.split_whitespace().last())
        .collect();
    assert_eq!(
        globals,
        ["errno_linux_to_bsd", "length_of_translation_array"]
            .into_iter()
            .collect()
    );
    for name in ["errno_linux_to_bsd", "length_of_translation_array"] {
        assert!(
            symbols
                .lines()
                .any(|line| line.contains("GLOBAL HIDDEN") && line.ends_with(name)),
            "original hidden global missing: {symbols}"
        );
    }
    let compiler = Command::new("cc")
        .args([
            "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-isystem",
        ])
        // Preserve the upstream header verbatim. Its always_inline declarations
        // trigger GCC warnings, so treat the imported headers as system headers.
        .arg(xtask.join("../crates/dearust-libsimple/include"))
        .arg("-I")
        .arg(xtask.join("../crates/dearust-errno/include"))
        .arg(xtask.join("tests/fixtures/staged-libsimple.c"))
        .arg(&archive)
        .arg(&errno)
        .arg("-o")
        .arg(&executable)
        .output()
        .expect("native C compiler is required for staged ABI verification");
    assert!(
        compiler.status.success(),
        "{}",
        String::from_utf8_lossy(&compiler.stderr)
    );
    let output = Command::new(executable).output().unwrap();
    assert!(output.status.success(), "staged C ABI harness: {output:?}");
    assert_eq!(output.stdout, b"staged libsimple C ABI stress passed\n");
}
