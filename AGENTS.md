# Dearust

Public repository: https://github.com/countradooku/dearust
Project branding is Dearust; use Dearust Cargo package/crate names; preserve exported runtime ABI names.

## Standing owner directive: quality and completion gates

The owner requires Dearust to become the best macOS-to-Linux compatibility
project, not merely a rewrite of Darling in Rust. This is the engineering
objective, not a claim that the current repository has achieved it. These
requirements apply to every future implementation and supersede conflicting
historical continuation notes below.

- Correctness, ultra-high performance, safety, and idiomatic Rust are mandatory
  priorities together. Performance includes CPU efficiency, throughput, latency
  (including tail latency), memory use, allocations, stack consumption, startup
  cost, and behavior under contention and sustained load. Do not assume Rust is
  faster, or silently sacrifice one priority to satisfy another.
- Known unresolved limitations, correctness defects, safety risks, and material
  performance regressions are strictly forbidden in work declared complete.
  Merely documenting a limitation or risk does not satisfy this requirement.
  Resolve it, validate the resolution, and add regression coverage. If it cannot
  yet be resolved, keep the affected work explicitly unfinished with its failing
  completion gate visible; never conceal uncertainty or claim risk-free behavior
  without evidence. Historical partial ports are unfinished work to remediate,
  not exceptions authorizing future incomplete implementations.
- Implement everything undertaken fully: required Darwin/macOS semantics,
  supported ABI and architecture coverage, flags, error behavior, lifecycle,
  concurrency, cancellation, resource ownership, build/runtime integration, and
  applicable application behavior. Establish the required scope before coding;
  do not shrink it afterward to make an incomplete implementation appear done.
  No placeholders, success-returning stubs, ignored flags, unsafe fallbacks, or
  unimplemented branches disguised as compatibility. Host-kernel constraints
  require a correct, safe compatibility strategy or remain an unmet gate.
- Hunt for upstream and existing Dearust bugs while porting. Fix confirmed bugs
  rather than copying them, with tests that reproduce the defect and verify the
  corrected behavior. Revisit previously recorded limitations and risks as part
  of the ongoing implementation backlog; do not grandfather them in.
- Measure against the previous implementation and relevant original baselines
  using reproducible, representative native and actual guest workloads. Include
  cold/warm behavior, latency distributions, CPU, memory and concurrency where
  relevant. Microbenchmarks alone do not establish application performance.
  Investigate and eliminate material regressions before declaring completion;
  a safety fix accompanied by an unresolved performance regression is still
  unfinished against these joint requirements. In particular, revisit the
  guest-memory-copy overhead recorded in RUNTIME-HARDENING.txt rather than
  treating its measured cost as an accepted permanent tradeoff.
- Prefer safe, idiomatic Rust and clear ownership. Keep unavoidable OS/FFI/ABI
  unsafe boundaries minimal, auditable, hardened, and tested; documentation by
  itself is not proof of safety. Avoid unnecessary allocations, copies, syscalls,
  locks and caches; any optimization must preserve semantics and lifetime safety.
- The owner authorizes full use of `rustix` wherever it fits the implementation.
  Verify its target/backend, ABI, semantics, safety and measured performance;
  Linux host-tool support does not imply that its Darwin backend can issue the
  Linux syscalls required inside a Mach-O compatibility runtime.
- Keep related operations in cohesive subsystem crates, not one crate per
  syscall. Improve the architecture and compatibility rather than translating
  C mechanically or preserving poor design for its own sake.
- Study and borrow useful architectural and performance ideas from Wine/Proton
  and other compatibility systems when relevant. Adapt them to actual Darwin
  semantics and validate them here; their success elsewhere is not proof of
  correctness in Dearust. Respect source licenses, attribution, and provenance.

Report evidence candidly. Distinguish implemented, verified, and still-unfinished
work. The prohibition on limitations and risks is a completion standard, never
permission to suppress findings, remove architecture guards, weaken tests, or
promise universal compatibility that has not been established.

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

Core conversion supports x86 32/64 registers and overlay reconstruction. Actual
floating/vector images and lossless raw notes supersede upstream's zero-state
TODO; see CORE-REGISTERS.txt. Diagnostics differ. HOME-unset lookup
uses procfs/passwd without NSS. Other CLI readers copy entire regular files with
a snapshot size bound. CORE-INPUT.txt supersedes the core converter snapshot
with owned metadata and bounded payload transfer.

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

Continuation: unlinkat/linkat/renameat
------------------------------------
Latest evidence: RUNTIME-FILEOPS.txt and runtime-fileops-verification.json. New
allocation-free Rust directory-relative mutation wrappers retain original atfd,
flag/vchroot policies and syscall widths/order. Native stack remains ~4KiB for
unlinkat and ~8KiB for two-path operations. Native adapted roundtrip is 2.31%
slower; actual guest CPU medians are essentially equal. Preserve those scope
limits; do not claim a speedup. Three new NULL-path probes crash prior runtime
and pass Rust. 125 Rust tests, 29 Python tests and documented regression/app/GUI
gates pass. Final artifacts: dearust-continuation/work/fileops-next/
{final-stage,matching-final,pinned-final}. Add --unlinkat-object, --linkat-object
and --renameat-object to previous relinker options. Twelve dependency sources
partial, 265791 entries pending; target counts seven/1622/zero retired.

