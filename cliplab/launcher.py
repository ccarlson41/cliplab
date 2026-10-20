"""Runs the engine and the app together, restarting either if it exits, and installs updates (see updater.py).

`python -m cliplab` starts it (replacing a copy that's already running);
`python -m cliplab stop` stops it. Restarts are logged to logs/launcher.log.
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import updater
from .settings import LOGS, NO_WINDOW, PACKAGE

PIDFILE = LOGS / "launcher.pid"
SERVICES = ("engine", "app")
PYTHON = re.compile(r"pythonw?(\d+(\.\d+)*)?(\.exe)?")
RUNS_CLIPLAB = re.compile(r"(^|\s)-m\s+cliplab(\s|$)")


def note(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "launcher.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def describe_process(pid):
    """(program name, command line) of process pid; ("", "") if nothing runs under that number; None if the
    system couldn't say."""
    if os.name != "nt":
        try:
            parts = [p.decode(errors="replace") for p in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if p]
        except FileNotFoundError:
            return "", ""
        except OSError:
            return None
        return (Path(parts[0]).name, " ".join(parts)) if parts else ("", "")
    script = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' -ErrorAction Stop; "
              "if ($p) { $p.Name; $p.CommandLine }")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    lines = out.stdout.splitlines()
    return (lines[0].strip(), " ".join(line.strip() for line in lines[1:])) if lines else ("", "")


def is_cliplab(info):
    """True for a Python process running `-m cliplab`."""
    name, cmdline = info
    return bool(PYTHON.fullmatch(name.lower())) and bool(RUNS_CLIPLAB.search(cmdline))


def stop():
    """Stop the launcher named in launcher.pid (and its engine and app). After a restart that number can belong to
    any program, so it's only stopped when it's still ClipLab; if that can't be checked, it's left alone."""
    try:
        pid = int(PIDFILE.read_text())
    except (FileNotFoundError, ValueError):
        return False
    if pid == os.getpid():
        return False
    info = describe_process(pid)
    if info is None:
        note(f"didn't stop pid {pid} from launcher.pid: couldn't check that it's ClipLab")
        return False
    if not is_cliplab(info):
        PIDFILE.unlink(missing_ok=True)   # stale: ClipLab isn't running under that number any more
        return False
    updater.kill_tree(pid)
    PIDFILE.unlink(missing_ok=True)
    time.sleep(1)
    return True


def stop_services(procs):
    """Stop this launcher's engine and app (and anything they started, like ffmpeg) for an update."""
    for proc in procs.values():
        if proc.poll() is None:
            updater.kill_tree(proc.pid)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                pass
    procs.clear()


def relaunch():
    """Start a launcher running the code now on disk, to take over from this one after an update."""
    PIDFILE.unlink(missing_ok=True)   # else the new launcher would stop this one, and so itself (it's our child)
    flags = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    cmd = [sys.executable, "-m", "cliplab"]
    try:   # outlive this launcher even if it runs inside a job that's closed when it exits (some terminals do that)
        return subprocess.Popen(cmd, cwd=str(PACKAGE.parent),
                                creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    except OSError:   # a job that doesn't allow it
        return subprocess.Popen(cmd, cwd=str(PACKAGE.parent), creationflags=flags)


def main():
    stop()
    LOGS.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    procs, backoff, update = {}, {name: 1 for name in SERVICES}, {}
    note("launcher started")
    while True:
        for name in SERVICES:
            proc = procs.get(name)
            if proc and proc.poll() is None:
                continue
            if proc:
                note(f"{name} exited with {proc.returncode}; restarting in {backoff[name]}s")
                time.sleep(backoff[name])
                backoff[name] = min(backoff[name] * 2, 60)
            procs[name] = subprocess.Popen([sys.executable, "-m", "cliplab", name], cwd=str(PACKAGE.parent),
                                           creationflags=NO_WINDOW)
            note(f"{name} started (pid {procs[name].pid})")
        for name, proc in procs.items():   # a service that keeps running earns its backoff back
            if proc.poll() is None and backoff[name] > 1:
                backoff[name] = max(1, backoff[name] // 2)
        result = updater.tick(update, lambda: stop_services(procs), relaunch)
        if result == "exit":
            note("handed over to the updated launcher")
            return
        if result == "rolled-back":   # the new launcher wrote its pid; this one carries on
            PIDFILE.write_text(str(os.getpid()))
        if result:
            note(f"update {result}; starting the engine and the app again")
            continue
        time.sleep(5)
