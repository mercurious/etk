#!/usr/bin/env python3
"""park_ab -- is the ETK semaphore park (GTK_SEMA_PARK) worth having? Measured, not remembered.

The park was crowned on the 0.7.3 base (2026-07-12, one title, one session) against a hot spin.
The 0.9/0.10 base replaced that spin with its own tiered WFE wait, so the old numbers are
folklore until re-measured; and the park multiplies RR7's base desync ~8x (hunt-20261019-rr7).

  park_ab.py run GAME [--attach] [--secs 300] [--settle 120] [--label TEXT] [--out DIR]
      one sample: launch (unless --attach: the driver or a macro is already running it), wait for
      the core's perfstat line, skip --settle s, measure --secs s, exit gracefully. run.json holds
      the core identity, its GTK_SEMA_* env, the arm, and the window's numbers. GAME is anything
      ./hunt takes (gt5p, gt6, a serial, "ridge racer"); --out defaults to
      state/park_ab/<date>/<SERIAL>-rN (the next free N).
  park_ab.py report [DIR...]   (default: every run under state/park_ab)
      every run.json under DIR, grouped by game + arm: N, fps / frametime / system busy, and the
      park's own activity.

What it reads, all on the car (ssh, so USB-net or WiFi alike):
  /dev/shm/rpcs3_perf_log  the core's perfstat append log (ep= + fps ft_ms cpu ppu spu rsx ... sq sw
                           pk pf). It is written ONLY while the title's Performance Overlay is
                           Enabled at Detail level High; without it the run ends NO_PERFSTAT.
  /proc/stat               whole-system CPU busy over the window (what a park would hand back)
  thermal zones            the hottest zone at the window's end
Park activity per second: sq waits entered, sw wait laps, pk/pf futex parks (non-flip / flip).
parked_cores = (pk * GTK_SEMA_PARK_US + pf * GTK_SEMA_PARK_FLIP_US) / window: the most core time the
park could have handed back. Near zero = the park is inert on that title and scene (neither help
nor harm). The A/B (park on = the certified core, off = a nopark core or GTK_SEMA_PARK=0) decides
the rest: fps / frametime / busy within noise = the park buys nothing there.
Launch goes through the car's own garage code (EmulationStation's API); exit is Pitlink EXIT (cache
banking intact) over USB, else an ssh tunnel to the car's gadget listener, else (a hung core has no
listener left) the kit's R3 path, gtpilot.py recover.
Exit: 0 measured, 2 no game / launch failed, 3 the game died in the window, 4 NO_PERFSTAT.
"""
import argparse
import glob
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
STATE = os.path.join(ETK, "state", "park_ab")
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))
from client import PitlinkClient, PitlinkError  # noqa: E402

PERF_LOG = "/dev/shm/rpcs3_perf_log"
RIG_LOG = "/storage/.cache/rpcs3/RPCS3.log"
ETK_BIN = "/storage/games-internal/roms/etk/bin"
GADGET_PITLINK = "169.254.170.2:47500"  # GTK_PITLINK_TCP=gadget:47500 on the car
PARK_US, FLIP_US = 200, 100             # 0.10.0 defaults (GTK_SEMA_PARK_US / _FLIP_US)
FIELDS = ("fps", "ft_ms", "cpu", "ppu", "spu", "rsx", "sq", "sw", "pk", "pf")
LINE = re.compile(r"^ep=(\d+) (.*)$")
# the car's garage, run in place: the same launch/running code Pitlink's garage serves
GARAGE = ("import sys, json; sys.path[:0] = [%s]; import etk_pitlink_usbd as D; "
          "print(json.dumps(D.garage(json.loads(sys.argv[1]))))" % json.dumps(ETK_BIN))


def ssh(rig, cmd, timeout=30):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", rig, cmd],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh {cmd[:60]!r}: {r.stderr.strip()[-200:]}")
    return r.stdout


def garage(rig, req, timeout=60):
    q = json.dumps(req).replace("'", "'\\''")
    return json.loads(ssh(rig, f"python3 -c '{GARAGE}' '{q}'", timeout).strip().splitlines()[-1])


