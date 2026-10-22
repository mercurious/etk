#!/usr/bin/env python3
"""rc_report -- compose ONE release-candidate report from what the trials and the ledger already say.

WHY: the 0.10.0 RC matrix (etk-dossiers/release_v0.10.0/research/pitlink_release_validation.md §1,
release plan §4) is run by hand, one trial at a time: gt6_trial / gt5_trial / rr7_trial each leave a
verdict.json in their --out dir, the car's ledger (sessions.tsv) holds the row that is the SURFACE for
every verdict (core=, build=, stack=, status), and the cold-boot surfaces are a handful of read-only
command outputs. A verdict without its ledger row is a diary entry; a row without its trial is
unattributed. Nobody should join those by eye at the end of a 3-hour matrix.

HOW: pure bytes, no rig access, no sequencing.
  rc_report.py --out state/rc/<ver>/ [--ledger state/rc/<ver>/sessions.tsv] [--surfaces FILE|-]
               [--expect-core 0.10.0_armsx3-8290349e5] [--min-n 3]
  * every `<out>/*/verdict.json` is a trial: tool, game, verdict, label, its window
    [t0_epoch, end_epoch] (gt5_trial writes them; older verdicts fall back to the file's mtime)
  * the ledger is the PULLED sessions.tsv (`ssh <car> cat .../sessions.tsv > <out>/sessions.tsv`;
    this tool never pulls it); a trial joins the row whose [epoch - duration_s, epoch] window holds
    the trial's launch (|row start - t0| <= --slack), same game_id
  * --surfaces is free-form text the Engineer pastes/pipes in, one claim per line; a line's verdict is
    its first PASS / FAIL / WARN / MISSING / INCONCLUSIVE / OK token (else NOTED)
  * the matrix: claim · command · verdict · N · evidence path -> <out>/report.md, headline on stdout

VERDICT RULE (printed in the report so the reader can disagree with it):
  FAIL          any RC trial not PASS/SURVIVED; any RC row whose core= is not --expect-core; any
                surface line FAIL/MISSING. A trial whose label says control/ctl is a CONTROL arm:
                it never fails the headline (RR7 is known-bad; the 0.9.0.3 arm is the baseline) --
                a PASS on an RR7 control is flagged NEWS instead.
  INCONCLUSIVE  an RC trial with no ledger row; a SURVIVED trial whose row is RECOVERY:*/PANIC (the
                exit was a kill, not CLEAN); a GT5P arm below --min-n (GT6/RR7/GT HD: N=1 suffices,
                deterministic bugs); a surface WARN; nothing to report at all
  PASS          everything else. The headline is a reading of the evidence, not the certification:
                the operator certifies (release plan §4.6, §5).
Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE · 3 usage.

DISCRIMINATION: tools/autopilot/test_rc_report.py builds fake trial dirs + a fake ledger + surfaces and
pins the join, the N count, the three headlines and the control-arm exemption.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

OK_VERDICTS = {"PASS", "SURVIVED"}
CLEAN_STATUS = ("CLEAN", "SURVIVED:")          # the ledger's "counts as clean" (manual §B.2)
N1_OK = {"BCUS98296", "NPUB30457", "NPEA90002"}  # deterministic-bug titles: one run decides
TITLES = {"BCUS98114": "GT5", "BCUS98158": "GT5P ISO", "NPEA00050": "GT5P PSN", "BCUS98296": "GT6",
          "NPUB30457": "RR7", "NPEA90002": "GT HD"}
TOOL_GAME = {"gt6_trial": "BCUS98296", "rr7_trial": "NPUB30457", "gt5_trial": "BCUS98114"}
SURFACE_TOKEN = re.compile(r"\b(PASS|FAIL|WARN|MISSING|INCONCLUSIVE|OK)\b")
CORE_RE = re.compile(r"(?:^|;)core=([^;]+)")
BUILD_RE = re.compile(r"(?:^|;)build=([^;]+)")
STACK_RE = re.compile(r"(?:^|;)stack=([^;]+)")


# ---- pure helpers (tested) ----------------------------------------------------------------

def infer_tool(v):
    if v.get("tool"):
        return v["tool"]
    if "hold" in v:
        return "rr7_trial"
    if "boot_id" in v or "battery_pct_start" in v:
        return "gt5_trial"
    return "gt6_trial"


def load_trial(dirpath):
    """dir with a verdict.json -> trial dict (tool, game, verdict, label, t0, t_end, dir) or None."""
    vp = os.path.join(dirpath, "verdict.json")
    try:
        with open(vp) as f:
            v = json.load(f)
    except (OSError, ValueError):
        return None
    tool = infer_tool(v)
    game = (v.get("game") or TOOL_GAME.get(tool, "?")).upper()
    secs = v.get("secs") or 0
    t_end = v.get("end_epoch") or int(os.path.getmtime(vp))
    t0 = v.get("t0_epoch") or (t_end - secs)
    label = v.get("label") or ""
    return {"dir": dirpath, "name": os.path.basename(dirpath.rstrip("/")), "tool": tool, "game": game,
            "verdict": v.get("verdict", "?"), "label": label, "secs": secs, "t0": int(t0), "t_end": int(t_end),
            "control": bool(re.search(r"control|\bctl\b|-ctl", label + " " + os.path.basename(dirpath), re.I)),
            "note": v.get("note") or v.get("fault") or "", "exit_ok": v.get("verdict") in OK_VERDICTS}


def parse_ledger(text):
    """sessions.tsv text -> [row dict by header name]; rows shorter than the header read ''."""
    rows, header = [], None
    for ln in text.splitlines():
        if not ln.strip():
            continue
        cols = ln.split("\t")
        if header is None:
            if cols[0] == "epoch":
                header = cols
                continue
            # a headerless (older) ledger: the postmortem's column order
            header = ("epoch duration_s build game_id status peak_load peak_ram_mb peak_temp avg_temp crash_sig "
                      "fence_at_crash shaders_harvested drain_pct thermal_overrides tune_tag crash_shot fps_med "
                      "fps_1low ft_p99_ms res_scale gpu_mhz pwr ft_jitter_ms gpu_fault_status gpu_fault_fence_hex "
                      "aud snd lock_pct perfect_pct rescues perf").split()
        row = dict(zip(header, cols + [""] * (len(header) - len(cols))))
        try:
            row["_epoch"] = int(float(row["epoch"]))
            row["_dur"] = int(float(row["duration_s"] or 0))
        except (ValueError, KeyError):
            continue
        tag = row.get("tune_tag", "")
        row["_core"] = (CORE_RE.search(tag) or [None, "?"])[1]
        row["_build"] = (BUILD_RE.search(tag) or [None, "?"])[1]
        row["_stack"] = (STACK_RE.search(tag) or [None, "?"])[1]
        rows.append(row)
    return rows


def join_row(trial, rows, slack=180):
    """The ledger row whose [epoch - duration_s, epoch] window holds the trial's launch, same game;
    the closest start wins. None when no row is within --slack of the launch."""
    best, best_d = None, None
    for r in rows:
        if r.get("game_id", "").upper() != trial["game"]:
            continue
        start = r["_epoch"] - r["_dur"]
        d = abs(start - trial["t0"])
        if d <= slack or (start - slack <= trial["t0"] <= r["_epoch"] and d <= 2 * slack):
            if best is None or d < best_d:
                best, best_d = r, d
    return best


def parse_surfaces(text, path="surfaces"):
    out = []
    for n, ln in enumerate((text or "").splitlines(), 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = SURFACE_TOKEN.search(ln)
        out.append({"claim": ln.strip(), "verdict": m.group(1) if m else "NOTED", "evidence": f"{path}:{n}"})
    return out


def row_is_clean(row):
    st = row.get("status", "")
    return st == "CLEAN" or st.startswith("SURVIVED:")


def judge(trials, surfaces, expect_core=None, min_n=3):
    """-> (headline, reasons, arms). arms: {(game, core): {"n": trials, "rows": [...], ...}}"""
    reasons, arms = [], {}
    for t in trials:
        row = t.get("row")
        core = row["_core"] if row else "?"
        arm = arms.setdefault((t["game"], core), {"n": 0, "rows": [], "clean": 0, "control": t["control"]})
        arm["n"] += 1
        if row:
            arm["rows"].append(row)
            arm["clean"] += row_is_clean(row)
    fail, inconclusive = [], []
    for t in trials:
        who = f"{t['name']} ({TITLES.get(t['game'], t['game'])} {t['verdict']})"
        row = t.get("row")
        if t["control"]:
            if t["game"] == "NPUB30457" and t["verdict"] == "PASS":
                reasons.append(f"NEWS: {who} -- a known-bad control passed; re-run x3 at --hold 900 before believing it")
            continue
        if not t["exit_ok"]:
            fail.append(f"{who}: {t['note'][:120] or 'trial verdict is not PASS/SURVIVED'}")
        if row is None:
            inconclusive.append(f"{who}: no ledger row within the trial window -- a verdict without its row is a diary entry")
            continue
        if expect_core and not row["_core"].startswith(expect_core):
            fail.append(f"{who}: ledger row core={row['_core']} is not the RC core {expect_core}")
        if t["exit_ok"] and not row_is_clean(row):
            inconclusive.append(f"{who}: trial SURVIVED but the row closed {row['status']} -- the exit was not graceful; re-run with the macro's exit")
    for (game, core), arm in arms.items():
        if arm["control"] or game in N1_OK:
            continue
        if arm["n"] < min_n:
            inconclusive.append(f"{TITLES.get(game, game)} arm core={core}: N={arm['n']} < {min_n} (no crowns from one run)")
    for s in surfaces:
        if s["verdict"] in ("FAIL", "MISSING"):
            fail.append(f"surface {s['evidence']}: {s['claim'][:100]}")
        elif s["verdict"] in ("WARN", "INCONCLUSIVE"):
            inconclusive.append(f"surface {s['evidence']}: {s['claim'][:100]}")
    if not trials and not surfaces:
        inconclusive.append("nothing to report: no verdict.json under --out and no --surfaces")
    headline = "FAIL" if fail else ("INCONCLUSIVE" if inconclusive else "PASS")
    return headline, [f"FAIL: {r}" for r in fail] + [f"INCONCLUSIVE: {r}" for r in inconclusive] + reasons, arms


def command_of(t):
    cmd = f"python3 tools/autopilot/{t['tool']}.py --out {t['dir']}"
    if t["tool"] == "gt5_trial":
        cmd += f" --game {t['game']}"
    if t["label"]:
        cmd += f" --label {t['label']}"
    return cmd


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def compose(out_dir, trials, surfaces, headline, reasons, arms, ledger_path, version):
    lines = [f"# RC report {version or os.path.basename(out_dir.rstrip('/'))} -- **{headline}**", "",
             f"Composed {time.strftime('%Y-%m-%d %H:%M')} by rc_report.py from `{out_dir}` "
             f"({len(trials)} trial dirs), ledger `{ledger_path or '(none given)'}`, {len(surfaces)} surface lines. "
             "The headline is a reading of the evidence, not the certification: the operator certifies.", ""]
    if reasons:
        lines += ["## Why", ""] + [f"- {r}" for r in reasons] + [""]
    lines += ["## Matrix", ""]
    mrows = []
    for t in trials:
        row = t.get("row")
        n = arms[(t["game"], row["_core"] if row else "?")]["n"]
        rv = f"{t['verdict']}" + (" (control)" if t["control"] else "")
        rs = f"row {row['status']} core={row['_core']} build={row['_build']} pwr={row.get('pwr', '')}" if row else "row: NONE"
        ev = t["dir"] + (f" + ledger epoch {row['_epoch']}" if row else "")
        mrows.append((f"{TITLES.get(t['game'], t['game'])} ({t['game']}) {t['label']}".strip(), f"`{command_of(t)}`",
                      f"{rv} / {rs}", n, ev))
    for s in surfaces:
        mrows.append((s["claim"], "(surface)", s["verdict"], 1, s["evidence"]))
    lines.append(md_table(("claim", "command", "verdict", "N", "evidence"), mrows))
    lines += ["", "## Arms (from the joined ledger rows)", ""]
    arows = []
    for (game, core), arm in sorted(arms.items()):
        durs = sorted(r["_dur"] for r in arm["rows"])
        p50 = durs[len(durs) // 2] if durs else "-"
        mx = durs[-1] if durs else "-"
        fps = [float(r.get("fps_med") or 0) for r in arm["rows"] if (r.get("fps_med") or "0") not in ("", "-")]
        fps_med = f"{statistics.median([v for v in fps if v > 0]):.1f}" if any(v > 0 for v in fps) else "-"
        arows.append((f"{TITLES.get(game, game)} ({game})", core + (" (control)" if arm["control"] else ""), arm["n"],
                      len(arm["rows"]), f"{arm['clean']}/{len(arm['rows'])}", p50, mx, fps_med))
    lines.append(md_table(("title", "core", "trials", "rows", "clean rows", "DUR p50 s", "DUR max s", "fps_med"), arows))
    lines += ["", "Judge GT5P on DUR p50/max, CRASH n/N, fps_med/1low, RESC/h -- PERFECT% is a 60 fps KPI and reads "
              "0-4 % on a 30 fps title (etk_dyno.py --by core prints the caveat)."]
    if surfaces:
        lines += ["", "## Surfaces (as given)", "", "```"] + [s["claim"] for s in surfaces] + ["```"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="state/rc/<ver>/ -- trial dirs live under it; report.md lands in it")
    ap.add_argument("--ledger", help="the PULLED sessions.tsv (default <out>/sessions.tsv when present)")
    ap.add_argument("--surfaces", help="free-form surface lines, a file or - for stdin")
    ap.add_argument("--expect-core", help="the RC core token rows must carry (prefix match), e.g. 0.10.0_armsx3-8290349e5")
    ap.add_argument("--min-n", type=int, default=3)
    ap.add_argument("--slack", type=int, default=180, help="seconds between a trial's launch and its row's start")
    ap.add_argument("--version", default="")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.out):
        print(f"rc_report: --out {a.out} is not a directory", file=sys.stderr)
        return 3
    trials = [t for t in (load_trial(os.path.join(a.out, d)) for d in sorted(os.listdir(a.out))
                          if os.path.isdir(os.path.join(a.out, d))) if t]
    ledger = a.ledger or (os.path.join(a.out, "sessions.tsv") if os.path.exists(os.path.join(a.out, "sessions.tsv")) else None)
    rows = []
    if ledger:
        try:
            with open(ledger) as f:
                rows = parse_ledger(f.read())
        except OSError as e:
            print(f"rc_report: ledger unreadable: {e}", file=sys.stderr)
            return 3
    for t in trials:
        t["row"] = join_row(t, rows, a.slack)
    surfaces = []
    if a.surfaces:
        text = sys.stdin.read() if a.surfaces == "-" else open(a.surfaces).read()
        surfaces = parse_surfaces(text, "stdin" if a.surfaces == "-" else a.surfaces)
    headline, reasons, arms = judge(trials, surfaces, a.expect_core, a.min_n)
    report = compose(a.out, trials, surfaces, headline, reasons, arms, ledger, a.version)
    rp = os.path.join(a.out, "report.md")
    with open(rp, "w") as f:
        f.write(report)
    print(f"RC {a.version or os.path.basename(a.out.rstrip('/'))}: {headline}  ({len(trials)} trials, "
          f"{sum(1 for t in trials if t.get('row'))} with rows, {len(surfaces)} surfaces) -> {rp}")
    for r in reasons:
        print("  " + r)
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[headline]


if __name__ == "__main__":
    sys.exit(main())
