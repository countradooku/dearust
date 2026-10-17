Darling Rust migration
=====================

Target: the FULL migration of https://github.com/darlinghq/darling to Rust,
including its runtime, server, libraries, frameworks and dependencies.
The full migration is INCOMPLETE. Three Rust runtime slices have executed macOS
command-line applications inside an existing locally modified Darling installation.
This workspace does not yet build a complete Darling runtime.

Functioning Rust components
---------------------------
Six Linux host binaries now build with Cargo:

  elfdep, getuuid       Bounds-checked thin/FAT Mach-O metadata readers
  bsdln                Hard/symbolic linking utility, without libbsd
  print_wrapped_elf    All wrapped ELF names in the first Mach-O architecture
  darling-coredump    32/64-bit x86 ELF core to Mach-O core conversion
  wrapgen              ELF64 wrapper source/header generation (partial port)

The parser/converter crates and synchronization state-machine core forbid
unsafe code. The native synchronization adapter has small documented unsafe
C pointer, callback and Linux futex boundaries; see its SAFETY.txt. The workspace has no third-party Rust dependencies. The core converter preserves
register layouts and overlay file reconstruction. Its floating-point state remains zero, matching upstream's
unimplemented floating-state extraction. Its diagnostics differ from upstream;
HOME-unset account lookup uses procfs and /etc/passwd, without NSS services.
CLI readers currently read whole inputs, which can use more memory than mmap.

wrapgen matches upstream generated C/header output for ordinary identifiers
and corrects DT_SONAME handling at nonzero load addresses. It never loads or
executes input libraries. Safe name resolution covers explicit paths,
LD_LIBRARY_PATH, ldconfig cache and common Linux directories; caller-specific
RPATH/RUNPATH, audit modules and all loader hardware policy are not implemented.
It therefore remains explicitly PARTIAL. Use explicit library paths for
reproducible generation. Invalid source identifiers/names are rejected.

A native Linux x86_64 libsimple synchronization archive also provides the
13 documented C functions plus the externally linked cmpxchg helper, preserving
four-byte state layouts. C-link concurrency tests compare it with upstream.
This source remains PARTIAL: the native and Darling x86_64 futex backends work,
while i386/other architectures and clean pinned-runtime startup remain pending. Cargo build/staging now emits a no_std panic-abort archive; libc supplies
syscall and abort. Normal Rust development/tests retain the default std feature.

A separate no_std errno converter now preserves the pinned Linux-to-Darwin
mapping, sign handling and both original C globals. It fixes signed overflow
for INT_MIN, verified against compiled original C plus UBSan. Cargo stages a
native ELF object with hidden symbols and no unresolved runtime dependencies.
Darwin Mach-O errno/futex/lock objects now link into the actual original
libsystem_kernel. Native execution checks real guarded-table and close consumers.
i386 Rust replacements, full emulation migration and clean pinned-runtime startup
remain pending. Actual installed-runtime execution is described below.

Build and stage with Cargo
--------------------------
Rust 1.85+, Cargo, a host linker, rustfmt and Clippy are required:

  cargo xtask build
  cargo xtask check
  cargo xtask install --destdir /path/to/stage

Native builds require no CMake. The release binaries are under target/release
(or CARGO_TARGET_DIR). Staging uses an explicit host triple and target directory,
preserves upstream build-tree paths for build-only tools and the upstream
bin/darling-coredump installation path. The typed graph also stages the native
libsimple static archive at src/libsimple-linux/liblibsimple_darlingserver.a. Its deterministic manifest records the
host artifact graph. The errno object is staged separately under
lib/darling/native. See BUILDING.txt and xtask/README.md for boundaries.

This is a Cargo replacement for these host artifact operations. MIG generation,
Full Cargo runtime linking, SDK/framework assembly, circular dependencies,
architecture slices and the complete upstream install graph remain pending.

Fixes and evidence
------------------
Regression tests cover forced links deleting their own source, DT_SONAME being
treated as a virtual address, dropped extra core mappings, incorrect zero-fill
lengths, uninitialized Mach-O segment padding, output/input aliases, parser
bounds and adversarial resource amplification. Differential suites compare all
six utilities with compiled pinned upstream sources. Generated wrapper C also
compiles against the pinned elfcalls ABI header and links to an x86-64 Mach-O
whose ELF dependency is checked. These include isolated native ABI checks; they do not establish full runtime or
macOS application conformance. verification.json records the latest checks.

