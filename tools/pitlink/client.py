#!/usr/bin/env python3
"""client — PitlinkClient: one PLNK v1 link into the car (or fake_server.py).

  c = PitlinkClient("usb")                   # raw USB (PLUSB via usb_broker.py) -- the default
  c = PitlinkClient("169.254.170.2:47500")   # TCP over USB-net / WiFi (fallback)
  c = PitlinkClient("@etk-pitlink")          # Linux abstract unix socket (same host)
  c.hello(token)                             # role 0 = controller, 1 = observer
  c.video(640, 360, 30, CODEC_ZSTD); f = c.wait_frame(); f.image().save("x.png")
  c.press("cross", ms=120)                   # flip-counted ON THE CAR, see press()

One background reader thread owns the socket's read side. Requests are matched
to replies by seq; pushes (seq 0) land in three places: the LATEST frame (latest
wins — vision wants the newest picture, never a backlog: the autogamer lesson),
a ring of frame-aligned WATCH bytes, and an event queue. Frames are decoded
lazily (only when someone looks at the pixels), so a 30 Hz link costs a recv.

Hands: the car applies PADs at the cellPad boundary and counts holds in flips.
The host never times a press with sleep() — link jitter would become input
jitter. press() stamps BOTH edges with at_flip; hold() keeps the car's dead-man
fed from a heartbeat thread, so a dead host releases the pad within ttl_ms.
"""
import collections
import math
import os
import queue
import socket
import threading
import time

try:
    from . import plnk as P
except ImportError:
    import plnk as P

DEFAULT_ADDR = "usb"
USB_SOCKET = "@etk-pitlink-usb"  # the broker's local socket (plusb.HOST_SOCKET)
BROKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb_broker.py")
BROKER_LOG = os.path.expanduser("~/.cache/etk/pitlink-usb.log")
GARAGE_SOCKET = os.environ.get("PITLINK_GARAGE", "@etk-garage-usb")  # car control, same USB link
FPS_FALLBACK = 60.0  # before any FRAME has told us the car's flip rate
PRESS_LEAD = 2  # flips between "now" and the press edge: >= 1 full flip for the PAD to land
TTL_MARGIN_MS = 500

Event = collections.namedtuple("Event", "kind text t")


class PitlinkError(RuntimeError):
    pass


class PitlinkClosed(PitlinkError):
    pass


class PitlinkTimeout(PitlinkError, TimeoutError):
    pass


def default_addr():
    return os.environ.get("PITLINK_ADDR", DEFAULT_ADDR)


def parse_addr(addr):
    """'usb' -> the USB broker's socket · '@name' -> (AF_UNIX, '\\0name') (abstract: no file to
    unlink) · 'host:port' -> (AF_INET*, (host, port))."""
    if addr == "usb":
        addr = USB_SOCKET
    if addr.startswith("@"):
        return socket.AF_UNIX, "\0" + addr[1:]
    host, _, port = addr.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"pitlink addr {addr!r}: want @name or host:port")
    host = host.strip("[]")
    return (socket.AF_INET6 if ":" in host else socket.AF_INET), (host, int(port))


def spawn_broker():
    """Start usb_broker.py detached (it outlives this process; one per M1)."""
    import subprocess
    import sys
    os.makedirs(os.path.dirname(BROKER_LOG), exist_ok=True)
    with open(BROKER_LOG, "ab") as logf:
        subprocess.Popen([sys.executable, BROKER, "serve"], stdin=subprocess.DEVNULL, stdout=logf,
                         stderr=logf, start_new_session=True, close_fds=True)


def _garage_connect(addr, deadline):
    spawned = False
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect("\0" + addr.lstrip("@"))
            return s
        except (ConnectionRefusedError, FileNotFoundError):
            s.close()
            if time.monotonic() >= deadline:
                raise
            if not spawned:
                spawn_broker()
                spawned = True
            time.sleep(0.2)


