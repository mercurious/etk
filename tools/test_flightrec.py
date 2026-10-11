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

print("\n[4] PMIC reset reason (harvest_pon)")
# car8 PM8150 (SID 0) after an operator cold boot, read live 2026-10-11.
CAR8_COLD = {0x804: 0x01, 0x805: 0x04, 0x8C0: 0x81, 0x8C4: 0x80, 0x8C5: 0x02,
             0x8C7: 0x80}


def dump(vals, lo=0x0000, hi=0x10000):
    return "".join(f"{a:04x}: {vals.get(a, 0):02x}\n" for a in range(lo, hi))


regs = bb.parse_regmap_dump(dump(CAR8_COLD, 0x800, 0x8E0))
check("dump parsed by address", (regs[0x8C0], regs[0x8C5]), (0x81, 0x02))
holey = bb.parse_regmap_dump("08c0: 81\ngarbage\n08c5: 02\n")
check("a hole or junk line never shifts addresses", holey, {0x8C0: 0x81, 0x8C5: 0x02})
cold = bb.decode_pon(regs)
check("cold boot decodes pon_reason bits",
      "pon_reason=81(HARD_RESET+KPDPWR)" in cold, True)
check("zero registers listed for diffing", "warm_reset=00" in cold and "fault1=00" in cold, True)
check("power-key boot verdict", bb.pon_verdict(["0-00 " + cold]), "COLD")
# car8 after the operator's reboot, 2026-10-11 15:45 (same window, pon_reason 0x01)
soc = bb.decode_pon(bb.parse_regmap_dump(dump({**CAR8_COLD, 0x8C0: 0x01}, 0x800, 0x8E0)))
check("HARD_RESET without the power key -> SOC_RESET (reboot / panic path)",
      bb.pon_verdict(["0-00 " + soc, "0-02 " + cold]), "SOC_RESET")
check("not a PON peripheral -> None", bb.decode_pon({0x804: 0x2C}), None)
warm = bb.decode_pon(bb.parse_regmap_dump(dump({**CAR8_COLD, 0x8C2: 0x02}, 0x800, 0x8E0)))
check("PS_HOLD warm reset -> WARM", bb.pon_verdict(["0-00 " + warm]), "WARM")
uvlo = bb.decode_pon(bb.parse_regmap_dump(dump({**CAR8_COLD, 0x8C8: 0x40}, 0x800, 0x8E0)))
check("UVLO fault -> BROWNOUT (outranks warm)",
      bb.pon_verdict(["0-00 " + warm, "0-02 " + uvlo]), "BROWNOUT")
otst = bb.decode_pon(bb.parse_regmap_dump(dump({**CAR8_COLD, 0x8C9: 0x80}, 0x800, 0x8E0)))
check("OTST3 -> OVERTEMP", bb.pon_verdict(["0-00 " + otst]), "OVERTEMP")

import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    maps = os.path.join(tmp, "regmap")
    for sid, vals in (("0-00", {**CAR8_COLD, 0x8C2: 0x02}), ("0-01", {})):
        os.makedirs(os.path.join(maps, sid))
        with open(os.path.join(maps, sid, "name"), "w") as f:
            f.write("pmic-spmi\n")
        with open(os.path.join(maps, sid, "registers"), "w") as f:
            f.write(dump(vals))
    os.makedirs(os.path.join(maps, "9200000.llcc"))
    bb.BB_DIR = os.path.join(tmp, "bb")
    os.makedirs(bb.BB_DIR)
    bb.PON_REGMAP_GLOB = os.path.join(maps, "*")
    bb.TRIPWIRE_LOG = os.path.join(tmp, "tripwire.log")
    bb._boot_id = lambda: "boot-A"
    bb.harvest_pon()
    pons = [p for p in os.listdir(bb.BB_DIR) if p.startswith("pon-")]
    check("one pon-<epoch>.txt written", len(pons), 1)
    body = open(os.path.join(bb.BB_DIR, pons[0])).read() if pons else ""
    check("file carries boot_id and verdict", "boot_id=boot-A" in body and "verdict=WARM" in body, True)
    check("seek-read of the PON window found SID 0 only (SID 1 TYPE != PON)",
          [ln.split()[0] for ln in body.splitlines() if "subtype=" in ln], ["0-00"])
    check("raw bytes kept beside the decode", "0-00 raw 8c0-8cf: 81 00 02" in body, True)
    check("tripwire line written", "PON last reset = WARM" in open(bb.TRIPWIRE_LOG).read(), True)
    bb.harvest_pon()
    check("same boot (install-time restart) -> no second file",
          len([p for p in os.listdir(bb.BB_DIR) if p.startswith("pon-")]), 1)
    bb.PON_REGMAP_GLOB = os.path.join(tmp, "absent", "*")
    bb._boot_id = lambda: "boot-B"
    bb.harvest_pon()
    check("no debugfs -> nothing written, no raise",
          len([p for p in os.listdir(bb.BB_DIR) if p.startswith("pon-")]), 1)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL FLIGHTREC CHECKS PASSED")
