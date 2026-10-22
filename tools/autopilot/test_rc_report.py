#!/usr/bin/env python3
"""Host tests for rc_report.py: the trial/ledger join, the N count, the three headlines, the
control-arm exemption, the surfaces parse -- on fake trial dirs + a fake ledger (no rig).

WHY: the RC report is the last surface before the cut (release plan §4.6: "a clean report = push
the cut commit"); a join that silently attaches a trial to the wrong row, or a headline that reads
PASS over a missing row, would certify on a diary entry.

HOW: a temp `state/rc/x/` with gt6-r1 (PASS, has a CLEAN row), gt5p-rc-r1..r3 (SURVIVED; r3's row
closed RECOVERY), gt5p-ctl-r1 (control: CRASH, must not fail the headline), rr7-r1 (control DESYNC),
plus a 31-col ledger with rows whose [epoch - duration_s, epoch] windows hold those launches, and
surfaces text with PASS/FAIL/WARN lines.

DISCRIMINATION: rc_report.py is new on 2026-10-22; there is no pre-change revision to run against
(`--against` would have nothing to show). The checks fail against an rc_report that ignores the
ledger (every row would read NONE), that counts N per trial dir instead of per (game, core) arm,
or that lets a control arm's CRASH set the headline.

  python3 tools/autopilot/test_rc_report.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rc_report as R  # noqa: E402

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
RC = "build=26.2.2_gtk_0.7;stack=rkc445081/k7.2.0#9/r0.10.0;core=0.10.0_armsx3-8290349e5;default"
CTL = "build=26.2.2_gtk_0.7;stack=rkc445081/k7.2.0#9/r0.10.0;core=0.9.0.3_armsx3-a74a0f3e0;default"


def row(end, dur, game, status, tag, fps="28.0", pwr="race", shd="2"):
    cols = [str(end), str(dur), "FULL", game, status, "8.0", "3000", "75", "66", "", "0", shd, "-1", "0", tag, "-",
            fps, "12.0", "80.0", "100", "925", pwr, "3.0", "-", "-", "-", "ok", "40.0", "1.0", "0", "-"]
    return "\t".join(cols)


def trial(root, name, tool, game, verdict, t0, secs, label="", **extra):
    d = os.path.join(root, name)
    os.makedirs(d)
    v = {"tool": tool, "game": game, "verdict": verdict, "label": label, "secs": secs,
         "t0_epoch": t0, "end_epoch": t0 + secs}
    v.update(extra)
    with open(os.path.join(d, "verdict.json"), "w") as f:
        json.dump(v, f)
    return d


def build(root, ledger_variant="full"):
    T = 1_800_000_000
    trial(root, "gt6-r1", "gt6_trial", "BCUS98296", "PASS", T, 400, "rc-0.10.0")
    trial(root, "gt5p-rc-r1", "gt5_trial", "BCUS98158", "SURVIVED", T + 1000, 800, "rc")
    trial(root, "gt5p-rc-r2", "gt5_trial", "BCUS98158", "SURVIVED", T + 2000, 800, "rc")
    trial(root, "gt5p-rc-r3", "gt5_trial", "BCUS98158", "SURVIVED", T + 3000, 800, "rc")
    trial(root, "gt5p-ctl-r1", "gt5_trial", "BCUS98158", "CRASH", T + 4000, 300, "rc-control", fault="VM: Access violation")
    trial(root, "rr7-r1", "rr7_trial", "NPUB30457", "DESYNC", T + 5000, 90, "rc-control")
    os.makedirs(os.path.join(root, "surfaces"))        # a dir without verdict.json is not a trial
    rows = [HEADER,
            row(T + 60 + 400, 400, "BCUS98296", "CLEAN", RC),                   # gt6: starts 60 s after launch
            row(T + 1000 + 30 + 800, 800, "BCUS98158", "CLEAN", RC),
            row(T + 2000 + 30 + 800, 800, "BCUS98158", "SURVIVED:Adreno", RC),
            row(T + 3000 + 30 + 800, 800, "BCUS98158", "RECOVERY:Silent", RC),  # r3's exit was a kill
            row(T + 4000 + 30 + 300, 300, "BCUS98158", "RECOVERY:Adreno", CTL),
            row(T + 5000 + 20 + 90, 90, "NPUB30457", "RECOVERY:Silent", RC),
            row(T - 90000, 600, "BCUS98158", "CLEAN", CTL)]                      # yesterday: must not join
    if ledger_variant == "no_r2":
        rows.pop(3)
    with open(os.path.join(root, "sessions.tsv"), "w") as f:
        f.write("\n".join(rows) + "\n")
    with open(os.path.join(root, "surfaces.txt"), "w") as f:
        f.write("# cold-boot surfaces\nabl_slot verify: ABL_SLOT_VERIFY PASS (10 checks)\n"
                "boot line ROCKNIX-GTK 0.10.0-20261001 Pit Wall: PASS\nPON verdict=COLD: PASS\n")
    return T


print("[1] parse / join / infer")
root = tempfile.mkdtemp(prefix="rcreport-")
T = build(root)
rows = R.parse_ledger(open(os.path.join(root, "sessions.tsv")).read())
check("7 rows parsed, core/build/stack split from tune_tag", (len(rows), rows[0]["_core"], rows[0]["_build"]),
      (7, "0.10.0_armsx3-8290349e5", "26.2.2_gtk_0.7"))
t = R.load_trial(os.path.join(root, "gt5p-rc-r2"))
check("trial window from the verdict", (t["t0"], t["t_end"], t["game"], t["control"]), (T + 2000, T + 2800, "BCUS98158", False))
check("control arm recognised by label / dir name",
      (R.load_trial(os.path.join(root, "gt5p-ctl-r1"))["control"], R.load_trial(os.path.join(root, "rr7-r1"))["control"]), (True, True))
j = R.join_row(t, rows)
check("joins the row whose window holds the launch (30 s after)", (j["status"], j["_epoch"]), ("SURVIVED:Adreno", T + 2830))
check("yesterday's row never joins", R.join_row(dict(t, t0=T - 50000), rows), None)
check("wrong game never joins", R.join_row(dict(t, game="NPEA00050"), rows), None)
old = {"label": "x", "verdict": "PASS", "secs": 100, "debug_env": {}}   # a pre-10-22 gt6 verdict: no tool/game/epochs
check("older verdicts infer the tool and game", (R.infer_tool(old), R.infer_tool({"hold": 150}), R.infer_tool({"boot_id": "a"})),
      ("gt6_trial", "rr7_trial", "gt5_trial"))
d = os.path.join(root, "old-gt6")
os.makedirs(d)
json.dump(old, open(os.path.join(d, "verdict.json"), "w"))
os.utime(os.path.join(d, "verdict.json"), (T + 9000, T + 9000))
lt = R.load_trial(d)
check("older verdicts fall back to the file mtime for the window", (lt["game"], lt["t_end"], lt["t0"]), ("BCUS98296", T + 9000, T + 8900))
import shutil
shutil.rmtree(d)
S = R.parse_surfaces("# c\nabl verify: PASS (10 checks)\nmdns: WARN renamed\nosguard: mismatch FAIL\nnote only\n", "s.txt")
check("surface verdict tokens", [(s["verdict"], s["evidence"]) for s in S],
      [("PASS", "s.txt:2"), ("WARN", "s.txt:3"), ("FAIL", "s.txt:4"), ("NOTED", "s.txt:5")])

print("\n[2] full matrix: r3's row closed RECOVERY -> INCONCLUSIVE, controls exempt")
rp = os.path.join(root, "report.md")
code = R.main(["--out", root, "--surfaces", os.path.join(root, "surfaces.txt"), "--expect-core", "0.10.0_armsx3-8290349e5", "--version", "0.10.0"])
rep = open(rp).read()
check("exit 2 INCONCLUSIVE, headline in the report", (code, "**INCONCLUSIVE**" in rep), (2, True))
check("the reason names r3's RECOVERY row", "gt5p-rc-r3" in rep and "RECOVERY:Silent" in rep, True)
check("the control CRASH and the RR7 DESYNC do not FAIL it", "FAIL:" not in rep, True)
check("N=3 on the RC GT5P arm (per (game, core), not per dir)", "| 3 |" in rep and "| 1 |" in rep, True)
check("matrix has claim/command/verdict/N/evidence", "| claim | command | verdict | N | evidence |" in rep, True)
check("the command is reconstructable", "gt5_trial.py --out " in rep and "--game BCUS98158" in rep, True)
check("surfaces land as rows with their line evidence", "surfaces.txt:2" in rep, True)
check("arms table shows the control core", "0.9.0.3_armsx3-a74a0f3e0 (control)" in rep, True)
if os.environ.get("KEEP_RC"):
    print(f"kept: {rp}")

print("\n[3] PASS: fix r3's row")
root2 = tempfile.mkdtemp(prefix="rcreport-")
build(root2)
led = open(os.path.join(root2, "sessions.tsv")).read().replace("RECOVERY:Silent\t8.0", "CLEAN\t8.0", 1)
open(os.path.join(root2, "sessions.tsv"), "w").write(led)
code = R.main(["--out", root2, "--surfaces", os.path.join(root2, "surfaces.txt"), "--expect-core", "0.10.0_armsx3-8290349e5"])
check("exit 0 PASS", (code, "**PASS**" in open(os.path.join(root2, "report.md")).read()), (0, True))

print("\n[4] FAIL: a surface FAIL line; an RC row on the wrong core; a non-control CRASH")
open(os.path.join(root2, "surfaces.txt"), "a").write("osguard: mismatch, not activating FAIL\n")
code = R.main(["--out", root2, "--surfaces", os.path.join(root2, "surfaces.txt"), "--expect-core", "0.10.0_armsx3-8290349e5"])
check("surface FAIL -> exit 1", code, 1)
open(os.path.join(root2, "surfaces.txt"), "w").write("all PASS\n")
code = R.main(["--out", root2, "--expect-core", "0.10.0_armsx3-DEADBEEF"])
check("RC rows on another core -> FAIL naming it", (code, "is not the RC core" in open(os.path.join(root2, "report.md")).read()), (1, True))
json.dump({"tool": "gt6_trial", "game": "BCUS98296", "verdict": "DEADLOCK", "secs": 400, "t0_epoch": T, "end_epoch": T + 400,
           "label": "rc-0.10.0"}, open(os.path.join(root2, "gt6-r1", "verdict.json"), "w"))
code = R.main(["--out", root2, "--expect-core", "0.10.0_armsx3-8290349e5"])
check("GT6 DEADLOCK -> FAIL", code, 1)

print("\n[5] INCONCLUSIVE: a missing row, a short arm, nothing at all")
root3 = tempfile.mkdtemp(prefix="rcreport-")
build(root3, ledger_variant="no_r2")
led = open(os.path.join(root3, "sessions.tsv")).read().replace("RECOVERY:Silent\t8.0", "CLEAN\t8.0", 1)
open(os.path.join(root3, "sessions.tsv"), "w").write(led)
code = R.main(["--out", root3])
rep = open(os.path.join(root3, "report.md")).read()
check("r2 without its row -> INCONCLUSIVE, named", (code, "gt5p-rc-r2" in rep and "no ledger row" in rep), (2, True))
shutil.rmtree(os.path.join(root3, "gt5p-rc-r3"))
code = R.main(["--out", root3, "--min-n", "3"])
check("RC GT5P arm N<3 -> INCONCLUSIVE (r2 rowless sits in its own ?-core arm, r1 alone is N=1)", (code, "N=1 < 3" in open(os.path.join(root3, "report.md")).read()), (2, True))
empty = tempfile.mkdtemp(prefix="rcreport-")
check("empty --out -> INCONCLUSIVE (never PASS on nothing)", R.main(["--out", empty]), 2)
check("missing --out dir -> 3", R.main(["--out", empty + "/nope"]), 3)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL RC_REPORT CHECKS PASSED")
