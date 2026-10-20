"""Literal symlink provenance from local committed Git fixtures only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dependency_inventory import Inventory, tree
from inventory import committed_blobs, inventory


class SymlinkInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    def repo(self, name, links, modules=()):
        repo = self.base / name
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / ".gitmodules").write_text("".join(
            f'[submodule "{path}"]\n path = {path}\n url = {url}\n'
            for path, url, _ in modules))
        (repo / "regular.txt").write_text("ordinary data\n")
        for path, target in links.items():
            destination = repo / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, os.fsencode(destination))
        self.git(repo, "add", ".")
        for path, _, commit in modules:
            self.git(repo, "update-index", "--add", "--cacheinfo", "160000", commit, path)
        self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "fixture")
        return repo, self.git(repo, "rev-parse", "HEAD")

    def test_superproject_preserves_literal_bytes_without_working_reads(self):
        targets = {"relative": b"../missing", "absolute": b"/never/read/this",
                   "cycle-a": b"cycle-b", "cycle-b": b"cycle-a",
                   "raw": b"invalid-utf8-\xff\nwith-newline", "same": b"../missing"}
        repo, commit = self.repo("superproject", targets)
        for path in targets:
            (repo / path).unlink()
            os.symlink(b"/working/value/must/not/be/read", os.fsencode(repo / path))
        with patch("inventory.UPSTREAM_COMMIT", commit), patch("inventory.REPLACEMENTS", {}), \
                patch.object(Path, "read_text", side_effect=AssertionError("working text read")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("working byte read")), \
                patch("os.readlink", side_effect=AssertionError("working symlink read")):
            first = inventory(repo)
            second = inventory(repo)
        self.assertEqual(first, second)
        roundtrip = json.loads(json.dumps(first, ensure_ascii=True))
        entries = {e["path"]: e for e in roundtrip["entries"]}
        for path, target in targets.items():
            self.assertEqual(entries[path]["symlink_target"].encode("utf-8", "surrogateescape"), target)
            self.assertEqual(entries[path]["category"], "symlink")
            self.assertEqual(entries[path]["status"], "pending")
        self.assertNotIn("symlink_target", entries["regular.txt"])

    def test_nested_dependencies_use_committed_targets(self):
        child, child_commit = self.repo("child", {"sdk/link": b"../../outside"})
        parent, parent_commit = self.repo("parent", {"alias": b"nested/sdk"},
                                          [("nested", "../child.git", child_commit)])
        root, root_commit = self.repo("root", {}, [("vendor", "../parent.git", parent_commit)])
        cache = self.base / "cache"
        cache.mkdir()
        for repo, name in [(parent, "parent"), (child, "child")]:
            url = f"https://example.invalid/{name}.git"
            destination = cache / (hashlib.sha256(url.encode()).hexdigest() + ".git")
            subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(destination)], check=True)
        (child / "sdk/link").unlink()
        os.symlink(b"/changed", os.fsencode(child / "sdk/link"))
        (parent / "alias").unlink()
        os.symlink(b"/changed-parent", os.fsencode(parent / "alias"))
        with patch("os.readlink", side_effect=AssertionError("working symlink read")):
            first = Inventory(root, cache).run(root_commit, "https://example.invalid/root.git")
            second = Inventory(root, cache).run(root_commit, "https://example.invalid/root.git")
        self.assertEqual(first, second)
        self.assertEqual(first["counts"]["by_availability"], {"inventoried": 2})
        dependencies = {d["path"]: {e["path"]: e for e in d["entries"]}
                        for d in first["dependencies"]}
        self.assertEqual(dependencies["vendor"]["alias"]["symlink_target"], "nested/sdk")
        self.assertEqual(dependencies["vendor/nested"]["sdk/link"]["symlink_target"], "../../outside")
        self.assertNotIn("symlink_target", dependencies["vendor"]["nested"])

    def test_superproject_ignores_later_head_and_working_files(self):
        repo, commit = self.repo("head-independence", {"alias": b"original"})
        with patch("inventory.UPSTREAM_COMMIT", commit), patch("inventory.REPLACEMENTS", {}):
            before = inventory(repo)
            (repo / "alias").unlink()
            os.symlink(b"new-committed-target", os.fsencode(repo / "alias"))
            (repo / "new-source.c").write_text("newer committed content\n")
            self.git(repo, "add", ".")
            self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "-qm", "advance reference HEAD")
            self.assertNotEqual(self.git(repo, "rev-parse", "HEAD"), commit)
            (repo / "regular.txt").write_text("uncommitted candidate work\n")
            (repo / "alias").unlink()
            os.symlink(b"dirty-candidate-target", os.fsencode(repo / "alias"))
            after = inventory(repo)
        self.assertEqual(before, after)
        self.assertNotIn("new-source.c", {entry["path"] for entry in after["entries"]})

    def test_replacement_refs_do_not_change_pinned_superproject_or_dependency_tree(self):
        repo, commit = self.repo("replace-independence", {"alias": b"original"})
        with patch("inventory.UPSTREAM_COMMIT", commit), patch("inventory.REPLACEMENTS", {}):
            before = inventory(repo)
            tree_before = tree(repo / ".git", commit)
            (repo / "regular.txt").write_text("replacement content\n")
            self.git(repo, "add", ".")
            self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "-qm", "replacement")
            self.git(repo, "replace", commit, self.git(repo, "rev-parse", "HEAD"))
            self.assertEqual(inventory(repo), before)
            self.assertEqual(tree(repo / ".git", commit), tree_before)

    def test_superproject_pin_must_be_a_commit(self):
        repo, commit = self.repo("commit-type", {"alias": b"original"})
        blob = self.git(repo, "rev-parse", commit + ":regular.txt")
        with patch("inventory.UPSTREAM_COMMIT", blob), patch("inventory.REPLACEMENTS", {}):
            with self.assertRaisesRegex(ValueError, "to name a commit, found blob"):
                inventory(repo)

    def test_batch_deduplicates_and_preserves_binary_framing(self):
        repo, commit = self.repo("batch", {"one": b"../x\nsecond-line", "two": b"../x\nsecond-line"})
        entries = tree(repo / ".git", commit)
        oid = next(e["object_id"] for e in entries if e["mode"] == "120000")
        self.assertEqual(committed_blobs(repo, [oid, oid]), {oid: b"../x\nsecond-line"})
        with patch("inventory.subprocess.check_output", side_effect=AssertionError("empty batch ran Git")):
            self.assertEqual(committed_blobs(repo, []), {})

    def test_missing_nonblob_and_nonliteral_requests_fail_closed(self):
        repo, commit = self.repo("errors", {"link": b"missing"})
        with self.assertRaisesRegex(ValueError, "missing or non-blob"):
            committed_blobs(repo, ["0" * 40])
        with self.assertRaisesRegex(ValueError, "missing or non-blob"):
            committed_blobs(repo, [commit])
        with self.assertRaisesRegex(ValueError, "full Git object IDs"):
            committed_blobs(repo, ["HEAD:link"])


if __name__ == "__main__":
    unittest.main()