Continuation: mkdirat/faccessat/fchmodat
--------------------------------------
Latest evidence: RUNTIME-PERMISSIONS.txt and runtime-permissions-verification.json.
Three allocation-free x86_64 wrappers preserve syscall widths, atfd/flags/vchroot
policies and both fchmodat exports. The helper preserves real metadata errno,
fixing EPERM masking; retain legacy faccessat ignored flags, fchmod symlink no-op
and check/use race. Native/guest CPU roundtrip costs are 1.24%/2.26% higher; do
not claim speedups. Stack remains one 4096-byte path, plus 144-byte metadata on
the fchmod branch. 131 Rust tests, 29 Python tests, 14 guest regression probes,
16 utilities and documented app/GUI checks pass. Add --mkdirat-object,
--faccessat-object and --fchmodat-object to previous relinker arguments. Latest
artifacts: dearust-continuation/work/permissions-next/{final-stage,matching-final,
pinned-final}. Fifteen dependency slices partial, 265788 entries pending; target
counts seven/1622/zero retired. Installed binaries/user prefix stay untouched.

Continuation: descriptor modes, truncation and synchronization
-------------------------------------------------------------
Latest evidence: RUNTIME-FDOPS.txt and runtime-fdops-verification.json. Four
new no_std objects fchmod/ftruncate/fsync/fdatasync preserve original signed
widths, C int narrowing and errno boundary. Keep both fsync exports, original
generated cancellation stubs and raw EINTR propagation; no new hook/retry.
No heap/path storage. Native body uses one eight-byte syscall argument push;
no RSS claim. Native CPU+0.19%; guest median-1.97% with overlapping quartiles.
131 existing Rust tests, 29 Python tests, 18 guest probes, 16 utilities and existing
app/GUI checks pass. New relinker options --fchmod-object, --ftruncate-object,
--fsync-object, --fdatasync-object extend previous args. Latest artifacts:
dearust-continuation/work/fdops-next/{final-stage,matching-final,pinned-final}.
Nineteen dependency slices partial, 265784 pending; seven target slices/1622
pending/zero retired. Installed binaries/user prefix remain untouched.

Continuation: grouped descriptor subsystem
------------------------------------------
User explicitly requests grouping related operations into subsystem crates,
rather than continuing one crate per syscall; preserve this direction for future
batches. CPU and memory performance remain priorities. Latest evidence:
RUNTIME-DESCRIPTORS.txt and runtime-descriptors-verification.json. dearust-fd
replaces four prior fd crates and adds lseek/flock (six sources/seven exports).
Stage fd.o and pass --fd-object instead of previous four descriptor switches.
Do not mix grouped/individual options. Preserve lseek full 64-bit success, error-
only narrowing, original guest cancellation stubs and single-call EINTR. No
heap/path/cache/TLS storage. Workspace packages 23 -> 20; native CPU unchanged,
guest workload median 4.12% lower; matching dylib 632 bytes smaller, no RSS/general
app claim. 131 existing Rust tests, 29 Python tests, 20 probes and existing utility/
app/GUI checks pass. Final artifacts: dearust-continuation/work/descriptors-next/
{final-stage,matching-final,pinned-final}. Twenty-one dependency slices partial,
265782 pending; target counts seven/1622/zero retired. Installed/user prefix intact.

Continuation: uncached process identifiers in existing subsystem
---------------------------------------------------------------
Latest evidence: RUNTIME-IDENTIFIERS.txt/runtime-identifiers-verification.json.
Existing dearust-process now owns getpid/getppid/getpgrp/getpgid/getsid, no new
crate (still 20 packages). Runtime features queries ->process.o/matching-only and
identifiers ->process-ids.o/bothlinks use --no-default-features. Do not substitute
a default combined-feature object. Add --process-ids-object to previous arguments.
Preserve raw signed arguments/int narrowing, getppid 0->1 after conversion, no
cache across fork/namespaces, original guest stubs and single-call EINTR. Native
CPU+1.39% with overlapping quartiles; guest raw-query median-2.15%; mixed dylib
file-1608B, no RSS/general app claim. 131 existing Rust tests, 29 Python tests,
21 probes plus existing utility/app/GUI gates pass. Latest artifacts:
dearust-continuation/work/identifiers-next/{final-stage,matching-final,pinned-final}.
Twenty-six dependency slices partial, 265777 pending; seven target slices/1622
pending/zero retired. Installed binaries and existing user prefix intact.

