# Darling full Rust migration: continuation instructions

## Objective and authorization

Continue the FULL migration of https://github.com/darlinghq/darling to safe,
idiomatic Rust, with unsafe only at unavoidable, documented OS/ABI boundaries.
Preserve runtime behavior, exported ABIs, resources and dependency scope. Fix
confirmed upstream bugs with regression tests and measure performance rather
than inferring it from language choice. Try replacing the entire CMake graph
with Cargo-native orchestration. Do actual implementation.

The user authorizes parallel subagents and isolated Git worktrees, and requested
continuation in a separate chat. Reuse this repository and preserve its history.
Do not equate host-tool progress with a full port, use placeholder implementations
to satisfy scope, or claim macOS applications run. Full migration is INCOMPLETE.
Follow later user instructions if they revise these priorities.

## Read first

- README.txt, BUILDING.txt, MIGRATION.txt, verification.json
- This file and crates/darling-libsimple/SAFETY.txt
- Inventory summaries in migration-inventory.json, dependency-inventory.json,
  migration-targets.json, upstream-cmake-graph.json
- tools/BASELINE.txt, tools/DEPENDENCY-INVENTORY.txt,
  upstream-baseline-report.json, xtask/README.md

The dependency and graph JSON files are large (~82 MB and ~53 MB). Inspect their
summary fields or targeted records with scripts; do not dump them into context.

## Repository and exact reference sources

Repository: /home/radud/Documents/Codex/2026-10-18/re/outputs/darling-rust
Branch: main. Last implementation/integration commit before this handoff:
e1c311c. This AGENTS.md is committed afterward. Start by checking status/history.
Pinned upstream: 60ba801decee7a00782f74f6be4c8ffb013f79ff.

Reference locations:
- /home/radud/Documents/Codex/2026-10-18/re/work/upstream
  Locally modified for the older adapter patch; read ORIGINAL sources with
  git show PIN:path, not its working files. Do not discard modifications.
- /home/radud/Documents/Codex/2026-10-18/re/work/full-baseline
  Independent exact-pin original clone with all 160 pinned dependency trees
  materialized. Its default CMake configuration succeeds without source edits.
- /home/radud/Documents/Codex/2026-10-18/re/work/full-baseline-build
  Actual configured default Unix Makefiles build. Original elfdep/getuuid/bsdln,
  wrapgen, darling-coredump and libsimple_darlingserver targets build successfully.
  Full runtime has NOT been built. 44 Swift Git LFS binary payloads remain
  unmaterialized; pointer objects alone do not satisfy these prerequisites.
- /home/radud/Documents/Codex/2026-10-18/re/work/dependency-objects
  Isolated bare exact-pin dependency object cache; all 149 first-level plus
  11 nested trees are available. Do not silently advance any pins.
- /home/radud/Documents/Codex/2026-10-18/re/work/upstream-patch-check
  Earlier three-tool patch application/integration check checkout.

Existing migration worktrees (inspect status before reusing):
- /home/radud/Documents/Codex/2026-10-18/re/work/parser-review
- /home/radud/Documents/Codex/2026-10-18/re/work/cargo-build
- /home/radud/Documents/Codex/2026-10-18/re/work/full-inventory
Additional errno implementation/review work is preserved under:
- /home/radud/Documents/Codex/2026-10-18/darling-rust-continuation/work/errno-port
- /home/radud/Documents/Codex/2026-10-18/darling-rust-continuation/work/errno-review
The first is a detached Git worktree; the second holds independent reference
builds and UBSan evidence. Inspect status before reusing either location.
Their integrated commits may have different ancestry after cherry-picking.
Preserve scratch/reference binaries under work/ and never reset dirty work.

## Implemented components and precise limitations

Six functioning Cargo binaries: elfdep, getuuid, bsdln, print_wrapped_elf,
darling-coredump, wrapgen. Parser/converter crates forbid unsafe. They include
bounds, resource-amplification and destructive-file-alias regression coverage.

