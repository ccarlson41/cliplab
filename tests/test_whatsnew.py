"""Tests for What's new: the changelog the app's pop-up and patch history show, the version, where Request a
feature sends people, and the files that let ClipLab be installed as an app.

They run the app on a spare port with a throwaway ClipLab home, never your real one.
"""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer

os.environ["CLIPLAB_HOME"] = tempfile.mkdtemp(prefix="cliplab-test-")   # never your real ClipLab home

from cliplab import __version__, server  # noqa: E402
from cliplab.settings import CONFIG, HOME, WEB, load_config, web_address, write_json  # noqa: E402

ACTIONS = {"install", "request", "history"}   # the buttons a highlight can carry (ACTIONS in index.html)
VERSION = re.compile(r"\d+\.\d+\.\d+$")


def as_tuple(version):
    return tuple(int(n) for n in version.split("."))


class ChangelogTests(unittest.TestCase):
    """Every release needs a What's new entry, newest first, and the newest one is this version."""

    releases = json.loads(server.CHANGELOG.read_text(encoding="utf-8"))

    def test_newest_entry_is_this_version(self):
        self.assertTrue(VERSION.match(__version__))
        self.assertEqual(self.releases[0]["version"], __version__,
                         "Bumped __version__? Add its What's new entry to the top of cliplab/changelog.json.")

    def test_newest_first_and_no_repeats(self):
        versions = [as_tuple(r["version"]) for r in self.releases]
        self.assertEqual(versions, sorted(set(versions), reverse=True))
        dates = [date.fromisoformat(r["date"]) for r in self.releases]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_each_release_is_complete(self):
        for r in self.releases:
            with self.subTest(r["version"]):
                self.assertEqual(set(r), {"version", "date", "title", "highlights"})
                self.assertTrue(VERSION.match(r["version"]))
                self.assertTrue(r["title"].strip())
                self.assertTrue(1 <= len(r["highlights"]) <= 8, "Give each release 1 to 8 highlights.")
                for h in r["highlights"]:
                    self.assertTrue(set(h) <= {"icon", "headline", "text", "action"}, h)
                    self.assertTrue(h["icon"].strip() and len(h["icon"]) <= 4, h)   # one emoji
                    self.assertTrue(h["headline"].strip() and h["text"].strip(), h)
                    self.assertTrue(len(h["headline"]) <= 40, h["headline"])
                    self.assertIn(h.get("action", "install"), ACTIONS)


class FeedbackUrlTests(unittest.TestCase):
    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def test_keeps_plain_web_addresses(self):
        for url in ("https://forms.gle/abc123", "http://192.168.1.20:9000/ideas",
                    "https://docs.google.com/forms/d/e/x/viewform?usp=pp_url&entry.1={what}&entry.2={why}"):
            self.assertEqual(web_address(url), url)
        self.assertEqual(web_address("  https://forms.gle/abc  "), "https://forms.gle/abc")

    def test_anything_else_means_the_default(self):
        for bad in ("", "forms.gle/abc", "javascript:alert(1)", "file:///C:/x.html", "ftp://example.com/x",
                    "https://", "http://[::1", "https://example.com/a b", "https://example.com/\nx",
                    "https://example.com/" + "x" * 2000, None, 42, ["https://x.example"]):
            self.assertEqual(web_address(bad), "", repr(bad))

    def test_config_falls_back_to_github(self):
        self.assertEqual(load_config()["feedback_url"], "")
        info = server.whats_new()
        self.assertEqual(info["feedback_to"], "github")
        self.assertTrue(info["feedback_url"].startswith("https://github.com/ccarlson41/cliplab/issues/new?"))
        for blank in ("template=feature_request.yml", "labels=feature-request", "{title}", "{what}", "{why}",
                      "{name}", "{version}"):
            self.assertIn(blank, info["feedback_url"])

        write_json(CONFIG, {"feedback_url": "javascript:alert(1)"})
        self.assertEqual(server.whats_new()["feedback_to"], "github")

        write_json(CONFIG, {"feedback_url": "https://forms.gle/abc123"})
        info = server.whats_new()
        self.assertEqual((info["feedback_to"], info["feedback_url"]), ("custom", "https://forms.gle/abc123"))

    def test_github_form_matches_the_issue_template(self):
        template = (WEB.parent.parent / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml").read_text(encoding="utf-8")
        for field in re.findall(r"[?&](\w+)=\{\w+\}", server.FEEDBACK_GITHUB):
            if field != "title":
                self.assertIn(f"id: {field}\n", template)
        self.assertIn('labels: ["feature-request"]', template)
        self.assertIn('title: "[Feature] "', template)


class AppTests(unittest.TestCase):
    """The real request handler."""

    @classmethod
    def setUpClass(cls):
        HOME.mkdir(parents=True, exist_ok=True)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def tearDown(self):
        CONFIG.unlink(missing_ok=True)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body and json.dumps(body),
                     headers={"Content-Type": "application/json"} if body is not None else {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type"), data

    def test_changelog_and_version_are_served(self):
        status, kind, data = self.request("GET", "/api/changelog")
        self.assertEqual((status, kind), (200, "application/json"))
        info = json.loads(data)
        self.assertEqual(info["version"], __version__)
        self.assertEqual(info["releases"], ChangelogTests.releases)
        self.assertEqual(info["feedback_to"], "github")
        status, _, data = self.request("GET", "/api/state")
        self.assertEqual(json.loads(data)["version"], __version__)
        self.assertNotIn("feedback_url", json.loads(data)["config"])   # set by hand only, sent by /api/changelog

    def test_feedback_url_is_not_editable_from_the_app(self):
        status, _, data = self.request("POST", "/api/config", {"feedback_url": "https://evil.example"})
        self.assertEqual(status, 400)
        self.assertIn("Unknown setting", json.loads(data)["error"])

    def test_install_files_are_served(self):
        manifest = json.loads(self.request("GET", "/manifest.webmanifest")[2])
        self.assertNotIn("laptop", manifest["description"])
        sizes = {(i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes)
        page = self.request("GET", "/")[2].decode("utf-8")
        paths = [i["src"] for i in manifest["icons"]] + re.findall(r'rel="apple-touch-icon" href="([^"]+)"', page)
        self.assertIn("/apple-touch-icon.png", paths)
        for path in paths:
            status, kind, data = self.request("GET", path)
            self.assertEqual(status, 200, path)
            if path.endswith(".png"):
                self.assertEqual(kind, "image/png")
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                want = re.search(r"(\d+)", path.rsplit("-", 1)[-1])
                size = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
                self.assertEqual(size, (int(want.group(1)),) * 2 if want else (180, 180), path)

    def test_only_known_web_files_are_served(self):
        for path in ("/index.html.bak", "/../settings.py", "/changelog.json", "/web/icon.svg"):
            self.assertEqual(self.request("GET", path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
