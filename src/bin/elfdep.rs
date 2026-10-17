#![forbid(unsafe_code)]
fn main() -> std::process::ExitCode {
    match dearust::run("elfdep") {
        Ok(true) => std::process::ExitCode::SUCCESS,
        Ok(false) => std::process::ExitCode::FAILURE,
        Err(error) => {
            eprintln!("elfdep: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}
