#!/usr/bin/env python3
"""SLEEP PROBE -- name what wakes (or blocks) a rig's lid/power-key suspend.

This kernel has no CONFIG_PM_SLEEP_DEBUG (no /sys/power/pm_wakeup_irq, no pm_debug_messages),
`wakeup_count` stays 0 on every source, and dmesg time stops inside s2idle, so neither the
journal nor the counters say who ended a sleep. This recorder runs ON the rig, snapshots
/proc/interrupts + debugfs wakeup_sources + lid/cable every 0.5 s, and when a suspend happens
(suspend_stats success/fail moved) writes one JSONL event: how long the SoC really slept
(delta of BOOTTIME - MONOTONIC), lid + cable before/after, and the IRQ / wakeup-source deltas
across it. A bounce shows as slept_s ~ 0 with an IRQ the clean RTC-woken baseline lacks.
It also records the battery (capacity + voltage_now, never current_now: it under-reads ~5x)
either side of each sleep, so `report` gives s2idle's drain in %/h, and the SoC's own low-power
counters (debugfs qcom_stats: aosd = AOSS sleep, cxsd = CX rail collapse, ddr): a "held" sleep
whose aosd/cxsd never move kept the SoC out of its deep states -- why s2idle drains.
Disposable: dies at reboot, writes only its own log.

  python3 tools/sleep_probe.py --rig flip2-12g start     # push + start the recorder
  python3 tools/sleep_probe.py --rig flip2-12g report    # one row per suspend, waker candidates
  python3 tools/sleep_probe.py --rig flip2-12g stop
  python3 tools/sleep_probe.py --rig flip2-12g isolate qce,slpi   # take suspects out for the next sleeps
  python3 tools/sleep_probe.py --rig flip2-12g restore            # put them back (a reboot does too)
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
ISOLATED = "/run/sleep_probe.isolated"
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


def battery():
    """capacity %, voltage mV, charger online -- what s2idle's drain is judged by."""
    b = "/sys/class/power_supply/battery/"
    cap, uv = read(b + "capacity"), read(b + "voltage_now")
    return [int(cap) if cap.isdigit() else None, int(uv) // 1000 if uv.isdigit() else None,
            read("/sys/class/power_supply/pm8150b-charger/online") not in ("", "0")]


def soc_sleep():
    """qcom_stats {state: [count, seconds]}; durations tick at 19.2 MHz."""
    out = {}
    for k in ("aosd", "cxsd", "ddr"):
        t = read("/sys/kernel/debug/qcom_stats/" + k)
        c = re.search(r"Count:\s*(\d+)", t)
        d = re.search(r"Accumulated Duration:\s*(\d+)", t)
        if c and d:
            out[k] = [int(c.group(1)), int(d.group(1)) / 19.2e6]
    return out


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
        "batt": battery(),
        "soc": soc_sleep(),
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
        "batt": [pre.get("batt"), post.get("batt")],
        "soc": {k: [v[0] - pre["soc"][k][0], round(v[1] - pre["soc"][k][1], 1)]
                for k, v in post.get("soc", {}).items() if k in pre.get("soc", {})},
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


# Suspects for "what keeps aosd/cxsd at 0": each can leave and come back at runtime, so a lid
# sleep with it out says whether it holds the SoC up. The report shows what was out.
def _qce_busy():
    busy = []
    for block in read("/proc/crypto").split("\n\n"):
        drv, ref = "", 1
        for line in block.splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "driver":
                drv = v.strip()
            elif k.strip() == "refcnt":
                ref = int(v.strip() or 1)
        if drv.endswith("-qce") and ref > 1:
            busy.append(drv)
    return busy


def _rproc(name):
    for r in sorted(os.listdir("/sys/class/remoteproc")):
        if read("/sys/class/remoteproc/%s/name" % r) == name:
            return "/sys/class/remoteproc/" + r
    return None


def _write(path, value):
    with open(path, "w") as f:
        f.write(value)


