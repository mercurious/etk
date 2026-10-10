#!/usr/bin/env python3
"""ETK autopilot — the RIG half (stdlib only; ROCKNIX ships no evdev/numpy).

Never installed: tools/autopilot/rig.py streams this file over ssh on every
session (length-prefixed on stdin), so the rig holds no hand-pushed copy and
the code that runs is always the repo's.

Modes (argv after the bootstrap):
  agent            JSON-lines RPC on stdin/stdout (pad, PINE, chords).
                   DEAD-MAN'S SWITCH: stdin EOF (ssh dropped, host died)
                   releases every input and destroys the virtual pad.
  snap <ranges>    dump committed guest RAM to stdout (zlib), for host-side
                   search. <ranges> = "all" or "0x10000-0x10000000,...".
  maps             print committed guest ranges (JSON) and exit.

The three channels this wires, and why each is the one it is:
  CONTROL  a uinput gamepad ("ETK Autopilot Pad"). RPCS3 Player 1 binds it
           through a launch-time `--input-config etk_autopilot` (nothing
           persists). InputPlumber 0.79's analog SendEvent panics
           ("Cannot block the current thread from within a runtime"), so the
           DualSense path carries only SendButtonChord taps (menus).
  RAM      RPCS3's PINE server ($XDG_RUNTIME_DIR/rpcs3.sock, ipc.yml) for
           live reads; /proc/<pid>/mem on vm::g_sudo_addr for bulk snapshots.
  VISION   grim, host-driven (rig.py frame) — not in this file.
"""
import fcntl
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib

# ---------------------------------------------------------------- uinput pad
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_SETUP, UI_ABS_SETUP = 0x405C5503, 0x401C5504  # sizeof 92 / 28
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BUS_USB = 0x03

# PS-semantic name -> evdev code. xpad/SDL convention: BTN_X(0x133) is the
# WEST face button, BTN_Y(0x134) the NORTH one (the kernel's BTN_NORTH alias
# is wrong for every Xbox-style pad, and SDL follows xpad).
BUTTONS = {
    "cross": 0x130, "circle": 0x131, "square": 0x133, "triangle": 0x134,
    "l1": 0x136, "r1": 0x137, "select": 0x13A, "start": 0x13B, "ps": 0x13C,
    "l3": 0x13D, "r3": 0x13E,
}
# axis name -> (code, min, max, neutral)
AXES = {
    "lx": (0x00, -32768, 32767, 0), "ly": (0x01, -32768, 32767, 0),
    "l2": (0x02, 0, 255, 0),
    "rx": (0x03, -32768, 32767, 0), "ry": (0x04, -32768, 32767, 0),
    "r2": (0x05, 0, 255, 0),
    "hatx": (0x10, -1, 1, 0), "haty": (0x11, -1, 1, 0),
}
DPAD = {"left": ("hatx", -1), "right": ("hatx", 1), "up": ("haty", -1), "down": ("haty", 1)}


