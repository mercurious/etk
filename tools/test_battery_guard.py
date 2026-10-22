#!/usr/bin/env python3
"""Host harness for install.sh STEP 6.79 (battery guard: power off at the floor, awake or asleep).

Extracts the etk-battery-guard.sh body install.sh writes to the rig (one source) and runs it under
/bin/sh against a fake sysfs (ETK_SYSFS_ROOT: battery, charger, rtc0) with systemctl / systemd-run /
busctl / etk_notify.sh stubs that record what the guard asked for.

  python3 tools/test_battery_guard.py

  [ARM]      before a suspend: the RTC alarm = since_epoch + CHECK_MIN*60, read back, the
             battery + lid recorded; near the floor the check tightens to 10 min; no RTC = ARM FAILED
  [RTC]      an RTC wake (alarm fired: reads back empty, since_epoch >= alarm) above the floor
             schedules the 5 s re-suspend and powers nothing off
  [ASLEEP]   an RTC wake at the floor on battery powers off (clean `systemctl poweroff`) and leaves
             the verdict for the next boot; on a charger only a FALLING cell does; the voltage floor
             catches a gauge that still reads healthy
  [USER]     a wake before the alarm (lid, power key) clears the alarm and changes nothing; a resume
             with nothing armed changes nothing
  [RESLEEP]  re-suspends unless the lid was opened during the 5 s look
  [AWAKE]    one poll at the floor only warns (toast); the second powers off; plugging in a charger
             stands the warning down, and a charger that cannot keep up still trips; no gauge = no trip
  [BOOT]     the next boot's watch shows the last power-off's reason on the glass, once
  [WIRE]     kill-switch + clamped knobs; the arm hook runs before EVERY suspend, the resume hook
             after; the poll restarts; a verdict is read back; uninstall removes it all

DISCRIMINATION: against install.sh before STEP 6.79 every check fails (no body to extract).
"""
import atexit
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
INSTALL = os.environ.get("ETK_INSTALL", os.path.join(ROOT, "install.sh"))
UNINSTALL = os.path.join(ROOT, "uninstall.sh")
FAILS = []
NOW = 1792626727
OFF = "systemd-run --no-block --collect --on-active=1 --timer-property=AccuracySec=100ms /usr/bin/systemctl poweroff"


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


src = open(INSTALL).read()
m = re.search(r"cat << 'BATTGUARD' > /storage/.config/custom_scripts/etk-battery-guard.sh\n(.*?)\nBATTGUARD\n", src, re.S)
body = m.group(1) if m else ""
check("install.sh carries the etk-battery-guard.sh body", bool(body), True)

STUB = """#!/bin/sh
echo "%s $*" >> "$FAKE_CALLS"
%s
"""