def _qce_out():
    # the crypto engine has no runtime PM: its 393600 kB/s DDR vote (tag 0 = sleep set too) never drops
    drv = "/sys/bus/platform/drivers/qcrypto/"
    devs = [d for d in os.listdir(drv) if d.endswith(".crypto")] if os.path.isdir(drv) else []
    if not devs:
        return "not bound"
    busy = _qce_busy()
    if busy:   # unregistering an algorithm in use is a kernel BUG()
        return "REFUSED, in use: " + ",".join(busy)
    _write(drv + "unbind", devs[0])
    return "unbound " + devs[0]


def _qce_in(dev):
    _write("/sys/bus/platform/drivers/qcrypto/bind", dev.split()[-1])
    return "bound"


def _slpi_out():
    r = _rproc("slpi")
    if not r or read(r + "/state") != "running":
        return "not running"
    _write(r + "/state", "stop")
    return "stopped"


def _slpi_in(_):
    _write(_rproc("slpi") + "/state", "start")
    return "started"


CPUFREQ = "/sys/bus/platform/drivers/qcom-cpufreq-hw/"


def _cpufreq_out():
    # every CPU's OPP votes DDR with tag 0 (sleep set too) at its current OPP -- 8.4 GB/s peak
    # from an idling big cluster. Unbinding frees the OPP tables, which puts (zeroes) those
    # paths. Park each policy at its minimum first: with no driver the hardware keeps the
    # last request.
    devs = [d for d in os.listdir(CPUFREQ) if d.endswith(".cpufreq")] if os.path.isdir(CPUFREQ) else []
    if not devs:
        return "not bound"
    pol = "/sys/devices/system/cpu/cpufreq/"
    for p in sorted(os.listdir(pol)):
        if p.startswith("policy"):
            _write(pol + p + "/scaling_max_freq", read(pol + p + "/cpuinfo_min_freq"))
    time.sleep(0.5)
    _write(CPUFREQ + "unbind", devs[0])
    return "unbound " + devs[0]


def _cpufreq_in(dev):
    _write(CPUFREQ + "bind", dev.split()[-1])
    return "bound (governors back to the kernel default until the next POWER apply / game launch)"


def ddr_voters():
    """Who still votes DDR (ebi): consumer -> (tag, avg, peak). Tag 0 = also in the sleep set."""
    out, on = {}, False
    for line in read("/sys/kernel/debug/interconnect/interconnect_summary").splitlines():
        if not line.startswith(" "):
            on = line.startswith("ebi@")
            continue
        p = line.split()
        if on and len(p) == 4 and (int(p[2]) or int(p[3])):
            out[p[0]] = max(out.get(p[0], (0, 0, 0)), (int(p[1]), int(p[2]), int(p[3])))
    return out


CAMCC_BL = "/storage/.config/modprobe.d/etk-sleeplab-camcc.conf"


def _camcc_out():
    # camcc-sm8250 keeps cam_cc_gdsc_clk CLK_IS_CRITICAL under the non-AO XO (bi_tcxo): XO can
    # never shut down. A critical clock cannot leave at runtime, so this blacklists the module
    # (the Flip 2 has no camera) and takes effect at the NEXT BOOT.
    if os.path.exists(CAMCC_BL):
        return "blacklisted (loaded now: %s)" % os.path.isdir("/sys/module/camcc_sm8250")
    _write(CAMCC_BL, "# ETK sleep lab (tools/sleep_probe.py): cam_cc_gdsc_clk pins XO. restore removes this.\n"
                     "blacklist camcc_sm8250\n")
    return "blacklisted -- REBOOT to take effect"


def _camcc_in(_):
    if os.path.exists(CAMCC_BL):
        os.remove(CAMCC_BL)
    return "blacklist removed (loads again at the next boot)"


def _sync_out():
    # providers whose sync_state never fires keep boot-time resources on: gcc + gpucc wait on
    # 3d6a000.gmu (no driver binds it), and rpmhpd -- which holds EVERY rail at its maximum
    # level until synced -- waits on camcc once camcc is blacklisted. Writing state_synced
    # forces the handoff that would have happened; irreversible until a reboot.
    done = []
    for root, _dirs, files in os.walk("/sys/devices"):
        if "state_synced" in files and read(root + "/state_synced") == "0":
            _write(root + "/state_synced", "1")
            done.append(os.path.basename(root))
    return "synced " + ",".join(done) if done else "already synced"


def _sync_in(_):
    return "cannot be undone -- a reboot restores the pending state"


