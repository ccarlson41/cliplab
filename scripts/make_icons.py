"""Draw the app's PNG icons from cliplab/web/icon.svg. Run it again after changing the icon:

    python scripts/make_icons.py

Phones and "Install app" want PNGs: 192 and 512 px for the manifest, a "maskable" 512 (full-bleed, the
drawing inside the middle 80% that Android may crop to a circle) and a 180 px apple-touch-icon for iPhones
(square; iOS rounds the corners itself). Microsoft Edge (or Chrome) draws them in headless mode.
"""
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "cliplab" / "web"
SVG_NS = "http://www.w3.org/2000/svg"
BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe")
# file name, size in px, None = icon.svg as drawn, else how much of the full-bleed square the drawing fills
ICONS = (("icon-192.png", 192, None), ("icon-512.png", 512, None),
         ("icon-maskable-512.png", 512, 0.72), ("apple-touch-icon.png", 180, 0.86))


def svg_for(scale):
    """icon.svg as is, or its drawing scaled onto a full-bleed square of its background color."""
    if scale is None:
        return (WEB / "icon.svg").read_text(encoding="utf-8")
    ET.register_namespace("", SVG_NS)
    root = ET.fromstring((WEB / "icon.svg").read_text(encoding="utf-8"))
    background = root[0]   # the rounded square everything sits on
    root.remove(background)
    size = float(root.get("viewBox").split()[2])
    group = ET.Element(f"{{{SVG_NS}}}g", transform=f"translate({size / 2} {size / 2}) scale({scale}) "
                                                   f"translate({-size / 2} {-size / 2})")
    group.extend(list(root))
    for child in list(root):
        root.remove(child)
    root.append(ET.Element(f"{{{SVG_NS}}}rect", width=str(size), height=str(size), fill=background.get("fill")))
    root.append(group)
    return ET.tostring(root, encoding="unicode")


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def main():
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("msedge") or shutil.which("chrome")
    if not browser:
        sys.exit("Needs Microsoft Edge or Google Chrome to draw the icons.")
    tmp = Path(tempfile.mkdtemp(prefix="cliplab-icons-"))
    try:
        for name, px, scale in ICONS:
            svg = svg_for(scale).replace("<svg ", f'<svg width="{px}" height="{px}" ', 1)
            page = tmp / (name + ".html")
            page.write_text(f'<!doctype html><html><body style="margin:0;overflow:hidden">{svg}</body></html>',
                            encoding="utf-8")
            out = WEB / name
            out.unlink(missing_ok=True)
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                            f"--user-data-dir={tmp / 'profile'}", "--force-device-scale-factor=1",
                            "--default-background-color=00000000", f"--window-size={px},{px}",
                            f"--screenshot={out}", page.as_uri()], capture_output=True, timeout=60)
            last = -1
            for _ in range(100):   # Edge's launcher can return before the screenshot is written
                size = out.stat().st_size if out.exists() else -1
                if size > 0 and size == last:
                    break
                last = size
                time.sleep(0.3)
            if not out.exists() or png_size(out) != (px, px):
                sys.exit(f"{name} didn't come out {px}x{px}.")
            print(f"{name}  {px}x{px}  {out.stat().st_size // 1024 + 1} KB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
