"""Tests for waiting until the game closes before rendering: spotting games, the engine's hold,
Render now, and the settings. They use a throwaway ClipLab home and fake games and clocks.
"""
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("CLIPLAB_HOME", tempfile.mkdtemp(prefix="cliplab-test-"))   # never your real ClipLab home

from cliplab import engine, games, server  # noqa: E402
from cliplab.settings import (CONFIG, HEARTBEAT, HOLD, HOME, RENDER_NOW, clean_config, load_config,  # noqa: E402
                              process_names, read_json, write_json)

TASKLIST = '''"System Idle Process","0","Services","0","8 K"
"svchost.exe","1204","Services","0","12,340 K"
"LeagueClient.exe","5120","Console","1","210,412 K"
"LeagueClientUx.exe","5188","Console","1","180,002 K"
"RAINBOWSIX_BE.EXE","9001","Console","1","3,100,000 K"
"Some, Odd ""Name"".exe","42","Console","1","1 K"
not csv at all
INFO: something tasklist said
'''


class SpottingTests(unittest.TestCase):
    def test_reads_tasklist_csv_in_lowercase(self):
        names = games.parse_tasklist(TASKLIST)
        self.assertIn("leagueclient.exe", names)
        self.assertIn("rainbowsix_be.exe", names)
        self.assertIn('some, odd "name".exe', names)   # quoted commas and quotes
        self.assertNotIn("system idle process", names)
        self.assertEqual(games.parse_tasklist(""), set())

    def test_league_client_is_not_a_game_but_a_match_is(self):
        client = {"leagueclient.exe", "leagueclientux.exe", "riotclientservices.exe"}
        self.assertIsNone(games.find_game(client))
        self.assertEqual(games.find_game(client | {"league of legends.exe"}), "League of Legends")

    def test_known_games(self):
        self.assertEqual(games.find_game(games.parse_tasklist(TASKLIST)), "Rainbow Six Siege")
        for exe in ("RainbowSix.exe", "RainbowSix_Vulkan.exe", "RainbowSix_DX12.exe"):
            self.assertEqual(games.find_game({exe.lower()}), "Rainbow Six Siege")
        self.assertEqual(games.find_game({"valorant-win64-shipping.exe"}), "VALORANT")
        self.assertIsNone(games.find_game({"valorant.exe", "riotclientux.exe"}))   # the launcher isn't a match

    def test_extra_games_from_settings(self):
        self.assertEqual(games.find_game({"fortniteclient-win64-shipping.exe"}, ["FortniteClient-Win64-Shipping.exe"]),
                         "FortniteClient-Win64-Shipping")
        self.assertIsNone(games.find_game({"cs2.exe"}, ["Fortnite.exe"]))

    def test_fails_open(self):
        cfg = load_config()
        with mock.patch("cliplab.games.subprocess.run", side_effect=OSError("no tasklist")):
            self.assertIsNone(games.running())
            self.assertIsNone(games.detect(cfg))
        failed = mock.Mock(returncode=1, stdout=TASKLIST)
        with mock.patch("cliplab.games.subprocess.run", return_value=failed):
            self.assertIsNone(games.detect(cfg))   # an error exit isn't trusted, even with a game in the output
        with mock.patch("cliplab.games.running", side_effect=RuntimeError("odd")):
            self.assertIsNone(games.detect(cfg))

    @unittest.skipUnless(os.name == "nt", "tasklist is Windows-only")
    def test_real_tasklist_sees_this_test(self):
        self.assertIn("python.exe", games.running() or set())


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class HoldTests(unittest.TestCase):
    def setUp(self):
        HOME.mkdir(parents=True, exist_ok=True)
        RENDER_NOW.unlink(missing_ok=True)
        self.clock, self.playing, self.checks = Clock(), None, 0
        self.cfg = load_config()
        self.hold = engine.GameHold(detect=self.detect, clock=self.clock)

    def tearDown(self):
        HOLD.unlink(missing_ok=True)
        RENDER_NOW.unlink(missing_ok=True)

    def detect(self, cfg):
        self.checks += 1
        return self.playing

    def step(self, seconds):
        self.clock.now += seconds
        return self.hold.update(self.cfg)

    def test_holds_then_resumes_after_the_grace_period(self):
        self.assertIsNone(self.step(0))
        self.assertFalse(HOLD.exists())
        self.playing = "VALORANT"
        self.assertEqual(self.step(5), "VALORANT")
        self.assertEqual(read_json(HOLD, None), {"game": "VALORANT", "override": False, "closing": False})
        self.playing = None   # the match ends
        self.assertEqual(self.step(5), "VALORANT")   # still within the grace period
        self.assertTrue(read_json(HOLD, None)["closing"])
        self.assertEqual(self.step(engine.GameHold.GRACE - 6), "VALORANT")
        self.assertIsNone(self.step(2))   # GRACE seconds after it was last seen
        self.assertFalse(HOLD.exists())

    def test_next_match_within_the_grace_period_keeps_holding(self):
        self.playing = "League of Legends"
        self.step(0)
        self.playing = None
        for _ in range(3):
            self.assertEqual(self.step(5), "League of Legends")
        self.playing = "League of Legends"   # loading into the next game
        for _ in range(10):
            self.assertEqual(self.step(5), "League of Legends")
        self.assertFalse(read_json(HOLD, None)["closing"])

    def test_checks_for_games_at_most_every_few_seconds(self):
        self.playing = "VALORANT"
        for _ in range(12):   # 6 seconds
            self.step(0.5)
        self.assertEqual(self.checks, 2)   # the first update, then once CHECK seconds had passed

    def test_render_now_lasts_until_the_game_closes(self):
        self.playing = "Rainbow Six Siege"
        self.assertEqual(self.step(0), "Rainbow Six Siege")
        RENDER_NOW.write_text("x")
        self.assertIsNone(self.step(5))
        self.assertTrue(read_json(HOLD, None)["override"])
        self.playing = None
        self.step(engine.GameHold.GRACE + 1)
        self.assertFalse(RENDER_NOW.exists())   # used up once the game closed
        self.playing = "Rainbow Six Siege"
        self.assertEqual(self.step(5), "Rainbow Six Siege")   # the next game waits again

    def test_setting_off_never_holds(self):
        self.playing = "VALORANT"
        self.cfg["wait_for_game_close"] = False
        self.assertIsNone(self.step(0))
        self.assertEqual(self.checks, 0)
        self.assertFalse(HOLD.exists())

    def test_turning_it_off_mid_game_lets_renders_go(self):
        self.playing = "VALORANT"
        self.assertEqual(self.step(0), "VALORANT")
        self.cfg["wait_for_game_close"] = False
        self.assertIsNone(self.step(1))
        self.assertFalse(HOLD.exists())


class SettingsTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_game_processes_are_cleaned(self):
        self.assertEqual(process_names([" Fortnite ", "cs2.exe", "CS2.EXE", "", '"Apex.exe"']),
                         (["Fortnite.exe", "cs2.exe", "Apex.exe"], None))
        for bad in (r"C:\Games\cs2.exe", "../x.exe", "a" * 70, "x<y>.exe"):
            names, problem = process_names([bad, "ok"])
            self.assertEqual(names, ["ok.exe"])
            self.assertIn("isn't a program name", problem)

    def test_app_settings_are_validated(self):
        cfg, error = clean_config({"wait_for_game_close": False, "game_processes": ["Fortnite"]})
        self.assertIsNone(error)
        self.assertEqual((cfg["wait_for_game_close"], cfg["game_processes"]), (False, ["Fortnite.exe"]))
        self.assertIsNotNone(clean_config({"wait_for_game_close": "no"})[1])
        self.assertIsNotNone(clean_config({"game_processes": "Fortnite.exe"})[1])
        self.assertIsNotNone(clean_config({"game_processes": [r"C:\Games\x.exe"]})[1])

    def test_defaults_and_hand_edits(self):
        cfg = load_config()
        self.assertTrue(cfg["wait_for_game_close"])
        self.assertEqual(cfg["game_processes"], [])
        write_json(CONFIG, {"wait_for_game_close": "yes", "game_processes": "Fortnite.exe"})
        self.assertEqual((load_config()["wait_for_game_close"], load_config()["game_processes"]), (True, []))
        write_json(CONFIG, {"wait_for_game_close": False, "game_processes": ["Fortnite", "bad/name"]})
        self.assertEqual((load_config()["wait_for_game_close"], load_config()["game_processes"]), (False, ["Fortnite.exe"]))


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        HOME.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for f in (HOLD, RENDER_NOW, HEARTBEAT):
            f.unlink(missing_ok=True)

    def render_now(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/render_now", body=b"{}", headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, data

    def test_hold_shows_only_while_the_engine_runs(self):
        write_json(HOLD, {"game": "VALORANT", "override": False, "closing": False})
        HEARTBEAT.write_text(str(time.time()))
        self.assertEqual(server.build_state()["hold"]["game"], "VALORANT")
        HEARTBEAT.write_text(str(time.time() - 120))   # the engine stopped mid-game
        self.assertIsNone(server.build_state()["hold"])

    def test_render_now(self):
        self.assertEqual(self.render_now()[0], 400)   # nothing is waiting
        self.assertFalse(RENDER_NOW.exists())
        write_json(HOLD, {"game": "VALORANT", "override": False, "closing": False})
        HEARTBEAT.write_text(str(time.time()))
        self.assertEqual(self.render_now(), (200, {"ok": True}))
        self.assertTrue(RENDER_NOW.exists())


if __name__ == "__main__":
    unittest.main()
