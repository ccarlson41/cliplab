"""Tests for ClipLab's OBS control: the obs-websocket client, the source changes, sounds and settings.

The WebSocket tests talk to a small fake OBS on a spare port; nothing here needs OBS installed.
"""
import base64
import hashlib
import http.client
import io
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer

os.environ.setdefault("CLIPLAB_HOME", tempfile.mkdtemp(prefix="cliplab-test-"))

from cliplab import engine, obsctl, obsws, server, sounds  # noqa: E402
from cliplab.settings import CONFIG, DEFAULTS, clean_config, load_config, webcam_rect, write_json  # noqa: E402

PASSWORD = "hunter2"


# ---------- a fake obs-websocket server ----------

def frame(opcode, payload):
    n = len(payload)
    head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n))
    return head + payload


def read_frame(conn):
    b1, b2 = conn.recv(2, socket.MSG_WAITALL)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", conn.recv(2, socket.MSG_WAITALL))[0]
    mask = conn.recv(4, socket.MSG_WAITALL)
    data = conn.recv(n, socket.MSG_WAITALL) if n else b""
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def fake_obs(conn, password):
    request = b""
    while b"\r\n\r\n" not in request:
        request += conn.recv(1024)
    key = [ln.split(b":", 1)[1].strip() for ln in request.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key")][0]
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Protocol: obswebsocket.json\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    salt, challenge = "salty", "chally"
    send = lambda obj: conn.sendall(frame(0x1, json.dumps(obj).encode()))   # noqa: E731
    send({"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": salt, "challenge": challenge}}})
    _, identify = read_frame(conn)
    if json.loads(identify)["d"].get("authentication") != obsws.auth_string(password, salt, challenge):
        conn.sendall(frame(0x8, struct.pack("!H", 4009) + b"Authentication failed."))
        return
    send({"op": 2, "d": {"negotiatedRpcVersion": 1}})
    while True:
        opcode, payload = read_frame(conn)
        if opcode == 0x8:
            return
        if opcode == 0xA:   # our ping's pong
            continue
        d = json.loads(payload)["d"]
        reply = {"requestType": d["requestType"], "requestId": d["requestId"],
                 "requestStatus": {"result": True, "code": 100}}
        if d["requestType"] == "GetVersion":
            conn.sendall(frame(0x9, b"hi"))   # a ping first: the client must answer and carry on
            reply["responseData"] = {"obsVersion": "31.0.0", "big": "x" * 300}
        elif d["requestType"] == "SaveReplayBuffer":
            send({"op": 5, "d": {"eventType": "ReplayBufferSaved", "eventData": {"savedReplayPath": "C:/x.mp4"}}})
        else:
            reply["requestStatus"] = {"result": False, "code": 204, "comment": "No such request"}
        send({"op": 7, "d": reply})


class FakeServer:
    def __init__(self, password=PASSWORD):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, args=(password,), daemon=True).start()

    def serve(self, password):
        conn, _ = self.sock.accept()
        with conn:
            try:
                fake_obs(conn, password)
            except (OSError, ValueError):
                pass
        self.sock.close()


class WebSocketTests(unittest.TestCase):
    def test_requests_events_and_pings(self):
        srv = FakeServer()
        with obsws.Obs(port=srv.port, password=PASSWORD, events=obsws.OUTPUT_EVENTS) as obs:
            data = obs.request("GetVersion")
            self.assertEqual(data["obsVersion"], "31.0.0")
            self.assertEqual(len(data["big"]), 300)   # a frame with a 16-bit length
            with self.assertRaisesRegex(obsws.ObsError, "No such request"):
                obs.request("Nonsense")
            obs.request("SaveReplayBuffer")
            self.assertEqual(obs.next_event(timeout=1)["eventType"], "ReplayBufferSaved")
            self.assertIsNone(obs.next_event(timeout=0.2))

    def test_wrong_password(self):
        srv = FakeServer()
        with self.assertRaisesRegex(obsws.ObsError, "password"):
            with obsws.Obs(port=srv.port, password="wrong"):
                pass

    def test_obs_not_running(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with self.assertRaisesRegex(obsws.ObsError, "isn't running"):
            with obsws.Obs(port=port, password=""):
                pass

    def test_connection_info_from_obs_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"server_enabled": True, "server_port": 4466, "auth_required": True,
                           "server_password": "abc"}, f)
            self.assertEqual(obsws.connection_info(path), (4466, "abc"))
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"server_enabled": False}, f)
            with self.assertRaisesRegex(obsws.ObsError, "Enable WebSocket server"):
                obsws.connection_info(path)
            with self.assertRaisesRegex(obsws.ObsError, "Can't find"):
                obsws.connection_info(os.path.join(tmp, "missing.json"))


