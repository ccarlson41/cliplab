"""Runs the engine and the app together, restarting either if it exits.

`python -m cliplab` starts it (replacing a copy that's already running);
`python -m cliplab stop` stops it. Restarts are logged to logs/launcher.log.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

from .settings import LOGS, NO_WINDOW, PACKAGE

PIDFILE = LOGS / "launcher.pid"
SERVICES = ("engine", "app")


def note(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "launcher.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def stop():
    try:
        pid = int(PIDFILE.read_text())
    except (FileNotFoundError, ValueError):
        return False
    if pid == os.getpid():
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
        subprocess.run(["kill", str(pid)], capture_output=True)
    PIDFILE.unlink(missing_ok=True)
    time.sleep(1)
    return True


def main():
    stop()
    LOGS.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    procs, backoff = {}, {name: 1 for name in SERVICES}
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
        time.sleep(5)
