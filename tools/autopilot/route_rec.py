#!/usr/bin/env python3
"""route_rec — RECORD a human driving a route on the car, replay it as a macro.

  route_rec.py OUT [--secs 300] [--rig root@SM8250.local] [--addr usb] [--nodes event5,event9]

The operator drives the car by hand (watching ./onboard on a headless car). This tails the
pad's evdev node(s) ON THE CAR over ssh (a small reader shipped on stdin: `llHHi` frames ->
text lines), and watches the Pitlink frame stream as an observer, so every press is stamped
with the car's flip and gets a frame saved beside it. It never sends a pad; it never needs
the wheel (if its HELLO lands it, it holds it idle -- start `pitlink.py serve` first to keep
./onboard as the observer instead).

OUT/route.tsv    t_s  flip  kind  name  value     every button edge, hat and stick move
OUT/snaps/NNN_name.png                            the frame at each button-down
OUT/route.macro                                   gt5_trial / ssx_drive replay: sleep · press · hold · snap

Why (operator 2026-10-26): blind press-frame-look loops over a menu cost a round trip per
guess and never reach certainty; a human drives the menu in seconds and the recording IS
the route. Macro `press` ms is the measured hold clamped to 80..400 (a longer d-pad hold
auto-repeats at the game's frame rate: the GT5 macro note).
"""
import argparse
import os
import select
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))

EV_KEY, EV_ABS = 1, 3
KEYS = {304: "cross", 305: "circle", 307: "triangle", 308: "square", 310: "l1", 311: "r1",
        312: "l2", 313: "r2", 314: "select", 315: "start", 316: "ps", 317: "l3", 318: "r3"}
HAT = {16: ("left", "right"), 17: ("up", "down")}       # ABS_HAT0X / ABS_HAT0Y: -1 / +1
STICKS = {0: "lx", 1: "ly", 3: "rx", 4: "ry"}           # ABS_X ABS_Y ABS_RX ABS_RY
TRIGGERS = {2: "l2", 5: "r2"}                           # ABS_Z ABS_RZ (analog; the KEY edge wins)
DEADZONE = 0.35
PRESS_MIN_MS, PRESS_MAX_MS = 80, 400

# Runs ON THE CAR (python3 is there: the Pitlink USB daemon is python). Prints one text line per
# evdev frame: "<t> <node> <type> <code> <value>", plus one "abs <node> <code> <min> <max>" per
# axis at start (EVIOCGABS), so the host normalizes sticks without guessing the range.
CAR_READER = r'''
import os, select, struct, sys, time, fcntl, array
nodes = sys.argv[1:]
fds = {}
for n in nodes:
    try:
        fds[os.open("/dev/input/" + n, os.O_RDONLY | os.O_NONBLOCK)] = n
    except OSError as e:
        print("err %s %s" % (n, e), flush=True)
for fd, n in fds.items():
    for code in (0, 1, 2, 3, 4, 5, 16, 17):
        buf = array.array("i", [0] * 6)
        try:
            fcntl.ioctl(fd, 0x80184540 + code, buf, True)
            print("abs %s %d %d %d" % (n, code, buf[1], buf[2]), flush=True)
        except OSError:
            pass
print("ready %s" % " ".join(fds.values()), flush=True)
SZ = struct.calcsize("llHHi")
while fds:
    r, _, _ = select.select(list(fds), [], [], 1.0)
    for fd in r:
        try:
            data = os.read(fd, SZ * 64)
        except OSError:
            data = b""
        if not data:
            print("gone %s" % fds.pop(fd), flush=True); continue
        for i in range(0, len(data) - SZ + 1, SZ):
            s, us, t, c, v = struct.unpack("llHHi", data[i:i + SZ])
            if t in (1, 3):
                print("%d.%06d %s %d %d %d" % (s, us, fds[fd], t, c, v), flush=True)
'''


