#!/usr/bin/env python3
"""Host harness for install.sh STEP 6.77 (USB wake off: a cabled rig's suspend holds).

Extracts the etk-usb-nowake.sh body install.sh writes to the rig (one source) and runs it against a
fake sysfs (ETK_SYSFS_ROOT), under /bin/sh.

  python3 tools/test_usb_nowake.py

  [OFF]    the qcom glue + dwc3 core with wakeup=enabled are both written disabled and logged OFF
  [THERM]  both tsens blocks (c263000/c265000.thermal-sensor) are written disabled too -- a fan-warm
           SoC cooling through its 40 C threshold in s2idle storms tsens and bounced car12 (2026-10-13)
  [SKIP]   a *.usb already disabled, or with no power/wakeup (not wake-capable), and a non-USB
           device with wakeup=enabled (the lid, the power key), are left alone
  [NONE]   nothing wake-enabled: logs "nothing to do", exits 0
  [STUCK]  an attribute that refuses the write logs OFF FAILED (never a silent success)
  [WIRE]   install.sh's step is kill-switched (ETK_USB_NOWAKE), enables the unit and reads a verdict
           back (any *.usb still enabled = FAIL); the unit re-runs before EVERY suspend (a gadget
           bind re-arms dwc3's wake); uninstall.sh removes the unit + script

DISCRIMINATION: against install.sh before STEP 6.77 every check fails (no body to extract); against
the USB-only 6.77 (2cfc8d1) [THERM] fails.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
INSTALL = os.environ.get("ETK_INSTALL", os.path.join(ROOT, "install.sh"))
UNINSTALL = os.path.join(ROOT, "uninstall.sh")
FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


src = open(INSTALL).read()
m = re.search(r"cat << 'USBNOWAKE' > /storage/.config/custom_scripts/etk-usb-nowake.sh\n(.*?)\nUSBNOWAKE\n", src, re.S)
body = m.group(1) if m else ""
check("install.sh carries the etk-usb-nowake.sh body", bool(body), True)

DEVS = "sys/bus/platform/devices"


def dev(tmp, name, wakeup):
    d = os.path.join(tmp, DEVS, name)
    os.makedirs(d)
    if wakeup is not None:
        os.makedirs(os.path.join(d, "power"))
        with open(os.path.join(d, "power/wakeup"), "w") as f:
            f.write(wakeup + "\n")


def wake(tmp, name):
    p = os.path.join(tmp, DEVS, name, "power/wakeup")
    return open(p).read().strip() if os.path.isfile(p) else None


def run(sysfs_setup, names):
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, DEVS))
        sysfs_setup(tmp)
        script = os.path.join(tmp, "nowake.sh")
        with open(script, "w") as f:
            f.write(body + "\n")
        log = os.path.join(tmp, "nowake.log")
        p = subprocess.run(["/bin/sh", script], env=dict(os.environ, ETK_SYSFS_ROOT=tmp, ETK_USB_NOWAKE_LOG=log),
                           capture_output=True, text=True, timeout=20)
        return p.returncode, (open(log).read() if os.path.exists(log) else ""), {n: wake(tmp, n) for n in names}


def sm8250(tmp):
    # car12's live set 2026-10-11: glue + core wake-enabled, the lid wake-enabled (must stay)
    dev(tmp, "a6f8800.usb", "enabled")
    dev(tmp, "a600000.usb", "enabled")
    dev(tmp, "gpio-keys-lid", "enabled")


print("[OFF]")
rc, log, w = run(sm8250, ["a6f8800.usb", "a600000.usb"]) if body else (1, "", {})
check("exit 0", rc, 0)
check("glue + core written disabled", w, {"a6f8800.usb": "disabled", "a600000.usb": "disabled"})
check("each logged OFF", ("a6f8800.usb: wakeup enabled -> disabled (OFF)" in log,
                          "a600000.usb: wakeup enabled -> disabled (OFF)" in log), (True, True))

print("[THERM]")


def tsens(tmp):
    sm8250(tmp)
    dev(tmp, "c263000.thermal-sensor", "enabled")
    dev(tmp, "c265000.thermal-sensor", "enabled")


rc, log, w = run(tsens, ["c263000.thermal-sensor", "c265000.thermal-sensor", "gpio-keys-lid"]) if body else (1, "", {})
check("both tsens blocks written disabled, the lid untouched", w,
      {"c263000.thermal-sensor": "disabled", "c265000.thermal-sensor": "disabled", "gpio-keys-lid": "enabled"})
check("USB + thermal: four logged OFF", log.count("(OFF)"), 4)
check("verdict counts a wake-armed thermal sensor as a failure", "*.thermal-sensor; do" in src, True)

print("[SKIP]")


def mixed(tmp):
    sm8250(tmp)
    dev(tmp, "a8f8800.usb", "disabled")
    dev(tmp, "a800000.usb", None)


rc, log, w = run(mixed, ["gpio-keys-lid", "a8f8800.usb"]) if body else (1, "", {})
check("the lid keeps its wake; an already-off controller is untouched",
      w, {"gpio-keys-lid": "enabled", "a8f8800.usb": "disabled"})
check("only the two it changed are logged", (log.count("(OFF)"), "a8f8800" in log, "a800000" in log), (2, False, False))

print("[NONE]")
rc, log, _ = run(lambda tmp: dev(tmp, "a6f8800.usb", "disabled"), []) if body else (1, "", {})
check("nothing wake-enabled: exit 0, says so", (rc, "nothing to do" in log), (0, True))

print("[STUCK]")


def stuck(tmp):
    dev(tmp, "a600000.usb", "enabled")
    os.chmod(os.path.join(tmp, DEVS, "a600000.usb/power/wakeup"), 0o444)


if os.geteuid() == 0:
    print("  SKIP  (root ignores the read-only bit)")
else:
    rc, log, w = run(stuck, ["a600000.usb"]) if body else (1, "", {})
    check("a refused write logs OFF FAILED, value unchanged", ("OFF FAILED" in log, w.get("a600000.usb")), (True, "enabled"))

print("[WIRE]")
step = src[src.find("# STEP 6.77"):src.find("# STEP 6.73")] if "# STEP 6.77" in src else ""
check("kill-switch ETK_USB_NOWAKE (default 1)", '"${ETK_USB_NOWAKE:-1}" = "1"' in step, True)
check("unit enabled + a verdict read back", ("systemctl enable /storage/.config/system.d/etk-usb-nowake.service" in step,
                                             "USBNOWAKE_OK" in step and "USBNOWAKE_FAIL" in step), (True, True))
check("verdict fails while any *.usb is still wake-enabled", '[ -z "$on" ]' in step, True)
unit = re.search(r"cat << 'USBNOWAKEUNIT'.*?\n(.*?)\nUSBNOWAKEUNIT\n", src, re.S)
unit = unit.group(1) if unit else ""
# a gadget bind (Pitlink attach, usbgadget start) re-arms dwc3's wake: a boot-only oneshot loses
check("re-runs before EVERY suspend (WantedBy + Before sleep.target, no RemainAfterExit)",
      (bool(re.search(r"^WantedBy=.*\bsleep\.target\b", unit, re.M)), "Before=sleep.target" in unit,
       bool(re.search(r"^RemainAfterExit=", unit, re.M))), (True, True, False))
check("verdict requires the sleep.target hook", "list-dependencies sleep.target" in step, True)
un = open(UNINSTALL).read()
check("uninstall removes unit + script", ("etk-usb-nowake.service" in un, "etk-usb-nowake.sh" in un), (True, True))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL USB NOWAKE CHECKS PASSED")
