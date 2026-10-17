//! Explicit Cargo graph for the migrated host slice, not the Darling runtime.
use std::{
    collections::BTreeSet,
    ffi::OsStr,
    fmt,
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Component, Path, PathBuf},
    process::{Command, ExitCode},
};

const GRAPH: &str = include_str!("../host-artifacts.tsv");
const MANIFEST: &str = "darling-host-artifacts.tsv";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Bin,
    Staticlib,
    Object,
}

struct Artifact<'a> {
    kind: Kind,
    target: &'a str,
    package: &'a str,
    destination: &'a str,
    hidden_symbols: Vec<&'a str>,
}

impl Artifact<'_> {
    fn source_name(&self) -> String {
        match self.kind {
            Kind::Bin => format!("{}{}", self.target, std::env::consts::EXE_SUFFIX),
            Kind::Staticlib => format!("lib{}.a", self.target.replace('-', "_")),
            Kind::Object => format!("{}.o", self.target.replace('-', "_")),
        }
    }
}

#[derive(Debug)]
pub struct Error {
    message: String,
    code: ExitCode,
}

impl Error {
    pub fn code(&self) -> ExitCode {
        self.code
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(f)
    }
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self {
            message: error.to_string(),
            code: ExitCode::FAILURE,
        }
    }
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

fn graph() -> io::Result<Vec<Artifact<'static>>> {
    parse_graph(GRAPH)
}

fn parse_graph(text: &str) -> io::Result<Vec<Artifact<'_>>> {
    let mut destinations = BTreeSet::new();
    let mut sources = BTreeSet::new();
    text.lines()
        .filter(|line| !line.starts_with('#') && !line.is_empty())
        .map(|line| {
            let fields: Vec<_> = line.split('\t').collect();
            if !(5..=6).contains(&fields.len()) || fields.iter().any(|value| value.is_empty()) {
                return Err(invalid("invalid host artifact graph record"));
            }
            for name in &fields[1..3] {
                if !name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
                {
                    return Err(invalid("invalid Cargo target or package name"));
                }
            }
            for path in &fields[3..5] {
                if !Path::new(path)
                    .components()
                    .all(|part| matches!(part, Component::Normal(_)))
                {
                    return Err(invalid(
                        "host artifact graph paths must be relative without traversal",
                    ));
                }
            }
            if !destinations.insert(fields[3]) {
                return Err(invalid("duplicate host artifact destination"));
            }
            let kind = match fields[0] {
                "bin" => Kind::Bin,
                "staticlib" => Kind::Staticlib,
                "object" => Kind::Object,
                _ => return Err(invalid("unsupported host artifact kind")),
            };
            let hidden_symbols: Vec<_> = fields
                .get(5)
                .map_or(Vec::new(), |value| value.split(',').collect());
            if (kind == Kind::Object) != !hidden_symbols.is_empty()
                || hidden_symbols.iter().any(|name| {
                    name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
                })
            {
                return Err(invalid(
                    "only object artifacts declare hidden C ABI symbols",
                ));
            }
            let artifact = Artifact {
                kind,
                target: fields[1],
                package: fields[2],
                destination: fields[3],
                hidden_symbols,
            };
            if !sources.insert(artifact.source_name()) {
                return Err(invalid("duplicate host artifact source filename"));
            }
            Ok(artifact)
        })
        .collect()
}

/// An explicit --target-dir prevents CARGO_TARGET_DIR and Cargo config from
/// silently changing artifact discovery. --target fixes the host even if the
/// workspace is configured to cross-compile Darwin components later.
fn host_triple(cargo: &OsStr, root: &Path) -> Result<String, Error> {
    let output = Command::new(cargo)
        .current_dir(root)
        .args(["-vV"])
        .output()?;
    if !output.status.success() {
        return Err(Error {
            message: "cargo -vV failed".into(),
            code: super::exit_code(output.status),
        });
    }
    let version =
        String::from_utf8(output.stdout).map_err(|_| invalid("Cargo version is not UTF-8"))?;
    let triple = version
        .lines()
        .find_map(|line| line.strip_prefix("host: "))
        .filter(|value| {
            !value.is_empty()
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        })
        .ok_or_else(|| invalid("Cargo did not report a valid host triple"))?;
    Ok(triple.into())
}