The synthetic 64 MiB zero-fill core conversion benchmark measured about 32 ms
upstream versus 22 ms Rust median, with equal normalized output. This supports
a workload-specific improvement only. Whole wrapgen CLI measurements on libm
were slower in Rust. Reproducible scripts, raw samples, environment and limits
are committed; no general Darling speedup is claimed.

Full-scope tracking
-------------------
migration-inventory.json covers all 27,274 superproject entries at
60ba801decee7a00782f74f6be4c8ffb013f79ff: 5 implemented source replacements,
2 partial source replacements, and 27,267 pending entries. Counts include
headers, resources and gitlinks; they do not measure runtime completeness.
dependency-inventory.json separately expands exact pinned dependency trees,
including nested gitlinks and license evidence. Two dependency sources (errno/futex) have verified native execution and Darwin
consumer linkage and remain partial; 265,801 leaves remain pending. MIGRATION.txt defines full-completion gates. No original ABI, resource,
framework or dependency is waived, and no placeholders count as ports.

The optional upstream-rust-tools.patch remains the earlier THREE-tool adapter
snapshot (elfdep/getuuid/bsdln). It applies to the pinned upstream but does not
contain the newer converter/wrapper ports or replace the remaining CMake graph.
The isolated original upstream configures with its default CMake options and
all pinned dependency trees. Forty-four Swift Git LFS binary pointers remain
unmaterialized; configuration is not a full build. The recorded File API graph
and baseline report capture remaining migration inputs. migration-targets.json
tracks 1,629 default targets: seven have Cargo artifact slices (including partial
ones), while 1,622 remain pending. Separate Git worktrees
preserve parallel implementation and review history.

The errno conversion microbenchmark measured 3.042 ns/call for pinned C and
2.803 ns/call for the final safe Rust lookup over mixed signed inputs. The first
Rust match implementation was slower. Raw measurements, compiler/source hashes
and limits are in crates/darling-errno; this is not a Darling runtime speedup.

Darling runtime integration
---------------------------
`cargo xtask runtime --target x86_64-apple-darwin --destdir /path/to/runtime`
builds three no_std Mach-O objects and a thin libsimple archive without CMake,
an allocator or a Rust panic runtime. The native target can build the same
Darling ABI chain for execution tests. See tools/DARWIN-RUNTIME.txt.

The original emulation/system_kernel targets now build. The complete actual
system_kernel final link accepts the Rust x86_64 slices with the same 1,362 public
symbols and 18 imports. Its original i386 final slice is preserved byte-for-byte.
This pinned-baseline link gate remains separate from actual execution in the
locally modified installed runtime described below. Full Cargo runtime migration
remains pending.


Actual macOS command-line execution
----------------------------------
The Rust errno/futex/libsimple slices also link into the locally modified installed
Darling runtime, preserving its 1,370 public definitions and 40 imports. Matching
original and Rust variants both boot and pass Bash pipeline, SQLite table/insert/
aggregate, Python JSON/SHA-256 and offline curl version checks, plus Objective-C,
pthread, TLS, fork and Mach semaphore tests. The running launchd process's mapped
library inode identifies the copied Rust dylib, whose SHA-256 was checked.

This execution uses existing C/assembly, frameworks and server binaries from that
installation. Its local fileport RPC additions shift call numbers by two; the
clean pinned kernel therefore cannot boot against that installed server. Matching
local build objects resolve this protocol mismatch without changing installed
files. Only x86_64 was exercised; GUI applications, cancellation behavior and clean
pinned full-runtime startup remain unverified. No general speedup or complete port
is established by these checks.

mac-app-verification.json records 29 checks per matching variant: 24 strict
expected-output passes and five shared mismatches. The latter are two lockset
assertions, a CFURL fixture requiring absent /home, unsupported/failing libproc
queries, and an extra timestamped cfbridge NSLog line despite a zero exit.
No status/exit regression was observed between the matching variants in this suite.
The record includes hashes and paths to raw execution and mapping evidence.
