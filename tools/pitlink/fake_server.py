#!/usr/bin/env python3
"""fake_server — a pure-Python PLNK v1 SERVER: the car, minus the car.

The fake_ra.py pattern from the autogamer lab: the host side (client, CLI, MCP
server, perception, driver) is developed and regression-tested against this,
offline, with no mint and no rig. It speaks §3 of docs/PITLINK_SPEC.md exactly
as the C++ GTK Pitlink server must — where §3 is silent, the choice made here is
marked `CHOICE:` and is the one the C++ side should be checked against.

  fake_server.py                          # @etk-pitlink + 127.0.0.1:47500, 60 fps
  fake_server.py --listen 127.0.0.1:0 --fps 30 --token s3cret -v

The synthetic car:
  - a flip counter at --fps (wall-clock paced; PAUSE/RESUME/STEP stop and start it)
  - BGRA frames: a gradient, red tint = flip, and an 8x8 white block whose cell
    index (row-major) is flip mod cells — `block_index()` decodes it back
  - guest RAM = one bytearray (--ram BASE:SIZE, default 0x10000:1 MiB) with a
    live mirror written AT EVERY FLIP, before WATCH is sampled:
        +0x00 u32  flip                       +0x08 u8x4  lx ly rx ry (port 0)
        +0x04 u16  port-0 buttons in effect   +0x0C f32   "speed" ramp 0..300
        +0x06 u8   port-0 mode                +0x10 u8x12 port-0 effective pressure
    (all big-endian, as the PS3 stores it); +0x20.. is free scratch for RAM_WRITE.
  - every PAD received is logged (`pads`), every flip's port state too (`history`)
"""
import argparse
import collections
import socket
import struct
import sys
import threading
import time

import numpy as np

try:
    from . import plnk as P
    from .client import parse_addr, recv_msg
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import plnk as P
    from client import parse_addr, recv_msg

BLOCK = 8
MIRROR = 0x20  # bytes of RAM the car rewrites every flip
PORTS = 7  # CELL_PAD_MAX_PORT_NUM
OBSERVER_OK = (P.PING, P.STATUS, P.RAM_READ)  # §3 Roles: frames + these, nothing else
_CLOSE = object()