SUSPECTS = {"qce": (_qce_out, _qce_in), "slpi": (_slpi_out, _slpi_in), "cpufreq": (_cpufreq_out, _cpufreq_in),
            "camcc": (_camcc_out, _camcc_in), "sync": (_sync_out, _sync_in)}


def standing():
    """What can still hold the SoC up: XO enables, cx perf, consumer-less GDSCs that are on."""
    xo = read("/sys/kernel/debug/clk/bi_tcxo/clk_enable_count", "?")
    camcc = read("/sys/kernel/debug/clk/cam_cc_gdsc_clk/clk_enable_count", "absent")
    gd, name, on, dev = [], None, False, False
    for line in read("/sys/kernel/debug/pm_genpd/pm_genpd_summary").splitlines() + ["end"]:
        if line[:1].isalpha():
            if name and on and not dev and not name.startswith("power-domain-cpu"):
                gd.append(name)
            p = line.split()
            name, on, dev = p[0], len(p) > 1 and p[1] == "on", False
        elif line.startswith("    ") and line.strip():
            dev = True
    return {"xo_enables": xo, "cam_cc_gdsc_clk": camcc, "cx_perf": read("/sys/kernel/debug/pm_genpd/cx/perf_state", "?"),
            "gdsc_on_unclaimed": gd}


def isolate(names):
    done = json.loads(read(ISOLATED, "{}"))
    for n in names:
        if n not in SUSPECTS:
            sys.exit("unknown suspect %s (known: %s)" % (n, ", ".join(SUSPECTS)))
        if n in done:
            print("%s: already out (%s)" % (n, done[n]))
            continue
        try:
            how = SUSPECTS[n][0]()
        except OSError as e:
            how = "FAILED: %s" % e
        print("%s: %s" % (n, how))
        if how.startswith(("unbound", "stopped", "blacklisted", "synced")):
            done[n] = how
    _write(ISOLATED, json.dumps(done))
    v, st = ddr_voters(), standing()
    print("still voting DDR: " + ("  ".join("%s tag%d %d/%d" % (k, *t) for k, t in sorted(v.items())) or "nobody"))
    print("still standing:   " + "  ".join("%s=%s" % kv for kv in st.items()))
    with open(LOG, "a") as f:
        f.write(json.dumps({"isolated": sorted(done), "at": time.strftime("%F %T"), "ddr_voters": v,
                            "standing": st}) + "\n")


def restore():
    done = json.loads(read(ISOLATED, "{}"))
    if os.path.exists(CAMCC_BL):           # the blacklist outlives /run: it is its own record
        done.setdefault("camcc", "blacklisted")
    for n, how in done.items():
        try:
            print("%s: %s" % (n, SUSPECTS[n][1](how)))
        except OSError as e:
            print("%s: RESTORE FAILED: %s -- a reboot restores it" % (n, e))
    if os.path.exists(ISOLATED):
        os.remove(ISOLATED)
    with open(LOG, "a") as f:
        f.write(json.dumps({"isolated": [], "at": time.strftime("%F %T")}) + "\n")


