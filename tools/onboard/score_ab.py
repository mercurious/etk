#!/usr/bin/env python3
"""score_ab -- split the rig ledger into the dyno A/B's two arms (ONBOARD observer on / off) and judge each
with tools/etk_dyno.py, plus a compact per-run table. The arm of a ledger row is the run whose start
(loop.log "<HH:MM:SS> <run>: start") is the closest one before the row's ignition (epoch - duration_s),
same day. Bake runs (shaders_harvested > WARM_SHD) are shown but the dyno drops them itself.

  tools/onboard/score_ab.py ROOT [--ledger PATH] [--game BCUS98296]
"""
import argparse, datetime as dt, functools, os, re, statistics, subprocess, sys
print = functools.partial(print, flush=True)  # keep our lines ahead of the dyno subprocess output
HERE = os.path.dirname(os.path.abspath(__file__)); ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--ledger", default=None); ap.add_argument("--game", default="BCUS98296")
a = ap.parse_args()
ledger = a.ledger or os.path.join(a.root, "sessions.tsv")
if not os.path.exists(ledger):
    sys.exit(f"no ledger at {ledger} (scp the rig's etk_telemetry/sessions.tsv there, or --ledger)")
day = dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(a.root, "loop.log"))).date()
runs = []
for ln in open(os.path.join(a.root, "loop.log")):
    m = re.match(r"(\d\d):(\d\d):(\d\d) (\w+): start", ln)
    if m:
        t = dt.datetime.combine(day, dt.time(int(m[1]), int(m[2]), int(m[3]))).timestamp()
        runs.append((t, m[4]))
runs.sort()
hdr = open(ledger).readline().rstrip("\n").split("\t")
rows = [(l.rstrip("\n").split("\t") + [""] * len(hdr))[:len(hdr)] for l in open(ledger).readlines()[1:] if l.strip()]  # a PANIC row is short
col = {n: i for i, n in enumerate(hdr)}
arms = {"on": [], "off": []}; table = []
for r in rows:
    if r[col["game_id"]] != a.game: continue
    ign = float(r[col["epoch"]]) - float(r[col["duration_s"]])
    before = [(t, n) for t, n in runs if t <= ign + 5]
    if not before: continue
    t, name = before[-1]
    if ign - t > 900: continue
    arm = re.sub(r"\d+$", "", name)
    arms[arm].append(r); table.append((name, arm, r))
def f(r, k): return r[col[k]]
print(f"{'run':6} {'arm':4} {'status':16} {'dur':>5} {'fps_med':>7} {'1low':>5} {'p99':>6} {'jit':>5} {'lock':>5} {'perf':>5} {'resc':>4} {'shd':>5}")
for name, arm, r in table:
    print(f"{name:6} {arm:4} {f(r,'status'):16} {f(r,'duration_s'):>5} {f(r,'fps_med'):>7} {f(r,'fps_1low'):>5} {f(r,'ft_p99_ms'):>6} {f(r,'ft_jitter_ms'):>5} {f(r,'lock_pct'):>5} {f(r,'perfect_pct'):>5} {f(r,'rescues'):>4} {f(r,'shaders_harvested'):>5}")
for arm in ("off", "on"):
    warm = [r for r in arms[arm] if f(r, "status") != "ABORTED" and float(f(r, "shaders_harvested") or 0) <= 5 and float(f(r, "duration_s")) >= 60]
    def med(k):
        v = [float(f(r, k)) for r in warm if f(r, k) not in ("", "0", "0.0") or k == "rescues"]
        return statistics.median(v) if v else float("nan")
    print(f"\n{arm.upper()} warm N={len(warm)}" + ("  (LOW-N: no verdict)" if len(warm) < 3 else "") +
          f": fps_med {med('fps_med'):.1f} | 1low {med('fps_1low'):.1f} | ft_p99 {med('ft_p99_ms'):.1f} | jit {med('ft_jitter_ms'):.1f} | lock {med('lock_pct'):.1f} | perfect {med('perfect_pct'):.1f} | dur {med('duration_s'):.0f}")
    p = os.path.join(a.root, f"ledger_{arm}.tsv")
    with open(p, "w") as fh:
        fh.write("\t".join(hdr) + "\n"); [fh.write("\t".join(r) + "\n") for r in arms[arm]]
    print(f"--- etk_dyno on {p}:")
    subprocess.run([sys.executable, os.path.join(ETK, "tools", "etk_dyno.py"), "--game", a.game, "--ledger", p, "--min-n", "1"])