Continuation: process group/session control, masks and descriptor limits
----------------------------------------------------------------------
Latest evidence RUNTIME-CONTROL.txt/runtime-control-verification.json. Existing
process identifiers feature adds setpgid/setsid (seven exports); fd adds
umask/getdtablesize (nine exports). Same --process-ids-object/--fd-object options,
20 packages. getdtablesize retains C sys_getrlimit, declared FULL 176-byte xnu_rlimit
(twoU64+reserved20LP64longs) initialized on stack; Cintstatusnarrow, negative
Darwin error returned unchanged, unsigned cur clampedINTMAX. Do not optimize
away the reserved ABI tail. Native frame 184 B matches originalC. No heap/cache/path
storage. Warm group-leader-child control/error workload nativeCPU+0.56% with
overlapping quartiles; guestmedian-2.34%, no successful-session creation/general
app/RSS claim. 131 existing Rust tests, 29 Python tests, 24 probes and existing
app/utility/GUI gates pass. Mutations use owned children/restored masks/limits.
Artifacts: dearust-continuation/work/control-next/{final-stage,matching-final,
pinned-final}. Thirty partial sources/265773 pending; target slices 7/1622/0.
Installed binaries and existing user prefix intact.

Continuation: consolidated pathname subsystem, rmdir and truncate
----------------------------------------------------------------
Latest RUNTIME-PATHS.txt/runtime-paths-verification.json. dearust-path replaces
eight single-operation crates and adds rmdir/truncate (ten C sources/11 exports).
All eight previous ABI modules are copied unchanged; shared copier retains three
tests. Packages 20 -> 13; 117 Rust tests after fourteen duplicate cases removed, 29 Python
tests and 26 guest probes plus existing apps/utilities/GUI gates pass. Preserve
module semantics/unsafe contracts; no heap/intermediate path arrays/cache.
Use --paths-object(paths.o), removing eight prior pathname object switches;
keep lstat/fstatat metadata objects and matching/pinned original TLS/fork bridges.
Old switches remain accepted separately for historical objects, not with paths.
Native CPU+2.11%, guest median-2.23% overlapping ranges; readonly errno data
1008 -> 126 bytes, mixed file-4768B; no general app/RSS claim. New NULL truncate
and overlong paths return bounded errors; prior guest probes fail 132/139.
Native test-only weak abort adapters satisfy unexercised consolidated imports;
never include them in production. All ten native fixtures still execute.
Artifacts: dearust-continuation/work/paths-next/{final-stage,matching-final,
pinned-final}. Thirty-two partial sources/265771 pending; target counts 7/1622/0.
Installed binaries and existing user prefix intact; full migration incomplete.

Continuation: consolidated metadata in pathname subsystem
--------------------------------------------------------
Latest evidence RUNTIME-METADATA.txt/runtime-metadata-verification.json.
Existing dearust-path adds stat/stat64/fstat/fstat64 and absorbs dearust-lstat/
dearust-fstatat; moved ABI modules byte-identical to5c44951. Packages13 ->11,
111 Rust tests after six duplicate copier tests removed,29 Python tests,28guest
probes plus existing app/utility/GUI gates pass. paths.o has19exports from14source
slices; --paths-object requires original --lstat-bridge without --lstat-object or
--fstatat-object. Conflicting individual switches are rejected. Matching/pinned
bridges stay separate, process queries matching-only. Retain original C metadata
converters. stat reads guest TLS before sibling delegation; fstat initializes144B
aligned8, preserves int status narrowing and kernel-error-before-NULL precedence.
Validfd+NULL now EFAULT rather than crash; invalidfd+NULL stays EBADF. fstat native
local160B frame+8push matchesC, no heap/cache. Shared errno readonly378 ->126B,
matching dylib-752B, no RSS/general app claim. Native metadata comparison uses C
entries plus sharedRustfstatat versusRustentries, not an all-Cchain; actual guest
uses raw legacy120Bstat and wide144Bstat64 layouts. Artifacts:
dearust-continuation/work/metadata-next/{final-stage,matching-final,pinned-final}.
Thirty-fourpartial sources/265769pending; target slices7/1622/0. Installed binaries
and existing user prefix intact; full migration remains incomplete.

Continuation: fix guest-pointer and permission limitations while porting
-----------------------------------------------------------------------
User explicitly requests fixing outstanding limitations alongside future ports,
keeping subsystem grouping and CPU/memory priority. Current evidence:
RUNTIME-HARDENING.txt/runtime-hardening-verification.json. dearust-path nowports
access/chmod too (16sources/21exports),11packages;111Rust/29Python/30guestprobes plus
existingapp/utility/GUI gates pass. Use --paths-object withoriginalTLSbridge; both
links replace16 originals once. Kernel selfmemorycopies replace rawguestderefs:
64B firstread,4032B continuationifneeded; checkedintegeraddress, freshscopedLinuxPID
(noforkstale cache), metadataCconverter intoinitializedlocal144B thenexact120/144
copyout. Invalid/protected/readonly mappings error; partialcopyoutmaywriteprefix
beforeEFAULT, reserved/paddingbytesnowzero. NoRustheap/signalhandler/unsafe fallback.
faccessat strictXNUmask0x30 uses439 forflags,269flags0. fchmodatmask0x20 usesatomic
452NOFOLLOW,268flags0, no metadata check/use or fake symlink success. Unsupported
kernel/policy errorspropagate; no unsafe fallback/cache. Follow currentledgers
SAFETY-MEMORY.txt and SAFETY-PERMISSIONS.txt; olderparagraphs historical.
Measured guestCPUcost metadata+63.49%, permissions+16.91%; native+253.33%/+61.77%.
Keepcost prominent, no broadperformance/RSSclaim. Optimized64Breadreducesinitial
safeimplementationCPU. fchmodhelperlocal160B->0B, copyoutlocal200B+16push; extra
calleeframes apply. Matchingfile-480B; readonlyerrno126B unchanged. Do not promise
whole-runtime memorysafety: maliciousmappedinternalaliases, unportedC/vchroot,
i386Rust, cleanpinnedboot and arbitraryapps remainoutsideverifiedscope.
Thirty-sixpartialdependencyentries/265767pending; targets7/1622/0. Artifacts:
dearust-continuation/work/hardening-next/{final-stage,matching-final,pinned-final}.
Installedbinaries/existinguserprefix preserved.


