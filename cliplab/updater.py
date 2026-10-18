"""Keeps ClipLab up to date from its GitHub releases.

The launcher calls tick() every few seconds. tick() asks GitHub for the latest release when ClipLab starts and every
6 hours, unless auto_update in config.json is "off". "auto" (the default) installs a new version by itself; "notify"
waits for Update now in the app. Updating a downloaded copy of ClipLab (a zip install):
  1. download the release zip and its .sha256, and check the hash and that the zip's cliplab/__init__.py says that
     version
  2. wait until the engine isn't rendering, then stop the engine and the app
  3. back up the code to backups\\<old version>, copy the new code in, and remove code files the new version dropped
     (by the list in .cliplab-files). Data (settings, shorts, logs, models...) is never touched
  4. start a new launcher; if the app doesn't answer with the new version within a minute, put the old code back,
     carry on with it, and skip that version
A git checkout only ever gets `git pull --ff-only`, and only on main with nothing of its own; otherwise the app just
says an update is out. Python and ffmpeg aren't updated here (the install line does those).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .settings import (HOME, LOGS, NO_WINDOW, PACKAGE, REQUESTS, STATUS, load_config, output_dir, read_json,
                       watch_dir, write_json)

# CLIPLAB_UPDATE_URL points ClipLab at another "latest release" address, for testing updates against a local copy
API = os.environ.get("CLIPLAB_UPDATE_URL") or "https://api.github.com/repos/ccarlson41/cliplab/releases/latest"
ROOT = PACKAGE.parent                 # the folder the code is in: the ClipLab home folder for an install
UPDATES = HOME / "updates"
CHECK = UPDATES / "check.json"        # GitHub's last answer (from the launcher, or Check now in the app)
REQUEST = UPDATES / "request.json"    # Update now, for the launcher
PROGRESS = UPDATES / "progress.json"  # what the launcher is doing about an update, for the app's banner
SKIPPED = UPDATES / "skipped.json"    # versions that didn't start and were rolled back
BEAT = UPDATES / "launcher-beat"      # touched by the launcher's loop, so the app knows updates can be installed
BACKUPS = HOME / "backups"
FILES = ROOT / ".cliplab-files"       # the installed version's code files, one per line (written by install.ps1 too)
CHECK_EVERY = 6 * 3600
HEALTHY_WITHIN = 60                   # seconds the new version gets to answer before it's rolled back
KEEP_BACKUPS = 3
MAX_ZIP = 200 << 20
ACTIVE = ("downloading", "waiting", "installing", "restarting")
# where code lives when there's no .cliplab-files yet (a copy installed before updates existed)
CODE_DIRS = ("cliplab", "scripts", "tests", "docs", "clipper", ".github")
CODE_FILES = ("README.md", "LICENSE", "install.ps1", ".gitignore", ".gitattributes")
# never code, whatever a file list or a zip says: settings, shorts, logs, downloads, git (see settings.py, .gitignore)
DATA_DIRS = {"inbox", "ready", "failed", "tossed", "work", "logs", "requests", "thumbs", "models", "bin", "sounds",
             "backups", "updates", "syncthing", ".stfolder", ".git", "__pycache__"}
DATA_FILES = {"config.json", "processed.json", "decisions.json", "status.json", "heartbeat", ".stignore",
              ".cliplab-files", "clipper/config.json"}


class UpdateError(Exception):
    """An update that can't go ahead, with a reason the app can show."""


