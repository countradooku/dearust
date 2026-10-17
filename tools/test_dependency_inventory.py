"""Hermetic dependency inventory tests; no network or third-party packages."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dependency_inventory import Inventory, resolve_url, NATIVE_ERRNO_SOURCE, FUTEX_SOURCE


class DependencyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cache = self.base / "cache"
        self.cache.mkdir()

    def repo(self, name, files, modules=()):
        path = self.base / name
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        for filename, content in files.items():
            destination = path / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        if modules:
            (path / ".gitmodules").write_text("".join(
                f'[submodule "{p}"]\n path = {p}\n url = {u}\n' for p, u, _ in modules))
        self.run_git(path, "add", ".")
        for module, _, commit in modules:
            self.run_git(path, "update-index", "--add", "--cacheinfo", "160000", commit, module)
        self.run_git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "-qm", "fixture")
        return path, self.run_git(path, "rev-parse", "HEAD").strip()

    def run_git(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args]).decode()

    def store(self, repo, url):
        destination = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".git")
        subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(destination)], check=True)

    def test_exact_pin_recursion_licenses_and_unavailable(self):
        child, child_commit = self.repo("child", {"LICENSE": "terms\n", "src.c": "int x;\n"})
        parent, parent_commit = self.repo("parent", {"header.h": "int x;\n"},
                                        [("nested", "../child.git", child_commit)])
        root, root_commit = self.repo("root", {"root.txt": "root"}, [
            ("deps/parent", "../parent.git", parent_commit),
            ("missing", "../missing.git", "1" * 40)])
        self.store(parent, "https://example.invalid/parent.git")
        self.store(child, "https://example.invalid/child.git")
        # Worktree modifications, a new HEAD, and untracked files cannot enter inventory.
        (child / "LICENSE").write_text("changed")
        (root / "untracked.c").write_text("not committed")
        self.run_git(root, "checkout", "--orphan", "other")
        data = Inventory(root, self.cache).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(data["counts"]["by_availability"], {"inventoried": 2, "unavailable": 1})
        self.assertEqual(data["counts"]["inventoried_entries"], 5)
        nested = data["dependencies"][1]
        self.assertEqual(nested["path"], "deps/parent/nested")
        license = nested["license_candidates"][0]
        self.assertEqual(license["sha256"], hashlib.sha256(b"terms\n").hexdigest())
        self.assertEqual(license["size"], 6)
        again = Inventory(root, self.cache).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(json.dumps(data), json.dumps(again))
        self.assertNotIn(str(self.base), json.dumps(data))
        self.assertTrue(all(e["status"] == "pending" for d in data["dependencies"] for e in d.get("entries", [])))

    def test_wrong_commit_in_store_remains_unavailable(self):
        child, _ = self.repo("child", {"file": "data"})
        root, root_commit = self.repo("root", {}, [("missing", "../child.git", "2" * 40)])
        self.store(child, "https://example.invalid/child.git")
        result = Inventory(root, self.cache).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(result["dependencies"][0]["availability"], "unavailable")
        self.assertEqual(result["counts"]["inventoried_entries"], 0)

    def test_mismatched_modules_rejected(self):
        root, commit = self.repo("root", {".gitmodules": '[submodule "extra"]\npath=extra\nurl=../x.git\n'})
        with self.assertRaisesRegex(ValueError, "gitlinks and .gitmodules differ"):
            Inventory(root).run(commit)

    def test_initialized_store_shared_by_git_worktree(self):
        child, child_commit = self.repo("child", {"COPYING": "terms"})
        root, root_commit = self.repo("root", {}, [("vendor/child", "../child.git", child_commit)])
        destination = root / ".git" / "modules" / "vendor" / "child"
        destination.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(child), str(destination)], check=True)
        checkout = self.base / "worktree"
        self.run_git(root, "worktree", "add", "--quiet", "--detach", str(checkout), root_commit)
        direct = Inventory(root).run(root_commit, "https://example.invalid/root.git")
        linked = Inventory(checkout).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(direct, linked)
        self.assertEqual(linked["counts"]["by_availability"], {"inventoried": 1})

    def test_native_errno_slice_remains_partial_and_requires_its_exact_pin(self):
        child, commit = self.repo("xnu", {NATIVE_ERRNO_SOURCE: "fixture errno source", FUTEX_SOURCE: "fixture futex source", "other.c": "unported"})
        root, root_commit = self.repo("root", {}, [("src/external/xnu", "../xnu.git", commit)])
        self.store(child, "https://example.invalid/xnu.git")
        # The source path alone cannot mark a different version as ported.
        with self.assertRaisesRegex(ValueError, "exact xnu pin"):
            Inventory(root, self.cache).run(root_commit, "https://example.invalid/root.git")
        with patch("dependency_inventory.NATIVE_ERRNO_PIN", commit):
            data = Inventory(root, self.cache).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(data["counts"]["entries_by_status"], {"partial": 2, "pending": 1})
        dependency = data["dependencies"][0]
        self.assertEqual(dependency["migration_status"], "partial")
        source = next(e for e in dependency["entries"] if e["path"] == NATIVE_ERRNO_SOURCE)
        self.assertEqual(source["status"], "partial")
        self.assertIn("i386", source["limitation"])

    def test_git_relative_url_rules(self):
        self.assertEqual(resolve_url("https://host/org/repo.git", "../child.git"), "https://host/org/child.git")
        self.assertEqual(resolve_url("git@host:org/repo.git", "../child.git"), "git@host:org/child.git")
        self.assertEqual(resolve_url("/local/repo.git", "./child.git"), "/local/repo.git/child.git")
        self.assertEqual(resolve_url("https://host/a.git", "https://other/b.git"), "https://other/b.git")


if __name__ == "__main__":
    unittest.main()
