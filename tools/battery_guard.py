#!/usr/bin/env python3
"""BATTERY GUARD TRIAL -- run install.sh STEP 6.79's own guard on a rig, disposably, before
integration (manual §1.4). Nothing under /storage/.config is touched: the script, its conf and
the three units go to /run and die at the next boot. The log (/storage/etk_battery_guard.log)
and the last power-off verdict persist, so a trial that ends in a power-off can still be read.

  python3 tools/battery_guard.py --rig flip2-12g trial [--floor N] [--check-min M] [--no-watch]
        stage the guard (floor default 10 %, asleep checks default 2 min for a trial) and start
        tools/sleep_probe.py's recorder, so every sleep also logs its battery drain.
        --floor above the battery's % forces the power-off path; --no-watch leaves only the
        sleep hooks, so a forced floor trips on the first RTC wake instead of while awake.
  python3 tools/battery_guard.py --rig flip2-12g status    # the guard's view + the probe report
  python3 tools/battery_guard.py --rig flip2-12g remove    # stop + delete the trial, clear the alarm

Refuses to stage over an installed guard (install.sh STEP 6.79): one guard per rig.
THE OPERATOR runs `trial`: the guard it stages exists to power the rig off.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.join(HERE, os.pardir, "install.sh")
TRIAL = "/run/etk-battery-guard-trial"
UNITS = {"BATTGUARDUNIT": "etk-battery-guard.service", "BATTGUARDARM": "etk-battery-guard-arm.service",
         "BATTGUARDRESUME": "etk-battery-guard-resume.service"}
INSTALLED = "/storage/.config/system.d/etk-battery-guard.service"


def bodies():
    src = open(INSTALL).read()
    m = re.search(r"cat << 'BATTGUARD' > \S+\n(.*?)\nBATTGUARD\n", src, re.S)
    if not m:
        sys.exit("BATTERY_GUARD_FAIL: install.sh carries no STEP 6.79 body")
    out = {"etk-battery-guard.sh": m.group(1) + "\n"}
    for tag, name in UNITS.items():
        u = re.search(r"cat << '%s'.*?\n(.*?)\n%s\n" % (tag, tag), src, re.S).group(1)
        u = u.replace("/storage/.config/custom_scripts/etk-battery-guard.sh", TRIAL + "/etk-battery-guard.sh")
        out[name] = u.replace("[Service]\n", "[Service]\nEnvironment=ETK_BATTERY_GUARD_CONF=%s/conf\n" % TRIAL) + "\n"
    return out


def ssh(rig, cmd, check=True):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", rig, cmd], check=check)


def trial(rig, floor, check_min, watch):
    if subprocess.run(["ssh", rig, "test -e " + INSTALLED]).returncode == 0:
        sys.exit("BATTERY_GUARD_FAIL: %s already runs the installed guard (STEP 6.79) -- nothing to trial" % rig)
    files = bodies()
    files["conf"] = "ETK_BATTERY_FLOOR=%d\nETK_BATTERY_CHECK_MIN=%d\n" % (floor, check_min)
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            with open(os.path.join(tmp, name), "w") as f:
                f.write(text)
        ssh(rig, "mkdir -p %s" % TRIAL)
        subprocess.run(["scp", "-q"] + [os.path.join(tmp, n) for n in files] + ["%s:%s/" % (rig, TRIAL)], check=True)
    units = " ".join(UNITS.values())
    start = "systemctl restart etk-battery-guard.service" if watch else "systemctl stop etk-battery-guard.service"
    ssh(rig, "cd %s && chmod +x etk-battery-guard.sh && cp %s /run/systemd/system/ && systemctl daemon-reload && "
             "systemctl enable --runtime %s >/dev/null 2>&1; %s; sleep 1; "
             "echo; echo \"TRIAL STAGED: floor %d%%, asleep checks every %d min, awake poll %s\"; "
             "ETK_BATTERY_GUARD_CONF=%s/conf sh etk-battery-guard.sh status | head -2"
             % (TRIAL, units, units, start, floor, check_min, "ON" if watch else "OFF", TRIAL))
    subprocess.run([sys.executable, os.path.join(HERE, "sleep_probe.py"), "--rig", rig, "start"])


def status(rig):
    ssh(rig, "[ -x %s/etk-battery-guard.sh ] && G=%s/etk-battery-guard.sh || G=/storage/.config/custom_scripts/"
             "etk-battery-guard.sh; ETK_BATTERY_GUARD_CONF=%s/conf sh $G status; "
             "cat /storage/etk_battery_guard.last* 2>/dev/null | sed 's/^/last power-off: /'" % (TRIAL, TRIAL, TRIAL),
        check=False)
    print()
    subprocess.run([sys.executable, os.path.join(HERE, "sleep_probe.py"), "--rig", rig, "report"])


def remove(rig):
    units = " ".join(UNITS.values())
    ssh(rig, "systemctl disable --runtime --now %s >/dev/null 2>&1; echo 0 > /sys/class/rtc/rtc0/wakealarm; "
             "cd /run/systemd/system && rm -f %s; rm -rf %s; systemctl daemon-reload; echo trial removed"
             % (units, units, TRIAL), check=False)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != "--rig" or a[2] not in ("trial", "status", "remove"):
        sys.exit(__doc__)
    rig, cmd, opts = a[1], a[2], a[3:]

    def opt(name, default):
        return int(opts[opts.index(name) + 1]) if name in opts else default

    if cmd == "trial":
        trial(rig, opt("--floor", 10), opt("--check-min", 2), "--no-watch" not in opts)
    elif cmd == "status":
        status(rig)
    else:
        remove(rig)
