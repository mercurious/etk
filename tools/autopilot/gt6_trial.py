#!/usr/bin/env python3
"""gt6_trial -- one unattended GT6 (BCUS98296) boot trial for the 0.9.1-base deadlock.

Launches GT6 through the Pitlink garage (USB), then decides:
  PASS      a non-black frame arrived (GT6 never draws one when the deadlock forms)
  DEADLOCK  the known precursor appeared (thread 9qstY: cellUserInfoGetList, then
            sys_rsx_context_iomap io=0x2700000) and 30 s later the signature holds: the main
            thread sleeps in _sys_lwcond_queue_wait while lwmutex L1 (0x16a8d08) is owned by
            the main thread (0x01000000) with a waiter
  TIMEOUT   neither within --secs
Evidence (thread dump, the log slice since launch, last frame, verdict.json) goes to --out.
It does not recover the car; run `gtpilot.py recover` after (the kit's R3 path).
Set the run's diagnostics first: `pitlink.py garage debug_env action=set ARMSX3_...=...`.

  gt6_trial.py --out DIR [--secs 1200] [--label TEXT]
Exit: 0 PASS, 1 DEADLOCK, 2 TIMEOUT / no game.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))
from client import PitlinkClient, PitlinkError, garage  # noqa: E402

L1 = 0x16a8d08
MAIN = 0x01000000
PRECURSOR = re.compile(r"cellUserInfo: cellUserInfoGetList\(|sys_rsx_context_iomap\(context_id=0x55555555, io=0x2700000")


def thread_table(text):
    t, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"(PPU|SPU)\[(0x[0-9a-f]+)\]; State", ln)
        if m:
            cur = m.group(2)
            t[cur] = {}
            continue
        if cur:
            for k, rx in (("func", r"^In function: (.*)"), ("wait", r"^Waiting: ([\d.]+)s")):
                mm = re.match(rx, ln.strip())
                if mm and k not in t[cur]:
                    t[cur][k] = mm.group(1)
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--secs", type=float, default=1200)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    verdict = {"label": a.label, "debug_env": garage({"op": "debug_env", "action": "show"})["env"]}

    def finish(v, code, **kw):
        verdict.update(verdict=v, secs=round(time.time() - t0), **kw)
        with open(os.path.join(a.out, "verdict.json"), "w") as f:
            json.dump(verdict, f, indent=1)
        print(json.dumps(verdict), flush=True)
        sys.exit(code)

    if garage({"op": "running"})["running"]:
        sys.exit("gt6_trial: a game is already running -- recover first (gtpilot.py recover)")
    log0 = garage({"op": "log", "tail": 1})["size"]
    garage({"op": "launch", "game": "BCUS98296"})
    t0 = time.time()
    print(f"gt6_trial: launched ({a.label or 'no label'}), env {verdict['debug_env']}", flush=True)

    # RPCS3 rewrites RPCS3.log at boot; until the size drops below the pre-launch size the file
    # is still the PREVIOUS boot's, which contains the precursor too.
    c, precursor_t, last_log_check, rotated = None, None, 0.0, log0 == 0
    while time.time() - t0 < a.secs:
        if c is None:
            try:
                c = PitlinkClient("usb", timeout=4)
                c.hello()
            except (OSError, PitlinkError):
                c = None
                time.sleep(2)
                continue
        try:
            f = c.wait_frame(timeout=2.0)
        except PitlinkError:
            f = None
            if c.closed:
                c = None
        if f is not None and f.rgb().mean() > 3:
            f.png(os.path.join(a.out, "first_lit.png"))
            finish("PASS", 0, flip=f.flip, note="non-black frame")
        now = time.time()
        if now - last_log_check > 10:
            last_log_check = now
            if not rotated:
                rotated = garage({"op": "log", "tail": 1})["size"] < log0
            rep = garage({"op": "log", "from": 0, "grep": PRECURSOR.pattern}) if rotated else {"lines": 0}
            if rep["lines"] and precursor_t is None:
                precursor_t = now
                print(f"gt6_trial: precursor at +{now - t0:.0f}s:\n{rep['text'][-400:]}", flush=True)
        if precursor_t and now - precursor_t > 30:
            dump = garage({"op": "dump_threads", "timeout": 8}, timeout=60)
            text = dump.get("text", "")
            with open(os.path.join(a.out, "dump.txt"), "w") as fh:
                fh.write(text)
            tt = thread_table(text)
            mainf = tt.get("0x1000000", {}).get("func")
            l1 = None
            if c is not None:
                try:
                    l1 = c.ram_read([(L1, 8)])[0]
                except PitlinkError:
                    pass
            owner = int.from_bytes(l1[:4], "big") if l1 else None
            waiters = int.from_bytes(l1[4:8], "big") if l1 else None
            if f is not None:
                f.png(os.path.join(a.out, "last.png"))
            sig = mainf == "_sys_lwcond_queue_wait" and owner == MAIN and (waiters or 0) > 0
            info = dict(main_func=mainf, l1_owner=owner and hex(owner), l1_waiters=waiters,
                        lwmutex_blocked=[k for k, v in tt.items() if v.get("func") == "_sys_lwmutex_lock"])
            if sig:
                finish("DEADLOCK", 1, **info)
            print(f"gt6_trial: precursor seen but no deadlock signature yet: {info}", flush=True)
            precursor_t = now + 60  # look again in 90 s
        time.sleep(0.5)
    finish("TIMEOUT", 2)


if __name__ == "__main__":
    main()