class GarageSession:
    """Several garage requests over ONE session (a chunked hunt put is hundreds of them).
    `with GarageSession() as g: g.call({...})` -- each call: a JSON line out, one back."""

    def __init__(self, timeout=60.0, addr=GARAGE_SOCKET):
        self.timeout = timeout
        self.sock = _garage_connect(addr, time.monotonic() + timeout)
        self.f = self.sock.makefile("rwb")

    def call(self, req, timeout=None):
        import json
        self.sock.settimeout(timeout or self.timeout)
        self.f.write((json.dumps(req) + "\n").encode())
        self.f.flush()
        line = self.f.readline()
        if not line:
            raise PitlinkClosed("garage: the car closed the session (USB link down?)")
        rep = json.loads(line)
        if not rep.get("ok"):
            raise PitlinkError(f"garage {req.get('op')}: {rep.get('err')}")
        return rep

    def close(self):
        try:
            self.f.close()
        finally:
            self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def garage(req, timeout=30.0, addr=GARAGE_SOCKET):
    """One request to the car's garage service (launch / running / games / log / dump_threads
    ...) over the USB link: JSON line out, JSON line back. Starts the broker if needed."""
    deadline = time.monotonic() + timeout
    with GarageSession(timeout, addr) as g:
        return g.call(req, timeout=max(1.0, deadline - time.monotonic()))


def open_socket(addr, timeout=5.0):
    fam, sa = parse_addr(addr)
    deadline = time.monotonic() + timeout
    spawned = False
    while True:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            s.connect(sa)
            break
        except (ConnectionRefusedError, FileNotFoundError):
            s.close()
            # "usb": the broker is started on first use, then we wait for its socket
            if addr != "usb" or time.monotonic() >= deadline:
                raise
            if not spawned:
                spawn_broker()
                spawned = True
            time.sleep(0.1)
        except OSError:
            s.close()
            raise
    if fam != socket.AF_UNIX:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 32-byte PADs must not wait for Nagle
    s.settimeout(None)
    return s


def recv_exact(sock, n):
    buf = bytearray(n)
    mv, got = memoryview(buf), 0
    while got < n:
        k = sock.recv_into(mv[got:], n - got)
        if not k:
            raise EOFError("peer closed")
        got += k
    return buf


def recv_msg(sock):
    """-> (type, seq, payload). Raises EOFError / ProtocolError."""
    t, _flags, seq, n = P.decode_header(recv_exact(sock, P.HDR.size))
    return t, seq, (recv_exact(sock, n) if n else b"")


class Frame:
    """One FRAME push: identity (flip/vblank/host_ns/fps), WATCH bytes, the picture."""
    __slots__ = ("flip", "vblank", "host_ns", "w", "h", "codec", "flags", "fps", "watch", "data", "recv_t", "_bgra")

    def __init__(self, meta, watch, data, recv_t):
        for k in ("flip", "vblank", "host_ns", "w", "h", "codec", "flags", "fps"):
            setattr(self, k, meta[k])
        self.watch, self.data, self.recv_t, self._bgra = watch, data, recv_t, None

    @property
    def snap(self):
        return bool(self.flags & P.FRAME_SNAP)

    @property
    def overlays(self):
        return bool(self.flags & P.FRAME_OVERLAYS)

    @property
    def bgra(self):
        if self._bgra is None:
            self._bgra = P.decode_image(self.data, self.codec, self.w, self.h)
        return self._bgra

    def rgb(self):
        import numpy as np
        return np.ascontiguousarray(self.bgra[..., 2::-1])

    def image(self):
        from PIL import Image
        return Image.fromarray(self.rgb(), "RGB")

    def png(self, path=None, max_w=None):
        """PNG bytes (optionally downscaled to max_w); also written to path if given."""
        import io
        im = self.image()
        if max_w and im.width > max_w:
            im = im.resize((max_w, max(1, round(im.height * max_w / im.width))))
        b = io.BytesIO()
        im.save(b, "PNG")
        if path:
            with open(path, "wb") as f:
                f.write(b.getvalue())
        return b.getvalue()

    def meta(self):
        return {"flip": self.flip, "vblank": self.vblank, "host_ns": self.host_ns, "w": self.w, "h": self.h,
                "codec": self.codec, "snap": self.snap, "overlays": self.overlays,
                "fps": round(self.fps, 2), "watch": self.watch.hex()}

    def __repr__(self):
        return f"Frame(flip={self.flip} {self.w}x{self.h} codec={self.codec} fps={self.fps:.1f} watch={self.watch.hex()})"


