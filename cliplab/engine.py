"""ClipLab engine: turns gaming clips that land in the watch folder into vertical shorts.

Runs forever (started by the launcher). Each loop it:
  1. handles jobs the app dropped in requests/ (re-cut a short, build a reel),
  2. renders any new clip in the watch folder once it has stopped growing,
  3. once an hour, recycles tossed shorts older than the configured age.

Each clip becomes 1080x1920 shorts in one ffmpeg pass (one decode, one file per
enabled style) plus a caption file, in output_dir/<date>/:
  <name>_blur.mp4  the fight in the middle, blurred copy filling the frame
  <name>_crop.mp4  tight center crop that fills the phone screen
  <name>_cam.mp4   your webcam with the play, in the layout you pick: a strip on top or at the bottom, half and
                   half, or a box in a corner (when the app has placed a webcam in OBS; see layouts.py)
Originals are never touched.

If a tag file sits next to the clip (<clip>.json, see docs/tag-format.md), the
shorts are trimmed to the action and its labels are drawn on screen. Optional
extras: burned-in subtitles of what was said (subtitles.py) and an intro hook
(a whoosh and a quick zoom punch). Settings are re-read for every job.
"""
import json
import shutil
import threading
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import layouts, obsctl, obsws, sounds, subtitles
from .layouts import cam_height  # noqa: F401  (the top strip's height; tests and older callers use it from here)
from .settings import (FAILED, GAMES, HEARTBEAT, HOME, LOGS, NO_WINDOW, REQUESTS, STATE, STATUS, TOSSED, VIDEO_EXT, WORK,
                       cam_layout, check_folders, load_config, output_dir, read_json, recycle, tag_path, tool,
                       watch_dir, webcam_rect, write_json)

POLL_SECONDS = 5
FONT = "C\\:/Windows/Fonts/arialbd.ttf"
LOG_LIMIT = 1_000_000


# ---------- small helpers ----------

def log(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / "engine.log"
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        path.replace(LOGS / "engine.log.1")
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW, check=True, cwd=cwd)


def probe(path, cfg=None):
    out = run([str(tool("ffprobe", cfg)), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
               "-of", "json", str(path)])
    info = json.loads(out.stdout)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return float(info["format"]["duration"]), has_audio


def load_tags(clip):
    """The auto-clipper's tag file for this clip, or None."""
    data = read_json(tag_path(clip), None)
    return data if isinstance(data, dict) and data.get("events") else None


def game_of(tags, cfg):
    """Which game a clip is from: the one its tag file names, if ClipLab knows it, else the game in settings."""
    named = (tags or {}).get("game")
    for key, game in GAMES.items():
        if game["name"] == named:
            return key
    return cfg.get("game") if cfg.get("game") in GAMES else "league"


def safe_text(s):
    return "".join(c for c in str(s).upper() if c.isalnum() or c == " ")


# ---------- planning ----------

def plan(duration, tags, cfg, override=None):
    """Return (start, length, overlays); overlays are (label, seconds into the short).

    override = {"start": s, "end": e} in seconds of the raw clip, set by a re-cut from the app.
    """
    events = (tags or {}).get("events", [])
    if override:
        start = min(max(0.0, float(override["start"])), max(0.0, duration - 1))
        end = min(duration, max(start + 1, float(override["end"])))
    elif events:
        befores = [float(e["secondsBeforeEnd"]) for e in events]
        start = max(0.0, duration - (max(befores) + cfg["lead_in"]))
        end = min(duration, duration - min(befores) + cfg["tail"])
        if end - start < cfg["min_seconds"]:
            start = max(0.0, end - cfg["min_seconds"])
        if end - start > cfg["max_seconds"]:
            start = end - cfg["max_seconds"]
    else:
        start, end = max(0.0, duration - cfg["max_seconds"]), duration
    overlays = []
    for e in events:
        at = (duration - float(e["secondsBeforeEnd"])) - start
        if e.get("label") and 0 <= at <= end - start:
            overlays.append((e["label"], at))
    return start, end - start, overlays


# ---------- rendering ----------

def fill(w, h):
    """Filters that cut the middle of the picture to w:h and scale it to w x h, whatever shape the input is."""
    return f"crop='min(iw,ih*{w / h:.4f})':'min(ih,iw*{h / w:.4f})',scale={w}:{h}"


def rounded(r):
    """A geq expression: 255 inside a W x H rectangle with corners rounded by r pixels, 0 outside, smooth edges."""
    return (f"'255*clip({r}+0.5-hypot(max(0,abs(X+0.5-W/2)-(W/2-{r})),max(0,abs(Y+0.5-H/2)-(H/2-{r}))),0,1)'")


