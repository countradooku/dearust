// Rust port of Darling src/bsdln/ln.c. Original copyright (c) 1987, 1993,
// 1994 The Regents of the University of California. All rights reserved.
// See LICENSE-BSDLN for the retained license and disclaimer.
#![forbid(unsafe_code)]

use std::{
    ffi::OsString,
    fs,
    io::{self, Read, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{MetadataExt, symlink},
    },
    path::{Path, PathBuf},
    process::ExitCode,
};

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum Replace {
    #[default]
    Never,
    Force,
    Interactive,
}

#[derive(Default)]
struct Options {
    symbolic: bool,
    remove_directory: bool,
    no_dereference: bool,
    verbose: bool,
    replace: Replace,
}

fn usage() -> &'static str {
    "usage: ln [-Ffhinsv] source_file [target_file]\n       ln [-Ffhinsv] source_file ... target_dir\n       link source_file target_file"
}

fn parse() -> Result<(Options, Vec<PathBuf>), &'static str> {
    let mut args = std::env::args_os();
    let as_link = args
        .next()
        .as_deref()
        .and_then(|arg| Path::new(arg).file_name())
        .is_some_and(|arg| arg == "link");
    let mut options = Options::default();
    let mut operands = Vec::new();
    let mut flags = true;
    let posix = std::env::var_os("POSIXLY_CORRECT").is_some();
    for arg in args {
        let bytes = arg.as_bytes();
        if flags && bytes == b"--" {
            flags = false;
        } else if flags && bytes.starts_with(b"-") && bytes.len() > 1 {
            if as_link {
                return Err(usage());
            }
            for flag in &bytes[1..] {
                match flag {
                    b'F' => options.remove_directory = true,
                    b'f' => options.replace = Replace::Force,
                    b'i' => options.replace = Replace::Interactive,
                    b'h' | b'n' => options.no_dereference = true,
                    b's' => options.symbolic = true,
                    b'v' => options.verbose = true,
                    _ => return Err(usage()),
                }
            }
        } else {
            operands.push(PathBuf::from(arg));
            if posix {
                flags = false;
            }
        }
    }
    if operands.is_empty() || (as_link && operands.len() != 2) {
        return Err(usage());
    }
    options.remove_directory &= options.symbolic;
    if options.remove_directory && options.replace != Replace::Interactive {
        options.replace = Replace::Force;
    }
    Ok((options, operands))
}

fn diagnostic(path: &Path, error: impl std::fmt::Display) {
    eprintln!("bsdln: {}: {error}", path.display());
}

fn confirm(path: &Path) -> io::Result<bool> {
    let mut stderr = io::stderr().lock();
    stderr.write_all(b"replace ")?;
    stderr.write_all(path.as_os_str().as_bytes())?;
    stderr.write_all(b"? ")?;
    stderr.flush()?;
    let mut stdin = io::stdin().lock();
    let mut byte = [0];
    let mut first = None;
    while stdin.read(&mut byte)? != 0 {
        first.get_or_insert(byte[0]);
        if byte[0] == b'\n' {
            break;
        }
    }
    if matches!(first, Some(b'y' | b'Y')) {
        return Ok(true);
    }
    stderr.write_all(b"not replaced\n")?;
    Ok(false)
}

fn lstat(path: &Path) -> io::Result<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(Some(metadata)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn link_one(target: &Path, destination: &Path, directory: bool, options: &Options) -> bool {
    if !options.symbolic {
        match fs::metadata(target) {
            Ok(metadata) if metadata.is_dir() => {
                diagnostic(target, "Is a directory");
                return false;
            }
            Ok(_) => {}
            Err(error) => {
                diagnostic(target, error);
                return false;
            }
        }
    }
    let existing = match lstat(destination) {
        Ok(existing) => existing,
        Err(error) => {
            diagnostic(destination, error);
            return false;
        }
    };
    let directory = directory
        || existing.as_ref().is_some_and(fs::Metadata::is_dir)
        || (!options.no_dereference && fs::metadata(destination).is_ok_and(|m| m.is_dir()));
    let destination = if directory {
        // Path::file_name ignores trailing separators; POSIX basename("/") is "/".
        let name = basename(target);
        let mut destination = destination.as_os_str().to_os_string();
        destination.push("/");
        destination.push(name);
        PathBuf::from(destination)
    } else {
        destination.to_path_buf()
    };
    let result = (|| -> io::Result<bool> {
        let existing = lstat(&destination)?;
        if let Some(metadata) = existing {
            let replace = match options.replace {
                Replace::Never => false,
                Replace::Force => true,
                Replace::Interactive => confirm(&destination)?,
            };
            if options.replace == Replace::Interactive && !replace {
                return Ok(false);
            }
            if replace {
                if !options.symbolic {
                    let source = fs::symlink_metadata(target)?;
                    if source.dev() == metadata.dev() && source.ino() == metadata.ino() {
                        return Err(io::Error::other("source and destination are the same file"));
                    }
                }
                if options.remove_directory && metadata.is_dir() {
                    fs::remove_dir(&destination)?;
                } else {
                    fs::remove_file(&destination)?;
                }
            }
        }
        if options.symbolic {
            symlink(target, &destination)?;
        } else {
            fs::hard_link(target, &destination)?;
        }
        if options.verbose {
            let mut stdout = io::stdout().lock();
            stdout.write_all(destination.as_os_str().as_bytes())?;
            stdout.write_all(if options.symbolic { b" -> " } else { b" => " })?;
            stdout.write_all(target.as_os_str().as_bytes())?;
            stdout.write_all(b"\n")?;
        }
        Ok(true)
    })();
    match result {
        Ok(success) => success,
        Err(error) => {
            diagnostic(&destination, error);
            false
        }
    }
}

fn basename(path: &Path) -> OsString {
    let bytes = path.as_os_str().as_bytes();
    if bytes.is_empty() {
        return OsString::from(".");
    }
    let end = bytes
        .iter()
        .rposition(|&byte| byte != b'/')
        .map_or(0, |i| i + 1);
    if end == 0 {
        return OsString::from("/");
    }
    let start = bytes[..end]
        .iter()
        .rposition(|&byte| byte == b'/')
        .map_or(0, |i| i + 1);
    std::ffi::OsStr::from_bytes(&bytes[start..end]).to_os_string()
}

fn run() -> bool {
    let (options, operands) = match parse() {
        Ok(parsed) => parsed,
        Err(usage) => {
            eprintln!("{usage}");
            return false;
        }
    };
    match operands.as_slice() {
        [target] => link_one(target, Path::new("."), true, &options),
        [target, destination] => link_one(target, destination, false, &options),
        many => {
            let destination = &many[many.len() - 1];
            if options.no_dereference
                && fs::symlink_metadata(destination).is_ok_and(|m| m.file_type().is_symlink())
            {
                diagnostic(destination, "Not a directory");
                return false;
            }
            match fs::metadata(destination) {
                Ok(metadata) if metadata.is_dir() => {}
                Ok(_) => {
                    eprintln!("{}", usage());
                    return false;
                }
                Err(error) => {
                    diagnostic(destination, error);
                    return false;
                }
            }
            let mut success = true;
            for target in &many[..many.len() - 1] {
                success &= link_one(target, destination, true, &options);
            }
            success
        }
    }
}

fn main() -> ExitCode {
    if run() {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