def report():
    held_s, held_drop = 0.0, 0         # uncabled holds only: what a lid-shut rig in a bag loses
    for line in read(LOG).splitlines():
        try:
            e = json.loads(line)
        except ValueError:                 # a hard power-off leaves a torn/zeroed tail line
            print("--- (unreadable line skipped: %d bytes)" % len(line))
            continue
        if "isolated" in e:
            print("--- %s isolated: %s" % (e["at"], ", ".join(e["isolated"]) or "none (all restored)"))
            if e.get("ddr_voters") is not None:
                print("      ddr  " + ("  ".join("%s tag%d %d/%d" % (k, *t) for k, t in sorted(e["ddr_voters"].items()))
                                       or "nobody votes DDR"))
            if e.get("standing"):
                print("      held " + "  ".join("%s=%s" % kv for kv in e["standing"].items()))
            continue
        if "start" in e:
            print("--- recorder started %s (pid %s, lid %s)" % (e["start"], e["pid"], e["lid_dev"]))
            continue
        # a bounce comes back in ~0 s; anything longer slept until its waker (see ws: the lid = you)
        verdict = ("FREEZE-FAIL" if e["fail"] else "HELD" if e["slept_s"] >= 3 else "BOUNCE")
        lid = "/".join({True: "shut", False: "open", None: "?"}[x] for x in e["lid"])
        print("%s  %-11s slept %8.1fs  lid %-9s cable %s udc %s  usbwake %s" % (
            e["pre_wall"], verdict, e["slept_s"], lid, "/".join(e["cable"]), "/".join(e["udc"]),
            ",".join("%s=%s" % (k[:7], v[:3]) for k, v in e["usbwake_pre"].items())))
        if e.get("therm") and e["therm"][0]:
            print("      cluster1 %s -> %s C, fan step %s -> %s" % (e["therm"][0][0], e["therm"][1][0],
                                                                  e["therm"][0][1], e["therm"][1][1]))
        b = e.get("batt")
        if b and b[0] and b[1] and b[0][0] is not None and b[1][0] is not None:
            drop = b[0][0] - b[1][0]
            rate = " %+.1f %%/h" % (-drop * 3600 / e["slept_s"]) if e["slept_s"] >= 600 else ""
            print("      battery %s%% -> %s%% (%s -> %s mV)%s%s" % (b[0][0], b[1][0], b[0][1], b[1][1], rate,
                                                              "  [charger]" if b[0][2] or b[1][2] else ""))
            if verdict == "HELD" and not (b[0][2] or b[1][2]):
                held_s, held_drop = held_s + e["slept_s"], held_drop + drop
        if e.get("soc"):
            print("      soc  " + "  ".join("%s +%d (%.1fs)" % (k, *v) for k, v in e["soc"].items())
                  + ("" if any(v[0] for v in e["soc"].values()) or e["slept_s"] < 3 else
                     "   <- never reached a deep state"))
        cand = [x for x in e["irq"] if not RESUME_NOISE.search(x[1]) and not RESUME_NOISE.search(x[2])]
        print("      irq  " + "  ".join("%s:%s(+%d)" % (k, n.split()[-1] if n else "", d) for d, k, n in cand[:10]))
        if e["ws"]:
            print("      ws   " + "  ".join("%s act+%d ev+%d wk+%d" % (k, *v) for k, v in e["ws"].items()))
    if held_s:
        # capacity moves in whole percent: trust the rate only over hours of sleep
        print("=== s2idle drain, uncabled holds: %d%% over %.2f h = %.2f %%/h" % (held_drop, held_s / 3600,
                                                                              held_drop * 3600 / held_s))


def host(rig, cmd):
    cmd = cmd if isinstance(cmd, list) else [cmd]
    alive = "[ -f %s ] && kill -0 $(cat %s) 2>/dev/null" % (PIDFILE, PIDFILE)
    if cmd == ["start"]:
        subprocess.run(["scp", "-q", os.path.abspath(__file__), "%s:%s" % (rig, RIG_PATH)], check=True)
        subprocess.run(["ssh", rig, "%s && echo already running || { nohup python3 %s record >/dev/null 2>&1 "
                        "</dev/null & echo $! > %s; sleep 1; %s && echo recording to %s; }"
                        % (alive, RIG_PATH, PIDFILE, alive, LOG)], check=True)
    elif cmd == ["stop"]:
        subprocess.run(["ssh", rig, "%s && kill $(cat %s) && rm -f %s && echo stopped || echo not running"
                        % (alive, PIDFILE, PIDFILE)])
    elif cmd[0] in ("isolate", "restore"):
        host(rig, "start")             # the recorder must be running to see the next sleep
        subprocess.run(["ssh", rig, "python3 %s %s" % (RIG_PATH, " ".join(cmd))])
    else:
        subprocess.run(["ssh", rig, "python3 %s report" % RIG_PATH])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--rig"] and len(a) == 3:
        host(a[1], a[2:])
    elif a[:1] == ["--rig"] and len(a) == 4 and a[2] == "isolate":
        host(a[1], ["isolate", a[3]])
    elif a == ["record"]:
        record()
    elif a == ["report"]:
        report()
    elif len(a) == 2 and a[0] == "isolate":
        isolate(a[1].split(","))
    elif a == ["restore"]:
        restore()
    else:
        sys.exit(__doc__)
