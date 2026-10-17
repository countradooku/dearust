#![forbid(unsafe_code)]
use std::io::Write;

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or("Usage: print_wrapped_elf <mach-o file>")?;
    if args.next().is_some() {
        return Err("Usage: print_wrapped_elf <mach-o file>".into());
    }
    let data = std::fs::read(path)?;
    let names = darling_buildtools::wrapped_elf::names(&data)?;
    let mut output = std::io::stdout().lock();
    for name in names {
        output.write_all(name)?;
        output.write_all(b"\n")?;
    }
    output.flush()?;
    Ok(())
}

fn main() -> std::process::ExitCode {
    match run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("print_wrapped_elf: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}
