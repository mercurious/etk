#!/usr/bin/env python3
"""Host tests for etkconf.py and the autopilot tools' rig defaults (no rig, no ssh).

WHY: 2026-10-22, car8 out of service, car12 the only rig. Four autopilot defaults still named
car8 (`etk-rig`, `root@SM8250.local`, `root@169.254.170.2`, `169.254.170.2:47500`); a tool run
without its flag went looking for a car that is not on the bus (pitlink_release_validation §3).

HOW: the resolver is exercised on canned etk.conf text + env dicts (never the real etk.conf);
the tools are checked by SOURCE -- the car8 literal may survive only as the documented fallback
handed to etkconf (`etkconf.*(fallback=...)` / a FALLBACK_* constant), never as an argparse
default or a bare module constant.

DISCRIMINATION: `--against REV` reads the four tools from `git show REV:...`; against 456b078
every source check FAILs (the literals are argparse defaults there).

  python3 tools/autopilot/test_etkconf.py                 # working tree -- must PASS
  python3 tools/autopilot/test_etkconf.py --against HEAD  # pre-change -- source checks FAIL
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etkconf as E  # noqa: E402

REV = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "--against" else None
FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


def source(name):
    if REV:
        r = subprocess.run(["git", "-C", HERE, "show", f"{REV}:tools/autopilot/{name}"], capture_output=True, text=True)
        return r.stdout
    return open(os.path.join(HERE, name)).read()


print("[1] read_conf / conf_ssh")
C = E.read_conf(text='# comment\nRIG_SSH="root@SM8250.local"\nCAR="car12"\nCAR8_SSH="root@SM8250.local"\n'
                     "CAR12_SSH='root@sm8250-12gb.local'   # mDNS\nETK_BUILD_TYPE=FULL\n")
check("quoted, single-quoted and bare values parse; comments ignored",
      (C["RIG_SSH"], C["CAR12_SSH"], C["ETK_BUILD_TYPE"]), ("root@SM8250.local", "root@sm8250-12gb.local", "FULL"))
check("CAR=car12 -> CAR12_SSH, not RIG_SSH", E.conf_ssh(C), "root@sm8250-12gb.local")
check("CAR=car8 -> CAR8_SSH", E.conf_ssh(dict(C, CAR="car8")), "root@SM8250.local")
check("CAR set but its _SSH missing -> RIG_SSH", E.conf_ssh({"CAR": "car12", "RIG_SSH": "root@x"}), "root@x")
check("nothing -> None", E.conf_ssh({}), None)
check("missing file -> {}", E.read_conf(path="/nonexistent/etk.conf"), {})

print("\n[2] rig_ssh precedence: env > etk.conf > fallback")
check("env ETK_RIG wins", E.rig_ssh(conf=C, env={"ETK_RIG": "flip2-12g-usb"}), "flip2-12g-usb")
check("etk.conf next", E.rig_ssh(conf=C, env={}), "root@sm8250-12gb.local")
check("fallback last (the old car8 value, named by the caller)", E.rig_ssh(fallback="etk-rig", conf={}, env={}), "etk-rig")
check("rig_host strips user@; an alias passes through", (E.rig_host("root@a.local"), E.rig_host("flip2-12g-usb")),
      ("a.local", "flip2-12g-usb"))

print("\n[3] pitlink_tcp: PITLINK_ADDR (host:port only) > <rig host>:47500 > fallback")
check("env PITLINK_ADDR host:port", E.pitlink_tcp(conf=C, env={"PITLINK_ADDR": "169.254.170.6:47500"}), "169.254.170.6:47500")
check("env PITLINK_ADDR=usb is the broker, not a TCP address -> derived", E.pitlink_tcp(conf=C, env={"PITLINK_ADDR": "usb"}),
      "sm8250-12gb.local:47500")
check("etk.conf PITLINK_ADDR honoured", E.pitlink_tcp(conf=dict(C, PITLINK_ADDR="10.0.0.9:47500"), env={}), "10.0.0.9:47500")
check("no conf, no env -> fallback", E.pitlink_tcp(conf={}, env={}), E.FALLBACK_PITLINK_TCP)
check("ETK_RIG alone derives the host", E.pitlink_tcp(conf={}, env={"ETK_RIG": "root@169.254.170.6"}), "169.254.170.6:47500")

print(f"\n[4] no car8 literal survives as a tool default ({'rev ' + REV if REV else 'working tree'})")
LITERALS = ("etk-rig", "root@SM8250.local", "root@169.254.170.2", "169.254.170.2:47500")
for name in ("gt5_trial.py", "rr7_trial.py", "park_ab.py", "rig.py"):
    src = source(name)
    bad = []
    for ln in src.splitlines():
        code = ln.split("#", 1)[0]
        for lit in LITERALS:
            # a literal as an argparse default, a bare module constant, or an os.environ default
            # is a car8 default; the same literal inside a help string or docstring is prose
            if re.search(r'default\s*=\s*["\']%s["\']' % re.escape(lit), code) \
                    or re.match(r'\s*[A-Z_]+\s*=\s*["\']%s["\']' % re.escape(lit), code) \
                    or re.search(r'environ\.get\(\s*"ETK_RIG"\s*,\s*"%s"' % re.escape(lit), code):
                if "FALLBACK_" not in code:
                    bad.append(ln.strip()[:90])
    check(f"{name}: car8 literals only as etkconf fallbacks", bad, [])
    check(f"{name}: resolves through etkconf", "etkconf" in src, True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL ETKCONF CHECKS PASSED")
