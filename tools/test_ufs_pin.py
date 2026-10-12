#!/usr/bin/env python3
"""Host harness for install.sh STEP 6.74 (the UFS clock pin, the GT5 SoC-freeze fix).

Extracts the etk-ufs-pin.sh body install.sh writes to the rig (one source) and runs it against a
fake sysfs (ETK_SYSFS_ROOT), under /bin/sh.

  python3 tools/test_ufs_pin.py

  [PIN]    a ufshc with clkscale_enable=1 is written 0 and logged PINNED with its cur_freq
  [NONE]   no ufshc (card-only board): logs "nothing to pin", exits 0
  [STUCK]  an attribute that refuses the write logs PIN FAILED (never a silent success)
  [WIRE]   install.sh's step is kill-switched (ETK_UFS_PIN), enables the unit and reads a verdict back;
           uninstall.sh removes the unit + script and restores clkscale_enable=1

DISCRIMINATION: against install.sh before STEP 6.74 every check fails (no body to extract).
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
m = re.search(r"cat << 'UFSPIN' > /storage/.config/custom_scripts/etk-ufs-pin.sh\n(.*?)\nUFSPIN\n", src, re.S)
body = m.group(1) if m else ""
check("install.sh carries the etk-ufs-pin.sh body", bool(body), True)


def run(sysfs_setup):
    with tempfile.TemporaryDirectory() as tmp:
        sysfs_setup(tmp)
        script = os.path.join(tmp, "pin.sh")
        with open(script, "w") as f:
            f.write(body + "\n")
        log = os.path.join(tmp, "pin.log")
        p = subprocess.run(["/bin/sh", script], env=dict(os.environ, ETK_SYSFS_ROOT=tmp, ETK_UFS_PIN_LOG=log),
                           capture_output=True, text=True, timeout=20)
        val = None
        attr = os.path.join(tmp, "sys/bus/platform/devices/1d84000.ufshc/clkscale_enable")
        if os.path.isfile(attr):
            val = open(attr).read().strip()
        return p.returncode, (open(log).read() if os.path.exists(log) else ""), val


def ufs(tmp, value="1", freq="37500000"):
    d = os.path.join(tmp, "sys/bus/platform/devices/1d84000.ufshc")
    os.makedirs(d)
    with open(os.path.join(d, "clkscale_enable"), "w") as f:
        f.write(value + "\n")
    f2 = os.path.join(tmp, "sys/class/devfreq/1d84000.ufshc")
    os.makedirs(f2)
    with open(os.path.join(f2, "cur_freq"), "w") as f:
        f.write(freq + "\n")


print("[PIN]")
rc, log, val = run(ufs) if body else (1, "", None)
check("exit 0", rc, 0)
check("clkscale_enable written 0", val, "0")
check("logged PINNED with the device and cur_freq", "1d84000.ufshc: clkscale_enable 1 -> 0 (PINNED, cur_freq 37500000)" in log, True)

print("[NONE]")
rc, log, _ = run(lambda tmp: os.makedirs(os.path.join(tmp, "sys/bus/platform/devices"))) if body else (1, "", None)
check("no ufshc: exit 0, says so", (rc, "nothing to pin" in log), (0, True))

print("[STUCK]")


def stuck(tmp):
    ufs(tmp)
    os.chmod(os.path.join(tmp, "sys/bus/platform/devices/1d84000.ufshc/clkscale_enable"), 0o444)


if os.geteuid() == 0:
    print("  SKIP  (root ignores the read-only bit)")
else:
    rc, log, val = run(stuck) if body else (1, "", None)
    check("a refused write logs PIN FAILED, value unchanged", ("PIN FAILED" in log, val), (True, "1"))

print("[WIRE]")
step = src[src.find("# STEP 6.74"):src.find("# STEP 6.73")] if "# STEP 6.74" in src else ""
check("kill-switch ETK_UFS_PIN (default 1)", '"${ETK_UFS_PIN:-1}" = "1"' in step, True)
check("unit enabled + a verdict read back", ("systemctl enable /storage/.config/system.d/etk-ufs-pin.service" in step,
                                             "UFSPIN_OK" in step and "UFSPIN_FAIL" in step), (True, True))
un = open(UNINSTALL).read()
check("uninstall removes unit + script", ("etk-ufs-pin.service" in un, "etk-ufs-pin.sh" in un), (True, True))
check("uninstall restores clkscale_enable=1", 'echo 1 > "$d/clkscale_enable"' in un, True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL UFS PIN CHECKS PASSED")
