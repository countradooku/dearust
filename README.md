
# Dearust

**Darling, reimagined in Rust.**

Dearust is an incremental migration of [Darling](https://github.com/darlinghq/darling) to Rust for running macOS applications on Linux. It is an independent project with upstream attribution and original source-specific license notices preserved.

Dearust is a project by [@countradooku](https://github.com/countradooku), developed using AI assistance from OpenAI Codex for implementation, code review, and test automation. Verification records describe what has actually been built and tested.

> [!WARNING]
> **Status: Experimental & Incomplete**
> This workspace does not yet build a standalone replacement for the full Darling runtime. Runtime integration currently relies on an existing matching Darling build; the full server and frameworks are not yet fully ported. Cargo packages and crate directories use Dearust naming, while exported runtime ABI symbols and upstream executable/install interfaces remain backward-compatible.

---

## Implementation & Recorded Verification

Runtime execution evidence uses a locally modified, matching `x86_64` environment. The current accounting index is [`migration-reconciliation.json`](migration-reconciliation.json); [`migration-targets.json`](migration-targets.json) schema v2 separates artifact mappings, source coverage, linking, execution and retirement. Historical verification retains its original scope; these records do not establish arbitrary macOS application compatibility.

* **6 Host Utilities:** Five ported tools and one partial wrapper generator handle Mach-O/ELF metadata parsing and core file conversion.
* **Low-Level Synchronization:** Safe Rust synchronization primitives, Linux `futex` backends, and Darwin/Linux `errno` translation layers.
* **Mach Locksets:** Rust state and ownership handle real lock handoff through retained XNU port, scheduler and lifecycle bridges. Whole-server replacement remains pending.
* **Runtime Queries:** Rust BSD/task process information, descriptor guard tables, bounded pathname metadata and `readlinkat` wrappers, descriptor `fstat`, directory-relative symlink, deletion, hard-link, rename, directory creation, access and permission operations, plus descriptor-only mode, truncation and synchronization calls, and partial vnode and mapped-file queries. Fixes include the original `bsdtar` null-path crash and guarded-table memory corruption.
* **Recorded Test Coverage:** The accounting validation on **2026-10-20** passed **202** ordinary Rust tests, **11** compile-fail checks and **81** Python tests, plus deterministic inventory regeneration. [Exact commands and results](inventory-reconciliation-verification.json). Guest and performance results retain their earlier scope in [`runtime-resource-copies-verification.json`](runtime-resource-copies-verification.json); this accounting run does not validate resource ownership candidates.
* **Filesystem Queries:** Four `statfs`/`fstatfs` entries share `dearust-path`, with initialized outputs, mount identity, fault protection, and descriptor-pressure handling. The tested four-call guest workload uses **84.75% less CPU** than exact original C; private memory is unchanged. [Implementation and measurements](RUNTIME-FILESYSTEM.txt).
* **Resource ABI (unfinished):** Resource limits and usage now have guarded guest copies, correct Darwin layouts/units and coherent NOFILE accounting. Full unsigned-limit compatibility and measured CPU overhead remain open completion gates. [Implementation and failing gates](RUNTIME-RESOURCES.txt).
* **Resource Registration Candidate (unfinished):** Reserved EIDs, prepared monitor/thread publication and intrusive cleanup now build in owned server copies. [Scope and open gates](tools/PREPARED-RESOURCE-REGISTRATION.txt). This prerequisite does not activate resource ownership or retire a target.
* **Core Input:** The core converter owns checked metadata and streams dumped payload with safe kernel copies or a bounded buffer, descriptor-based alias checks, and sparse zero output. [Safety and measurements](CORE-INPUT.txt).
* **Core Registers:** The existing `dearust-coredump` subsystem preserves actual x86 floating-point, AVX and AVX512 state, including source CPU layout metadata and lossless raw images. Missing state is no longer fabricated as zeros. [Format and verification](CORE-REGISTERS.txt).
* **Traditional Pathnames:** Six more operations share the path subsystem, with corrected `readlinkat` sizes and zero-length reads. The paired guest workload uses **4.04% less CPU** than original C. [Evidence](RUNTIME-PATH-FRONTENDS.txt).
* **Stream I/O:** Read/write join the existing descriptor subsystem. Full-width byte counts and terminal EOF/EIO bugs are fixed; the paired guest workload uses **5.73% less CPU** than exact original C, with native performance effectively unchanged. [Stream evidence](RUNTIME-STREAMS.txt).
* **Guest Copy Performance:** The corrected matching x86_64 kernel uses **4.4% less metadata CPU** and **6.8% less permissions CPU** than the original C implementation on the same guest workloads. Pointer and signal/fork protections remain enabled. [Scope, latency, memory and correctness evidence](RUNTIME-COPY-PERFORMANCE.txt) describes the paired baseline and tested boundaries.
* **App Verification:** **29/29** app checks pass against the Rust kernel (28 exact-match outputs, 1 passing with an explicit timestamp normalization on a known `cfbridge` log line). Sixteen additional utility workloads and three GUI samples (**TextEditor**, **Calculator**, and repaired **CGShadingCreate**) pass. See [runtime expansion details](RUNTIME-EXPANSION.txt), [performance measurements](RUNTIME-PERFORMANCE.txt), [prior pointer and permission fixes](RUNTIME-HARDENING.txt), and [pathname consolidation](RUNTIME-PATHS.txt). Earlier batches cover [process control](RUNTIME-CONTROL.txt), [process identifiers](RUNTIME-IDENTIFIERS.txt), [file operations](RUNTIME-FILEOPS.txt), [permissions](RUNTIME-PERMISSIONS.txt), [descriptor ports](RUNTIME-FDOPS.txt), and [descriptor grouping](RUNTIME-DESCRIPTORS.txt).

---

## Implemented Components

### 1. Linux Host Binaries (`Cargo`)

All utilities build via Cargo without requiring CMake. The parsers, conversion logic, and synchronization state machines strictly forbid `unsafe` code (see individual crate `SAFETY.txt` files for small, documented C/futex boundaries).

| Utility | Description | Status |
| --- | --- | --- |
| `elfdep` | Bounds-checked thin/FAT Mach-O dependency reader | Implemented host slice |
| `getuuid` | Mach-O UUID extractor | Implemented host slice |
| `bsdln` | Hard & symbolic linking utility (removes `libbsd` dependency) | Implemented host slice |
| `print_wrapped_elf` | Scans wrapped ELF library references in first Mach-O slice | Implemented host slice |
| `darling-coredump` | 32/64-bit x86 ELF core to Mach-O core dump converter | Implemented host slice |
| `wrapgen` | ELF64 wrapper source/header generator | Partial port* |

**Note on `wrapgen`:* Safe path resolution covers explicit file paths, `LD_LIBRARY_PATH`, `/etc/ld.so.cache`, and standard Linux system dirs. Caller-specific `RPATH`/`RUNPATH`, custom audit modules, and dynamic loader hardware quirks are not implemented. Always supply explicit paths for deterministic outputs.

### 2. Runtime Slices (`no_std` Mach-O / ELF)

* **`libsimple` Synchronization:** Provides the 13 documented C synchronization primitives and the externally linked `cmpxchg` helper, maintaining identical 4-byte state layouts. Staged as a `no_std`, panic-abort archive (`liblibsimple_darlingserver.a`).
* **Darwin/Linux Errno Mapping:** A `no_std` translation layer preserving exact Linux-to-Darwin mappings and signed integer handling (including `INT_MIN` boundary fixes). Host staging emits a native ELF validation object; separate runtime staging emits the x86_64 Mach-O object used in verified `libsystem_kernel` links.
* **Pathname Subsystem (`dearust-path`):** Pathname and metadata source slices share one crate, including `access`/`chmod` and `stat`/`fstat`. Guest paths and metadata outputs use guarded fault-contained copies with a checked kernel fallback; permission flags use kernel handling, including atomic no-follow chmod. [Current performance and safety evidence](RUNTIME-COPY-PERFORMANCE.txt) supersedes the earlier hardening costs.
* **Descriptor Subsystem (`dearust-fd`):** Groups stream reads/writes, mode, creation-mask, descriptor-limit, truncation, synchronization, seek and file-lock operations in one allocation-free runtime crate. The workspace now has 11 packages after subsystem consolidation.
* **Process Services (`dearust-process`):** Grouped entries cover uncached process/thread IDs and control, bounded kernel entropy and the unfinished resource ABI. Entropy size, fault and interrupted-call defects are repaired. [Evidence](RUNTIME-PROCESS-SERVICES.txt). Runtime feature separation preserves the matching/pinned boundaries.
* **Mach Lockset Server & Process Queries:** Executes real Mach-O applications inside unprivileged user, mount, and PID namespaces without patching installed binaries.

---

## Migration Progress

Progress is tracked granularly against upstream commit [`60ba801`](https://github.com/darlinghq/darling/commit/60ba801decee7a00782f74f6be4c8ffb013f79ff).

The pinned superproject and dependency inventories count committed tree leaves,
including headers, data, symlinks and gitlinks. The captured default CMake graph
covers one configuration. Their entry and target totals are scope denominators,
not percentages of implementation effort.

A target can contain substantial Rust source work while its whole-target status
remains pending. Host and runtime manifests stage different artifacts; one object
can serve several targets, and several objects can serve one target. Source
coverage, verified consumer links, executed workloads, production selection and
legacy retirement must therefore be read separately. No legacy target retirement
or complete runtime migration is claimed. Resource ownership candidates remain
outside the default runtime staging path.

Detailed breakdowns are maintained in:

* [`migration-inventory.json`](migration-inventory.json): Full superproject file-by-file tracking.
* [`dependency-inventory.json`](dependency-inventory.json): Pinned submodules, license records, and leaf dependencies.
* [`migration-targets.json`](migration-targets.json): Schema v2 target records, source slices and artifact relationships.
* [`migration-reconciliation.json`](migration-reconciliation.json): Current reconciliation, evidence scope, unresolved gaps and package dependencies.
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

* **Core Conversion (`darling-coredump`):** [Input architecture measurements](CORE-INPUT.txt) compare original C with the immediately preceding register-preserving Rust converter. [The earlier register batch](CORE-REGISTERS.txt) separately records the cost of adding real state and raw archives. Results depend on workload and filesystem; they do not establish that every conversion is faster.
* **Errno Translation:** Microbenchmarks recorded **2.803 ns/call (Rust branchless/table)** vs. **3.042 ns/call (Upstream C)** over mixed signed values.
* **Runtime Path/Region Costs:** In-place path copying reduces generated local stack allocation from ~12 KiB to ~4 KiB. A specific guest region-query benchmark uses ~56% less CPU; see [measurement scope and raw evidence](RUNTIME-PERFORMANCE.txt).
* **`wrapgen`:** Full CLI runs over complex inputs like `libm` are currently slightly slower in Rust due to complete buffered I/O rather than memory-mapped reads.

---

## Documentation Index

| File | Purpose |
| --- | --- |
| [`BUILDING.txt`](BUILDING.txt) | Detailed build requirements, linker flags, and environment vars |
| [`MIGRATION.txt`](MIGRATION.txt) | Subsystem boundaries, migration rules, and remaining milestones |
| [`LOCKSET-PROCESS.txt`](LOCKSET-PROCESS.txt) | Historical lockset implementation and execution scope |
| [`GUI-VERIFICATION.txt`](GUI-VERIFICATION.txt) | Test notes and execution constraints for Cocoa GUI applications |
| [`verification.json`](verification.json) | Historical verification; consult the reconciliation for later scoped evidence |

---

## Licensing

Dearust is licensed under the **GNU General Public License v3.0** ([`LICENSE`](LICENSE)).

Individual ported components, bridge files, and headers retain their original upstream licenses:

* `bsdln`: Distributed under the BSD 3-Clause License ([`LICENSE-BSDLN`](LICENSE-BSDLN)).
* Lockset bridge sources (`crates/dearust-lockset/bridge`): Retain original Apple Public Source License (APSL) notices.
* Upstream Darling contributors and third-party dependency authors retain all respective copyrights.

Resource follow-up evidence is in `RUNTIME-RESOURCE-COPIES.txt`. The fixed-copy
optimization is validated in the default runtime; full unsigned-limit ownership,
XNU enforcement and generic-buffer CPU gates remain unfinished.
