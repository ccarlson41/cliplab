"""Tests for refusing POSTs that another website sends to ClipLab through your browser.

They run the app on a spare port with a throwaway ClipLab home, never your real one.
"""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

os.environ.setdefault("CLIPLAB_HOME", tempfile.mkdtemp(prefix="cliplab-test-"))

from cliplab import server  # noqa: E402

JSON = "application/json"


class CrossSiteTests(unittest.TestCase):
    def check(self, **headers):
        return server.cross_site({k.replace("_", "-"): v for k, v in headers.items()})

    def test_own_page_on_this_pc(self):
        self.assertIsNone(self.check(Content_Type=JSON, Host="localhost:8765", Origin="http://localhost:8765"))
        self.assertIsNone(self.check(Content_Type=JSON, Host="127.0.0.1:8765", Origin="http://127.0.0.1:8765"))
        self.assertIsNone(self.check(Content_Type=JSON, Host="[::1]:8765", Origin="http://[::1]:8765"))

    def test_own_page_on_a_phone_with_allow_lan(self):
        self.assertIsNone(self.check(Content_Type=JSON, Host="192.168.1.20:8765", Origin="http://192.168.1.20:8765"))

    def test_charset_and_case_dont_matter(self):
        self.assertIsNone(self.check(Content_Type="Application/JSON; charset=utf-8", Host="LocalHost:8765",
                                     Origin="http://localhost:8765"))

    def test_referer_stands_in_for_a_missing_origin(self):
        self.assertIsNone(self.check(Content_Type=JSON, Host="localhost:8765", Referer="http://localhost:8765/#x"))
        self.assertTrue(self.check(Content_Type=JSON, Host="localhost:8765", Referer="https://evil.example/page"))

    def test_scripts_without_origin_or_referer(self):
        self.assertIsNone(self.check(Content_Type=JSON, Host="localhost:8765"))

    def test_other_websites_are_refused(self):
        self.assertTrue(self.check(Content_Type=JSON, Host="localhost:8765", Origin="https://evil.example"))
        self.assertTrue(self.check(Content_Type=JSON, Host="localhost:8765", Origin="null"))
        self.assertTrue(self.check(Content_Type=JSON, Host="localhost:8765", Origin="http://localhost:9999"))
        self.assertTrue(self.check(Content_Type=JSON, Host="localhost:8765", Origin="http://localhost:8765.evil.example"))
        self.assertTrue(self.check(Content_Type=JSON, Origin="http://localhost:8765"))   # no Host to match

    def test_needs_a_json_content_type(self):
        for kind in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
                     "text/plain; application/json"):
            headers = {"Host": "localhost:8765", "Origin": "http://localhost:8765"}
            if kind:
                headers["Content-Type"] = kind
            self.assertTrue(server.cross_site(headers), kind)


class PostTests(unittest.TestCase):
    """The real request handler: refused requests never reach the routes."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, headers, body=b'{"key": "nope", "decision": "toss"}'):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/decide", body=body, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, data

    def test_app_page_gets_through(self):
        status, data = self.post({"Content-Type": JSON, "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 400)   # reached /api/decide, which doesn't know this clip
        self.assertIn("known clip", data["error"])

    def test_no_cors_text_plain_is_refused(self):
        status, data = self.post({"Content-Type": "text/plain;charset=UTF-8", "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("JSON", data["error"])

    def test_other_origin_is_refused(self):
        status, data = self.post({"Content-Type": JSON, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("own page", data["error"])


if __name__ == "__main__":
    unittest.main()
