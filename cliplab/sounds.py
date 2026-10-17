"""The little sounds ClipLab can play when OBS saves a replay. Made on the fly, so there are no audio files to ship."""
import io
import math
import struct
import wave

from .settings import HOME

RATE = 44100
TONES = {   # (frequency Hz, seconds) played one after another
    "chime": [(880, 0.09), (1318.5, 0.16)],
    "blip": [(1250, 0.07)],
    "pop": [(520, 0.035), (780, 0.05)],
}


def wav_bytes(name, volume=0.35):
    frames = bytearray()
    for freq, secs in TONES[name]:
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
