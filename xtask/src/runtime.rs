//! Explicit native/Darling objects; no standard library or panic runtime bundled.
use crate::install::{self, Artifact, Error, Kind};
use std::{
    collections::BTreeSet,
    ffi::OsStr,
    fs, io,
    path::{Component, Path, PathBuf},
    process::Command,
};
const GRAPH: &str = include_str!("../runtime-artifacts.tsv");
const MANIFEST: &str = "darling-runtime-artifacts.tsv";

struct Spec<'a> {
    artifact: Artifact<'a>,
    features: &'a str,
}
fn graph() -> io::Result<Vec<Spec<'static>>> {
    let mut destinations = BTreeSet::new();
    let mut sources = BTreeSet::new();
    GRAPH
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(|line| {
            let f: Vec<_> = line.split('\t').collect();
            if f.len() != 7 || f.iter().any(|s| s.is_empty()) {
                return Err(invalid("invalid runtime graph record"));
            }
            let name = |s: &str| {
                !s.is_empty()
                    && s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
            };
            if !name(f[1])
                || !name(f[2])
                || (f[3] != "-" && !f[3].split(',').all(name))
                || !matches!(f[4], "default" | "private")
            {
                return Err(invalid("invalid runtime target/features/visibility"));
            }
            for path in &f[5..7] {
                if !Path::new(path)
                    .components()
                    .all(|part| matches!(part, Component::Normal(_)))
                {
                    return Err(invalid("runtime paths must be relative without traversal"));
                }
            }
            if !destinations.insert(f[5]) || !sources.insert(f[1]) {
                return Err(invalid("duplicate runtime artifact"));
            }
            if f[4] == "private"
                && !matches!(
                    f[2],
                    "dearust-errno"
                        | "dearust-process"
                        | "dearust-guarded"
                        | "dearust-lstat"
                        | "dearust-fstatat"
                        | "dearust-symlinkat"
                        | "dearust-readlinkat"
                )
            {
                return Err(invalid("unknown runtime private-symbol policy"));
            }
            let kind = match f[0] {
                "object" => Kind::Object,
                "staticlib" => Kind::Staticlib,
                _ => return Err(invalid("invalid runtime kind")),
            };
            Ok(Spec {
                artifact: Artifact {
                    kind,
                    target: f[1],
                    package: f[2],
                    destination: f[5],
                    hidden_symbols: if f[4] == "private" {
                        match f[2] {
                            "dearust-process" => vec!["sys_proc_info"],
                            "dearust-readlinkat" => vec!["sys_readlinkat"],
                            "dearust-symlinkat" => vec!["sys_symlinkat"],
                            "dearust-fstatat" => vec!["sys_fstatat", "sys_fstatat64"],
                            "dearust-lstat" => vec!["sys_lstat", "sys_lstat64"],
                            "dearust-guarded" => vec![
                                "guard_table_add",
                                "guard_table_modify",
                                "guard_table_remove",
                                "guard_table_check",
                                "guard_table_postfork_child",
                                "guard_table_prefork",
                                "guard_table_postfork_parent",
                            ],
                            _ => vec!["errno_linux_to_bsd", "length_of_translation_array"],
                        }
                    } else {
                        vec![]
                    },
                },
                features: f[3],
            })
        })
        .collect()
}
fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn run(command: &mut Command) -> Result<(), Error> {
    eprintln!("+ {command:?}");
    let status = command.status()?;
    if status.success() {
        Ok(())
    } else {
        Err(Error {
            message: "runtime artifact command failed".into(),
            code: crate::exit_code(status),
        })
    }
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub fn build_and_stage(
    cargo: &OsStr,
    root: &Path,
    triple: &str,
    destination: &Path,
) -> Result<(), Error> {
    install::validate_destination(destination)?;
    if !matches!(triple, "x86_64-unknown-linux-gnu" | "x86_64-apple-darwin") {
        return Err(invalid("unsupported runtime target").into());
    }
    let specs = graph()?;
    let target = root.join("target/runtime-slices");
    fs::create_dir_all(&target)?;
    let scratch = target.join(format!(".runtime-{}", std::process::id()));
    fs::create_dir(&scratch)?;
    let scratch = Scratch(scratch);
    let outputs = scratch.0.join("objects");
    fs::create_dir(&outputs)?;
    for spec in specs.iter().filter(|s| s.artifact.kind == Kind::Object) {
        let artifact = &spec.artifact;
        let object = outputs.join(artifact.source_name());
        let mut command = Command::new(cargo);
        command
            .current_dir(root)
            .args([
                "rustc",
                "--package",
                artifact.package,
                "--lib",
                "--crate-type",
                "rlib",
                "--release",
                "--no-default-features",
                "--offline",
                "--locked",
                "--config",
                "profile.release.lto=false",
                "--config",
                "profile.release.panic=\"abort\"",
                "--target",
                triple,
                "--target-dir",
            ])
            .arg(scratch.0.join(artifact.target));
        if spec.features != "-" {
            command.args(["--features", spec.features]);
        }
        command
            .args([
                "--",
                "-C",
                "codegen-units=1",
                "-C",
                "relocation-model=pic",
                "-C",
                "embed-bitcode=no",
            ])
            .arg(format!("--emit=obj={}", object.display()));
        run(&mut command)?;
        let mut bytes = fs::read(&object)?;
        if triple == "x86_64-apple-darwin" {
            if bytes.get(..4) != Some(&[0xcf, 0xfa, 0xed, 0xfe])
                || u32_at(&bytes, 4)? != 0x0100_0007
                || u32_at(&bytes, 12)? != 1
            {
                return Err(invalid("expected x86_64 Mach-O object").into());
            }
            private_extern(&mut bytes, &artifact.hidden_symbols)?;
            fs::write(&object, bytes)?;
        } else {
            if bytes.get(..6) != Some(b"\x7fELF\x02\x01")
                || bytes.get(16..20) != Some(&[1, 0, 62, 0])
            {
                return Err(invalid("expected x86_64 ELF object").into());
            }
            if !artifact.hidden_symbols.is_empty() {
                let mut visibility = Command::new("llvm-objcopy");
                for symbol in &artifact.hidden_symbols {
                    visibility.arg(format!("--set-symbol-visibility={symbol}=hidden"));
                }
                run(visibility.arg(&object))?;
            }
        }
    }
    for spec in specs.iter().filter(|s| s.artifact.kind == Kind::Staticlib) {
        let mut archive = Command::new("llvm-ar");
        archive
            .arg("rcsD")
            .arg(outputs.join(spec.artifact.source_name()));
        let mut count = 0;
        for member in specs.iter().filter(|s| {
            s.artifact.kind == Kind::Object && s.artifact.package == spec.artifact.package
        }) {
            archive.arg(outputs.join(member.artifact.source_name()));
            count += 1;
        }
        if count == 0 {
            return Err(invalid("runtime archive has no declared members").into());
        }
        run(&mut archive)?;
    }
    let artifacts: Vec<_> = specs.into_iter().map(|s| s.artifact).collect();
    let manifest = format!("# target {triple}\n{GRAPH}");
    install::stage_manifest(&outputs, destination, &artifacts, MANIFEST, &manifest)?;
    eprintln!(
        "Staged Darling runtime slice for {triple} at {}",
        destination.display()
    );
    Ok(())
}

fn u32_at(bytes: &[u8], offset: usize) -> io::Result<u32> {
    let end = offset
        .checked_add(4)
        .ok_or_else(|| invalid("Mach-O offset overflow"))?;
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..end)
            .ok_or_else(|| invalid("truncated Mach-O field"))?
            .try_into()
            .map_err(|_| invalid("invalid Mach-O word"))?,
    ))
}

