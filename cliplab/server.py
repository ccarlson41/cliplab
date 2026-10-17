"""ClipLab app: a small web app for browsing and managing the shorts.

Serves http://localhost:<port> (and, with allow_lan, other devices on your home
network). Shows clips arriving and rendering, plays, downloads, copies captions,
keeps or tosses shorts, re-cuts a short from its raw clip, builds highlight reels,
and edits render settings. Jobs are handed to the engine through requests/.
"""
import ipaddress
import json
import mimetypes
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__, obs, obsctl, obsws, sounds
from .settings import (CONFIG, DECISIONS, FAILED, GAMES, HEARTBEAT, HOME, NO_WINDOW, PACKAGE, REQUESTS, STATE, STATUS,
                       THUMBS, TOSSED, VIDEO_EXT, WEB, WEBCAM_CORNERS, clean_config, load_config, output_dir, read_json,
                       tool, watch_dir, webcam_rect, write_json)

MULTI = ("DOUBLE", "TRIPLE", "QUADRA", "PENTA")
CHANGELOG = PACKAGE / "changelog.json"   # the What's new pop-up and patch history, newest release first
STATIC = {"manifest.webmanifest", "icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-512.png",
          "apple-touch-icon.png"}
# Request a feature opens a new GitHub issue on the feature_request form, filled in from the app ({...} blanks);
# feedback_url in config.json swaps in another form
FEEDBACK_GITHUB = ("https://github.com/ccarlson41/cliplab/issues/new?template=feature_request.yml"
                   "&labels=feature-request&title={title}&what={what}&why={why}&name={name}&version={version}")
thumb_lock = threading.Lock()
sync_cache = {"at": 0.0, "data": None}


def syncthing(peer):
    """Two-machine mode: connection to the peer and what's still copying in (cached for 2 s)."""
    if not (peer.get("syncthing_home") and peer.get("syncthing_device")):
        return {"online": None, "paused": False, "copying": [], "pct": None}
    if time.time() - sync_cache["at"] < 2 and sync_cache["data"]:
        return sync_cache["data"]
    try:
        gui = ET.parse(Path(peer["syncthing_home"]) / "config.xml").getroot().find("gui")
        base, key = "http://" + gui.findtext("address"), gui.findtext("apikey")

        def get(path):
            req = urllib.request.Request(f"{base}/rest/{path}", headers={"X-API-Key": key})
            with urllib.request.urlopen(req, timeout=2) as r:
                return json.load(r)

        conn = get("system/connections")["connections"].get(peer["syncthing_device"], {})
        files, pct = [], None
        folder = peer.get("syncthing_folder")
        if folder:
            need = get(f"db/need?folder={folder}&perpage=100")
            files = [f for k in ("progress", "queued", "rest") for f in need.get(k, [])]
            need_bytes = get(f"db/status?folder={folder}").get("needBytes", 0)
            total = sum(f.get("size", 0) for f in files)
            pct = round(100 * (total - need_bytes) / total) if total else None
        data = {"online": bool(conn.get("connected")), "paused": bool(conn.get("paused")),
                "copying": [f["name"] for f in files], "pct": pct}
    except Exception:
        data = {"online": None, "paused": False, "copying": [], "pct": None}
    sync_cache.update(at=time.time(), data=data)
    return data


def engine_health():
    status = read_json(STATUS, None)
    try:
        age = time.time() - float(HEARTBEAT.read_text())
    except (FileNotFoundError, ValueError):
        age = None
    busy = bool(status) and time.time() - status.get("started", 0) < 900
    return {"alive": busy or (age is not None and age < 30), "busy": busy, "status": status,
            "heartbeat_age": None if age is None else round(age)}


def recorded_at(key, fallback):
    """When the clip was recorded: from OBS's "Replay YYYY-MM-DD HH-MM-SS" name, else the given time."""
    m = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2})-(\d{2})", key)
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}" if m else fallback


def stem_of(entry):
    first = (entry.get("outputs") or [""])[0]
    return first.rsplit("_", 1)[0] if "_" in first else Path(first).stem


def latest_clip(watch):
    """The newest video in the watch folder, for the Getting started test."""
    clips = [p for p in watch.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT] if watch.exists() else []
    newest = max(clips, key=lambda p: p.stat().st_mtime, default=None)
    return {"name": str(newest.relative_to(watch)), "mtime": newest.stat().st_mtime} if newest else None


def setup_state():
    """Everything the Getting started screen checks: OBS's settings and whether a replay has arrived."""
    cfg = load_config()
    watch = watch_dir(cfg)
    return {"watch_dir": str(watch), "obs": obs.check(watch), "obs_running": obs.obs_running(),
            "latest": latest_clip(watch), "now": time.time(), "game": cfg["game"]}