Continuation: guest-copy regression removal and signal/fork transactions
------------------------------------------------------------------------
Read RUNTIME-COPY-PERFORMANCE.txt and runtime-copy-performance-verification.json.
Original-C guest CPU regressions are resolved on the same measured workloads:
metadata -4.40%, permissions -6.80%, 42 samples/variant in reversed order.
Affinity-controlled latency/memory cohorts have20 processes/variant/workload;
p99 metadata10->10us and permissions32->30us, private dirty within one page.
These compare original C kernel code in the matching locally modified runtime,
using the same unchanged Rust server, not a pristine whole-Darling benchmark.
Do not turn these workload-specific results into broad compatibility claims.

New guarded exact-PC copies require BOTH signal bridge objects, ALL THREE RPC
bridge objects, and the single replacement vchroot object. BUILDING.txt gives
commands. Keep matching per-source sigexc -O0; builders explicitly select x64.
Strong imports reject missing integration. Native weak boundary adapters are
only for tests. PathBuffer has an initialized NUL prefix and uninitialized tail;
never form Rust byte references over that tail or reinstate 4KiB zeroing.
Fallback blocks catchable signals across fresh getpid and the whole kernel copy.
No persistent PID, mask, mapping, credential or metadata cache is introduced.
RPC child epoch/leaf cancellation, complete RT masks, descriptor ownership and
root transaction ordering are correctness contracts, not optional optimizations.
All confirmed resolver bounds/dotdot/long-symlink/root transaction bugs tested.

Final evidence: dearust-continuation/work/original-benchmark. matching-final5 and
pinned-final5 link final objects; native-final2/darwin-final2 reproduce paths.o
byte-identically. 112 Rust/35 Python tests, strict Clippy/fmt, Darwin ABI test,
17 native sanitizer fixtures,200008 C converter comparisons, resolver differential
and bounds/root checks, native signal/RPC regressions pass. New signal/fork/RPC
probes repeat under two prefixes;30 old probes,29 apps (one timestamp normalized),
16 utilities and3 actual GUI samples pass. Baseline failures remain recorded.
No installed runtime/reference/app prefix changed. Workspace still11 packages;
dependencies37 partial/265766 pending, target slices7/1622 pending/0 retired.
Regenerate dependency inventory from re/work/upstream with dependency-objects;
full-baseline alone lacks one nested object store and gives an incomplete count.
Full migration remains unfinished. Continue from verified subsystem dependencies.


Continuation: stream I/O and original-C baseline repair
------------------------------------------------------
Latest commit implements read/write ONLY in existing dearust-fd (stream.rs).
Read RUNTIME-STREAMS.txt/runtime-streams-verification.json. fd.o14 globals,
13 private +public __write_for_xtrace; both relinkers replace read.c/write.c
through existing --fd-object. Strong imports sys_pthread_canceled and __real_ioctl
remain C runtime boundaries. Native stubs are test-only. Preserve full64-bit
Darwin count, INT_MAX rejection after fd/access/O_PATH validation, cancellation
once before work, no retry, raw kernel guest-buffer validation, and cold local
PTY probes. TIOCGPTN excludes slave/background EIO; legacy master fallback checks
fstat char-major2 including high bits. No heap/shared scratch/cache/new locks.

Exact original C reproduces count2^32 returning0 and background slaveEIO returning
falseEOF; Rust fixes both. The matching read.c has LOCAL rclol_trace_io code, so
final performance reference compiles exact pinned read.c/write.c with matching
flags and relinks them into owned original-stream kernel. Initial5.36% result
superseded. Final original-C guest workload -5.73% CPU, native-0.27% overlapping
quartiles, p99 2->2us; private dirty3512->3516KiB, no whole-process RSS claim.
Same unchangedRustserver/localmatching context; not whole-Darling performance.

Artifacts: dearust-continuation/work/stream-next/{native-final,darwin-final,
matching-final,pinned-final,original-stream}; exact benchmark/resource reports
have exact- prefixes. 112 Rust/35 Python checks, explicitDarwinABI, native plain+
ASan/UBSan C fixture and original-bug reproducer, newgueststream regression,
30existing +3signal/RPC probes,29apps(one timestamp normalized),16utilities,
3GUI samples pass. Baseline failures retained. Installed/runtime references and
existing appprefix untouched. Inventories39partial/265764pending,11packages,
7targetslices/1622pending/0retired. Fullmigration unfinished.

