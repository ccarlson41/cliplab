"""Tests for the trim editor's math: where a drag on the timeline lands, how far the handles and the window can go, and
the 0.1 s snapping. The math is plain JavaScript inside cliplab/web/index.html, so these run it in Node (skipped if
Node isn't installed).
"""
import shutil
import subprocess
import unittest

from cliplab.settings import WEB

START, END = "// trim math:", "// end trim math"

CHECKS = r"""
const assert = require("node:assert/strict");
const src = require("node:fs").readFileSync(0, "utf8");
const { snapTime, pxToTime, edgeLimits, setEdge, slideWin, nearestEdge, grab, TRIM_MIN, GRIP_OUT, GRIP_IN } = new Function(
  src + "; return { snapTime, pxToTime, edgeLimits, setEdge, slideWin, nearestEdge, grab, TRIM_MIN, GRIP_OUT, GRIP_IN };")();
const win = { start: 10, end: 20 }, dur = 60;

// snapping lands on tenths with no float noise
assert.equal(snapTime(12.44), 12.4); assert.equal(snapTime(12.46), 12.5); assert.equal(snapTime(0.30000000000000004), 0.3);

// pixels -> seconds, clamped to the clip
assert.equal(pxToTime(150, 100, 200, 60), 15);
assert.equal(pxToTime(50, 100, 200, 60), 0);
assert.equal(pxToTime(400, 100, 200, 60), 60);
assert.equal(pxToTime(150, 100, 0, 60), 0);   // a hidden timeline has no width

// one edge moves; it stops a second short of the other and inside the clip
assert.deepEqual(setEdge(win, "start", 12.44, dur), { start: 12.4, end: 20 });
assert.deepEqual(setEdge(win, "start", -5, dur), { start: 0, end: 20 });
assert.deepEqual(setEdge(win, "start", 19.7, dur), { start: 19, end: 20 });
assert.deepEqual(setEdge(win, "end", 10.2, dur), { start: 10, end: 11 });
assert.deepEqual(setEdge(win, "end", 75, dur), { start: 10, end: 60 });
assert.deepEqual(setEdge(win, "end", 63.47, 63.47), { start: 10, end: 63.47 });   // the clip's last frame, even off the grid
assert.deepEqual(setEdge({ start: 10, end: 20.345 }, "start", 19.9, dur), { start: 19.345, end: 20.345 });
assert.deepEqual(edgeLimits(win, "start", dur), [0, 19]);
assert.deepEqual(edgeLimits(win, "end", dur), [11, 60]);

// the window slides by tenths, keeps its length, and stops at either end of the clip
assert.deepEqual(slideWin(win, 3.04, dur), { start: 13, end: 23 });
assert.deepEqual(slideWin(win, -25, dur), { start: 0, end: 10 });
assert.deepEqual(slideWin(win, 100, dur), { start: 50, end: 60 });
assert.deepEqual(slideWin({ start: 12.345, end: 30.2 }, 0.1, dur), { start: 12.445, end: 30.3 });
const slid = slideWin({ start: 1.1, end: 2.3 }, 0.2, dur);
assert.equal(+(slid.end - slid.start).toFixed(9), 1.2);

// a tap on empty track moves the nearer handle
assert.equal(nearestEdge(win, 3), "start");
assert.equal(nearestEdge(win, 40), "end");
assert.equal(nearestEdge(win, 14), "start");
assert.equal(nearestEdge(win, 16), "end");

// what a press picks up, with the edges at 100 px and 200 px
assert.equal(grab(100, 100, 200), "start");
assert.equal(grab(100 - GRIP_OUT, 100, 200), "start");
assert.equal(grab(100 - GRIP_OUT - 1, 100, 200), "jump");
assert.equal(grab(100 + GRIP_IN, 100, 200), "start");
assert.equal(grab(100 + GRIP_IN + 1, 100, 200), "slide");
assert.equal(grab(150, 100, 200), "slide");
assert.equal(grab(200 - GRIP_IN, 100, 200), "end");
assert.equal(grab(200 + GRIP_OUT, 100, 200), "end");
assert.equal(grab(200 + GRIP_OUT + 1, 100, 200), "jump");
// a window only 4 px wide: each handle still owns its side, split at the middle
assert.equal(grab(101, 100, 104), "start");
assert.equal(grab(103, 100, 104), "end");
assert.ok(GRIP_OUT + GRIP_IN >= 24, "handles need a 24 px hit area for fingers");
console.log("ok");
"""


@unittest.skipUnless(shutil.which("node"), "Node isn't installed")
class TrimMathTests(unittest.TestCase):
    def test_trim_math(self):
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn(START, page)
        math = page[page.index(START):page.index(END)]
        run = subprocess.run(["node", "-e", CHECKS], input=math, capture_output=True, text=True, encoding="utf-8",
                             timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
