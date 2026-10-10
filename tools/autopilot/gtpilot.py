#!/usr/bin/env python3
"""gtpilot — the ETK autopilot's host CLI (Engineer drives the rig).

  gtpilot.py doctor              read-only: every channel's readiness
  gtpilot.py arm                 write ipc.yml (PINE on) + the etk_autopilot
                                 input config on the rig (idempotent; prints
                                 what changed). `disarm` removes both.
  gtpilot.py launch <SERIAL>     gated launch (no RPCS3 already up) with
                                 Player 1 = the virtual pad (--input-config)
  gtpilot.py frame [out.png]     grim the live screen to the host
  gtpilot.py pine                PINE: status / title id / version
  gtpilot.py padtest             open the pad, wiggle, release, close
  gtpilot.py layout              committed guest RAM ranges

SESSION (one agent, one pad, held across commands — a pad that came and went
per command would read to the game as a controller disconnect each time):
  gtpilot.py serve               hold the session (run in the background);
                                 Ctrl-C / kill = dead-man: inputs released,
                                 pad destroyed
  gtpilot.py tap cross [--ms 120]        press, hold, release (rig-timed)
  gtpilot.py tap --dpad down             d-pad taps the same way
  gtpilot.py hold lx=-0.3 r2=0.6 cross=1 set and KEEP (sticks -1..1,
                                         triggers 0..1, buttons 0/1)
  gtpilot.py release                     everything neutral
  gtpilot.py read 0x1234:f32 ...         live guest reads (big-endian decode)
  gtpilot.py watch --hz 30 --secs 3 0x1234:f32 ...
  gtpilot.py hexdump 0x1234 [n]
  gtpilot.py call <op> '<json>'          any agent op, raw
  gtpilot.py snap out.npz [ranges]       bulk RAM snapshot -> host (no session)

Channels: CONTROL = uinput pad (rig_agent.VPad) bound by launch-time
--input-config; menus can also use InputPlumber SendButtonChord on the
operator's own pad. RAM = PINE (live) + /proc/<pid>/mem (bulk). VISION = grim.
The operator's L1+R3 panic (input_d, evdev) is untouched by all of it.
"""
import argparse
import json
import os
import shlex
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import Rig, RIG_ENV, RpcError  # noqa: E402

CFG = "/storage/.config/rpcs3"
PAD_NAME = "ETK Autopilot Pad"
INPUT_CFG = "etk_autopilot"

ARM = r"""
C=%(cfg)s
if [ -f $C/ipc.yml ] && grep -q "IPC Server enabled: true" $C/ipc.yml; then
  echo "ipc.yml: PINE already on"
else
  [ -f $C/ipc.yml ] && cp -f $C/ipc.yml $C/ipc.yml.etkbak-autopilot
  printf 'IPC Server enabled: true\nIPC Port: 28012\n' > $C/ipc.yml.tmp && mv -f $C/ipc.yml.tmp $C/ipc.yml
  echo "ipc.yml: PINE ON (takes effect at the next RPCS3 launch)"
fi
G=$C/input_configs/global
# Player 1's Device line only; Handler stays SDL, every binding stays the operator's.
awk -v dev="  Device: %(pad)s 1" '
  /^Player 1 Input:/ {p1=1}
  /^Player [2-7] Input:/ {p1=0}
  p1 && /^  Device: / && !done {print dev; done=1; next}
  {print}' $G/Default.yml > $G/%(icfg)s.yml.tmp && mv -f $G/%(icfg)s.yml.tmp $G/%(icfg)s.yml
echo "input config: $G/%(icfg)s.yml (Player 1 -> %(pad)s 1); Default.yml untouched:"
diff $G/Default.yml $G/%(icfg)s.yml | sed 's/^/  /'
"""

DISARM = r"""
C=%(cfg)s
if [ -f $C/ipc.yml.etkbak-autopilot ]; then mv -f $C/ipc.yml.etkbak-autopilot $C/ipc.yml; else rm -f $C/ipc.yml; fi
rm -f $C/input_configs/global/%(icfg)s.yml
echo "disarmed: PINE back to its prior state, autopilot input config removed"
"""

