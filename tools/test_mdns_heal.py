#!/usr/bin/env python3
"""Host harness for install.sh STEP 6.78 (mDNS name heal: the rig answers to <host>.local again).

Extracts the etk-mdns-heal.sh body install.sh writes to the rig (one source) and runs it under
/bin/sh against a fake busctl / avahi-set-host-name / ip (PATH + ETK_* overrides).

  python3 tools/test_mdns_heal.py

  [OK]       avahi already on the base name: nothing called, nothing logged
  [SELF]     avahi on SM8250-2, nobody answers SM8250.local -> avahi-set-host-name SM8250, RECLAIMED
  [ECHO]     avahi on SM8250-2, SM8250.local answered only by this rig's own address -> RECLAIMED
  [REAL]     answered by ANOTHER address (a second car, one name) -> no rename, REAL CONFLICT logged
             once across repeated runs (the timer fires every 30 s)
  [FOREIGN]  avahi on a name that is not <host>-N -> left alone, said so
  [DOWN]     avahi unreachable -> exit 0, silent
  [FAILED]   avahi-set-host-name refuses -> RECLAIM FAILED (never a silent success)
  [WIRE]     kill-switch ETK_MDNS_HEAL, a 30 s timer enabled + started, verdict read back,
             uninstall removes timer + service + script

DISCRIMINATION: against install.sh before STEP 6.78 every check fails (no body to extract).
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
m = re.search(r"cat << 'MDNSHEAL' > /storage/.config/custom_scripts/etk-mdns-heal.sh\n(.*?)\nMDNSHEAL\n", src, re.S)
body = m.group(1) if m else ""
check("install.sh carries the etk-mdns-heal.sh body", bool(body), True)

# car8 2026-10-13: gadget 169.254.170.2 + fe80::2, wlan0 192.168.1.53
IP_ADDR = """1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
    inet6 ::1/128 scope host
3: gadget: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 169.254.170.2/16 brd 169.254.255.255 scope global gadget
    inet6 fe80::2/64 scope link
4: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.1.53/24 brd 192.168.1.255 scope global wlan0
"""

FAKE_BUSCTL = """#!/bin/sh
echo "busctl $*" >> "$FAKE_CALLS"
case "$*" in
  *GetHostName*) [ -n "$FAKE_CUR" ] || { echo "Call failed: no such service" >&2; exit 1; }
                 echo "s \\"$FAKE_CUR\\"" ;;
  *ResolveHostName*) [ -n "$FAKE_ANSWER" ] || { echo "Call failed: Timeout reached" >&2; exit 1; }
                 echo "iisisu 4 0 \\"$FAKE_HOST.local\\" 0 \\"$FAKE_ANSWER\\" 4" ;;
esac
"""
FAKE_SET = """#!/bin/sh
echo "set $*" >> "$FAKE_CALLS"
exit ${FAKE_SET_RC:-0}
"""


def run(cur, answer="", set_rc=0, times=1):
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        for name, text in (("busctl", FAKE_BUSCTL), ("avahi-set-host-name", FAKE_SET),
                           ("ip", "#!/bin/sh\ncat <<'EOF'\n" + IP_ADDR + "EOF\n")):
            path = os.path.join(bindir, name)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, 0o755)
        script = os.path.join(tmp, "heal.sh")
        with open(script, "w") as f:
            f.write(body + "\n")
        log, calls = os.path.join(tmp, "heal.log"), os.path.join(tmp, "calls")
        env = dict(os.environ, PATH=bindir + ":" + os.environ["PATH"], ETK_HOSTNAME="SM8250",
                   ETK_MDNS_HEAL_LOG=log, FAKE_CALLS=calls, FAKE_CUR=cur, FAKE_ANSWER=answer,
                   FAKE_HOST="SM8250", FAKE_SET_RC=str(set_rc))
        rcs = [subprocess.run(["/bin/sh", script], env=env, capture_output=True, text=True, timeout=20).returncode
               for _ in range(times)]
        rd = lambda p: open(p).read() if os.path.exists(p) else ""
        return rcs, rd(log), [c for c in rd(calls).splitlines() if c.startswith("set ")]


def go(*a, **k):
    return run(*a, **k) if body else ([1], "", ["<no body>"])


print("[OK]")
rcs, log, sets = go("SM8250")
check("on the base name: exit 0, no rename, no log", (rcs, sets, log), ([0], [], ""))

print("[SELF]")
rcs, log, sets = go("SM8250-2")
check("renamed, base name unanswered -> avahi-set-host-name SM8250", (rcs, sets), ([0], ["set SM8250"]))
check("logged RECLAIMED (unanswered)", ("RECLAIMED SM8250.local" in log, "unanswered" in log), (True, True))

print("[ECHO]")
for own in ("192.168.1.53", "FE80::2"):
    rcs, log, sets = go("SM8250-2", answer=own)
    check(f"answered only by this rig ({own}) -> reclaimed", (sets, "answered only by this rig" in log),
          (["set SM8250"], True))

print("[REAL]")
rcs, log, sets = go("SM8250-2", answer="192.168.1.99", times=3)
check("another device holds the name -> no rename", (rcs, sets), ([0, 0, 0], []))
check("REAL CONFLICT logged once over 3 timer runs", log.count("REAL CONFLICT"), 1)
check("names the other address", "answered by 192.168.1.99" in log, True)

print("[FOREIGN]")
rcs, log, sets = go("rocknix")
check("a name that is not SM8250-N is left alone, said so", (sets, "left alone" in log), ([], True))
rcs, log, sets = go("SM8250-handheld")
check("SM8250-<non-digit> is not a conflict rename", (sets, "left alone" in log), ([], True))

print("[DOWN]")
rcs, log, sets = go("")
check("avahi unreachable: exit 0, silent", (rcs, sets, log), ([0], [], ""))

print("[FAILED]")
rcs, log, sets = go("SM8250-2", set_rc=1)
check("a refused rename logs RECLAIM FAILED", ("RECLAIM FAILED" in log, "RECLAIMED" in log), (True, False))

print("[WIRE]")
step = src[src.find("# STEP 6.78"):src.find("# STEP 6.73: PITLINK USB")] if "# STEP 6.78" in src else ""
check("kill-switch ETK_MDNS_HEAL (default 1)", '"${ETK_MDNS_HEAL:-1}" = "1"' in step, True)
timer = re.search(r"cat << 'MDNSHEALTIMER'.*?\n(.*?)\nMDNSHEALTIMER\n", src, re.S)
timer = timer.group(1) if timer else ""
check("timer: every 30 s, wanted by timers.target",
      ("OnUnitActiveSec=30s" in timer, bool(re.search(r"^WantedBy=timers\.target$", timer, re.M))), (True, True))
check("timer enabled + started, a verdict read back",
      ("systemctl enable /storage/.config/system.d/etk-mdns-heal.timer" in step,
       "systemctl restart etk-mdns-heal.timer" in step, "MDNSHEAL_OK" in step and "MDNSHEAL_FAIL" in step),
      (True, True, True))
un = open(UNINSTALL).read()
check("uninstall removes timer + service + script",
      ("etk-mdns-heal.timer" in un, "etk-mdns-heal.service" in un, "etk-mdns-heal.sh" in un), (True, True, True))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL MDNS HEAL CHECKS PASSED")
