"""Tests for the ClipLab engine and settings. Run from the repo root:  python -m unittest -v

They use a throwaway ClipLab home, so they never touch your real clips. The render
test needs ffmpeg: on PATH, or in the folder named by CLIPLAB_TEST_FFMPEG.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_home = tempfile.mkdtemp(prefix="cliplab-test-")
os.environ["CLIPLAB_HOME"] = _home

from cliplab import engine  # noqa: E402
from cliplab.settings import (CONFIG, DEFAULTS, NO_WINDOW, check_folders, clean_config, load_config,  # noqa: E402
                              write_json)

FF_DIR = os.environ.get("CLIPLAB_TEST_FFMPEG") or next(
    (str(p) for p in (Path.home() / "ClipLab" / "bin",) if (p / "ffmpeg.exe").exists()), "")
HAVE_FFMPEG = bool(FF_DIR or shutil.which("ffmpeg"))
CFG = json.loads(json.dumps(DEFAULTS))


def tags(*events):
    return {"events": [{"label": label, "secondsBeforeEnd": sbe} for label, sbe in events]}


class PlanTests(unittest.TestCase):
    def test_untagged_keeps_last_max_seconds(self):
        self.assertEqual(engine.plan(90, None, CFG), (30.0, 60.0, []))
        self.assertEqual(engine.plan(40, None, CFG), (0.0, 40.0, []))

    def test_tagged_trims_around_events(self):
        start, length, overlays = engine.plan(60, tags(("DOUBLE KILL", 14.3), ("TRIPLE KILL", 10.2)), CFG)
        self.assertAlmostEqual(start, 60 - 14.3 - 4)
        self.assertAlmostEqual(start + length, 60 - 10.2 + 3)
        self.assertEqual([o[0] for o in overlays], ["DOUBLE KILL", "TRIPLE KILL"])
        self.assertAlmostEqual(overlays[0][1], 4.0)

    def test_short_play_padded_to_minimum(self):
        start, length, _ = engine.plan(60, tags(("ACE", 5)), CFG)
        self.assertAlmostEqual(length, CFG["min_seconds"])
        self.assertAlmostEqual(start + length, 58)

    def test_long_chain_capped_keeps_the_end(self):
        cfg = dict(CFG, max_seconds=20)
        start, length, overlays = engine.plan(60, tags(("DOUBLE KILL", 50), ("PENTAKILL", 5)), cfg)
        self.assertAlmostEqual(length, 20)
        self.assertAlmostEqual(start + length, 58)
        self.assertEqual([o[0] for o in overlays], ["PENTAKILL"])   # the earlier label falls outside the cut

    def test_override_wins_and_is_clamped(self):
        start, length, overlays = engine.plan(60, tags(("ACE", 10)), CFG, {"start": -5, "end": 80})
        self.assertEqual((start, length), (0.0, 60.0))
        self.assertAlmostEqual(overlays[0][1], 50)

    def test_override_keeps_at_least_one_second(self):
        start, length, _ = engine.plan(60, None, CFG, {"start": 30, "end": 30})
        self.assertEqual((start, length), (30.0, 1.0))


class TextTests(unittest.TestCase):
    def test_labels_are_sanitized_for_drawtext(self):
        self.assertEqual(engine.safe_text("triple kill: it's 'over'"), "TRIPLE KILL ITS OVER")

    def test_watermark_only_when_set(self):
        self.assertNotIn("0.88", engine.post_filters([], CFG))
        self.assertIn("@cam", engine.post_filters([], dict(CFG, watermark="@cam")))

    def test_caption_handles_missing_champion(self):
        text = engine.caption_for({"events": [{"label": "ACE"}], "title": "Ace", "champion": ""}, "x")
        self.assertTrue(text.startswith("Ace\n"))
        self.assertNotIn("# ", text)
        self.assertIn("#kaisa", engine.caption_for({"events": [{}], "title": "t", "champion": "Kai'Sa"}, "x"))

    def test_caption_uses_the_game_hashtags(self):
        self.assertIn("#leagueoflegends", engine.caption_for(None, "x"))
        r6 = engine.caption_for(None, "Replay 1", "r6")
        self.assertTrue(r6.startswith("Replay 1\n"))
        self.assertIn("#rainbowsixsiege", r6)
        self.assertNotIn("#lol", r6)

    def test_tag_file_game_beats_the_setting(self):
        r6 = dict(CFG, game="r6")
        self.assertEqual(engine.game_of(None, r6), "r6")
        self.assertEqual(engine.game_of({"game": "League of Legends"}, r6), "league")
        self.assertEqual(engine.game_of({"game": "Some Other Game"}, r6), "r6")
        self.assertEqual(engine.game_of(None, dict(CFG, game="bogus")), "league")

    def test_reel_game_only_when_every_short_agrees(self):
        self.assertEqual(engine.reel_game([{"game": "r6"}, {"game": "r6"}]), "r6")
        self.assertEqual(engine.reel_game([{}, {"game": "league"}]), "league")   # old entries are League
        self.assertIsNone(engine.reel_game([{"game": "r6"}, {}]))


class SettingsTests(unittest.TestCase):
    def test_rejects_out_of_range_unknown_and_system_keys(self):
        self.assertIsNotNone(clean_config({"crf": 99})[1])
        self.assertIsNotNone(clean_config({"nope": 1})[1])
        self.assertIsNotNone(clean_config({"styles": []})[1])
        self.assertIsNotNone(clean_config({"min_seconds": 50, "max_seconds": 20})[1])
        self.assertIsNotNone(clean_config({"watch_dir": "C:\\"})[1])   # folders aren't editable from the app
        self.assertIsNotNone(clean_config({"game": "fortnite"})[1])
        self.assertIsNotNone(clean_config({"game": ["r6"]})[1])
        self.assertEqual(clean_config({"game": "r6"}), (dict(clean_config({})[0], game="r6"), None))

    def test_cleans_watermark_and_styles(self):
        cfg, err = clean_config({"watermark": "@cam:'x'", "styles": ["crop", "bogus"]})
        self.assertIsNone(err)
        self.assertEqual(cfg["watermark"], "@camx")
        self.assertEqual(cfg["styles"], ["crop"])

    def test_hand_edited_game_is_normalized(self):
        try:
            for saved, expected in (("R6", "r6"), ("siege", "league"), (["r6"], "league")):
                write_json(CONFIG, {"game": saved})
                self.assertEqual(load_config()["game"], expected)
        finally:
            CONFIG.unlink(missing_ok=True)

    def test_output_inside_watch_folder_is_refused(self):
        self.assertIsNotNone(check_folders(dict(CFG, watch_dir="C:\\Clips", output_dir="C:\\Clips\\Shorts")))
        self.assertIsNotNone(check_folders(dict(CFG, watch_dir="C:\\Clips", output_dir="C:\\Clips")))
        self.assertIsNone(check_folders(dict(CFG, watch_dir="C:\\Clips", output_dir="C:\\Shorts")))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RenderTests(unittest.TestCase):
    def test_one_pass_renders_every_style(self):
        cfg = dict(CFG, watermark="@test", ffmpeg=FF_DIR)
        ffmpeg = engine.tool("ffmpeg", cfg)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "Replay test.mp4"
            subprocess.run([str(ffmpeg), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=12:s=1280x720:r=30",
                            "-f", "lavfi", "-i", "sine=d=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                            "-c:a", "aac", str(src)], check=True, creationflags=NO_WINDOW)
            (tmp / "Replay test.mp4.json").write_text(json.dumps(tags(("TRIPLE KILL", 4))))
            start, length, overlays = engine.plan(12, engine.load_tags(src), cfg)
            names = engine.render(src, tmp / "out", src.stem, start, length, True, overlays, cfg)
            self.assertEqual(names, ["Replay test_blur.mp4", "Replay test_crop.mp4"])
            for name in names:
                duration, has_audio = engine.probe(tmp / "out" / name, cfg)
                self.assertAlmostEqual(duration, length, delta=0.2)
                self.assertTrue(has_audio)


def tearDownModule():
    shutil.rmtree(_home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
