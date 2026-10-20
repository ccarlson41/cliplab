"""Burned-in subtitles: what was said in the clip, a few words at a time, the way shorts show them.

Speech-to-text is ffmpeg's own whisper filter (whisper.cpp, in ffmpeg 8+ "full" builds), so there is
nothing else to install. The speech model is downloaded into HOME\\models the first time it's needed.
The engine treats every failure here as "no subtitles this time", never as a failed short.
"""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

from .settings import HOME, NO_WINDOW, RENDER_FLAGS, SUBTITLE_POSITIONS, SUBTITLE_STYLES, WORK, tool

# file name, size in MB, SHA-256 (from Hugging Face's LFS pointers at the pinned revision below)
MODELS = {"on": ("ggml-base.bin", 148, "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
          "accurate": ("ggml-small.bin", 488, "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b")}
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/{}"
NO_WHISPER = ("This PC's ffmpeg can't do speech-to-text, so shorts are made without subtitles. "
              "Run ClipLab's setup again to update it.")
MODELS_DIR = HOME / "models"
LEAD = 0.25   # whisper's word times run a little late; showing words just early reads naturally

# ASS colours are &HAABBGGRR (alpha 00 = solid). The app's preview copies these looks in CSS.
PRESETS = {
    "bold": dict(font="Arial Black", size=84, colour="&H00FFFFFF", line="&H00000000", back="&H00000000",
                 border=1, outline=7, shadow=0, upper=True),
    "pop": dict(font="Arial Black", size=90, colour="&H0000E1FF", line="&H00000000", back="&H00000000",
                border=1, outline=7, shadow=0, upper=True, pop=True),
    "boxed": dict(font="Arial", size=66, colour="&H00FFFFFF", line="&H60000000", back="&H60000000",
                  border=3, outline=14, shadow=0, upper=False),
    "clean": dict(font="Segoe UI Semibold", size=72, colour="&H00FFFFFF", line="&H50000000", back="&H70000000",
                  border=1, outline=3, shadow=3, upper=False),
}
assert tuple(PRESETS) == SUBTITLE_STYLES and SUBTITLE_POSITIONS == ("lower", "center", "upper")

_whisper_builds = {}


class SubtitleError(Exception):
    """Why a short was made without subtitles, worded for the app."""


def ffmpeg_has_whisper(cfg):
    """Whether this ffmpeg can transcribe (gyan.dev "full" builds from 8.0 on can). Checked once per ffmpeg."""
    exe = str(tool("ffmpeg", cfg))
    if exe not in _whisper_builds:
        try:
            out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30,
                                 creationflags=NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _whisper_builds[exe] = bool(re.search(r"\bwhisper\s+A->A", out))
    return _whisper_builds[exe]


def model_path(level, progress=None):
    """The speech model for "on" or "accurate", downloading it the first time (to a .part file, checked against
    its SHA-256, then renamed). whisper.cpp's native code reads this file, so a damaged one is never kept."""
    name, mb, sha256 = MODELS[level]
    path = MODELS_DIR / name
    if path.exists() and path.stat().st_size > mb * 0.9e6:
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    part = path.with_name(name + ".part")
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(MODEL_URL.format(name), timeout=30) as r, open(part, "wb") as f:
            total, done, shown = int(r.headers.get("Content-Length") or mb * 1e6), 0, 0.0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and time.time() - shown > 1:
                    progress(done / total)
                    shown = time.time()
        if part.stat().st_size < mb * 0.9e6:
            raise OSError(f"the speech model download stopped early ({part.stat().st_size // 1_000_000} MB)")
        if digest.hexdigest() != sha256:
            raise OSError("the speech model that downloaded isn't the right file")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


def parse_words(lines):
    """whisper's JSON lines ({"start": ms, "end": ms, "text": ...}, one word each) -> [(start s, end s, word)]."""
    words = []
    for line in lines:
        try:
            item = json.loads(line)
            text = str(item.get("text", "")).strip()
            start, end = float(item["start"]) / 1000, float(item["end"]) / 1000
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if not text or re.fullmatch(r"[\[(].*[\])]|[♪♫\s]+", text):   # [BLANK_AUDIO], (music), ♪
            continue
        words.append((start, max(end, start), text))
    return words


def transcribe(src, start, length, model, cfg):
    """The words spoken in src between start and start+length, timed from the start of the short."""
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"words-{uuid.uuid4().hex[:8]}.json"
    rel = lambda p: os.path.relpath(p, HOME).replace("\\", "/")   # noqa: E731  (no drive colons to escape)
    whisper = f"whisper=model={rel(model)}:language=auto:queue=30:max_len=1:destination={rel(out)}:format=json"
    try:
        subprocess.run([str(tool("ffmpeg", cfg)), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}",
                        "-t", f"{length:.2f}", "-i", str(src), "-vn", "-af", whisper, "-f", "null", "-"],
                       cwd=HOME, capture_output=True, text=True, check=True, creationflags=RENDER_FLAGS,
                       timeout=max(180, length * 8))
        return parse_words(out.read_text(encoding="utf-8").splitlines() if out.exists() else [])
    finally:
        out.unlink(missing_ok=True)


