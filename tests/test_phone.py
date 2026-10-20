"""Tests for phone access: picking the address a phone can reach, the QR code, and the Settings switch
(which only someone at this PC may flip). Uses a throwaway ClipLab home, never your real one.
"""
import http.client
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_home = tempfile.mkdtemp(prefix="cliplab-test-")
os.environ["CLIPLAB_HOME"] = _home   # never your real ClipLab home

from cliplab import lan, server  # noqa: E402
from cliplab.settings import CONFIG, load_config, write_json  # noqa: E402
from cliplab.vendor.qrcodegen import QrCode  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# what Windows reports on a typical gaming PC with WSL, Hyper-V, a VPN and Tailscale installed
ADAPTERS = [
    {"ip": "172.24.160.1", "name": "vEthernet (WSL (Hyper-V firewall))", "description": "Hyper-V Virtual Ethernet Adapter",
     "physical": False, "gateway": False},
    {"ip": "172.31.80.1", "name": "vEthernet (Default Switch)", "description": "Hyper-V Virtual Ethernet Adapter #2",
     "physical": False, "gateway": False},
    {"ip": "10.8.0.6", "name": "OpenVPN TAP", "description": "TAP-Windows Adapter V9", "physical": False, "gateway": True},
    {"ip": "100.101.102.103", "name": "Tailscale", "description": "Tailscale Tunnel", "physical": False, "gateway": False},
    {"ip": "192.168.56.1", "name": "Ethernet 3", "description": "VirtualBox Host-Only Ethernet Adapter",
     "physical": False, "gateway": False},
    {"ip": "169.254.12.7", "name": "Ethernet", "description": "Realtek PCIe 2.5GbE Family Controller",
     "physical": True, "gateway": False},
    {"ip": "192.168.1.42", "name": "Wi-Fi", "description": "Intel(R) Wi-Fi 6E AX211 160MHz", "physical": True,
     "gateway": True},
    {"ip": "10.0.0.15", "name": "Bluetooth Network Connection", "description": "Bluetooth Device (Personal Area Network)",
     "physical": True, "gateway": False},
]


