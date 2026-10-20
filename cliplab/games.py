"""Spots a game running on this PC, so the engine can hold renders until it closes.

One `tasklist` per check, no window. Anything odd (not Windows, tasklist missing or
slow, output it can't read) counts as "no game": renders then go ahead as before.
"""
import csv
import os
import subprocess

from .settings import NO_WINDOW

# in-game executables (lowercase) -> the name the app shows. League's client (LeagueClient*.exe) stays open
# between matches, so only the match itself, "League of Legends.exe", counts.
KNOWN = {
    "league of legends.exe": "League of Legends",
    "rainbowsix.exe": "Rainbow Six Siege",
    "rainbowsix_be.exe": "Rainbow Six Siege",        # BattlEye launch
    "rainbowsix_vulkan.exe": "Rainbow Six Siege",
    "rainbowsix_dx12.exe": "Rainbow Six Siege",      # Siege X's DirectX 12 build
    "valorant-win64-shipping.exe": "VALORANT",
}


def parse_tasklist(text):
    """Lowercase image names from `tasklist /FO CSV /NH` output."""
    names = set()
    for row in csv.reader(text.splitlines()):
        if row and row[0].strip().lower().endswith(".exe"):
            names.add(row[0].strip().lower())
    return names


def running():
    """Lowercase names of the programs running now, or None if that can't be told."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, encoding="oem",
                             errors="replace", timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError, LookupError):
        return None
    return parse_tasklist(out.stdout) if out.returncode == 0 else None


def find_game(names, extra=()):
    """The display name of a game among the running names, or None. extra = the game_processes setting."""
    for exe, name in KNOWN.items():
        if exe in names:
            return name
    for exe in extra:
        if exe.lower() in names:
            return exe[:-4] if exe.lower().endswith(".exe") else exe
    return None


def detect(cfg):
    """The game running on this PC now, or None (also when it can't be told: fail open)."""
    try:
        names = running()
        return find_game(names, cfg.get("game_processes") or ()) if names else None
    except Exception:  # never let game spotting stop a render
        return None