def parse_perf(text):
    """perf log text -> [{"ep": int, field: float...}] (lines missing a field are skipped)."""
    rows = []
    for ln in text.splitlines():
        m = LINE.match(ln.strip())
        if not m:
            continue
        kv = dict(p.split("=", 1) for p in m.group(2).split() if "=" in p)
        try:
            row = {k: float(kv[k]) for k in FIELDS}
        except (KeyError, ValueError):
            continue
        row["ep"] = int(m.group(1))
        rows.append(row)
    return rows


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def summarize(rows, t0, t1, park_us=PARK_US, flip_us=FLIP_US):
    """The window [t0, t1] (epoch s) of perf rows -> the run's numbers."""
    w = [r for r in rows if t0 <= r["ep"] <= t1]
    span = max(1, t1 - t0)
    s = {"intervals": len(w), "window_s": span}
    if not w:
        return s
    for k in ("fps", "cpu", "ppu", "spu", "rsx"):
        s[k + "_med"] = round(statistics.median(r[k] for r in w), 2)
    s["ft_ms_med"] = round(statistics.median(r["ft_ms"] for r in w), 2)
    s["ft_ms_p95"] = round(pct([r["ft_ms"] for r in w], 0.95), 2)
    s["fps_p05"] = round(pct([r["fps"] for r in w], 0.05), 2)
    for k in ("sq", "sw", "pk", "pf"):
        s[k + "_per_s"] = round(sum(r[k] for r in w) / span, 1)
    s["parked_cores"] = round((sum(r["pk"] for r in w) * park_us + sum(r["pf"] for r in w) * flip_us)
                              / span / 1e6, 4)
    return s


def cpu_jiffies(rig):
    f = ssh(rig, "head -1 /proc/stat").split()[1:]
    v = [int(x) for x in f]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v) - idle, sum(v)


def hottest(rig):
    out = ssh(rig, "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null")
    t = [int(x) for x in out.split() if x.lstrip("-").isdigit()]
    return round(max(t) / 1000, 1) if t else None


def emu_env(rig):
    out = ssh(rig, "p=$(pgrep -f AppRun.wrapped | head -1); [ -n \"$p\" ] && "
                   "tr '\\0' '\\n' < /proc/$p/environ | grep '^GTK_SEMA' ; true")
    return dict(ln.split("=", 1) for ln in out.split() if "=" in ln)


def arm_of(identity, env):
    """'on' / 'off': the park's state for this run -- the core's own getter: 0.10.0 parks unless
    GTK_SEMA_PARK starts with 0; a nopark core parks only on a value starting with 1."""
    v = env.get("GTK_SEMA_PARK", "")
    if "nopark" in identity:
        return "on" if v.startswith("1") else "off"
    return "off" if v.startswith("0") else "on"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pitlink_exit(rig):
    """Graceful EXIT over USB Pitlink, else an ssh tunnel (WiFi) to the car's gadget listener."""
    try:
        c = PitlinkClient("usb", timeout=6)
        c.hello()
        c.exit()
        return "usb"
    except (OSError, PitlinkError):
        pass
    port = free_port()
    tun = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                            "-L", f"{port}:{GADGET_PITLINK}", rig],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(20):
            time.sleep(0.5)
            try:
                c = PitlinkClient(f"127.0.0.1:{port}", timeout=6)
                c.hello()
                c.exit()
                return "tunnel"
            except (OSError, PitlinkError):
                continue
        return None
    finally:
        tun.terminate()


def close_game(rig):
    """Pitlink EXIT; a game that will not go (RR7's exit hang: no listener left) gets the kit's R3
    path, gtpilot.py recover (bin/recovery.sh). -> how it closed."""
    how = pitlink_exit(rig)
    for _ in range(30 if how else 0):
        if not garage(rig, {"op": "running"}).get("running"):
            return how
        time.sleep(2)
    r = subprocess.run([sys.executable, os.path.join(HERE, "gtpilot.py"), "recover"], capture_output=True,
                       text=True, timeout=90, env=dict(os.environ, ETK_RIG=rig))
    return f"recover: {r.stdout.strip()[-40:]}"


def resolve(game):
    """The operator's game names, via ./hunt's resolver (one list of names for the whole kit)."""
    from importlib.machinery import SourceFileLoader
    return SourceFileLoader("hunt_front_door", os.path.join(ETK, "hunt")).load_module().resolve(game)[0]


def next_out(serial):
    day = os.path.join(STATE, time.strftime("%Y%m%d"))
    n = 1
    while os.path.exists(os.path.join(day, f"{serial}-r{n}")):
        n += 1
    return os.path.join(day, f"{serial}-r{n}")


