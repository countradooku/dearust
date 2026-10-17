// SPDX-License-Identifier: GPL-3.0-or-later
#![forbid(unsafe_code)]
use std::{env, path::PathBuf, process::ExitCode};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("Usage: wrapgen <library-path> <output-file> <var-access-header>".into());
    }
    let path = darling_buildtools::elf_wrapper::resolve_library(&args[0])?;
    let data = std::fs::read(&path)?;
    let library = darling_buildtools::elf_wrapper::Library::parse(&data)?;
    let fallback = path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid library filename")?;
    let (source, header) = library.generate(fallback)?;
    let output = PathBuf::from(&args[1]);
    let header_path = PathBuf::from(&args[2]);
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