class _Waiter:
    __slots__ = ("ev", "val")

    def __init__(self):
        self.ev, self.val = threading.Event(), None


class PitlinkClient:
    def __init__(self, addr=None, timeout=5.0, connect=True):
        self.addr = addr or default_addr()
        self.timeout = timeout
        self.sock = None
        self.info, self.role = None, None
        self.watch_items = []  # the WATCH list we set (frames' WATCH bytes split by it)
        self.events = queue.Queue()  # Event(kind, text, t) — consumers drain this
        self.nacks = collections.deque(maxlen=64)  # unsolicited ACKs (e.g. -1 to an observer's PAD)
        self.watch_ring = collections.deque(maxlen=600)  # (flip, host_ns, watch bytes), 20 s @ 30 Hz
        self.rtt_ms = None
        self._latest, self._nframes = None, 0
        self._snap, self._nsnaps = None, 0  # SNAP frames kept apart: latest-wins must not eat one
        self._evlog, self._nevents = collections.deque(maxlen=1024), 0
        self._cv = threading.Condition()
        self._waiters, self._wlock = {}, threading.Lock()
        self._slock = threading.Lock()
        self._seq = 0
        self._closed = None
        self._held, self._press_until, self._keep = {}, {}, {}
        if connect:
            self.connect()

    # ---- link ------------------------------------------------------------------------
    def connect(self):
        self.sock = open_socket(self.addr, self.timeout)
        self._closed = None
        threading.Thread(target=self._reader, args=(self.sock,), name="pitlink-rx", daemon=True).start()
        return self

    def close(self):
        for port in list(self._keep):
            self._stop_keepalive(port)
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
        self._closed = self._closed or "closed by host"

    @property
    def closed(self):
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _reader(self, sock):
        why = "peer closed"
        try:
            while True:
                t, seq, p = recv_msg(sock)
                self._dispatch(t, seq, p)
        except (EOFError, OSError) as e:
            why = str(e) or e.__class__.__name__
        except P.ProtocolError as e:
            why = f"protocol error: {e}"
        finally:
            self._closed = self._closed or why
            with self._wlock:
                ws, self._waiters = list(self._waiters.values()), {}
            for w in ws:
                w.ev.set()
            with self._cv:
                self._cv.notify_all()

    def _dispatch(self, t, seq, p):
        if seq:
            with self._wlock:
                w = self._waiters.pop(seq, None)
            if w:
                w.val = (t, p)
                w.ev.set()
            elif t == P.ACK:
                ft, st, msg = P.unpack_ack(p)
                self.nacks.append((seq, ft, st, msg))
            return
        if t == P.FRAME:
            meta, watch, img = P.unpack_frame(p)
            fr = Frame(meta, watch, img, time.monotonic())
            with self._cv:
                self._latest = fr
                self._nframes += 1
                if fr.snap:
                    self._snap, self._nsnaps = fr, self._nsnaps + 1
                self.watch_ring.append((fr.flip, fr.host_ns, watch))
                self._cv.notify_all()
        elif t == P.EVENT:
            kind, text = P.unpack_event(p)
            ev = Event(kind, text, time.monotonic())
            self.events.put(ev)
            with self._cv:
                self._nevents += 1
                self._evlog.append((self._nevents, ev))
                self._cv.notify_all()
        # anything else unsolicited: a newer server talking; ignore

    def _next_seq(self):
        with self._slock:
            self._seq = self._seq % 0xFFFFFFFF + 1  # never 0: seq 0 marks a push
            return self._seq

    def _send(self, t, payload, seq):
        if self._closed:
            raise PitlinkClosed(self._closed)
        try:
            with self._slock:
                self.sock.sendall(P.encode(t, payload, seq))
        except OSError as e:
            raise PitlinkClosed(str(e)) from e

    def _post(self, t, payload=b""):
        """Fire-and-forget (PAD, PAD_RELEASE). A refusal comes back as an unsolicited ACK -> .nacks."""
        seq = self._next_seq()
        self._send(t, payload, seq)
        return seq

    def _request(self, t, payload=b"", reply=P.ACK, timeout=None):
        seq, w = self._next_seq(), _Waiter()
        with self._wlock:
            self._waiters[seq] = w
        try:
            self._send(t, payload, seq)
        except PitlinkClosed:
            with self._wlock:
                self._waiters.pop(seq, None)
            raise
        if not w.ev.wait(self.timeout if timeout is None else timeout):
            with self._wlock:
                self._waiters.pop(seq, None)
            raise PitlinkTimeout(f"{P.NAMES.get(t, t)}: no reply")
        if w.val is None:
            raise PitlinkClosed(self._closed or "closed")
        rt, rp = w.val
        if rt == P.ACK:
            ft, st, msg = P.unpack_ack(rp)
            if st < 0:
                raise PitlinkError(f"{P.NAMES.get(t, t)} refused ({st}): {msg}")
            if reply != P.ACK:
                raise P.ProtocolError(f"{P.NAMES.get(t, t)}: got ACK, want {P.NAMES.get(reply)}")
            return msg
        if rt != reply:
            raise P.ProtocolError(f"{P.NAMES.get(t, t)}: got {P.NAMES.get(rt, rt)}, want {P.NAMES.get(reply)}")
        return rp

    # ---- session ---------------------------------------------------------------------
    def hello(self, token=""):
        """-> HELLO_OK dict. A wrong token gets the link closed (PitlinkClosed)."""
        self.info = P.unpack_hello_ok(self._request(P.HELLO, P.pack_hello(token), P.HELLO_OK))
        self.role = self.info["role"]
        return self.info

    @property
    def controller(self):
        return self.role == P.ROLE_CONTROLLER

    def ping(self):
        """-> (flip, host_ns) as the car sees them NOW; records rtt_ms."""
        t0 = time.perf_counter()
        flip, ns = P.unpack_pong(self._request(P.PING, b"", P.PONG))
        self.rtt_ms = (time.perf_counter() - t0) * 1e3
        return flip, ns

    def status(self):
        return P.unpack_status_reply(self._request(P.STATUS, b"", P.STATUS_REPLY))

    # ---- eyes ------------------------------------------------------------------------
    def video(self, w=640, h=360, hz=30, codec=None, zstd_level=1, overlays=0):
        """Set the frame tap (controller only; hz 0 = off). codec default: zstd if this host has it."""
        codec = (P.CODEC_ZSTD if P.ZSTD else P.CODEC_RAW) if codec is None else codec
        if codec not in P.CODECS:
            raise PitlinkError(f"codec {codec}: this host cannot decode it (zstd={P.ZSTD})")
        return self._request(P.VIDEO, P.pack_video(w, h, hz, codec, zstd_level, overlays))

    def snap(self, codec=P.CODEC_RAW, wait=True, timeout=None):
        """One native-size frame (FRAME flags bit0). wait -> that Frame. Needs a flip: paused = no snap."""
        mark = self._nsnaps
        self._request(P.SNAP, P.pack_snap(codec))
        if wait:
            return self._wait(lambda: self._snap if self._nsnaps > mark else None, timeout, "snap frame")

    def latest_frame(self):
        return self._latest

    def wait_frame(self, timeout=None, after_flip=None):
        """Block for a frame newer than the current one (or with flip > after_flip)."""
        mark = self._nframes
        if after_flip is None:
            return self._wait(lambda: self._latest if self._nframes > mark else None, timeout, "frame")
        return self._wait(lambda: self._latest if self._latest and self._latest.flip > after_flip else None,
                          timeout, f"frame after flip {after_flip}")

    def fps(self):
        f = self._latest
        return f.fps if f is not None and f.fps > 0 else FPS_FALLBACK

    def frames_for(self, ms):
        return max(1, int(round(ms * self.fps() / 1000.0)))

    # ---- hands -----------------------------------------------------------------------
    def pad(self, state):
        """Send one PAD as-is (fire-and-forget). Returns its seq (a refusal shows in .nacks)."""
        return self._post(P.PAD, state.pack())

    def hold(self, state=None, port=0, keepalive=True, **controls):
        """Set and KEEP: hold(lx=-0.3, r2=0.6, cross=1). keepalive re-sends it every ttl/3 so the
        car's dead-man stays fed while this process lives — and fires when it dies."""
        if state is None:
            state = P.PadState(port=port)
            for k, v in controls.items():
                state.set(k, v)
        state = state.copy(at_flip=0)
        self._held[state.port] = state
        self.pad(state)
        if keepalive:
            self._start_keepalive(state.port, (state.ttl_ms or 500) / 3000.0)
        return state

    @property
    def held(self):
        return dict(self._held)

    def release(self, port=0):
        self._stop_keepalive(port)
        self._held.pop(port, None)
        self._press_until.pop(port, None)
        return self._post(P.PAD_RELEASE, P.pack_pad_release(port))

    def press(self, buttons, ms=120, port=0, lead=PRESS_LEAD, value=1):
        """Tap `buttons` for frames_for(ms) flips, counted on the car.

        Both edges are PADs stamped with at_flip: down at F+lead, up at F+lead+n, where
        F is the car's flip from a PING (a FRAME's flip lags 1-2 flips: async readback) and
        n = round(ms * fps / 1000) from the latest FRAME's flip-rate EMA. The car applies
        each at its flip, so the game sees exactly n flips of the press whatever the link
        jitter (as long as the PAD lands within `lead` flips: 2 = >= 16 ms at 60 Hz, vs
        ~1 ms USB-net RTT). lead=0 = down "now" (n minus however many flips the PAD took).
        The press rides on top of any hold() (and returns to it); ttl covers the whole tap.
        """
        if isinstance(buttons, str):
            buttons = [buttons]
        n, fps = self.frames_for(ms), self.fps()
        base = self._held.get(port) or P.PadState(port=port)
        flip, _ = self.ping()
        start = flip + lead if lead > 0 else 0
        end = (start or flip) + n
        span_ms = (max(lead, 0) + n) * 1000.0 / fps
        ttl = min(0xFFFF, int(math.ceil(span_ms)) + TTL_MARGIN_MS)
        down = base.copy(at_flip=start, ttl_ms=ttl)
        for b in buttons:
            down.press(b, value)
        up = base.copy(at_flip=end, ttl_ms=ttl)
        self._press_until[port] = time.monotonic() + span_ms / 1000.0 + 0.05  # heartbeat stands aside
        self.pad(down)
        self.pad(up)
        return {"flips": n, "down_at": start or flip, "up_at": end, "ttl_ms": ttl}

    def _start_keepalive(self, port, period):
        self._stop_keepalive(port)
        stop = threading.Event()

        def beat():
            while not stop.wait(period) and not self._closed:
                st = self._held.get(port)
                if st is None:
                    break
                if time.monotonic() < self._press_until.get(port, 0):
                    continue
                try:
                    self.pad(st)
                except PitlinkError:
                    break
        th = threading.Thread(target=beat, name=f"pitlink-hold{port}", daemon=True)
        self._keep[port] = (th, stop)
        th.start()

    def _stop_keepalive(self, port):
        k = self._keep.pop(port, None)
        if k:
            k[1].set()

    # ---- memory ----------------------------------------------------------------------
    def ram_read(self, items):
        """[(addr, size)] -> [bytes | None] (None = unreadable)."""
        items = [(int(a), int(s)) for a, s in items]
        if len(items) > P.RAM_READ_MAX_ITEMS:
            raise ValueError(f"RAM_READ: {len(items)} items > {P.RAM_READ_MAX_ITEMS}")
        return P.unpack_ram_data(self._request(P.RAM_READ, P.pack_ram_read(items), P.RAM_DATA))

    def ram_write(self, addr, data):
        return self._request(P.RAM_WRITE, P.pack_ram_write(int(addr), bytes(data)))

    def watch(self, items):
        """Set the per-flip WATCH list [(addr, size 1/2/4/8)]; [] clears it."""
        items = [(int(a), int(s)) for a, s in items]
        msg = self._request(P.WATCH, P.pack_watch(items))
        self.watch_items = items
        return msg

    def watch_rows(self):
        """Snapshot of the frame-aligned WATCH ring: [(flip, host_ns, watch bytes)]."""
        with self._cv:
            return list(self.watch_ring)

    def watch_values(self, frame=None, kinds=None):
        """Frame WATCH bytes -> one value per WATCH entry (big-endian; kinds: 'u'|'s'|'f' each)."""
        frame = frame or self._latest
        if frame is None:
            return None
        parts = P.split_watch(frame.watch, self.watch_items)
        if parts is None:
            return None
        kinds = kinds or ["u"] * len(parts)
        return [P.be_value(b, k) for b, k in zip(parts, kinds)]

    # ---- clock -----------------------------------------------------------------------
    def pause(self):
        return self._request(P.PAUSE)

    def resume(self):
        return self._request(P.RESUME)

    def step(self, n=1, wait=False, timeout=None):
        """Resume for n flips then pause (EVENT 7). wait -> that Event."""
        mark = self._nevents
        self._request(P.STEP, P.pack_step(n))
        if wait:
            return self.wait_event(P.EV_STEP, timeout, after=mark)

    def exit(self, savestate=False):
        return self._request(P.EXIT, P.pack_exit(savestate))

    # ---- events ----------------------------------------------------------------------
    @property
    def event_mark(self):
        return self._nevents

    def events_since(self, mark):
        with self._cv:
            return [ev for i, ev in self._evlog if i > mark]

    def wait_event(self, kind=None, timeout=None, after=None):
        """Block for an EVENT (of `kind`) pushed after `after` (default: now)."""
        mark = self._nevents if after is None else after

        def hit():
            for i, ev in self._evlog:
                if i > mark and (kind is None or ev.kind == kind):
                    return ev
        return self._wait(hit, timeout, f"event {kind}")

    def wait_any(self, mark, timeout):
        """Block until a frame or event arrives after `mark` = (nframes, nevents); -> new mark."""
        with self._cv:
            self._cv.wait_for(lambda: (self._nframes, self._nevents) != mark or self._closed, timeout)
            return self._nframes, self._nevents

    @property
    def mark(self):
        return self._nframes, self._nevents

    def _wait(self, pred, timeout, what):
        timeout = self.timeout if timeout is None else timeout
        with self._cv:
            r = None

            def ok():
                nonlocal r
                r = pred()
                return r is not None or self._closed
            self._cv.wait_for(ok, timeout)
            if r is not None:
                return r
            if self._closed:
                raise PitlinkClosed(self._closed)
            raise PitlinkTimeout(f"no {what} in {timeout:.1f} s")
