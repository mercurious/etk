#!/usr/bin/env python3
"""ETK Dyno — ledger-driven knob A/B report (host-side, read-only).

The Dyno concept (project_etk_dyno): N-trial knob A/B judged from the race
ledger, keystone = tune_tag. This is the JUDGE half: group warm race sessions
by their full tune attribution (tune_tag + res + clk + pwr) and score each arm
by Fable's Challenge metrics (perfect_pct is THE KPI), with the crash-era
signal (duration / time-to-crash ceiling) alongside.

Verdict discipline baked in (feedback_* memories):
  - warm sessions only (shaders_harvested <= WARM_SHD): bake runs are excluded
  - ABORTED rows dropped; duration >= 60s
  - N per arm is always shown — no verdicts at N<3 (flagged LOW-N)
  - medians, never means-of-fps

Usage:
  tools/etk_dyno.py [--game NPEA00050] [--ledger PATH] [--res 100] [--all]
Default ledger: dossiers/etk_telemetry/sessions.tsv (host mirror; pass the
rig-synced path for freshest data). --res filters to one resolution (KPI is
only honest at 100). --all includes crash rows in scoring (default: they count
toward N and duration but their fps/lock stats are still real — kept).
"""
import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WARM_SHD = 5          # <= this many new shaders = warm (shd~0 doctrine)
MIN_DUR = 60

COLS = {"epoch": 0, "dur": 1, "game": 3, "status": 4, "sig": 9, "shd": 11,
        "tune": 14, "fps_med": 16, "ft_jit": 22, "res": 19, "clk": 20,
        "pwr": 21, "lock": 27, "perfect": 28, "rescues": 29}


def f(row, key, default=0.0):
    i = COLS[key]
    try:
        return float(row[i]) if i < len(row) and row[i] not in ("", "-") else default
    except ValueError:
        return default


def s(row, key, default=""):
    i = COLS[key]
    return row[i] if i < len(row) and row[i] else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--ledger", default="dossiers/etk_telemetry/sessions.tsv")
    ap.add_argument("--res", type=int, help="filter to one Resolution Scale (KPI res=100)")
    ap.add_argument("--min-n", type=int, default=1, help="hide arms below this N")
    args = ap.parse_args()

    path = Path(args.ledger)
    if not path.exists():
        sys.exit(f"ledger not found: {path}")

    arms = defaultdict(list)
    for line in path.read_text().splitlines()[1:]:
        row = line.split("\t")
        if len(row) < 15:
            continue
        if args.game and s(row, "game") != args.game:
            continue
        status = s(row, "status")
        if status == "ABORTED" or f(row, "dur") < MIN_DUR:
            continue
        if f(row, "shd") > WARM_SHD:
            continue  # bake session — excluded from tuning verdicts
        res = int(f(row, "res"))
        if args.res and res != args.res:
            continue
        arm = (s(row, "tune", "default"), res or "?",
               int(f(row, "clk")) or "?", s(row, "pwr", "none"))
        arms[arm].append(row)

    if not arms:
        sys.exit("no warm scoreable sessions matched (bake runs + ABORTED are excluded)")

    def med(rows, key, nonzero=True):
        vals = [f(r, key) for r in rows]
        if nonzero:
            vals = [v for v in vals if v > 0]
        return statistics.median(vals) if vals else 0.0

    print(f"{'ARM (tune | res | clk | pwr)':<46} {'N':>3} {'PERFECT%':>8} "
          f"{'LOCK%':>6} {'JIT ms':>6} {'RESC/h':>6} {'DUR p50':>8} {'DUR max':>8}  CRASH")
    scored = []
    for arm, rows in arms.items():
        n = len(rows)
        crashes = sum(1 for r in rows if s(r, "status") != "CLEAN")
        scored.append((med(rows, "perfect"), med(rows, "lock"), arm, rows, n, crashes))
    # rank by THE KPI, then lock share
    for perfect, lock, arm, rows, n, crashes in sorted(
            scored, key=lambda t: (t[0], t[1]), reverse=True):
        if n < args.min_n:
            continue
        label = f"{arm[0]} | {arm[1]} | {arm[2]} | {arm[3]}"
        durs = sorted(f(r, "dur") for r in rows)
        flag = "" if n >= 3 else "  LOW-N"
        # rescue rate: keepalive survives per hour of play (the freeze-hitch
        # cost of a lighter LSD gear; 0.0 on pre-col-30 rows = honest unknown)
        total_dur = sum(durs)
        resc_hr = sum(f(r, "rescues") for r in rows) / total_dur * 3600 if total_dur else 0.0
        print(f"{label:<46} {n:>3} {perfect:>8.1f} {lock:>6.1f} "
              f"{med(rows, 'ft_jit'):>6.1f} {resc_hr:>6.1f} {durs[len(durs)//2]:>7.0f}s {durs[-1]:>7.0f}s"
              f"  {crashes}/{n}{flag}")
    print("\nKPI = PERFECT% (5s windows >=95% locked-16.7ms, no hitch) at res 100."
          "\nNo verdicts below N=3; res<100 arms are context, not wins.")


if __name__ == "__main__":
    main()
