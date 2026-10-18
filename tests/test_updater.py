"""Tests for updates (updater.py) and the launcher's check that launcher.pid is still ClipLab.

They use a throwaway ClipLab home, a made-up install folder, zips built here, and a local web server standing in
for GitHub. Nothing touches your real ClipLab, and nothing is downloaded from the internet.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["CLIPLAB_HOME"] = tempfile.mkdtemp(prefix="cliplab-test-")   # never your real ClipLab home

from cliplab import __version__, launcher, updater  # noqa: E402
from cliplab.settings import CONFIG, HEARTBEAT, HOLD, HOME, REQUESTS, STATUS, write_json  # noqa: E402

NEXT = "99.0.0"   # a version that's always newer than this one
REAL_ANSWERS = updater.answers
QUIET_PORT = {"port": 9}   # where "is the new version answering?" finds nothing (never your real ClipLab's port)


class Fake(BaseHTTPRequestHandler):
    """Serves Fake.files ({path: (status, bytes)}), like GitHub's API and release downloads."""
    files = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        status, body = Fake.files.get(self.path, (404, b"{}"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def release_zip(path, version, files, prefix=True):
    with zipfile.ZipFile(path, "w") as z:
        for rel, text in {"cliplab/__init__.py": f'__version__ = "{version}"\n', **files}.items():
            z.writestr((f"cliplab-{version}/" if prefix else "") + rel, text)
    return Path(path).read_bytes()


def release_json(base, version, assets=True):
    names = [f"cliplab-{version}.zip", f"cliplab-{version}.zip.sha256"] if assets else []
    return {"tag_name": f"v{version}", "html_url": f"{base}/releases/v{version}", "published_at": "2026-10-17T12:00:00Z",
            "assets": [{"name": n, "browser_download_url": f"{base}/dl/{n}"} for n in names]}


class Env(unittest.TestCase):
    """A made-up install (code next to data, as install.ps1 leaves it) and a fake GitHub on a spare port."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cliplab-update-test-"))
        self.root = self.tmp / "install"
        self.root.mkdir()
        self.saved = {k: getattr(updater, k) for k in ("ROOT", "FILES", "PACKAGE", "API", "HEALTHY_WITHIN")}
        updater.ROOT, updater.FILES, updater.PACKAGE = self.root, self.root / ".cliplab-files", self.root / "cliplab"
        updater.API = self.base + "/latest"
        for folder in (updater.UPDATES, updater.BACKUPS, REQUESTS):
            shutil.rmtree(folder, ignore_errors=True)
        STATUS.unlink(missing_ok=True)
        CONFIG.unlink(missing_ok=True)
        Fake.files = {}
        self.code = {"cliplab/__init__.py": f'__version__ = "{__version__}"\n', "cliplab/app.py": "old app\n",
                     "cliplab/gone.py": "dropped in the new version\n", "README.md": "old readme\n",
                     "scripts/tool.ps1": "old tool\n"}
        self.data = {"config.json": '{"port": 8765}', "processed.json": "{}", "decisions.json": "{}",
                     "Inbox/Replay 1.mp4": "raw clip", "Ready/2026-10-17/short.mp4": "a short",
                     "Failed/x.txt": "why", "Tossed/old.mp4": "tossed", "work/x.mp4": "part", "logs/engine.log": "log",
                     "requests/done/a.json": "{}", "thumbs/a.jpg": "jpg", "models/ggml-base.bin": "model",
                     "bin/ffmpeg.exe": "ffmpeg", "sounds/whoosh.wav": "wav", "clipper/config.json": "secret",
                     "clipper/autoclip.log": "log", "backups/0.1.0/x": "old backup"}
        for rel, text in {**self.code, **self.data}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(updater, k, v)
        updater.answers = REAL_ANSWERS
        shutil.rmtree(self.tmp, ignore_errors=True)
        CONFIG.unlink(missing_ok=True)

    def manifest(self):
        updater.FILES.write_text("".join(rel + "\n" for rel in self.code), encoding="utf-8")

    def publish(self, version, files, sha=None, prefix=True):
        """Put a release on the fake GitHub: its JSON, zip and checksum."""
        data = release_zip(self.tmp / f"release-{version}.zip", version, files, prefix)
        Fake.files.update({"/latest": (200, json.dumps(release_json(self.base, version)).encode()),
                           f"/dl/cliplab-{version}.zip": (200, data),
                           f"/dl/cliplab-{version}.zip.sha256":
                               (200, f"{sha or hashlib.sha256(data).hexdigest()}  cliplab-{version}.zip\n".encode())})
        return updater.parse_release(release_json(self.base, version))

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def assert_data_intact(self):
        for rel, text in self.data.items():
            self.assertEqual(self.read(rel), text, rel)


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version("0.10.0"), (0, 10, 0))
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        for bad in ("", None, "1.2", "1.2.3.4", "v1.2.x", "1.2.3-beta", "latest"):
            self.assertIsNone(updater.parse_version(bad), bad)

    def test_compare_is_numeric(self):
        self.assertTrue(updater.newer("0.10.0", "0.9.0"))   # not a string compare
        self.assertTrue(updater.newer("v1.0.0", "0.99.99"))
        self.assertFalse(updater.newer("0.9.0", "0.10.0"))
        self.assertFalse(updater.newer("v0.10.0", "0.10.0"))
        self.assertFalse(updater.newer(None, "0.10.0"))
        self.assertFalse(updater.newer("0.11.0", "garbage"))


class ReleaseTests(unittest.TestCase):
    def test_reads_version_and_downloads(self):
        got = updater.parse_release(release_json("https://x", "0.11.0"))
        self.assertEqual(got["version"], "0.11.0")
        self.assertEqual(got["zip"], "https://x/dl/cliplab-0.11.0.zip")
        self.assertEqual(got["sha256"], "https://x/dl/cliplab-0.11.0.zip.sha256")
        self.assertEqual(got["page"], "https://x/releases/v0.11.0")

    def test_unusable_releases(self):
        for data in ({**release_json("https://x", "0.11.0"), "prerelease": True},
                     {**release_json("https://x", "0.11.0"), "draft": True},
                     {**release_json("https://x", "0.11.0"), "tag_name": "nightly"},
                     release_json("https://x", "0.11.0", assets=False), [], None):
            with self.assertRaises(updater.UpdateError):
                updater.parse_release(data)


class CheckTests(Env):
    def test_finds_a_release(self):
        self.publish(NEXT, {})
        result = updater.check()
        self.assertEqual(result["latest"]["version"], NEXT)
        self.assertNotIn("error", result)
        info = updater.summary(updater.load_config())
        self.assertTrue(info["available"])
        self.assertEqual((info["latest"], info["mode"], info["kind"]), (NEXT, "auto", "zip"))

    def test_no_release_yet_is_not_an_error(self):
        result = updater.check()   # the fake GitHub answers 404, as GitHub does before the first release
        self.assertIsNone(result["latest"])
        self.assertNotIn("error", result)
        self.assertFalse(updater.summary(updater.load_config())["available"])

    def test_offline_keeps_the_last_answer_quietly(self):
        self.publish(NEXT, {})
        updater.check()
        updater.API = "http://127.0.0.1:9/latest"   # nothing listens there
        result = updater.check()
        self.assertIn("Couldn't check", result["error"])
        self.assertEqual(result["latest"]["version"], NEXT)

    def test_garbled_answer(self):
        Fake.files["/latest"] = (200, b"<html>not json")
        result = updater.check()
        self.assertIsNone(result["latest"])
        self.assertIn("Couldn't check", result["error"])


class DownloadTests(Env):
    def test_checksum_mismatch_installs_nothing(self):
        latest = self.publish(NEXT, {"cliplab/app.py": "new app\n"}, sha="0" * 64)
        with self.assertRaisesRegex(updater.UpdateError, "didn't match its checksum"):
            updater.download(latest)
        self.assertFalse(any(updater.UPDATES.glob("*.zip")))
        self.assertEqual(self.read("cliplab/app.py"), "old app\n")

    def test_zip_must_be_the_version_it_claims(self):
        latest = self.publish(NEXT, {})
        data = release_zip(self.tmp / "wrong.zip", "1.0.0", {})   # right checksum, wrong version inside
        Fake.files[f"/dl/cliplab-{NEXT}.zip"] = (200, data)
        Fake.files[f"/dl/cliplab-{NEXT}.zip.sha256"] = (200, hashlib.sha256(data).hexdigest().encode())
        with self.assertRaisesRegex(updater.UpdateError, "not " + NEXT):
            updater.download(latest)

    def test_checksum_file_must_hold_a_hash(self):
        latest = self.publish(NEXT, {})
        Fake.files[f"/dl/cliplab-{NEXT}.zip.sha256"] = (200, b"Not Found")
        with self.assertRaisesRegex(updater.UpdateError, "SHA-256"):
            updater.download(latest)

    def test_unsafe_paths_are_refused(self):
        path = self.tmp / "evil.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("cliplab/__init__.py", f'__version__ = "{NEXT}"\n')
            z.writestr("../outside.txt", "x")
        with self.assertRaisesRegex(updater.UpdateError, "unsafe"):
            updater.zip_contents(path, NEXT)

    def test_zip_never_carries_data(self):
        path, files = updater.download(self.publish(NEXT, {"cliplab/app.py": "new\n", "config.json": "{}",
                                                            "Inbox/x.mp4": "x", "logs/a.log": "x"}))
        self.assertEqual(set(files), {"cliplab/__init__.py", "cliplab/app.py"})
        release_zip(self.tmp / "flat.zip", NEXT, {"README.md": "x"}, prefix=False)
        files = updater.zip_contents(self.tmp / "flat.zip", NEXT)
        self.assertEqual(set(files), {"cliplab/__init__.py", "README.md"})   # a zip without a top folder works too


class InstallTests(Env):
    def install(self, files):
        path, contents = updater.download(self.publish(NEXT, files))
        return updater.install(path, contents)

    def test_data_survives_and_stale_code_goes(self):
        self.manifest()
        backup = self.install({"cliplab/app.py": "new app\n", "cliplab/added.py": "new file\n",
                               "README.md": "new readme\n", "scripts/tool.ps1": "new tool\n"})
        self.assert_data_intact()
        self.assertEqual(self.read("cliplab/app.py"), "new app\n")
        self.assertEqual(self.read("cliplab/added.py"), "new file\n")
        self.assertIn(NEXT, self.read("cliplab/__init__.py"))
        self.assertFalse((self.root / "cliplab/gone.py").exists())   # dropped by the new version
        self.assertEqual(self.read("backups/0.1.0/x"), "old backup")  # older backups aren't touched here
        self.assertEqual(backup, updater.BACKUPS / __version__)
        self.assertEqual((backup / "cliplab/gone.py").read_text(encoding="utf-8"), "dropped in the new version\n")
        self.assertEqual(set(updater.FILES.read_text(encoding="utf-8").split()),
                         {"cliplab/__init__.py", "cliplab/app.py", "cliplab/added.py", "README.md",
                          "scripts/tool.ps1"})

    def test_older_install_without_a_file_list(self):
        (self.root / "cliplab/__pycache__").mkdir()
        (self.root / "cliplab/__pycache__/app.cpython-312.pyc").write_bytes(b"x")
        self.install({"cliplab/app.py": "new app\n", "README.md": "new\n", "scripts/tool.ps1": "new\n"})
        self.assert_data_intact()
        self.assertFalse((self.root / "cliplab/gone.py").exists())   # found by looking in the code folders
        self.assertTrue(updater.FILES.exists())

    def test_watch_folder_inside_the_code_folder_is_safe(self):
        (self.root / "My Clips").mkdir()
        (self.root / "My Clips/keep.mp4").write_text("mine")
        write_json(CONFIG, {"watch_dir": str(self.root / "My Clips")})
        updater.FILES.write_text("cliplab/__init__.py\nMy Clips/keep.mp4\n", encoding="utf-8")   # a bad list
        self.install({})
        self.assertEqual(self.read("My Clips/keep.mp4"), "mine")

    def test_restore_undoes_an_update(self):
        self.manifest()
        backup = self.install({"cliplab/app.py": "new app\n", "cliplab/added.py": "new\n"})
        updater.restore(backup, {"cliplab/__init__.py", "cliplab/app.py", "cliplab/added.py"})
        for rel, text in self.code.items():
            self.assertEqual(self.read(rel), text, rel)
        self.assertFalse((self.root / "cliplab/added.py").exists())
        self.assertEqual(set(updater.FILES.read_text(encoding="utf-8").split()), set(self.code))
        self.assert_data_intact()


class TickTests(Env):
    """The launcher's side: when to install, waiting for renders, and rolling back."""

    def setUp(self):
        super().setUp()
        self.manifest()
        self.stops, self.launches = 0, []
        updater.HEALTHY_WITHIN = 3
        write_json(CONFIG, QUIET_PORT)

    def stop_services(self):
        self.stops += 1

    def relaunch(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])   # never answers
        self.launches.append(proc)
        return proc

    def tearDown(self):
        for proc in self.launches:
            proc.kill()
            proc.wait()
        super().tearDown()

    def tick(self, state):
        return updater.tick(state, self.stop_services, self.relaunch)

    def test_waits_while_the_engine_renders(self):
        self.publish(NEXT, {"cliplab/app.py": "new app\n"})
        write_json(STATUS, {"key": "Replay 1.mp4", "started": time.time()})
        state = {}
        for _ in range(3):
            self.assertIsNone(self.tick(state))
        self.assertEqual(updater.summary(updater.load_config())["progress"]["state"], "waiting")
        self.assertEqual((self.stops, self.read("cliplab/app.py")), (0, "old app\n"))
        STATUS.unlink()
        REQUESTS.mkdir(parents=True, exist_ok=True)
        (REQUESTS / "job.json").write_text("{}")   # a re-cut waiting its turn counts as busy too
        self.assertIsNone(self.tick(state))
        self.assertEqual(self.stops, 0)

    def test_rolls_back_when_the_new_version_doesnt_answer(self):
        self.publish(NEXT, {"cliplab/app.py": "new app\n", "cliplab/added.py": "new\n"})
        state = {}
        self.assertIsNone(self.tick(state))   # checked, downloaded; quiet for the first time
        state["idle_since"] -= 10             # ...and still quiet a pass later
        self.assertEqual(self.tick(state), "rolled-back")
        self.assertEqual(self.stops, 1)
        self.assertEqual(len(self.launches), 1)
        self.assertIsNotNone(self.launches[0].poll())   # the new launcher was stopped
        for rel, text in self.code.items():
            self.assertEqual(self.read(rel), text, rel)
        self.assertFalse((self.root / "cliplab/added.py").exists())
        self.assert_data_intact()
        info = updater.summary(updater.load_config())
        self.assertTrue(info["skipped"])
        self.assertEqual(info["progress"]["state"], "rolled-back")
        self.assertIn("won't install", info["progress"]["message"])
        self.assertIsNone(self.tick(state))   # a skipped version isn't tried again by itself
        self.assertEqual(self.stops, 1)

    def test_installs_and_hands_over(self):
        self.publish(NEXT, {"cliplab/app.py": "new app\n"})
        updater.answers = lambda version, child: version == NEXT
        state = {}
        self.tick(state)
        state["idle_since"] -= 10
        self.assertEqual(self.tick(state), "exit")
        self.assertEqual(self.read("cliplab/app.py"), "new app\n")
        self.assert_data_intact()
        self.assertFalse(any(updater.UPDATES.glob("*.zip")))
        self.assertTrue((updater.BACKUPS / __version__ / "cliplab/app.py").exists())

    def test_notify_and_off_wait_for_update_now(self):
        self.publish(NEXT, {"cliplab/app.py": "new app\n"})
        write_json(CONFIG, {**QUIET_PORT, "auto_update": "off"})
        state = {}
        self.assertIsNone(self.tick(state))
        self.assertFalse(updater.CHECK.exists())   # "off" doesn't even check
        write_json(CONFIG, {**QUIET_PORT, "auto_update": "notify"})
        state = {}
        self.assertIsNone(self.tick(state))
        self.assertTrue(updater.summary(updater.load_config())["available"])
        self.assertEqual(self.stops, 0)            # "notify" checks, but doesn't install
        os.utime(updater.BEAT, (time.time() - 120, time.time() - 120))
        self.assertIn("launcher isn't running", updater.request(NEXT))
        updater.BEAT.write_text("x")
        self.assertIsNone(updater.request(NEXT))
        self.assertTrue(updater.summary(updater.load_config())["requested"])
        self.tick(state)
        state["idle_since"] -= 10
        self.assertEqual(self.tick(state), "rolled-back")   # Update now installed it (and the fake didn't answer)
        self.assertFalse(updater.REQUEST.exists())

    def test_update_now_needs_an_update(self):
        updater.BEAT.parent.mkdir(parents=True, exist_ok=True)
        updater.BEAT.write_text("x")
        self.assertIn("no update", updater.request(NEXT))

    def test_a_bad_download_is_reported_and_not_retried_in_a_loop(self):
        self.publish(NEXT, {}, sha="f" * 64)
        state = {}
        self.assertEqual(self.tick(state), "failed")
        self.assertIn("checksum", updater.summary(updater.load_config())["progress"]["message"])
        self.assertIsNone(self.tick(state))
        self.assertEqual(self.stops, 0)


def git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if out.returncode:
        raise AssertionError(out.stderr)
    return out.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitTests(Env):
    """A git checkout only gets `git pull --ff-only`, on main, with nothing of its own."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.root)
        self.origin = Path(tempfile.mkdtemp(prefix="cliplab-origin-"))
        seed = Path(tempfile.mkdtemp(prefix="cliplab-seed-"))
        git(self.origin, "init", "--quiet", "--bare", "--initial-branch=main")
        git(seed, "init", "--quiet", "--initial-branch=main")
        for k, v in (("user.email", "test@example.com"), ("user.name", "Test")):
            git(seed, "config", k, v)
        (seed / "cliplab").mkdir()
        (seed / "cliplab/__init__.py").write_text(f'__version__ = "{__version__}"\n')
        git(seed, "add", ".")
        git(seed, "commit", "--quiet", "-m", "first")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "--quiet", "origin", "main")
        git(self.tmp, "clone", "--quiet", str(self.origin), str(self.root))
        for k, v in (("user.email", "test@example.com"), ("user.name", "Test")):
            git(self.root, "config", k, v)
        self.seed = seed

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.origin, ignore_errors=True)
        shutil.rmtree(self.seed, ignore_errors=True)

    def release_on_origin(self):
        (self.seed / "cliplab/__init__.py").write_text(f'__version__ = "{NEXT}"\n')
        git(self.seed, "commit", "--quiet", "-am", "release")
        git(self.seed, "push", "--quiet", "origin", "main")

    def test_clean_main_is_pulled(self):
        self.assertTrue(updater.is_git())
        self.assertIsNone(updater.git_blocked())
        self.release_on_origin()
        updater.answers = lambda version, child: version == NEXT
        launched = []
        result = updater.apply_git(NEXT, lambda: None,
                                   lambda: launched.append(subprocess.Popen([sys.executable, "-c", "pass"])) or launched[0])
        launched[0].wait()
        self.assertEqual(result, "exit")
        self.assertIn(NEXT, (self.root / "cliplab/__init__.py").read_text())

    def test_rollback_goes_back_to_the_old_commit(self):
        before = git(self.root, "rev-parse", "HEAD")
        self.release_on_origin()
        write_json(CONFIG, QUIET_PORT)
        updater.HEALTHY_WITHIN = 5
        result = updater.apply_git(NEXT, lambda: None, lambda: subprocess.Popen([sys.executable, "-c", "pass"]))
        self.assertEqual(result, "rolled-back")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)

    def test_anything_of_its_own_blocks_it(self):
        git(self.root, "checkout", "--quiet", "-b", "my-feature")
        self.assertIn("my-feature", updater.git_blocked())
        git(self.root, "checkout", "--quiet", "main")
        (self.root / "cliplab/__init__.py").write_text("# my change\n")
        self.assertIn("changes of its own", updater.git_blocked())
        git(self.root, "commit", "--quiet", "-am", "mine")
        self.assertIn("commits of its own", updater.git_blocked())
        self.release_on_origin()
        self.assertEqual(updater.apply_git(NEXT, self.fail, self.fail), "failed")   # never stops or pulls
        self.assertEqual(updater.summary(updater.load_config())["progress"]["state"], "blocked")

    def test_untracked_files_dont_block_it(self):
        (self.root / "Ready").mkdir()
        (self.root / "Ready/short.mp4").write_text("x")   # data in a checkout that's also the ClipLab home
        self.assertIsNone(updater.git_blocked())


class EngineBusyTests(unittest.TestCase):
    def tearDown(self):
        STATUS.unlink(missing_ok=True)
        shutil.rmtree(REQUESTS, ignore_errors=True)

    def test_busy(self):
        self.assertFalse(updater.engine_busy())
        write_json(STATUS, {"started": time.time() - 3600})   # a stale status left by a crash
        self.assertFalse(updater.engine_busy())
        write_json(STATUS, {"started": time.time()})
        self.assertTrue(updater.engine_busy())

    def test_waits_while_a_game_is_on(self):
        HOME.mkdir(parents=True, exist_ok=True)
        try:
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            self.assertFalse(updater.engine_busy())
            write_json(HOLD, {"game": "VALORANT", "override": False, "closing": False})
            self.assertTrue(updater.engine_busy())   # no restart mid-match
            write_json(HOLD, {"game": "VALORANT", "override": True, "closing": False})
            self.assertTrue(updater.engine_busy())   # Render now still means a game is on
            HEARTBEAT.write_text(str(time.time() - 120), encoding="utf-8")
            self.assertFalse(updater.engine_busy())   # a crashed engine's hold.json doesn't block updates
            HEARTBEAT.unlink()
            self.assertFalse(updater.engine_busy())
        finally:
            HOLD.unlink(missing_ok=True)
            HEARTBEAT.unlink(missing_ok=True)


class LauncherPidTests(unittest.TestCase):
    """After a reboot launcher.pid can name any program: only a Python running `-m cliplab` gets stopped."""

    def tearDown(self):
        launcher.PIDFILE.unlink(missing_ok=True)

    def test_what_counts_as_cliplab(self):
        yes = [("python.exe", r'"C:\Python312\python.exe" -m cliplab'), ("pythonw.exe", "pythonw.exe -m cliplab"),
               ("python3.12", "/usr/bin/python3.12 -m cliplab run"), ("python.exe", "python -m  cliplab app")]
        no = [("chrome.exe", "chrome.exe -m cliplab"), ("python.exe", "python -m cliplab_other"),
              ("python.exe", "python script.py"), ("python.exe", "python -m pip install cliplab"), ("", "")]
        for info in yes:
            self.assertTrue(launcher.is_cliplab(info), info)
        for info in no:
            self.assertFalse(launcher.is_cliplab(info), info)

    def check_stop(self, info, killed):
        launcher.PIDFILE.parent.mkdir(parents=True, exist_ok=True)
        launcher.PIDFILE.write_text("4242")
        calls = []
        saved = launcher.describe_process, updater.kill_tree
        launcher.describe_process, updater.kill_tree = (lambda pid: info), calls.append
        try:
            self.assertEqual(launcher.stop(), killed)
        finally:
            launcher.describe_process, updater.kill_tree = saved
        self.assertEqual(calls, [4242] if killed else [])

    def test_stop_only_kills_cliplab(self):
        self.check_stop(("python.exe", "python.exe -m cliplab"), True)
        self.assertFalse(launcher.PIDFILE.exists())
        self.check_stop(("notepad.exe", "notepad.exe"), False)
        self.assertFalse(launcher.PIDFILE.exists())   # a stale file is cleared
        self.check_stop(("", ""), False)               # nothing runs under that number
        self.check_stop(None, False)                   # couldn't tell: leave it alone
        self.assertTrue(launcher.PIDFILE.exists())

    def test_real_processes(self):
        fake = Path(tempfile.mkdtemp(prefix="cliplab-fakepkg-"))   # a package named cliplab that just waits
        (fake / "cliplab").mkdir()
        (fake / "cliplab/__init__.py").write_text("")
        (fake / "cliplab/__main__.py").write_text("import time\ntime.sleep(60)\n")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        mine = subprocess.Popen([sys.executable, "-m", "cliplab"], cwd=str(fake))
        try:
            time.sleep(1)
            launcher.PIDFILE.parent.mkdir(parents=True, exist_ok=True)
            launcher.PIDFILE.write_text(str(other.pid))
            self.assertFalse(launcher.stop())
            self.assertIsNone(other.poll(), "an unrelated program was stopped")
            launcher.PIDFILE.write_text(str(mine.pid))
            self.assertTrue(launcher.stop())
            mine.wait(15)
            self.assertIsNotNone(mine.poll())
        finally:
            for proc in (other, mine):
                proc.kill()
                proc.wait()
            shutil.rmtree(fake, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
