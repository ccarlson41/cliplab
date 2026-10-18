"""Tests for webcam layouts: where the Webcam version puts your webcam, and that labels, subtitles and the
watermark stay off it. Run from the repo root:  python -m unittest -v

They use a throwaway ClipLab home, never your real one. The render tests need ffmpeg: on PATH, or in the
folder named by CLIPLAB_TEST_FFMPEG.
"""
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ["CLIPLAB_HOME"] = tempfile.mkdtemp(prefix="cliplab-test-")   # never your real ClipLab home

from cliplab import engine, layouts, server, subtitles  # noqa: E402
from cliplab.settings import (CAM_LAYOUTS, CAM_SIZES, CONFIG, DEFAULTS, HOME, NO_WINDOW, REQUESTS, STATE,  # noqa: E402
                              WEBCAM_CORNERS, clean_config, load_config, write_json)

FF_DIR = os.environ.get("CLIPLAB_TEST_FFMPEG") or next(
    (str(p) for p in (Path.home() / "ClipLab" / "bin",) if (p / "ffmpeg.exe").exists()), "")
HAVE_FFMPEG = bool(FF_DIR or shutil.which("ffmpeg"))
CFG = json.loads(json.dumps(DEFAULTS))
WIDE = [0, 0.75, 0.25, 0.25]   # a 16:9 webcam in OBS's bottom-left corner
SHAPES = {"16:9": WIDE, "4:3": [0, 0, 0.15, 0.2], "tall": [0.5, 0.1, 0.2, 0.4], "very wide": [0, 0, 1, 0.1],
          "tiny": [0.98, 0.98, 0.02, 0.02]}
EVERY_LAYOUT = [{"layout": k} for k in CAM_LAYOUTS if k != "corner"] + [
    {"layout": "corner", "corner": c, "size": s} for c in WEBCAM_CORNERS for s in CAM_SIZES]
LABEL_H, SUB_H, MARK_H = 110, 200, 50   # room a play label, two lines of subtitles and the watermark take up