class AddressTests(unittest.TestCase):
    def test_the_home_wifi_wins_and_virtual_adapters_are_skipped(self):
        picked = lan.pick(ADAPTERS, default_ip="10.8.0.6")   # a VPN owning the default route doesn't fool it
        self.assertEqual(picked, [{"ip": "192.168.1.42", "name": "Wi-Fi"}])

    def test_wired_and_wireless_both_shown_best_first(self):
        both = ADAPTERS + [{"ip": "192.168.1.30", "name": "Ethernet 2", "description": "Intel(R) Ethernet I225-V",
                            "physical": True, "gateway": True}]
        self.assertEqual([a["ip"] for a in lan.pick(both, default_ip="192.168.1.30")], ["192.168.1.30", "192.168.1.42"])

    def test_falls_back_to_the_route_windows_uses(self):
        self.assertEqual(lan.pick(ADAPTERS[:5], default_ip="192.168.0.9"), [{"ip": "192.168.0.9", "name": ""}])
        self.assertEqual(lan.pick(ADAPTERS[:5], default_ip="8.8.4.4"), [])   # never a public address
        self.assertEqual(lan.pick([], default_ip=None), [])

    def test_only_private_ipv4(self):
        self.assertTrue(lan.home_ip("192.168.1.2"))
        self.assertTrue(lan.home_ip("10.1.2.3"))
        for bad in ("127.0.0.1", "169.254.1.1", "8.8.8.8", "100.64.1.1", "fe80::1", "", None, "nonsense"):
            self.assertFalse(lan.home_ip(bad), bad)

    def test_powershell_trouble_falls_back_to_the_pc_name(self):
        lan._cache.update(at=0.0, adapters=None)
        with mock.patch.object(lan.os, "name", "nt"), \
                mock.patch.object(lan.subprocess, "run", return_value=mock.Mock(stdout="not json")), \
                mock.patch.object(lan.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.42", 0))]):
            self.assertEqual(lan.windows_adapters()[0]["ip"], "192.168.1.42")
        with mock.patch.object(lan.subprocess, "run") as run:   # cached for a while: PowerShell is slow to start
            lan.windows_adapters()
            run.assert_not_called()
        lan._cache.update(at=0.0, adapters=None)

    def test_an_old_answer_comes_back_at_once_and_refreshes_behind_the_scenes(self):
        old = [{"ip": "192.168.1.42", "name": "Wi-Fi", "description": "", "physical": True, "gateway": True}]
        lan._cache.update(at=time.time() - 120, adapters=old, refreshing=False)
        slow = mock.Mock(side_effect=lambda: (time.sleep(0.3), [dict(old[0], ip="192.168.1.43")])[1])
        with mock.patch.object(lan, "_ask_windows", slow):
            t0 = time.time()
            self.assertEqual(lan.windows_adapters(), old)          # no waiting on PowerShell
            self.assertLess(time.time() - t0, 0.2)
            for _ in range(50):
                if lan._cache["adapters"][0]["ip"] == "192.168.1.43":
                    break
                time.sleep(0.05)
        self.assertEqual(lan.windows_adapters()[0]["ip"], "192.168.1.43")
        self.assertEqual(slow.call_count, 1)
        lan._cache.update(at=0.0, adapters=None)

    def test_one_adapter_from_powershell_is_still_a_list(self):
        lan._cache.update(at=0.0, adapters=None)
        one = json.dumps({"ip": "192.168.1.42", "name": "Wi-Fi", "description": "", "physical": True, "gateway": True})
        with mock.patch.object(lan.os, "name", "nt"), \
                mock.patch.object(lan.subprocess, "run", return_value=mock.Mock(stdout=one)):
            self.assertEqual([a["ip"] for a in lan.windows_adapters()], ["192.168.1.42"])
        lan._cache.update(at=0.0, adapters=None)


class QrTests(unittest.TestCase):
    def test_svg_draws_every_dark_module(self):
        url = "http://192.168.1.42:8765/"
        svg = lan.qr_svg(url)
        qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
        n = qr.get_size()
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertIn(f'viewBox="0 0 {n + 6} {n + 6}"', svg)
        self.assertEqual(svg.count("h1v1h-1z"), sum(qr.get_module(x, y) for y in range(n) for x in range(n)))
        self.assertIn('fill="#fff"', svg)   # always dark on white, even in the dark theme


class PhoneSwitchTests(unittest.TestCase):
    """The real request handler on a spare port."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

        class PhoneHandler(server.Handler):   # every request "comes from" a phone on the Wi-Fi
            def client_ip(self):
                return ipaddress.ip_address("192.168.1.50")
        cls.phone = ThreadingHTTPServer(("127.0.0.1", 0), PhoneHandler)
        threading.Thread(target=cls.phone.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for h in (cls.httpd, cls.phone):
            h.shutdown()
            h.server_close()

    def setUp(self):
        write_json(CONFIG, {"allow_lan": False})
        lan._cache.update(at=time.time(), adapters=[ADAPTERS[6]])   # no PowerShell in tests

    def tearDown(self):
        CONFIG.unlink(missing_ok=True)
        lan._cache.update(at=0.0, adapters=None)

    def request(self, method, path, body=None, headers=None, port=None):
        port = port or self.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=None if body is None else json.dumps(body),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", **(headers or {})})
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {}

    def test_turning_it_on_saves_and_restarts_the_app(self):
        fake = mock.Mock()
        with mock.patch.object(server, "_httpd", fake):
            status, data = self.request("POST", "/api/lan", {"on": True})
            self.assertEqual((status, data), (200, {"ok": True, "restarting": True}))
            self.assertTrue(load_config()["allow_lan"])
            time.sleep(0.8)
            fake.shutdown.assert_called_once()   # the launcher starts the app again on 0.0.0.0
            status, data = self.request("POST", "/api/lan", {"on": True})
            self.assertEqual(data, {"ok": True, "restarting": False})   # already on: nothing to restart
        status, data = self.request("GET", "/api/lan")
        self.assertEqual((data["on"], data["can_change"], data["url"]), (True, True, "http://192.168.1.42:8765/"))   # the port phones use
        self.assertTrue(data["qr"].startswith("<svg"))

    def test_off_shows_no_address(self):
        status, data = self.request("GET", "/api/lan")
        self.assertEqual((status, data["on"], data["url"], data["qr"]), (200, False, None, None))

    def test_a_phone_cannot_flip_it(self):
        write_json(CONFIG, {"allow_lan": True})
        status, data = self.request("POST", "/api/lan", {"on": False}, port=self.phone.server_address[1])
        self.assertEqual(status, 403)
        self.assertIn("on the PC", data["error"])
        self.assertTrue(load_config()["allow_lan"])
        status, data = self.request("GET", "/api/lan", port=self.phone.server_address[1])
        self.assertEqual((status, data["on"], data["can_change"]), (200, True, False))   # it can see the address
        write_json(CONFIG, {"allow_lan": False})   # off: a phone gets nothing at all
        self.assertEqual(self.request("GET", "/api/lan", port=self.phone.server_address[1])[0], 403)

    def test_other_websites_and_bad_requests_are_refused(self):
        status, _ = self.request("POST", "/api/lan", {"on": True}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/lan", {"on": True}, {"Content-Type": "text/plain"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("POST", "/api/lan", {"on": "yes"})[0], 400)
        self.assertFalse(load_config()["allow_lan"])

    def test_the_settings_form_cannot_change_it(self):
        status, data = self.request("POST", "/api/config", {"allow_lan": True})
        self.assertEqual(status, 400)
        self.assertFalse(load_config()["allow_lan"])


@unittest.skipUnless(shutil.which("powershell"), "needs Windows PowerShell")
class SetupScriptTests(unittest.TestCase):
    def test_setup_and_installer_parse_and_take_the_switches(self):
        files = ", ".join(f"'{REPO / name}'" for name in ("scripts/setup.ps1", "install.ps1"))
        check = ("$t = $null; $e = $null; foreach ($f in @(" + files + ")) { $ast = [System.Management.Automation."
                 "Language.Parser]::ParseFile($f, [ref]$t, [ref]$e); $p = @($ast.ParamBlock.Parameters | ForEach-Object "
                 "{ $_.Name.VariablePath.UserPath }) -join ','; \"$($e.Count)|$p\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", check],
                             capture_output=True, text=True, timeout=60).stdout.split()
        setup, install = out[0].split("|"), out[1].split("|")
        self.assertEqual((setup[0], install[0]), ("0", "0"))
        self.assertIn("AllowLan", setup[1].split(","))
        self.assertIn("NoLan", setup[1].split(","))
        text = (REPO / "scripts" / "setup.ps1").read_text(encoding="ascii")   # Windows PowerShell 5.1 reads ASCII safely
        self.assertIn("NotePropertyName allow_lan", text)   # honored on an existing config, like -Game
        self.assertIn("Settings > More", (REPO / "install.ps1").read_text(encoding="ascii"))


def tearDownModule():
    shutil.rmtree(_home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