class Route:
    """Pad edges -> steps. Pure (no I/O): fed (t, type, code, value) and a flip per event."""

    def __init__(self):
        self.absinfo = {}      # code -> (min, max)
        self.rows = []         # (t, flip, kind, name, value)
        self.steps = []        # ("press", name, ms) | ("hold", {axis: v}, secs) each with its t0
        self._down = {}        # name -> (t0, flip0)
        self._stick = {}       # axis -> (t0, peak)
        self._hat = {16: 0, 17: 0}
        self.snaps = []        # (index, name, flip) in press order
        self.on_down = None    # callback(name, flip, index) -> the host saves a frame

    def set_abs(self, code, lo, hi):
        self.absinfo[code] = (lo, hi)

    def _norm(self, code, v):
        lo, hi = self.absinfo.get(code, (-32768, 32767))
        if lo >= 0:  # unsigned axis (0..255): centre at the middle
            return (2.0 * (v - lo) / max(1, hi - lo)) - 1.0
        return max(-1.0, min(1.0, v / float(hi if v >= 0 else -lo)))

    def feed(self, t, typ, code, val, flip):
        if typ == EV_KEY and code in KEYS:
            self._edge(t, flip, KEYS[code], val)
        elif typ == EV_ABS and code in HAT:
            neg, pos = HAT[code]
            prev = self._hat[code]
            if prev:
                self._edge(t, flip, neg if prev < 0 else pos, 0)
            if val:
                self._edge(t, flip, neg if val < 0 else pos, 1)
            self._hat[code] = val
        elif typ == EV_ABS and code in STICKS:
            axis, x = STICKS[code], self._norm(code, val)
            self.rows.append((t, flip, "stick", axis, round(x, 3)))
            cur = self._stick.get(axis)
            if abs(x) >= DEADZONE:
                if cur is None:
                    self._stick[axis] = (t, x)
                elif abs(x) > abs(cur[1]):
                    self._stick[axis] = (cur[0], x)
            elif cur is not None:
                t0, peak = self._stick.pop(axis)
                self.steps.append((t0, "hold", {axis: round(peak, 2)}, max(0.1, round(t - t0, 2))))

    def _edge(self, t, flip, name, val):
        self.rows.append((t, flip, "key", name, val))
        if val:
            if name in self._down:
                return
            self._down[name] = (t, flip)
            idx = len(self.snaps)
            self.snaps.append((idx, name, flip))
            if self.on_down:
                self.on_down(name, flip, idx)
        else:
            t0, _ = self._down.pop(name, (t, flip))
            ms = int(round((t - t0) * 1000))
            self.steps.append((t0, "press", name, max(PRESS_MIN_MS, min(PRESS_MAX_MS, ms)), len(self.snaps) - 1))

    def macro(self, header=""):
        """Steps in time order with the gaps as `sleep`; a `snap` after every press."""
        out = [f"# {header}".rstrip()] if header else []
        steps = sorted(self.steps, key=lambda s: s[0])
        # chords: presses whose downs are within 60 ms become one press a+b
        merged, i = [], 0
        while i < len(steps):
            s = steps[i]
            if s[1] == "press":
                names, ms, snap = [s[2]], s[3], s[4]
                while i + 1 < len(steps) and steps[i + 1][1] == "press" and steps[i + 1][0] - s[0] < 0.06:
                    i += 1
                    names.append(steps[i][2]); ms = max(ms, steps[i][3])
                merged.append((s[0], "press", "+".join(names), ms, snap))
            else:
                merged.append(s)
            i += 1
        t_prev = None
        for s in merged:
            if t_prev is not None:
                gap = s[0] - t_prev
                if gap >= 0.3:
                    out.append(f"sleep {round(gap * 2) / 2:g}")
            if s[1] == "press":
                out.append(f"press {s[2]} {s[3]}")
                idx = s[4]
                if 0 <= idx < len(self.snaps):
                    out.append(f"snap {idx:03d}_{self.snaps[idx][1]}")
                t_prev = s[0] + s[3] / 1000.0
            else:
                kv = " ".join(f"{k}={v:g}" for k, v in s[2].items())
                out.append(f"hold {kv} secs={s[3]:g}")
                t_prev = s[0] + s[3]
        return "\n".join(out) + "\n"


