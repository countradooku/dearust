#!/usr/bin/env python3
"""Inventory exact pinned dependency trees using local Git objects, never work files.

Missing objects remain explicit pending scope. This command never fetches. Supply
--object-cache containing bare repositories named SHA256(repository URL) + '.git'.
Use `--fetch-path` only to fetch the exact pin for an explicitly selected path.
"""

import argparse
from collections import Counter
import configparser
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import subprocess
from urllib.parse import urlsplit, urlunsplit

from inventory import UPSTREAM_COMMIT, category

UPSTREAM_URL = "https://github.com/darlinghq/darling.git"

# Qualified source slices are recorded only at their exact dependency commit.
# Source-slice native execution and Darwin consumer linking do not complete the dependency.
NATIVE_ERRNO_SOURCE = "darling/src/libsystem_kernel/emulation/src/conversion/errno.c"
FUTEX_SOURCE = "darling/src/libsystem_kernel/emulation/src/linux_premigration/ext/futex.c"
NATIVE_ERRNO_PIN = "fa29287aa2f0115271e091f1031f53c9e024005d"
PROCESS_SOURCE = "darling/src/libsystem_kernel/emulation/src/xnu_syscall/bsd/impl/misc/proc_info.c"
LOCKSET_SOURCE = "duct-tape/xnu/osfmk/kern/sync_lock.c"
LOCKSET_PIN = "89751e64bc6c2082f7725061824ee0e33395b0de"


def git(gitdir, *args):
    return subprocess.check_output(["git", "--git-dir", str(gitdir), *args], stderr=subprocess.DEVNULL)


def resolve_url(parent, child):
    # Git relative submodule URLs are relative to the repository itself.
    if not child.startswith(("./", "../")):
        return child
    parsed = urlsplit(parent)
    if parsed.scheme:
        return urlunsplit((parsed.scheme, parsed.netloc,
                           posixpath.normpath(parsed.path + "/" + child), "", ""))
    if ":" in parent and not parent.startswith("/"):
        host, path = parent.split(":", 1)
        return host + ":" + posixpath.normpath(path + "/" + child)
    return posixpath.normpath(parent + "/" + child)


def module_urls(gitdir, commit):
    try:
        raw = git(gitdir, "show", commit + ":.gitmodules").decode()
    except subprocess.CalledProcessError:
        return {}
    config = configparser.RawConfigParser()
    config.read_string(raw)
    return {config[s]["path"]: config[s].get("url", "") for s in config.sections()
            if s.startswith("submodule ")}


def tree(gitdir, commit):
    result = []
    for raw in git(gitdir, "ls-tree", "-rlz", "--full-tree", commit).split(b"\0"):
        if not raw:
            continue
        meta, path = raw.split(b"\t", 1)
        mode, kind, oid, size = meta.decode("ascii").split()
        result.append({"path": path.decode("utf-8"), "mode": mode,
                       "object_type": kind, "object_id": oid,
                       "size": None if size == "-" else int(size)})
    return sorted(result, key=lambda item: item["path"])


def is_license(path):
    name = PurePosixPath(path).name.lower()
    return any(name.startswith(p) for p in ("license", "licence", "copying", "copyright", "notice", "apsl"))


