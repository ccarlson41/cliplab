"""Tests for subtitles, the intro hook and the other bits that came with them.

The real speech-to-text test needs an ffmpeg with the whisper filter and a whisper.cpp model:
set CLIPLAB_TEST_WHISPER_MODEL to a ggml model file (e.g. ggml-base.bin) to run it.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

_home = tempfile.mkdtemp(prefix="cliplab-test-")
os.environ["CLIPLAB_HOME"] = _home   # never your real ClipLab home

from cliplab import engine, sounds, subtitles  # noqa: E402
from cliplab.settings import CONFIG, DEFAULTS, GAMES, HOME, NO_WINDOW, clean_config, load_config, write_json  # noqa: E402

CFG = json.loads(json.dumps(DEFAULTS))
FF_DIR = os.environ.get("CLIPLAB_TEST_FFMPEG") or next(
    (str(p) for p in (Path.home() / "ClipLab" / "bin",) if (p / "ffmpeg.exe").exists()), "")
MODEL = os.environ.get("CLIPLAB_TEST_WHISPER_MODEL", "")


def words(*items):
    return [(s, e, t) for s, e, t in items]


class WordTests(unittest.TestCase):
    def test_parse_skips_noise_markers_and_bad_lines(self):
        lines = ['{"start":0,"end":500,"text":" Okay,"}', '{"start":500,"end":900,"text":"[BLANK_AUDIO]"}',
                 'not json', '{"start":900,"end":1000,"text":"(music)"}', '{"start":1000,"end":1200,"text":"♪"}',
                 '{"start":1200,"end":1100,"text":"go"}', '{"text":"no times"}', '']
        self.assertEqual(subtitles.parse_words(lines), [(0.0, 0.5, "Okay,"), (1.2, 1.2, "go")])

    def test_chunks_are_short_and_break_at_pauses_and_sentences(self):
        w = words((1.0, 1.2, "okay,"), (1.3, 1.5, "go"), (1.5, 1.7, "go"), (1.7, 1.9, "go"), (1.9, 2.1, "now"),
                  (3.5, 3.9, "that's"), (3.9, 4.0, "a"), (4.0, 4.4, "pentakill!"), (4.4, 4.8, "unbelievable"))
        chunks = subtitles.chunk(w)
        self.assertEqual([c[2] for c in chunks], ["OKAY", "GO GO GO", "NOW", "THAT'S A", "PENTAKILL!", "UNBELIEVABLE"])
        self.assertAlmostEqual(chunks[0][0], 1.0 - subtitles.LEAD)   # shown just before it's said
        for a, b in zip(chunks, chunks[1:]):
            self.assertLessEqual(a[1], b[0])                          # never two captions at once
        self.assertTrue(all(e - s >= 0.15 for s, e, _ in chunks))

    def test_case_follows_the_style(self):
        self.assertEqual(subtitles.chunk(words((0, 1, "Nice."),), upper=False)[0][2], "Nice")
        self.assertEqual(subtitles.chunk([]), [])

    def test_positions_stay_clear_of_labels_and_watermark(self):
        self.assertEqual(subtitles.anchor("lower"), (2, 1450))       # bottom edge, above the watermark (y 1690)
        self.assertEqual(subtitles.anchor("upper"), (8, 420))         # under the play label (y 250-355)
        self.assertEqual(subtitles.anchor("center"), (5, 960))
        self.assertEqual(subtitles.anchor("center", cam_top=608), (5, 608 + (1920 - 608) // 2))   # over the play
        self.assertEqual(subtitles.anchor("upper", cam_top=608), (8, 778))

    def test_ass_file(self):
        path = HOME / "work" / "t.ass"
        path.parent.mkdir(parents=True, exist_ok=True)
        rel = subtitles.write_ass(path, [(0.5, 1.25, "GG {WP}"), (61.0, 62.5, "a\\b")], "pop", "upper", cam_top=608)
        self.assertEqual(rel, "work/t.ass")
        text = path.read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1080", text)
        self.assertIn("Style: Sub,Arial Black,90,&H0000E1FF", text)
        self.assertIn(r"Dialogue: 0,0:00:00.50,0:00:01.25,Sub,,0,0,0,,{\an8\pos(540,778)\fscx70", text)
        self.assertIn("GG (WP)", text)          # braces can't start ASS override tags
        self.assertIn("0:01:01.00,0:01:02.50", text)
        self.assertIn("}a/b", text)
        subtitles.write_ass(path, [(0, 1, "x")], "boxed", "lower")
        self.assertNotIn(r"\fscx70", path.read_text(encoding="utf-8"))   # only Pop pops


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(subtitles, "MODELS_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def fake_download(self, size):
        body = io.BytesIO(b"x" * size)
        body.headers = {"Content-Length": str(size)}
        body.__enter__ = lambda *a: body
        body.__exit__ = lambda *a: None
        return mock.MagicMock(__enter__=lambda *a: body, __exit__=lambda *a: None)

    def model(self, size):
        return {"on": ("tiny.bin", 1, hashlib.sha256(b"x" * size).hexdigest())}

    def test_downloads_once_then_reuses(self):
        seen = []
        with mock.patch.dict(subtitles.MODELS, self.model(1_000_000)), \
                mock.patch.object(subtitles.urllib.request, "urlopen", return_value=self.fake_download(1_000_000)) as get:
            path = subtitles.model_path("on", progress=seen.append)
            self.assertEqual(path, self.dir / "tiny.bin")
            self.assertEqual(path.stat().st_size, 1_000_000)
            subtitles.model_path("on")
            self.assertEqual(get.call_count, 1)
        self.assertEqual(list(self.dir.glob("*.part")), [])

    def test_a_cut_off_download_leaves_nothing_behind(self):
        with mock.patch.dict(subtitles.MODELS, self.model(300_000)), \
                mock.patch.object(subtitles.urllib.request, "urlopen", return_value=self.fake_download(300_000)):
            with self.assertRaisesRegex(OSError, "stopped early"):
                subtitles.model_path("on")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_wrong_file_is_never_kept(self):
        # the right size, the wrong bytes: whisper.cpp's native code would be reading a file nobody vetted
        with mock.patch.dict(subtitles.MODELS, {"on": ("tiny.bin", 1, "0" * 64)}), \
                mock.patch.object(subtitles.urllib.request, "urlopen", return_value=self.fake_download(1_000_000)):
            with self.assertRaisesRegex(OSError, "isn't the right file"):
                subtitles.model_path("on")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_downloads_are_pinned(self):
        self.assertNotIn("/resolve/main/", subtitles.MODEL_URL)
        self.assertTrue(all(len(sha) == 64 for _, _, sha in subtitles.MODELS.values()))


class FallbackTests(unittest.TestCase):
    def test_off_or_silent_means_no_subtitles(self):
        self.assertEqual(engine.make_subtitles("x.mp4", "x", 0, 10, True, CFG, None, lambda note: None), ({}, ""))
        files, why = engine.make_subtitles("x.mp4", "x", 0, 10, False, dict(CFG, subtitles="on"), None, lambda note: None)
        self.assertEqual((files, "no sound" in why), ({}, True))
        with mock.patch.object(subtitles, "prepare", return_value={}):
            files, why = engine.make_subtitles("x.mp4", "x", 0, 10, True, dict(CFG, subtitles="on"), None, lambda note: None)
        self.assertEqual((files, "nobody talks" in why), ({}, True))

    def test_problems_render_without_subtitles(self):
        cfg = dict(CFG, subtitles="on")
        with mock.patch.object(subtitles, "ffmpeg_has_whisper", return_value=False), \
                mock.patch.object(engine, "log") as log:
            files, why = engine.make_subtitles("x.mp4", "x.mp4", 0, 10, True, cfg, None, lambda note: None)
        self.assertEqual(files, {})
        self.assertIn("Run ClipLab's setup again", why)          # shown on the short in the app
        self.assertIn("Run ClipLab's setup again", log.call_args[0][0])
        with mock.patch.object(subtitles, "ffmpeg_has_whisper", return_value=True), \
                mock.patch.object(subtitles, "model_path", side_effect=OSError("no internet")), \
                mock.patch.object(engine, "log") as log:
            files, why = engine.make_subtitles("x.mp4", "x.mp4", 0, 10, True, cfg, None, lambda note: None)
        self.assertEqual(files, {})
        self.assertIn("Couldn't download the speech model (no internet)", why)
        with mock.patch.object(subtitles, "ffmpeg_has_whisper", return_value=True), \
                mock.patch.object(subtitles, "model_path", return_value=Path("m.bin")), \
                mock.patch.object(subtitles, "transcribe", side_effect=subprocess.CalledProcessError(
                    1, "ffmpeg", stderr="first line\nwhisper: failed to load model\n")), \
                mock.patch.object(engine, "log"):
            files, why = engine.make_subtitles("x.mp4", "x.mp4", 0, 10, True, cfg, None, lambda note: None)
        self.assertEqual(why, "Subtitles didn't work this time (whisper: failed to load model).")

    def test_the_app_hears_about_it(self):
        from cliplab import server
        with mock.patch.object(subtitles, "ffmpeg_has_whisper", return_value=False):
            state = server.build_state()
        self.assertIs(state["speech_to_text"], False)
        write_json(HOME / "processed.json", {"a.mp4": {"done": "2026-10-17T10:00:00", "outputs": ["a_crop.mp4"],
                                                       "dir": "d", "subtitles_note": "No subtitles: nobody talks in this clip."}})
        (HOME / "Ready" / "d").mkdir(parents=True, exist_ok=True)
        (HOME / "Ready" / "d" / "a_crop.mp4").write_bytes(b"")
        try:
            clip = next(c for c in server.build_state()["clips"] if c["key"] == "a.mp4")
            self.assertEqual(clip["subtitles_note"], "No subtitles: nobody talks in this clip.")
        finally:
            (HOME / "processed.json").unlink(missing_ok=True)

    def test_whisper_detection(self):
        subtitles._whisper_builds.clear()
        run = mock.Mock(return_value=mock.Mock(stdout=" .. whisper           A->A       Transcribe audio\n"))
        with mock.patch.object(subtitles.subprocess, "run", run), \
                mock.patch.object(subtitles, "tool", side_effect=lambda name, cfg: Path(cfg["ffmpeg"]) / "ffmpeg.exe"):
            self.assertTrue(subtitles.ffmpeg_has_whisper(dict(CFG, ffmpeg="C:\\a")))
            subtitles.ffmpeg_has_whisper(dict(CFG, ffmpeg="C:\\a"))
            self.assertEqual(run.call_count, 1)   # asked once per ffmpeg
            run.return_value = mock.Mock(stdout=" ... volume A->A Change input volume.\n")
            self.assertFalse(subtitles.ffmpeg_has_whisper(dict(CFG, ffmpeg="C:\\b")))
        subtitles._whisper_builds.clear()


class GraphTests(unittest.TestCase):
    """The ffmpeg command render() builds, without running ffmpeg."""

    def command(self, has_audio=True, subs=None, **cfg):
        cmds = []
        with mock.patch.object(engine, "run", side_effect=lambda cmd, cwd=None: cmds.append((cmd, cwd))), \
                mock.patch.object(engine, "move_in"):
            engine.render(Path("C:/clips/x.mp4"), HOME / "out", "x", 2, 10, has_audio, [], dict(CFG, **cfg), None, subs)
        return cmds[0]

    def test_intro_whoosh_and_punch(self):
        cmd, cwd = self.command(intro="whoosh", styles=["crop"])
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(cwd, HOME)
        self.assertIn(str(sounds.whoosh_file()), cmd)
        self.assertIn(engine.ZOOM, graph)
        self.assertIn("amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=disabled:latency=1,", graph)
        cmd, _ = self.command(intro="punch", styles=["crop"])
        self.assertNotIn(str(sounds.whoosh_file()), cmd)
        self.assertIn(engine.ZOOM, cmd[cmd.index("-filter_complex") + 1])
        cmd, _ = self.command(intro="off", styles=["crop"])
        self.assertNotIn("eval=frame", cmd[cmd.index("-filter_complex") + 1])

    def test_a_silent_clip_still_gets_the_whoosh(self):
        cmd, _ = self.command(has_audio=False, intro="whoosh", styles=["blur", "crop"])
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[1:a]aresample=48000,apad,asplit=2[a0][a1]", graph)
        self.assertNotIn("-an", cmd)
        cmd, _ = self.command(has_audio=False, intro="off", styles=["crop"])
        self.assertIn("-an", cmd)

    def test_subtitles_go_under_the_labels(self):
        cmd, _ = self.command(subs={"normal": "work/s.ass"}, styles=["crop"], watermark="@me")
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("subtitles=f=work/s.ass,drawtext", graph)   # labels and watermark drawn over them


class SettingsTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_new_choices(self):
        cfg, err = clean_config({"subtitles": "accurate", "subtitle_style": "boxed", "subtitle_position": "upper",
                                 "intro": "whoosh", "game": "valorant"})
        self.assertIsNone(err)
        self.assertEqual((cfg["subtitles"], cfg["intro"], cfg["game"]), ("accurate", "whoosh", "valorant"))
        for bad in ({"subtitles": "yes"}, {"subtitle_style": "comic"}, {"subtitle_position": "left"}, {"intro": "boom"}):
            self.assertIsNotNone(clean_config(bad)[1], bad)
        write_json(CONFIG, {"subtitle_style": "neon", "intro": 3})
        self.assertEqual((load_config()["subtitle_style"], load_config()["intro"]), ("bold", "off"))
        self.assertEqual(GAMES["valorant"]["hashtags"], "#valorant #valorantclips")

    def test_whoosh_is_a_short_valid_wav(self):
        with wave.open(io.BytesIO(sounds.wav_bytes("whoosh"))) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 0.38, places=2)
        self.assertIn("whoosh", sounds.NAMES)
        self.assertEqual(sounds.whoosh_file().read_bytes(), sounds.wav_bytes("whoosh"))   # the same whoosh each time


@unittest.skipUnless(MODEL and os.path.exists(MODEL) and os.name == "nt", "set CLIPLAB_TEST_WHISPER_MODEL")
class RealSubtitleTests(unittest.TestCase):
    def test_spoken_words_end_up_on_screen(self):
        cfg = dict(CFG, ffmpeg=FF_DIR, subtitles="on", subtitle_style="pop", styles=["crop"], intro="whoosh")
        if not subtitles.ffmpeg_has_whisper(cfg):
            self.skipTest("this ffmpeg has no whisper filter")
        ffmpeg = str(engine.tool("ffmpeg", cfg))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "models").mkdir()
            shutil.copy(MODEL, tmp / "models" / "ggml-base.bin")   # removed with tmp; transcribe uses a relative path
            patcher = mock.patch.object(subtitles, "MODELS_DIR", tmp / "models")
            patcher.start()
            self.addCleanup(patcher.stop)
            speech = tmp / "speech.wav"
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                            f"$s.SetOutputToWaveFile('{speech}'); $s.Speak('Nice, that is a triple kill. Lets go.'); $s.Dispose()"],
                           check=True, creationflags=NO_WINDOW)
            clip = tmp / "Replay talk.mp4"
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=6:s=1280x720:r=30",
                            "-i", str(speech), "-filter_complex", "[1]adelay=1000|1000,apad[a]", "-map", "0", "-map", "[a]",
                            "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(clip)],
                           check=True, creationflags=NO_WINDOW)
            subs, why = engine.make_subtitles(clip, "talk", 0, 6, True, cfg, None, lambda note: None)
            self.assertEqual(why, "")
            text = (HOME / subs["normal"]).read_text(encoding="utf-8")
            self.assertIn("TRIPLE", text)
            names = engine.render(clip, tmp / "out", clip.stem, 0, 6, True, [], cfg, None, subs)
            self.assertEqual(engine.probe(tmp / "out" / names[0], cfg)[1], True)


def tearDownModule():
    shutil.rmtree(_home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
