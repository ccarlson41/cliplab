"""Tests for the Getting started checks: reading OBS's settings and when the guide shows.

They use made-up OBS settings folders and a throwaway ClipLab home, never your real ones.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ["CLIPLAB_HOME"] = tempfile.mkdtemp(prefix="cliplab-test-")   # never your real ClipLab home

from cliplab import obs, server  # noqa: E402
from cliplab.settings import CONFIG, HOME, STATE, write_json  # noqa: E402

SAVE_F12 = json.dumps({"ReplayBuffer.Save": [{"control": True, "key": "OBS_KEY_F12"}]})


def make_obs(root, profiles, current=None, ini="user.ini", bom=False):
    """An OBS settings folder: profiles maps a profile folder name to its basic.ini text."""
    for name, text in profiles.items():
        folder = root / "basic" / "profiles" / name
        folder.mkdir(parents=True)
        (folder / "basic.ini").write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    if current:
        (root / ini).write_text(f"[General]\nFirstRun=true\n\n[Basic]\nProfile={current}\nProfileDir={current}\n",
                                encoding="utf-8")
    return root


class ObsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cliplab-obs-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_simple_mode_defaults_mean_no_replay_buffer(self):
        home = make_obs(self.tmp, {"Untitled": "[General]\nName=Untitled\n"}, "Untitled")
        result = obs.check("C:\\Clips", home, videos="C:\\Users\\x\\Videos")
        self.assertEqual(result["profile"], "Untitled")
        self.assertTrue(result["same_as_stream"])   # OBS's default Recording Quality
        self.assertFalse(result["replay_buffer"])
        self.assertEqual(result["replay_seconds"], 20)
        self.assertEqual(result["path"], "C:\\Users\\x\\Videos")
        self.assertFalse(result["path_ok"])
        self.assertEqual(result["hotkeys"], [])

    def test_simple_mode_set_up_for_cliplab(self):
        home = make_obs(self.tmp, {"Mine": "[SimpleOutput]\nRecQuality=Small\nRecRB=true\nRecRBTime=45\n"
                                           "FilePath=c:/clips/\n\n[Hotkeys]\nReplayBuffer=" + SAVE_F12 + "\n"}, "Mine")
        result = obs.check("C:\\Clips", home)
        self.assertTrue(result["replay_buffer"])
        self.assertEqual(result["replay_seconds"], 45)
        self.assertTrue(result["path_ok"])   # OBS writes forward slashes; case and a trailing slash don't matter
        self.assertEqual(result["hotkeys"], ["Ctrl + F12"])

    def test_advanced_mode_uses_its_own_keys(self):
        home = make_obs(self.tmp, {"ClipLab": "[General]\nName=ClipLab\n\n[Output]\nMode=Advanced\n\n[AdvOut]\n"
                                              "RecFilePath=D:/Replays\nRecRB=true\nRecRBTime=60\n\n[SimpleOutput]\n"
                                              "FilePath=C:/Clips\n"}, "ClipLab", ini="global.ini", bom=True)
        result = obs.check("C:\\Clips", home)
        self.assertTrue(result["replay_buffer"])
        self.assertFalse(result["same_as_stream"])   # Recording Quality only matters in Simple mode
        self.assertEqual(result["path"], "D:\\Replays")
        self.assertFalse(result["path_ok"])

    def test_picks_the_current_profile(self):
        profiles = {"A": "[SimpleOutput]\nRecRBTime=30\n", "B": "[SimpleOutput]\nRecRBTime=90\n"}
        self.assertEqual(obs.check("C:\\Clips", make_obs(self.tmp / "1", profiles, "B"))["replay_seconds"], 90)
        unknown = obs.check("C:\\Clips", make_obs(self.tmp / "2", profiles))   # two profiles, no hint which
        self.assertIsNone(unknown["profile"])
        self.assertIsNone(unknown["replay_buffer"])
        self.assertEqual(obs.check("C:\\Clips", make_obs(self.tmp / "3", {"A": ""}))["replay_seconds"], 20)

    def test_no_obs_settings_means_unknown(self):
        result = obs.check("C:\\Clips", self.tmp / "missing")
        self.assertIsNone(result["replay_buffer"])
        self.assertIsNone(result["path_ok"])
        self.assertIsNone(result["hotkeys"])

    def test_hotkey_names(self):
        self.assertEqual(obs.hotkey_name({"key": "OBS_KEY_F9"}), "F9")
        self.assertEqual(obs.hotkey_name({"shift": True, "alt": True, "key": "OBS_KEY_PAGEUP"}), "Alt + Shift + Pageup")
        self.assertEqual(obs.hotkey_name({"key": "OBS_KEY_MOUSE4"}), "Mouse 4")
        bad = obs.read_ini(make_obs(self.tmp, {"A": "[Hotkeys]\nReplayBuffer={not json\n"}) / "basic/profiles/A/basic.ini")
        self.assertEqual(obs.save_hotkey(bad), [])


class GuideTests(unittest.TestCase):
    def setUp(self):
        HOME.mkdir(parents=True, exist_ok=True)
        self.watch = Path(tempfile.mkdtemp(prefix="cliplab-watch-"))
        write_json(CONFIG, {"watch_dir": str(self.watch), "output_dir": str(self.watch.with_name(self.watch.name + "-out"))})

    def tearDown(self):
        CONFIG.unlink(missing_ok=True)
        STATE.unlink(missing_ok=True)
        shutil.rmtree(self.watch, ignore_errors=True)

    def test_latest_clip_is_the_newest_video(self):
        self.assertIsNone(server.latest_clip(self.watch))
        (self.watch / "notes.txt").write_text("x")
        old, new = self.watch / "Replay 1.mp4", self.watch / "sub" / "Replay 2.mkv"
        new.parent.mkdir()
        old.write_bytes(b"x"), new.write_bytes(b"x")
        os.utime(old, (time.time() - 60, time.time() - 60))
        self.assertEqual(server.latest_clip(self.watch)["name"], str(Path("sub", "Replay 2.mkv")))

    def test_guide_shows_until_set_up(self):
        self.assertTrue(server.build_state()["setup_needed"])
        self.assertEqual(server.setup_state()["watch_dir"], str(self.watch))
        write_json(STATE, {"Replay 1.mp4": {"failed": "2026-01-01T00:00:00"}})   # has used ClipLab already
        self.assertFalse(server.build_state()["setup_needed"])
        STATE.unlink()
        write_json(CONFIG, {"watch_dir": str(self.watch), "peer": {"name": "Gaming PC"}})   # OBS is on the other PC
        self.assertFalse(server.build_state()["setup_needed"])
        write_json(CONFIG, {"watch_dir": str(self.watch), "setup_done": True})
        self.assertFalse(server.build_state()["setup_needed"])
        self.assertNotIn("setup_done", server.build_state()["config"])


if __name__ == "__main__":
    unittest.main()
