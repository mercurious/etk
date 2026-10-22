#!/usr/bin/env python3
"""Host tests for etk_dyno.py's 2026-10-22 RC additions: --by core, --game A,B, the fps columns
and the PERFECT% caveat -- on a fixture ledger (no rig; the real ledger is never read).

WHY: the 0.10.0 RC verdict (release plan §4.7) is "0.10.0 vs 0.9.0.3 on GT5P, N=3 each": two
arms that differ ONLY in core=. The dyno's default key is the full tune_tag x res x clk x pwr,
so the two arms print as two S-ids with "!! more than one stack present" even when the stack is
the same -- and it has no view that pools NPEA00050 + BCUS98158 (one engine). PERFECT% is its
headline KPI and reads 0-4 % on every GT5P arm (a 30 fps title under a 60 fps window test): a
reader who ranks by it ranks noise.

HOW: a 31-column ledger in the postmortem's layout with 3 RC rows (core=0.10.0, pwr=race,
BCUS98158), 3 control rows (core=0.9.0.3, same build/stack/pwr), one NPEA00050 RC row, one RC
row on another kernel build (stack differs), one bake row (shd 300), one ABORTED row, one
60 fps title (fps_med 59) -- the dyno is run as a subprocess and its stdout pinned.

DISCRIMINATION: `--against REV` runs `git show REV:tools/etk_dyno.py` instead (the dyno has no
relative imports). Against 456b078: --by / a comma --game are argparse errors, no fps columns,
no caveat -> [2]-[4] FAIL; [1] (default view unchanged) still passes.

  python3 tools/test_etk_dyno.py                  # working tree -- must PASS
  python3 tools/test_etk_dyno.py --against HEAD   # pre-change -- [2]-[4] FAIL
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DYNO = os.path.join(HERE, "etk_dyno.py")
if len(sys.argv) >= 3 and sys.argv[1] == "--against":
    DYNO = os.path.join(tempfile.mkdtemp(prefix="dyno-against-"), "etk_dyno.py")
    with open(DYNO, "w") as f:
        f.write(subprocess.run(["git", "-C", HERE, "show", f"{sys.argv[2]}:tools/etk_dyno.py"],
                               capture_output=True, text=True, check=True).stdout)
    print(f"against {sys.argv[2]}: {DYNO}")
FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


HEADER = ("epoch\tduration_s\tbuild\tgame_id\tstatus\tpeak_load\tpeak_ram_mb\tpeak_temp\tavg_temp\tcrash_sig\t"
          "fence_at_crash\tshaders_harvested\tdrain_pct\tthermal_overrides\ttune_tag\tcrash_shot\tfps_med\tfps_1low\t"
          "ft_p99_ms\tres_scale\tgpu_mhz\tpwr\tft_jitter_ms\tgpu_fault_status\tgpu_fault_fence_hex\taud\tsnd\tlock_pct\t"
          "perfect_pct\trescues\tperf")
STACK = "build=26.2.2_gtk_0.7;stack=rkc445081/k7.2.0#9/r0.10.0"
RC = STACK + ";core=0.10.0_armsx3-8290349e5;default"
CTL = STACK + ";core=0.9.0.3_armsx3-a74a0f3e0;default"
RC_K8 = "build=26.2.2_gtk_0.7;stack=rkc445081/k7.2.0#8/r0.10.0;core=0.10.0_armsx3-8290349e5;default"


def row(epoch, dur, game, status, tag, fps="28.0", low="11.0", perfect="1.5", pwr="race", shd="2", resc="0"):
    return "\t".join([str(epoch), str(dur), "FULL", game, status, "8.0", "3000", "75", "66", "", "0", shd, "-1", "0",
                      tag, "-", fps, low, "80.0", "100", "925", pwr, "3.0", "-", "-", "-", "ok", "40.0", perfect,
                      resc, "-"])


T = 1_800_000_000
LEDGER = "\n".join([
    HEADER,
    row(T + 1, 900, "BCUS98158", "CLEAN", RC, fps="29.0", perfect="2.0"),
    row(T + 2, 700, "BCUS98158", "SURVIVED:Adreno", RC, fps="27.5", perfect="1.0", resc="1"),
    row(T + 3, 1100, "BCUS98158", "CLEAN", RC, fps="30.1", perfect="3.0"),
    row(T + 4, 600, "BCUS98158", "CLEAN", CTL, fps="28.0", perfect="1.5"),
    row(T + 5, 300, "BCUS98158", "RECOVERY:Silent", CTL, fps="26.0", perfect="0.5"),
    row(T + 6, 800, "BCUS98158", "CLEAN", CTL, fps="28.8", perfect="2.5"),
    row(T + 7, 850, "NPEA00050", "CLEAN", RC, fps="28.2", perfect="1.0"),     # same engine, PSN copy
    row(T + 8, 500, "BCUS98158", "CLEAN", RC_K8, fps="28.0", perfect="1.0"),  # another kernel build
    row(T + 9, 2000, "BCUS98158", "CLEAN", RC, shd="300"),                     # bake: excluded
    row(T + 10, 20, "BCUS98158", "ABORTED", RC),                               # excluded
    row(T + 11, 700, "BLUS30767", "CLEAN", RC, fps="59.5", low="55.0", perfect="88.0"),  # a 60 fps title
]) + "\n"
LP = os.path.join(tempfile.mkdtemp(prefix="dyno-"), "sessions.tsv")
with open(LP, "w") as f:
    f.write(LEDGER)


def dyno(*args):
    r = subprocess.run([sys.executable, DYNO, "--ledger", LP, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


print("[1] default view unchanged: full tune_tag key, two S-ids, the refusal line")
rc, out = dyno("--game", "BCUS98158")
check("exit 0", rc, 0)
check("two stacks listed, comparison refused across them", ("S1" in out and "S2" in out, "more than one stack present" in out), (True, True))
check("RC and control are different arms (the core= is inside the tag)", out.count("| 100 | 925 | race"), 3)
check("bake and ABORTED rows excluded (RC arm N=3)", "  3 " in out and "2000s" not in out, True)

print("\n[2] --by core: one arm per core= token, kernel/turnip/pwr/titles as context")
rc, out = dyno("--game", "BCUS98158", "--by", "core")
check("exit 0 (an unknown --by would be argparse 2)", rc, 0)
lines = [ln for ln in out.splitlines() if ln.startswith(("0.10.0_", "0.9.0.3_"))]
check("two arms, keyed on core", [ln.split(" | ")[0] for ln in lines], ["0.10.0_armsx3-8290349e5", "0.9.0.3_armsx3-a74a0f3e0"])
rcl = next((ln for ln in lines if ln.startswith("0.10.0_")), "")
check("RC arm pools the k#8 row: N=4, `stack differs (2)` flagged, kernels named",
      ("  4 " in rcl, "stack differs (2)" in rcl, "k7.2.0#8,k7.2.0#9" in rcl), (True, True, True))
ctl = next((ln for ln in lines if ln.startswith("0.9.0.3_")), "")
check("control arm N=3 on one stack: no flag, CRASH 1/3", ("stack differs" in ctl, "1/3" in ctl), (False, True))
check("no STACKS refusal in core view", "more than one stack present" in out, False)

print("\n[3] --game A,B pools the GT5P PSN + ISO copies")
rc, out = dyno("--game", "NPEA00050,BCUS98158", "--by", "core")
rcl = next((ln for ln in out.splitlines() if ln.startswith("0.10.0_")), "")
check("exit 0, RC arm N=5 with both serials as context", (rc, "  5 " in rcl, "BCUS98158,NPEA00050" in rcl), (0, True, True))
rc, out = dyno("--game", "NPEA00050")
check("a single serial still filters alone", (rc, sum(ln.startswith("S1 |") for ln in out.splitlines())), (0, 1))

print("\n[4] fps columns beside PERFECT%, and the caveat only on a <45 fps arm")
rc, out = dyno("--game", "BCUS98158", "--by", "core")
check("header carries FPS med and 1%low after PERFECT%", "PERFECT% FPS med  1%low" in out, True)
check("the caveat names the KPI's limit and what to judge instead",
      ("PERFECT% is a 60 fps KPI" in out, "DUR p50/max, CRASH n/N, FPS med / 1%low" in out), (True, True))
rc, out = dyno("--game", "BLUS30767")
check("a 60 fps title (fps_med 59.5) prints no caveat", (rc, "60 fps KPI" in out, " 59.5 " in out), (0, False, True))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL ETK_DYNO CHECKS PASSED")