Candidate next dependency work includes positioned I/O (must address Darwin
pwrite ignoring O_APPEND, unlike Linux pwrite) or resource subsystem. A read-only
resource audit found originalgetrlimit NULL/failure writes, infinity-domain
mismatch, getrusage maxrssKiB/bytes and timevalpadding/who mismatches. These are
PENDING defects in unported resources, not fixes accomplished by this batch.
NOFILE-1 originated disabledold driver relocation; establish deliberate descriptor
accounting before changing policy. Do not mechanically copy these defects or
broaden scoped completion claims. All current strong memory/signal/RPC contracts
and rustix authorization remain in effect. Continue substantive porting batches.


Continuation: traditional pathname frontends and readlinkat correctness
-----------------------------------------------------------------------
Read RUNTIME-PATH-FRONTENDS.txt/runtime-path-frontends-verification.json. Six
traditional entries readlink/symlink/link/unlink/rename/mkdir now in existing
path subsystem abi/frontends.rs. Capture originalTLS once, share fd across both
link/rename arguments, preserveflags0/mode/target. paths.o35 private globals,
27ABIentries +8copyhelpers; imports and all strong bridges unchanged. No new
crate, heap, extra pathbuffer, lock/cache/cancellation or addedsyscall by glues.

Fix canonicalreadlinkat to usize count; >INTMAX rejects before anypath/fd/output
lookup, matching pinnedXNUvfs_syscalls.c6711. Traditionalreadlink remainsint32,
negativeconvertstousize correctlyfails. Size0 uses1LOCALinitializedbyte Linuxprobe
andreturns0, retainingpath/type/permissionerrors withouttouchingguestoutput.
The previous zeroEINVAL test was wrong againstXNU. Retainall30existingprobes,
refreshONLYreadlinkat-regression binary; all29othersbyteidentical/goldenssame.
Newindependentwidthguest reproduces4294967297 truncatingto1 andwritingbyte before
fix; newkernelreturnsEINVAL untouched. Do not reinstateintwidth or zeroEINVAL.

Artifacts dearust-continuation/work/path-frontends-next/{native-final,darwin-final,
matching-final,pinned-final}. NativeCfrontends+sameRustcanonicalbackend comparison
is NOT all-C; +0.86%median withoverlap. GuestfullCchain73.6425->70.6675us/8calls,
-4.04%CPU; p99median204->142.5us, private dirtyunchanged3512KiB. All13entryunits
matchexactpin; retainedhelpers/surroundinglocalruntime/sameRustserver NOT pristinewholeDarling.
Allrawoutliers/maximumvariation/RSSrecorded, no universalperformanceclaim.
112Rust/35Python, strictfmtClippy, explicitDarwinABI, nativeplain+ASan/UBSan and
independentrepeat, actualnewregressions/30old/3signalRPC/29apps(one timestampnormalized)/
16utilities/3GUI pass. Installed/reference/app prefixes untouched. Inventories
45partial/265758pending,11packages,7targetslices/1622pending/0retired. Fullportunfinished.

PositionedI/O was audited before implementation and deferred as a separate
undertaking: Darwin permits some negativechar-device offsets, pwriteignoresappend,
-1mustneverreachLinuxpwritev2 (streampositionsemantics), OFF_T_MAXhas specialbehavior,
andrawx64pwritev2 takesFULL64offsetinarg4,arg5=0 (not32/32split). Needcorrectdevice/
olderhoststrategy beforeclaimingcomplete; no unsafeF_SETFL toggle/reopenfallback.
Resource/NOFILE-policy pendingdefects frompriorhandoff remainunimplemented.
Continue by dependencyorder with concretecorrectness/performancegates.


Continuation: process entropy and thread identity
-------------------------------------------------
Read RUNTIME-PROCESS-SERVICES.txt/runtime-process-services-verification.json.
getentropy/thread_selfid now in existing dearust-process/services.rs, identifiers
feature, existing process-ids.o.9globals:8private plus PUBLICthread_selfid forxtrace.
Both --process-ids-object relinkers replace7oldIDs+2misc originals once.Queries
process.o matching-only, all strong bridges and otherobjects unchanged.

PinnedXNU executable randomdev/copyio contracts supersede conflictingmanpage:
>256EINVAL beforepointer;0success noaccess; rawboundedgetrandom318flags0; noRust
references/outputbuffer/cache/heap; negativeerrorsconvert once; smallpartialfault
EFAULT withoutretry; startupEINTRretry; impossibleoverreturnEIO. threadselfid186
uncached withoriginalintnarrowing. NeverrestoreC signed/unsigned false-success.