def run(a):
    a.game = resolve(a.game)
    a.out = a.out or next_out(a.game)
    os.makedirs(a.out, exist_ok=True)
    print(f"park_ab: {a.game} -> {a.out}", flush=True)
    rec = {"game": a.game, "label": a.label, "settle": a.settle, "secs": a.secs, "started": int(time.time())}

    def done(verdict, code, **more):
        rec.update(verdict=verdict, **more)
        with open(os.path.join(a.out, "run.json"), "w") as f:
            json.dump(rec, f, indent=1)
        print(json.dumps({k: rec.get(k) for k in ("game", "label", "arm", "verdict", "fps_med", "ft_ms_p95",
                                                  "busy_pct", "pk_per_s", "pf_per_s", "parked_cores")}))
        return code

    if not a.attach:
        if garage(a.rig, {"op": "running"}).get("running"):
            return done("BUSY", 2, note="a game is already running")
        ssh(a.rig, f"rm -f {PERF_LOG}")
        rep = garage(a.rig, {"op": "launch", "game": a.game})
        rec["launched"] = rep.get("launched", {}).get("name")
    deadline, first = time.time() + a.boot, None
    while time.time() < deadline:
        rows = parse_perf(ssh(a.rig, f"tail -n 3 {PERF_LOG} 2>/dev/null; true"))
        if rows:
            first = rows[-1]["ep"]
            break
        time.sleep(5)
    rec["identity"] = ssh(a.rig, f"head -c 400 {RIG_LOG} | head -1").strip().lstrip("﻿")
    rec["env"] = emu_env(a.rig)
    rec["arm"] = arm_of(rec["identity"], rec["env"])
    if first is None:
        alive = bool(garage(a.rig, {"op": "running"}).get("running"))
        if alive and not a.attach:
            rec["exit"] = close_game(a.rig)
        return done("NO_PERFSTAT", 4, note="no perfstat line: the title's Performance Overlay must be "
                                           "Enabled at Detail level High" if alive else "the game never came up")
    time.sleep(max(0, first + a.settle - time.time()))
    b0, n0 = cpu_jiffies(a.rig)
    t0 = int(time.time())
    time.sleep(a.secs)
    t1 = int(time.time())
    b1, n1 = cpu_jiffies(a.rig)
    rec["hottest_c"] = hottest(a.rig)
    rows = parse_perf(ssh(a.rig, f"cat {PERF_LOG} 2>/dev/null; true", timeout=60))
    park_us = int(rec["env"].get("GTK_SEMA_PARK_US", PARK_US))
    flip_us = int(rec["env"].get("GTK_SEMA_PARK_FLIP_US", FLIP_US))
    rec.update(summarize(rows, t0, t1, park_us, flip_us))
    rec["busy_pct"] = round(100.0 * (b1 - b0) / max(1, n1 - n0), 1)
    with open(os.path.join(a.out, "perf_log.txt"), "w") as f:
        f.writelines(f"ep={r['ep']} " + " ".join(f"{k}={r[k]:g}" for k in FIELDS) + "\n" for r in rows)
    alive = bool(garage(a.rig, {"op": "running"}).get("running"))
    if not a.attach and alive:
        rec["exit"] = close_game(a.rig)
    last = max((r["ep"] for r in rows), default=0)
    if not alive or last < t1 - 10:
        return done("DIED", 3, note=f"perfstat stopped {t1 - last} s before the window ended")
    return done("MEASURED", 0)


def report(dirs):
    runs = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "**", "run.json"), recursive=True)):
            with open(p) as f:
                runs.append(json.load(f))
    groups = {}
    for r in runs:
        groups.setdefault((r.get("game"), r.get("arm")), []).append(r)
    cols = ("fps_med", "fps_p05", "ft_ms_p95", "busy_pct", "spu_med", "hottest_c", "sq_per_s", "sw_per_s",
            "pk_per_s", "pf_per_s", "parked_cores")
    print(f"{'game':10} {'arm':3} {'N':>2} {'ok':>2}  " + " ".join(f"{c:>12}" for c in cols))
    for (game, arm), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        ok = [r for r in rs if r.get("verdict") == "MEASURED"]
        cells = []
        for c in cols:
            v = [r[c] for r in ok if r.get(c) is not None]
            cells.append(f"{statistics.median(v):>12g}" if v else f"{'-':>12}")
        print(f"{game:10} {str(arm):3} {len(rs):>2} {len(ok):>2}  " + " ".join(cells))
    print("(medians of per-run values; per-run fps/busy spread: see each run.json)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("game")
    r.add_argument("--out")
    r.add_argument("--settle", type=int, default=120)
    r.add_argument("--secs", type=int, default=300)
    r.add_argument("--boot", type=int, default=900, help="seconds to wait for the first perfstat line")
    r.add_argument("--label", default="")
    r.add_argument("--attach", action="store_true")
    r.add_argument("--rig", default="root@SM8250.local")
    p = sub.add_parser("report")
    p.add_argument("dirs", nargs="*", default=[STATE])
    a = ap.parse_args()
    return run(a) if a.cmd == "run" else report(a.dirs)


if __name__ == "__main__":
    sys.exit(main())