def cam_graph(src, out, webcam, cam):
    """The Webcam version: the webcam (webcam = its rect in OBS's canvas) placed as layouts.frame says (cam)."""
    x, y, w, h = webcam
    cx, cy, cw, ch = cam["cam"]
    face = (f"[{src}c]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
            f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch}")
    game = f"[{src}g]{fill(*cam['game'][2:])}[{src}game]"
    if cam["layout"] != "corner":   # two strips, one above the other
        order = f"[{src}game][{src}cam]" if cam["layout"] == "bottom" else f"[{src}cam][{src}game]"
        return (f"[{src}]split=2[{src}c][{src}g];{face}[{src}cam];{game};"
                f"{order}vstack,setsar=1,format=yuv420p[{out}]")
    # a rounded box with a white edge over the play; both shapes are drawn once and reused for every frame
    b, r = layouts.BORDER, layouts.RADIUS
    return (f"[{src}]split=2[{src}c][{src}g];{game};"
            f"{face},format=yuva420p[{src}face];"
            f"color=c=black:s={cw}x{ch}:r=1:d=1,format=gray,geq=lum={rounded(r)}[{src}mask];"
            f"[{src}face][{src}mask]alphamerge[{src}cam];"
            f"color=c=white:s={cw + 2 * b}x{ch + 2 * b}:r=1:d=1,format=rgba,"
            f"geq=r=255:g=255:b=255:a={rounded(r + b)}[{src}edge];"
            f"[{src}game][{src}edge]overlay={cx - b}:{cy - b}[{src}under];"
            f"[{src}under][{src}cam]overlay={cx}:{cy},setsar=1,format=yuv420p[{out}]")


def style_graph(style, src, out, cfg, webcam=None, cam=None):
    """cam = the Webcam version's layout (layouts.frame), for style "cam"."""
    if style == "cam":
        return cam_graph(src, out, webcam, cam or layouts.frame(webcam))
    if style == "blur":
        z = cfg["blur_zoom"]
        return (f"[{src}]split=2[{src}a][{src}b];"
                f"[{src}a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                f"boxblur=8:2,eq=brightness=-0.10,scale=1080:1920[{src}bg];"
                f"[{src}b]crop=iw*{z:.3f}:ih:iw*{(1 - z) / 2:.3f}:0,scale=1080:-2[{src}fg];"
                f"[{src}bg][{src}fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[{out}]")
    # League's camera keeps your champion near the middle of the screen; shooters keep the crosshair there.
    return f"[{src}]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,format=yuv420p[{out}]"


def post_filters(overlays, cfg, label_y="h*0.13", watermark_y="h*0.88"):
    parts = [
        f"drawtext=fontfile='{FONT}':text='{safe_text(label)}':fontsize=104:fontcolor=white:"
        f"borderw=7:bordercolor=black@0.85:x=(w-text_w)/2:y={label_y}:"
        f"enable='between(t,{max(0, at - 0.15):.2f},{at + 2.2:.2f})'"
        for label, at in overlays]
    if cfg.get("watermark"):
        parts.append(f"drawtext=fontfile='{FONT}':text='{cfg['watermark']}':fontsize=42:fontcolor=white@0.75:"
                     f"borderw=3:bordercolor=black@0.5:x=(w-text_w)/2:y={watermark_y}")
    return ",".join(parts)


def move_in(tmp, dst):
    """Move a finished render into place, retrying if a sync tool or viewer briefly holds the file."""
    for attempt in range(10):
        try:
            shutil.move(str(tmp), str(dst))
            return
        except PermissionError:
            time.sleep(1 + attempt)
    shutil.move(str(tmp), str(dst))


PUNCH = 0.12, 0.3   # the intro zoom: starts this much bigger and settles over this many seconds
ZOOM = (f"scale=w='1080*(1+{PUNCH[0]}*max(0,1-t/{PUNCH[1]}))':h=-2:eval=frame,crop=1080:1920")


def styles_for(cfg, webcam):
    """The enabled styles this clip can have: the Webcam version needs to know where the webcam was."""
    styles = [s for s in cfg["styles"] if s != "cam" or webcam]
    return styles or ["crop"]