class Rig:
    """One fake rig: sysfs files, the guard's run dir, and the stubs' call log."""

    def __init__(self, tmp, cap="50", uv="3900000", online="0", lid="false", rtc_now=NOW, alarm=""):
        self.tmp = tmp
        self.ps = os.path.join(tmp, "sys/class/power_supply")
        self.rtc = os.path.join(tmp, "sys/class/rtc/rtc0")
        for d in (self.ps + "/battery", self.ps + "/pm8150b-charger", self.rtc, tmp + "/bin", tmp + "/etk/bin"):
            os.makedirs(d, exist_ok=True)
        self.set(cap=cap, uv=uv, online=online, rtc_now=rtc_now, alarm=alarm)
        self.lid = lid
        self.script = os.path.join(tmp, "etk-battery-guard.sh")
        with open(self.script, "w") as f:
            f.write(body + "\n")
        for name, extra in (("systemctl", ""), ("systemd-run", ""),
                            ("busctl", 'echo "b $FAKE_LID"')):
            self.stub(os.path.join(tmp, "bin", name), STUB % (name, extra))
        self.stub(os.path.join(tmp, "etk/bin/etk_notify.sh"), STUB % ("notify", ""))

    @staticmethod
    def stub(path, text):
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def put(self, path, value):
        if value is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w") as f:
            f.write(f"{value}\n")

    def set(self, cap=False, uv=False, online=False, rtc_now=False, alarm=False):
        for path, v in ((self.ps + "/battery/capacity", cap), (self.ps + "/battery/voltage_now", uv),
                        (self.ps + "/pm8150b-charger/online", online), (self.rtc + "/since_epoch", rtc_now),
                        (self.rtc + "/wakealarm", alarm)):
            if v is not False:
                self.put(path, v)

    def run(self, *args, timeout=20, **knobs):
        env = dict(os.environ, ETK_SYSFS_ROOT=self.tmp, ETK_ROOT=self.tmp + "/etk",
                   ETK_BATTERY_GUARD_CONF=self.tmp + "/none.conf",
                   ETK_BATTERY_GUARD_LOG=self.tmp + "/guard.log", ETK_BATTERY_GUARD_RUN=self.tmp + "/run",
                   ETK_BATTERY_GUARD_MARK=self.tmp + "/guard.last", FAKE_CALLS=self.tmp + "/calls",
                   FAKE_LID=self.lid, PATH=self.tmp + "/bin:" + os.environ["PATH"])
        env.update({k: str(v) for k, v in knobs.items()})
        try:
            p = subprocess.run(["/bin/sh", self.script, *args], env=env, capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode
        except subprocess.TimeoutExpired:
            return "timeout"

    def read(self, rel):
        p = os.path.join(self.tmp, rel)
        return open(p).read() if os.path.exists(p) else ""

    def calls(self):
        return self.read("calls")

    def alarm(self):
        return self.read("sys/class/rtc/rtc0/wakealarm").strip()

    def verdict(self):
        f = self.read("guard.last").split("\t")
        return f[2].strip() if len(f) > 2 else None

    def armed(self):
        return self.read("run/armed").split()


def rig(**kw):
    tmp = tempfile.mkdtemp(prefix="battguard.")
    atexit.register(shutil.rmtree, tmp, True)
    return Rig(tmp, **kw)


def asleep(cap0, lid0="true", **wake):
    """A rig armed at cap0, then woken by the RTC (alarm fired) with the battery in **wake."""
    r = rig(cap=str(cap0), lid=lid0)
    r.run("arm")
    at = int(r.armed()[0]) if r.armed() else NOW
    r.set(rtc_now=at + 1, alarm="", **wake)
    r.run("resume")
    return r


print("[ARM]")
r = rig(cap="60", lid="true")
check("exit 0", r.run("arm") if body else 1, 0)
check("RTC alarm = since_epoch + 30 min, read back", r.alarm(), str(NOW + 1800))
check("armed record: alarm, battery, lid", r.armed(), [str(NOW + 1800), "60", "true"])
check("logged", "armed: RTC wake in 1800 s" in r.read("guard.log"), True)
r = rig(cap="14")
r.run("arm")
check("within 5% of the floor the check tightens to 10 min", r.alarm(), str(NOW + 600))
r = rig(cap="60")
r.run("arm", ETK_BATTERY_CHECK_MIN=5)
check("ETK_BATTERY_CHECK_MIN sets the interval", r.alarm(), str(NOW + 300))
r = rig(cap="60", rtc_now=None)
r.run("arm")
check("no RTC clock: ARM FAILED, nothing recorded", ("ARM FAILED" in r.read("guard.log"), r.armed()), (True, []))

print("[RTC]")
r = asleep(60, cap="58")
check("above the floor: the 5 s re-suspend is scheduled", "--on-active=5 --timer-property=AccuracySec=100ms" in r.calls()
      and "resleep true" in r.calls(), True)
check("nothing powered off", "poweroff" in r.calls(), False)
check("the alarm is cleared and the record consumed", (r.alarm(), r.armed()), ("0", []))

print("[ASLEEP]")
r = asleep(12, cap="9")
check("RTC wake at the floor on battery: clean poweroff", OFF in r.calls(), True)
# systemd refuses a poweroff enqueued from inside the resume unit's own start job ("Transaction ...
# is destructive", car12 2026-10-21, 3 of 3): it must leave through a transient timer
check("...handed to a transient timer, never `systemctl poweroff` from the resume job itself",
      "systemctl poweroff --no-block" in r.calls(), False)
check("no re-suspend", "resleep" in r.calls(), False)
check("the verdict is left for the next boot", r.verdict(), "asleep")
r = asleep(8, cap="9", online="16")
check("at the floor on a charger and RISING: back to sleep", ("poweroff" in r.calls(), "resleep" in r.calls()),
      (False, True))
r = asleep(9, cap="8", online="16")
check("at the floor on a charger and FALLING (the 500 mA port): poweroff", "poweroff" in r.calls(), True)
r = asleep(40, cap="35", uv="3350000")
check("voltage under 3400 mV while the gauge reads 35%: poweroff", ("poweroff" in r.calls(),
      "3350 mV" in r.read("guard.log")), (True, True))
r = asleep(40, cap="35", uv="3350000", online="16")
check("the voltage floor never trips on a charger", "poweroff" in r.calls(), False)

print("[USER]")
r = rig(cap="40")
r.run("arm")
r.set(rtc_now=NOW + 600, cap="39")             # woken 20 min early, the alarm still set
r.run("resume")
check("a wake before the alarm: alarm cleared, nothing else", (r.alarm(), "systemd-run" in r.calls(),
      "poweroff" in r.calls()), ("0", False, False))
check("logged as an early wake", "1200 s before the alarm" in r.read("guard.log"), True)
r = rig(cap="5")
r.run("resume")
check("resume with nothing armed: no action, even at 5%", ("poweroff" in r.calls(), "no alarm was armed" in
      r.read("guard.log")), (False, True))

print("[RESLEEP]")
r = rig(lid="false")
r.run("resleep", "true")
check("lid shut at arm, open now: stays awake", "suspend" in r.calls(), False)
r = rig(lid="true")
r.run("resleep", "true")
check("lid still shut: systemctl suspend", "systemctl suspend" in r.calls(), True)
r = rig(lid="false")
r.run("resleep", "false")
check("put to sleep with the lid open (power key): suspends again", "systemctl suspend" in r.calls(), True)

print("[AWAKE]")
r = rig(cap="9")
r.run("check")
check("first poll at the floor: a toast, no poweroff", ("notify ETK BATTERY GUARD Battery 9%" in r.calls(),
      "poweroff" in r.calls()), (True, False))
r.run("check")
check("second poll: clean poweroff, verdict kept", (OFF in r.calls(),
      r.verdict()), (True, "awake"))
r = rig(cap="9")
r.run("check")
r.set(online="16")
r.run("check")
check("a charger plugged in stands the warning down", ("poweroff" in r.calls(), "stood down" in
      r.read("guard.log")), (False, True))
r.set(cap="8")
r.run("check")
r.run("check")
check("...but a charger that cannot keep up (8% < 9%) still trips", "poweroff" in r.calls(), True)
r = rig(cap="50")
r.run("check")
r.run("check")
check("healthy: no toast, no poweroff", r.calls(), "")
r = rig(cap=None, uv=None)
r.run("check")
r.run("check")
check("no gauge readable: never trips", "poweroff" in r.calls(), False)

print("[BOOT]")
r = rig(cap="60")
r.put(r.tmp + "/guard.last", "2026-10-21 03:12:00\tbattery 9% at the 10% floor, no charger\tasleep")
r.run("watch", timeout=3, ETK_BATTERY_POLL_S=60)
check("the boot toast names the last power-off", "notify ETK BATTERY GUARD Powered off by the battery guard: "
      "2026-10-21 03:12:00 battery 9% at the 10% floor, no charger" in r.calls(), True)
check("shown once (the verdict moves aside)", (os.path.exists(r.tmp + "/guard.last"),
      os.path.exists(r.tmp + "/guard.last.shown")), (False, True))

print("[WIRE]")
step = src[src.find("# STEP 6.79"):src.find("# STEP 6.73")] if "# STEP 6.79" in src else ""
check("kill-switch ETK_BATTERY_GUARD (default 1)", '"${ETK_BATTERY_GUARD:-1}" = "1"' in step, True)
check("knobs clamped (floor 3-50 %, check 5-240 min)", ('[ "$BG_FLOOR" -ge 3 ] && [ "$BG_FLOOR" -le 50 ]' in step,
      '[ "$BG_CHECK" -ge 5 ] && [ "$BG_CHECK" -le 240 ]' in step), (True, True))


def unit(tag):
    u = re.search(r"cat << '%s'.*?\n(.*?)\n%s\n" % (tag, tag), src, re.S)
    return u.group(1) if u else ""


arm, res, watch = unit("BATTGUARDARM"), unit("BATTGUARDRESUME"), unit("BATTGUARDUNIT")
check("arm runs before EVERY suspend (WantedBy + Before sleep.target, no RemainAfterExit)",
      (bool(re.search(r"^WantedBy=sleep\.target$", arm, re.M)), "Before=sleep.target" in arm,
       bool(re.search(r"^RemainAfterExit=", arm, re.M))), (True, True, False))
check("resume runs after every resume (WantedBy + After suspend.target)",
      (bool(re.search(r"^WantedBy=suspend\.target$", res, re.M)), "After=suspend.target" in res), (True, True))
check("the awake poll restarts if it dies", ("Restart=always" in watch, "WantedBy=rocknix.target" in watch),
      (True, True))
check("verdict read back: the poll active, both hooks wired", ("BATTGUARD_OK" in step, "BATTGUARD_FAIL" in step,
      "list-dependencies sleep.target" in step, "list-dependencies suspend.target" in step), (True,) * 4)
un = open(UNINSTALL).read()
check("uninstall removes the units, script, conf and any armed alarm",
      all(x in un for x in ("etk-battery-guard.service", "etk-battery-guard-arm.service",
                            "etk-battery-guard-resume.service", "etk-battery-guard.sh", "etk_battery_guard.conf",
                            "rtc0/wakealarm")), True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL BATTERY GUARD CHECKS PASSED")
