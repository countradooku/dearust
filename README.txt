Darling Rust migration
=====================

Target: a full Rust migration of https://github.com/darlinghq/darling,
including the runtime, server, libraries, frameworks and dependencies.
Current implementation: THREE Linux host utilities. The full migration is
INCOMPLETE; this repository cannot run macOS applications.

Implemented
-----------
elfdep and getuuid share a bounds-checked Mach-O metadata parser. It handles
32/64-bit thin images and FAT/FAT64 containers in both byte orders, borrows
dependency names without copying, and validates commands/slices before output.
Malformed file ranges, unterminated section names, fat-slice overlap and
repeated-section parsing amplification have regression coverage.

bsdln implements the upstream hard/symbolic linking CLI and -Ffhinsv options,
including interactive replacement and directory handling, without libbsd.
It fixes source deletion caused by forcing a hard link onto the same inode.
The original failure and corrected behavior were reproduced in temporary dirs.
BSD license notices from the original implementation are retained.

All implementation crates forbid unsafe code and use only the Rust standard
library. Future unavoidable OS/ABI unsafe boundaries are described in
MIGRATION.txt; they have not been implemented or hidden behind stubs.

Build with Cargo
----------------
From this directory, using Rust 1.85+ with rustfmt and Clippy installed:

    cargo xtask build
    cargo xtask check
    cargo xtask bench

Native builds require no CMake. Executables are target/release/elfdep,
target/release/getuuid and target/release/bsdln. See BUILDING.txt for details.

Verification
------------
29 normal tests pass, plus two explicitly run optional differential tests
against compiled pinned upstream tools. These differential tests cover 48
Mach-O utility comparisons and eight bsdln filesystem scenarios. Formatting,
strict Clippy, release builds and a Clang-generated Mach-O object check pass.
verification.json records the checks, limitations and local benchmark sample.
No general speedup over upstream is claimed. The safe CLI readers currently
copy whole files, which may use more memory/I/O than upstream's mmap readers.

To run the optional differential tests after compiling upstream references:

    UPSTREAM_GETUUID=/absolute/path/getuuid \
    UPSTREAM_ELFDEP=/absolute/path/elfdep \
    UPSTREAM_BSDLN=/absolute/path/bsdln \
      cargo test --workspace --offline --locked -- --ignored

Full-scope tracking
-------------------
MIGRATION.txt sets out the remaining migration and compatibility gates.
migration-inventory.json records all 27,274 superproject entries at commit
60ba801decee7a00782f74f6be4c8ffb013f79ff, including 149 pinned submodules.
Only three entries are marked implemented; 27,271 remain pending. Entries
include headers/resources, and submodule contents require separate expansion.
This is scope tracking, not a claim that the remaining sources were rewritten.

Parallel implementation used three separate Git worktrees for Cargo tooling,
parser review and scope inventory. Their commits were cherry-picked into this
repository's main branch; the main integration also adds bsdln and regressions.

Optional patch for the existing upstream tree
--------------------------------------------
upstream-rust-tools.patch replaces the three original C utilities with this
Cargo package and minimal adapters for the remaining upstream CMake graph.
It preserves executable locations used by the existing dSYM/SDK build rules.
From a clean checkout of the pinned upstream revision:

    git apply --check /absolute/path/upstream-rust-tools.patch
    git apply /absolute/path/upstream-rust-tools.patch

The patch was checked against the pinned source and its isolated CMake
consumer build exercised with Ninja and Unix Makefiles. This does not verify
a full upstream build. Native Cargo builds remain independent of the adapters;
the unported upstream components still need their existing build system.

Full Cargo replacement requires explicit equivalents for MIG generation,
Mach-O cross-linking, multi-architecture slices, circular dylib dependencies,
SDK/framework assembly and installation. Those are tracked remaining work.