def render(src, out_dir, stem, start, length, has_audio, overlays, cfg, webcam=None, subs=None, layout=None,
           styles=None):
    """One ffmpeg pass producing every enabled style (or just these styles). Returns the output file names.

    subs = {"normal": ..., "cam": ...}: subtitle files from subtitles.prepare, relative to HOME (ffmpeg runs there).
    layout = the Webcam version's layout (settings.cam_layout); None is the webcam on top."""
    styles = styles or styles_for(cfg, webcam)
    n = len(styles)
    subs = subs or {}
    cam = layouts.frame(webcam, layout) if webcam else None
    whoosh = cfg.get("intro") == "whoosh"
    graph = ["[0:v]" + (f"split={n}" + "".join(f"[v{i}]" for i in range(n)) if n > 1 else "null[v0]")]
    for i, style in enumerate(styles):
        # in the Webcam version, play labels and the watermark go where the webcam isn't
        post = post_filters(overlays, cfg, cam["label_y"], cam["watermark_y"]) if style == "cam" \
            else post_filters(overlays, cfg)
        sub = subs.get("cam" if style == "cam" else "normal")
        chain = [ZOOM if cfg.get("intro") in ("whoosh", "punch") else "", f"subtitles=f={sub}" if sub else "", post]
        graph.append(style_graph(style, f"v{i}", f"{style}raw", cfg, webcam, cam))
        graph.append(f"[{style}raw]{','.join(c for c in chain if c) or 'null'}[{style}v]")
    split = f"asplit={n}" + "".join(f"[a{i}]" for i in range(n)) if n > 1 else "anull[a0]"
    if has_audio and whoosh:
        graph.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[game];"
                     "[1:a]aresample=48000,volume=0.8[swish];"
                     "[game][swish]amix=inputs=2:duration=first:normalize=0,"
                     f"alimiter=limit=0.89:level=disabled:latency=1,{split}")   # the swish on a loud moment can't clip
    elif has_audio:
        graph.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11," + split)
    elif whoosh:   # a silent clip still gets the whoosh, then silence
        graph.append(f"[1:a]aresample=48000,apad,{split}")

    WORK.mkdir(parents=True, exist_ok=True)
    cmd = [str(tool("ffmpeg", cfg)), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}",
           "-i", str(src)]
    if whoosh:
        cmd += ["-i", str(sounds.whoosh_file())]
    cmd += ["-filter_complex", ";".join(graph)]
    has_audio = has_audio or whoosh
    tmps = []
    for i, style in enumerate(styles):
        tmp = WORK / f"{stem}_{style}.mp4"
        cmd += ["-map", f"[{style}v]", "-t", f"{length:.2f}", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", str(cfg["crf"]), "-maxrate", f"{cfg['maxrate_mbps']}M",
                "-bufsize", f"{cfg['maxrate_mbps'] * 2}M", "-r", "60", "-movflags", "+faststart"]
        cmd += (["-map", f"[a{i}]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if has_audio else ["-an"])
        cmd.append(str(tmp))
        tmps.append(tmp)
    run(cmd, cwd=HOME)
    out_dir.mkdir(parents=True, exist_ok=True)
    for tmp in tmps:
        move_in(tmp, out_dir / tmp.name)
    return [t.name for t in tmps]


def caption_for(tags, fallback, game="league"):
    hashtags = f"{GAMES[game]['hashtags']} #gaming #shorts"
    if not tags:
        return f"{fallback}\n\n{hashtags}\n"
    champ = tags.get("champion", "")   # or the operator, agent... whoever the player was
    title = tags.get("title") or " / ".join(e.get("label", "") for e in tags["events"])
    tag_champ = f" #{''.join(c for c in champ if c.isalnum()).lower()}" if champ else ""
    return f"{title}\n\n{hashtags}{tag_champ}\n"


# ---------- jobs ----------

def current_webcam(cfg):
    """Where the webcam is for a new clip: asked of OBS when it's running here, else the spot the app saved."""
    if not cfg["webcam"]["rect"]:
        return None
    try:
        return webcam_rect(obsctl.live_webcam_rect())
    except Exception:  # OBS closed, on another PC, or odd: never let it stop a render
        return cfg["webcam"]["rect"]


def make_subtitles(clip, key, start, length, has_audio, cfg, cam, note):
    """(subtitle files, why there are none). Files are {} when subtitles are off, there's nothing to hear, or
    anything goes wrong; the reason is shown on the short in the app ("" when subtitles are off).
    cam = the Webcam version's layout (layouts.frame), or None when there's no Webcam version."""
    if cfg["subtitles"] == "off":
        return {}, ""
    if not has_audio:
        return {}, "No subtitles: this clip has no sound."
    try:
        files = subtitles.prepare(clip, start, length, cfg, cam["subs"] if cam else None, note)
        return files, "" if files else "No subtitles: nobody talks in this clip."
    except subtitles.SubtitleError as e:
        log(f"SUBS {key}: made without subtitles ({e})")
        return {}, str(e)
    except Exception as e:  # subtitles are a nice extra: never let them stop a short
        detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()
        reason = (detail or [type(e).__name__])[-1][:200]
        log(f"SUBS {key}: made without subtitles ({reason})")
        return {}, f"Subtitles didn't work this time ({reason})."


def process_clip(clip, key, state, cfg, override=None, layout=None):
    """Make a clip's shorts (again) and record them in state. Returns the clip's new state entry.

    override = {"start", "end"} from a re-cut in the app. layout = a new webcam layout for this short from the app:
    then only its Webcam version is made again, with the same cut."""
    t0 = time.time()
    previous = state.get(key, {})
    duration, has_audio = probe(clip, cfg)
    tags = load_tags(clip)
    # a re-cut or re-render keeps the clip's game (shorts from before game support are all League)
    game = previous.get("game", "league") if previous.get("done") else game_of(tags, cfg)
    webcam = previous.get("webcam") if previous.get("done") else current_webcam(cfg)   # likewise where the webcam was
    recut, styles = bool(override), None
    if layout is not None:
        if not (previous.get("done") and webcam):
            raise ValueError("This short has no Webcam version to change.")
        layout, styles = cam_layout(layout), ["cam"]
        if previous.get("start") is not None and previous.get("end") is not None:
            override = {"start": previous["start"], "end": previous["end"]}
    else:   # and its webcam layout (shorts from before layouts have the webcam on top)
        layout = cam_layout(previous.get("cam_layout")) if previous.get("done") else cfg["cam_layout"]
    start, length, overlays = plan(duration, tags, cfg, override)
    day = previous.get("dir") or datetime.now().strftime("%Y-%m-%d")
    out_dir = output_dir(cfg) / day
    status = {"key": key, "styles": styles or styles_for(cfg, webcam), "started": time.time(), "trim": recut}
    write_json(STATUS, status)

    def note(text):   # shown in the app while this clip is being made
        write_json(STATUS, dict(status, note=text, started=time.time()))

    def make(layout):
        subs, subs_note = make_subtitles(clip, key, start, length, has_audio, cfg,
                                         layouts.frame(webcam, layout) if webcam else None, note)
        if subs:
            note("Rendering with subtitles")
        try:
            names = render(clip, out_dir, clip.stem, start, length, has_audio, overlays, cfg, webcam, subs, layout,
                           styles)
        finally:
            for f in subs.values():
                (HOME / f).unlink(missing_ok=True)
        return names, bool(subs), subs_note

    try:
        outputs, subtitled, subs_note = make(layout)
    except subprocess.CalledProcessError as e:
        # a layout must never cost you a short: if one ever fails, make the short with the webcam on top instead
        if not webcam or layout["layout"] == "top" or "cam" not in status["styles"]:
            raise
        reason = ((e.stderr or "").strip().splitlines() or ["ffmpeg failed"])[-1][:200]
        log(f"LAYOUT {key}: the {layout['layout']} webcam layout didn't render ({reason}); putting the webcam on top")
        layout = cam_layout(None)
        outputs, subtitled, subs_note = make(layout)
    if styles:   # only the Webcam version was made again; the others stay as they were
        outputs = previous.get("outputs", []) + [o for o in outputs if o not in previous.get("outputs", [])]
    for stale in set(previous.get("outputs", [])) - set(outputs):   # a style was switched off since last time
        if (out_dir / stale).exists():
            (TOSSED / day).mkdir(parents=True, exist_ok=True)
            shutil.move(str(out_dir / stale), str(TOSSED / day / stale))
    (out_dir / f"{clip.stem}_caption.txt").write_text(caption_for(tags, clip.stem, game), encoding="utf-8")
    entry = state[key] = {
        "done": datetime.now().isoformat(timespec="seconds"), "outputs": outputs, "tagged": bool(tags), "game": game,
        "webcam": webcam, "cam_layout": layout if webcam else None,
        "dir": day, "length": round(length, 1), "start": round(start, 2), "end": round(start + length, 2),
        "src_duration": round(duration, 2), "labels": [lbl for lbl, _ in overlays],
        "title": (tags or {}).get("title", ""), "champion": (tags or {}).get("champion", ""),
        "trimmed": recut or previous.get("trimmed", False), "subtitled": subtitled,
        "subtitles_note": subs_note,
    }
    what = " + ".join(lbl for lbl, _ in overlays) or "untagged"
    made = ", ".join(outputs) if not styles else f"{', '.join(styles)} only, {layout['layout']} layout"
    log(f"OK   {key} ({what}, {length:.0f}s of {duration:.0f}s) -> {made} in {time.time() - t0:.0f}s")
    return entry


def reel_game(entries):
    """The game every short in a reel is from, or None for a mixed reel (which gets no game hashtags)."""
    games = {e.get("game", "league") for e in entries}   # shorts from before game support are all League
    return games.pop() if len(games) == 1 else None


def make_reel(req, state, cfg):
    out_root = output_dir(cfg)
    style = req.get("style") if req.get("style") in cfg["styles"] else cfg["styles"][0]
    picks = []
    for key in req.get("keys", []):
        entry = state.get(key, {})
        name = next((o for o in entry.get("outputs", []) if o.endswith(f"_{style}.mp4")), None)
        if name and (out_root / entry["dir"] / name).exists():
            picks.append((entry.get("done", ""), key, entry, out_root / entry["dir"] / name))
    if len(picks) < 2:
        raise ValueError("A reel needs at least two shorts that have this version.")
    picks.sort(key=lambda p: p[0])
    stamp = datetime.now()
    stem = f"Reel {stamp:%Y-%m-%d %H-%M-%S}"
    WORK.mkdir(parents=True, exist_ok=True)
    listing = WORK / f"{stem}.txt"
    listing.write_text("".join("file '" + str(p[3]).replace("'", "'\\''") + "'\n" for p in picks), encoding="utf-8")
    tmp = WORK / f"{stem}_{style}.mp4"
    base = [str(tool("ffmpeg", cfg)), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing)]
    try:
        run(base + ["-c", "copy", "-movflags", "+faststart", str(tmp)])
    except subprocess.CalledProcessError:   # mismatched streams: re-encode instead of copying
        run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg["crf"]), "-r", "60",
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(tmp)])
    listing.unlink(missing_ok=True)
    day = stamp.strftime("%Y-%m-%d")
    (out_root / day).mkdir(parents=True, exist_ok=True)
    move_in(tmp, out_root / day / tmp.name)
    labels = [lbl for p in picks for lbl in p[2].get("labels", [])]
    title = f"Highlight reel: {len(picks)} plays"
    game = reel_game([p[2] for p in picks])
    hashtags = GAMES[game]["hashtags"] + " " if game in GAMES else ""
    (out_root / day / f"{stem}_caption.txt").write_text(
        f"{title}\n\n{hashtags}#gaming #highlights #shorts\n", encoding="utf-8")
    key = f"reel:{req['id']}"
    state[key] = {"done": stamp.isoformat(timespec="seconds"), "outputs": [tmp.name], "tagged": False, "reel": True,
                  "game": game,
                  "dir": day, "length": round(sum(p[2].get("length") or 0 for p in picks), 1), "labels": labels,
                  "title": title, "champion": "", "sources": [p[1] for p in picks]}
    log(f"REEL {key}: {len(picks)} shorts ({style}) -> {tmp.name}")
    return f"Reel ready: {len(picks)} plays."