def band(an, y):
    """The rows a subtitle anchored at (an, y) covers, with room for two lines."""
    return {8: (y, y + SUB_H), 5: (y - SUB_H // 2, y + SUB_H // 2), 2: (y - SUB_H, y)}[an]


def apart(a, b):
    return a[1] <= b[0] or b[1] <= a[0]


class GeometryTests(unittest.TestCase):
    def test_top_is_the_webcam_on_top_from_before(self):
        f = layouts.frame(WIDE)
        self.assertEqual((f["layout"], f["cam"], f["game"]), ("top", (0, 0, 1080, 608), (0, 608, 1080, 1312)))
        self.assertEqual((f["label_y"], f["watermark_y"]), (658, 1690))   # the label just under the webcam
        self.assertEqual(f["subs"], {"upper": (8, 778), "center": (5, 1264), "lower": (2, 1450)})
        self.assertEqual(engine.cam_height(WIDE), 608)

    def test_each_layout_puts_the_webcam_where_it_says(self):
        self.assertEqual(layouts.frame(WIDE, {"layout": "bottom"})["cam"], (0, 1312, 1080, 608))
        self.assertEqual(layouts.frame(WIDE, {"layout": "split"})["cam"], (0, 0, 1080, 960))
        self.assertEqual(layouts.frame(WIDE, {"layout": "split"})["game"], (0, 960, 1080, 960))
        box = {"layout": "corner", "corner": "top-left", "size": "medium"}
        self.assertEqual(layouts.frame(WIDE, box)["cam"], (40, 150, 460, 258))
        self.assertEqual(layouts.frame(WIDE, dict(box, corner="bottom-right", size="large"))["cam"],
                         (1080 - 40 - 580, 1590 - 326, 580, 326))
        self.assertEqual(layouts.frame(WIDE, box)["game"], (0, 0, 1080, 1920))   # the play fills the frame

    def test_text_moves_out_of_the_way(self):
        bottom = layouts.frame(WIDE, {"layout": "bottom"})
        self.assertEqual(bottom["label_y"], 250)             # labels go back to the top
        self.assertEqual(bottom["subs"]["lower"], (2, 1162))  # subtitles sit above the strip, the watermark under them
        self.assertEqual(bottom["watermark_y"], 1232)
        top_box = layouts.frame(WIDE, {"layout": "corner", "corner": "top-right", "size": "small"})
        self.assertEqual(top_box["label_y"], 150 + 202 + 50)  # under the box
        low_box = layouts.frame(WIDE, {"layout": "corner", "corner": "bottom-left", "size": "small"})
        self.assertEqual((low_box["label_y"], low_box["watermark_y"]), (250, 1690))
        self.assertEqual(low_box["subs"]["lower"], (2, 1590 - 202 - 50))

    def test_every_layout_and_webcam_shape_fits_and_keeps_text_off_the_webcam(self):
        for shape, rect in SHAPES.items():
            for layout in EVERY_LAYOUT:
                with self.subTest(shape=shape, **layout):
                    f = layouts.frame(rect, layout)
                    x, y, w, h = f["cam"]
                    gx, gy, gw, gh = f["game"]
                    for v in (x, y, w, h, gx, gy, gw, gh):
                        self.assertIsInstance(v, int)
                    for v in (w, h, gw, gh):
                        self.assertEqual(v % 2, 0)   # yuv420p needs even sizes
                    self.assertTrue(0 <= x and x + w <= 1080 and 0 <= y and y + h <= 1920 and w >= 300 and h >= 200)
                    self.assertTrue(gw == 1080 and gy >= 0 and gy + gh <= 1920 and gh >= 960)
                    webcam = (y, y + h)
                    label = (f["label_y"], f["label_y"] + LABEL_H)
                    mark = (f["watermark_y"], f["watermark_y"] + MARK_H)
                    self.assertTrue(apart(label, webcam), "label")
                    self.assertTrue(apart(mark, webcam), "watermark")
                    self.assertTrue(0 <= label[0] and mark[1] <= 1920)
                    for position, (an, sy) in f["subs"].items():
                        sub = band(an, sy)
                        self.assertTrue(apart(sub, webcam), position)
                        self.assertTrue(0 <= sub[0] and sub[1] <= 1920, position)
                    self.assertLessEqual(label[1], f["subs"]["upper"][1])        # upper subtitles go under the label
                    self.assertLessEqual(f["subs"]["lower"][1], f["watermark_y"])  # lower ones above the watermark

    def test_odd_layouts_mean_the_webcam_on_top(self):
        on_top = layouts.frame(WIDE)
        for odd in (None, "corner", {"layout": "diagonal"}, {"layout": ["top"]}, {}):
            self.assertEqual(layouts.frame(WIDE, odd), on_top, odd)
        box = layouts.frame(WIDE, {"layout": "corner", "corner": "middle", "size": "huge"})
        self.assertEqual(box["cam"], (40, 150, 460, 258))   # the default corner and size

    def test_same_ignores_the_corner_unless_its_the_corner_layout(self):
        self.assertTrue(layouts.same({"layout": "top", "corner": "top-left"}, {"layout": "top", "corner": "bottom-right"}))
        self.assertTrue(layouts.same(None, {"layout": "top"}))
        self.assertFalse(layouts.same({"layout": "corner", "size": "small"}, {"layout": "corner", "size": "large"}))
        self.assertFalse(layouts.same({"layout": "bottom"}, {"layout": "split"}))

    def test_previews_match_the_frames(self):
        p = layouts.preview(WIDE)
        self.assertEqual(set(p), set(CAM_LAYOUTS))
        self.assertEqual(p["top"]["cam"], [0, 0, 1, round(608 / 1920, 4)])
        self.assertEqual(p["bottom"]["subs"]["lower"], [2, round(1162 / 1920, 4)])
        box = p["corner"]["bottom-right"]["large"]["cam"]
        self.assertEqual(box, [round(460 / 1080, 4), round(1264 / 1920, 4), round(580 / 1080, 4), round(326 / 1920, 4)])
        self.assertEqual(len(json.dumps(p)) < 4000, True)   # sent with every poll of the app's state


class SubtitleSpotTests(unittest.TestCase):
    def test_the_cam_subtitles_follow_the_layout(self):
        path = HOME / "work" / "layout.ass"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            for layout, position, expected in (({"layout": "bottom"}, "lower", r"{\an2\pos(540,1162)}"),
                                               ({"layout": "split"}, "upper", r"{\an8\pos(540,1130)}"),
                                               ({"layout": "corner", "corner": "top-left"}, "upper",
                                                r"{\an8\pos(540,578)}")):
                subtitles.write_ass(path, [(0, 1, "x")], "bold", position, layouts.frame(WIDE, layout)["subs"])
                self.assertIn(expected, path.read_text(encoding="utf-8"), layout)
        finally:
            path.unlink(missing_ok=True)

    def test_prepare_writes_the_cam_file_for_the_layout(self):
        cfg = dict(CFG, subtitles="on")
        spots = layouts.frame(WIDE, {"layout": "bottom"})["subs"]
        with mock.patch.object(subtitles, "ffmpeg_has_whisper", return_value=True), \
                mock.patch.object(subtitles, "model_path", return_value=Path("m.bin")), \
                mock.patch.object(subtitles, "transcribe", return_value=[(0.5, 0.9, "nice")]):
            files = subtitles.prepare("x.mp4", 0, 5, cfg, spots)
        try:
            self.assertIn(r"\pos(540,1162)", (HOME / files["cam"]).read_text(encoding="utf-8"))
            self.assertIn(r"\pos(540,1450)", (HOME / files["normal"]).read_text(encoding="utf-8"))
        finally:
            for f in files.values():
                (HOME / f).unlink(missing_ok=True)


class GraphTests(unittest.TestCase):
    """The ffmpeg command render() builds for each layout, without running ffmpeg."""

    def graph(self, layout, styles=("cam",), **cfg):
        cmds = []
        with mock.patch.object(engine, "run", side_effect=lambda cmd, cwd=None: cmds.append(cmd)), \
                mock.patch.object(engine, "move_in"):
            names = engine.render(Path("C:/clips/x.mp4"), HOME / "out", "x", 2, 10, True, [("ACE", 1)],
                                  dict(CFG, styles=list(styles), watermark="@me", **cfg), WIDE,
                                  {"normal": "work/n.ass", "cam": "work/c.ass"}, layout)
        cmd = cmds[0]
        return cmd[cmd.index("-filter_complex") + 1], names, cmd

    def test_strips(self):
        g, _, _ = self.graph({"layout": "top"})
        self.assertIn("[v0cam][v0game]vstack", g)
        self.assertIn("scale=1080:608:force_original_aspect_ratio=increase,crop=1080:608", g)
        self.assertIn("subtitles=f=work/c.ass", g)
        self.assertIn("y=658:", g)          # the label under the webcam
        self.assertIn("y=1690", g)          # the watermark where it always was
        g, _, _ = self.graph({"layout": "bottom"})
        self.assertIn("[v0game][v0cam]vstack", g)
        self.assertIn("y=250:", g)
        self.assertIn("y=1232", g)          # the watermark above the strip
        g, _, _ = self.graph({"layout": "split"})
        self.assertIn("crop=1080:960[v0cam]", g)
        self.assertIn("scale=1080:960[v0game]", g)

    def test_corner_box(self):
        g, _, _ = self.graph({"layout": "corner", "corner": "top-right", "size": "large"})
        self.assertIn("crop=580:326,format=yuva420p[v0face]", g)
        self.assertIn("color=c=black:s=580x326:r=1:d=1,format=gray,geq=lum='255*clip(30+0.5-", g)
        self.assertIn("[v0face][v0mask]alphamerge[v0cam]", g)
        self.assertIn("color=c=white:s=592x338", g)
        self.assertIn("[v0game][v0edge]overlay=454:144[v0under]", g)
        self.assertIn("[v0under][v0cam]overlay=460:150", g)
        self.assertIn("y=526:", g)          # the label under the box

    def test_other_versions_dont_change(self):
        g, names, _ = self.graph({"layout": "bottom"}, styles=("blur", "crop", "cam"))
        self.assertEqual(names, ["x_blur.mp4", "x_crop.mp4", "x_cam.mp4"])
        crop = g.split("[cropraw]")[-1].split("[cropv]")[0]   # what's drawn over the Center crop version
        self.assertIn("subtitles=f=work/n.ass", crop)
        self.assertIn("y=h*0.13:", crop)
        self.assertIn("y=h*0.88", crop)

    def test_just_the_webcam_version(self):
        cmds = []
        with mock.patch.object(engine, "run", side_effect=lambda c, cwd=None: cmds.append(c)), \
                mock.patch.object(engine, "move_in"):
            names = engine.render(Path("x.mp4"), HOME / "out", "x", 0, 5, True, [], CFG, WIDE, None,
                                  {"layout": "split"}, ["cam"])
        self.assertEqual(names, ["x_cam.mp4"])
        self.assertEqual(cmds[0].count("-map"), 2)   # one picture, one sound


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RenderTests(unittest.TestCase):
    """Real renders: the gameplay is blue and the webcam's spot in OBS's picture is red, so the pixels say
    where the webcam ended up."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(CFG, styles=["cam"], ffmpeg=FF_DIR, watermark="@test")
        cls.tmp = Path(tempfile.mkdtemp(prefix="cliplab-layouts-"))
        cls.src = cls.tmp / "Replay layout.mp4"
        subprocess.run([str(engine.tool("ffmpeg", cls.cfg)), "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=0x2040c0:s=1280x720:r=30:d=4,drawbox=x=0:y=540:w=320:h=180:color=red:t=fill",
                        "-f", "lavfi", "-i", "sine=d=4", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(cls.src)], check=True, creationflags=NO_WINDOW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, layout, name, **cfg):
        out = self.tmp / name
        names = engine.render(self.src, out, self.src.stem, 0.5, 3, True, [("TRIPLE KILL", 0.5)], dict(self.cfg, **cfg),
                              WIDE, None, layout)
        video = out / names[0]
        size = subprocess.run([str(engine.tool("ffprobe", self.cfg)), "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
                              capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
        self.assertEqual(size, "1080,1920")
        duration, has_audio = engine.probe(video, self.cfg)
        self.assertTrue(has_audio)
        self.assertAlmostEqual(duration, 3, delta=0.2)
        frame = subprocess.run([str(engine.tool("ffmpeg", self.cfg)), "-v", "error", "-ss", "1.5", "-i", str(video),
                                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               capture_output=True, creationflags=NO_WINDOW, check=True).stdout
        return lambda x, y: tuple(frame[(y * 1080 + x) * 3:(y * 1080 + x) * 3 + 3])

    def assertWebcam(self, px):
        r, g, b = px
        self.assertTrue(r > 180 and g < 90 and b < 90, px)

    def assertPlay(self, px):
        r, g, b = px
        self.assertTrue(b > 140 and r < 90, px)

    def test_top(self):
        px = self.render({"layout": "top"}, "top")
        self.assertWebcam(px(540, 300))
        self.assertPlay(px(540, 1300))

    def test_bottom(self):
        px = self.render({"layout": "bottom"}, "bottom")
        self.assertPlay(px(540, 700))
        self.assertWebcam(px(540, 1620))

    def test_split(self):
        px = self.render({"layout": "split"}, "split")
        self.assertWebcam(px(540, 480))
        self.assertPlay(px(540, 1440))

    def test_corner_boxes(self):
        px = self.render({"layout": "corner", "corner": "top-left", "size": "large"}, "corner-tl")
        self.assertWebcam(px(40 + 290, 150 + 163))
        r, g, b = px(40 - 3, 150 + 163)   # the white edge
        self.assertTrue(min(r, g, b) > 200, (r, g, b))
        self.assertPlay(px(40 + 2, 150 + 2))   # the rounded corner shows the play behind it
        self.assertPlay(px(800, 1000))
        px = self.render({"layout": "corner", "corner": "bottom-right", "size": "small"}, "corner-br",
                         intro="whoosh")   # the zoom punch is over by 1.5 s
        self.assertWebcam(px(1040 - 180, 1590 - 101))
        self.assertPlay(px(200, 1000))


class ShortTests(unittest.TestCase):
    """Each short keeps its own layout through re-cuts and re-renders; a new layout redoes only its Webcam version."""

    def setUp(self):
        self.clip = Path(load_config()["watch_dir"]) / "Replay 2026-10-18 10-00-00.mp4"
        self.clip.parent.mkdir(parents=True, exist_ok=True)
        self.clip.write_bytes(b"")
        self.key = self.clip.name
        self.renders = []
        self.config()
        patches = [mock.patch.object(engine, "probe", return_value=(30.0, True)),
                   mock.patch.object(engine, "current_webcam", return_value=WIDE),
                   mock.patch.object(engine, "render", side_effect=self.fake_render),
                   mock.patch.object(engine, "log")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.clip.unlink(missing_ok=True)
        for f in (STATE, CONFIG):
            f.unlink(missing_ok=True)
        shutil.rmtree(REQUESTS, ignore_errors=True)

    def fake_render(self, src, out_dir, stem, start, length, has_audio, overlays, cfg, webcam=None, subs=None,
                    layout=None, styles=None):
        styles = styles or engine.styles_for(cfg, webcam)
        self.renders.append({"layout": layout, "styles": styles, "start": start, "length": length})
        out_dir.mkdir(parents=True, exist_ok=True)
        return [f"{stem}_{s}.mp4" for s in styles]

    def config(self, **changes):   # the settings the engine reads for app jobs
        write_json(CONFIG, dict({"styles": ["blur", "cam"], "webcam": {"rect": WIDE}}, **changes))

    def make(self, state, **changes):
        self.config(**changes)
        return engine.process_clip(self.clip, self.key, state, load_config())

    def job(self, state, **req):
        write_json(REQUESTS / "job.json", dict(req, key=self.key))
        engine.handle_requests(state)
        return json.loads(next((REQUESTS / "done").glob("*.json")).read_text(encoding="utf-8"))

    def test_new_shorts_use_the_default_and_keep_it(self):
        corner = {"layout": "corner", "corner": "bottom-right", "size": "small"}
        state = {}
        entry = self.make(state, cam_layout=corner)
        self.assertEqual(entry["cam_layout"], corner)
        self.assertEqual(self.renders[-1]["layout"], corner)
        # the default changes; this short's re-cut and re-render keep its own layout
        self.config(cam_layout={"layout": "split"})
        self.assertEqual(self.job(state, type="rerender", start=5, end=15)["ok"], True)
        self.assertEqual((self.renders[-1]["layout"], state[self.key]["cam_layout"]), (corner, corner))
        self.assertEqual(state[self.key]["start"], 5)
        shutil.rmtree(REQUESTS / "done")
        self.job(state, type="rerender")
        self.assertEqual(self.renders[-1]["layout"], corner)

    def test_a_new_layout_makes_only_the_webcam_version_with_the_same_cut(self):
        state = {}
        self.make(state)
        state[self.key].update(start=4.5, end=12.25, trimmed=False)
        before = dict(state[self.key])
        result = self.job(state, type="layout", cam_layout={"layout": "bottom"})
        self.assertEqual((result["ok"], result["message"]), (True, "Webcam layout changed."))
        last = self.renders[-1]
        self.assertEqual(last["styles"], ["cam"])
        self.assertEqual((last["start"], round(last["start"] + last["length"], 2)), (4.5, 12.25))
        entry = state[self.key]
        self.assertEqual(entry["cam_layout"], {"layout": "bottom", "corner": "top-left", "size": "medium"})
        self.assertEqual(entry["outputs"], before["outputs"])   # the other versions are still there
        self.assertFalse(entry["trimmed"])                      # a new layout isn't a re-cut
        # and later re-cuts keep the new layout
        shutil.rmtree(REQUESTS / "done")
        self.job(state, type="rerender", start=1, end=9)
        self.assertEqual(self.renders[-1]["layout"]["layout"], "bottom")
        self.assertEqual(self.renders[-1]["styles"], ["blur", "cam"])

    def test_shorts_from_before_layouts_have_the_webcam_on_top(self):
        state = {}
        self.make(state, cam_layout={"layout": "split", "corner": "top-left", "size": "medium"})
        del state[self.key]["cam_layout"]
        self.make(state, cam_layout={"layout": "split", "corner": "top-left", "size": "medium"})
        self.assertEqual(self.renders[-1]["layout"]["layout"], "top")

    def test_a_layout_that_fails_falls_back_to_the_webcam_on_top(self):
        state = {}
        self.make(state)

        def picky(*args, **kw):
            layout = args[10] if len(args) > 10 else kw.get("layout")
            if layout["layout"] == "corner":
                raise subprocess.CalledProcessError(1, "ffmpeg", stderr="Error: alphamerge went wrong\n")
            return self.fake_render(*args, **kw)
        with mock.patch.object(engine, "render", side_effect=picky):
            result = self.job(state, type="layout", cam_layout={"layout": "corner"})
        self.assertFalse(result["ok"])
        self.assertIn("went on top instead", result["message"])
        self.assertEqual(state[self.key]["cam_layout"]["layout"], "top")
        self.assertIn("alphamerge went wrong", engine.log.call_args_list[-2][0][0])
        # the webcam on top failing is a real failure, not something to retry
        with mock.patch.object(engine, "render", side_effect=subprocess.CalledProcessError(1, "ffmpeg", stderr="x")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.make(state)

    def test_a_short_without_a_webcam_cant_change_layout(self):
        state = {}
        with mock.patch.object(engine, "current_webcam", return_value=None):
            self.make(state)
        self.assertIsNone(state[self.key]["cam_layout"])
        result = self.job(state, type="layout", cam_layout={"layout": "bottom"})
        self.assertEqual((result["ok"], result["message"]), (False, "This short has no Webcam version to change."))


class SettingTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_default_layout_setting(self):
        self.assertEqual(load_config()["cam_layout"], {"layout": "top", "corner": "top-left", "size": "medium"})
        cfg, err = clean_config({"cam_layout": {"layout": "corner", "corner": "bottom-right", "size": "large"}})
        self.assertIsNone(err)
        self.assertEqual(cfg["cam_layout"], {"layout": "corner", "corner": "bottom-right", "size": "large"})
        self.assertEqual(clean_config({"cam_layout": {"layout": "split"}})[0]["cam_layout"]["layout"], "split")
        for bad in ("corner", {"layout": "diagonal"}, {"layout": "corner", "corner": "middle"}, {"corner": "top-left"},
                    {"layout": "corner", "size": "huge"}, {"layout": "top", "x": 1}, None, ["top"]):
            self.assertIsNotNone(clean_config({"cam_layout": bad})[1], bad)

    def test_hand_edited_layouts_are_tidied(self):
        write_json(CONFIG, {"cam_layout": {"layout": "corner", "corner": "middle", "size": "large"}})
        self.assertEqual(load_config()["cam_layout"], {"layout": "corner", "corner": "top-left", "size": "large"})
        write_json(CONFIG, {"cam_layout": "bottom"})
        self.assertEqual(load_config()["cam_layout"]["layout"], "top")

    def test_rename(self):
        self.assertIn("Webcam version", clean_config({"styles": ["cam"]})[1])


class EndpointTests(unittest.TestCase):
    """POST /api/layout: the app asks for a short's new layout."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        cfg = load_config()
        self.watch, self.out = Path(cfg["watch_dir"]), Path(cfg["output_dir"]) / "2026-10-18"
        self.watch.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        entry = {"done": "2026-10-18T10:00:00", "dir": "2026-10-18", "webcam": WIDE, "start": 1, "end": 9,
                 "src_duration": 30}
        self.state = {
            "a.mp4": dict(entry, outputs=["a_blur.mp4", "a_cam.mp4"]),                 # from before layouts
            "b.mp4": dict(entry, outputs=["b_blur.mp4", "b_cam.mp4"], cam_layout={"layout": "split"}),
            "c.mp4": dict(entry, outputs=["c_blur.mp4"]),                               # no Webcam version
            "gone.mp4": dict(entry, outputs=["gone_cam.mp4"]),                          # raw clip expired
            "reel:1": {"done": "2026-10-18T11:00:00", "dir": "2026-10-18", "outputs": ["Reel_cam.mp4"], "reel": True},
        }
        write_json(STATE, self.state)
        for key in ("a.mp4", "b.mp4", "c.mp4"):
            (self.watch / key).write_bytes(b"")
        for entry in self.state.values():
            for name in entry["outputs"]:
                (self.out / name).write_bytes(b"")

    def tearDown(self):
        STATE.unlink(missing_ok=True)
        shutil.rmtree(REQUESTS, ignore_errors=True)
        shutil.rmtree(self.watch, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def post(self, body, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("POST", "/api/layout", body=json.dumps(body),
                     headers={"Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, data

    def jobs(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in REQUESTS.glob("*.json")] if REQUESTS.exists() else []

    def test_a_new_layout_is_queued_for_the_engine(self):
        status, data = self.post({"key": "a.mp4", "cam_layout": {"layout": "corner", "corner": "bottom-left"}})
        self.assertEqual(status, 200)
        job, = self.jobs()
        self.assertEqual(job, {"type": "layout", "key": "a.mp4", "id": data["id"],
                               "cam_layout": {"layout": "corner", "corner": "bottom-left", "size": "medium"}})

    def test_the_same_layout_isnt_made_again(self):
        self.assertEqual(self.post({"key": "a.mp4", "cam_layout": {"layout": "top"}}), (200, {"ok": True, "unchanged": True}))
        self.assertEqual(self.post({"key": "b.mp4", "cam_layout": {"layout": "split", "size": "large"}})[1].get("unchanged"),
                         True)
        self.assertEqual(self.jobs(), [])

    def test_bad_layouts_and_shorts_are_refused(self):
        for bad in ({"layout": "diagonal"}, {"layout": "corner", "corner": "middle"}, {"layout": "corner", "size": 3},
                    {"layout": "top", "extra": True}, "bottom", None, {}):
            status, data = self.post({"key": "a.mp4", "cam_layout": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn("webcam", data["error"].lower())
        for key in ("c.mp4", "reel:1", "nope.mp4", ["a.mp4"], None):
            status, data = self.post({"key": key, "cam_layout": {"layout": "bottom"}})
            self.assertEqual((status, data["error"]), (400, "This short has no Webcam version."), key)
        status, data = self.post({"key": "gone.mp4", "cam_layout": {"layout": "bottom"}})
        self.assertEqual(status, 400)
        self.assertIn("raw clip", data["error"])
        self.assertEqual(self.post(["a.mp4"])[0], 400)
        self.assertEqual(self.jobs(), [])

    def test_other_websites_cant_change_a_layout(self):
        body = {"key": "a.mp4", "cam_layout": {"layout": "bottom"}}
        self.assertEqual(self.post(body, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post(body, {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.jobs(), [])
        port = self.httpd.server_address[1]
        self.assertEqual(self.post(body, {"Origin": f"http://127.0.0.1:{port}"})[0], 200)

    def test_the_app_sees_each_shorts_layout(self):
        state = server.build_state()
        clips = {c["key"]: c for c in state["clips"]}
        self.assertEqual(clips["a.mp4"]["cam_layout"]["layout"], "top")      # shorts from before layouts
        self.assertEqual(clips["b.mp4"]["cam_layout"]["layout"], "split")
        self.assertIsNone(clips["c.mp4"]["cam_layout"])
        self.assertEqual(clips["a.mp4"]["rev"], "2026-10-18T10:00:00")
        self.assertEqual(state["cam_preview"], layouts.preview(WIDE))         # no webcam placed yet: a 16:9 one


if __name__ == "__main__":
    unittest.main()
