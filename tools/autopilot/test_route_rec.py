#!/usr/bin/env python3
"""route_rec regression tests (no rig, no pad): the car-reader line format, the edge -> step
rules (press ms clamp, hat, chords, stick holds), snaps per down, and the macro text.
Run from the repo root:   python3 tools/autopilot/test_route_rec.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from route_rec import Route, parse_line, EV_KEY, EV_ABS  # noqa: E402


class TestRouteRec(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(parse_line("abs event9 0 -32768 32767"), ("abs", "event9", 0, -32768, 32767))
        self.assertEqual(parse_line("ready event5 event9"), ("ready", "event5 event9"))
        self.assertEqual(parse_line("12.500000 event9 1 304 1"), ("ev", 12.5, "event9", 1, 304, 1))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("garbage line here"))

    def test_presses_and_macro(self):
        r = Route()
        downs = []
        r.on_down = lambda name, flip, idx: downs.append((idx, name, flip))
        r.feed(10.0, EV_KEY, 315, 1, 100)      # start down
        r.feed(10.15, EV_KEY, 315, 0, 104)     # up after 150 ms
        r.feed(16.0, EV_KEY, 304, 1, 280)      # cross: a 30 ms tap is clamped UP to 80
        r.feed(16.03, EV_KEY, 304, 0, 281)
        r.feed(20.0, EV_ABS, 17, 1, 400)       # hat down held 900 ms: clamped DOWN to 400 (auto-repeat)
        r.feed(20.9, EV_ABS, 17, 0, 427)
        r.feed(21.0, EV_ABS, 17, -1, 430)      # hat flips straight from down to up: two edges
        r.feed(21.1, EV_ABS, 17, 0, 433)
        self.assertEqual([d[1] for d in downs], ["start", "cross", "down", "up"])
        self.assertEqual(downs[0][2], 100)
        text = r.macro("hdr")
        self.assertEqual(text.splitlines(), [
            "# hdr",
            "press start 150", "snap 000_start",
            "sleep 6", "press cross 80", "snap 001_cross",
            "sleep 4", "press down 400", "snap 002_down",
            "sleep 0.5", "press up 100", "snap 003_up",
        ])

    def test_chord_and_stick(self):
        r = Route()
        r.set_abs(0, -32768, 32767)
        r.set_abs(2, 0, 255)
        r.feed(1.0, EV_KEY, 308, 1, 1)         # square + circle within 60 ms = one chord press
        r.feed(1.02, EV_KEY, 305, 1, 2)
        r.feed(1.2, EV_KEY, 308, 0, 8)
        r.feed(1.21, EV_KEY, 305, 0, 8)
        r.feed(3.0, EV_ABS, 0, -20000, 60)     # lx to -0.61, peak -0.9, back to centre after 0.5 s
        r.feed(3.2, EV_ABS, 0, -29500, 66)
        r.feed(3.5, EV_ABS, 0, 0, 75)
        r.feed(5.0, EV_ABS, 0, 5000, 100)      # inside the deadzone: logged, not a hold
        r.feed(5.1, EV_ABS, 0, 0, 103)
        lines = r.macro().splitlines()
        self.assertEqual(lines[0], "press square+circle 200")
        self.assertIn("hold lx=-0.9 secs=0.5", lines)
        self.assertEqual(sum(1 for ln in lines if ln.startswith("hold")), 1)
        kinds = [row[2] for row in r.rows]
        self.assertEqual(kinds.count("stick"), 5)
        self.assertEqual(len(r.snaps), 2)      # both downs got a snap

    def test_unsigned_axis_normalizes_about_the_middle(self):
        r = Route()
        r.set_abs(0, 0, 255)
        r.feed(0.0, EV_ABS, 0, 128, 1)
        self.assertAlmostEqual(r.rows[-1][4], 0.0, places=1)
        r.feed(0.1, EV_ABS, 0, 0, 2)
        self.assertEqual(r.rows[-1][4], -1.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
