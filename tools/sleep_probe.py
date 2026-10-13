#!/usr/bin/env python3
"""SLEEP PROBE -- name what wakes (or blocks) a rig's lid/power-key suspend.

This kernel has no CONFIG_PM_SLEEP_DEBUG (no /sys/power/pm_wakeup_irq, no pm_debug_messages),
`wakeup_count` stays 0 on every source, and dmesg time stops inside s2idle, so neither the
journal nor the counters say who ended a sleep. This recorder runs ON the rig, snapshots
/proc/interrupts + debugfs wakeup_sources + lid/cable every 0.5 s, and when a suspend happens
(suspend_stats success/fail moved) writes one JSONL event: how long the SoC really slept
(delta of BOOTTIME - MONOTONIC), lid + cable before/after, and the IRQ / wakeup-source deltas
across it. A bounce shows as slept_s ~ 0 with an IRQ the clean RTC-woken baseline lacks.
Disposable: dies at reboot, writes only its own log.

  python3 tools/sleep_probe.py --rig flip2-12g start     # push + start the recorder
  python3 tools/sleep_probe.py --rig flip2-12g report    # one row per suspend, waker candidates
  python3 tools/sleep_probe.py --rig flip2-12g stop
  (on the rig: python3 /storage/sleep_probe.py record | report)
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time

LOG = "/storage/roms/etk/etk_telemetry/sleep_probe.jsonl"
RIG_PATH = "/storage/sleep_probe.py"
PIDFILE = "/run/sleep_probe.pid"
PERIOD = 0.5
# IRQs every s2idle resume touches (the clean RTC-woken 90 s hold on car12, 2026-10-13):
# never a waker on their own, so the report ranks everything else first.
RESUME_NOISE = re.compile(r"^(IPI\d|11|14|141)$|ipcc|glink|mhi|bhi|ce\d|DP_E|msm|dsi|qcom_geni|"
                          r"apps_smmu|arm-smmu|ufs|mmc|8804|99|98|90b|909|a94|soun")


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def irqs():
    out = {}
    for line in read("/proc/interrupts").splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        key = parts[0].rstrip(":")
        total, i = 0, 1
        while i < len(parts) and parts[i].isdigit():
            total += int(parts[i])
            i += 1
        out[key] = (total, " ".join(parts[i:])[-48:])
    return out


def wakeup_sources():
    out = {}
    for line in read("/sys/kernel/debug/wakeup_sources").splitlines()[1:]:
        p = line.split()
        if len(p) >= 5:
            out[p[0]] = (int(p[1]), int(p[2]), int(p[4]))   # active_count, event_count, wakeup_count
    return out


def lid_dev():
    for line in read("/proc/bus/input/devices").split("\n\n"):
        if "gpio-keys-lid" in line:
            m = re.search(r"event(\d+)", line)
            if m:
                return "/dev/input/event%s" % m.group(1)
    return None


def lid_closed(dev):
    if not dev:
        return None
    buf = bytearray(8)
    try:
        with open(dev, "rb") as f:
            fcntl.ioctl(f, (2 << 30) | (len(buf) << 16) | (ord("E") << 8) | 0x1B, buf)  # EVIOCGSW
    except OSError:
        return None
    return bool(buf[0] & 1)                                                           # SW_LID


def thermal():
    """cluster1 temp + the fan's step: a fan trip's hysteresis puts the tsens low threshold
    just under the idle temperature, and an SoC cooling through it in s2idle is a waker."""
    for z in sorted(os.listdir("/sys/class/thermal")):
        d = "/sys/class/thermal/" + z
        if read(d + "/type") == "cluster1-thermal":
            fan = [read("%s/%s/cur_state" % (d, c)) for c in sorted(os.listdir(d)) if re.fullmatch(r"cdev\d+", c)]
            return [int(read(d + "/temp", "0") or 0) // 100 / 10, max(fan or ["?"])]
    return None


def snap(dev):
    return {
        "wall": time.time(),
        "sleep_s": time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC),
        "ok": int(read("/sys/power/suspend_stats/success", "0") or 0),
        "fail": int(read("/sys/power/suspend_stats/fail", "0") or 0),
        "lid": lid_closed(dev),
        "cable": read("/sys/class/power_supply/pm8150b-charger/online"),
        "udc": read("/sys/class/udc/a600000.usb/state"),
        "usbwake": {d: read("/sys/bus/platform/devices/%s/power/wakeup" % d)
                    for d in ("a600000.usb", "a6f8800.usb")},
        "irq": irqs(),
        "ws": wakeup_sources(),
        "therm": thermal(),
    }


def event(pre, post):
    di = sorted(((post["irq"][k][0] - v[0], k, post["irq"][k][1]) for k, v in pre["irq"].items()
                 if k in post["irq"] and post["irq"][k][0] > v[0]), reverse=True)
    dw = {k: [b - a for a, b in zip(pre["ws"][k], v)] for k, v in post["ws"].items()
          if k in pre["ws"] and v != pre["ws"][k]}
    return {
        "pre_wall": time.strftime("%F %T", time.localtime(pre["wall"])),
        "post_wall": time.strftime("%F %T", time.localtime(post["wall"])),
        "slept_s": round(post["sleep_s"] - pre["sleep_s"], 2),
        "ok": post["ok"] - pre["ok"], "fail": post["fail"] - pre["fail"],
        "lid": [pre["lid"], post["lid"]], "cable": [pre["cable"], post["cable"]],
        "udc": [pre["udc"], post["udc"]], "usbwake_pre": pre["usbwake"],
        "therm": [pre.get("therm"), post.get("therm")],
        "irq": [[d, k, n] for d, k, n in di], "ws": dw,
    }


def record():
    dev = lid_dev()
    prev = snap(dev)
    with open(LOG, "a") as f:
        f.write(json.dumps({"start": time.strftime("%F %T"), "pid": os.getpid(), "lid_dev": dev}) + "\n")
    pre = None                      # last snapshot before a suspend still settling
    while True:
        time.sleep(PERIOD)
        cur = snap(dev)
        moved = (cur["ok"], cur["fail"]) != (prev["ok"], prev["fail"]) or cur["sleep_s"] - prev["sleep_s"] > 0.05
        if moved and pre is None:
            pre = prev
        elif not moved and pre is not None:   # the clock jump and the stats tick land in different polls
            with open(LOG, "a") as f:
                f.write(json.dumps(event(pre, cur)) + "\n")
            pre = None
        prev = cur


def report():
    for line in read(LOG).splitlines():
        try:
            e = json.loads(line)
        except ValueError:                 # a hard power-off leaves a torn/zeroed tail line
            print("--- (unreadable line skipped: %d bytes)" % len(line))
            continue
        if "start" in e:
            print("--- recorder started %s (pid %s, lid %s)" % (e["start"], e["pid"], e["lid_dev"]))
            continue
        verdict = ("FREEZE-FAIL" if e["fail"] else "HELD" if e["slept_s"] >= 20 else "BOUNCE")
        lid = "/".join({True: "shut", False: "open", None: "?"}[x] for x in e["lid"])
        print("%s  %-11s slept %8.1fs  lid %-9s cable %s udc %s  usbwake %s" % (
            e["pre_wall"], verdict, e["slept_s"], lid, "/".join(e["cable"]), "/".join(e["udc"]),
            ",".join("%s=%s" % (k[:7], v[:3]) for k, v in e["usbwake_pre"].items())))
        if e.get("therm") and e["therm"][0]:
            print("      cluster1 %s -> %s C, fan step %s -> %s" % (e["therm"][0][0], e["therm"][1][0],
                                                                  e["therm"][0][1], e["therm"][1][1]))
        cand = [x for x in e["irq"] if not RESUME_NOISE.search(x[1]) and not RESUME_NOISE.search(x[2])]
        print("      irq  " + "  ".join("%s:%s(+%d)" % (k, n.split()[-1] if n else "", d) for d, k, n in cand[:10]))
        if e["ws"]:
            print("      ws   " + "  ".join("%s act+%d ev+%d wk+%d" % (k, *v) for k, v in e["ws"].items()))


def host(rig, cmd):
    alive = "[ -f %s ] && kill -0 $(cat %s) 2>/dev/null" % (PIDFILE, PIDFILE)
    if cmd == "start":
        subprocess.run(["scp", "-q", os.path.abspath(__file__), "%s:%s" % (rig, RIG_PATH)], check=True)
        subprocess.run(["ssh", rig, "%s && echo already running || { nohup python3 %s record >/dev/null 2>&1 "
                        "</dev/null & echo $! > %s; sleep 1; %s && echo recording to %s; }"
                        % (alive, RIG_PATH, PIDFILE, alive, LOG)], check=True)
    elif cmd == "stop":
        subprocess.run(["ssh", rig, "%s && kill $(cat %s) && rm -f %s && echo stopped || echo not running"
                        % (alive, PIDFILE, PIDFILE)])
    else:
        subprocess.run(["ssh", rig, "python3 %s report" % RIG_PATH])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--rig"] and len(a) == 3:
        host(a[1], a[2])
    elif a == ["record"]:
        record()
    elif a == ["report"]:
        report()
    else:
        sys.exit(__doc__)