class VPad:
    """A virtual gamepad that SDL auto-maps (standard BTN_*/ABS_* layout)."""

    def __init__(self, name="ETK Autopilot Pad", vendor=0x1209, product=0x5054):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for code in BUTTONS.values():
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        for code, lo, hi, _ in AXES.values():
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("H2x6i", code, 0, lo, hi, 0, 0, 0))
        fcntl.ioctl(self.fd, UI_DEV_SETUP,
                    struct.pack("4H80sI", BUS_USB, vendor, product, 1, name.encode()[:79], 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.name = name
        self.state = {k: 0 for k in BUTTONS}
        self.state.update({k: v[3] for k, v in AXES.items()})
        self.node = self._find_node()

    def _find_node(self):
        for _ in range(50):  # udev needs a moment to publish the node
            for d in os.listdir("/sys/class/input"):
                if not d.startswith("event"):
                    continue
                try:
                    with open(f"/sys/class/input/{d}/device/name") as f:
                        if f.read().strip() == self.name:
                            return "/dev/input/" + d
                except OSError:
                    pass
            time.sleep(0.02)
        return None

    @staticmethod
    def _scale(name, v):
        _, lo, hi, _ = AXES[name]
        if name in ("hatx", "haty"):
            return max(-1, min(1, int(round(v))))
        if lo == 0:  # trigger: 0..1
            return max(0, min(hi, int(round(float(v) * hi))))
        v = max(-1.0, min(1.0, float(v)))  # stick: -1..1
        return int(round(v * (hi if v >= 0 else -lo)))

    def set(self, btn=None, axes=None, dpad=None, raw=False):
        """Apply a partial state; only changed values hit the wire, one SYN."""
        want = {}
        for k, v in (btn or {}).items():
            if k not in BUTTONS:
                raise ValueError(f"unknown button {k}")
            want[k] = 1 if v else 0
        for k, v in (axes or {}).items():
            if k not in AXES:
                raise ValueError(f"unknown axis {k}")
            want[k] = int(v) if raw else self._scale(k, v)
        if dpad is not None:  # dpad: list of held directions (replaces the hat)
            want["hatx"] = want["haty"] = 0
            for d in dpad:
                ax, val = DPAD[d]
                want[ax] = val
        out = []
        for k, v in want.items():
            if self.state.get(k) == v:
                continue
            self.state[k] = v
            if k in BUTTONS:
                out.append(struct.pack("qqHHi", 0, 0, EV_KEY, BUTTONS[k], v))
            else:
                out.append(struct.pack("qqHHi", 0, 0, EV_ABS, AXES[k][0], v))
        if out:
            out.append(struct.pack("qqHHi", 0, 0, EV_SYN, 0, 0))
            os.write(self.fd, b"".join(out))
        return len(out)

    def release(self):
        self.set(btn={k: 0 for k in BUTTONS},
                 axes={k: v[3] for k, v in AXES.items()}, raw=True)

    def close(self):
        try:
            self.release()
            time.sleep(0.05)  # let the release land before the device vanishes
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


# ---------------------------------------------------------------- PINE (RAM)
PINE_READ = {"u8": (0, 1), "u16": (1, 2), "u32": (2, 4), "u64": (3, 8)}
PINE_WRITE = {"u8": (4, 1), "u16": (5, 2), "u32": (6, 4), "u64": (7, 8)}
KIND = {  # kind -> (pine width, struct fmt of the LE host value it arrives as)
    "u8": ("u8", "<B"), "s8": ("u8", "<b"), "u16": ("u16", "<H"), "s16": ("u16", "<h"),
    "u32": ("u32", "<I"), "s32": ("u32", "<i"), "f32": ("u32", "<f"),
    "u64": ("u64", "<Q"), "s64": ("u64", "<q"), "f64": ("u64", "<d"),
}
MSG_TITLE, MSG_ID, MSG_STATUS, MSG_VERSION = 0x0B, 0x0C, 0x0F, 0x08
STATUS = {0: "running", 1: "paused", 2: "shutdown"}


def pine_socket_candidates():
    seen = []
    for d in (os.environ.get("XDG_RUNTIME_DIR"), "/var/run/0-runtime-dir",
              "/run/0-runtime-dir", "/tmp"):
        if d and d not in seen:
            seen.append(d)
    return [d + "/rpcs3.sock" for d in seen]


class Pine:
    def __init__(self, path=None, timeout=2.0):
        self.path, self.sock, self.timeout = path, None, timeout

    def connect(self):
        if self.sock:
            return True
        for p in ([self.path] if self.path else pine_socket_candidates()):
            if not os.path.exists(p):
                continue
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            try:
                s.connect(p)
            except OSError:
                s.close()
                continue
            self.sock, self.path = s, p
            return True
        return False

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _recvn(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("PINE socket closed")
            buf += chunk
        return bytes(buf)

    def xfer(self, payload):
        if not self.connect():
            raise ConnectionError("no PINE socket (ipc.yml off, or RPCS3 not running)")
        try:
            self.sock.sendall(struct.pack("<I", len(payload) + 4) + payload)
            n = struct.unpack("<I", self._recvn(4))[0]
            body = self._recvn(n - 4)
        except (OSError, ConnectionError):
            self.close()
            raise
        if not body or body[0] != 0:
            raise ValueError("PINE command failed (an address in the batch is unmapped?)")
        return body[1:]

    def _string(self, op):
        b = self.xfer(bytes([op]))
        n = struct.unpack_from("<I", b, 0)[0]
        return b[4:4 + n].rstrip(b"\0").decode(errors="replace")

    def status(self):
        b = self.xfer(bytes([MSG_STATUS]))
        return STATUS.get(struct.unpack_from("<I", b, 0)[0], "?")

    def title_id(self):
        return self._string(MSG_ID)

    def title(self):
        return self._string(MSG_TITLE)

    def version(self):
        return self._string(MSG_VERSION)

    def read(self, items):
        """items: [(guest_addr, kind)] -> [value]. Batched; one failure fails all."""
        out, i = [], 0
        while i < len(items):
            chunk = items[i:i + 40000]  # reply <= 8 B/item, cap 450000
            req = bytearray()
            for addr, kind in chunk:
                op, _ = PINE_READ[KIND[kind][0]]
                req += struct.pack("<BI", op, addr)
            b = self.xfer(bytes(req))
            off = 0
            for _, kind in chunk:
                width, fmt = KIND[kind]
                w = PINE_READ[width][1]
                raw = b[off:off + w]
                off += w
                if fmt == "<f":
                    out.append(struct.unpack("<f", raw)[0])
                else:
                    out.append(struct.unpack(fmt, raw)[0])
            i += len(chunk)
        return out

    def write(self, addr, kind, value):
        width, fmt = KIND[kind]
        op, w = PINE_WRITE[width]
        self.xfer(struct.pack("<BI", op, addr) + struct.pack(fmt, value)[:w])


# --------------------------------------------------- bulk guest RAM (snapshot)
def rpcs3_pids():
    pids = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ")
        except OSError:
            continue
        if re.search(rb"rpcs3-sa|AppRun\.wrapped|rpcs3", cmd) and b"rig_agent" not in cmd:
            pids.append(int(p))
    return pids


def guest_layout():
    """-> (pid, sudo_base, [(guest_start, guest_end)]) from /proc/<pid>/maps.

    vm::g_sudo_addr = g_base_addr + 4 GiB is an always-RW mirror of guest RAM
    (memfd-backed, MAP_SHARED). Committed guest memory shows up there as
    `rw-s` memfd mappings; reserved-but-unmapped space is anonymous `---p`.
    The base is the start of a 4 GiB-aligned reservation, read from the RPCS3
    log line "vm::g_sudo_addr = 0x...", falling back to the maps scan.
    """
    sudo = None
    try:
        with open("/storage/.cache/rpcs3/RPCS3.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4 * 1024 * 1024))
            m = re.findall(rb"g_sudo_addr = (?:0x)?([0-9a-fA-F]+)", f.read())
            if m:
                sudo = int(m[-1], 16)
    except OSError:
        pass
    for pid in rpcs3_pids():
        try:
            with open(f"/proc/{pid}/maps") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        spans = []
        for ln in lines:
            parts = ln.split()
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            spans.append((lo, hi, parts[1], parts[5] if len(parts) > 5 else ""))
        if sudo is None or not any(lo <= sudo < hi or sudo <= lo < sudo + (1 << 32)
                                   for lo, hi, _, _ in spans):
            continue
        rng = []
        for lo, hi, perm, path in spans:
            if hi <= sudo or lo >= sudo + (1 << 32) or "r" not in perm or perm[3] != "s":
                continue
            g0, g1 = max(lo, sudo) - sudo, min(hi, sudo + (1 << 32)) - sudo
            if rng and rng[-1][1] == g0:
                rng[-1] = (rng[-1][0], g1)
            else:
                rng.append((g0, g1))
        if rng:
            return pid, sudo, rng
    return None, sudo, []


class MemReader:
    """Live guest-RAM reads via /proc/<pid>/mem on the vm::g_sudo_addr mirror.

    The primary RAM path: one pread per item, no socket, works whether or not
    PINE came up. (PINE can lose its socket file: Emulator::Init runs twice
    at a --no-gui boot, the second IPC server binds rpcs3.sock, then the
    first server's Cleanup() unlink()s that same path — the live server is
    left listening on a name nobody can connect to. Seen on car8 2026-10-10,
    core v0.9.1: "IPC: Starting server" twice 18 ms apart, no socket file.)
    Guest memory is big-endian; values decode with '>' formats.
    """

    def __init__(self):
        self.fd = None
        self.pid = self.sudo = None

    def open(self):
        if self.fd is not None:
            return True
        pid, sudo, _ = guest_layout()
        if not pid:
            return False
        self.fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        self.pid, self.sudo = pid, sudo
        return True

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self, items):
        if not self.open():
            raise ConnectionError("no RPCS3 guest memory (not running?)")
        out = []
        for addr, kind in items:
            fmt = ">" + KIND[kind][1][1]
            n = struct.calcsize(fmt)
            try:
                b = os.pread(self.fd, n, self.sudo + addr)
            except OSError:
                self.close()  # process gone, or the address is unmapped
                raise
            out.append(struct.unpack(fmt, b)[0] if len(b) == n else None)
        return out

    def block(self, addr, n):
        if not self.open():
            raise ConnectionError("no RPCS3 guest memory (not running?)")
        return os.pread(self.fd, n, self.sudo + addr)


def parse_ranges(spec, committed):
    if spec in ("", "all"):
        return committed
    want = []
    for part in spec.split(","):
        a, b = (int(x, 0) for x in part.split("-"))
        for c0, c1 in committed:  # clip to committed memory only
            lo, hi = max(a, c0), min(b, c1)
            if lo < hi:
                want.append((lo, hi))
    return want


def cmd_snap(spec):
    pid, sudo, committed = guest_layout()
    if not pid:
        sys.stderr.write("snap: no RPCS3 guest memory found\n")
        return 2
    ranges = parse_ranges(spec, committed)
    # Capture FIRST, ship second: streaming straight into zlib+ssh lets a slow
    # link back-pressure the reads, smearing one "snapshot" across a minute of
    # gameplay. Reading everything up front bounds the capture window to the
    # pread time (~1-2 s for ~450 MB), which is what makes diffs meaningful.
    fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    chunks = []
    t0 = time.time()
    try:
        for g0, g1 in ranges:
            off = g0
            while off < g1:
                n = min(1 << 22, g1 - off)
                try:
                    data = os.pread(fd, n, sudo + off)
                except OSError:
                    data = b""
                if len(data) < n:
                    data += b"\0" * (n - len(data))
                chunks.append(data)
                off += n
    finally:
        os.close(fd)
    t1 = time.time()
    out = sys.stdout.buffer
    hdr = json.dumps({"pid": pid, "sudo": sudo, "ranges": ranges, "t": t0,
                      "capture_s": round(t1 - t0, 3)}).encode()
    out.write(struct.pack("<I", len(hdr)) + hdr)
    z = zlib.compressobj(1)
    for data in chunks:
        out.write(z.compress(data))
    out.write(z.flush())
    out.flush()
    return 0


# ------------------------------------------------------------- JSON-RPC agent
def ip_chord(buttons, timeout=3):
    """InputPlumber SendButtonChord: press all, release in reverse (~80 ms).

    Rides the operator's own virtual DualSense, so it reaches ES and any
    RPCS3 launch without an input-config. Buttons only — no holds, no analog.
    """
    caps = []
    for b in buttons:
        caps.append(b if ":" in b else "Gamepad:Button:" + b)
    cmd = ["busctl", "call", "org.shadowblip.InputPlumber",
           "/org/shadowblip/InputPlumber/CompositeDevice0",
           "org.shadowblip.Input.CompositeDevice", "SendButtonChord",
           "as", str(len(caps))] + caps
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "busctl failed")


class Agent:
    def __init__(self):
        self.pad = None
        self.pine = Pine()
        self.mem = MemReader()

    def close(self):
        if self.pad:
            self.pad.close()
            self.pad = None
        self.pine.close()
        self.mem.close()

    def _reader(self, m):
        """`via`: "mem" (default; /proc/<pid>/mem) or "pine"."""
        return self.pine if m.get("via") == "pine" else self.mem

    # every handler returns a JSON-able dict
    def op_ping(self, m):
        return {"t": time.time()}

    def op_pad_open(self, m):
        if not self.pad:
            self.pad = VPad(m.get("name", "ETK Autopilot Pad"))
        return {"name": self.pad.name, "node": self.pad.node}

    def op_pad_close(self, m):
        if self.pad:
            self.pad.close()
            self.pad = None
        return {}

    def op_pad(self, m):
        if not self.pad:
            raise RuntimeError("pad not open")
        n = self.pad.set(m.get("btn"), m.get("axes"), m.get("dpad"))
        return {"events": n}

    def op_tap(self, m):
        """Press, hold `ms`, release — timed on the rig, not over WiFi."""
        if not self.pad:
            raise RuntimeError("pad not open")
        btn = {b: 1 for b in m.get("btn", [])}
        self.pad.set(btn=btn, dpad=m.get("dpad"))
        time.sleep(m.get("ms", 100) / 1000.0)
        self.pad.set(btn={b: 0 for b in btn}, dpad=[] if m.get("dpad") else None)
        return {}

    def op_program(self, m):
        """Run a timed input program rig-side: [[t_ms, {btn,axes,dpad}], ...]."""
        if not self.pad:
            raise RuntimeError("pad not open")
        t0 = time.monotonic()
        for t_ms, st in m["steps"]:
            dt = t0 + t_ms / 1000.0 - time.monotonic()
            if dt > 0:
                time.sleep(dt)
            self.pad.set(st.get("btn"), st.get("axes"), st.get("dpad"))
        if m.get("release", True):
            self.pad.release()
        return {"ms": round((time.monotonic() - t0) * 1000)}

    def op_release(self, m):
        if self.pad:
            self.pad.release()
        return {}

    def op_chord(self, m):
        ip_chord(m["buttons"])
        return {}

    def op_pine(self, m):
        ok = self.pine.connect()
        if not ok:
            return {"connected": False, "candidates": pine_socket_candidates()}
        return {"connected": True, "path": self.pine.path, "status": self.pine.status(),
                "id": self.pine.title_id(), "title": self.pine.title(),
                "version": self.pine.version()}

    def op_read(self, m):
        items = [(int(a, 0) if isinstance(a, str) else a, k) for a, k in m["items"]]
        return {"v": self._reader(m).read(items), "t": time.time()}

    def op_block(self, m):
        """Raw guest bytes (hex) — for eyeballing a structure around a hit."""
        a = int(m["addr"], 0) if isinstance(m["addr"], str) else m["addr"]
        return {"hex": self.mem.block(a, int(m.get("n", 256))).hex()}

    def op_watch(self, m):
        """Sample `items` at `hz` for `secs` rig-side; return the series."""
        items = [(int(a, 0) if isinstance(a, str) else a, k) for a, k in m["items"]]
        hz, secs = float(m.get("hz", 30)), float(m.get("secs", 2))
        rd = self._reader(m)
        rows, t0 = [], time.monotonic()
        while time.monotonic() - t0 < secs:
            t = time.monotonic()
            rows.append([round(t - t0, 4)] + rd.read(items))
            dt = 1.0 / hz - (time.monotonic() - t)
            if dt > 0:
                time.sleep(dt)
        return {"rows": rows}

    def op_write(self, m):
        a = int(m["addr"], 0) if isinstance(m["addr"], str) else m["addr"]
        self.pine.write(a, m["kind"], m["value"])
        return {}

    def op_layout(self, m):
        pid, sudo, rng = guest_layout()
        return {"pid": pid, "sudo": sudo, "ranges": rng,
                "mb": round(sum(b - a for a, b in rng) / 2**20, 1)}

    def run(self):
        out = sys.stdout
        out.write(json.dumps({"ready": True, "pid": os.getpid()}) + "\n")
        out.flush()
        for line in sys.stdin:  # EOF = the dead-man's switch
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                fn = getattr(self, "op_" + m.get("op", ""), None)
                if not fn:
                    raise ValueError(f"unknown op {m.get('op')}")
                res = {"id": m.get("id"), "ok": True}
                res.update(fn(m))
            except Exception as e:  # report, never die mid-session
                res = {"id": m.get("id") if isinstance(m, dict) else None,
                       "ok": False, "err": f"{type(e).__name__}: {e}"}
            out.write(json.dumps(res) + "\n")
            out.flush()


def main(argv):
    mode = argv[0] if argv else "agent"
    if mode == "snap":
        return cmd_snap(argv[1] if len(argv) > 1 else "all")
    if mode == "maps":
        pid, sudo, rng = guest_layout()
        print(json.dumps({"pid": pid, "sudo": sudo, "ranges": rng}))
        return 0
    agent = Agent()
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
        signal.signal(sig, lambda *_: (agent.close(), os._exit(0)))
    try:
        agent.run()
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
