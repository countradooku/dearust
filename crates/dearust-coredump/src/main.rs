// SPDX-License-Identifier: GPL-3.0-or-later
#![forbid(unsafe_code)]
use std::path::PathBuf;
fn main() {
    let mut args = std::env::args_os();
    let program = args.next().unwrap_or_default();
    let Some(input) = args.next().map(PathBuf::from) else {
        eprintln!(
            "Usage: {} <input-coredump> [output-coredump]",
            program.to_string_lossy()
        );
        std::process::exit(1);
    };
    let output = args.next().map(PathBuf::from).unwrap_or_else(|| {
        let mut name = std::ffi::OsString::from("darlingcore-");
        name.push(input.file_name().unwrap_or_default());
        input
            .parent()
            .unwrap_or_else(|| std::path::Path::new("."))
            .join(name)
    });
    if let Err(error) = dearust_coredump::convert_file(&input, &output, &Default::default()) {
        eprintln!("darling-coredump: {error}");
        std::process::exit(1);
    }
}