wrapgen remains PARTIAL: ELF64 x86_64 parsing/generation works, including corrected
DT_SONAME offset handling and matching ordinary upstream generated output.
Safe lookup supports explicit paths, LD_LIBRARY_PATH, ldconfig cache and common
Linux directories; caller RPATH/RUNPATH, audit and full hardware policy differ.
It does not execute input library constructors. Prefer explicit paths.

Core conversion supports x86 32/64 registers and overlay reconstruction. Zero
floating states match upstream's TODO. Diagnostics differ. HOME-unset lookup
uses procfs/passwd without NSS. CLI readers copy entire regular files with a
snapshot size bound, so large real inputs can cost more memory than mmap.

Native Linux x86_64 libsimple static archive: 13 header synchronization functions
plus the actually exported cmpxchg_wrapper_u32 helper (14 external symbols).
Four-byte C state layouts are preserved. The atomic state-machine module forbids
unsafe; C state conversion, callback calls and libc futex syscall are narrowly
unsafe and documented. REQUEUE storage must remain alive/stable through ALL
requeued waiters, beyond the notifying syscall. Nullable completed-once callback
is supported. This source is PARTIAL: Darling/Darwin futex backend, other
architectures and runtime/bootstrap integration remain unported. Cargo
build/staging emits a no_std panic-abort native archive; C consumers use -pthread.
Normal Rust development/tests retain default std; see SAFETY.txt for C-only panic
handler ownership. Darwin startup integration remains pending.

Cargo xtask builds migrated packages and stages six tools, the native archive and native errno validation object
using the typed graph xtask/host-artifacts.tsv. Real staged tools are executed;
a separate C stress harness links the staged archive. This does NOT replace all
upstream generation, cross-linking, architecture or install rules. The optional
upstream-rust-tools.patch remains an EARLIER THREE-tool adapter snapshot only.

## Scope tracking

- Superproject: 27,274 entries; 5 implemented, 2 partial, 27,267 pending.
- Dependencies: all 160 exact-pin trees inventoried; 265,803 committed leaves,
  one partial errno source slice and 265,802 pending; 341 license filename candidates with actual blob hashes.
  Source category labels are suffix heuristics, not a semantic language count.
- Observed default CMake File API graph: 1,629 targets, 7,785 dependency edges,
  34,478 source records (9,593 generated), 1,238 targets with install metadata.
  Six default targets have Cargo artifact slices including partial ones;
  1,623 remain pending. No legacy target retirement is claimed. Optional
  print_wrapped_elf is absent from this default configuration. Custom-command
  bodies, optional configurations and generator-specific recipes need more work.

## Verification and reproducible commands

Latest cargo xtask check passed formatting, strict Clippy, 77 ordinary tests.
Five optional differential suites were explicitly run successfully. Eight Python
inventory/graph tests pass; both inventories and graph regenerate identically.
Cargo release build, native C ABI differential/concurrency tests and generated
wrapper compilation/linking to Mach-O pass. No full runtime/app validation exists.

Run from this repository:

    cargo xtask check
    cargo xtask build
    cargo xtask install --destdir /an/exclusively-owned/staging-directory
    python3 -m unittest discover -s tools -p 'test_*.py' -v

For optional differential tests, set:

    UPSTREAM_GETUUID=/home/radud/Documents/Codex/2026-10-18/re/work/getuuid-upstream
    UPSTREAM_ELFDEP=/home/radud/Documents/Codex/2026-10-18/re/work/elfdep-upstream
    UPSTREAM_BSDLN=/home/radud/Documents/Codex/2026-10-18/re/work/bsdln-upstream
    UPSTREAM_WRAPGEN=/home/radud/Documents/Codex/2026-10-18/darling-rust-migration/work/upstream-wrapgen
    UPSTREAM_PRINT_WRAPPED_ELF=/home/radud/Documents/Codex/2026-10-18/re/work/print-wrapped-reference/print_wrapped_elf
    DARLING_COREDUMP_REFERENCE=/home/radud/Documents/Codex/2026-10-18/re/work/full-baseline-build/src/hosttools/darling-coredump

