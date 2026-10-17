#!/usr/bin/env python3
"""Compare Rust wrapgen bare-name lookup before/after a Git revision.

This deliberately measures only resolver lookups satisfied by LD_LIBRARY_PATH.
It excludes parsing/generation/process startup and is not an upstream speedup.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="Git revision containing the earlier Rust resolver")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.iterations < 1 or args.trials < 1:
        parser.error("iterations and trials must be positive")
    root = Path(__file__).resolve().parent.parent
    before = subprocess.check_output(["git", "show", f"{args.before}:src/elf_wrapper.rs"], cwd=root)
    results = {}
    with tempfile.TemporaryDirectory(prefix="darling-lookup-bench-") as directory:
        work = Path(directory)
        # The resolver inspects only these 20 identification bytes; this is not
        # a valid complete library and never reaches parsing or native loading.
        library = work / "libdarling_lookup_benchmark.so"
        library.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(11) + b"\x3e\x00")
        environment = dict(os.environ, LD_LIBRARY_PATH=str(work))
        for label, source in [("before", before), ("after", (root / "src/elf_wrapper.rs").read_bytes())]:
            module = work / f"{label}.rs"
            module.write_bytes(source)
            harness = work / f"{label}_bench.rs"
            # JSON string quoting is also valid Rust literal quoting for these
            # generated ASCII temporary paths. No shell processes are used.
            harness.write_text(f'''#[allow(dead_code)]
#[path={json.dumps(str(module))}] mod elf_wrapper;
fn main() {{
    let expected = std::path::Path::new({json.dumps(str(library))});
    let start = std::time::Instant::now();
    for _ in 0..{args.iterations} {{
        let actual = elf_wrapper::resolve_library(std::ffi::OsStr::new("libdarling_lookup_benchmark.so")).unwrap();
        assert_eq!(std::hint::black_box(actual), expected);
    }}
    println!("{{}}", start.elapsed().as_nanos());
}}
''')
            executable = work / f"{label}-bench"
            subprocess.run(["rustc", "--edition=2024", "-O", str(harness), "-o", str(executable)], check=True)
            samples = [int(subprocess.check_output([str(executable)], cwd=work.parent, env=environment)) / args.iterations for _ in range(args.trials)]
            results[label] = {"ns_per_lookup": samples, "median_ns_per_lookup": statistics.median(samples)}
    print(json.dumps({"before_revision": args.before, "iterations_per_trial": args.iterations, "trials": args.trials,
                      "workload": "Rust resolver only, warm LD_LIBRARY_PATH identification fixture; no ELF parse, generation or upstream comparison", **results}, indent=2))


if __name__ == "__main__":
    main()