/// LLVM objcopy currently ignores ELF visibility options on Mach-O. Preserve
/// the upstream private-extern ABI by setting only N_PEXT on requested defined
/// global nlist_64 records, retaining N_EXT and all offsets/section contents.
fn private_extern(bytes: &mut [u8], names: &[&str]) -> io::Result<()> {
    if names.is_empty() {
        return Ok(());
    }
    let commands = u32_at(bytes, 16)? as usize;
    let command_end = 32usize
        .checked_add(u32_at(bytes, 20)? as usize)
        .filter(|end| *end <= bytes.len())
        .ok_or_else(|| invalid("invalid Mach-O command extent"))?;
    if commands > (command_end - 32) / 8 {
        return Err(invalid("invalid Mach-O command count"));
    }
    let mut offset = 32;
    let mut symtab = None;
    for _ in 0..commands {
        let command = u32_at(bytes, offset)?;
        let size = u32_at(bytes, offset + 4)? as usize;
        if size < 8 || offset.checked_add(size).is_none_or(|end| end > command_end) {
            return Err(invalid("invalid Mach-O load command"));
        }
        if command == 2 {
            if size != 24 || symtab.is_some() {
                return Err(invalid("invalid Mach-O symbol command"));
            }
            symtab = Some((
                u32_at(bytes, offset + 8)? as usize,
                u32_at(bytes, offset + 12)? as usize,
                u32_at(bytes, offset + 16)? as usize,
                u32_at(bytes, offset + 20)? as usize,
            ));
        }
        offset += size;
    }
    if offset != command_end {
        return Err(invalid("Mach-O load-command size mismatch"));
    }
    let (symbols, count, strings, string_size) =
        symtab.ok_or_else(|| invalid("missing Mach-O symbol table"))?;
    if count > bytes.len() / 16
        || symbols
            .checked_add(count * 16)
            .is_none_or(|end| end > bytes.len())
        || strings
            .checked_add(string_size)
            .is_none_or(|end| end > bytes.len())
    {
        return Err(invalid("invalid Mach-O symbol extent"));
    }
    for name in names {
        let expected = format!("_{name}\0");
        let mut found = false;
        for index in 0..count {
            let record = symbols + index * 16;
            let string = u32_at(bytes, record)? as usize;
            if string
                .checked_add(expected.len())
                .is_none_or(|end| end > string_size)
            {
                continue;
            }
            if bytes.get(strings + string..strings + string + expected.len())
                == Some(expected.as_bytes())
            {
                let ty = bytes[record + 4];
                if ty & 0xe0 != 0 || ty & 1 == 0 || !matches!(ty & 0xe, 2 | 0xe) || found {
                    return Err(invalid("requested Mach-O symbol is not one defined global"));
                }
                bytes[record + 4] |= 0x10;
                found = true;
            }
        }
        if !found {
            return Err(invalid("requested private Mach-O symbol missing"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn private_extern_changes_only_the_named_defined_global_flag() {
        let mut object = vec![0; 32 + 24 + 32];
        object[16..20].copy_from_slice(&1u32.to_le_bytes());
        object[20..24].copy_from_slice(&24u32.to_le_bytes());
        for (offset, value) in [(32, 2u32), (36, 24), (40, 56), (44, 2), (48, 88), (52, 15)] {
            object[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        object[56..60].copy_from_slice(&1u32.to_le_bytes());
        object[60] = 0xf;
        object[72..76].copy_from_slice(&8u32.to_le_bytes());
        object[76] = 0xf;
        object.extend_from_slice(b"\0_first\0_second\0");
        let mut expected = object.clone();
        expected[60] |= 0x10;
        private_extern(&mut object, &["first"]).unwrap();
        assert_eq!(object, expected);
        private_extern(&mut object, &["first"]).unwrap();
        assert_eq!(object, expected);
        assert!(private_extern(&mut object, &["missing"]).is_err());
    }
    #[test]
    fn private_visibility_rejects_truncation_missing_symbols_and_bad_commands() {
        for length in 0..32 {
            assert!(private_extern(&mut vec![0; length], &["error"]).is_err());
        }
        let mut object = vec![0; 32];
        assert!(private_extern(&mut object, &["error"]).is_err());
        object[16..20].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(private_extern(&mut object, &["error"]).is_err());
    }
}
