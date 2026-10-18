# Dearust

Public repository: https://github.com/countradooku/dearust
Project branding is Dearust; use Dearust Cargo package/crate names; preserve exported runtime ABI names.

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
to satisfy scope, or claim application compatibility beyond recorded execution.
Selected macOS command-line checks now run in a locally modified installed runtime
with three Rust slices; the full migration is INCOMPLETE.
Follow later user instructions if they revise these priorities.

## Read first

- README.md, BUILDING.txt, MIGRATION.txt, verification.json
- This file and crates/dearust-libsimple/SAFETY.txt
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
  Original emulation and system_kernel targets now also build; full Darling runtime
  has NOT been built. 44 Swift Git LFS binary payloads remain
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
is supported. This source remains PARTIAL: native and Darling x86_64 backends work and the
Darling objects link into actual system_kernel. i386/other architectures and
clean pinned-runtime startup and general application compatibility remain unverified. Cargo
build/staging emits a no_std panic-abort native archive; C consumers use -pthread.
Normal Rust development/tests retain default std; see SAFETY.txt for C-only panic
handler ownership. Installed-runtime startup has the narrower scope recorded below.

Cargo xtask builds migrated packages and stages six tools, the native archive and native errno validation object
using the typed graph xtask/host-artifacts.tsv. Real staged tools are executed;
a separate C stress harness links the staged archive. This does NOT replace all
upstream generation, cross-linking, architecture or install rules. The optional
upstream-rust-tools.patch remains an EARLIER THREE-tool adapter snapshot only.

## Scope tracking

- Superproject: 27,274 entries; 5 implemented, 2 partial, 27,267 pending.
- Dependencies: all 160 exact-pin trees inventoried; 265,803 committed leaves,
  two partial errno/futex source slices and 265,801 pending; 341 license filename candidates with actual blob hashes.
  Source category labels are suffix heuristics, not a semantic language count.
- Observed default CMake File API graph: 1,629 targets, 7,785 dependency edges,
  34,478 source records (9,593 generated), 1,238 targets with install metadata.
  Seven default targets have Cargo artifact slices including partial ones;
  1,622 remain pending. No legacy target retirement is claimed. Optional
  print_wrapped_elf is absent from this default configuration. Custom-command
  bodies, optional configurations and generator-specific recipes need more work.

## Verification and reproducible commands

Latest cargo xtask check passed formatting, strict Clippy, 84 ordinary tests.
Five optional differential suites were explicitly run successfully. Eight Python
inventory/graph tests pass; both inventories and graph regenerate identically.
Cargo release build, native C ABI differential/concurrency tests and generated
wrapper compilation/linking to Mach-O pass. Selected installed-runtime application
checks pass as described below; full runtime/app validation remains incomplete.

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
    crates/dearust-libsimple/scripts/check-abi.sh /path/to/upstream \
      target/release/libdearust_simple.a /path/to/reference-output
    python3 tools/check_wrapper_macho.py --upstream /path/to/upstream \
      --wrapgen target/release/wrapgen --elfdep target/release/elfdep

Reference build scripts use pinned committed source objects. Both x86_64-unknown-linux-gnu and x86_64-apple-darwin Rust targets are installed. Rust 1.85 minimum,
32-bit host, Miri/sanitizers and unsupported runtime targets remain unverified.

Measured synthetic 64 MiB zero-fill CLI: 31.513 ms upstream vs 22.336 ms Rust,
equal normalized output, 21 alternating samples; separate RSS 3892 vs 1944 KiB.
This is workload-specific. Whole wrapgen CLI was slower in Rust (1.202 vs 1.066 ms).
Rust-only warm LD_LIBRARY_PATH lookup improved ~855 us to ~3.62 us by avoiding
unneeded ldconfig processes. Scripts and raw records are committed. No general
Darling performance improvement has been established.

## Continue with real runtime/build work

Use independently testable components and parallel implementation/review. Native
and Darling x86_64 synchronization/futex/errno slices now have real consumer
execution/link checks and installed-runtime command-line smoke coverage. Continue
with i386, emulation_dyld/static consumers, clean pinned-runtime startup,
cancellation semantics and broader application validation before retiring
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
crates/dearust-errno ports exact-pin xnu emulation conversion/errno.c as no_std
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

Continuation 2026-10-18: Darling futex and full actual consumer link
-----------------------------------------------------------------
The dearust-libsimple feature selects __linux_futex_reterr and bootstrap
__simple_abort. runtime-object omits the final panic handler; default native
static archive behavior remains available. dearust-futex is no_std and preserves
both exported wrappers over the original seven-argument raw linux_syscall ABI.
Its cerror declaration now matches the actual LP64 unsigned128 return, rather
than the old source's mismatched long declaration; cancellation may terminate
threads but must not unwind through Rust. Installed-runtime TLS smoke tests now
pass; cancellation semantics remain untested.

