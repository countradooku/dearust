// SPDX-License-Identifier: GPL-3.0-or-later
#![forbid(unsafe_code)]
use std::{
    env,
    path::{Path, PathBuf},
    process::ExitCode,
};

// Canonicalize the parent even when the output does not yet exist. This detects
// aliases such as out and ./out, including symlinked parent directories. Refuse
// output symlinks rather than following dangling links to an unchecked alias.
fn output_path(path: &Path) -> std::io::Result<PathBuf> {
    let filename = path.file_name().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "output must name a file")
    })?;
    match std::fs::symlink_metadata(path) {
        Ok(meta) if !meta.is_file() || meta.is_symlink() => {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "output must be a regular file, not a symlink or directory",
            ));
        }
        Ok(_) => (),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
        Err(error) => return Err(error),
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    Ok(parent.canonicalize()?.join(filename))
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("Usage: wrapgen <library-path> <output-file> <var-access-header>".into());
    }
    let path = dearust::elf_wrapper::resolve_library(&args[0])?;
    let data = dearust::read_input(&path)?;
    let library = dearust::elf_wrapper::Library::parse(&data)?;
    let fallback = path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid library filename")?;
    let (source, header) = library.generate(fallback)?;
    let output = output_path(Path::new(&args[1]))?;
    let header_path = output_path(Path::new(&args[2]))?;
    // Validate all aliases before truncating either output, including hard links.
    let input_meta = std::fs::metadata(&path)?;
    for candidate in [&output, &header_path] {
        if let Ok(meta) = std::fs::metadata(candidate) {
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if meta.dev() == input_meta.dev() && meta.ino() == input_meta.ino() {
                    return Err("output aliases input library".into());
                }
            }
        }
    }
    #[cfg(unix)]
    if let (Ok(a), Ok(b)) = (std::fs::metadata(&output), std::fs::metadata(&header_path)) {
        use std::os::unix::fs::MetadataExt;
        if a.dev() == b.dev() && a.ino() == b.ino() {
            return Err("source and header outputs alias".into());
        }
    }
    if output == header_path
        || (output
            .canonicalize()
            .ok()
            .zip(header_path.canonicalize().ok())
            .is_some_and(|(a, b)| a == b))
    {
        return Err("source and header outputs alias".into());
    }
    if let Some(header) = header {
        std::fs::write(header_path, header)?;
    }
    std::fs::write(output, source)?;
    Ok(())
}
fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("wrapgen: {e}");
            ExitCode::FAILURE
        }
    }
}