DOCTOR = r"""
C=%(cfg)s
echo "rig: $(hostname)  up $(cut -d' ' -f1 /proc/uptime)s  sentry=$(cat /dev/shm/etk_shm/active_id.txt 2>/dev/null)"
echo "uinput: $(ls -l /dev/uinput 2>&1 | cut -c1-10)"
echo "ipc.yml: $(tr '\n' ' ' < $C/ipc.yml 2>/dev/null || echo ABSENT)"
echo "input config: $(ls $C/input_configs/global/%(icfg)s.yml 2>/dev/null || echo ABSENT)"
echo "rpcs3: $(pgrep -f 'rpcs3-sa|AppRun.wrapped' | tr '\n' ' ')"
ls -l /var/run/0-runtime-dir/rpcs3.sock /tmp/rpcs3.sock 2>/dev/null
echo "inputplumber: $(systemctl is-active inputplumber)"
echo "core: $(cat /storage/rpcs3/rpcs3-sa.custom.src 2>/dev/null)"
"""

# Mirrors start_rpcs3.sh's boot-path logic (.psn -> EBOOT, .m3u -> ISO) and
# its --no-gui exec; the ETK wrapper (/usr/bin/rpcs3-sa) passes argv through,
# so the per-title CORE swap still applies.
LAUNCH = r"""
if pgrep -f 'rpcs3-sa|AppRun.wrapped' >/dev/null; then echo "REFUSED: an RPCS3 is already running (R3 it, or recovery.sh)"; exit 3; fi
S=%(serial)s
P=""
if [ -d /storage/.config/rpcs3/dev_hdd0/game/$S/USRDIR ] && [ -f /storage/.config/rpcs3/dev_hdd0/game/$S/USRDIR/EBOOT.BIN ]; then
  P=/storage/.config/rpcs3/dev_hdd0/game/$S/USRDIR/EBOOT.BIN
fi
if [ -z "$P" ]; then
  I=$(grep "^$S:" /storage/.config/rpcs3/games.yml | sed 's/^[^:]*: *//')
  [ -n "$I" ] && P="$I"
fi
[ -z "$P" ] && { echo "REFUSED: cannot resolve $S to a boot path"; exit 4; }
echo "boot: $P"
setsid sh -c '. /etc/profile >/dev/null 2>&1; export HOME=/storage %(env)s SDL_VIDEODRIVER=wayland QT_QPA_PLATFORM=xcb; exec /usr/bin/rpcs3-sa --no-gui --input-config %(icfg)s "$0"' "$P" </dev/null >/dev/null 2>&1 &
sleep 2; pgrep -f 'rpcs3-sa|AppRun.wrapped' | head -3
"""


def sock_path(host):
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"gtpilot-{host}.sock")


def client(host, msg, timeout=120):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path(host))
    except OSError:
        sys.exit(f"no session: run `gtpilot.py serve` first ({sock_path(host)})")
    with s:
        s.sendall((json.dumps(msg) + "\n").encode())
        line = s.makefile("rb").readline()
    r = json.loads(line) if line else {"ok": False, "err": "session closed"}
    if not r.get("ok"):
        sys.exit("ERR " + str(r.get("err")))
    r.pop("ok", None)
    r.pop("id", None)
    return r


def serve(rig):
    path = sock_path(rig.host)
    if os.path.exists(path):
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.connect(path)
            probe.close()
            sys.exit(f"a session is already serving at {path}")
        except OSError:
            os.unlink(path)  # stale
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ag = rig.agent()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        pad = ag.call("pad_open")
        srv.bind(path)
        srv.listen(8)
        print(f"[serve] {rig.host} agent pid {ag.hello['pid']}; pad {pad['name']} at {pad['node']}; "
              f"listening {path}", flush=True)
        while True:
            conn, _ = srv.accept()
            with conn:
                f = conn.makefile("rwb")
                line = f.readline()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    op = m.pop("op")
                    if op == "_stop":
                        f.write(b'{"ok": true}\n')
                        f.flush()
                        break
                    r = ag.call(op, **m)
                except RpcError as e:
                    r = {"ok": False, "err": str(e)}
                    if "agent died" in str(e):
                        f.write((json.dumps(r) + "\n").encode())
                        f.flush()
                        raise
                except Exception as e:
                    r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
                print(time.strftime("[%H:%M:%S] ") + line.decode().strip()[:160]
                      + ("" if r.get("ok") else "  -> " + str(r.get("err"))), flush=True)
                f.write((json.dumps(r) + "\n").encode())
                f.flush()
    finally:
        srv.close()
        if os.path.exists(path):
            os.unlink(path)
        ag.close()  # dead-man: release + destroy on the rig
        print("[serve] closed; pad released and destroyed", flush=True)


