# Migrated host artifact staging

`cargo xtask install --destdir /path/to/stage` builds the migrated host tools
with Cargo and stages these executable paths:

- `src/bsdln/bsdln`
- `src/buildtools/elfdep`
- `src/buildtools/getuuid`

`host-artifacts.tsv` declares the Cargo package, binary target, destination and
pinned upstream CMake target definition. The same deterministic file is written
to `darling-host-artifacts.tsv` at the staging root. It has no timestamps or
checkout-specific paths. It describes the artifact graph, not content hashes.

The pinned upstream `src/bsdln/CMakeLists.txt` and
`src/buildtools/CMakeLists.txt` have no installation rules for these targets.
The staged paths reproduce the **build tree** layout consumed by
`Developer/symlinks.sh`, `cmake/dsym.cmake` and `tools/rpm/darling.attr`.
This command does not install a Darling runtime or replace its remaining CMake
graph. It invokes Cargo directly, without invoking CMake or a shell.

Staging builds specify the host triple from `cargo -vV`, `--offline --locked`,
and `--target-dir target/host-tools`. Their inputs are declared `--bin` targets;
output paths are derived from that explicit target directory and host triple,
without guessing from `CARGO_TARGET_DIR`. The usual `cargo xtask build` retains
its existing Cargo output-directory behavior.

Use a staging directory you own exclusively. Relative paths resolve against the
invocation directory; parent traversal and symlink directory components are
rejected. All input artifacts and existing output paths are checked before any
output tree is created. Symlinks and non-regular output files are rejected.
Executables are copied to a private temporary directory, preserving executable
permissions, then published with rename. Existing hard links are replaced
without modifying their other links. The graph manifest is published last.
Individual files are atomic replacements; the whole directory update is not a
transaction. A publication error may leave a partially updated directory and
returns failure. Concurrent hostile mutation of directory components is outside
the safe standard-library staging API's guarantees.

The integration test builds the actual declared binaries, executes all three
staged tools, checks repeatable manifests and verifies that a failing Cargo
build preserves its exit status without creating the output tree. Unit tests
cover symlink rejection, missing inputs, executable modes and hard-link
preservation. Add new functioning artifacts to the graph only after their
Cargo targets and compatibility tests exist.