pub fn build_and_stage(cargo: &OsStr, root: &Path, destination: &Path) -> Result<(), Error> {
    let artifacts = graph()?;
    // Validate destination before starting an expensive build or mutating it.
    validate_destination(destination)?;
    let triple = host_triple(cargo, root)?;
    let target_dir = root.join("target/host-tools");
    for package in artifacts
        .iter()
        .filter(|artifact| artifact.kind != Kind::Object)
        .map(|artifact| artifact.package)
        .collect::<BTreeSet<_>>()
    {
        let mut command = Command::new(cargo);
        command
            .current_dir(root)
            .args([
                "build",
                "--package",
                package,
                "--release",
                "--offline",
                "--locked",
                "--target",
                &triple,
                "--target-dir",
            ])
            .arg(&target_dir);
        // Declare every executable/library rather than discovering target files.
        for artifact in artifacts
            .iter()
            .filter(|artifact| artifact.package == package && artifact.kind != Kind::Object)
        {
            match artifact.kind {
                Kind::Bin => {
                    command.args(["--bin", artifact.target]);
                }
                Kind::Staticlib => {
                    command.args([
                        "--lib",
                        "--no-default-features",
                        "--config",
                        "profile.release.panic=\"abort\"",
                    ]);
                }
                Kind::Object => unreachable!("object packages use the dedicated build below"),
            }
        }
        eprintln!("+ {command:?}");
        let status = command.status()?;
        if !status.success() {
            return Err(Error {
                message: "host artifact build failed".into(),
                code: super::exit_code(status),
            });
        }
    }
    let release = target_dir.join(&triple).join("release");
    for artifact in artifacts
        .iter()
        .filter(|artifact| artifact.kind == Kind::Object)
    {
        build_object(cargo, root, &triple, &target_dir, &release, artifact)?;
    }
    stage(&release, destination, &artifacts)?;
    eprintln!(
        "Staged {} host artifacts at {} (remaining Darling graph is unported)",
        artifacts.len(),
        destination.display()
    );
    Ok(())
}

/// Build a single no_std C ABI object without bundling a panic runtime. A fresh
/// private Cargo directory prevents a cached rlib from hiding a missing custom
/// --emit=obj output: Cargo does not track that side output as a normal artifact.
fn build_object(
    cargo: &OsStr,
    root: &Path,
    triple: &str,
    target_dir: &Path,
    release: &Path,
    artifact: &Artifact<'_>,
) -> Result<(), Error> {
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fs::create_dir_all(target_dir)?;
    let scratch = target_dir.join(format!(
        ".object-{}-{}",
        artifact.target,
        std::process::id()
    ));
    fs::create_dir(&scratch)?;
    let scratch = Scratch(scratch);
    let object = scratch.0.join(artifact.source_name());
    let mut command = Command::new(cargo);
    command
        .current_dir(root)
        .args([
            "rustc",
            "--package",
            artifact.package,
            "--lib",
            "--release",
            "--offline",
            "--locked",
            "--config",
            "profile.release.lto=false",
            "--target",
            triple,
            "--target-dir",
        ])
        .arg(scratch.0.join("cargo"))
        .args([
            "--",
            "-C",
            "lto=off",
            "-C",
            "codegen-units=1",
            "-C",
            "relocation-model=pic",
            "-C",
            "embed-bitcode=no",
            "-C",
            "panic=abort",
        ])
        .arg(format!("--emit=obj={}", object.display()));
    eprintln!("+ {command:?}");
    let status = command.status()?;
    if !status.success() {
        return Err(Error {
            message: "runtime object build failed".into(),
            code: super::exit_code(status),
        });
    }
    // Stable Rust's C exports have default visibility. Match the original
    // emulation object's -fvisibility=hidden without changing global linkage.
    let mut visibility = Command::new("llvm-objcopy");
    for symbol in &artifact.hidden_symbols {
        visibility.arg(format!("--set-symbol-visibility={symbol}=hidden"));
    }
    visibility.arg(&object);
    eprintln!("+ {visibility:?}");
    let status = visibility.status()?;
    if !status.success() {
        return Err(Error {
            message: "runtime object visibility adjustment failed".into(),
            code: super::exit_code(status),
        });
    }
    fs::create_dir_all(release)?;
    // Rename replaces a previous file without writing through a symlink/hardlink.
    fs::rename(&object, release.join(artifact.source_name()))?;
    Ok(())
}

fn absolute(path: &Path) -> io::Result<PathBuf> {
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err(invalid(
            "destination must be nonempty and contain no parent traversal",
        ));
    }
    Ok(if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir()?.join(path)
    })
}