`cargo xtask runtime --target x86_64-apple-darwin --destdir PATH` emits three
objects and a thin libsimple archive through xtask/runtime-artifacts.tsv.
The native target builds the same Darling ABI chain for execution tests. No Rust
allocator/panic runtime is bundled; exact exports/imports and private-extern errno
flags are checked, including the explicitly-run optional Darwin artifact test.

Original `emulation` and `system_kernel` CMake targets now build successfully.
Their build logs are preserved at continuation work/original-emulation-build.log
and original-system-kernel-build.log. `tools/relink_system_kernel.py` integrates
the Cargo x86_64 objects/archive into the full actual final link (890 original
response objects), outside the original build tree. It preserves the same 1,362
public definitions and 18 imports, verifies real symbol provenance to original
cerror/raw syscall/abort and real guarded/close consumers, and recombines the
original i386 final slice byte-identically. No new stubs/dynamic-lookup mode are
added. This is a mixed-language link, not complete Rust/Cargo runtime migration.

`tools/check_darwin_runtime.py` executes unchanged pinned guarded/table.c and
full close.c with real raw syscall assembly (ELF spelling adapter only) through
both original and Rust chains: 120,000 concurrent table cycles, capacity/collisions,
fork-child cleanup, real pipe close, 22 direct errno/TLS futex checks and the full
existing C synchronization stress pass. Only host cerror TLS/abort are test
adapters there; the actual Mach-O full link uses original production boundaries.
Records: runtime-consumer-verification.json and system-kernel-integration.json.
84 ordinary tests, eight Python tests, eight feature-darling tests, strict Clippy
and the optional Darwin artifact integration test pass. Subsequent Mach-O launch
evidence is recorded in the installed-runtime continuation below.

Retained isolated worktrees (inspect before reuse): continuation work/
{darwin-libsimple,futex-port,runtime-consumer}. The continuation root is
/home/radud/Documents/Codex/2026-10-18/darling-rust-continuation.
Staged runtime objects are under work/{native-runtime,darwin-runtime}; the final
full linked mixed library is under work/system-kernel-rust-verified. Parent original
baseline tree/build remain unchanged reference inputs. Full target and source
counts stay partial: seven target slices, 1,622 pending targets, two partial
pinned dependency sources and 265,801 pending dependency entries.


Continuation 2026-10-18: actual installed-runtime application execution
---------------------------------------------------------------------
Actual x86_64 mixed-runtime boot and command-line execution now pass with the
Rust errno/futex/libsimple slices. The comparison is a matching original relink;
both use existing objects from /home/radud/darling-lol/build/darling-src/build.
These objects belong to the locally modified installed source tree, not the clean
pinned baseline. Installed/build kernel and server files are byte-identical.
Both relinks retain 1,370 public definitions and 40 imports. The running launchd
maps the copied Rust dylib's inode, and its SHA-256 is checked.

Verified workloads include Bash pipelines, SQLite table/insert/aggregate, Python
JSON/SHA-256, offline curl version output, Objective-C, pthread, TLS, fork and Mach
semaphores. Original and Rust comparisons pass. Do not expand this evidence into
a claim that arbitrary apps or GUI programs work. Selected GUI checks below pass;
cancellation semantics remain untested. Most runtime code, server and frameworks remain C/assembly.

The installed local protocol adds fileport_makeport/fileport_makefd, shifting
subsequent RPC numbers by 2. Pinned task_self_trap=33 becomes installed
pthread_canceled=33, causing the same boot failure for original and Rust pinned
kernels. Matching local objects avoid that mismatch; clean pinned full-runtime
boot remains pending. Only x86_64 is exercised; no i386 Rust validation or target
retirement is claimed. Inventory counts remain unchanged.

Scratch relink.py/provenance.py, exact input-object hashes and tracked local
patches are under continuation work/installed-runtime-match. Its original and Rust
outputs are libsystem_kernel-original.dylib and libsystem_kernel-rust.dylib.
The latter has SHA-256 cb63eb32207f13d28beaf62ecb753708b4d0053ed8cbd2ad314e93a4d60b1480.
Do not overwrite the installed runtime or existing ~/.darling application prefix.

mac-app-verification.json records 29 checks per matching variant: 24 strict
expected-output passes and five shared mismatches. The latter are two lockset
assertions, a CFURL fixture requiring absent /home, unsupported/failing libproc
queries, and an extra timestamped cfbridge NSLog line despite a zero exit.
No status/exit regression was observed between the matching variants in this suite.
The record includes hashes and paths to raw execution and mapping evidence.


Continuation: GUI execution and verification runner fixes
--------------------------------------------------------
See GUI-VERIFICATION.txt and gui-verification.json. The unchanged Cocotron
TextEditor sample displays and accepts real XTest keyboard input; an independent
visual check confirms the text in its screenshot. The Calculator Cocoa fixture
displays controls and its button actions produce 42. These checks execute both
matching original and Rust kernels on a separate task-owned nested X display,
with actual copied-kernel inode/hash evidence. They are sample/fixture coverage,
not verification of arbitrary third-party apps. Existing Riot/League stays intact.

