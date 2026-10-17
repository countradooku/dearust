Darling Rust migration
=====================

Target: the FULL migration of https://github.com/darlinghq/darling to Rust,
including its runtime, server, libraries, frameworks and dependencies.
The full migration is INCOMPLETE. This repository cannot run macOS apps.

Functioning Rust components
---------------------------
Six Linux host binaries now build with Cargo:

  elfdep, getuuid       Bounds-checked thin/FAT Mach-O metadata readers
  bsdln                Hard/symbolic linking utility, without libbsd
  print_wrapped_elf    All wrapped ELF names in the first Mach-O architecture
  darling-coredump    32/64-bit x86 ELF core to Mach-O core conversion
  wrapgen              ELF64 wrapper source/header generation (partial port)

All implementation crates forbid unsafe code and use only Rust's standard
library. The core converter preserves register layouts and overlay file
reconstruction. Its floating-point state remains zero, matching upstream's
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

Build and stage with Cargo
--------------------------
Rust 1.85+, Cargo, a host linker, rustfmt and Clippy are required:

  cargo xtask build
  cargo xtask check
  cargo xtask install --destdir /path/to/stage

Native builds require no CMake. The release binaries are under target/release
(or CARGO_TARGET_DIR). Staging uses an explicit host triple and target directory,
preserves upstream build-tree paths for build-only tools and the upstream
bin/darling-coredump installation path. Its deterministic manifest records the
host artifact graph. See BUILDING.txt and xtask/README.md for boundaries.

This is a Cargo replacement for these host artifact operations. MIG generation,
Mach-O linking of the runtime, SDK/framework assembly, circular dependencies,
architecture slices and the complete upstream install graph remain pending.

Fixes and evidence
------------------
Regression tests cover forced links deleting their own source, DT_SONAME being
treated as a virtual address, dropped extra core mappings, incorrect zero-fill
lengths, uninitialized Mach-O segment padding, output/input aliases, parser
bounds and adversarial resource amplification. Differential suites compare all
six utilities with compiled pinned upstream sources. Generated wrapper C also
compiles against the pinned elfcalls ABI header and links to an x86-64 Mach-O
whose ELF dependency is checked. These are host artifact tests, not runtime ABI
or macOS application conformance. verification.json records the latest checks.

The synthetic 64 MiB zero-fill core conversion benchmark measured about 32 ms
upstream versus 22 ms Rust median, with equal normalized output. This supports
a workload-specific improvement only. Whole wrapgen CLI measurements on libm
were slower in Rust. Reproducible scripts, raw samples, environment and limits
are committed; no general Darling speedup is claimed.

Full-scope tracking
-------------------
migration-inventory.json covers all 27,274 superproject entries at
60ba801decee7a00782f74f6be4c8ffb013f79ff: 5 implemented source replacements,
1 partial source replacement, and 27,268 pending entries. Counts include
headers, resources and gitlinks; they do not measure runtime completeness.
dependency-inventory.json separately expands exact pinned dependency trees,
including nested gitlinks and license evidence. Every dependency entry remains
pending. MIGRATION.txt defines full-completion gates. No original ABI, resource,
framework or dependency is waived, and no placeholders count as ports.

The optional upstream-rust-tools.patch remains the earlier THREE-tool adapter
snapshot (elfdep/getuuid/bsdln). It applies to the pinned upstream but does not
contain the newer converter/wrapper ports or replace the remaining CMake graph.
Separate Git worktrees preserve parallel implementation and review history.
