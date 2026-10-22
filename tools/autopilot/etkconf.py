#!/usr/bin/env python3
"""etkconf -- the autopilot's rig defaults, read from etk.conf (never executed).

WHY: every autopilot tool used to carry car8 baked in (`etk-rig` = `SM8250.local`,
`root@169.254.170.2`, `169.254.170.2:47500`). 2026-10-22 car8 is out of service and car12 is
the only rig (USB-net 169.254.170.6, mDNS sm8250-12gb.local): a tool run without its --host/
--rig flag went looking for a car that is not on the bus. The kit already knows the car:
etk.conf's CAR= / CAR<N>_SSH= / RIG_SSH= (install.sh's own gate reads them).

HOW: precedence, highest first --
  1. the tool's own flag (callers pass it straight through)
  2. env ETK_RIG (ssh target) / PITLINK_ADDR (Pitlink TCP, the client's own knob)
  3. etk.conf CAR<N>_SSH when CAR=car<N> is set, else RIG_SSH
  4. the old hard-coded value, documented at each call site as the FALLBACK
The Pitlink TCP fallback is derived from the ssh host (`<host>:47500`, GTK_PITLINK_TCP=gadget:47500
on the car); there is no PITLINK_ADDR key in etk.conf.example today, but one is honoured if set.

  python3 tools/autopilot/etkconf.py        # prints what each default resolves to
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
CONF = os.path.join(ETK, "etk.conf")
PITLINK_PORT = 47500

FALLBACK_SSH = "etk-rig"                       # pre-2026-10-22: ~/.ssh/config alias for car8
FALLBACK_RIG = "root@SM8250.local"             # car8's mDNS name
FALLBACK_RIG_IP = "root@169.254.170.2"         # car8's USB-net address
FALLBACK_PITLINK_TCP = "169.254.170.2:47500"   # car8's gadget


def read_conf(path=CONF, text=None):
    """etk.conf KEY="value" lines, parsed, never executed (same rule as hunt/grantctl.read_conf)."""
    out = {}
    if text is None:
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            return out
    for ln in text.splitlines():
        m = re.match(r'^\s*([A-Z][A-Z0-9_]*)=("([^"]*)"|\'([^\']*)\'|(\S*))', ln)
        if m:
            out[m.group(1)] = next(g for g in m.group(3, 4, 5) if g is not None)
    return out


def conf_ssh(conf):
    """The ssh target etk.conf names for THIS checkout's car: CAR<N>_SSH for CAR=car<N>, else RIG_SSH."""
    car = (conf.get("CAR") or "").strip().lower()
    m = re.fullmatch(r"car(\d+)", car)
    if m and conf.get(f"CAR{m.group(1)}_SSH"):
        return conf[f"CAR{m.group(1)}_SSH"].strip()
    return (conf.get("RIG_SSH") or "").strip() or None


def rig_ssh(fallback=FALLBACK_RIG, conf=None, env=None):
    """user@host for ssh. env ETK_RIG > etk.conf > fallback (the old hard-coded car8 value)."""
    env = os.environ if env is None else env
    if env.get("ETK_RIG"):
        return env["ETK_RIG"]
    return conf_ssh(read_conf() if conf is None else conf) or fallback


def rig_host(ssh_target):
    """`root@host` -> `host`; an ~/.ssh/config alias passes through unchanged."""
    return ssh_target.rpartition("@")[2]


def pitlink_tcp(fallback=FALLBACK_PITLINK_TCP, conf=None, env=None):
    """host:port of the car's Pitlink TCP listener (the TCP path behind the USB broker).
    env/etk.conf PITLINK_ADDR (when it is host:port, not `usb`/`@name`) > <rig host>:47500 > fallback."""
    env = os.environ if env is None else env
    conf = read_conf() if conf is None else conf
    for cand in (env.get("PITLINK_ADDR"), conf.get("PITLINK_ADDR")):
        if cand and cand != "usb" and not cand.startswith("@") and cand.rpartition(":")[2].isdigit():
            return cand
    ssh = conf_ssh(conf) or env.get("ETK_RIG")
    return f"{rig_host(ssh)}:{PITLINK_PORT}" if ssh else fallback


if __name__ == "__main__":
    c = read_conf()
    print(f"etk.conf: {CONF} ({'read' if c else 'absent or empty'}); CAR={c.get('CAR') or '-'}")
    print(f"rig_ssh      -> {rig_ssh()}   (fallback {FALLBACK_RIG})")
    print(f"rig_host     -> {rig_host(rig_ssh())}")
    print(f"pitlink_tcp  -> {pitlink_tcp()}   (fallback {FALLBACK_PITLINK_TCP})")