Native+actualguest regressions reproduceolddefects/fixnew, concurrentthreads/fork,
plain+ClangASanUBSanCboundaries.112Rust/35Python, strictfmtClippy, bothABItests,
30existing/3signalRPC/29apps(one timestampnormalized)/16utilities/3GUIpass.
ActualoriginalC benchmark must use build_original_process_services.py: matching
base.h routesoldCthroughlocaltracewrapper; ownedpinnedbaseheaderoverlayremovesthat
confound. Same unchangedRustserver/localruntime; nopristinewholeDarlingclaim.
CPUguest+0.26%,native-0.20%, bothoverlappingquartiles, nomaterialchangeestablished;
p99median2->2us, private dirtywithinonepage, noRSSreductionclaim. No hiddenoutliers.
Firstmeasuredround follows pthread_threadid_np, notuntouchedcoldprocess.
Artifacts: dearust-continuation/work/process-services-next/{native-stage,darwin-final,
matching-final,pinned-final,original-services};47partial/265756pending,11packages,
7targetslices/1622pending/0retired. Installedreference/userprefixunchanged.
Fullmigrationunfinished; resource/positionedI/O auditsremainpending asabove.

RawbufferRustAPIrepair: getentropy and5existingstream exports are now unsafeextern
withcallerownership/alias/concurrency/inputinit contracts. Kerneladdressvalidation
isnotRustborrowvalidation. Threecompilefailchecks (115Rustchecks total) enforce
unsafe calls; CABI andallnative/Darwin objectbytesunchanged. Privateentropyhelpers
propagateunsafecontractexplicitly. Do not expose these as safe rawpointerRustAPIs.


Continuation: filesystem accounting, mount identity and descriptor pressure
-------------------------------------------------------------------------
Read RUNTIME-FILESYSTEM.txt/runtime-filesystem-verification.json. Four statfs
entries now share dearust-path, 24 source slices/39 private paths.o globals.
No new crate; 11 packages. Initialized2168-byte layout, frsize block units, optimal
IO size, flags, final symlink following, anonymous-fd EINVAL, exact mount identity
and guarded copyout fix confirmed defects. Keep unsafe Rust ownership contracts.

Fast path: pinned fd, raw 138 authorization/accounting, statx UNIQUE mount ID,
statmount 457 flags0/current namespace/root, mask0x330 including optional subtype.
Never restore BY_FD (foreign mount namespace/root) or drop fuse-style subtype.
Reuse the4096-byte point scratch; initialize only size word before statmount,
form references only over the checked initialized prefix. Older/oversized queries
stream procfs4096-byte pread chunks by legacy mount ID; no mount/capability cache.

Keep source pin alive through cold SCM_RIGHTS RPC83; never reuse overwritten
point storage as pathname or retry a server error with an unpinned descriptor.
Full tables and hard/softNOFILE=0 work, including fd0 and F_DUPFD EINVAL. Client
raw 137/138 probes and type/fsid checks, server userns/actual UID/GID/groups/CapEff/
LSM checks remain mandatory; pathname mode additionally compares root/mountns.
Host feature is Linux-only. Server reads client PID/TID mountinfo, exact VM copy
counts, and owns generated received descriptors. All 83 wrappers preserve existing
fork/atomic/full-mask/rights cleanup. No speculative RPC83 to old servers.

Final artifacts work/filesystem-next/{native-final5,darwin-final5,matching-final5,
pinned-final5,server-final4}; host source capture source-final3. Both native/Darwin
repeat identically; other runtime objects match prior batch. Matching local public
lolsys_fill_mount_names/cache retained verbatim in compat object, unused by new
entries or exact C reference. Pinned80 protocol explicitly rebased83 for LINK-only
validation; no clean pinned boot claim. Preserve matching sigexc-O0 and all bridges.

126 ordinary Rust/3 compile-fail/35 Python checks pass, both ABI tests, native
plain/sanitizers/forced procfs, injected pressure/security/direct-query cases,
actual filesystem/NOFILE=0 guests, 30 old+3 signalRPC+4 prior regressions, 29 apps
(one timestamp normalized), 16 utilities and 3 GUI pass. Original failures recorded.
Native CPU -84.73%, guest -84.75% for four filesystem calls/round; p99 process median
158.5->27 us. Private dirty3516->3516 KiB, PSS+3.5 KiB, RSS+92 KiB with overlapping
quartiles; no RSS reduction/universal speed claim. Same NEW paired server for C
and Rust, exact pinned C entries/base.h overlay, retained matching runtime.
42 warm CPU samples/variant and 20 resource processes/variant; first round follows
100 warmups, not untouched cold startup. Forced fallback has correctness coverage;
performance claim is tested Linux 7.2.9 direct-query scope. Installed/reference
runtime and existing user prefix intact.49 partial/265754 pending, 7 target slices/
1622 pending/0 retired. Full migration and prior deferred resource/positioned/vector
I/O/credential/dup lifecycle audits remain unfinished; do not claim those fixed.


Continuation: truthful core register state and host-tool I/O costs
----------------------------------------------------------------
Read CORE-REGISTERS.txt/core-registers-verification.json. Existing dearust-coredump
now preserves actual x86/i386 FP, FXSR and standard XSAVE, with exact Darwin
FLOAT/AVX/AVX512 encoding and per-thread lossless LC_NOTE archives. No new crate,
source-port count or runtime object. Missing/partial state must never be invented
as a complete zero-filled register set. Keep FSAVE-only raw; it lacks SSE/MXCSR.