def block_index(bgra):
    """Decode the synthetic frame's flip cell (flip mod cells) from its white block."""
    ys, xs = np.nonzero((bgra[..., :3] == 255).all(axis=2))
    if not len(ys):
        return None
    cols = bgra.shape[1] // BLOCK
    return (ys.min() // BLOCK) * cols + xs.min() // BLOCK


def cells(w, h):
    return max(1, (w // BLOCK) * (h // BLOCK))


class Conn:
    """One client link. Replies/events are queued in order and never dropped; a FRAME
    replaces any unsent FRAME (latest wins — a slow client never backs up the flip)."""

    def __init__(self, sock, peer, log):
        self.sock, self.peer, self.log = sock, peer, log
        self.hello, self.role, self.alive = False, None, True
        self.q, self.cv = collections.deque(), threading.Condition()
        self.frames_dropped = 0
        threading.Thread(target=self._writer, daemon=True).start()

    def send(self, t, payload=b"", seq=0):
        with self.cv:
            self.q.append(("m", P.encode(t, payload, seq)))
            self.cv.notify()

    def send_frame(self, msg):
        with self.cv:
            n = len(self.q)
            self.q = collections.deque(i for i in self.q if i is _CLOSE or i[0] != "f")
            self.frames_dropped += n - len(self.q)
            self.q.append(("f", msg))
            self.cv.notify()

    def ack(self, for_type, seq, status=0, msg=""):
        self.send(P.ACK, P.pack_ack(for_type, status, msg), seq)

    def close(self, flush=True):
        self.alive = False
        with self.cv:
            if not flush:
                self.q.clear()
            self.q.append(_CLOSE)
            self.cv.notify()

    def _writer(self):
        try:
            while True:
                with self.cv:
                    self.cv.wait_for(lambda: self.q)
                    item = self.q.popleft()
                if item is _CLOSE:
                    break
                self.sock.sendall(item[1])
        except OSError:
            pass
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class FakeCar:
    def __init__(self, listen=("@etk-pitlink", "127.0.0.1:47500"), fps=60.0, native=(1280, 720),
                 video=(640, 360, 30, P.CODEC_ZSTD if P.ZSTD else P.CODEC_RAW), token="", ram=(0x10000, 1 << 20), ttl_ms=500,
                 pad_mode=P.MODE_MERGE, title_id="NPEA00050", title="FAKE CAR (pitlink fake_server)",
                 version="fake_server PLNK v1", verbose=False):
        self.listen, self.fps, self.native, self.token = list(listen), float(fps), native, token
        self.ttl_ms, self.pad_mode, self.verbose = ttl_ms, pad_mode, verbose
        self.title_id, self.title, self.version = title_id, title, version
        w, h, hz, codec = video  # the car streams zstd until VIDEO says otherwise (GTK Pitlink 0.10.0)
        self.video = {"w": w, "h": h, "hz": hz, "codec": codec, "zstd_level": 1, "overlays": 0}
        self.ram_base, self.ram = ram[0], bytearray(ram[1])
        self.lock = threading.RLock()
        self.flip, self.vblank, self.fps_ema = 0, 0, self.fps
        self.paused, self.step_left, self.snap_codec = False, 0, None
        self.watch = []
        self.agent = {p: P.PadState(port=p) for p in range(PORTS)}
        self.pending = []  # (at_flip, arrival#, PadState)
        self.deadline = {}  # port -> (monotonic deadline, ttl_ms)
        self.pads, self.history, self.events_sent = [], collections.deque(maxlen=100000), []
        self.conns, self.controller = [], None
        self.addrs, self._socks = [], []
        self.exited, self.exit_savestate = threading.Event(), None
        self._arrivals, self._since_frame = 0, 0
        self._grad, self._last_frame = {}, None
        self._stop = threading.Event()

    # ---- lifecycle ----------------------------------------------------------------------
    def start(self):
        for a in self.listen:
            fam, sa = parse_addr(a)
            s = socket.socket(fam, socket.SOCK_STREAM)
            if fam != socket.AF_UNIX:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(sa)
            s.listen(8)
            if fam == socket.AF_UNIX:
                self.addrs.append(a)
            else:
                host, port = s.getsockname()[:2]
                self.addrs.append(f"[{host}]:{port}" if ":" in host else f"{host}:{port}")
            self._socks.append(s)
            threading.Thread(target=self._accept, args=(s, fam), daemon=True).start()
        self._last_flip_t = None
        threading.Thread(target=self._run, name="fake-flip", daemon=True).start()
        self._log(f"listening on {' '.join(self.addrs)} · {self.fps:g} fps · RAM {self.ram_base:#x}+{len(self.ram):#x}"
                  + (" · token set" if self.token else ""))
        return self

    def stop(self):
        self._stop.set()
        for s in self._socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        with self.lock:
            for c in list(self.conns):
                c.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def _log(self, msg, force=False):
        if self.verbose or force:
            print(f"[fake {self.flip:>7}] {msg}", file=sys.stderr, flush=True)

    # ---- links ------------------------------------------------------------------------
    def _accept(self, s, fam):
        while not self._stop.is_set():
            try:
                cs, peer = s.accept()
            except OSError:
                return
            if fam != socket.AF_UNIX:
                cs.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c = Conn(cs, peer or "unix", self._log)
            with self.lock:
                self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            while c.alive:
                t, seq, p = recv_msg(c.sock)
                try:
                    self._handle(c, t, seq, p)
                except P.ProtocolError as e:
                    c.ack(t, seq, -1, f"malformed: {e}")
        except (EOFError, OSError, P.ProtocolError):
            pass
        finally:
            c.close()
            with self.lock:
                if c in self.conns:
                    self.conns.remove(c)
                if self.controller is c:
                    # CHOICE: a dropped controller frees the wheel; its pad state is NOT
                    # released here — the TTL dead-man is the one release path for a lost link.
                    self.controller = None
                    self._log("controller left; the wheel is free")

    def broadcast(self, t, payload):
        with self.lock:
            for c in self.conns:
                if c.hello and c.alive:
                    c.send(t, payload)

    def event(self, kind, text=""):
        self.events_sent.append((self.flip, kind, text))
        self._log(f"EVENT {kind} {P.EVENT_NAMES.get(kind)}: {text}")
        self.broadcast(P.EVENT, P.pack_event(kind, text))

    # ---- the protocol -------------------------------------------------------------------
    def _handle(self, c, t, seq, p):
        self._log(f"<- {P.NAMES.get(t, hex(t))} seq={seq} len={len(p)} from {c.peer}")
        if not c.hello and self.token and t != P.HELLO:
            return c.close(flush=False)  # §3: with a token, a client that does not HELLO first is closed
        if t == P.HELLO:
            h = P.unpack_hello(p)
            if self.token and h["token"] != self.token:
                self._log(f"bad token from {c.peer}: closed")
                return c.close(flush=False)
            if h["proto"] != P.PROTO:
                return c.ack(t, seq, -1, f"proto {h['proto']} unsupported (server speaks {P.PROTO})")
            with self.lock:
                # CHOICE: the role is decided at each HELLO — the first HELLO while the wheel is
                # free gets it; an observer may re-HELLO to take a freed wheel.
                if self.controller is None or self.controller is c:
                    self.controller, c.role = c, P.ROLE_CONTROLLER
                else:
                    c.role = P.ROLE_OBSERVER
                c.hello = True
                caps = P.CAP_VIDEO | P.CAP_PAD | P.CAP_RAM | P.CAP_CLOCK
                c.send(P.HELLO_OK, P.pack_hello_ok(P.PROTO, caps, c.role, self.version, self.title_id, self.title), seq)
                if self._last_frame:
                    # CHOICE: a fresh HELLO gets the most recent FRAME at once (its own old flip id),
                    # so a client that joins a PAUSED car still has a picture.
                    c.send_frame(self._last_frame)
            return
        if t == P.PING:
            return c.send(P.PONG, P.pack_pong(self.flip, time.monotonic_ns()), seq)
        if t == P.STATUS:
            st = P.ST_PAUSED if self.paused else P.ST_RUNNING
            return c.send(P.STATUS_REPLY, P.pack_status_reply(st, self.flip, self.vblank, self.title_id, self.title), seq)
        if t == P.RAM_READ:
            items = P.unpack_ram_read(p)
            if len(items) > P.RAM_READ_MAX_ITEMS:
                return c.ack(t, seq, -1, f"n {len(items)} > {P.RAM_READ_MAX_ITEMS}")
            with self.lock:
                out = [self.read(a, s) if s <= P.RAM_READ_MAX_SIZE else None for a, s in items]
            return c.send(P.RAM_DATA, P.pack_ram_data(out), seq)
        if t not in P.NAMES or t & 0x8000:
            return c.ack(t, seq, -1, "unknown type")
        if c.role != P.ROLE_CONTROLLER:
            # CHOICE: pre-HELLO (no token) = observer rights; fire-and-forget PAD/PAD_RELEASE
            # from an observer is still ACKed -1, echoing the PAD's seq.
            return c.ack(t, seq, -1, "observer: read-only" if c.hello else "HELLO first")
        with self.lock:
            return self._control(c, t, seq, p)

    def _control(self, c, t, seq, p):
        if t == P.PAD:
            st = P.PadState.unpack(p)
            if st.port >= PORTS:  # CHOICE: a bad PAD gets EVENT 6 (error text) to the sender only
                return c.send(P.EVENT, P.pack_event(P.EV_ERROR, f"PAD port {st.port} out of range"))
            now = time.monotonic()
            ttl = st.ttl_ms or self.ttl_ms
            self.pads.append({"t": now, "flip": self.flip, "seq": seq, "state": st, "raw": bytes(p)})
            # CHOICE: the newest PAD's ttl governs, measured from RECEIPT (not from at_flip).
            self.deadline[st.port] = (now + ttl / 1000.0, ttl)
            if st.at_flip == 0 or st.at_flip <= self.flip:  # CHOICE: an at_flip already reached = now
                self._apply(st)
            else:
                # CHOICE: a newer "now" PAD does not cancel PADs scheduled for later flips;
                # PAD_RELEASE and the dead-man do.
                self._arrivals += 1
                self.pending.append((st.at_flip, self._arrivals, st))
                self.pending.sort(key=lambda x: x[:2])
            return
        if t == P.PAD_RELEASE:
            port = P.unpack_pad_release(p)
            if port < PORTS:
                self._neutral(port)
                self.deadline.pop(port, None)
            return
        if t == P.VIDEO:
            v = P.unpack_video(p)
            if v["hz"] and v["codec"] not in P.CODECS:
                return c.ack(t, seq, -1, f"codec {v['codec']} unsupported")
            if v["hz"] and (not v["w"] or not v["h"]):  # CHOICE: w or h 0 = native size
                v["w"], v["h"] = self.native
            if v["hz"] and (v["w"] < BLOCK or v["h"] < BLOCK):
                return c.ack(t, seq, -1, "frame too small")
            self.video, self._since_frame = v, 0
            return c.ack(t, seq)
        if t == P.SNAP:
            codec = P.unpack_snap(p)
            if codec not in P.CODECS:
                return c.ack(t, seq, -1, f"codec {codec} unsupported")
            self.snap_codec = codec
            return c.ack(t, seq)
        if t == P.RAM_WRITE:
            addr, data = P.unpack_ram_write(p)
            o = addr - self.ram_base
            if o < 0 or o + len(data) > len(self.ram):
                return c.ack(t, seq, -1, f"unmapped {addr:#x}+{len(data)}")
            self.ram[o:o + len(data)] = data
            return c.ack(t, seq)
        if t == P.WATCH:
            items = P.unpack_watch(p)
            if len(items) > P.WATCH_MAX:
                return c.ack(t, seq, -1, f"n {len(items)} > {P.WATCH_MAX}")
            if any(s not in P.WATCH_SIZES for _, s in items):
                return c.ack(t, seq, -1, "WATCH size must be 1/2/4/8")
            self.watch = items
            return c.ack(t, seq)
        if t == P.PAUSE:
            was, self.paused, self.step_left = self.paused, True, 0
            c.ack(t, seq)
            if not was:
                self.event(P.EV_STATE, "paused")
            return
        if t == P.RESUME:
            was, self.paused, self.step_left = self.paused, False, 0
            c.ack(t, seq)
            if was:
                self.event(P.EV_STATE, "running")
            return
        if t == P.STEP:
            n = P.unpack_step(p)
            if n == 0:  # CHOICE: STEP 0 is refused (nothing to count)
                return c.ack(t, seq, -1, "STEP needs flips >= 1")
            self.paused, self.step_left = False, n
            return c.ack(t, seq)
        if t == P.EXIT:
            self.exit_savestate = P.unpack_exit(p)
            c.ack(t, seq)
            self.event(P.EV_STATE, f"exit savestate={int(self.exit_savestate)}")
            self.exited.set()
            threading.Thread(target=self.stop, daemon=True).start()
            return
        return c.ack(t, seq, -1, "unknown type")

    # ---- the car ------------------------------------------------------------------------
    def read(self, addr, size):
        o = addr - self.ram_base
        if o < 0 or o + size > len(self.ram):
            return None
        return bytes(self.ram[o:o + size])

    def hist(self):
        """Snapshot of (flip, port-0 buttons) per flip — safe while the car runs."""
        with self.lock:
            return list(self.history)

    def _apply(self, st):
        st = st.copy(mode=self.pad_mode if st.mode == P.MODE_DEFAULT else st.mode)
        self.agent[st.port] = st
        self._log(f"PAD applied: {st}")

    def _neutral(self, port):
        changed = not self.agent[port].is_neutral() or any(s.port == port for _, _, s in self.pending)
        self.agent[port] = P.PadState(port=port)
        self.pending = [x for x in self.pending if x[2].port != port]
        return changed

    def _deadman(self, now):
        for port, (dl, ttl) in list(self.deadline.items()):
            if now >= dl:
                del self.deadline[port]
                # CHOICE: EVENT 4 only when the lapse actually changed something
                # (a non-neutral state or a scheduled PAD); a lapsed neutral pad is silent.
                if self._neutral(port):
                    self.event(P.EV_DEADMAN, f"port {port}: no PAD for {ttl} ms -> neutral")

    def _run(self):
        nxt = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            with self.lock:
                self._deadman(now)
                paused = self.paused
                if paused:
                    self._last_flip_t = None  # the EMA measures flips, not pauses
            if paused:
                nxt = now
                time.sleep(0.002)
                continue
            if now < nxt:
                time.sleep(min(nxt - now, 0.004))
                continue
            nxt += 1.0 / self.fps
            if now - nxt > 0.25:
                nxt = now
            self._flip(now)

    def _flip(self, now):
        with self.lock:
            self.flip += 1
            self.vblank += max(1, round(60.0 / self.fps))
            dt = now - (self._last_flip_t or now)
            self._last_flip_t = now
            if dt > 0:
                self.fps_ema += 0.1 * (1.0 / dt - self.fps_ema)
            host_ns = time.monotonic_ns()
            while self.pending and self.pending[0][0] <= self.flip:
                self._apply(self.pending.pop(0)[2])
            a = self.agent[0]
            struct.pack_into(">IHBx4Bf", self.ram, 0, self.flip, a.bits, a.mode,
                             *(P.stick_byte(getattr(a, s)) for s in P.STICKS), float(self.flip % 3000) / 10.0)
            self.ram[0x10:0x1C] = bytes(a.pressure_of(n) for n in P.PRESSURE)
            self.history.append((self.flip, a.bits))
            watch = b"".join(self.read(ad, s) or bytes(s) for ad, s in self.watch)
            stepping_done = False
            if self.step_left:
                self.step_left -= 1
                if not self.step_left:
                    self.paused, stepping_done = True, True
            v, shot, flags = self.video, False, 0
            w = h = codec = None
            if self.snap_codec is not None:
                (w, h), codec, flags = self.native, self.snap_codec, P.FRAME_SNAP
                self.snap_codec, shot = None, True
            elif v["hz"]:
                self._since_frame += 1
                # CHOICE: the flip that completes a STEP is always tapped (whatever hz says):
                # a lockstep agent must get the picture of the flip it stopped on.
                if self._since_frame >= max(1, round(self.fps / v["hz"])) or stepping_done:
                    self._since_frame, shot = 0, True
                w, h, codec = v["w"], v["h"], v["codec"]
            if v["overlays"]:
                flags |= P.FRAME_OVERLAYS
            flip, vblank, fps, level = self.flip, self.vblank, self.fps_ema, v["zstd_level"]
        if shot:
            img = P.encode_image(self.render(w, h, flip), codec, level)
            msg = P.encode(P.FRAME, P.pack_frame(flip, vblank, host_ns, w, h, codec, flags, fps, watch, img))
            with self.lock:
                self._last_frame = msg
                for c in self.conns:
                    if c.hello and c.alive:
                        c.send_frame(msg)
        if stepping_done:  # after the stepped flip's FRAME, so the waiter already holds it
            with self.lock:
                self.event(P.EV_STEP, f"flip={flip}")

    def render(self, w, h, flip):
        g = self._grad.get((w, h))
        if g is None:
            g = np.empty((h, w, 4), np.uint8)
            g[..., 0] = (np.arange(w) * 200 // max(1, w - 1))[None, :]
            g[..., 1] = (np.arange(h) * 200 // max(1, h - 1))[:, None]
            g[..., 3] = 255
            self._grad[(w, h)] = g
        img = g.copy()
        img[..., 2] = (flip * 3) % 200
        cols, k = w // BLOCK, flip % cells(w, h)
        y, x = (k // cols) * BLOCK, (k % cols) * BLOCK
        img[y:y + BLOCK, x:x + BLOCK, :] = 255
        return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", action="append", help="@name or host:port (repeatable); "
                    "default @etk-pitlink + 127.0.0.1:47500")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--native", default="1280x720")
    ap.add_argument("--video", default="640x360@30", help="default tap WxH@HZ (raw codec)")
    ap.add_argument("--token", default="")
    ap.add_argument("--ram", default="0x10000:0x100000", help="BASE:SIZE")
    ap.add_argument("--ttl-ms", type=int, default=500)
    ap.add_argument("--secs", type=float, default=0, help="stop after this long (0 = until EXIT / Ctrl-C)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    nw, nh = (int(x) for x in a.native.split("x"))
    size, _, hz = a.video.partition("@")
    vw, vh = (int(x) for x in size.split("x"))
    base, _, sz = a.ram.partition(":")
    car = FakeCar(a.listen or ("@etk-pitlink", "127.0.0.1:47500"), a.fps, (nw, nh), (vw, vh, int(hz or 30), 0),
                  a.token, (int(base, 0), int(sz, 0)), a.ttl_ms, verbose=a.verbose).start()
    print(f"fake_server: listening on {' '.join(car.addrs)} · {car.fps:g} fps · RAM {car.ram_base:#x}+{len(car.ram):#x}"
          + (" · token set" if car.token else ""), file=sys.stderr, flush=True)
    try:
        car.exited.wait(a.secs or None)
    except KeyboardInterrupt:
        pass
    car.stop()
    print(f"fake_server: {car.flip} flips, {len(car.pads)} PADs", file=sys.stderr)


if __name__ == "__main__":
    main()