Export those variables, then run:

    cargo test --workspace --offline --locked -- --ignored
    crates/darling-libsimple/scripts/check-abi.sh /path/to/upstream \
      target/release/libdarling_simple.a /path/to/reference-output
    python3 tools/check_wrapper_macho.py --upstream /path/to/upstream \
      --wrapgen target/release/wrapgen --elfdep target/release/elfdep

Reference build scripts use pinned committed source objects. Only the native
x86_64-unknown-linux-gnu Rust target was installed at handoff. Rust 1.85 minimum,
32-bit host, Miri/sanitizers and unsupported runtime targets remain unverified.

Measured synthetic 64 MiB zero-fill CLI: 31.513 ms upstream vs 22.336 ms Rust,
equal normalized output, 21 alternating samples; separate RSS 3892 vs 1944 KiB.
This is workload-specific. Whole wrapgen CLI was slower in Rust (1.202 vs 1.066 ms).
Rust-only warm LD_LIBRARY_PATH lookup improved ~855 us to ~3.62 us by avoiding
unneeded ldconfig processes. Scripts and raw records are committed. No general
Darling performance improvement has been established.

## Continue with real runtime/build work

Use independently testable components and parallel implementation/review. The
native libsimple slice is a concrete runtime starting point: remove inappropriate
std/bootstrap dependencies, implement required target backend only with real ABI
and concurrency validation, and map it into actual consumers before retiring
original targets. Also advance Cargo code-generation/link/install edges using
the captured original graph and actual generated artifacts. Choose tasks based
on dependency order and evidence, not the ease of marking inventory entries.

Keep regression coverage, source/target inventories, unsafe ledger, verification
records and docs accurate as code changes. Separate original defects from intended
behavior. Preserve all exported symbols, including helpers with external linkage.
Do not fake missing runtime subsystems, hide CMake inside build.rs, or describe
source inventory/graph capture as completed migration. Full completion requires
all remaining implementation, ABI/application compatibility and packaging gates.

Continuation 2026-10-18: native no_std archive
-------------------------------------------
Cargo build and staging now explicitly disable libsimple's default std feature
and use panic=abort. C syscall/abort are documented boundaries; no Darwin backend
or full darlingserver consumer build is claimed. C ABI differential/concurrency,
73 ordinary workspace tests, seven Python tests, strict normal/no_std Clippy,
staged no-std archive member checks and SIGABRT invalid-callback test pass.
Independent review rebuilt/linked/reran the C harness in an isolated target dir.
See verification.json for exact record. Source/target status counts are unchanged.

Continuation 2026-10-18: native errno runtime slice
------------------------------------------------
crates/darling-errno ports exact-pin xnu emulation conversion/errno.c as no_std
with no OS calls, allocator or panic handler. Both original global C symbols,
hidden visibility, length=512, signed mapping and unknown passthrough are retained.
INT_MIN overflow is fixed; tools/check_errno.py reproduces original UBSan failure
and compares 131,073 range inputs plus one million random inputs using committed
headers. Cargo stages a runtime-free native ELF object; it is not the required
Darwin Mach-O/fat object. Full emulation targets remain pending with source-slice
annotations; dependency inventory has one partial source and 265,802 pending.
77 ordinary tests, eight Python tests, C linkage beside libsimple, exact two
hidden globals/no undefineds, deterministic repeated object staging and late
object-build failure tests pass. Independent review reproduced differential and
UBSan gates. Native call benchmark: C3.042ns, Rust2.803ns; raw match/const-array/final
records are preserved, and no general runtime speedup is established.
Continue with real Darwin backend/target linking and actual consumers. The next
backend path is src/external/xnu/darling/src/libsystem_kernel/emulation/src/
linux_premigration/ext/futex.c, used by libsimple's __linux_futex_reterr branch.
Its Linux raw-syscall and Darwin errno/cerror contracts need separate validation.