def parse_items(specs):
    items = []
    for s in specs:
        a, _, k = s.partition(":")
        items.append([int(a, 0), k or "u32"])
    return items


def parse_hold(specs):
    from rig_agent import AXES, BUTTONS
    btn, axes, dpad = {}, {}, None
    for s in specs:
        k, _, v = s.partition("=")
        if k == "dpad":
            dpad = [d for d in v.split(",") if d]
        elif k in AXES:
            axes[k] = float(v)
        elif k in BUTTONS:
            btn[k] = int(float(v or 1))
        else:
            sys.exit(f"unknown control {k} (axes {sorted(AXES)}; buttons {sorted(BUTTONS)}; dpad=up,left)")
    msg = {"op": "pad"}
    if btn:
        msg["btn"] = btn
    if axes:
        msg["axes"] = axes
    if dpad is not None:
        msg["dpad"] = dpad
    return msg


def live_values(h, addr, kind):
    import numpy as np
    vals = []
    for i in range(0, len(addr), 50000):
        chunk = [[int(x), kind] for x in addr[i:i + 50000]]
        vals += client(h, {"op": "read", "items": chunk}, timeout=120)["v"]
    return np.array([np.nan if v is None else v for v in vals], dtype=np.float64)


def scan(a, h):
    """RAM search. First pass on a snapshot; narrow on snapshots or LIVE."""
    import numpy as np
    import ramscan as rs
    out = a.out or a.cand
    if a.action == "new":
        meta, regions = rs.load_snap(a.snap)
        addr = rs.first_pass(regions, a.kind, a.a, a.b, limit=5_000_000)
        rs.save_cand(out, addr, a.kind)
        print(f"{len(addr)} candidates ({a.kind} in [{a.a}, {a.b}]) -> {out}")
        return
    addr, kind = rs.load_cand(a.cand)
    if a.action == "snapfilter":
        _, regions = rs.load_snap(a.snap)
        vals = rs.values_at(regions, addr, kind).astype(np.float64)
        ref = rs.values_at(rs.load_snap(a.ref)[1], addr, kind).astype(np.float64) if a.ref else None
        keep = rs.filt(addr, vals, a.op, a.a, a.b, ref)
    elif a.action in ("live", "show"):
        vals = live_values(h, addr, kind)
        if a.action == "show":
            z = np.load(a.cand)
            last = z["last"] if "last" in z.files else None
            for i, (ad, v) in enumerate(zip(addr[:a.n], vals[:a.n])):
                prev = f"   (was {last[i]:.6g})" if last is not None and i < len(last) else ""
                print(f"  {int(ad):#010x}  {v:.6g}{prev}")
            print(f"  ... {len(addr)} total")
            np.savez(a.cand, addr=addr.astype(np.uint32), kind=kind, last=vals)
            return
        z = np.load(a.cand)
        ref = z["last"] if "last" in z.files else None
        if a.op in ("gt", "lt", "same", "changed") and ref is None:
            sys.exit("live gt/lt/same/changed needs a prior `scan show` or `scan live` (stores `last`)")
        m_addr = rs.filt(addr, vals, a.op, a.a, a.b, ref)
        keep_idx = np.isin(addr, m_addr)
        np.savez(out, addr=addr[keep_idx].astype(np.uint32), kind=kind, last=vals[keep_idx])
        print(f"{len(addr)} -> {keep_idx.sum()} candidates ({a.op}) -> {out}")
        return
    rs.save_cand(out, keep, kind)
    print(f"{len(addr)} -> {len(keep)} candidates ({a.op}) -> {out}")