Source CPU metadata is process-wide and may follow all thread notes. Validate
once, borrow it, and never decode using converter-host CPUID. The finite historical
mask/size domains support known Intel/AMD cores; unknown features require explicit
layout metadata. Linux exports uncompacted XSAVE even from compacted internal
storage. Preserve inactive-state initialization, full x64 pointers, raw software
bytes, unknown metadata-backed components, owner namespaces and unique TIDs.
Reject malformed input before touching the destination. All disjoint PT_NOTE
segments are processed in file order. Do not add four-byte padding to core x64
LC_THREAD; XNU emits its legitimate size modulo 8 = 4, and readers accommodate it.

Use fixed GPR arrays and borrowed FP images. CLI snapshot reads reserve their
checked size once, retaining length-change safety. Small zero padding uses 64-byte
scratch, output has one 64 KiB buffer. Profiling removed repeated 64 KiB clears and
214 small writes for 256 full-state threads; final 23 writes. No unsafe mmap or
per-thread raw-image heap allocation. The extra raw/derived state is real output,
so comparisons must show byte counts and additional work, not claim equivalence.

158 ordinary Rust checks, 3 compile-fail checks, 35 Python checks, strict fmt/Clippy,
pinned C differential, 8 independently decoded actual captured states, 25 separate
adversarial CLI cases and staged/release output equality pass. tools/check_core_states.py
reproduces exact SDK ABI probes, owned x64/i386 ptrace captures and reader checks.
LLDB 22 x64 loads GPRs but cannot read Mach-core FP; its i386 reader crashes for
original C/prior Rust/current Rust/GPR-only alike. These are recorded external
reader defects, not passed application gates or new format regressions.

Final performance has 24 balanced fresh-process samples/variant/input, 12 scoped
owned-input fadvise samples and 36 same-output-inode zero-fill repeats. Actual
captured i386/x64 CPU 452.5/457 us vs prior 468/481.5 us. Full 256-thread CPU 1486.5 us vs
C 1639.5/prior 1143 us while output grows 183328->1467424 bytes. Added output cost
remains visible. Zero-fill difference does not repeat in the same-inode cohort.
Large 64 MiB input CPU improves 19998.5->16253 us vs prior but C 10795.5 us remains
faster. The broader safe streaming/input architecture optimization is UNFINISHED;
full-file input storage and its CPU cost are not fixed by this register batch.
Do not claim universal faster-than-Darling behavior or complete that gate on docs.

Evidence is dearust-continuation/work/core-state-next and outputs/core-registers-verification.
Installed kernel/server and previous owned server hashes remain unchanged. Prior
runtime/app checks retain their historical scope; no runtime change was installed.
Still 11 packages, 49 partial dependency leaves/265754 pending, 7 target slices/
1622 pending/0 retired. Full migration remains unfinished.

Additional read-only audits from this batch are preserved, not implemented:
work/timestamps-next covers original futimes NULL crash, usec/ns error, object
semantics, XNU copy ordering and null-time authorization. A correct strategy must
handle descriptor exhaustion and owner-or-write authorization without timestamp
precision or lifetime mistakes. work/memory-locks-next covers nested XNU wiring
counts versus Linux boolean locks and failed-call partial effects; mmap/unmap/
remap/fork lifecycle is required. Earlier resource, positioned/vector I/O and
credential/descriptor-lifecycle audits also remain pending. Do not mechanically
port those wrappers or report audit findings as completed fixes.


Continuation: bounded core input and non-destructive file selection
------------------------------------------------------------------
Read CORE-INPUT.txt/core-input-verification.json. Existing dearust-coredump owns
ELF/program-header/PT_NOTE metadata and borrows FP images from it, then transfers
dumped PT_LOAD bytes without a full-file Vec or unsafe mmap. Shared parser still
validates ALL load ranges/notes/state/layout before destination access, including
inaccessible ELF32 virtual-range overflow. fp.rs and
source CPU layout/raw-note contracts are unchanged. A concurrent rewrite is not
an atomic snapshot; observed length changes/short dumped reads return errors.
Late I/O errors may leave partial output, as before; never fabricate dumped data.

Open actual destination without truncation, compare its fd identity with pinned
input and selected backings, then truncate the SAME fd for regular output. Preserve /dev/null and connected
FIFO stream destinations; only regular output uses sparse holes/final set_len.
Input/backing opens are nonblocking then require regular types; reject FIFOs
before reads. Check identity-only skipped device/zero ranges without spare fds. Pin unique backing
selections including absence; never reopen after alias checks. At 64 pins/pressure,
capture only requested ranges into safe rustix O_TMPFILE scratch before truncation.
No named scratch payload or guessed fallback. Real open/read/resource errors now
propagate; only NotFound/PermissionDenied mean unavailable. EMFILE may establish
absence with metadata without an fd; existing sources retain resource errors.
NOFILE5 missing-only and NOFILE16/32 hundred-file tests pass.

