"""Where ClipLab keeps its files, and its settings.

Everything lives under the ClipLab home folder: %CLIPLAB_HOME%, or
%USERPROFILE%\\ClipLab when that isn't set. config.json there holds the settings;
anything missing falls back to DEFAULTS below.
"""
import ctypes
import json
import os
import shutil
from pathlib import Path

HOME = Path(os.environ.get("CLIPLAB_HOME") or Path.home() / "ClipLab").resolve()
CONFIG = HOME / "config.json"
STATE = HOME / "processed.json"       # one entry per clip (or reel) the engine has handled
STATUS = HOME / "status.json"         # what the engine is rendering right now
HEARTBEAT = HOME / "heartbeat"        # touched every engine loop; the app shows it as health
DECISIONS = HOME / "decisions.json"   # keep / toss choices from the app
FAILED = HOME / "Failed"
TOSSED = HOME / "Tossed"
WORK = HOME / "work"                  # renders land here first, then move into the output folder
LOGS = HOME / "logs"
REQUESTS = HOME / "requests"          # jobs from the app (re-cut, reel) for the engine
THUMBS = HOME / "thumbs"
PACKAGE = Path(__file__).resolve().parent
WEB = PACKAGE / "web"

VIDEO_EXT = {".mp4", ".mkv", ".mov"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW for subprocesses on Windows
ALL_STYLES = ("blur", "crop")
GAMES = {                             # games ClipLab knows: display name (as in tag files) and caption hashtags
    "league": {"name": "League of Legends", "hashtags": "#leagueoflegends #lol"},
    "r6": {"name": "Rainbow Six Siege", "hashtags": "#rainbowsixsiege #r6siege #r6"},
}

DEFAULTS = {
    # --- folders and machine setup (edit config.json by hand or rerun setup) ---
    "watch_dir": str(HOME / "Inbox"),      # raw clips arrive here (your OBS folder, or a synced folder)
    "output_dir": str(HOME / "Ready"),     # finished shorts go here
    "ffmpeg": "",                          # folder holding ffmpeg/ffprobe; empty = HOME\\bin, then PATH
    "port": 8765,
    "allow_lan": False,                    # True lets phones/other PCs on your home network open the app
    "peer": {                              # optional second machine (two-machine mode via Syncthing)
        "name": "",                        # e.g. "Gaming PC"; empty = one-machine mode
        "syncthing_home": "",              # this machine's Syncthing config folder (holds config.xml)
        "syncthing_device": "",            # the peer's Syncthing device ID
        "syncthing_folder": "",            # Syncthing folder ID that delivers raw clips
        "clips_path": "",                  # where raw clips live on the peer (shown in the app)
        "shorts_path": "",                 # where finished shorts land on the peer (shown in the app)
    },
    # --- render settings (editable from the app) ---
    "game": "league",                      # a key of GAMES; a tag file naming its game overrides it per clip
    "styles": ["blur", "crop"],
    "lead_in": 4.0,
    "tail": 3.0,
    "min_seconds": 8.0,
    "max_seconds": 60.0,
    "blur_zoom": 0.62,
    "watermark": "",
    "crf": 21,
    "maxrate_mbps": 8,
    "tag_wait": 20,
    "recycle_tossed_after_days": 14,
}
APP_EDITABLE = ("game", "styles", "lead_in", "tail", "min_seconds", "max_seconds", "blur_zoom", "watermark",
                "crf", "maxrate_mbps", "tag_wait", "recycle_tossed_after_days")
LIMITS = {
    "lead_in": (0, 15), "tail": (0, 15), "min_seconds": (3, 60), "max_seconds": (10, 180),
    "blur_zoom": (0.4, 1.0), "crf": (16, 30), "maxrate_mbps": (2, 20), "tag_wait": (0, 120),
    "recycle_tossed_after_days": (1, 365),
}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    saved = read_json(CONFIG, {})
    for key, value in saved.items():
        if key == "peer" and isinstance(value, dict):
            cfg["peer"].update({k: v for k, v in value.items() if k in DEFAULTS["peer"]})
        elif key in DEFAULTS:
            cfg[key] = value
    game = str(cfg["game"]).lower()   # hand edits like "R6" still count; anything unknown means League
    cfg["game"] = game if game in GAMES else DEFAULTS["game"]
    return cfg


def watch_dir(cfg=None):
    return Path((cfg or load_config())["watch_dir"])


def output_dir(cfg=None):
    return Path((cfg or load_config())["output_dir"])


def tool(name, cfg=None):
    """Path to ffmpeg/ffprobe: the configured folder, then HOME\\bin, then PATH."""
    cfg = cfg or load_config()
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in filter(None, (cfg.get("ffmpeg"), str(HOME / "bin"))):
        if (Path(folder) / exe).exists():
            return Path(folder) / exe
    found = shutil.which(name)
    return Path(found) if found else Path(exe)


def check_folders(cfg):
    """Return a problem with the folder setup, or None."""
    watch, out = Path(cfg["watch_dir"]).resolve(), Path(cfg["output_dir"]).resolve()
    if watch == out or watch in out.parents:
        return "output_dir can't be the watch folder or inside it, or every short would be re-processed."
    if out in watch.parents:
        return "watch_dir can't be inside output_dir."
    return None


def clean_config(changes):
    """Validate render settings sent by the app. Returns (merged config, error or None)."""
    cfg = load_config()
    for key, value in changes.items():
        if key not in APP_EDITABLE:
            return cfg, f"Unknown setting: {key}"
        if key == "styles":
            styles = [s for s in ALL_STYLES if s in value]
            if not styles:
                return cfg, "Pick at least one version to render."
            cfg[key] = styles
        elif key == "game":
            if not isinstance(value, str) or value not in GAMES:
                return cfg, f"Unknown game: {value}"
            cfg[key] = value
        elif key == "watermark":
            cfg[key] = "".join(c for c in str(value) if c.isalnum() or c in " @._-#")[:40].strip()
        else:
            try:
                num = float(value)
            except (TypeError, ValueError):
                return cfg, f"{key} needs a number."
            lo, hi = LIMITS[key]
            if not lo <= num <= hi:
                return cfg, f"{key} must be between {lo} and {hi}."
            cfg[key] = int(num) if isinstance(DEFAULTS[key], int) else num
    if cfg["min_seconds"] > cfg["max_seconds"]:
        return cfg, "Minimum length can't be longer than the maximum."
    return cfg, None


def tag_path(clip):
    return clip.with_name(clip.name + ".json")


def recycle(path):
    """Send a file to the Windows Recycle Bin (recoverable, not a hard delete)."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 16, 64, 1024
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(Path(path).resolve()) + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
