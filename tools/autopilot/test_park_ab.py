#!/usr/bin/env python3
"""Host tests for park_ab.py: the perfstat parse, the window math, the arm.

  python3 tools/autopilot/test_park_ab.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import park_ab as A  # noqa: E402

LOG = """ep=100 fps=30.0 ft_ms=33.30 cpu=70.0 ppu=20.0 spu=40.0 rsx=10.0 rsxload=50 thr=40 sq=10 sw=100 pk=50 pf=0 av=0 fr=0
garbage line
ep=101 fps=28.0 ft_ms=35.70 cpu=72.0 ppu=21.0 spu=41.0 rsx=11.0 rsxload=51 thr=40 sq=10 sw=100 pk=50 pf=10 av=0 fr=0
ep=102 fps=20.0 ft_ms=50.00 cpu=75.0 ppu=22.0 spu=42.0 rsx=12.0 rsxload=52 thr=40 sq=0 sw=0 pk=0 pf=0 av=0 fr=0
ep=150 fps=99.0 ft_ms=10.00 cpu=1.0 ppu=1.0 spu=1.0 rsx=1.0 rsxload=1 thr=1 sq=9 sw=9 pk=9 pf=9 av=0 fr=0
ep=103 fps=30.0
"""


class Parse(unittest.TestCase):
    def test_rows(self):
        rows = A.parse_perf(LOG)
        self.assertEqual([r["ep"] for r in rows], [100, 101, 102, 150])  # short + garbage lines skipped
        self.assertEqual(rows[1]["pf"], 10.0)

    def test_window(self):
        s = A.summarize(A.parse_perf(LOG), 100, 102)
        self.assertEqual(s["intervals"], 3)            # ep=150 is outside the window
        self.assertEqual(s["fps_med"], 28.0)
        self.assertEqual(s["ft_ms_p95"], 50.0)
        self.assertEqual(s["pk_per_s"], 50.0)          # 100 parks / 2 s
        # (100 parks x 200 us + 10 flip parks x 100 us) / 2 s = 0.0105 cores
        self.assertEqual(s["parked_cores"], 0.0105)

    def test_empty_window(self):
        self.assertEqual(A.summarize([], 0, 10), {"intervals": 0, "window_s": 10})


class Arm(unittest.TestCase):
    REL = "RPCS3 v0.0.42 | GTK Edition v0.10.0 (armsx3-8290349e5)"
    NOPARK = "RPCS3 v0.0.42 | GTK Edition v0.10.0-hunt-rr7-nopark (armsx3-8290349e5)"

    def test_release_core(self):
        self.assertEqual(A.arm_of(self.REL, {}), "on")
        self.assertEqual(A.arm_of(self.REL, {"GTK_SEMA_PARK": "0"}), "off")
        self.assertEqual(A.arm_of(self.REL, {"GTK_SEMA_PARK": "yes"}), "on")

    def test_nopark_core(self):
        self.assertEqual(A.arm_of(self.NOPARK, {}), "off")
        self.assertEqual(A.arm_of(self.NOPARK, {"GTK_SEMA_PARK": "1"}), "on")
        self.assertEqual(A.arm_of(self.NOPARK, {"GTK_SEMA_PARK": "yes"}), "off")


if __name__ == "__main__":
    unittest.main()
