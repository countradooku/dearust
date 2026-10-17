# Migrated host artifact staging

`cargo xtask install --destdir /path/to/stage` builds the migrated host tools
and native Linux synchronization library with Cargo. Current paths are:

- `src/bsdln/bsdln`
- `src/buildtools/elfdep`
- `src/buildtools/getuuid`
- `src/libelfloader/wrapgen/wrapgen` (partial loader-name lookup; see root README)
- `src/libelfloader/wrapgen/print_wrapped_elf`
- `bin/darling-coredump`
- `src/libsimple-linux/liblibsimple_darlingserver.a` (native Linux x86-64 archive)
- `lib/darling/native/darling_errno.o` (native errno ABI validation object)

`host-artifacts.tsv` declares the artifact kind (`bin`, `staticlib` or `object`), Cargo package, target, destination and
pinned upstream CMake target definition. Object rows additionally declare the
C globals requiring hidden visibility. The same deterministic file is written
to `darling-host-artifacts.tsv` at the staging root. It has no timestamps or
checkout-specific paths. It describes the artifact graph, not content hashes.

The pinned upstream `src/bsdln/CMakeLists.txt` and
`src/buildtools/CMakeLists.txt` have no installation rules for these targets.
The build-only tools reproduce the **build tree** layout consumed by
`Developer/symlinks.sh`, `cmake/dsym.cmake` and `tools/rpm/darling.attr`.
This command does not install a Darling runtime or replace its remaining CMake
graph. `darling-coredump` follows its upstream `bin` installation rule. The
static library reproduces the native Linux `libsimple_darlingserver` target's
build output: upstream adds `libsimple` under binary directory
`src/libsimple-linux`, and CMake prefixes the target name with `lib`.
Cargo builds `libdarling_simple.a` and staging gives it that upstream filename.
The Darwin libsimple backend and its Mach-O/fat archive remain unported.
The native staticlib is built with `--no-default-features` and an explicit
`profile.release.panic="abort"` Cargo override. It is a final C-linked archive
with its own panic handler, so it must not be linked into a Rust std application
or combined with another Rust panic handler. Normal Rust builds/tests retain
the default `std` feature. libc provides `syscall` and `abort`; no Rust allocator,
TLS setup or unwinder is required by the synchronization implementation.
The command invokes Cargo directly, without invoking CMake or a shell.

Staging builds specify the host triple from `cargo -vV`, `--offline --locked`,
and `--target-dir target/host-tools`. Their inputs are declared `--bin`/`--lib` targets;
output paths are derived from that explicit target directory and host triple,
without guessing from `CARGO_TARGET_DIR`. The usual `cargo xtask build` retains
its existing Cargo output-directory behavior.

Use a staging directory you own exclusively. Relative paths resolve against the
invocation directory; parent traversal and symlink directory components are
rejected. All input artifacts and existing output paths are checked before any
output tree is created. Symlinks and non-regular output files are rejected.
Artifacts are copied to a private temporary directory, preserving executable
permissions, then published with rename. Existing hard links are replaced
without modifying their other links. The graph manifest is published last.
Individual files are atomic replacements; the whole directory update is not a
transaction. A publication error may leave a partially updated directory and
returns failure. Concurrent hostile mutation of directory components is outside
the safe standard-library staging API's guarantees.

The integration test builds the actual declared artifacts, exercises staged
executables, checks repeatable manifests and verifies that a failing Cargo
build preserves its exit status without creating the output tree. On native
Linux x86-64 it also compiles and links a C pthread stress harness against the
**staged** archive using the copied upstream headers (`cc` and `ar` are required). The test rejects bundled std/alloc/unwinder
archive members and links without `-ldl` or `-lm`. A pre-thread fork verifies
that a null initializing callback terminates with SIGABRT. The
harness checks four-byte ABI layouts, once callback publication and completed
null callbacks, mutex/rwlock mutual exclusion, and repeated notify-all requeue
and mutex reacquisition with 12 waiters. An alarm bounds deadlocks at 30 seconds. Unit tests
cover symlink rejection, missing inputs, executable modes and hard-link
preservation. Add new functioning artifacts to the graph only after their
Cargo targets and compatibility tests exist.

The errno object requires `llvm-objcopy`, which sets the two original C globals
to hidden visibility while preserving global linkage. Cargo builds it in a fresh
private directory for each staging operation, with an explicit object output,
PIC, one codegen unit and LTO disabled. A cached rlib cannot hide a missing object
side output. Compiler/tool failure preserves the stage and forwards its status;
a regression specifically covers failure after the normal package builds.

This native ELF object is not the Darwin Mach-O/fat object required by emulation
and emulation_dyld. It preserves their errno C ABI in native validation only.
The integration harness links it alongside the no_std libsimple archive, checks
errno mapping/extrema, both hidden globals, empty undefined-symbol output, ELF
format and byte-identical objects on repeated staging. `nm` and `readelf` are
required for these native checks. The object has no panic handler and does not
introduce another Rust runtime into that combined C executable.
