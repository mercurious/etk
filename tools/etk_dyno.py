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
  - an audio cell without `drop=` (a build before the drop counter) is UNKNOWN,
    never zero (see aud_drop_per_min) — its own N is printed beside it

Usage:
  tools/etk_dyno.py [--game NPEA00050[,BCUS98158]] [--ledger PATH] [--res 100] [--by stack|core]
  tools/etk_dyno.py --audio            # rank titles by audio skip-per-second
Default ledger: dossiers/etk_telemetry/sessions.tsv (host mirror; pass the
rig-synced path for freshest data). --res filters to one resolution (KPI is
only honest at 100). --all includes crash rows in scoring (default: they count
toward N and duration but their fps/lock stats are still real — kept).

2026-10-22 (0.10.0 RC validation, release plan §3.2):
  --game A,B     a comma list: NPEA00050 + BCUS98158 are the same engine (GT5P Spec III
                 PSN / ISO) and one RC arm.
  --by core      group arms on the ledger's core= token ONLY (the RC core vs the 0.9.0.3
                 control), with kernel build / Turnip / pwr / titles as CONTEXT columns and a
                 `stack differs` flag on an arm whose rows straddle more than one
                 (build, stack, pwr) — instead of the default's refusal to compare. The
                 default (--by stack: tune_tag in full x res x clk x pwr) is unchanged.
  PERFECT% caveat printed whenever an arm's fps_med < 45: PERFECT% is a 60 fps KPI (5 s
  windows locked at 16.7 ms) and reads 0-4 % on a 30 fps title on BOTH arms — it cannot
  discriminate there. Judge DUR p50/max, CRASH n/N, fps_med / fps_1low (printed beside
  it), JIT ms, RESC/h. (A real 30 fps lock KPI is deferred to 0.11.0; lock_pct is the
  better of the two today.)
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
        "tune": 14, "fps_med": 16, "fps_1low": 17, "ft_jit": 22, "res": 19, "clk": 20,
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


def aud_drop_per_min(aud):
    """Whole audio blocks discarded per minute of audio uptime, or None.

    ARMSX3 14e740513 found cellAudio's commit_data silently discarding any
    5.33 ms block that did not fit the ring — a click with ur=0 and nothing in
    the log, the same clean-delivery-layer signature GT5P recorded. The GTK
    Edition exports the per-guest-boot count as `drop=` (appended after
    buf_ms; every earlier key unchanged). A cell from an older build has no
    such key, and that is UNKNOWN, not a clean session — so None, never 0.0.
    Per minute rather than per second: a handful of clicks in a session would
    otherwise print as 0.00.
    """
    if "drop" not in aud:
        return None
    up = aud.get("up_s", 0.0)
    return aud["drop"] / up * 60 if up > 0 else None


LOW_FPS_KPI = 45.0    # below this fps_med the 60 fps PERFECT% KPI cannot discriminate (30 fps titles)


def games_of(arg):
    """--game value -> set of serials ('' / None -> empty = every title); a comma list is one arm."""
    return {g.strip().upper() for g in (arg or "").split(",") if g.strip()}


def core_of(tune):
    """The ledger's core= token from a tune_tag, or 'unattributed' (pre-core-tag rows)."""
    for part in tune.split(";"):
        if part.startswith("core="):
            return part[5:]
    return "unattributed"


def kernel_of(tune):
    """`k7.2.0#9` from stack=rk…/k…/r…, or '?'."""
    for part in tune.split(";"):
        if part.startswith("stack="):
            segs = part[6:].split("/")
            return segs[1] if len(segs) > 1 else "?"
    return "?"


