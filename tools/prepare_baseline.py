#!/usr/bin/env python3
"""Materialize a pinned upstream baseline without network, shared config, or hooks.

Requires the dependency inventory and its local bare object cache. Each dependency
gets an independent .git directory; sources and submodules are never reset in place.
Run CMake separately: cmake -S BASELINE -B BUILD (documented upstream defaults).
Git LFS smudging is disabled; committed pointer files stay explicit prerequisites.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dependency_inventory import UPSTREAM_COMMIT


def command(*args, cwd=None):
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    # Global filters/hooks cannot run during checkout; even submodule recursion
    # remains local and explicit. Hardlinks/alternates to original stores are off.
    return subprocess.check_output(["git", "-c", "core.hooksPath=" + os.devnull,
        "-c", "protocol.allow=never", "-c", "protocol.file.allow=always", *args],
        cwd=cwd, env=env, stderr=subprocess.PIPE)


def clone_pin(source, destination, commit):
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("destination must be absent or an empty directory: " + str(destination))
    command("clone", "--quiet", "--no-hardlinks", "--no-checkout", "--no-recurse-submodules",
            "--", str(source.resolve()), str(destination.resolve()))
    # Some cache pins are reachable only through FETCH_HEAD; clone copies named
    # refs, so explicitly transfer the exact commit from the local object store.
    command("-C", str(destination), "fetch", "--quiet", "--depth=1", "--no-tags", "--",
            str(source.resolve()), commit + ":refs/baseline/pin")
    command("-C", str(destination), "cat-file", "-e", commit + "^{commit}")
    command("-C", str(destination), "checkout", "--quiet", "--detach", commit)
    actual = command("-C", str(destination), "rev-parse", "HEAD").decode().strip()
    if actual != commit:
        raise ValueError("checkout pin mismatch at " + str(destination))


def prepare(source, cache, destination, inventory):
    if inventory["upstream_commit"] != UPSTREAM_COMMIT:
        raise ValueError("wrong upstream inventory commit")
    dependencies = inventory["dependencies"]
    if any(d["availability"] != "inventoried" for d in dependencies):
        raise ValueError("every dependency must have exact inventoried objects")
    # Preflight all stores before creating the baseline. cctools-port may reside
    # only in an initialized submodule store of the original reference checkout.
    stores = {}
    common = Path(command("-C", str(source), "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
    for dep in sorted(dependencies, key=lambda d: (d["path"].count("/"), d["path"])):
        relative = Path(dep["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe dependency path")
        store = cache / (hashlib.sha256(dep["repository_url"].encode()).hexdigest() + ".git")
        if not store.is_dir():
            store = common / "modules" / relative
        command("--git-dir", str(store), "cat-file", "-e", dep["commit"] + "^{commit}")
        stores[dep["path"]] = store
    clone_pin(source, destination, UPSTREAM_COMMIT)
    for dep in sorted(dependencies, key=lambda d: (d["path"].count("/"), d["path"])):
        target = destination / dep["path"]
        # Reject symlink parents to avoid writing outside the isolated baseline.
        parent = target.parent
        while parent != destination:
            if parent.is_symlink():
                raise ValueError("dependency parent is a symlink: " + str(parent))
            parent = parent.parent
        clone_pin(stores[dep["path"]], target, dep["commit"])
        print("materialized", dep["path"], dep["commit"], flush=True)
    print(json.dumps({"upstream_commit": UPSTREAM_COMMIT, "materialized_dependency_trees": len(dependencies),
                      "network": False, "git_lfs_smudge": False}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("object_cache", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--inventory", type=Path, default=Path("dependency-inventory.json"))
    args = parser.parse_args()
    try:
        prepare(args.upstream, args.object_cache, args.destination,
                json.loads(args.inventory.read_text()))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr.decode(errors="replace") if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, "baseline preparation failed: " + detail + "\n")


if __name__ == "__main__":
    main()