The old app runner had genuine bugs: daemon-inherited PIPEs could hang it, timeout
or stale completion could be mishandled, and missing /private/tmp and /home fixture
paths caused false failures. The reusable tools/check_mac_apps.py fixes these
with regressions. Legacy lockset_create is an unconditional KERN_FAILURE stub;
legacy proc_info call1 returns ENOTSUP. Neither failure is in the migrated slices.
No Rust runtime implementation defect was reproduced during atomics, futex,
errno, cerror/cancellation-source audit. Real SIGUSR1/EINTR retry now runs in
original/native Rust/Darling-backend ABI differentials, with eintr=1 in the record.
Do not convert passing source audits into untested cancellation claims.


Continuation: Rust locksets and process queries
--------------------------------------------
See LOCKSET-PROCESS.txt and lockset-process-verification.json. The Rust lockset
server and partial process-query dispatcher execute real Mach-O apps in isolated
unprivileged user/mount/PID namespaces, without modifying installed binaries or
the existing user prefix. All 29 app checks pass under the Rust kernel when the
known cfbridge timestamp is explicitly normalized (28 strict + 1 normalized). Both
Cocoa GUI checks and new lifecycle/process probes pass. 97 Rust tests, 24 Python
tests, formatting and strict Clippy pass. Earlier records above are historical.

Four dependency source slices are partial, 265799 entries remain pending. Target
retirement counts are unchanged. Legacy process flavors still delegate to C;
fixed-priority lock ordering uses enqueue-time priority; unlinked overlay map
identity remains limited. Full Rust migration and arbitrary app compatibility
are not established. Matching final artifacts are continuation work/
server-lockset-release/darlingserver and process-kernel-final/
libsystem_kernel-rust.dylib. Reproduction commands are in LOCKSET-PROCESS.txt.


Continuation: guarded table, process accounting and lstat
--------------------------------------------------------
See RUNTIME-EXPANSION.txt and runtime-expansion-verification.json. The combined
Rust runtime passes 28 exact app checks plus one explicit log normalization,
eight utility workloads, three GUI samples and three new runtime probes.
107 Rust tests and 29 Python tests pass, including explicit Darwin ABI checks.
Six dependency slices remain partial; 265797 dependency entries remain pending.
Matching and pinned links preserve original symbol sets; no target is retired.
Use copied fork/TLS bridges from the corresponding source/build, never mix
local-protocol bridges with the pinned baseline. Latest artifacts are in
continuation work/runtime-expansion-{stage,kernel}; server-lockset-release remains
the tested server. Preserve installed binaries and the existing user prefix.
README.md includes the project owner @countradooku and OpenAI Codex AI assistance.

Continuation: thread/path queries, fstatat and symlinkat
-----------------------------------------------------
Latest implementation/evidence: RUNTIME-NEXT.txt and runtime-next-verification.json.
Dearust-process now handles thread and executable-path queries with bounded,
transactional output. Unexpansion requires headroom for the original unchecked
/Volumes/SystemRoot prefix insertion; the pure path-length tests cover that limit.
New dearust-fstatat and dearust-symlinkat objects preserve original C conversion,
flag and virtual-root boundaries. symlinkat now honors its actual directory fd.
Use both new --fstatat-object and --symlinkat-object relinker options, matching
fork/TLS bridges separately for matching versus pinned links. Final artifacts
are dearust-continuation/work/port-next/{final-stage,matching-final,pinned-final}.
116 Rust tests / 29 Python tests, seven actual guest probes, sixteen utility
workloads, existing app checks and three GUI samples pass within documented
normalization/fixture limits. Eight dependency sources remain partial, 265795
entries remain pending, target counts remain seven / 1622 / zero retired.

Continuation: CPU/memory performance and readlinkat
-------------------------------------------------
User explicitly prioritizes very high CPU and memory performance. Preserve ABI
and correctness, avoid allocations/caches that change process lifetime semantics,
and measure actual work rather than infer gains from Rust. Latest evidence is
RUNTIME-PERFORMANCE.txt and runtime-performance-verification.json. In-place path
copying cuts generated local stack from ~12KiB to ~4KiB; address region queries
skip impossible metadata work and measure ~56% less CPU in the specified guest
workload. Do not turn those into general app or process-RSS claims. New readlinkat
object replaces one C source, preserving raw output/count behavior. Add its
--readlinkat-object relinker option to previous arguments. Final artifacts are
dearust-continuation/work/perf-next/{final-stage,matching-final,pinned-final}.
119 Rust tests, 29 Python tests and documented app/utility/GUI/probe gates pass.
Nine dependency sources remain partial, 265794 entries pending; target counts
remain seven Cargo slices / 1622 pending / zero retired.
