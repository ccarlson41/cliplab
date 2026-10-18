"""Tests for scripts/release.py: what a release checks before it moves main, tags and publishes.

The git checks run against a throwaway repository (with a local stand-in for GitHub), and GitHub's answers about test
runs are made up here, so nothing is pushed, tagged or released anywhere.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

ENTRY = {"version": "0.10.0", "date": "2026-10-17", "title": "ClipLab keeps itself fresh",
         "highlights": [{"icon": "🔄", "headline": "Automatic updates", "text": "It updates itself."},
                        {"icon": "🎉", "headline": "Wow!", "text": "Exciting."}]}


class LogicTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(release.read_version('"""ClipLab."""\n\n__version__ = "0.10.0"\n'), "0.10.0")
        for bad in ("", '__version__ = "0.10"\n', "version = 1\n"):
            with self.assertRaises(release.Stop):
                release.read_version(bad)

    def test_changelog_entry_must_be_the_version(self):
        self.assertEqual(release.release_entry([ENTRY], "0.10.0"), ENTRY)
        with self.assertRaisesRegex(release.Stop, "newest entry is 0.10.0, but __version__ is 0.11.0"):
            release.release_entry([ENTRY], "0.11.0")
        with self.assertRaises(release.Stop):
            release.release_entry([], "0.10.0")

    def test_notes(self):
        notes = release.render_notes(ENTRY, "ab" * 32)
        self.assertIn("## ClipLab keeps itself fresh", notes)
        self.assertIn("- 🔄 **Automatic updates.** It updates itself.", notes)
        self.assertIn("- 🎉 **Wow!** Exciting.", notes)   # no stray full stop after "!"
        self.assertIn(release.INSTALL, notes)
        self.assertIn("`cliplab-0.10.0.zip` SHA-256: `" + "ab" * 32 + "`", notes)

    def test_tests_verdict(self):
        sha = "a" * 40
        run = {"head_sha": sha, "status": "completed", "conclusion": "success", "html_url": "https://run/1"}
        self.assertEqual(release.tests_verdict([run], sha), "https://run/1")
        self.assertEqual(release.tests_verdict([{**run, "conclusion": "failure"}, run], sha), "https://run/1")
        cases = {"still running": [{**run, "status": "in_progress", "conclusion": None}],
                 "failed": [{**run, "conclusion": "failure"}],
                 "haven't run": [{**run, "head_sha": "b" * 40}], }
        for words, runs in cases.items():
            with self.assertRaisesRegex(release.Stop, words):
                release.tests_verdict(runs, sha)

    def test_workflow_changes(self):
        self.assertEqual(release.workflow_changes(["README.md", ".github/workflows/release.yml",
                                                   ".github/ISSUE_TEMPLATE/feature_request.yml"]),
                         [".github/workflows/release.yml"])


def git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        raise AssertionError(out.stderr)
    return out.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "needs git")
class PlanTests(unittest.TestCase):
    """plan() against a throwaway repository whose origin is a local folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cliplab-release-test-"))
        self.origin, self.work = self.tmp / "origin.git", self.tmp / "work"
        git(self.tmp, "init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        git(self.tmp, "clone", "--quiet", str(self.origin), str(self.work))
        for k, v in (("user.email", "test@example.com"), ("user.name", "Test")):
            git(self.work, "config", k, v)
        self.commit("0.9.0", [{**ENTRY, "version": "0.9.0"}])
        git(self.work, "push", "--quiet", "origin", "HEAD:main", "HEAD:dev")
        self.cwd = os.getcwd()
        os.chdir(self.work)
        self.runs = []
        self.real_run = release.run
        release.run = self.fake_gh

    def tearDown(self):
        release.run = self.real_run
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_gh(self, *cmd, quiet=False):
        """GitHub's answer about test runs is made up; git really runs."""
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"workflow_runs": self.runs}), "")
        return self.real_run(*cmd, quiet=quiet)

    def commit(self, version, changelog, extra=None):
        (self.work / "cliplab").mkdir(exist_ok=True)
        (self.work / "cliplab/__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        (self.work / "cliplab/changelog.json").write_text(json.dumps(changelog), encoding="utf-8")
        for rel, text in (extra or {}).items():
            (self.work / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.work / rel).write_text(text, encoding="utf-8")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", version)
        return git(self.work, "rev-parse", "HEAD")

    def dev(self, version="0.10.0", extra=None):
        sha = self.commit(version, [{**ENTRY, "version": version}, {**ENTRY, "version": "0.9.0"}], extra)
        git(self.work, "push", "--quiet", "origin", "HEAD:dev")
        self.runs = [{"head_sha": sha, "status": "completed", "conclusion": "success", "html_url": "https://run"}]
        return sha

    def test_ready(self):
        sha = self.dev()
        p = release.plan()
        self.assertEqual((p["sha"], p["version"], p["tag"], p["workflow_changes"]), (sha, "0.10.0", "v0.10.0", []))
        self.assertNotEqual(p["main"], sha)   # main fast-forwards to it

    def test_already_released(self):
        self.dev("0.9.0")
        git(self.work, "tag", "v0.9.0")
        git(self.work, "push", "--quiet", "origin", "v0.9.0")
        with self.assertRaisesRegex(release.Stop, "v0.9.0 is already released"):
            release.plan()

    def test_main_has_diverged(self):
        self.dev()
        git(self.work, "checkout", "--quiet", "-b", "hotfix", "HEAD~1")
        (self.work / "hotfix.txt").write_text("x")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", "hotfix straight on main")
        git(self.work, "push", "--quiet", "origin", "HEAD:main")
        with self.assertRaisesRegex(release.Stop, "can't fast-forward"):
            release.plan()

    def test_tests_must_have_passed(self):
        self.dev()
        self.runs = []
        with self.assertRaisesRegex(release.Stop, "haven't run"):
            release.plan()

    def test_workflow_changes_are_noticed(self):
        self.dev(extra={".github/workflows/release.yml": "name: Release\n"})
        self.assertEqual(release.plan()["workflow_changes"], [".github/workflows/release.yml"])

    def test_build_makes_the_zip_and_checksum(self):
        import hashlib
        import zipfile
        self.dev()
        p = release.plan()
        release.DIST, saved = self.tmp / "dist", release.DIST
        try:
            zip_path, digest = release.build(p)
        finally:
            release.DIST = saved
        self.assertEqual(hashlib.sha256(zip_path.read_bytes()).hexdigest(), digest)
        self.assertEqual((zip_path.parent / "cliplab-0.10.0.zip.sha256").read_text(), f"{digest}  cliplab-0.10.0.zip\n")
        with zipfile.ZipFile(zip_path) as z:
            self.assertIn('"0.10.0"', z.read("cliplab-0.10.0/cliplab/__init__.py").decode())
        self.assertIn(digest, (zip_path.parent / "notes.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
