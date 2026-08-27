#!/usr/bin/env python3
"""ETK — host-side tests for the blackbox flight recorder's pure helpers.

Run from the repo root:   python3 tools/test_flightrec.py

The flight recorder exists because the fast PANIC class (2026-08-27 census)
dies without a single kmsg line: the resource curve is the only witness a
silent instant death can leave. The sampler itself needs the rig; these
tests pin the PARSERS against real SM8250-shaped /proc content so a kernel
that formats a field oddly (or lacks PSI) degrades to sentinel values
instead of a raised exception inside the daemon.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "blackbox_d", os.path.join(HERE, os.pardir, "bin", "blackbox_d.py"))
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}")
        print(f"          want {want!r}")
        FAILS.append(desc)


MEMINFO = """MemTotal:        7645768 kB
MemFree:          201304 kB
MemAvailable:    3418732 kB
Buffers:            4184 kB
Cached:          3348828 kB
SwapTotal:       3822880 kB
SwapFree:        3524608 kB
"""

PSI_MEM = """some avg10=12.48 avg60=8.11 avg300=2.90 total=182734455
full avg10=4.02 avg60=2.55 avg300=0.91 total=64183921
"""

PSI_CPU = """some avg10=63.77 avg60=51.20 avg300=44.06 total=9127346518
"""

print("[1] parse_meminfo")
m = bb.parse_meminfo(MEMINFO)
check("MemAvailable parsed (kB)", m["MemAvailable"], 3418732)
check("SwapFree parsed (kB)", m["SwapFree"], 3524608)
check("empty content -> zeros", bb.parse_meminfo(""),
      {"MemAvailable": 0, "SwapFree": 0})
check("garbage value -> stays 0",
      bb.parse_meminfo("MemAvailable: lots kB")["MemAvailable"], 0)

print("\n[2] parse_psi")
check("memory some avg10", bb.parse_psi(PSI_MEM, "some"), 12.48)
check("memory full avg10", bb.parse_psi(PSI_MEM, "full"), 4.02)
check("cpu some avg10", bb.parse_psi(PSI_CPU, "some"), 63.77)
check("cpu has no 'full' line -> -1.0", bb.parse_psi(PSI_CPU, "full"), -1.0)
check("PSI absent (CONFIG_PSI off) -> -1.0", bb.parse_psi("", "some"), -1.0)

print("\n[3] flightrec_row")
row = bb.flightrec_row(1787900000.7, "BCUS98114", m, 12.48, 4.02, 63.77,
                       0.55, 7.91, 74, 587)
cols = row.split("\t")
check("11 columns, matching the header",
      len(cols), len(bb.FLIGHTREC_HEADER.lstrip("# ").split("\t")))
check("epoch truncated to int seconds", cols[0], "1787900000")
check("game id in col 2", cols[1], "BCUS98114")
check("idle sample writes '-' for game",
      bb.flightrec_row(1, "", m, 0, 0, 0, 0, 0, 0, 0).split("\t")[1], "-")
check("temps and MHz are ints", (cols[9], cols[10]), ("74", "587"))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL FLIGHTREC CHECKS PASSED")