def handle_requests(state):
    done_dir = REQUESTS / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(REQUESTS.glob("*.json"), key=lambda p: p.stat().st_mtime):
        req = read_json(path, None)
        path.unlink(missing_ok=True)
        if not isinstance(req, dict):
            continue
        req.setdefault("id", uuid.uuid4().hex[:8])
        cfg = load_config()
        ok = True
        try:
            if req.get("type") == "rerender":
                key = req["key"]
                clip = watch_dir(cfg) / key
                if key not in state or not clip.exists():
                    raise ValueError("The raw clip is gone, so this short can't be re-cut.")
                override = {"start": req["start"], "end": req["end"]} if "start" in req and "end" in req else None
                process_clip(clip, key, state, cfg, override)
                message = "Re-cut done." if override else "Re-rendered with current settings."
            elif req.get("type") == "layout":
                key = req["key"]
                clip = watch_dir(cfg) / key
                if key not in state or not clip.exists():
                    raise ValueError("The raw clip is gone, so this short's webcam layout can't change.")
                wanted = cam_layout(req.get("cam_layout"))
                ok = process_clip(clip, key, state, cfg, layout=wanted)["cam_layout"] == wanted
                message = ("Webcam layout changed." if ok else
                           "That layout didn't work for this clip, so its webcam went on top instead.")
            elif req.get("type") == "reel":
                message = make_reel(req, state, cfg)
            else:
                raise ValueError(f"Unknown job type: {req.get('type')}")
        except Exception as e:  # report to the app; keep the engine alive
            ok, message = False, (getattr(e, "stderr", "") or str(e)).strip()[:300]
            log(f"JOB  {req.get('type')} failed: {message}")
        finally:
            STATUS.unlink(missing_ok=True)
        write_json(STATE, state)
        write_json(done_dir / f"{req['id']}.json",
                   {"id": req["id"], "type": req.get("type"), "ok": ok, "message": message,
                    "time": datetime.now().isoformat(timespec="seconds")})
    for old in sorted(done_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-20]:
        old.unlink(missing_ok=True)


