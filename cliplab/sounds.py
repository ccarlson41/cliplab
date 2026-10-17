"""ClipLab's little sounds: the replay-saved blips, and the whoosh at the start of a short. Made on the fly,
so there are no audio files to ship."""
import io
import math
import random
import struct
import wave

from .settings import HOME

RATE = 44100
TONES = {   # (frequency Hz, seconds) played one after another
    "chime": [(880, 0.09), (1318.5, 0.16)],
    "blip": [(1250, 0.07)],
    "pop": [(520, 0.035), (780, 0.05)],
}
NAMES = tuple(TONES) + ("whoosh",)   # everything wav_bytes can make


def whoosh_samples(secs=0.38, low=250.0, high=5200.0, seed=7):
    """Noise through a band-pass that sweeps up, swelling in and out: the swish when a short starts."""
    rng = random.Random(seed)   # the same whoosh every time
    n = int(RATE * secs)
    lp = bp = 0.0
    out = []
    for i in range(n):
        x = i / n
        f = low * (high / low) ** x
        k = 2 * math.sin(math.pi * f / RATE)                 # state-variable filter coefficient
        hp = rng.uniform(-1, 1) - lp - 0.22 * bp           # 0.22: a narrow band, so the sweep is heard
        bp += k * hp
        lp += k * bp
        out.append(bp * math.sin(math.pi * min(1.0, x * 1.4)) ** 2)   # quick swell, longer tail
    peak = max(abs(v) for v in out) or 1.0
    return [v / peak for v in out]


def wav_bytes(name, volume=0.35):
    frames = bytearray()
    if name == "whoosh":
        for v in whoosh_samples():
            frames += struct.pack("<h", int(32767 * 0.7 * v))
    for freq, secs in TONES.get(name, []):
        n = int(RATE * secs)
        for i in range(n):
            fade = min(1.0, i / (RATE * 0.004), (n - i) / (RATE * 0.03))   # no clicks at either end
            frames += struct.pack("<h", int(32767 * volume * fade * math.sin(2 * math.pi * freq * i / RATE)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return out.getvalue()


def whoosh_file():
    """The whoosh as a WAV in HOME/sounds, for ffmpeg to mix in."""
    path = HOME / "sounds" / "whoosh.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("whoosh.wav.tmp")
        tmp.write_bytes(wav_bytes("whoosh"))
        tmp.replace(path)
    return path


def play(name):
    """Play a sound without waiting for it. Quietly does nothing for "off", an unknown name, or off Windows."""
    if name not in TONES:
        return
    try:
        import winsound
    except ImportError:
        return
    path = HOME / "sounds" / f"{name}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(name))
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
