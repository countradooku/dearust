use std::{
    env,
    ffi::OsString,
    path::{Path, PathBuf},
    process::{Command, ExitCode, ExitStatus},
};

mod install;
mod runtime;

const HELP: &str = "Usage: cargo xtask <build|install|runtime|check|test|bench|help>

  build  Build ported Rust tools in release mode
  install --destdir PATH  Build and stage host tools in upstream build-tree layout
  runtime --target TRIPLE --destdir PATH  Build no_std Darling runtime objects/archive
  check  Check formatting, lint all workspace targets, then run tests and doctests
  test   Run workspace tests and doctests
  bench  Benchmark the ported Mach-O metadata parser

All commands are offline and use Cargo.lock. Only migrated Rust components
are built; this command does not build or invoke the legacy CMake project.";

#[derive(Debug, PartialEq, Eq)]
enum Task {
    Build,
    Install(PathBuf),
    Runtime {
        target: String,
        destination: PathBuf,
    },
    Check,
    Test,
    Bench,
    Help,
}

fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Task, String> {
    let mut args = args.into_iter();
    let first = args.next();
    if first.as_deref() == Some(std::ffi::OsStr::new("runtime")) {
        if args.next().as_deref() != Some(std::ffi::OsStr::new("--target")) {
            return Err("runtime requires --target TRIPLE --destdir PATH".into());
        }
        let target = args
            .next()
            .and_then(|s| s.into_string().ok())
            .filter(|s| {
                matches!(
                    s.as_str(),
                    "x86_64-unknown-linux-gnu" | "x86_64-apple-darwin"
                )
            })
            .ok_or("runtime target must be x86_64-unknown-linux-gnu or x86_64-apple-darwin")?;
        if args.next().as_deref() != Some(std::ffi::OsStr::new("--destdir")) {
            return Err("runtime requires --destdir PATH".into());
        }
        let destination = args
            .next()
            .filter(|s| !s.is_empty())
            .ok_or("runtime requires nonempty PATH")?;
        if args.next().is_some() {
            return Err("unexpected runtime arguments".into());
        }
        return Ok(Task::Runtime {
            target,
            destination: destination.into(),
        });
    }
    if first.as_deref() == Some(std::ffi::OsStr::new("install")) {
        if args.next().as_deref() != Some(std::ffi::OsStr::new("--destdir")) {
            return Err("install requires --destdir PATH".into());
        }
        let path = args
            .next()
            .filter(|path| !path.is_empty())
            .ok_or("install requires a nonempty destination path")?;
        if args.next().is_some() {
            return Err("unexpected additional install arguments".into());
        }
        return Ok(Task::Install(path.into()));
    }
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
        Task::Build => vec![
            vec![
                "build",
                "--workspace",
                "--exclude",
                "xtask",
                "--release",
                "--offline",
                "--locked",
            ],
            vec![
                "build",
                "--package",
                "darling-libsimple",
                "--lib",
                "--release",
                "--no-default-features",
                "--config",
                "profile.release.panic=\"abort\"",
                "--offline",
                "--locked",
            ],
        ],
        Task::Install(_) | Task::Runtime { .. } => vec![],
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
    if let Task::Install(path) = &task {
        if let Err(error) = install::build_and_stage(&cargo, root, path) {
            eprintln!("xtask: {error}");
            return error.code();
        }
    }
    if let Task::Runtime {
        target,
        destination,
    } = &task
    {
        if let Err(error) = runtime::build_and_stage(&cargo, root, target, destination) {
            eprintln!("xtask: {error}");
            return error.code();
        }
    }
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
        assert_eq!(
            parse(["install".into(), "--destdir".into(), "host tools".into()]),
            Ok(Task::Install(PathBuf::from("host tools")))
        );
        assert_eq!(
            parse(
                [
                    "runtime",
                    "--target",
                    "x86_64-apple-darwin",
                    "--destdir",
                    "stage"
                ]
                .into_iter()
                .map(OsString::from)
            ),
            Ok(Task::Runtime {
                target: "x86_64-apple-darwin".into(),
                destination: "stage".into()
            })
        );
        for args in [
            vec!["runtime"],
            vec![
                "runtime",
                "--target",
                "i686-apple-darwin",
                "--destdir",
                "stage",
            ],
            vec![
                "runtime",
                "--target",
                "x86_64-apple-darwin",
                "--destdir",
                "",
            ],
            vec![
                "runtime",
                "--target",
                "x86_64-apple-darwin",
                "--destdir",
                "stage",
                "extra",
            ],
            vec!["install"],
            vec!["install", "--destdir"],
            vec!["install", "--destdir", ""],
            vec!["install", "--prefix", "stage"],
            vec!["install", "--destdir", "stage", "extra"],
        ] {
            assert!(parse(args.into_iter().map(OsString::from)).is_err());
        }
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