def log(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "update.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def parse_version(text):
    """(major, minor, patch) from "0.10.0" or a "v0.10.0" tag, else None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(n) for n in m.groups()) if m else None


def newer(a, b):
    """True when version a is later than version b (both "x.y.z"); False when either isn't a version."""
    va, vb = parse_version(a), parse_version(b)
    return bool(va and vb and va > vb)


def describe(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"GitHub answered {e.code}"
    reason = getattr(e, "reason", e)
    return "it timed out" if "timed out" in str(reason) else str(reason)


def fetch(url, timeout=15, limit=1 << 20, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": f"ClipLab/{__version__}", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is bigger than expected")
    return data


def parse_release(data):
    """The version and download links in GitHub's latest-release JSON. Raises UpdateError if it can't be used."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("GitHub's latest release isn't a finished release.")
    version = parse_version(data.get("tag_name"))
    if not version:
        raise UpdateError(f"The latest release's tag ({data.get('tag_name')}) isn't a version number.")
    v = ".".join(map(str, version))
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or [] if isinstance(a, dict)}
    zip_url, sha_url = assets.get(f"cliplab-{v}.zip"), assets.get(f"cliplab-{v}.zip.sha256")
    if not (zip_url and sha_url):
        raise UpdateError(f"Release {v} has no cliplab-{v}.zip and .sha256 to install from.")
    return {"version": v, "zip": zip_url, "sha256": sha_url, "page": data.get("html_url") or "",
            "published": data.get("published_at") or ""}


def is_git():
    return (ROOT / ".git").exists()


def git(*args, timeout=60):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def git_blocked(fetch_first=True):
    """Why this git checkout shouldn't get `git pull --ff-only`, or None when it's safe to."""
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        if branch.returncode:
            return "git couldn't read this checkout"
        if branch.stdout.strip() != "main":
            return f"it's on branch {branch.stdout.strip()}, not main"
        if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return "it has changes of its own"
        if fetch_first and git("fetch", "--quiet", "origin", "main", timeout=120).returncode:
            return "git couldn't reach GitHub"
        ahead = git("rev-list", "--count", "origin/main..HEAD")
        if ahead.returncode or ahead.stdout.strip() != "0":
            return "it has commits of its own"
    except (OSError, subprocess.SubprocessError):
        return "git isn't available"
    return None


def check():
    """Ask GitHub for the latest release and keep the answer for the launcher and the app. Never raises."""
    result = {"checked": time.time(), "current": __version__, "latest": None}
    try:
        result["latest"] = parse_release(json.loads(fetch(API, accept="application/vnd.github+json")))
    except urllib.error.HTTPError as e:
        if e.code != 404:   # 404: nothing released yet, which isn't a problem
            result["error"] = f"Couldn't check for updates ({describe(e)})."
    except UpdateError as e:
        result["error"] = str(e)
    except (OSError, ValueError) as e:   # offline, a timeout, a garbled answer: keep the last release heard of
        result["error"] = f"Couldn't check for updates ({describe(e)})."
        result["latest"] = read_json(CHECK, {}).get("latest")
    if is_git():
        result["git_blocked"] = git_blocked()
    write_json(CHECK, result)
    return result


def check_now():
    """Check now in the app: ask GitHub, unless it was asked moments ago (GitHub allows 60 checks an hour)."""
    last = read_json(CHECK, {})
    return last if time.time() - last.get("checked", 0) < 30 else check()


def progress(state, version, message=""):
    write_json(PROGRESS, {"state": state, "version": version, "from": __version__, "message": message,
                          "at": time.time()})


def summary(cfg):
    """What the app shows about updates (Settings > More and the banner). Cheap: no network, no git."""
    checked = read_json(CHECK, {})
    latest = checked.get("latest") or {}
    version = latest.get("version")
    shown = read_json(PROGRESS, {})
    age = time.time() - shown.get("at", 0)
    if not (shown.get("state") in ACTIVE and age < 900 or shown.get("state") in ("failed", "rolled-back", "blocked")
            and age < 3 * 86400 and newer(shown.get("version"), __version__)):
        shown = None
    try:
        beat = time.time() - BEAT.stat().st_mtime < 30
    except OSError:
        beat = False
    return {"mode": cfg["auto_update"], "current": __version__, "latest": version,
            "available": newer(version, __version__), "page": latest.get("page") or "",
            "checked": checked.get("checked"), "error": checked.get("error"),
            "kind": "git" if is_git() else "zip", "blocked": checked.get("git_blocked") if is_git() else None,
            "skipped": version in read_json(SKIPPED, []), "requested": REQUEST.exists(), "launcher": beat,
            "progress": shown}


def request(version):
    """Update now: leave a note for the launcher, which does the installing. Returns an error, or None."""
    info = summary(load_config())
    if not info["available"] or info["latest"] != version:
        return "There's no update to install. Check again first."
    if info["blocked"]:
        return f"This copy of ClipLab is a git checkout ({info['blocked']}), so update it with git pull."
    if not info["launcher"]:
        return "ClipLab's launcher isn't running, so it can't restart itself. Run the install line to update."
    write_json(REQUEST, {"version": version, "at": time.time()})
    return None


# --- files ---

def is_data(rel, extra=()):
    """True for anything that isn't code: settings, shorts, logs, downloads (rel is a /-separated path in ROOT)."""
    low = rel.lower()
    parts = low.split("/")
    return (parts[0] in DATA_DIRS or low in DATA_FILES or "__pycache__" in parts or low.startswith("clipper/tmp/")
            or parts[-1].endswith((".log", ".log.old", ".tmp", ".part")) or "password" in parts[-1]
            or any(low == d or low.startswith(d + "/") for d in extra))


def data_inside_root():
    """The watch and output folders, when they're inside the code folder, as /-separated paths in it."""
    found = []
    cfg = load_config()
    for folder in (watch_dir(cfg), output_dir(cfg)):
        try:
            found.append(folder.resolve().relative_to(ROOT.resolve()).as_posix().lower())
        except ValueError:
            pass
    return tuple(found)


def installed_files(extra=()):
    """The code files of the installed version: .cliplab-files, or for older installs, what's in the code folders."""
    if FILES.exists():
        listed = {line.strip().replace("\\", "/") for line in FILES.read_text(encoding="utf-8").splitlines()}
        return {rel for rel in listed if rel and safe_rel(rel) and not is_data(rel, extra)}
    found = {name for name in CODE_FILES if (ROOT / name).is_file()}
    for top in CODE_DIRS:
        for p in (ROOT / top).rglob("*") if (ROOT / top).is_dir() else ():
            rel = p.relative_to(ROOT).as_posix()
            if p.is_file() and not is_data(rel, extra):
                found.add(rel)
    return found


def safe_rel(rel):
    parts = rel.split("/")
    return not (rel.startswith("/") or "\\" in rel or ":" in rel or any(p in ("", ".", "..") for p in parts))


def zip_contents(path, version):
    """{relative path: entry name} for the code in a release zip, after checking its paths and that it's `version`."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            tops = {n.split("/", 1)[0] for n in names}
            prefix = next(iter(tops)) + "/" if len(tops) == 1 else ""
            if f"{prefix}cliplab/__init__.py" not in names:
                prefix = ""
            if f"{prefix}cliplab/__init__.py" not in names:
                raise UpdateError("The update zip has no cliplab/__init__.py.")
            files = {}
            for name in names:
                rel = name[len(prefix):] if name.startswith(prefix) else ""
                if not safe_rel(rel):
                    raise UpdateError(f"The update zip has an unsafe path in it ({name}).")
                if not is_data(rel):   # a zip never overwrites your data, even one that has some in it
                    files[rel] = name
            init = z.read(prefix + "cliplab/__init__.py").decode("utf-8", "replace")
    except zipfile.BadZipFile:
        raise UpdateError("The update download isn't a zip.")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not found or found.group(1) != version:
        raise UpdateError(f"The update zip is version {found.group(1) if found else '?'}, not {version}.")
    return files


def download(latest):
    """Download the release zip and check it. Returns (path, files in it). Raises UpdateError."""
    v = latest["version"]
    try:
        want = fetch(latest["sha256"], timeout=30, limit=4096).decode("ascii", "replace").split()
    except (OSError, ValueError) as e:
        raise UpdateError(f"Couldn't download ClipLab {v}'s checksum ({describe(e)}).")
    want = want[0].lower() if want else ""
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise UpdateError(f"ClipLab {v}'s checksum file doesn't hold a SHA-256.")
    try:
        data = fetch(latest["zip"], timeout=120, limit=MAX_ZIP)
    except (OSError, ValueError) as e:
        raise UpdateError(f"Couldn't download ClipLab {v} ({describe(e)}).")
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError(f"The download of ClipLab {v} didn't match its checksum, so it wasn't installed.")
    UPDATES.mkdir(parents=True, exist_ok=True)
    path = UPDATES / f"cliplab-{v}.zip"
    path.write_bytes(data)
    return path, zip_contents(path, v)


def copy_file(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".update-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def remove_file(rel):
    path = ROOT / rel
    path.unlink(missing_ok=True)
    parent = path.parent
    while parent != ROOT and parent.is_dir() and not any(parent.iterdir()):   # tidy folders left empty
        parent.rmdir()
        parent = parent.parent


def install(zip_path, files):
    """Swap the code for the zip's: back up the old code, copy the new in, drop code files it no longer has.
    Returns the backup folder. On any error the old code is put back and the error raised."""
    extra = data_inside_root()
    old, new = installed_files(extra), {rel for rel in files if not is_data(rel, extra)}
    backup = BACKUPS / __version__
    shutil.rmtree(backup, ignore_errors=True)
    for rel in sorted(old | new):
        if (ROOT / rel).is_file():
            copy_file(ROOT / rel, backup / rel)
    write_json(backup / "backup.json", {"version": __version__, "files": sorted(old), "at": time.time()})
    try:
        with zipfile.ZipFile(zip_path) as z:
            for rel in sorted(new):
                dest = ROOT / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".update-tmp")
                with z.open(files[rel]) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.replace(tmp, dest)
        for rel in sorted(old - new):
            remove_file(rel)
        FILES.write_text("".join(rel + "\n" for rel in sorted(new)), encoding="utf-8")
    except Exception:
        restore(backup, new)
        raise
    return backup


def restore(backup, added):
    """Put a backup's code back, and remove files the version being undone added."""
    saved = read_json(backup / "backup.json", {})
    kept = {p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file()} - {"backup.json"}
    for rel in sorted(set(added) - kept):
        if not is_data(rel):
            remove_file(rel)
    for rel in sorted(kept):
        copy_file(backup / rel, ROOT / rel)
    FILES.write_text("".join(rel + "\n" for rel in saved.get("files", sorted(kept))), encoding="utf-8")


def prune_backups():
    folders = sorted((p for p in BACKUPS.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True) \
        if BACKUPS.exists() else []
    for old in folders[KEEP_BACKUPS:]:
        shutil.rmtree(old, ignore_errors=True)


# --- the launcher's side ---

def engine_busy():
    """True while the engine is rendering, or has app jobs (re-cuts, reels) waiting."""
    status = read_json(STATUS, None)
    if status and time.time() - status.get("started", 0) < 900:
        return True
    return REQUESTS.exists() and any(REQUESTS.glob("*.json"))


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
        subprocess.run(["kill", str(pid)], capture_output=True)


def answers(version, child):
    """True once the app answers /api/state as `version`; False if it doesn't in time, or the new launcher quit."""
    url = f"http://127.0.0.1:{load_config()['port']}/api/state"
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy
    deadline = time.time() + HEALTHY_WITHIN
    while time.time() < deadline:
        try:
            with local.open(url, timeout=5) as r:
                if json.load(r).get("version") == version:
                    return True
        except (OSError, ValueError):
            pass
        if child.poll() is not None:
            return False
        time.sleep(2)
    return False


def version_on_disk():
    found = re.search(r'__version__\s*=\s*"([^"]+)"', (PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    return found.group(1) if found else ""


def restart_or_undo(version, relaunch, undo):
    """Start the new version's launcher. Returns "exit" once it's up, or "rolled-back" after undoing it."""
    progress("restarting", version)
    try:
        child = relaunch()
    except OSError as e:
        log(f"couldn't start the new launcher: {e!r}")
        child = None
    if child and answers(version, child):
        log(f"ClipLab {version} is running; {__version__} handed over")
        progress("done", version)
        prune_backups()
        for leftover in UPDATES.glob("cliplab-*.zip"):
            leftover.unlink(missing_ok=True)
        return "exit"
    log(f"ClipLab {version} didn't answer within {HEALTHY_WITHIN} s: rolling back to {__version__}")
    if child:
        kill_tree(child.pid)
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            pass
    undo()
    skipped = read_json(SKIPPED, [])
    write_json(SKIPPED, sorted(set(skipped) | {version}, key=parse_version))
    progress("rolled-back", version, f"ClipLab {version} didn't start, so ClipLab went back to {__version__}. "
                                     f"It won't install {version} again by itself.")
    return "rolled-back"


def apply_zip(path, files, version, stop_services, relaunch):
    progress("installing", version)
    log(f"updating {__version__} -> {version}")
    stop_services()
    try:
        backup = install(path, files)
    except Exception as e:
        log(f"install of {version} failed, old code kept: {e!r}")
        progress("failed", version, f"Couldn't install ClipLab {version} ({e}). ClipLab stayed on {__version__}.")
        return "failed"
    return restart_or_undo(version, relaunch, lambda: restore(backup, files))


def apply_git(version, stop_services, relaunch):
    reason = git_blocked()
    if reason:
        progress("blocked", version, f"This copy of ClipLab is a git checkout ({reason}), so update it with git pull.")
        return "failed"
    old = git("rev-parse", "HEAD").stdout.strip()
    progress("installing", version)
    log(f"git pull for {version} (at {old[:10]})")
    stop_services()
    pull = git("pull", "--ff-only", "--quiet", "origin", "main", timeout=180)
    if pull.returncode:
        log(f"git pull failed: {pull.stderr.strip()[:300]}")
        progress("failed", version, "git pull didn't work, so ClipLab stayed as it was. See logs\\update.log.")
        return "failed"
    return restart_or_undo(version_on_disk() or version, relaunch, lambda: git("reset", "--keep", old))


def tick(state, stop_services, relaunch):
    """One pass from the launcher's loop (every 5 s). state is a dict the launcher keeps between passes.
    stop_services() stops the engine and the app; relaunch() starts a new launcher and returns its Popen.
    Returns "exit" when a new launcher has taken over, "rolled-back" or "failed" after a try, else None."""
    try:
        return _tick(state, stop_services, relaunch)
    except Exception as e:   # updates must never take the launcher down
        log(f"update error: {e!r}")
        state["tried"] = state.get("wanted")
        return None


def _tick(state, stop_services, relaunch):
    UPDATES.mkdir(parents=True, exist_ok=True)
    BEAT.write_text(str(time.time()), encoding="utf-8")
    cfg = load_config()
    checked = read_json(CHECK, {})
    if "checked" not in state or time.time() - max(state["checked"], checked.get("checked", 0)) >= CHECK_EVERY:
        state["checked"] = time.time()
        state.pop("tried", None)   # a fresh check gets a failed version another go (but not a rolled-back one)
        if cfg["auto_update"] != "off":
            checked = check()
    latest = checked.get("latest") or {}
    version = latest.get("version")
    asked = read_json(REQUEST, None)
    if asked and asked.get("version") != version:
        REQUEST.unlink(missing_ok=True)
        asked = None
    auto = (cfg["auto_update"] == "auto" and version not in read_json(SKIPPED, []) and state.get("tried") != version
            and not (is_git() and checked.get("git_blocked")))
    if not newer(version, __version__) or not (asked or auto):
        state.pop("idle_since", None)
        return None
    state["wanted"] = version
    if is_git():
        REQUEST.unlink(missing_ok=True)
        if engine_busy():
            progress("waiting", version)
            return None
        state["tried"] = version
        return apply_git(version, stop_services, relaunch)
    if state.get("zip_for") != version:
        progress("downloading", version)
        try:
            path, files = download(latest)
        except UpdateError as e:
            log(str(e))
            progress("failed", version, str(e))
            state["tried"] = version
            REQUEST.unlink(missing_ok=True)
            return "failed"
        state.update(zip_for=version, zip=path, files=files)
    if engine_busy():   # never cut a render short: wait for a quiet moment
        state.pop("idle_since", None)
        progress("waiting", version)
        return None
    if time.time() - state.setdefault("idle_since", time.time()) < 8:   # quiet for two passes in a row
        progress("waiting", version)
        return None
    REQUEST.unlink(missing_ok=True)
    state["tried"] = version
    state.pop("idle_since", None)
    return apply_zip(state["zip"], state["files"], version, stop_services, relaunch)
