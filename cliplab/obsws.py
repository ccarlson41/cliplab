"""A small obs-websocket v5 client, so ClipLab can change OBS's sources and hear when it saves a replay.

OBS 28+ has the WebSocket server built in (Tools > WebSocket Server Settings). ClipLab reads the port
and password from OBS's own settings file, so nothing needs typing. Standard library only.
"""
import base64
import hashlib
import json
import os
import select
import socket
import struct
import uuid
from pathlib import Path

OUTPUT_EVENTS = 1 << 6   # obs-websocket's EventSubscription::Outputs (ReplayBufferSaved is one)


class ObsError(Exception):
    """OBS can't be reached, or it refused a request. The message is shown in the app."""


def ws_config_path():
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"


def connection_info(path=None):
    """(port, password) from OBS's obs-websocket settings. Raises ObsError when the server is off."""
    try:
        data = json.loads(Path(path or ws_config_path()).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ObsError("Can't find OBS's WebSocket settings. Is OBS 28 or newer installed?") from None
    if not data.get("server_enabled"):
        raise ObsError("Turn on OBS's WebSocket server: in OBS, Tools > WebSocket Server Settings > "
                       "Enable WebSocket server, then OK.")
    password = data.get("server_password", "") if data.get("auth_required", True) else ""
    return int(data.get("server_port") or 4455), password


def auth_string(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class Obs:
    """with Obs() as obs: obs.request("GetVersion") ... Connects to OBS on this PC unless told otherwise."""

    def __init__(self, host="127.0.0.1", port=None, password=None, events=0, timeout=3):
        if port is None:
            port, saved = connection_info()
            password = saved if password is None else password
        self.host, self.port, self.password, self.events, self.timeout = host, port, password or "", events, timeout
        self.sock, self.buf, self.pending = None, b"", []   # pending: events that came in during a request

    # --- connection ---
    def __enter__(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError:
            raise ObsError("OBS isn't running (or its WebSocket server isn't on).") from None
        try:
            self.handshake()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.sock:
            try:
                self.send_frame(0x8, struct.pack("!H", 1000))
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.read_some()
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ObsError("OBS's WebSocket server didn't accept ClipLab's connection.")
        hello = self.receive()
        identify = {"rpcVersion": 1, "eventSubscriptions": self.events}
        auth = hello.get("d", {}).get("authentication")
        if auth:
            identify["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        self.send({"op": 1, "d": identify})
        try:
            reply = self.receive()
        except ObsError:
            raise ObsError("OBS refused ClipLab's WebSocket password. In OBS, open Tools > WebSocket Server "
                           "Settings and press OK, so the password is saved, then try again.") from None
        if reply.get("op") != 2:
            raise ObsError("OBS's WebSocket server answered in a way ClipLab doesn't understand.")

    # --- requests and events ---
    def request(self, request_type, data=None):
        rid = uuid.uuid4().hex
        self.send({"op": 6, "d": {"requestType": request_type, "requestId": rid, "requestData": data or {}}})
        while True:
            msg = self.receive()
            d = msg.get("d", {})
            if msg.get("op") == 5:
                self.pending.append(d)
            elif msg.get("op") == 7 and d.get("requestId") == rid:
                status = d.get("requestStatus", {})
                if not status.get("result"):
                    raise ObsError(f"OBS couldn't {request_type}: {status.get('comment') or status.get('code')}")
                return d.get("responseData") or {}

    def next_event(self, timeout):
        """The next event OBS sends ({"eventType": ..., "eventData": ...}), or None after timeout seconds."""
        if self.pending:
            return self.pending.pop(0)
        while True:
            if not self.buf and not select.select([self.sock], [], [], timeout)[0]:
                return None
            msg = self.receive()
            if msg.get("op") == 5:
                return msg.get("d", {})

    # --- WebSocket framing (RFC 6455): client frames are masked, OBS's aren't ---
    def send(self, obj):
        self.send_frame(0x1, json.dumps(obj).encode())

    def send_frame(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n) if n < 65536 \
            else bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def read_some(self):
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            raise ObsError("OBS stopped answering.") from None
        if not chunk:
            raise ObsError("OBS closed the WebSocket connection.")
        return chunk

    def read_exact(self, n):
        while len(self.buf) < n:
            self.buf += self.read_some()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def receive(self):
        """The next JSON message, answering pings along the way."""
        parts = []
        while True:
            b1, b2 = self.read_exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self.read_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self.read_exact(8))[0]
            mask = self.read_exact(4) if b2 & 0x80 else None
            payload = self.read_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ObsError("OBS closed the WebSocket connection" +
                               (f": {payload[2:].decode(errors='replace')}" if len(payload) > 2 else "."))
            if opcode == 0x9:
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if b1 & 0x80:
                return json.loads(b"".join(parts))