class Inventory:
    def __init__(self, upstream, cache=None):
        self.root = Path(subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "--path-format=absolute", "--git-common-dir"]).decode().strip())
        self.cache = cache
        self.dependencies = []

    def locate(self, url, commit, parent_gitdir, relative):
        candidates = [parent_gitdir / "modules" / relative]
        if self.cache:
            candidates.append(self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".git"))
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            try:
                # A gitlink must resolve to an actual commit, never a tag or tree.
                if git(candidate, "cat-file", "-t", commit).strip() == b"commit":
                    return candidate
            except subprocess.CalledProcessError:
                pass
        return None

    def visit(self, parent_gitdir, parent_commit, parent_url, prefix="", ancestry=()):
        modules = module_urls(parent_gitdir, parent_commit)
        links = [entry for entry in tree(parent_gitdir, parent_commit) if entry["mode"] == "160000"]
        if set(modules) != {e["path"] for e in links}:
            raise ValueError(f"gitlinks and .gitmodules differ below {prefix or 'root'}")
        for link in links:
            relative = link["path"]
            path = prefix + relative
            url = resolve_url(parent_url, modules[relative])
            commit = link["object_id"]
            dep = {"path": path, "repository_url": url, "declared_url": modules[relative],
                   "commit": commit, "migration_status": "pending"}
            self.dependencies.append(dep)
            identity = (url, commit)
            gitdir = self.locate(url, commit, parent_gitdir, relative)
            if identity in ancestry:
                dep.update(availability="cycle", reason="same repository and commit already in ancestry; scope remains pending")
                continue
            if gitdir is None:
                dep.update(availability="unavailable", reason="exact pinned commit not in supplied local object stores")
                continue
            entries = tree(gitdir, commit)
            licenses = []
            for entry in entries:
                entry["category"] = category(entry["path"], entry["mode"])
                entry["status"] = "pending"
                if path == "src/external/xnu" and entry["path"] in {NATIVE_ERRNO_SOURCE, FUTEX_SOURCE, PROCESS_SOURCE}:
                    if commit != NATIVE_ERRNO_PIN:
                        raise ValueError("runtime source replacement requires its exact xnu pin")
                    entry.update(status="partial",
                                 replacement={NATIVE_ERRNO_SOURCE: "crates/dearust-errno/src/lib.rs", FUTEX_SOURCE: "crates/dearust-futex/src/lib.rs", PROCESS_SOURCE: "crates/dearust-process/src/lib.rs"}[entry["path"]],
                                 scope="no_std native execution and x86_64 Mach-O system_kernel consumer integration verified; original public ABI retained",
                                 limitation="i386 Rust replacement pending; process-query slice retains legacy fallback flavors. Selected matching locally modified runtime checks do not establish general application compatibility; no target retirement")
                    dep["migration_status"] = "partial"
                if path == "src/external/darlingserver" and entry["path"] == LOCKSET_SOURCE:
                    if commit != LOCKSET_PIN:
                        raise ValueError("lockset source replacement requires its exact server pin")
                    entry.update(status="partial", replacement="crates/dearust-lockset/src/lib.rs",
                                 scope="Rust lockset state and ownership with minimal XNU port/scheduler/lifecycle bridge; matching server relink",
                                 limitation="Original server and most duct-tape sources retained; clean pinned full-runtime integration and other architectures pending")
                    dep["migration_status"] = "partial"
                if entry["mode"] in {"100644", "100755"} and is_license(entry["path"]):
                    blob = git(gitdir, "cat-file", "blob", entry["object_id"])
                    licenses.append({"path": entry["path"], "object_id": entry["object_id"],
                                     "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})
            dep.update(availability="inventoried", entries=entries, license_candidates=licenses,
                       license_review="pending; filename candidates and exact blob hashes are not license determinations",
                       counts={"entries": len(entries), "by_category": dict(sorted(Counter(
                           e["category"] for e in entries).items()))})
            self.visit(gitdir, commit, url, path + "/", ancestry + (identity,))

    def run(self, commit=UPSTREAM_COMMIT, url=UPSTREAM_URL):
        # Explicit commit lookup permits a dirty or differently checked-out reference.
        self.visit(self.root, commit, url, ancestry=((url, commit),))
        self.dependencies.sort(key=lambda entry: entry["path"])
        return {"schema_version": 1, "upstream_repository": url, "upstream_commit": commit,
                "coverage": "exact pinned dependency trees available locally, recursively; unavailable trees and cycles remain explicitly pending",
                "counts": {"dependencies": len(self.dependencies), "by_availability": dict(sorted(Counter(
                    d["availability"] for d in self.dependencies).items())),
                    "inventoried_entries": sum(len(d.get("entries", [])) for d in self.dependencies),
                    "entries_by_status": dict(sorted(Counter(e["status"] for d in self.dependencies for e in d.get("entries", [])).items())),
                    "license_candidates": sum(len(d.get("license_candidates", [])) for d in self.dependencies)},
                "dependencies": self.dependencies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("--object-cache", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dependency-inventory.json"))
    parser.add_argument("--fetch-path", action="append", default=[],
                        help="fetch exact missing pin at this dependency path into --object-cache; repeat for multiple paths")
    args = parser.parse_args()
    if args.fetch_path and args.object_cache is None:
        parser.error("--fetch-path requires --object-cache")
    try:
        data = Inventory(args.upstream, args.object_cache).run()
        # Refresh after each fetch so a later selected path may refer to a nested pin.
        for path in args.fetch_path:
            matches = [d for d in data["dependencies"] if d["path"] == path]
            if not matches:
                raise ValueError("no known pinned dependency at " + path)
            dep = matches[0]
            if dep["availability"] == "unavailable":
                args.object_cache.mkdir(parents=True, exist_ok=True)
                dest = args.object_cache / (hashlib.sha256(dep["repository_url"].encode()).hexdigest() + ".git")
                if not dest.exists():
                    subprocess.run(["git", "init", "--bare", "--quiet", str(dest)], check=True)
                subprocess.run(["git", "--git-dir", str(dest), "-c", "protocol.file.allow=never",
                                "fetch", "--depth=1", "--no-tags", "--", dep["repository_url"], dep["commit"]], check=True)
            data = Inventory(args.upstream, args.object_cache).run()
        args.output.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(json.dumps(data["counts"], sort_keys=True))
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        parser.exit(1, f"dependency inventory failed: {exc}\n")


if __name__ == "__main__":
    main()