def whats_new():
    """This version, every release for the What's new pop-up and patch history, and where feature requests go."""
    custom = load_config()["feedback_url"]
    return {"version": __version__, "releases": read_json(CHANGELOG, []),
            "feedback_url": custom or FEEDBACK_GITHUB, "feedback_to": "custom" if custom else "github"}


def obs_state(find_webcams):
    """OBS's mic, screen and webcam for the settings page. Follows the webcam if it was moved in OBS."""
    try:
        data = obsctl.describe(find_webcams)
    except obsws.ObsError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # an OBS version that answers differently
        return {"ok": False, "error": f"OBS answered in a way ClipLab didn't expect ({type(e).__name__}: {e})."}
    cam = data["webcam"]
    rect = webcam_rect(cam["rect"]) if cam["on"] else None
    cfg = load_config()
    if rect and cfg["webcam"]["rect"] and max(abs(a - b) for a, b in zip(rect, cfg["webcam"]["rect"])) > 0.005:
        cfg["webcam"]["rect"] = rect
        write_json(CONFIG, cfg)
    return {"ok": True, **data}


def obs_changes(body):
    """Check what the app asked to change in OBS. Returns (changes, error)."""
    changes = {}
    for key in ("mic", "display"):
        if body.get(key) is not None:
            if not isinstance(body[key], str) or not body[key]:
                return None, f"Pick a {key} from the list."
            changes[key] = body[key]
    if "webcam" in body:
        cam = body["webcam"] if isinstance(body["webcam"], dict) else {}
        device, corner = cam.get("device") or "", cam.get("corner", "bottom-left")
        try:
            size = float(cam.get("size", 0.25))
        except (TypeError, ValueError):
            size = 0
        if not isinstance(device, str) or corner not in WEBCAM_CORNERS or not 0.15 <= size <= 0.4:
            return None, "Pick a webcam, a corner and a size."
        changes["webcam"] = {"device": device, "corner": corner, "size": size}
    return changes, None