def parse_line(line):
    """A car-reader line -> ("abs", node, code, lo, hi) | ("ev", t, node, type, code, value) | (tag, text)."""
    p = line.split()
    if not p:
        return None
    if p[0] == "abs" and len(p) == 5:
        return ("abs", p[1], int(p[2]), int(p[3]), int(p[4]))
    if p[0] in ("ready", "gone", "err"):
        return (p[0], " ".join(p[1:]))
    if len(p) == 5:
        return ("ev", float(p[0]), p[1], int(p[2]), int(p[3]), int(p[4]))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--secs", type=float, default=300.0)
    ap.add_argument("--rig", default=os.environ.get("ETK_RIG", "root@SM8250.local"))
    ap.add_argument("--addr", default=None, help="Pitlink address (default $PITLINK_ADDR else usb)")
    ap.add_argument("--nodes", default="", help="evdev nodes on the car (default: every pad-named node)")
    ap.add_argument("--no-frames", action="store_true", help="no Pitlink: flips 0, no snaps")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "snaps"), exist_ok=True)

    rig = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", a.rig]
    nodes = [n.strip() for n in a.nodes.split(",") if n.strip()]
    if not nodes:
        names = subprocess.run(rig + ["for d in /sys/class/input/event*; do n=${d##*/}; [ -e /dev/input/$n ] && echo $n $(cat $d/device/name); done"],
                               capture_output=True, text=True, timeout=20).stdout
        for ln in names.splitlines():
            node, _, name = ln.partition(" ")
            nl = name.lower()
            if any(h in nl for h in ("gamepad", "controller", "joystick")) and \
               not any(x in nl for x in ("motion", "touchpad", "headset", "keyboard", "mouse")):
                nodes.append(node)
        if not nodes:
            sys.exit(f"route_rec: no pad-named evdev node on {a.rig}:\n{names}")
    print(f"route_rec: pad nodes on the car: {', '.join(nodes)}", flush=True)

    c = None
    if not a.no_frames:
        from client import PitlinkClient, PitlinkError, default_addr  # noqa: E402
        try:
            c = PitlinkClient(a.addr or default_addr(), timeout=5.0)
            info = c.hello(os.environ.get("PITLINK_TOKEN", ""))
            print(f"route_rec: pitlink {'controller (idle)' if c.controller else 'observer'} on "
                  f"{info.get('title_id')} {info.get('title')}", flush=True)
        except (OSError, PitlinkError) as e:
            print(f"route_rec: no pitlink ({e}): flips 0, no snaps", flush=True)
            c = None

    route = Route()
    header = f"recorded {time.strftime('%Y-%m-%d %H:%M')} on {a.rig} nodes {','.join(nodes)}"
    if c is not None:
        header += f" title {c.info.get('title_id')}"

    def flip_now():
        f = c.latest_frame() if c is not None else None
        return f.flip if f is not None else 0

    def on_down(name, flip, idx):
        f = c.latest_frame() if c is not None else None
        path = os.path.join(a.out, "snaps", f"{idx:03d}_{name}.png")
        if f is not None:
            try:
                f.png(path, 640)
            except Exception as e:  # a snap must never stall the recording
                print(f"route_rec: snap {idx} failed: {e}", flush=True)
        print(f"  {idx:03d} {name:8s} flip {flip}", flush=True)
    route.on_down = on_down

    proc = subprocess.Popen(rig + ["python3", "-", *nodes], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    proc.stdin.write(CAR_READER)
    proc.stdin.close()
    t_end = time.monotonic() + a.secs
    print(f"route_rec: recording for {a.secs:g} s -> {a.out}  (drive now; Ctrl-C ends early)", flush=True)
    try:
        while time.monotonic() < t_end and proc.poll() is None:
            r, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not r:
                continue
            line = proc.stdout.readline()
            ev = parse_line(line)
            if ev is None:
                continue
            if ev[0] == "abs":
                route.set_abs(ev[2], ev[3], ev[4])
            elif ev[0] == "ev":
                route.feed(ev[1], ev[3], ev[4], ev[5], flip_now())
            else:
                print(f"route_rec: car: {ev[0]} {ev[1]}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        if c is not None:
            c.close()
    with open(os.path.join(a.out, "route.tsv"), "w") as f:
        f.write("t_s\tflip\tkind\tname\tvalue\n")
        for r in route.rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    text = route.macro(header)
    with open(os.path.join(a.out, "route.macro"), "w") as f:
        f.write(text)
    print(f"route_rec: {len(route.rows)} events, {len(route.snaps)} presses -> {a.out}/route.macro:\n{text}")


if __name__ == "__main__":
    main()
