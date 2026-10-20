#!/usr/bin/env python3
"""Inventory every pinned Darling superproject leaf without fetching submodules.

Only Python's standard library and Git are required. Output has no local paths or
timestamps, so the same committed input produces byte-identical JSON.
"""

import argparse
from collections import Counter
import configparser
import json
from pathlib import Path, PurePosixPath
import subprocess


UPSTREAM_COMMIT = "60ba801decee7a00782f74f6be4c8ffb013f79ff"
REPLACEMENTS = {
    "src/bsdln/ln.c": "src/bin/bsdln.rs",
    "src/buildtools/elfdep.c": "src/bin/elfdep.rs",
    "src/buildtools/getuuid.c": "src/bin/getuuid.rs",
    "src/hosttools/src/coredump/main.cpp": "crates/dearust-coredump/src/lib.rs",
    "src/libelfloader/wrapgen/print_wrapped_elf.cpp": "src/wrapped_elf.rs",
    "src/libelfloader/wrapgen/wrapgen.cpp": "src/elf_wrapper.rs",
    "src/libsimple/src/lock.c": "crates/dearust-libsimple/src/lib.rs",
}
PARTIAL_REPLACEMENTS = {"src/libelfloader/wrapgen/wrapgen.cpp", "src/libsimple/src/lock.c"}
# Suffix hints only: these include tests and generated code, and do not prove
# hand-written implementation, production participation, or migration effort.
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cp", ".cpp", ".cxx", ".m", ".mm", ".s", ".asm", ".rs",
    ".swift", ".java", ".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08",
    ".l", ".lex", ".y", ".yacc",
}
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inc", ".defs"}


def git(repo, *args):
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo), *args])


def committed_blobs(repo, object_ids, *, gitdir=False):
    """Read exact blobs in one Git batch, without opening any working paths.

    Returned bytes include no batch framing. Callers decoding symlink contents
    use UTF-8 with surrogateescape so arbitrary committed bytes survive JSON
    serialization with ensure_ascii=True and encode(..., 'surrogateescape').
    """
    ids = sorted(set(object_ids))
    if not ids:
        return {}
    if any(len(oid) not in {40, 64} or any(c not in "0123456789abcdef" for c in oid)
           for oid in ids):
        raise ValueError("expected full Git object IDs for committed blob batch")
    command = ["git", "--no-replace-objects", "--git-dir" if gitdir else "-C",
               str(repo), "cat-file", "--batch"]
    output = subprocess.check_output(command, input=("\n".join(ids) + "\n").encode("ascii"))
    position = 0
    blobs = {}
    for oid in ids:
        end = output.find(b"\n", position)
        if end == -1:
            raise ValueError("truncated committed blob batch header")
        header = output[position:end].split()
        if len(header) != 3 or header[:2] != [oid.encode("ascii"), b"blob"]:
            raise ValueError("missing or non-blob committed object: " + oid)
        try:
            size = int(header[2])
        except ValueError as exc:
            raise ValueError("invalid committed blob batch size") from exc
        position = end + 1
        end = position + size
        if size < 0 or end >= len(output) or output[end:end + 1] != b"\n":
            raise ValueError("truncated committed blob batch content")
        blobs[oid] = output[position:end]
        position = end + 1
    if position != len(output):
        raise ValueError("unexpected trailing committed blob batch content")
    return blobs


def capture_symlink_targets(repo, entries, *, gitdir=False):
    """Add literal committed link content; never resolve or follow a target."""
    blobs = committed_blobs(repo, (e["object_id"] for e in entries if e["mode"] == "120000"),
                            gitdir=gitdir)
    for entry in entries:
        if entry["mode"] == "120000":
            entry["symlink_target"] = blobs[entry["object_id"]].decode("utf-8", "surrogateescape")


def component(path):
    parts = PurePosixPath(path).parts
    if parts[0] == "src" and len(parts) > 2:
        if parts[1] in {"external", "frameworks", "private-frameworks"}:
            return "/".join(parts[:3])
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else "root"


def category(path, mode):
    if mode == "160000":
        return "submodule"
    if mode == "120000":
        return "symlink"
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if name == "CMakeLists.txt" or suffix == ".cmake":
        return "build-cmake"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    if suffix in HEADER_SUFFIXES:
        return "abi-header-or-interface"
    if suffix in {".py", ".sh", ".pl", ".rb"}:
        return "script"
    return "resource-or-metadata"


