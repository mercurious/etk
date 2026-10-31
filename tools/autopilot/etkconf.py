#!/usr/bin/env python3
"""etkconf -- the autopilot's rig defaults, read from etk.conf (never executed).

WHY: every autopilot tool used to carry car8 baked in (`etk-rig` = `SM8250.local`,
`root@169.254.170.2`, `169.254.170.2:47500`). 2026-10-22 car8 is out of service and car12 is
the only rig (USB-net 169.254.170.6, mDNS sm8250-12gb.local): a tool run without its --host/
--rig flag went looking for a car that is not on the bus. The kit already knows the car:
etk.conf's CAR= / CARS= / RIG_SSH= (install.sh's own gate reads them).

HOW: precedence, highest first --
  1. the tool's own flag (callers pass it straight through)
  2. env ETK_RIG (ssh target) / PITLINK_ADDR (Pitlink TCP, the client's own knob)
  3. the ssh of etk.conf's CAR= in CARS= (or its CAR<N>_SSH=, the original scheme), else RIG_SSH
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

FALLBACK_SSH = "etk-rig"                       # the ~/.ssh/config alias scripts/etk_pair.sh writes
FALLBACK_RIG = "root@SM8250.local"             # ROCKNIX's default mDNS name on this SoC
FALLBACK_RIG_IP = "root@169.254.170.2"         # the first USB-net address etk_pair.sh expects
FALLBACK_PITLINK_TCP = "169.254.170.2:47500"   # the same gadget


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


def cars(conf):
    """{name: {"ssh", "ram_gb"}} in etk.conf order: CARS="name,ssh[,ramGB] ...", then any CAR<N>_SSH= (an N GB car<N>)."""
    out = {}
    for entry in (conf.get("CARS") or "").split():
        name, ssh, ram = (entry.split(",") + ["", ""])[:3]
        out[name] = {"ssh": ssh or None, "ram_gb": int(ram) if ram.isdigit() else None}
    for key, ssh in conf.items():
        m = re.fullmatch(r"CAR(\d+)_SSH", key)
        if m:
            out.setdefault(f"car{m.group(1)}", {"ssh": ssh.strip() or None, "ram_gb": int(m.group(1))})
    return out


def car_ssh(conf, car):
    """The ssh target etk.conf names for a car, or None."""
    return cars(conf).get(car, {}).get("ssh")


def conf_ssh(conf):
    """The ssh target etk.conf names for THIS checkout's car: CAR='s, else RIG_SSH."""
    return car_ssh(conf, (conf.get("CAR") or "").strip()) or (conf.get("RIG_SSH") or "").strip() or None


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
