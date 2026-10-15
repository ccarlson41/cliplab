"""Reads OBS Studio's settings so the Getting started screen can check the setup steps.

Read-only: ClipLab never changes OBS's files from here. OBS writes its settings when
you press Apply or OK, so the checks follow along as you change them.
"""
import configparser
import json
import os
import subprocess
from pathlib import Path

from .settings import NO_WINDOW

OBS_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio" / "bin" / "64bit" / "obs64.exe"
GOOD_REPLAY_SECONDS = 45   # shorts are up to max_seconds (60 by default) of the end of the replay


def obs_home():
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "obs-studio"


def read_ini(path):
    ini = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    ini.optionxform = str   # OBS's keys are case-sensitive
    try:
        ini.read_string(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, configparser.Error):
        return None
    return ini


def current_profile(home):
    """The folder of the profile OBS uses: user.ini (OBS 31+) or global.ini, else the only profile there is."""
    for name in ("user.ini", "global.ini"):
        ini = read_ini(home / name)
        folder = ini and (ini.get("Basic", "ProfileDir", fallback="") or ini.get("Basic", "Profile", fallback=""))
        if folder and (home / "basic" / "profiles" / folder / "basic.ini").exists():
            return home / "basic" / "profiles" / folder
    profiles = [p.parent for p in (home / "basic" / "profiles").glob("*/basic.ini")]
    return profiles[0] if len(profiles) == 1 else None


def videos_folder():
    """OBS saves to the Windows Videos folder until you pick another one."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return os.path.expandvars(winreg.QueryValueEx(key, "My Video")[0])
    except (ImportError, OSError):
        return str(Path.home() / "Videos")


def same_folder(a, b):
    return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def hotkey_name(binding):
    """{"control": true, "key": "OBS_KEY_F12"} -> "Ctrl + F12"."""
    mods = [label for flag, label in (("control", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"), ("command", "Win"))
            if binding.get(flag)]
    key = str(binding.get("key", "")).removeprefix("OBS_KEY_")
    key = {"MOUSE4": "Mouse 4", "MOUSE5": "Mouse 5", "NUMLOCK": "Num Lock"}.get(key, key.replace("_", " ").title()
                                                                               if len(key) > 3 else key)
    return " + ".join(mods + [key]) if key else ""


def save_hotkey(ini):
    """The Save Replay hotkey(s) from a profile's [Hotkeys] section, e.g. ["F12"]."""
    try:
        bindings = json.loads(ini.get("Hotkeys", "ReplayBuffer", fallback="") or "{}").get("ReplayBuffer.Save", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return [name for name in (hotkey_name(b) for b in bindings if isinstance(b, dict)) if name]


def obs_running():
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True,
                             timeout=5, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return "obs64.exe" in out.lower()


def check(watch_dir, home=None, videos=None):
    """What we can tell about OBS's setup for ClipLab. Unknown values are None, so the app just shows the steps."""
    home = Path(home) if home else obs_home()
    result = {"installed": OBS_EXE.exists() or home.exists(), "profile": None, "replay_buffer": None,
              "replay_seconds": None, "same_as_stream": False, "path": None, "path_ok": None, "hotkeys": None}
    profile = current_profile(home) if home.exists() else None
    ini = read_ini(profile / "basic.ini") if profile else None
    if not ini:
        return result
    result["profile"] = ini.get("General", "Name", fallback=profile.name)
    if ini.get("Output", "Mode", fallback="Simple") == "Advanced":
        on, seconds = ini.get("AdvOut", "RecRB", fallback="false"), ini.get("AdvOut", "RecRBTime", fallback="20")
        path = ini.get("AdvOut", "RecFilePath", fallback="")
    else:
        on, seconds = ini.get("SimpleOutput", "RecRB", fallback="false"), ini.get("SimpleOutput", "RecRBTime", fallback="20")
        path = ini.get("SimpleOutput", "FilePath", fallback="")
        # Simple mode has no replay buffer while Recording Quality is "Same as stream" (OBS's default)
        result["same_as_stream"] = ini.get("SimpleOutput", "RecQuality", fallback="Stream") == "Stream"
    path = path or videos or videos_folder()
    try:
        result["replay_seconds"] = int(float(seconds))
    except ValueError:
        pass
    result["replay_buffer"] = on.lower() == "true" and not result["same_as_stream"]
    result["path"] = str(Path(path))
    result["path_ok"] = same_folder(path, str(watch_dir))
    result["hotkeys"] = save_hotkey(ini)
    return result
