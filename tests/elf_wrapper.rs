use darling_buildtools::elf_wrapper::Library;
use std::{
    fs,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};

fn put16(d: &mut [u8], i: usize, v: u16) {
    d[i..i + 2].copy_from_slice(&v.to_le_bytes());
}
fn put32(d: &mut [u8], i: usize, v: u32) {
    d[i..i + 4].copy_from_slice(&v.to_le_bytes());
}
fn put64(d: &mut [u8], i: usize, v: u64) {
    d[i..i + 8].copy_from_slice(&v.to_le_bytes());
}
fn fixture() -> Vec<u8> {
    let mut d = vec![0; 512];
    d[..7].copy_from_slice(b"\x7fELF\x02\x01\x01");
    put16(&mut d, 16, 3);
    put16(&mut d, 18, 62);
    put32(&mut d, 20, 1);
    put64(&mut d, 32, 64);
    put64(&mut d, 40, 176);
    put16(&mut d, 52, 64);
    put16(&mut d, 54, 56);
    put16(&mut d, 56, 2);
    put16(&mut d, 58, 64);
    put16(&mut d, 60, 1);
    put32(&mut d, 64, 1);
    put64(&mut d, 80, 0x400000);
    put64(&mut d, 96, 512);
    put64(&mut d, 104, 4096);
    put32(&mut d, 120, 2);
    put64(&mut d, 128, 240);
    put64(&mut d, 152, 64);
    put32(&mut d, 180, 11);
    put64(&mut d, 200, 352);
    put64(&mut d, 208, 72);
    put64(&mut d, 232, 24);
    put64(&mut d, 240, 5);
    put64(&mut d, 248, 0x400130);
    put64(&mut d, 256, 10);
    put64(&mut d, 264, 32);
    put64(&mut d, 272, 14);
    put64(&mut d, 280, 1);
    d[304..329].copy_from_slice(b"\0libfixture.so\0zed\0alpha\0");
    for (base, name, kind) in [(352, 15, 2), (376, 19, 2), (400, 15, 1)] {
        put32(&mut d, base, name);
        d[base + 4] = 16 | kind;
        put16(&mut d, base + 6, 1);
        put64(&mut d, base + 8, 0x400080);
    }
    d
}
#[test]
fn soname_is_string_offset_even_with_nonzero_load_address() {
    let d = fixture();
    let lib = Library::parse(&d).unwrap();
    assert_eq!(lib.soname, Some("libfixture.so"));
    assert_eq!(
        lib.functions.into_iter().collect::<Vec<_>>(),
        ["alpha", "zed"]
    );
}
#[test]
fn truncated_or_out_of_range_inputs_never_parse_or_panic() {
    let d = fixture();
    for size in 0..424 {
        assert!(Library::parse(&d[..size]).is_err(), "size={size}");
    }
    for offset in [32, 40, 128, 152, 248, 264, 280, 200, 208] {
        let mut bad = d.clone();
        put64(&mut bad, offset, u64::MAX);
        assert!(Library::parse(&bad).is_err(), "offset={offset}");
    }
    let mut bad = d.clone();
    put64(&mut bad, 96, 320); // strings cross end of file-backed load
    assert!(Library::parse(&bad).is_err());
}
#[test]
fn generation_is_sorted_and_rejects_source_injection() {
    let d = fixture();
    let lib = Library::parse(&d).unwrap();
    let (source, header) = lib.generate("ignored").unwrap();
    assert!(source.find("void* alpha()").unwrap() < source.find("void* zed()").unwrap());
    assert!(source.contains(".asciz \\\"libfixture.so\\\""));
    assert!(header.unwrap().contains("#define zed (*__elf_get_zed())"));
    let mut bad = d.clone();
    bad[319] = b'"';
    assert!(Library::parse(&bad).is_err());
    let mut bad = d;
    bad[305] = b'"';
    assert!(Library::parse(&bad).unwrap().generate("x").is_err());
}
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static N: AtomicUsize = AtomicUsize::new(0);
        let p = std::env::temp_dir().join(format!(
            "darling-wrapgen-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
#[test]
fn cli_preserves_input_and_existing_outputs_on_parse_failure() {
    let tmp = Temp::new();
    let input = tmp.0.join("input");
    let source = tmp.0.join("source");
    let header = tmp.0.join("header");
    fs::write(&input, fixture()).unwrap();
    let run = |a: &std::path::Path, b: &std::path::Path| {
        Command::new(env!("CARGO_BIN_EXE_wrapgen"))
            .args([&input, a, b])
            .output()
            .unwrap()
    };
    assert!(!run(&input, &header).status.success());
    assert_eq!(fs::read(&input).unwrap(), fixture());
    fs::hard_link(&input, &source).unwrap();
    assert!(!run(&source, &header).status.success());
    fs::remove_file(&source).unwrap();
    fs::write(&source, b"preserve").unwrap();
    fs::write(&input, b"bad").unwrap();
    assert!(!run(&source, &header).status.success());
    assert_eq!(fs::read(&source).unwrap(), b"preserve");
}
#[test]
#[ignore = "requires C compiler and UPSTREAM_WRAPGEN pointing to pinned compiled wrapgen"]
fn differential_compiled_libraries() {
    let upstream = std::env::var_os("UPSTREAM_WRAPGEN").expect("set UPSTREAM_WRAPGEN");
    let tmp = Temp::new();
    let input = tmp.0.join("sample.c");
    fs::write(&input,"int exported_var=42; int zed(void){return 7;} int alpha(void){return exported_var;} static int local(void){return 0;} __attribute__((visibility(\"hidden\"))) int hidden(void){return local();}\n").unwrap();
    for (i, flags) in [
        vec![],
        vec!["-Wl,-soname,libsample.so.2"],
        vec!["-Wl,-soname,libsample.so.3", "-Wl,-Ttext-segment=0x400000"],
    ]
    .iter()
    .enumerate()
    {
        let library = tmp.0.join(format!("libsample{i}.so"));
        assert!(
            Command::new("cc")
                .args(["-shared", "-fPIC"])
                .args(flags)
                .arg(&input)
                .arg("-o")
                .arg(&library)
                .status()
                .unwrap()
                .success()
        );
        let mut outputs = Vec::new();
        for (label, exe) in [
            ("c", upstream.clone()),
            ("rust", env!("CARGO_BIN_EXE_wrapgen").into()),
        ] {
            let source = tmp.0.join(format!("{label}.c"));
            let header = tmp.0.join(format!("{label}.h"));
            let result = Command::new(exe)
                .args([&library, &source, &header])
                .output()
                .unwrap();
            assert!(result.status.success(), "{:?}", result);
            outputs.push((fs::read(source).unwrap(), fs::read(header).unwrap()));
        }
        if i < 2 {
            assert_eq!(outputs[0], outputs[1]);
        } else {
            assert!(String::from_utf8_lossy(&outputs[1].0).contains("libsample.so.3"));
            assert!(
                !String::from_utf8_lossy(&outputs[0].0).contains("libsample.so.3"),
                "upstream bug reproducer changed"
            );
        }
    }
}

#[test]
fn library_name_lookup_uses_ld_library_path_without_loading_code() {
    let tmp = Temp::new();
    fs::write(tmp.0.join("libdarlingwrap_fixture.so"), fixture()).unwrap();
    let source = tmp.0.join("source");
    let header = tmp.0.join("header");
    let result = Command::new(env!("CARGO_BIN_EXE_wrapgen"))
        .arg("libdarlingwrap_fixture.so")
        .args([&source, &header])
        .env("LD_LIBRARY_PATH", &tmp.0)
        .output()
        .unwrap();
    assert!(result.status.success(), "{result:?}");
    assert!(
        fs::read_to_string(source)
            .unwrap()
            .contains("libfixture.so")
    );
}