fn validate_destination(path: &Path) -> io::Result<()> {
    let path = absolute(path)?;
    let mut current = PathBuf::new();
    for part in path.components() {
        current.push(part);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.is_dir() && !metadata.is_symlink() => (),
            Ok(_) => {
                return Err(invalid(format!(
                    "destination component is not a real directory: {}",
                    current.display()
                )));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn regular_file_or_missing(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.is_symlink() => Ok(()),
        Ok(_) => Err(invalid(format!(
            "refusing non-regular artifact: {}",
            path.display()
        ))),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

/// Each file is published with rename, so existing hard links and symlinks are
/// never opened for writing. The manifest is published last. An I/O error during
/// publication can leave a partial update; callers must check the exit status.
/// Use an exclusively owned staging directory (no concurrent path mutation).
fn stage(release: &Path, destination: &Path, artifacts: &[Artifact<'_>]) -> io::Result<()> {
    let destination = absolute(destination)?;
    validate_destination(&destination)?;
    // Check every source and destination before touching the destination tree.
    for artifact in artifacts {
        let source = release.join(artifact.source_name());
        let metadata = fs::symlink_metadata(&source)?;
        if !metadata.is_file() || metadata.is_symlink() {
            return Err(invalid(format!(
                "build output is not a regular file: {}",
                source.display()
            )));
        }
        let output = destination.join(artifact.destination);
        validate_destination(output.parent().expect("artifact has parent"))?;
        regular_file_or_missing(&output)?;
    }
    regular_file_or_missing(&destination.join(MANIFEST))?;
    fs::create_dir_all(&destination)?;
    let temporary = destination.join(format!(".darling-stage-{}", std::process::id()));
    // Never reuse an existing path, including a symlink or stale transaction.
    fs::create_dir(&temporary)?;
    let result = (|| {
        for artifact in artifacts {
            fs::copy(
                release.join(artifact.source_name()),
                temporary.join(artifact.source_name()),
            )?;
        }
        let mut manifest = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(temporary.join(MANIFEST))?;
        manifest.write_all(GRAPH.as_bytes())?;
        manifest.sync_all()?;
        for artifact in artifacts {
            let output = destination.join(artifact.destination);
            fs::create_dir_all(output.parent().expect("artifact has parent"))?;
            fs::rename(temporary.join(artifact.source_name()), output)?;
        }
        fs::rename(temporary.join(MANIFEST), destination.join(MANIFEST))?;
        Ok(())
    })();
    let cleanup = fs::remove_dir_all(&temporary);
    result.and(cleanup)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "darling-stage-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture(temp: &Temp) -> PathBuf {
        let source = temp.0.join("release");
        fs::create_dir(&source).unwrap();
        for artifact in graph().unwrap() {
            fs::write(source.join(artifact.source_name()), artifact.target).unwrap();
        }
        source
    }

    #[test]
    fn stages_exact_layout_and_manifest_reproducibly() {
        let temp = Temp::new();
        let source = fixture(&temp);
        let dest = temp.0.join("stage");
        for _ in 0..2 {
            stage(&source, &dest, &graph().unwrap()).unwrap();
            assert_eq!(fs::read_to_string(dest.join(MANIFEST)).unwrap(), GRAPH);
            for artifact in graph().unwrap() {
                assert_eq!(
                    fs::read_to_string(dest.join(artifact.destination)).unwrap(),
                    artifact.target
                );
            }
            let top_directories: BTreeSet<_> = graph()
                .unwrap()
                .iter()
                .map(|a| a.destination.split('/').next().unwrap())
                .collect();
            assert_eq!(
                fs::read_dir(&dest).unwrap().count(),
                top_directories.len() + 1
            );
        }
    }

    #[test]
    fn missing_artifact_does_not_create_destination() {
        let temp = Temp::new();
        let source = fixture(&temp);
        fs::remove_file(source.join("getuuid")).unwrap();
        let dest = temp.0.join("stage");
        assert!(stage(&source, &dest, &graph().unwrap()).is_err());
        assert!(!dest.exists());
        assert!(absolute(Path::new("../escape")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_directories_and_files_without_touching_referents() {
        use std::os::unix::fs::symlink;
        let temp = Temp::new();
        let source = fixture(&temp);
        let dest = temp.0.join("stage");
        let outside = temp.0.join("outside");
        fs::create_dir(&outside).unwrap();
        fs::create_dir_all(dest.join("src")).unwrap();
        symlink(&outside, dest.join("src/bsdln")).unwrap();
        assert!(stage(&source, &dest, &graph().unwrap()).is_err());
        assert_eq!(fs::read_dir(&outside).unwrap().count(), 0);
        fs::remove_file(dest.join("src/bsdln")).unwrap();
        fs::create_dir(dest.join("src/bsdln")).unwrap();
        let victim = outside.join("victim");
        fs::write(&victim, "preserve").unwrap();
        symlink(&victim, dest.join("src/bsdln/bsdln")).unwrap();
        assert!(stage(&source, &dest, &graph().unwrap()).is_err());
        assert_eq!(fs::read_to_string(victim).unwrap(), "preserve");
    }

    #[cfg(unix)]
    #[test]
    fn replaces_hardlinked_destination_without_modifying_other_link() {
        use std::os::unix::fs::PermissionsExt;
        let temp = Temp::new();
        let source = fixture(&temp);
        fs::set_permissions(source.join("bsdln"), fs::Permissions::from_mode(0o755)).unwrap();
        let dest = temp.0.join("stage");
        fs::create_dir_all(dest.join("src/bsdln")).unwrap();
        let victim = temp.0.join("victim");
        fs::write(&victim, "preserve").unwrap();
        fs::hard_link(&victim, dest.join("src/bsdln/bsdln")).unwrap();
        stage(&source, &dest, &graph().unwrap()).unwrap();
        assert_eq!(fs::read_to_string(victim).unwrap(), "preserve");
        assert_eq!(
            fs::metadata(dest.join("src/bsdln/bsdln"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o755
        );
    }
}