# ---------- source changes, against a pretend OBS ----------

class PretendObs:
    """Just enough of OBS's requests for obsctl: one scene, inputs with settings, scene items with transforms."""

    def __init__(self, items=(), global_inputs=()):
        self.inputs = {}      # name -> {"kind", "settings"}
        self.items = []       # [{"sourceName", "inputKind", "sceneItemId", "sceneItemEnabled"}] in back-to-front order
        self.transforms = {}
        self.calls = []
        for name, kind in global_inputs:
            self.inputs[name] = {"kind": kind, "settings": {}}
        for name, kind in items:
            self.add(name, kind, {})

    def __call__(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def add(self, name, kind, settings, enabled=True):
        self.inputs[name] = {"kind": kind, "settings": dict(settings)}
        sid = len(self.transforms) + 1
        self.items.append({"sourceName": name, "inputKind": kind, "sceneItemId": sid, "sceneItemEnabled": enabled})
        self.transforms[sid] = {"positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_NONE",
                                "width": 1920, "height": 1080, "sourceWidth": 1920, "sourceHeight": 1080}
        return sid

    def item(self, sid):
        return next(i for i in self.items if i["sceneItemId"] == sid)

    def request(self, kind, data=None):
        data = data or {}
        self.calls.append(kind)
        if kind == "GetCurrentProgramScene":
            return {"sceneName": "Game", "currentProgramSceneName": "Game"}
        if kind == "GetSceneItemList":
            return {"sceneItems": [dict(i) for i in self.items]}
        if kind == "GetInputList":
            return {"inputs": [{"inputName": n, "inputKind": v["kind"], "unversionedInputKind": v["kind"]}
                               for n, v in self.inputs.items()]}
        if kind == "GetVideoSettings":
            return {"baseWidth": 1920, "baseHeight": 1080}
        if kind == "GetInputSettings":
            return {"inputSettings": self.inputs[data["inputName"]]["settings"]}
        if kind == "SetInputSettings":
            self.inputs[data["inputName"]]["settings"].update(data["inputSettings"])
            return {}
        if kind == "CreateInput":
            if data["inputName"] in self.inputs:
                raise obsws.ObsError("name taken")
            return {"sceneItemId": self.add(data["inputName"], data["inputKind"], data["inputSettings"],
                                            data.get("sceneItemEnabled", True))}
        if kind == "RemoveInput":
            if data["inputName"] not in self.inputs:
                raise obsws.ObsError("no such input")
            del self.inputs[data["inputName"]]
            self.items = [i for i in self.items if i["sourceName"] != data["inputName"]]
            return {}
        if kind == "GetInputPropertiesListPropertyItems":
            choices = {"device_id": [("default", "Default"), ("mic2", "USB Mic")],
                       "monitor_id": [("m1", "Monitor 1"), ("m2", "Monitor 2")],
                       "video_device_id": [("cam1", "Logitech C920")]}[data["propertyName"]]
            return {"propertyItems": [{"itemName": n, "itemValue": v, "itemEnabled": True} for v, n in choices]}
        if kind == "SetSceneItemIndex":
            item = self.item(data["sceneItemId"])
            self.items.remove(item)
            self.items.insert(data["sceneItemIndex"], item)
            return {}
        if kind == "SetSceneItemTransform":
            self.transforms[data["sceneItemId"]].update(data["sceneItemTransform"])
            return {}
        if kind == "GetSceneItemTransform":
            return {"sceneItemTransform": dict(self.transforms[data["sceneItemId"]])}
        if kind == "SetSceneItemEnabled":
            self.item(data["sceneItemId"])["sceneItemEnabled"] = data["sceneItemEnabled"]
            return {}
        raise AssertionError(f"unexpected request {kind}")


class ObsControlTests(unittest.TestCase):
    def test_describe_lists_choices_without_leaving_probes(self):
        obs = PretendObs(items=[("League", "game_capture")], global_inputs=[("Mic/Aux", "wasapi_input_capture")])
        obs.inputs["Mic/Aux"]["settings"] = {"device_id": "mic2"}
        d = obsctl.describe(obs_factory=obs)
        self.assertTrue(d["game_capture"])
        self.assertEqual((d["mic"]["source"], d["mic"]["current"]), ("Mic/Aux", "mic2"))
        self.assertEqual([o["id"] for o in d["display"]["options"]], ["m1", "m2"])
        self.assertIsNone(d["display"]["source"])
        self.assertIsNone(d["webcam"]["options"])   # no webcam probing (camera light) unless asked
        self.assertNotIn(obsctl.PROBE, obs.inputs)
        d = obsctl.describe(find_webcams=True, obs_factory=obs)
        self.assertEqual(d["webcam"]["options"], [{"id": "cam1", "name": "Logitech C920"}])
        self.assertNotIn(obsctl.PROBE, obs.inputs)

    def test_apply_mic_and_new_screen_capture_goes_behind_the_game(self):
        obs = PretendObs(items=[("League", "game_capture")], global_inputs=[("Mic/Aux", "wasapi_input_capture")])
        self.assertEqual(obsctl.apply({"mic": "mic2", "display": "m2"}, obs_factory=obs), "unchanged")
        self.assertEqual(obs.inputs["Mic/Aux"]["settings"]["device_id"], "mic2")
        self.assertEqual(obs.inputs["Display"]["settings"]["monitor_id"], "m2")
        self.assertEqual([i["sourceName"] for i in obs.items], ["Display", "League"])
        sid = obs.items[0]["sceneItemId"]
        self.assertEqual((obs.transforms[sid]["boundsWidth"], obs.transforms[sid]["boundsHeight"]), (1920, 1080))
        obsctl.apply({"display": "m1"}, obs_factory=obs)   # second time: the existing capture is changed
        self.assertEqual(obs.inputs["Display"]["settings"]["monitor_id"], "m1")
        self.assertEqual(len(obs.items), 2)

    def test_apply_webcam_places_it_on_top_in_the_corner(self):
        obs = PretendObs(items=[("League", "game_capture")])
        rect = obsctl.apply({"webcam": {"device": "cam1", "corner": "top-right", "size": 0.25}}, obs_factory=obs)
        self.assertEqual(rect, [0.75, 0.0, 0.25, 0.25 * 1920 * 9 / 16 / 1080])
        self.assertEqual(obs.items[-1]["sourceName"], "ClipLab Webcam")
        self.assertEqual(obs.inputs["ClipLab Webcam"]["settings"]["video_device_id"], "cam1")
        # OBS reports a 4:3 camera letterboxed in its 16:9 box: the rect is just the picture
        sid = obs.items[-1]["sceneItemId"]
        obs.transforms[sid].update(sourceWidth=640, sourceHeight=480)
        live = obsctl.live_webcam_rect(obs_factory=obs)
        self.assertAlmostEqual(live[2], (270 * 4 / 3) / 1920)
        self.assertAlmostEqual(live[0], (1440 + (480 - 360) / 2) / 1920)
        self.assertIsNone(obsctl.apply({"webcam": {"device": "", "corner": "top-right", "size": 0.25}}, obs_factory=obs))
        self.assertFalse(obs.items[-1]["sceneItemEnabled"])
        self.assertIsNone(obsctl.live_webcam_rect(obs_factory=obs))

    def test_item_rect_follows_obs_alignment(self):
        # centered item without bounds: position is its middle
        t = {"positionX": 960, "positionY": 540, "alignment": 0, "boundsType": "OBS_BOUNDS_NONE",
             "width": 480, "height": 270}
        self.assertEqual(obsctl.item_rect(t, 1920, 1080), [0.375, 0.375, 0.25, 0.25])
        # bottom-right aligned
        t.update(positionX=1920, positionY=1080, alignment=2 | 8)
        self.assertEqual(obsctl.item_rect(t, 1920, 1080), [0.75, 0.75, 0.25, 0.25])


# ---------- engine, settings, sounds, server ----------

class WebcamSettingsTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_webcam_rect_validation(self):
        self.assertEqual(webcam_rect([0.75, 0, 0.25, 0.25]), [0.75, 0.0, 0.25, 0.25])
        for bad in (None, [1, 2], [0.9, 0, 0.25, 0.25], [0, 0, 0.001, 0.2], ["a", 0, 0.2, 0.2], [-0.1, 0, 0.2, 0.2]):
            self.assertIsNone(webcam_rect(bad), bad)

    def test_choices_and_cam_style(self):
        self.assertIsNotNone(clean_config({"theme": "neon"})[1])
        self.assertIsNotNone(clean_config({"save_sound": "airhorn"})[1])
        self.assertEqual(clean_config({"accent": "blue", "save_sound": "chime"})[0]["accent"], "blue")
        self.assertIn("webcam", clean_config({"styles": ["cam"]})[1])   # no webcam placed yet
        write_json(CONFIG, {"webcam": {"rect": [0, 0.7, 0.25, 0.3]}, "theme": "LOUD"})
        cfg = load_config()
        self.assertEqual(cfg["theme"], "system")   # bad hand edits fall back
        self.assertEqual(cfg["webcam"]["corner"], "bottom-left")   # missing keys keep their defaults
        self.assertEqual(clean_config({"styles": ["cam", "crop"]}), (dict(cfg, styles=["crop", "cam"]), None))

    def test_cam_layout_math(self):
        self.assertEqual(engine.cam_height([0, 0, 0.25, 0.25]), 608)   # 16:9 webcam: 1080x608 on top
        self.assertEqual(engine.cam_height([0, 0, 0.2, 0.4]), 840)     # tall: capped so the play keeps room
        cfg = dict(json.loads(json.dumps(DEFAULTS)), styles=["blur", "cam"])
        self.assertEqual(engine.styles_for(cfg, None), ["blur"])
        self.assertEqual(engine.styles_for(dict(cfg, styles=["cam"]), None), ["crop"])
        self.assertEqual(engine.styles_for(cfg, [0, 0, 0.25, 0.25]), ["blur", "cam"])

    def test_sounds_are_valid_wavs(self):
        for name in sounds.TONES:
            with wave.open(io.BytesIO(sounds.wav_bytes(name))) as w:
                self.assertEqual((w.getnchannels(), w.getframerate()), (1, sounds.RATE))
                self.assertGreater(w.getnframes(), 1000)


class ObsEndpointTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_obs_changes_validation(self):
        self.assertEqual(server.obs_changes({"mic": "default"}), ({"mic": "default"}, None))
        self.assertIsNotNone(server.obs_changes({"mic": ""})[1])
        self.assertIsNotNone(server.obs_changes({"webcam": {"device": "cam1", "corner": "middle"}})[1])
        self.assertIsNotNone(server.obs_changes({"webcam": {"device": "cam1", "size": 2}})[1])
        self.assertEqual(server.obs_changes({"webcam": {"device": "", "corner": "top-left", "size": 0.2}})[0],
                         {"webcam": {"device": "", "corner": "top-left", "size": 0.2}})

    def test_placing_and_removing_the_webcam_updates_versions(self):
        original = obsctl.apply
        try:
            obsctl.apply = lambda changes: [0, 0.75, 0.25, 0.25]
            cam = {"device": "cam1", "corner": "bottom-left", "size": 0.25}
            self.assertEqual(self.post({"webcam": cam})[0], 200)
            cfg = load_config()
            self.assertEqual(cfg["webcam"]["rect"], [0, 0.75, 0.25, 0.25])
            self.assertEqual(cfg["styles"], ["blur", "crop", "cam"])
            obsctl.apply = lambda changes: None
            self.assertEqual(self.post({"webcam": dict(cam, device="")})[0], 200)
            cfg = load_config()
            self.assertIsNone(cfg["webcam"]["rect"])
            self.assertEqual(cfg["styles"], ["blur", "crop"])

            def unreachable(changes):
                raise obsws.ObsError("OBS isn't running (or its WebSocket server isn't on).")
            obsctl.apply = unreachable
            status, data = self.post({"mic": "default"})
            self.assertEqual(status, 400)
            self.assertIn("isn't running", data["error"])
        finally:
            obsctl.apply = original

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("POST", "/api/obs", body=json.dumps(body), headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, data


if __name__ == "__main__":
    unittest.main()
