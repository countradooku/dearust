
# Dearust

**Darling, reimagined in Rust.**

Dearust is an incremental migration of [Darling](https://github.com/darlinghq/darling) to Rust for running macOS applications on Linux. It is an independent project with upstream attribution and original source-specific license notices preserved.

Dearust is a project by [@countradooku](https://github.com/countradooku), developed using AI assistance from OpenAI Codex for implementation, code review, and test automation. Verification records describe what has actually been built and tested.

> [!WARNING]
> **Status: Experimental & Incomplete**
> This workspace does not yet build a standalone replacement for the full Darling runtime. Runtime integration currently relies on an existing matching Darling build; the full server and frameworks are not yet fully ported. Cargo packages and crate directories use Dearust naming, while exported runtime ABI symbols and upstream executable/install interfaces remain backward-compatible.

---

## Current Status & Verified Work

These results reflect a locally modified, matching `x86_64` runtime environment. They do **not** claim arbitrary macOS app compatibility. Full reproduction boundaries and verification details are documented in [`LOCKSET-PROCESS.txt`](LOCKSET-PROCESS.txt) and [`lockset-process-verification.json`](lockset-process-verification.json).

* **6 Host Utilities:** Fully ported to Rust, handling Mach-O/ELF metadata parsing and core file conversion.
* **Low-Level Synchronization:** Safe Rust synchronization primitives, Linux `futex` backends, and Darwin/Linux `errno` translation layers.
* **Mach Locksets:** Real Mach ports, lock handoff mechanisms, and thread/task lifecycle dispatching implemented in Rust.
* **Runtime Queries:** Rust BSD/task process information, descriptor guard tables, bounded `lstat` wrappers, and partial vnode and mapped-file queries. Fixes include the original `bsdtar` null-path crash and guarded-table memory corruption.
* **Test Coverage:** **108** Rust tests and **29** Python tests pass in the baseline environment.
* **App Verification:** **29/29** app checks pass against the Rust kernel (28 exact-match outputs, 1 passing with an explicit timestamp normalization on a known `cfbridge` log line). Sixteen additional utility workloads and three GUI samples (**TextEditor**, **Calculator**, and repaired **CGShadingCreate**) pass. See [runtime expansion details](RUNTIME-EXPANSION.txt).

---

## Implemented Components

### 1. Linux Host Binaries (`Cargo`)

All utilities build via Cargo without requiring CMake. The parsers, conversion logic, and synchronization state machines strictly forbid `unsafe` code (see individual crate `SAFETY.txt` files for small, documented C/futex boundaries).

| Utility | Description | Status |
| --- | --- | --- |
| `elfdep` | Bounds-checked thin/FAT Mach-O dependency reader | Full port |
| `getuuid` | Mach-O UUID extractor | Full port |
| `bsdln` | Hard & symbolic linking utility (removes `libbsd` dependency) | Full port |
| `print_wrapped_elf` | Scans wrapped ELF library references in first Mach-O slice | Full port |
| `darling-coredump` | 32/64-bit x86 ELF core to Mach-O core dump converter | Full port |
| `wrapgen` | ELF64 wrapper source/header generator | Partial port* |

**Note on `wrapgen`:* Safe path resolution covers explicit file paths, `LD_LIBRARY_PATH`, `/etc/ld.so.cache`, and standard Linux system dirs. Caller-specific `RPATH`/`RUNPATH`, custom audit modules, and dynamic loader hardware quirks are not implemented. Always supply explicit paths for deterministic outputs.

### 2. Runtime Slices (`no_std` Mach-O / ELF)

* **`libsimple` Synchronization:** Provides the 13 documented C synchronization primitives and the externally linked `cmpxchg` helper, maintaining identical 4-byte state layouts. Staged as a `no_std`, panic-abort archive (`liblibsimple_darlingserver.a`).
* **Darwin/Linux Errno Mapping:** A `no_std` translation layer preserving exact Linux-to-Darwin mappings and signed integer handling (including `INT_MIN` boundary fixes). Staged as a native ELF object with hidden symbols and linked into `libsystem_kernel`.
* **Mach Lockset Server & Process Queries:** Executes real Mach-O applications inside unprivileged user, mount, and PID namespaces without patching installed binaries.

---

## Migration Progress

Progress is tracked granularly against upstream commit [`60ba801`](https://github.com/darlinghq/darling/commit/60ba801decee7a00782f74f6be4c8ffb013f79ff).

```
Superproject Entries [27,274 total]
[██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 7 Ported / Partial (27,267 Pending)

Default Build Targets [1,629 total]
[█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 7 Cargo Slices (1,622 Pending)

```

Detailed breakdowns are maintained in:

* [`migration-inventory.json`](migration-inventory.json): Full superproject file-by-file tracking.
* [`dependency-inventory.json`](dependency-inventory.json): Pinned submodules, license records, and leaf dependencies.
* [`migration-targets.json`](migration-targets.json): Upstream build targets vs. Cargo implementations.
* [`MIGRATION.txt`](MIGRATION.txt): Completion gates, architectural boundaries, and pending ABI specs.

---

## Getting Started

### Prerequisites

* **Rust:** `1.85+` (with `rustfmt` and `clippy`)
* **Host Toolchain:** Host C compiler (`clang` or `gcc`), linker, and LLVM development tools
* *(Optional)*: A matching upstream Darling build tree if running Darwin integration tests

### Build & Test

Dearust uses `cargo-xtask` to orchestrate builds, verification passes, and staging without external build scripts:

```bash
# Clone the repository
git clone https://github.com/countradooku/dearust.git
cd dearust

# Build host tools (target/release)
cargo xtask build

# Run formatting, clippy, unit tests, and regression suites
cargo xtask check

# Stage host artifacts to a custom directory
cargo xtask install --destdir /path/to/stage

# (Optional) Build Darwin runtime Mach-O slices
cargo xtask runtime --target x86_64-apple-darwin --destdir /path/to/runtime

```

---

## Benchmarks & Micro-Optimizations

These benchmarks demonstrate isolated, workload-specific behavior only. **They do not claim a general Darling runtime speedup.**

* **Core Conversion (`darling-coredump`):** A synthetic 64 MiB zero-fill conversion measured **~22 ms (Rust)** vs. **~32 ms (Upstream C)** median runtime with bit-identical normalized output.
* **Errno Translation:** Microbenchmarks recorded **2.803 ns/call (Rust branchless/table)** vs. **3.042 ns/call (Upstream C)** over mixed signed values.
* **`wrapgen`:** Full CLI runs over complex inputs like `libm` are currently slightly slower in Rust due to complete buffered I/O rather than memory-mapped reads.

---

## Documentation Index

| File | Purpose |
| --- | --- |
| [`BUILDING.txt`](BUILDING.txt) | Detailed build requirements, linker flags, and environment vars |
| [`MIGRATION.txt`](MIGRATION.txt) | Subsystem boundaries, migration rules, and remaining milestones |
| [`LOCKSET-PROCESS.txt`](LOCKSET-PROCESS.txt) | Latest runtime lockset implementation details and verification |
| [`GUI-VERIFICATION.txt`](GUI-VERIFICATION.txt) | Test notes and execution constraints for Cocoa GUI applications |
| [`verification.json`](verification.json) | SHA-256 artifacts, suite outputs, and machine-readable test records |

---

## Licensing

Dearust is licensed under the **GNU General Public License v3.0** ([`LICENSE`](LICENSE)).

Individual ported components, bridge files, and headers retain their original upstream licenses:

* `bsdln`: Distributed under the BSD 3-Clause License ([`LICENSE-BSDLN`](LICENSE-BSDLN)).
* Lockset bridge sources (`crates/dearust-lockset/bridge`): Retain original Apple Public Source License (APSL) notices.
* Upstream Darling contributors and third-party dependency authors retain all respective copyrights.