def tidy(word, upper):
    word = word.strip(".,;:") or word   # captions drop trailing commas and full stops, keep ?! and inner ones
    return word.upper() if upper else word


def chunk(words, upper=True, max_words=3, max_chars=16, max_gap=0.6):
    """Group words into short captions: up to 3 words / 16 letters, breaking at pauses and sentence ends.
    Returns [(start, end, text)] that never overlap and each stay up at least a beat."""
    groups, cur = [], []
    for w in words:
        text = " ".join(x[2] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > max_chars or w[0] - cur[-1][1] > max_gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w[2][-1:] in ".!?,":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = [[max(0.0, g[0][0] - LEAD), max(g[-1][1] - LEAD, g[0][0] - LEAD + 0.45), " ".join(tidy(w[2], upper) for w in g)]
           for g in groups]
    for a, b in zip(out, out[1:]):
        a[1] = max(min(a[1], b[0]), a[0] + 0.15)
        b[0] = max(b[0], a[1])
        b[1] = max(b[1], b[0] + 0.15)
    return [(round(s, 2), round(e, 2), t) for s, e, t in out if e > s]


def anchor(position, cam_top=None):
    """(ASS \\an alignment, y) in the 1080x1920 frame, clear of the play labels near the top and the
    watermark near the bottom. In Webcam on top (cam_top = the webcam strip's height) they stay over the play."""
    if position == "upper":
        return 8, (cam_top + 170) if cam_top else 420
    if position == "center":
        return 5, (cam_top or 0) + (1920 - (cam_top or 0)) // 2
    return 2, 1450


def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_text(text):
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(path, chunks, style, position, cam_top=None):
    p = PRESETS[style]
    an, y = anchor(position, cam_top)
    pop = r"\fscx70\fscy70\t(0,110,\fscx108\fscy108)\t(110,170,\fscx100\fscy100)" if p.get("pop") else ""
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,{p['font']},{p['size']},{p['colour']},&H000000FF,{p['line']},{p['back']},-1,0,0,0,100,100,1,0,"
        f"{p['border']},{p['outline']},{p['shadow']},{an},70,70,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines += [f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Sub,,0,0,0,,{{\\an{an}\\pos(540,{y}){pop}}}{ass_text(t)}"
              for s, e, t in chunks]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return os.path.relpath(path, HOME).replace("\\", "/")


def prepare(src, start, length, cfg, cam_top=None, status=None):
    """Make this short's subtitle files: {"normal": path, "cam": path} relative to HOME (render runs there),
    or {} when nothing was said. Raises SubtitleError (or anything else) on a problem; the engine then renders
    without subtitles and keeps the reason for the app."""
    if not ffmpeg_has_whisper(cfg):
        raise SubtitleError(NO_WHISPER)
    try:
        model = model_path(cfg["subtitles"],
                           progress=status and (lambda f: status(f"Downloading the speech model… {f:.0%}")))
    except OSError as e:
        raise SubtitleError(f"Couldn't download the speech model ({e}). ClipLab tries again with the next clip.") from e
    if status:
        status("Writing subtitles")
    chunks = chunk(transcribe(src, start, length, model, cfg), upper=PRESETS[cfg["subtitle_style"]]["upper"])
    if not chunks:
        return {}
    tag = uuid.uuid4().hex[:8]
    files = {"normal": write_ass(WORK / f"subs-{tag}.ass", chunks, cfg["subtitle_style"], cfg["subtitle_position"])}
    if cam_top:
        files["cam"] = write_ass(WORK / f"subs-{tag}-cam.ass", chunks, cfg["subtitle_style"], cfg["subtitle_position"],
                                 cam_top)
    return files
