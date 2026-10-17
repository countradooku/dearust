use std::{
    ffi::OsString,
    fs,
    io::Write,
    os::unix::{
        ffi::OsStringExt,
        fs::{MetadataExt, symlink},
    },
    path::PathBuf,
    process::{Command, Output, Stdio},
    sync::atomic::{AtomicUsize, Ordering},
};

const BIN: &str = env!("CARGO_BIN_EXE_bsdln");
struct Sandbox(PathBuf);
impl Sandbox {
    fn new() -> Self {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "darling-ln-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(BIN)
            .current_dir(&self.0)
            .args(args)
            .env_remove("POSIXLY_CORRECT")
            .output()
            .unwrap()
    }
    fn input(&self, args: &[&str], input: &[u8]) -> Output {
        let mut child = Command::new(BIN)
            .current_dir(&self.0)
            .args(args)
            .env_remove("POSIXLY_CORRECT")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child.stdin.take().unwrap().write_all(input).unwrap();
        child.wait_with_output().unwrap()
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn hard_links_and_symbolic_links_preserve_contents() {
    let sandbox = Sandbox::new();
    fs::write(sandbox.0.join("source"), b"contents").unwrap();
    assert!(sandbox.run(&["source", "hard"]).status.success());
    assert_eq!(
        fs::metadata(sandbox.0.join("source")).unwrap().ino(),
        fs::metadata(sandbox.0.join("hard")).unwrap().ino()
    );
    assert!(
        sandbox
            .run(&["-sv", "missing", "symbolic"])
            .status
            .success()
    );
    assert_eq!(
        fs::read_link(sandbox.0.join("symbolic")).unwrap(),
        PathBuf::from("missing")
    );
    assert!(!sandbox.run(&["source", "hard"]).status.success());
    assert_eq!(
        sandbox.run(&["-sv", "new", "verbose"]).stdout,
        b"verbose -> new\n"
    );
}

#[test]
fn directory_destinations_and_multiple_sources() {
    let sandbox = Sandbox::new();
    fs::create_dir(sandbox.0.join("dir")).unwrap();
    fs::write(sandbox.0.join("a"), b"a").unwrap();
    fs::write(sandbox.0.join("b"), b"b").unwrap();
    assert!(sandbox.run(&["a", "b", "dir"]).status.success());
    assert_eq!(fs::read(sandbox.0.join("dir/a")).unwrap(), b"a");
    assert_eq!(fs::read(sandbox.0.join("dir/b")).unwrap(), b"b");
    assert!(!sandbox.run(&["missing", "a", "dir"]).status.success());
    assert!(!sandbox.run(&["dir", "hard-directory"]).status.success());
    let result = Command::new(BIN)
        .current_dir(sandbox.0.join("dir"))
        .arg("../a")
        .output()
        .unwrap();
    assert!(!result.status.success()); // a already exists in current directory
}

#[test]
fn forced_and_interactive_replacements_and_flag_precedence() {
    let sandbox = Sandbox::new();
    fs::write(sandbox.0.join("source"), b"new").unwrap();
    fs::write(sandbox.0.join("dest"), b"old").unwrap();
    let declined = sandbox.input(&["-fi", "source", "dest"], b"no\n");
    assert!(!declined.status.success());
    assert_eq!(fs::read(sandbox.0.join("dest")).unwrap(), b"old");
    assert!(
        sandbox
            .input(&["-i", "source", "dest"], b"Yes please\n")
            .status
            .success()
    );
    fs::remove_file(sandbox.0.join("dest")).unwrap();
    fs::write(sandbox.0.join("dest"), b"old again").unwrap();
    assert!(
        sandbox
            .input(&["-if", "source", "dest"], b"no\n")
            .status
            .success()
    );
    assert_eq!(fs::read(sandbox.0.join("dest")).unwrap(), b"new");
}

#[test]
fn no_dereference_replaces_symlink_instead_of_its_directory() {
    let sandbox = Sandbox::new();
    fs::create_dir(sandbox.0.join("dir")).unwrap();
    symlink("dir", sandbox.0.join("alias")).unwrap();
    assert!(sandbox.run(&["-s", "missing", "alias"]).status.success());
    assert_eq!(
        fs::read_link(sandbox.0.join("dir/missing")).unwrap(),
        PathBuf::from("missing")
    );
    assert!(
        sandbox
            .run(&["-sfh", "replacement", "alias"])
            .status
            .success()
    );
    assert_eq!(
        fs::read_link(sandbox.0.join("alias")).unwrap(),
        PathBuf::from("replacement")
    );
    assert!(sandbox.0.join("dir").is_dir());
    assert!(!sandbox.run(&["-snh", "a", "b", "alias"]).status.success());
}

#[test]
fn remove_directory_flag_removes_only_empty_directory_at_final_name() {
    let sandbox = Sandbox::new();
    fs::create_dir_all(sandbox.0.join("dir/target")).unwrap();
    assert!(sandbox.run(&["-sF", "target", "dir"]).status.success());
    assert_eq!(
        fs::read_link(sandbox.0.join("dir/target")).unwrap(),
        PathBuf::from("target")
    );
    fs::remove_file(sandbox.0.join("dir/target")).unwrap();
    fs::create_dir_all(sandbox.0.join("dir/target")).unwrap();
    fs::write(sandbox.0.join("dir/target/data"), b"keep").unwrap();
    assert!(!sandbox.run(&["-sF", "target", "dir"]).status.success());
    assert_eq!(
        fs::read(sandbox.0.join("dir/target/data")).unwrap(),
        b"keep"
    );
}

#[test]
fn forcing_a_hardlink_onto_itself_must_not_delete_the_source() {
    let sandbox = Sandbox::new();
    fs::write(sandbox.0.join("source"), b"irreplaceable").unwrap();
    assert!(!sandbox.run(&["-f", "source", "source"]).status.success());
    assert_eq!(
        fs::read(sandbox.0.join("source")).unwrap(),
        b"irreplaceable"
    );
    fs::hard_link(sandbox.0.join("source"), sandbox.0.join("alias")).unwrap();
    assert!(!sandbox.run(&["-f", "source", "alias"]).status.success());
    assert_eq!(fs::metadata(sandbox.0.join("source")).unwrap().nlink(), 2);
}

#[test]
fn supports_raw_names_double_dash_and_usage_errors() {
    let sandbox = Sandbox::new();
    let raw = OsString::from_vec(b"source-\xff".to_vec());
    fs::write(sandbox.0.join(&raw), b"raw").unwrap();
    assert!(
        Command::new(BIN)
            .current_dir(&sandbox.0)
            .arg(&raw)
            .arg("dest")
            .output()
            .unwrap()
            .status
            .success()
    );
    fs::write(sandbox.0.join("-source"), b"dash").unwrap();
    assert!(sandbox.run(&["--", "-source", "dash"]).status.success());
    for args in [vec![], vec!["-z"], vec!["a", "b", "missing-directory"]] {
        assert!(!sandbox.run(&args).status.success());
    }
}

#[test]
#[ignore = "requires compiled upstream bsdln in UPSTREAM_BSDLN"]
fn differential_against_upstream_bsdln() {
    let original = std::env::var_os("UPSTREAM_BSDLN").expect("UPSTREAM_BSDLN required");
    let cases = [
        vec!["a", "dest"],
        vec!["-sv", "missing", "dest"],
        vec!["-fv", "a", "dest"],
        vec!["-sfn", "missing", "alias"],
        vec!["a", "b", "dir"],
        vec!["-sF", "empty", "dir"],
        vec!["a", "dir"],
        vec!["--", "a", "dest"],
    ];
    for args in cases {
        let mut results = Vec::new();
        for exe in [std::ffi::OsStr::new(BIN), original.as_os_str()] {
            let sandbox = Sandbox::new();
            fs::write(sandbox.0.join("a"), b"A").unwrap();
            fs::write(sandbox.0.join("b"), b"B").unwrap();
            fs::write(sandbox.0.join("dest"), b"old").unwrap();
            fs::create_dir_all(sandbox.0.join("dir/empty")).unwrap();
            symlink("dir", sandbox.0.join("alias")).unwrap();
            let output = Command::new(exe)
                .current_dir(&sandbox.0)
                .args(&args)
                .output()
                .unwrap();
            let mut snapshot = Vec::new();
            for path in ["a", "b", "dest", "alias", "dir/a", "dir/b", "dir/empty"] {
                let full = sandbox.0.join(path);
                let data = if let Ok(link) = fs::read_link(&full) {
                    format!("link:{}", link.display()).into_bytes()
                } else if full.is_dir() {
                    b"directory".to_vec()
                } else {
                    fs::read(full).unwrap_or_else(|_| b"absent".to_vec())
                };
                snapshot.push((path, data));
            }
            results.push((output.status.code(), output.stdout, snapshot));
        }
        assert_eq!(results[0], results[1], "args={args:?}");
    }
}