def fmt(s, **kw):
    kw.setdefault("cfg", CFG)
    kw.setdefault("pad", PAD_NAME)
    kw.setdefault("icfg", INPUT_CFG)
    kw.setdefault("env", RIG_ENV)
    return s % kw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("doctor", "arm", "disarm", "pine", "padtest", "layout", "serve", "release", "stop"):
        sub.add_parser(c)
    p = sub.add_parser("launch")
    p.add_argument("serial")
    p = sub.add_parser("frame")
    p.add_argument("out", nargs="?", default=f"gt_{int(time.time())}.png")
    p = sub.add_parser("tap")
    p.add_argument("btn", nargs="*")
    p.add_argument("--dpad", default="")
    p.add_argument("--ms", type=int, default=120)
    p.add_argument("--wait", type=float, default=1.5, help="seconds before --frame")
    p.add_argument("--frame", default=None, help="grab a frame to this path after the tap")
    p = sub.add_parser("hold")
    p.add_argument("ctl", nargs="+")
    p = sub.add_parser("read")
    p.add_argument("items", nargs="+")
    p.add_argument("--via", default="mem")
    p = sub.add_parser("watch")
    p.add_argument("items", nargs="+")
    p.add_argument("--hz", type=float, default=30)
    p.add_argument("--secs", type=float, default=3)
    p = sub.add_parser("hexdump")
    p.add_argument("addr")
    p.add_argument("n", nargs="?", type=int, default=256)
    p = sub.add_parser("call")
    p.add_argument("op")
    p.add_argument("args", nargs="?", default="{}")
    p = sub.add_parser("snap")
    p.add_argument("out")
    p.add_argument("ranges", nargs="?", default="all")
    sub.add_parser("inspect")  # read-only: input-relevant config + sway focus
    sub.add_parser("recover")  # the kit's R3 path (bin/recovery.sh), nothing else
    p = sub.add_parser("scan", help="RAM search: new | snapfilter | live | show")
    p.add_argument("action", choices=["new", "snapfilter", "live", "show"])
    p.add_argument("--snap")
    p.add_argument("--ref", help="earlier snap (.npz) for gt/lt/same/changed")
    p.add_argument("--cand", default="cand.npz")
    p.add_argument("--out")
    p.add_argument("--kind", default="f32")
    p.add_argument("--op", default="range", choices=["range", "eq", "gt", "lt", "same", "changed"])
    p.add_argument("--a", type=float)
    p.add_argument("--b", type=float)
    p.add_argument("--n", type=int, default=30)
    p = sub.add_parser("sdlprobe")  # read-only: the pad through RPCS3's own libSDL3
    p.add_argument("secs", nargs="?", type=float, default=8.0)
    p = sub.add_parser("log")  # read-only: RPCS3.log lines matching a regex
    p.add_argument("pattern", nargs="?", default=".")
    p.add_argument("-n", type=int, default=40)
    a = ap.parse_args()
    rig = Rig(a.host)
    h = rig.host

    if a.cmd == "serve":
        serve(rig)
    elif a.cmd == "stop":
        client(h, {"op": "_stop"})
    elif a.cmd == "release":
        client(h, {"op": "release"})
    elif a.cmd == "tap":
        msg = {"op": "tap", "btn": a.btn, "ms": a.ms}
        if a.dpad:
            msg["dpad"] = a.dpad.split(",")
        if a.btn or a.dpad:
            client(h, msg)
        if a.frame:
            time.sleep(a.wait)
            print(rig.frame(a.frame))
    elif a.cmd == "hold":
        print(client(h, parse_hold(a.ctl)))
    elif a.cmd == "read":
        r = client(h, {"op": "read", "items": parse_items(a.items), "via": a.via})
        for s, v in zip(a.items, r["v"]):
            print(f"{s:>20}  {v}")
    elif a.cmd == "watch":
        r = client(h, {"op": "watch", "items": parse_items(a.items), "hz": a.hz, "secs": a.secs},
                   timeout=a.secs + 30)
        print("t\t" + "\t".join(a.items))
        for row in r["rows"]:
            print("\t".join(f"{x:.4g}" if isinstance(x, float) else str(x) for x in row))
    elif a.cmd == "hexdump":
        b = bytes.fromhex(client(h, {"op": "block", "addr": a.addr, "n": a.n})["hex"])
        base = int(a.addr, 0)
        for i in range(0, len(b), 16):
            row = b[i:i + 16]
            print(f"{base + i:08x}  {row.hex(' ', 4):<35}  "
                  + "".join(chr(c) if 32 <= c < 127 else "." for c in row))
    elif a.cmd == "call":
        print(json.dumps(client(h, {"op": a.op, **json.loads(a.args)}), indent=1))
    elif a.cmd == "snap":
        import numpy as np
        t = time.time()
        meta, regions = rig.snap(a.ranges)
        np.savez(a.out, meta=json.dumps(meta),
                 **{f"r{g0:08x}": np.frombuffer(b, dtype=np.uint8) for g0, b in regions.items()})
        mb = sum(len(b) for b in regions.values()) / 2**20
        print(f"{a.out}: {mb:.1f} MB in {len(regions)} ranges, {time.time() - t:.1f} s")
    elif a.cmd == "scan":
        scan(a, h)
    elif a.cmd == "recover":
        # Same entry the R3 chord and commander.sh use — recovery.sh is the ONE
        # definition of nuclear recovery (crash frame, kill, SHM flush, Sentry
        # handoff). Never re-inline it here.
        print(rig.sh("setsid /bin/bash /storage/games-internal/roms/etk/bin/recovery.sh "
                     "</dev/null >/dev/null 2>&1 & sleep 4; "
                     "pgrep -f 'rpcs3-sa|AppRun.wrapped' >/dev/null && echo 'rpcs3 STILL UP' || echo 'rpcs3 down'",
                     timeout=30))
    elif a.cmd == "sdlprobe":
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdl_probe.py")).read()
        print(rig.sh(f"python3 - {float(a.secs)} <<'PYEOF'\n{src}\nPYEOF\n", timeout=a.secs + 30))
    elif a.cmd == "inspect":
        print(rig.sh(fmt(r"""
grep -nE "Background input|Keep Pads Connected|Pad handler sleep|Pad Mode|Show mouse|Lock overlay input" %(cfg)s/config.yml
for P in $(pgrep -f 'AppRun.wrapped|rpcs3-sa'); do
  echo "--- pid $P input fds:"; ls -l /proc/$P/fd 2>/dev/null | grep -E "/dev/(input|hidraw|uinput)" | sed 's/.*-> /  /'
done
echo "--- sway windows (focused marked *)"
env %(env)s SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock swaymsg -t get_tree 2>/dev/null | python3 -c '
import json,sys
def walk(n):
    if n.get("pid") or n.get("app_id") or (n.get("window_properties") or {}).get("class"):
        cls=(n.get("window_properties") or {}).get("class")
        print(("*" if n.get("focused") else " "), n.get("app_id") or cls, "|", (n.get("name") or "")[:60], "| pid", n.get("pid"), "| visible", n.get("visible"), "| fs", n.get("fullscreen_mode"))
    for c in n.get("nodes",[])+n.get("floating_nodes",[]): walk(c)
walk(json.load(sys.stdin))'
""")))
    elif a.cmd == "log":
        print(rig.sh(f"grep -aE {shlex.quote(a.pattern)} /storage/.cache/rpcs3/RPCS3.log "
                     f"| tail -n {int(a.n)} | cut -c1-300"))
    elif a.cmd == "doctor":
        print(rig.sh(fmt(DOCTOR)))
    elif a.cmd == "arm":
        print(rig.sh(fmt(ARM), check=True))
    elif a.cmd == "disarm":
        print(rig.sh(fmt(DISARM), check=True))
    elif a.cmd == "launch":
        if not a.serial.isalnum():
            sys.exit("serial must be like NPEA00050")
        print(rig.sh(fmt(LAUNCH, serial=shlex.quote(a.serial)), timeout=40))
    elif a.cmd == "frame":
        print(rig.frame(a.out))
    elif a.cmd == "pine":
        with rig.agent() as ag:
            print(ag.call("pine"))
    elif a.cmd == "layout":
        with rig.agent() as ag:
            r = ag.call("layout")
            print(f"pid={r['pid']} sudo={r['sudo'] and hex(r['sudo'])} committed={r['mb']} MB")
            for g0, g1 in r["ranges"]:
                print(f"  {g0:#010x}-{g1:#010x}  {(g1 - g0) / 2**20:8.1f} MB")
    elif a.cmd == "padtest":
        with rig.agent() as ag:
            print(ag.call("pad_open"))
            ag.call("program", steps=[[0, {"axes": {"lx": -0.6}}], [300, {"axes": {"lx": 0.6}}],
                                      [600, {"axes": {"lx": 0.0, "r2": 0.5}}], [900, {"axes": {"r2": 0}}]])
            print("wiggled: lx -0.6 -> +0.6 -> 0, r2 0.5 -> 0; released; pad closes on exit")


if __name__ == "__main__":
    main()
