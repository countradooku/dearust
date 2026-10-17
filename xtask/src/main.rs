use std::{
    env,
    ffi::OsString,
    path::Path,
    process::{Command, ExitCode, ExitStatus},
};

const HELP: &str = "Usage: cargo xtask <build|check|test|bench|help>

  build  Build ported Rust tools in release mode
  check  Check formatting, lint all workspace targets, then run tests and doctests
  test   Run workspace tests and doctests
  bench  Benchmark the ported Mach-O metadata parser

All commands are offline and use Cargo.lock. Only migrated Rust components
are built; this command does not build or invoke the legacy CMake project.";

#[derive(Debug, PartialEq, Eq)]
enum Task {
    Build,
    Check,
    Test,
    Bench,
    Help,
}

fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Task, String> {
    let mut args = args.into_iter();
    let first = args.next();
    if args.next().is_some() {
        return Err("expected exactly one task; additional arguments are not supported".into());
    }
    match first.as_deref().and_then(|arg| arg.to_str()) {
        None if first.is_none() => Ok(Task::Help),
        Some("help" | "--help" | "-h") => Ok(Task::Help),
        Some("build") => Ok(Task::Build),
        Some("check") => Ok(Task::Check),
        Some("test") => Ok(Task::Test),
        Some("bench") => Ok(Task::Bench),
        _ => Err(format!("unknown task: {:?}", first.unwrap())),
    }
}

fn commands(task: &Task) -> Vec<Vec<&'static str>> {
    let test = vec!["test", "--workspace", "--offline", "--locked"];
    match task {
        Task::Build => vec![vec![
            "build",
            "--package",
            "darling-buildtools",
            "--release",
            "--offline",
            "--locked",
        ]],
        Task::Check => vec![
            vec!["fmt", "--all", "--", "--check"],
            vec![
                "clippy",
                "--workspace",
                "--all-targets",
                "--offline",
                "--locked",
                "--",
                "-D",
                "warnings",
            ],
            test,
        ],
        Task::Test => vec![test],
        Task::Bench => vec![vec![
            "bench",
            "--package",
            "darling-buildtools",
            "--bench",
            "metadata",
            "--offline",
            "--locked",
        ]],
        Task::Help => vec![],
    }
}

fn run(command: &mut Command) -> Result<(), ExitCode> {
    match command.status() {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => {
            eprintln!("xtask: command failed: {status}");
            Err(exit_code(status))
        }
        Err(error) => {
            eprintln!("xtask: could not start command: {error}");
            Err(ExitCode::FAILURE)
        }
    }
}

fn exit_code(status: ExitStatus) -> ExitCode {
    status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .filter(|code| *code != 0)
        .map_or(ExitCode::FAILURE, ExitCode::from)
}

fn main() -> ExitCode {
    let task = match parse(env::args_os().skip(1)) {
        Ok(task) => task,
        Err(error) => {
            eprintln!("xtask: {error}\n\n{HELP}");
            return ExitCode::from(2);
        }
    };
    if task == Task::Help {
        println!("{HELP}");
        return ExitCode::SUCCESS;
    }
    let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("xtask is a workspace member");
    for args in commands(&task) {
        eprintln!("+ cargo {}", args.join(" "));
        if let Err(code) = run(Command::new(&cargo).current_dir(root).args(args)) {
            return code;
        }
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_or_extra_arguments() {
        assert!(parse(["legacy".into()]).is_err());
        assert!(parse(["test".into(), "--release".into()]).is_err());
        assert_eq!(parse([]), Ok(Task::Help));
        assert_eq!(parse(["check".into()]), Ok(Task::Check));
    }

    #[test]
    fn check_tests_the_workspace_and_doctests_without_running_benchmarks() {
        let plan = commands(&Task::Check);
        assert_eq!(plan.len(), 3);
        assert_eq!(plan[0], ["fmt", "--all", "--", "--check"]);
        assert_eq!(plan[2], ["test", "--workspace", "--offline", "--locked"]);
        assert!(!plan[2].contains(&"--all-targets"));
    }

    #[test]
    fn spawn_errors_fail_the_task() {
        assert!(run(&mut Command::new("/nonexistent/darling-xtask-test-command")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn forwards_failure_codes_and_signal_failures() {
        use std::os::unix::process::ExitStatusExt;
        assert_eq!(exit_code(ExitStatus::from_raw(42 << 8)), ExitCode::from(42));
        assert_eq!(exit_code(ExitStatus::from_raw(9)), ExitCode::FAILURE);
    }
}