def recycle_old_tossed(cfg):
    cutoff = time.time() - cfg["recycle_tossed_after_days"] * 86400
    for f in TOSSED.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff and recycle(f):
            log(f"RECYCLED {f.relative_to(TOSSED)}")
    for d in TOSSED.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def backfill(state, cfg):
    """Older entries predate start/end tracking; fill them in so the app's trim editor works."""
    changed = False
    for key, entry in state.items():
        if "src_duration" in entry or "failed" in entry or entry.get("reel"):
            continue
        clip = watch_dir(cfg) / key
        if clip.exists():
            duration, _ = probe(clip, cfg)
            start, length, _ = plan(duration, load_tags(clip), cfg)
            entry.update(src_duration=round(duration, 2), start=round(start, 2), end=round(start + length, 2))
            changed = True
    if changed:
        write_json(STATE, state)


def candidates(root):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith((".syncthing", "~")):
            yield p


def listen_for_saves():
    """Plays the chosen sound each time OBS on this PC saves a replay. Runs in a thread for as long as the engine."""
    while True:
        sound = load_config()["save_sound"]
        if sound == "off":
            time.sleep(5)
            continue
        try:
            with obsws.Obs(events=obsws.OUTPUT_EVENTS) as obs:
                while load_config()["save_sound"] != "off":
                    event = obs.next_event(timeout=10)
                    if event and event.get("eventType") == "ReplayBufferSaved":
                        sounds.play(load_config()["save_sound"])
        except Exception:  # OBS closed or not started yet (or answered oddly); try again
            time.sleep(10)