Large dumped segment offsets match source modulo4096; exact prefix/tail reads
surround aligned copy_file_range whole pages. Remaining large data may use an
owned CLOEXEC pipe with safe splice and at most 1 MiB kernel capacity. Fully drain
every batch before advancing, including unsupported write-side fallback. Optional
pipe creation failure before transfer uses exact reads. Unsupported/zero transfers
fall back to one lazy reusable initialized 1 MiB buffer. Kernel pipe memory is
excluded from process RSS; preserve that distinction in measurements. Packed layout remains if padding
would overflow. Zero padding is outside segments/notes, NOT extra fake state.
Large zero area tails use sparse output after initial same-fd truncation; final
seek/flush/set_len errors propagate and exact EOF is retained, including final
holes and raw notes after holes. Never omit final length or input/backing checks.

182 ordinary Rust/3 compile-fail/35 Python, all explicit differential/ABI gates,
48 independent fault/pressure cases, 25 retained adversarial cases, four real
captured-core variants (eight decoded thread states) and six staged/release byte
comparisons pass. Preserved
runtime/reference hashes unchanged. Workspace remains 11 packages; rustix 1.1.5 fs/pipe
adds locked host dependencies only (linux_raw backend on measured Linux x64).
No new runtime slice, source-port count, target retirement or installed change.
Exact benchmarks, cross-filesystem fallback and sparse/same-inode repeats are in
core-input-verification.json. Keep output-size and filesystem scopes visible;
never claim every conversion or the runtime is faster. Final same-inode64MiB
CPU C/prior/current is 8.715/14.645/2.985 ms on btrfs, and
13.950/18.943/12.460 ms tmpfs-to-btrfs. Process peak RSS current is about2.4MiB;
pipe kernel memory is additional. See raw tails and zero extent-history cohorts.
Full migration and pending
timestamp/wiring/resource/positioned-vector-I/O/credential/fd audits remain open.


Continuation: resource ABI and fixed binary guest copies — UNFINISHED
---------------------------------------------------------------------
Read RUNTIME-RESOURCES.txt/runtime-resources-verification.json and process
SAFETY-RESOURCES.txt. sys_getrlimit/setrlimit/getrusage + original mapping helper
join existing process-ids.o (identifiers,resources), no new crate/artifact.13
globals:12 private and public thread_selfid. Both existing relinker options replace
three C inputs, preserving helper provenance and full public/import sets.
paths.o has42 private globals including strong copyin/out/new exact input PC.
Do not use native weak abort test adapters in a runtime link.

Copy exact16 bytes before set validation. Get validates resource/query before
copyout; usage accepts onlySELF0/CHILDREN-1. Rlimit guest ABI16, not original
header176. getdtablesize agrees with real NOFILE, without obsolete LKM +/-1.
Darwin usage144 has seconds64/usec32/zero padding; RSS is bytes. Conversion mutates
owned kernel storage in place; no extra144-byte output array, no guest refs, no
new heap/fd/cache/lock/cancellation. Shared copy mode2 has its own fault PC, existing
mask/readiness/AC checks, signal/fork abort and blocked fresh-PID kernel fallback.
Do not remove these checks to recover performance.

The resource subsystem is NOT COMPLETE. Darwin infinity=i64MAX versus Linuxu64MAX
cannot losslessly represent every valid unsigned pair statelessly. Current
EOVERFLOW on sentinel collision/order inversion prevents silent corruption but
is NOT exact-XNU success behavior. Source-specific CPU/stack/data/nproc/Mach-wire
policy is still Linux-backed, not a port of XNU enforcement. Do not hide these
gates, invent an unsynchronized limit/mask cache, swap sentinels or use unrelated
limits as hidden state. Completing logical ownership requires fork/exec/external
limit/lifecycle proof. Keep this subsystem unfinished until the strategy is valid.

187 Rust/8 compile-fail/35 Python checks, all optional/differential/ABI gates,
repeatnative/Darwin, full links, native fault/injection/pressure/sanitizers and
actual guest tests pass. Final2 actualguest CPU three-call round increases
1.06095->1.74545us (+64.52%), native1.029864->1.725616us (+67.56%); forced
fallback5.087805us. These are material OPEN performance gates, not accepted
tradeoffs or a completed safe port. In-place conversion removed144B frame/copy
but does not eliminate one mask query per copy. All raw samples/tails/RSS and
original baseline failures are retained. No speedup or universal memory claim.

Final artifacts repo work/resources-next/{native-final2,darwin-final2,
matching-final2,pinned-final2,final-validation2}; paired server remains unchanged
filesystem-next/server-final4. Other strong signal/RPC/vchroot/TLS/fork bridges
remain mandatory. Installed/reference runtime unchanged. Inventories52partial/
265751pending of265803,11packages,7target slices/1622pending/0retired. Full migration
and prior timestamp/wiring/positioned-vector-I/O/credential/fd audits unfinished.
Continue substantive work against the resource domain and CPU gates; do not
count these three partial sources as complete or silently skip the findings.
