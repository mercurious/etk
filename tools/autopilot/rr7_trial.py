#!/usr/bin/env python3
"""rr7_trial -- one unattended Ridge Racer 7 (NPUB30457) boot trial for the 19638 regression.

Launches RR7 through the Pitlink garage (USB) and reads only RPCS3.log and the garage's
`running`, so it judges any core: the 0.8.0-era bisect mints carry no Pitlink of their own.
The bad core desyncs the RSX FIFO within ~1 s of the game's first frames (hunt-20261019-rr7
baseline on 0.10.0: sys_rsx_context_allocate at 0:25.18, `FIFO error: possible desync event
(last cmd = 0xbf800000)` at 0:25.92, the ETK anti-lock stage3b gives up at 0:25.98 and stops
the emulation; stock RPCS3 throws "Dead FIFO commands queue state" at the same point).
  PASS      the RSX came up (sys_rsx_context_allocate) and --hold seconds later the game is
            still running with no FIFO give-up and no fault
  DESYNC    stage3b's give-up or stock RPCS3's dead-FIFO throw: the regression's signature
  STALL     the hold passed with no log verdict but the screen is frozen (two frames 6 s apart are
            identical): a starved game on a core with no anti-lock watchdog (a raw build)
  STARVE    ETK anti-lock stage2 gave up: the guest stopped submitting for 30 s (GET == PUT) and the
            watchdog stopped the emulation -- a hang, not a desync (m08 r1 / m09 r3: at title.pam)
  CRASH     a guest fault, a frozen emulation or a stop that is NOT the FIFO signature, or
            the emulator gone before the hold
  TIMEOUT   the RSX never came up within --secs (covers a fresh core's PPU compile)
`SYS: Stopping emulator` is NOT a fault here: a .psn title logs it at the EBOOT -> EMAIN respawn.
desync_events counts `FIFO error: possible desync` lines either way: a good core may still log a few.
Evidence (log tail, the first desync lines, verdict.json) goes to --out. It does not recover
the car; run `gtpilot.py recover` after (the kit's R3 path).

  rr7_trial.py --out DIR [--secs 2400] [--hold 150] [--label TEXT] [--attach]
--attach judges an RR7 run the driver already launched (no launch; the log is already this boot's).
Frames come from grim on the car over ssh (read-only); --rig names the car.
Liveness = grim frames and, on a Pitlink core, the flip counter; neither answering is INCONCLUSIVE, never PASS.
Exit: 0 PASS, 1 DESYNC, 2 TIMEOUT / no game, 3 CRASH, 4 STALL, 5 INCONCLUSIVE, 6 STARVE.
Evidence also keeps the whole boot's RPCS3.log.gz.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))
from client import PitlinkClient, PitlinkError, garage  # noqa: E402

GAME = "NPUB30457"
RSX_UP = re.compile(r"sys_rsx_context_allocate\(\): Mapped address")
DESYNC = re.compile(r"FIFO desync unrecoverable|Dead FIFO commands queue state")
FAULT = re.compile(r"VM: Access violation|SYS: Emulation has been frozen|Unhandled exception|PPU: Trap")
STARVE = re.compile(r"anti-lock stage2: FIFO still starved")
DESYNC_EVENT = r"FIFO error: possible desync"
RIG_LOG = "/storage/.cache/rpcs3/RPCS3.log"
# downscaled: a full 1080p PPM over ssh missed a 5 s timeout under load (m05 r3: screen_moving null)
GRIM = "WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/var/run/0-runtime-dir timeout 10 grim -s 0.25 -t ppm -"


def frame(rig):
    """One screen grab from the car (PPM bytes), or None."""
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", rig, GRIM], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 and r.stdout.startswith(b"P6") else None


def flips():
    """The core's flip counter over Pitlink (0.10.0-based cores), or None."""
    try:
        c = PitlinkClient("usb", timeout=4)
        c.hello()
        return c.ping()[0]
    except (OSError, PitlinkError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--secs", type=float, default=2400)
    ap.add_argument("--hold", type=float, default=150)
    ap.add_argument("--label", default="")
    ap.add_argument("--attach", action="store_true")
    ap.add_argument("--rig", default="root@169.254.170.2")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    verdict = {"label": a.label, "game": GAME, "hold": a.hold}

    def evidence():
        with open(os.path.join(a.out, "log_tail.txt"), "w") as fh:
            fh.write(garage({"op": "log", "tail": 400}).get("text", ""))
        ev = garage({"op": "log", "from": 0, "grep": DESYNC_EVENT})
        with open(os.path.join(a.out, "desync_lines.txt"), "w") as fh:
            fh.write("\n".join(ev.get("text", "").splitlines()[:40]))
        # the whole boot's log: the next launch rewrites it, and a 400-line tail misses the lead-up
        # (m08 r1: the starve began 30 s before the tail's first line)
        try:
            with open(os.path.join(a.out, "RPCS3.log.gz"), "wb") as fh:
                subprocess.run(["ssh", "-o", "ConnectTimeout=5", a.rig, f"gzip -c {RIG_LOG}"],
                               stdout=fh, timeout=60)
        except subprocess.TimeoutExpired:
            pass
        return ev.get("lines", 0)

    def finish(v, code, **kw):
        verdict.update(verdict=v, secs=round(time.time() - t0), **kw)
        with open(os.path.join(a.out, "verdict.json"), "w") as f:
            json.dump(verdict, f, indent=1)
        print(json.dumps(verdict), flush=True)
        sys.exit(code)

    running = garage({"op": "running"})["running"]
    if a.attach:
        if not running or GAME not in str(running) and "Ridge Racer 7" not in str(running):
            sys.exit(f"rr7_trial: --attach needs RR7 running (running: {running})")
        log0 = 0
    else:
        if running:
            sys.exit("rr7_trial: a game is already running -- recover first (gtpilot.py recover)")
        log0 = garage({"op": "log", "tail": 1})["size"]
        garage({"op": "launch", "game": GAME})
    t0 = time.time()
    print(f"rr7_trial: {'attached' if a.attach else 'launched'} ({a.label or 'no label'})", flush=True)

    # RPCS3 rewrites RPCS3.log at boot; until then the file is the PREVIOUS boot's (gt6_trial's
    # rule: a smaller file proves the rewrite, and so does 45 s).
    rotated, rsx_t = log0 == 0, None
    while time.time() - t0 < a.secs:
        time.sleep(5)
        now = time.time()
        if not rotated:
            rotated = garage({"op": "log", "tail": 1})["size"] < log0 or now - t0 > 45
            if not rotated:
                continue
        bad = garage({"op": "log", "from": 0, "grep": DESYNC.pattern})
        if bad["lines"]:
            finish("DESYNC", 1, signature=bad["text"][-600:], rsx_secs=rsx_t and round(rsx_t - t0),
                   desync_events=evidence())
        bad = garage({"op": "log", "from": 0, "grep": STARVE.pattern})
        if bad["lines"]:
            finish("STARVE", 6, fault=bad["text"][-600:], rsx_secs=rsx_t and round(rsx_t - t0),
                   desync_events=evidence())
        bad = garage({"op": "log", "from": 0, "grep": FAULT.pattern})
        if bad["lines"]:
            finish("CRASH", 3, fault=bad["text"][-1200:], rsx_secs=rsx_t and round(rsx_t - t0),
                   desync_events=evidence())
        if rsx_t is None and garage({"op": "log", "from": 0, "grep": RSX_UP.pattern})["lines"]:
            rsx_t = now
            print(f"rr7_trial: RSX up at +{now - t0:.0f}s; holding {a.hold:.0f}s", flush=True)
        if not garage({"op": "running"})["running"] and now - t0 > 45:
            finish("CRASH", 3, fault="emulator gone with no fault line", rsx_secs=rsx_t and round(rsx_t - t0),
                   desync_events=evidence())
        if rsx_t and now - rsx_t >= a.hold:
            moving = flipping = None
            for _ in range(3):
                p1, f1 = flips(), frame(a.rig)
                time.sleep(6)
                p2, f2 = flips(), frame(a.rig)
                if f2:
                    with open(os.path.join(a.out, "last.ppm"), "wb") as fh:
                        fh.write(f2)
                if f1 and f2:
                    moving = hashlib.sha256(f1).digest() != hashlib.sha256(f2).digest()
                if p1 is not None and p2 is not None:
                    flipping = p2 > p1
                if moving is not None or flipping is not None:
                    break
            if moving is False or flipping is False:
                finish("STALL", 4, note="no log verdict, but the screen or the flip counter is frozen",
                       screen_moving=moving, flips_advancing=flipping, rsx_secs=round(rsx_t - t0),
                       desync_events=evidence())
            if moving is None and flipping is None:
                finish("INCONCLUSIVE", 5, note="no log verdict and no liveness witness answered",
                       rsx_secs=round(rsx_t - t0), desync_events=evidence())
            finish("PASS", 0, screen_moving=moving, flips_advancing=flipping, note=f"running {a.hold:.0f}s past the RSX init, no FIFO give-up",
                   rsx_secs=round(rsx_t - t0), desync_events=evidence())
    finish("TIMEOUT", 2)


if __name__ == "__main__":
    main()