def inventory(repo):
    # A reference checkout may carry valuable candidate work or another HEAD.
    # Only the exact committed pin supplies inventory input.
    kind = git(repo, "cat-file", "-t", UPSTREAM_COMMIT).decode().strip()
    if kind != "commit":
        raise ValueError(f"expected upstream pin {UPSTREAM_COMMIT} to name a commit, found {kind}")
    config = configparser.RawConfigParser()
    config.read_string(git(repo, "show", f"{UPSTREAM_COMMIT}:.gitmodules").decode())
    modules = {
        config[section]["path"]: config[section].get("url", "")
        for section in config.sections()
        if section.startswith("submodule ")
    }
    entries = []
    for record in git(repo, "ls-tree", "-rz", "--full-tree", UPSTREAM_COMMIT).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        kind = category(path, mode)
        entry = {
            "path": path,
            "mode": mode,
            "object_type": object_type,
            "object_id": object_id,
            "component": component(path),
            "category": kind,
            "status": ("partial" if path in PARTIAL_REPLACEMENTS else "implemented") if path in REPLACEMENTS else "pending",
        }
        if path in REPLACEMENTS:
            entry["replacement"] = REPLACEMENTS[path]
            entry["scope"] = "Rust Linux host utility replacement; not a runtime or framework port; exact diagnostic wording is not guaranteed"
            if path == "src/libelfloader/wrapgen/wrapgen.cpp":
                entry["limitation"] = "ELF64 parsing/generation ported; safe library lookup covers explicit paths, LD_LIBRARY_PATH, ldconfig cache and common Linux directories, not arbitrary caller RPATH/RUNPATH or loader audit/hardware policy"
            if path == "src/libsimple/src/lock.c":
                entry["scope"] = "Native Linux x86_64 synchronization C ABI with safe atomic core and documented unavoidable FFI/futex unsafe boundary"
                entry["limitation"] = "Linux native and Darling x86_64 backends verified, including Mach-O system_kernel consumer link and selected matching-runtime guest startup/execution; i386/other Rust architectures and clean pinned-runtime startup remain unported/unverified; no target retirement"
        if kind == "submodule":
            entry["upstream_url"] = modules.get(path, "")
            entry["scope"] = "pinned gitlink only; contents and nested dependencies need separate inventory"
        entries.append(entry)
    capture_symlink_targets(repo, entries)
    entries.sort(key=lambda entry: entry["path"])
    seen = {entry["path"] for entry in entries}
    if len(seen) != len(entries):
        raise ValueError("duplicate tracked paths")
    if not set(REPLACEMENTS).issubset(seen):
        raise ValueError("replacement no longer present in pinned upstream")
    gitlinks = {entry["path"] for entry in entries if entry["category"] == "submodule"}
    if gitlinks != set(modules):
        raise ValueError("gitlinks and .gitmodules paths differ")
    return {
        "schema_version": 1,
        "upstream_repository": "https://github.com/darlinghq/darling.git",
        "upstream_commit": UPSTREAM_COMMIT,
        "coverage": "all committed superproject leaf entries, including symlinks and pinned gitlinks; excludes submodule contents",
        "status_definition": {
            "implemented": "Rust replacement exists for this host utility; compatibility claims require its tests",
            "partial": "functioning Rust component exists with explicitly recorded unported behavior",
            "pending": "not migrated; includes assets and headers that must be preserved or explicitly reviewed",
        },
        "counts": {
            "total_entries": len(entries),
            "by_category": dict(sorted(Counter(entry["category"] for entry in entries).items())),
            "by_status": dict(sorted(Counter(entry["status"] for entry in entries).items())),
            "by_component": dict(sorted(Counter(entry["component"] for entry in entries).items())),
        },
        "entries": entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path,
                        help="local repository containing the exact upstream commit; HEAD and working files are ignored")
    parser.add_argument("--output", type=Path, default=Path("migration-inventory.json"))
    args = parser.parse_args()
    try:
        data = inventory(args.upstream)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"inventory failed: {exc}\n")
    args.output.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in data["counts"].items()
                      if key != "by_component"}, sort_keys=True))


if __name__ == "__main__":
    main()