def main():
    cfg = load_config()
    problem = check_folders(cfg)
    if problem:
        log("SETUP " + problem)
        raise SystemExit(problem)
    for d in (watch_dir(cfg), output_dir(cfg), FAILED, TOSSED, LOGS, WORK, REQUESTS):
        d.mkdir(parents=True, exist_ok=True)
    state = read_json(STATE, {})
    STATUS.unlink(missing_ok=True)
    backfill(state, cfg)
    threading.Thread(target=listen_for_saves, daemon=True).start()
    sizes, stable = {}, {}   # size seen last poll / when it became stable (gives the tag file time to arrive)
    last_cleanup = 0.0
    log(f"ClipLab engine watching {watch_dir(cfg)} -> {output_dir(cfg)}")
    while True:
        HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
        handle_requests(state)
        cfg = load_config()
        root = watch_dir(cfg)
        for clip in candidates(root):
            key = str(clip.relative_to(root))
            if key in state:
                continue
            size = clip.stat().st_size
            if sizes.get(key) != size:
                sizes[key] = size
                stable.pop(key, None)
                continue
            if not load_tags(clip) and time.time() - stable.setdefault(key, time.time()) < cfg["tag_wait"]:
                continue
            sizes.pop(key, None)
            stable.pop(key, None)
            try:
                process_clip(clip, key, state, cfg)
            except Exception as e:  # note the failure and move on
                detail = getattr(e, "stderr", "") or str(e)
                state[key] = {"failed": datetime.now().isoformat(timespec="seconds")}
                FAILED.mkdir(parents=True, exist_ok=True)
                (FAILED / f"{clip.stem}.txt").write_text(str(detail), encoding="utf-8")
                log(f"FAIL {key}: {str(detail).strip()[:200]}")
            finally:
                STATUS.unlink(missing_ok=True)
            write_json(STATE, state)
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            handle_requests(state)   # let app jobs cut in between clips of a big batch
        if time.time() - last_cleanup > 3600:
            recycle_old_tossed(cfg)
            last_cleanup = time.time()
        time.sleep(POLL_SECONDS)
