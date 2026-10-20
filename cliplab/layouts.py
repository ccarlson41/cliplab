"""Webcam layouts: where the webcam, the play and the text go in the 1080x1920 frame of a short's Webcam version.

  top     the webcam as a strip across the top, the play below (the default, and how every short was made before)
  bottom  the play on top, the webcam as a strip under it
  split   half and half: the webcam on the top half, the play on the bottom half
  corner  the play fills the frame and the webcam sits in a rounded box in a corner: small, medium or large

Play labels, subtitles and the watermark always go where the webcam isn't. The engine builds the ffmpeg graph
from frame(), the subtitles take their spots from it, and the app's little previews come from preview().
"""
from .settings import CAM_LAYOUTS, CAM_SIZES, WEBCAM_CORNERS, cam_layout

W, H = 1080, 1920
BOX_WIDTH = {"small": 360, "medium": 460, "large": 580}   # the corner box; its height follows the webcam's shape
EDGE = 40           # the corner box's gap from the side of the frame
BOX_TOP = 150       # top corners stay under the bar TikTok, Shorts and Reels draw across the top
BOX_BOTTOM = 1590   # bottom corners stay above the caption and buttons they draw over the bottom
BORDER, RADIUS = 6, 30   # the corner box's white edge and rounded corners
# where text goes when nothing is in the way: the play label's top, the watermark's top, and the subtitles
# by position as (ASS \an alignment, y): 8 hangs below y, 5 centers on it, 2 sits on it
LABEL_Y, WATERMARK_Y = 250, 1690
SUBS = {"upper": (8, 420), "center": (5, 960), "lower": (2, 1450)}


def even(v):
    return int(round(v / 2)) * 2


def aspect(rect):
    """The webcam's width / height. The rect is a fraction of OBS's canvas, taken to be 16:9."""
    return (rect[2] * 16) / (rect[3] * 9)


def cam_height(rect):
    """How tall the webcam strip is in the top and bottom layouts: the webcam's own shape, within limits,
    so a tall webcam still leaves the play room."""
    return even(min(840, max(480, W / aspect(rect))))


def frame(rect, layout=None):
    """Where everything goes for this webcam (rect) in this layout:
    {"layout", "cam": (x, y, w, h) of the webcam, "game": (x, y, w, h) of the play, "label_y", "watermark_y",
     "subs": {position: (an, y)}}. Anything odd in layout means the webcam on top."""
    layout = cam_layout(layout)
    kind = layout["layout"]
    if kind == "corner":
        w = BOX_WIDTH[layout["size"]]
        h = even(w / min(16 / 9, max(1.0, aspect(rect))))   # 16:9 to square; very wide or tall webcams are cropped
        x = EDGE if layout["corner"].endswith("left") else W - EDGE - w
        if layout["corner"].startswith("top"):
            y = BOX_TOP
            under = y + h   # text moves below the box
            label, mark = under + 50, WATERMARK_Y
            subs = dict(SUBS, upper=(8, under + 170), center=(5, max(960, under + 110)))
        else:
            y = BOX_BOTTOM - h
            label, mark = LABEL_Y, WATERMARK_Y   # the watermark stays below the box
            subs = dict(SUBS, center=(5, min(960, y - 110)), lower=(2, min(1450, y - 50)))
        return {"layout": kind, "cam": (x, y, w, h), "game": (0, 0, W, H), "label_y": label, "watermark_y": mark,
                "subs": subs}
    strip = 960 if kind == "split" else cam_height(rect)
    if kind == "bottom":
        top = H - strip   # the play above the strip, its text above the webcam
        return {"layout": kind, "cam": (0, top, W, strip), "game": (0, 0, W, top), "label_y": LABEL_Y,
                "watermark_y": top - 80, "subs": dict(SUBS, center=(5, top // 2), lower=(2, top - 150))}
    return {"layout": kind, "cam": (0, 0, W, strip), "game": (0, strip, W, H - strip), "label_y": strip + 50,
            "watermark_y": WATERMARK_Y,
            "subs": dict(SUBS, upper=(8, strip + 170), center=(5, strip + (H - strip) // 2))}


def same(a, b):
    """Whether two layouts look the same (a corner and size only matter in the corner layout)."""
    a, b = cam_layout(a), cam_layout(b)
    return a["layout"] == b["layout"] and (a["layout"] != "corner" or a == b)


def preview(rect):
    """Every layout as fractions of the frame, for the app's little previews: the webcam's box [x, y, w, h], the play
    label's top, and the subtitles' [an, y] by position. Corner layouts are under ["corner"][corner][size]."""
    def one(layout):
        f = frame(rect, layout)
        return {"cam": [round(f["cam"][0] / W, 4), round(f["cam"][1] / H, 4), round(f["cam"][2] / W, 4),
                        round(f["cam"][3] / H, 4)],
                "label": round(f["label_y"] / H, 4),
                "subs": {p: [an, round(y / H, 4)] for p, (an, y) in f["subs"].items()}}
    out = {kind: one({"layout": kind}) for kind in CAM_LAYOUTS if kind != "corner"}
    out["corner"] = {c: {s: one({"layout": "corner", "corner": c, "size": s}) for s in CAM_SIZES}
                     for c in WEBCAM_CORNERS}
    return out