def eligible(path, args):
    """Warm, non-ABORTED, long-enough rows — the shared intake discipline."""
    games = games_of(getattr(args, "game", None))
    for line in path.read_text().splitlines()[1:]:
        row = line.split("\t")
        if len(row) < 15:
            continue
        if games and s(row, "game") not in games:
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

    DROP/min rides alongside as context, NOT a ranking key: its p50 is taken
    over only the cells that carry `drop=`, and DROP N says how many that was
    ('-' when none did). Older cells are unknown, not clean.
    """
    per_game, dropped, seen, with_drop = defaultdict(list), 0, 0, 0
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
        drop = aud_drop_per_min(aud)
        with_drop += drop is not None
        per_game[s(row, "game")].append(
            (aud.get("skip", 0.0) / up, aud.get("ur", 0.0), up, drop))

    if not per_game:
        sys.exit(f"no scoreable audio rows ({seen} seen, {dropped} SHM-stale)")

    print(f"{'GAME':<12} {'N':>3} {'SKIP/s p50':>10} {'SKIP/s max':>10} "
          f"{'UR p50':>7} {'AUDIO s':>8} {'DROP/min p50':>12} {'DROP N':>6}")
    for game, vals in sorted(per_game.items(),
                             key=lambda kv: statistics.median(v[0] for v in kv[1]),
                             reverse=True):
        rates = sorted(v[0] for v in vals)
        drops = [v[3] for v in vals if v[3] is not None]
        drop_str = f"{statistics.median(drops):.2f}" if drops else "-"
        print(f"{game:<12} {len(vals):>3} {statistics.median(rates):>10.2f} "
              f"{rates[-1]:>10.2f} {statistics.median(v[1] for v in vals):>7.1f} "
              f"{sum(v[2] for v in vals):>8.0f} {drop_str:>12} {len(drops):>6}")
    print(f"\n{seen} audio cells seen; {dropped} dropped as SHM-stale "
          f"(pre-63ba621 cross-contamination; lower bound — see aud_stale).")
    print("skip/s is the discriminator; ur is noise. No verdicts below N=3.")
    print(f"{with_drop} scored cells carry drop= (drop-counter builds); DROP/min is "
          f"over those alone — '-' is unknown, never zero.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", help="serial, or a comma list (NPEA00050,BCUS98158 = one GT5P arm)")
    ap.add_argument("--by", choices=("stack", "core"), default="stack",
                    help="arm key: stack (default: full tune_tag x res x clk x pwr) or core (the core= token only)")
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
        if args.by == "core":
            arm = (core_of(s(row, "tune", "default")),)
        else:
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
    if args.by == "stack":
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
        head = "ARM (stack | tune | res | clk | pwr)"
    else:
        head = "ARM (core | kernel | turnip | pwr | titles)"

    print(f"{head:<46} {'N':>3} {'PERFECT%':>8} {'FPS med':>7} {'1%low':>6} "
          f"{'LOCK%':>6} {'JIT ms':>6} {'RESC/h':>6} {'DUR p50':>8} {'DUR max':>8}  CRASH")
    scored = []
    for arm, rows in arms.items():
        n = len(rows)
        crashes = sum(1 for r in rows if s(r, "status") != "CLEAN")
        scored.append((med(rows, "perfect"), med(rows, "lock"), arm, rows, n, crashes))
    # rank by THE KPI, then lock share
    low_fps = []
    for perfect, lock, arm, rows, n, crashes in sorted(
            scored, key=lambda t: (t[0], t[1]), reverse=True):
        if n < args.min_n:
            continue
        flag = "" if n >= 3 else "  LOW-N"
        if args.by == "stack":
            _attr, _dials = split_attr(arm[0])
            label = f"{stacks[_attr]} | {_dials} | {arm[1]} | {arm[2]} | {arm[3]}"
        else:
            # context, never part of the key: a core arm whose rows straddle two stacks or two
            # POWER settings is named so, and the reader decides whether it is one measurement
            ctx = sorted({(split_attr(s(r, "tune", "default"))[0], s(r, "pwr", "none")) for r in rows})
            kernels = sorted({kernel_of(s(r, "tune", "")) for r in rows})
            builds = sorted({split_attr(s(r, "tune", ""))[0].split(";")[0].replace("build=", "") or "?" for r in rows})
            pwrs = sorted({s(r, "pwr", "none") for r in rows})
            games = sorted({s(r, "game") for r in rows})
            label = f"{arm[0]} | {','.join(kernels)} | {','.join(builds)} | {','.join(pwrs)} | {','.join(games)}"
            if len(ctx) > 1:
                flag += f"  stack differs ({len(ctx)})"
        durs = sorted(f(r, "dur") for r in rows)
        # rescue rate: keepalive survives per hour of play (the freeze-hitch
        # cost of a lighter LSD gear; 0.0 on pre-col-30 rows = honest unknown)
        total_dur = sum(durs)
        resc_hr = sum(f(r, "rescues") for r in rows) / total_dur * 3600 if total_dur else 0.0
        fps_med, fps_1low = med(rows, "fps_med"), med(rows, "fps_1low")
        if 0 < fps_med < LOW_FPS_KPI:
            low_fps.append(label.split(" | ")[0] if args.by == "core" else f"{label.split(' | ')[0]} {label.split(' | ')[1]}")
        print(f"{label:<46} {n:>3} {perfect:>8.1f} {fps_med:>7.1f} {fps_1low:>6.1f} {lock:>6.1f} "
              f"{med(rows, 'ft_jit'):>6.1f} {resc_hr:>6.1f} {durs[len(durs)//2]:>7.0f}s {durs[-1]:>7.0f}s"
              f"  {crashes}/{n}{flag}")
    print("\nKPI = PERFECT% (5s windows >=95% locked-16.7ms, no hitch) at res 100."
          "\nNo verdicts below N=3; res<100 arms are context, not wins.")
    if low_fps:
        print(f"!! PERFECT% is a 60 fps KPI and cannot discriminate here: fps_med < {LOW_FPS_KPI:.0f} on "
              f"{', '.join(low_fps)} (a 30 fps title reads 0-4 % on every arm)."
              "\n   Judge DUR p50/max, CRASH n/N, FPS med / 1%low, JIT ms, RESC/h instead.")


if __name__ == "__main__":
    main()