def build_state():
    cfg = load_config()
    peer = cfg["peer"]
    watch, out = watch_dir(cfg), output_dir(cfg)
    processed = read_json(STATE, {})
    health = engine_health()
    status = health["status"] or {}
    decisions = read_json(DECISIONS, {})
    sync = syncthing(peer)
    pending = [read_json(p, {}) for p in REQUESTS.glob("*.json")] if REQUESTS.exists() else []
    items = {}

    for name in sync["copying"]:
        if Path(name).suffix.lower() in VIDEO_EXT:
            key = name.replace("/", "\\")
            items[key] = {"key": key, "stage": "copying", "pct": sync["pct"] if len(sync["copying"]) <= 2 else None}

    if watch.exists():
        for p in watch.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXT or p.name.startswith((".syncthing", "~")):
                continue
            key = str(p.relative_to(watch))
            if key in processed or key in items:
                continue
            rendering = status.get("key") == key
            items[key] = {"key": key, "stage": "rendering" if rendering else "queued",
                          "tagged": (watch / (key + ".json")).exists(),
                          "time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    stats = {"total": 0, "week": 0, "multikills": 0, "kept": 0}
    for key, entry in processed.items():
        if "failed" in entry:
            try:
                reason = (FAILED / f"{Path(key).stem}.txt").read_text(encoding="utf-8").strip().splitlines()[-1][:200]
            except (FileNotFoundError, IndexError):
                reason = ""
            items[key] = {"key": key, "stage": "failed", "time": entry["failed"], "reason": reason}
            continue
        day = entry.get("dir", "")
        videos = {}
        for name in entry.get("outputs", []):
            if (out / day / name).exists():
                videos[name.rsplit("_", 1)[-1].removesuffix(".mp4")] = f"{day}/{name}"
        decision = decisions.get(key)
        if decision == "toss" or not videos:
            continue
        stem = stem_of(entry)
        caption_path = out / day / f"{stem}_caption.txt"
        labels = entry.get("labels") or []
        game = GAMES.get(entry.get("game", "league"))   # shorts from before game support are all League
        is_reel = bool(entry.get("reel"))
        raw = None if is_reel else watch / key
        paths = {}
        if peer.get("clips_path") and not is_reel:
            paths[f"{peer['name'] or 'Peer'} · original"] = f"{peer['clips_path']}\\{key}"
        if peer.get("shorts_path"):
            paths[f"{peer['name'] or 'Peer'} · shorts"] = f"{peer['shorts_path']}\\{day}"
        if not is_reel and not peer.get("clips_path"):
            paths["Original"] = str(watch / key)
        paths["Shorts" if not peer.get("name") else "This PC · shorts"] = str(out / day)
        items[key] = {
            "key": key, "stem": stem, "stage": "ready", "decision": decision, "reel": is_reel,
            "time": entry.get("done") if is_reel else recorded_at(key, entry.get("done")), "day": day,
            "videos": videos, "length": entry.get("length"), "labels": labels,
            "title": entry.get("title") or "", "champion": entry.get("champion") or "",
            "game": game["name"] if game else "",
            "caption": caption_path.read_text(encoding="utf-8") if caption_path.exists() else "",
            "start": entry.get("start"), "end": entry.get("end"), "src_duration": entry.get("src_duration"),
            "raw": quote(key.replace("\\", "/")) if raw and raw.exists() else None,
            "trimmed": entry.get("trimmed", False),
            "busy": status.get("key") == key or any(r.get("key") == key for r in pending),
            "paths": paths,
        }
        stats["total"] += 1
        stats["week"] += (entry.get("done") or "") >= week_ago
        stats["multikills"] += any(lbl.startswith(MULTI) for lbl in labels)
        stats["kept"] += decision == "keep"

    stats["free_gb"] = round(shutil.disk_usage(HOME).free / 1e9, 1)
    done_dir = REQUESTS / "done"
    results = sorted((read_json(p, {}) for p in done_dir.glob("*.json")), key=lambda r: r.get("time", ""))[-10:] \
        if done_dir.exists() else []
    clips = sorted(items.values(), key=lambda i: i.get("time") or "9999", reverse=True)
    editable = {k: v for k, v in cfg.items() if k not in ("peer", "watch_dir", "output_dir", "ffmpeg", "port",
                                                          "allow_lan", "setup_done", "feedback_url")}
    # new installs get the Getting started guide; two-PC setups record on the other PC, and anyone with shorts is set up
    setup_needed = not cfg["setup_done"] and not peer.get("name") and not processed
    return {"peer": {"name": peer["name"]} if peer.get("name") else None, "online": sync["online"],
            "paused": sync["paused"], "engine": health, "clips": clips, "stats": stats, "config": editable,
            "games": {k: g["name"] for k, g in GAMES.items()}, "setup_needed": setup_needed, "results": results,
            "queue": len(pending), "version": __version__,
            "now": datetime.now().isoformat(timespec="seconds")}


def inside(base, rel):
    path = (base / unquote(rel)).resolve()
    return path if base.resolve() in path.parents and path.is_file() else None


def thumbnail(rel):
    src = inside(output_dir(), rel)
    if not src:
        return None
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / (src.parent.name + "_" + src.stem + ".jpg")
    with thumb_lock:
        if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
            for at in ("4.5", "1", "0"):
                subprocess.run([str(tool("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y", "-ss", at,
                                "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)],
                               creationflags=NO_WINDOW, capture_output=True)
                if out.exists() and out.stat().st_size:
                    break
    return out if out.exists() else None


def decide(key, decision):
    decisions = read_json(DECISIONS, {})
    if decision == "toss":
        entry = read_json(STATE, {}).get(key, {})
        day, stem, out = entry.get("dir"), stem_of(entry), output_dir()
        if day:
            (TOSSED / day).mkdir(parents=True, exist_ok=True)
            for name in entry.get("outputs", []) + [f"{stem}_caption.txt"]:
                if (out / day / name).exists():
                    shutil.move(str(out / day / name), str(TOSSED / day / name))
    if decision == "clear":
        decisions.pop(key, None)
    else:
        decisions[key] = decision
    write_json(DECISIONS, decisions)


def cross_site(headers):
    """Why a POST looks like it came from another website, or None if it's from ClipLab's own page.

    Any page open in the same browser can POST to localhost, so ClipLab asks for two things its own page
    always sends: a JSON Content-Type (another site can't send one without a CORS preflight, which this
    server never approves) and an Origin, or failing that a Referer, naming the address the app is open on.
    Requests with neither header (curl, scripts on this PC) are let through on the Content-Type alone."""
    if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
        return "ClipLab only takes JSON requests."
    origin = headers.get("Origin")
    if origin is None:
        origin = headers.get("Referer")
    if origin is None:
        return None
    sent, host = urlparse(origin.strip()), (headers.get("Host") or "").strip().lower()
    if sent.scheme not in ("http", "https") or not host or sent.netloc.lower() != host:
        return "ClipLab only takes requests from its own page."
    return None


def queue_job(job):
    REQUESTS.mkdir(parents=True, exist_ok=True)
    job["id"] = uuid.uuid4().hex[:8]
    write_json(REQUESTS / f"{job['id']}.json", job)
    return job["id"]


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipLab/3"

    def log_message(self, *args):
        pass

    def local_only(self):
        ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip.is_loopback or ((ip.is_private or ip.is_link_local) and load_config()["allow_lan"]):
            return True
        self.send_error(403, "ClipLab only answers this PC (and your home network when allow_lan is on)")
        return False

    def discard_body(self):
        """Read and drop a refused POST's body: replying with it unread makes Windows reset the connection,
        and the sender can lose the refusal before reading it."""
        try:
            left = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        except (ValueError, OSError):
            pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download=False, cache=False):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            status = 206
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass

    def do_GET(self):
        if not self.local_only():
            return
        url = urlparse(self.path)
        route = url.path
        if route in ("/", "/index.html"):
            return self.send_file(WEB / "index.html")
        if route.lstrip("/") in STATIC:
            return self.send_file(WEB / route.lstrip("/"), cache=True)
        if route == "/api/state":
            return self.send_json(build_state())
        if route == "/api/setup":
            return self.send_json(setup_state())
        if route == "/api/changelog":
            return self.send_json(whats_new())
        if route.startswith("/sound/") and route.endswith(".wav") and route[7:-4] in sounds.TONES:
            body = sounds.wav_bytes(route[7:-4])
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return self.wfile.write(body)
        if route.startswith("/media/"):
            path = inside(output_dir(), route[len("/media/"):])
            return self.send_file(path, download="dl" in parse_qs(url.query)) if path else self.send_error(404)
        if route.startswith("/raw/"):
            path = inside(watch_dir(), route[len("/raw/"):])
            return self.send_file(path) if path else self.send_error(404)
        if route.startswith("/thumb/"):
            path = thumbnail(route[len("/thumb/"):])
            return self.send_file(path, cache=True) if path else self.send_error(404)
        self.send_error(404)

    def do_POST(self):
        if not self.local_only():
            return
        refused = cross_site(self.headers)
        if refused:
            self.discard_body()
            return self.send_json({"error": refused}, 403)
        route = urlparse(self.path).path
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "That request wasn't valid JSON."}, 400)
        state = read_json(STATE, {})

        if route == "/api/decide":
            key, decision = body.get("key"), body.get("decision")
            if decision not in ("keep", "toss", "clear") or key not in state:
                return self.send_json({"error": "Send a known clip and keep, toss or clear."}, 400)
            decide(key, decision)
            return self.send_json({"ok": True})

        if route == "/api/rerender":
            key = body.get("key")
            if key not in state or state[key].get("reel") or not (watch_dir() / key).exists():
                return self.send_json({"error": "The raw clip for this short is gone, so it can't be re-cut."}, 400)
            job = {"type": "rerender", "key": key}
            if "start" in body and "end" in body:
                try:
                    start, end = float(body["start"]), float(body["end"])
                except (TypeError, ValueError):
                    return self.send_json({"error": "Start and end need to be numbers of seconds."}, 400)
                if end - start < 1:
                    return self.send_json({"error": "Keep at least 1 second between start and end."}, 400)
                job.update(start=start, end=end)
            return self.send_json({"ok": True, "id": queue_job(job)})

        if route == "/api/reel":
            keys = [k for k in body.get("keys", []) if k in state]
            if len(keys) < 2:
                return self.send_json({"error": "Pick at least two shorts for a reel."}, 400)
            return self.send_json({"ok": True, "id": queue_job({"type": "reel", "keys": keys,
                                                                  "style": body.get("style", "crop")})})

        if route == "/api/config":
            cfg, error = clean_config(body)
            if error:
                return self.send_json({"error": error}, 400)
            write_json(CONFIG, cfg)
            return self.send_json({"ok": True})

        if route == "/api/obs/describe":   # a POST, not a GET: reading OBS borrows sources and can turn on the camera
            return self.send_json(obs_state(body.get("webcams") is True))

        if route == "/api/obs":
            changes, error = obs_changes(body)
            if error:
                return self.send_json({"error": error}, 400)
            try:
                rect, note = obsctl.apply(changes)
            except obsws.ObsError as e:
                return self.send_json({"error": str(e)}, 400)
            except Exception as e:  # an OBS version that answers differently
                return self.send_json({"error": f"OBS answered in a way ClipLab didn't expect ({e})."}, 400)
            if rect != "unchanged":   # the webcam moved, appeared or went away: keep the Webcam on top version in step
                cfg = load_config()
                cfg["webcam"] = {"rect": webcam_rect(rect), "corner": changes["webcam"]["corner"],
                                 "size": changes["webcam"]["size"]}
                styles = [st for st in cfg["styles"] if st != "cam"]
                cfg["styles"] = (styles + ["cam"]) if cfg["webcam"]["rect"] else (styles or ["crop"])
                write_json(CONFIG, cfg)
            return self.send_json({"ok": True, "note": note})

        if route == "/api/setup/done":
            cfg = load_config()
            cfg["setup_done"] = True
            write_json(CONFIG, cfg)
            return self.send_json({"ok": True})

        self.send_error(404)


def main():
    cfg = load_config()
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    mimetypes.add_type("image/svg+xml", ".svg")
    host = "0.0.0.0" if cfg["allow_lan"] else "127.0.0.1"
    ThreadingHTTPServer((host, int(cfg["port"])), Handler).serve_forever()
