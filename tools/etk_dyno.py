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
  - SHM-contaminated audio cells are dropped, and the count is printed (see
    aud_stale) — a poisoned cell is never silently scored

Usage:
  tools/etk_dyno.py [--game NPEA00050] [--ledger PATH] [--res 100] [--all]
  tools/etk_dyno.py --audio            # rank titles by audio skip-per-second
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
AUD_SLACK_S = 30      # mirrors session_postmortem.sh's up_s cross-check slack

COLS = {"epoch": 0, "dur": 1, "game": 3, "status": 4, "sig": 9, "shd": 11,
        "tune": 14, "fps_med": 16, "ft_jit": 22, "res": 19, "clk": 20,
        "pwr": 21, "aud": 25, "lock": 27, "perfect": 28, "rescues": 29,
        "perf": 30}


def f(row, key, default=0.0):
    i = COLS[key]
    try:
        return float(row[i]) if i < len(row) and row[i] not in ("", "-") else default
    except ValueError:
        return default


def s(row, key, default=""):
    i = COLS[key]
    return row[i] if i < len(row) and row[i] else default


def parse_kv(cell):
    """Parse a comma-folded `k=v,k=v` telemetry cell (aud / perf) to floats."""
    out = {}
    if not cell or cell == "-":
        return out
    for tok in cell.split(","):
        k, _, v = tok.partition("=")
        try:
            out[k.strip()] = float(v)
        except ValueError:
            pass
    return out


def aud_stale(row, aud):
    """True when this audio cell describes a longer run than the session had.

    RETROACTIVE half of the SHM cross-contamination fix (rig side: 63ba621).
    /dev/shm/rpcs3_audio_stat is never reset between games, so a session that
    ended before the emulator wrote it inherited the PREVIOUS game's counters
    wholesale — the live proof was a 14 s Demon's Souls row carrying 431 s of
    SOULCALIBUR V audio. Rows written before that fix are still poisoned.

    Only ONE of the postmortem's two guards can be re-run from a stored row:
    this content cross-check. The mtime guard needs a file that is long gone,
    so this is a LOWER BOUND — inheritance from a SHORTER previous session is
    undetectable here. Col 31 `perf` carries no self-reported uptime at all,
    so it has NO retroactive test; treat pre-fix perf cells as unverifiable
    rather than clean.
    """
    return aud.get("up_s", 0.0) > f(row, "dur") + AUD_SLACK_S


def eligible(path, args):
    """Warm, non-ABORTED, long-enough rows — the shared intake discipline."""
    for line in path.read_text().splitlines()[1:]:
        row = line.split("\t")
        if len(row) < 15:
            continue
        if args.game and s(row, "game") != args.game:
            continue
        if s(row, "status") == "ABORTED" or f(row, "dur") < MIN_DUR:
            continue
        if f(row, "shd") > WARM_SHD:
            continue  # bake session — excluded from tuning verdicts
        yield row


def audio_report(path, args):
    """Rank titles by audio skip-per-second — the surviving stutter discriminator.

    The middleware and headroom hypotheses are both dead (CRIWARE titles land
    on both sides; LBP at 0.06 skip/s and GT5P at 2.3 skip/s are both ~30 fps).
    What separates operator-perceived stutterers from clean titles is the skip
    RATE; `ur` (underruns) is noise. Rate is per second of AUDIO uptime, not of
    wall-clock session, so a title is not penalised for a long menu sit.
    """
    per_game, dropped, seen = defaultdict(list), 0, 0
    for row in eligible(path, args):
        aud = parse_kv(s(row, "aud"))
        if not aud:
            continue
        seen += 1
        if aud_stale(row, aud):
            dropped += 1
            continue
        up = aud.get("up_s", 0.0)
        if up <= 0:
            continue
        per_game[s(row, "game")].append(
            (aud.get("skip", 0.0) / up, aud.get("ur", 0.0), up))

    if not per_game:
        sys.exit(f"no scoreable audio rows ({seen} seen, {dropped} SHM-stale)")

    print(f"{'GAME':<12} {'N':>3} {'SKIP/s p50':>10} {'SKIP/s max':>10} "
          f"{'UR p50':>7} {'AUDIO s':>8}")
    for game, vals in sorted(per_game.items(),
                             key=lambda kv: statistics.median(v[0] for v in kv[1]),
                             reverse=True):
        rates = sorted(v[0] for v in vals)
        print(f"{game:<12} {len(vals):>3} {statistics.median(rates):>10.2f} "
              f"{rates[-1]:>10.2f} {statistics.median(v[1] for v in vals):>7.1f} "
              f"{sum(v[2] for v in vals):>8.0f}")
    print(f"\n{seen} audio cells seen; {dropped} dropped as SHM-stale "
          f"(pre-63ba621 cross-contamination; lower bound — see aud_stale).")
    print("skip/s is the discriminator; ur is noise. No verdicts below N=3.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--ledger", default="dossiers/etk_telemetry/sessions.tsv")
    ap.add_argument("--res", type=int, help="filter to one Resolution Scale (KPI res=100)")
    ap.add_argument("--min-n", type=int, default=1, help="hide arms below this N")
    ap.add_argument("--audio", action="store_true",
                    help="rank titles by audio skip-per-second instead of scoring arms")
    args = ap.parse_args()

    path = Path(args.ledger)
    if not path.exists():
        sys.exit(f"ledger not found: {path}")

    if args.audio:
        return audio_report(path, args)

    arms = defaultdict(list)
    for row in eligible(path, args):
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

    # Split the stack attribution (build=…;stack=…) off the front of tune_tag and
    # show it as a legend keyed S1, S2, … The tag is grouped on IN FULL — two arms
    # on different stacks must never merge — but printing ~74 chars of fingerprint
    # per row would wreck the table, and the whole point of the legend is that a
    # stack change becomes visible at a glance instead of hiding inside a string.
    def split_attr(tune):
        parts = tune.split(";")
        attr = [p for p in parts if p.startswith(("build=", "stack="))]
        dials = [p for p in parts if not p.startswith(("build=", "stack="))]
        return ";".join(attr), (";".join(dials) or "default")

    stacks, order = {}, []
    for arm in arms:
        attr, _ = split_attr(arm[0])
        if attr not in stacks:
            stacks[attr] = f"S{len(order) + 1}"
            order.append(attr)
    if len(order) > 1 or (order and order[0]):
        print("STACKS")
        for attr in order:
            print(f"  {stacks[attr]:<3} {attr or '(unattributed — pre-stack-tag rows)'}")
        if len(order) > 1:
            print("  !! more than one stack present — arms below are NOT comparable across S-ids")
        print()

    print(f"{'ARM (stack | tune | res | clk | pwr)':<46} {'N':>3} {'PERFECT%':>8} "
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
        _attr, _dials = split_attr(arm[0])
        label = f"{stacks[_attr]} | {_dials} | {arm[1]} | {arm[2]} | {arm[3]}"
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
