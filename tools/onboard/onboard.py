#!/usr/bin/env python3
"""onboard -- ONBOARD: the car's screen on the pit wall (a scrcpy-class mirror over the Pitlink USB link).

  onboard.py                         the window: PANEL (the car's screen) by default, GAME when only the game streams
  onboard.py --game                  GAME only: Pitlink EYES as an observer (the PANEL channel is never opened,
                                     so nothing captures on the car)
  onboard.py --panel                 pin PANEL (GAME is still observed, for the ring)
  onboard.py --snap OUT.png          one frame from the active/selected source to a PNG, no window
  onboard.py --ring DIR              the ring flushes to DIR/ring/ (default state/onboard/<epoch>/ring/)
  onboard.py --record                record from the start: the ring, then every frame live, until r / q
  onboard.py --fps N --quality Q --scale S    the PANEL CTRL sent when the channel opens
  onboard.py --gst                   decode PANEL H.264 through GStreamer (openh264dec); without it an H.264
                                     frame is ring-recorded and shown as "H.264 (needs --gst)"

Keys: p pin PANEL · g pin GAME · a auto · s snap · r record on/off · q quit.

Two sources, one window (docs: etk-dossiers/Onboard_LiveMirror_20261025.md §2, §8.3):
  GAME   PLNK v1 FRAME pushes from @etk-pitlink-usb. ONBOARD is an OBSERVER there: it HELLOs and sends
         nothing else that touches the emulator. If its HELLO lands the wheel (no one else holds it), it
         drops the link at once and retries later -- a viewer must never hold the wheel a hunt needs.
  PANEL  ONBD v1 messages from @etk-onboard-usb (the broker opens a car channel with target "onboard").

The ring (the forensic instrument): the last 60 s of received frames, both sources, payloads as received.
On link death (PitlinkClosed, socket EOF, broker gone) or on record, it goes to DIR/ring/: index.tsv
(one row per frame), frames.bin (payloads concatenated), last.png, sheet.png (4x4 contact sheet),
meta.json (reason, epoch, duration_s: joins the ledger row by [epoch - duration_s, epoch]).
"""
import argparse
import collections
import io
import json
import os
import queue
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tools", "pitlink"))
import plnk as P  # noqa: E402
import plusb  # noqa: E402
import client as C  # noqa: E402
from client import PitlinkClient, PitlinkClosed, PitlinkError  # noqa: E402

# ============================================================================ ONBD v1 (§8.3)
ONBD_MAGIC, ONBD_VER = b"ONBD", 1
HDR = struct.Struct("<4sBBBBHHIQ")   # magic ver kind codec flags w h seq car_ns  (24 B)
LEN = struct.Struct("<I")            # payload length                            (4 B)
FIXED = HDR.size + LEN.size          # 28
INFO, FRAME, CTRL, END = 0, 1, 2, 3
KIND_NAMES = {INFO: "INFO", FRAME: "FRAME", CTRL: "CTRL", END: "END"}
CODEC_RAW, CODEC_ZSTD, CODEC_JPEG, CODEC_H264 = 0, 1, 2, 3   # PLNK's 0/1 are the same two
CODEC_NAMES = {CODEC_RAW: "raw", CODEC_ZSTD: "zstd", CODEC_JPEG: "jpeg", CODEC_H264: "h264"}
FLAG_KEY, FLAG_SNAP = 1, 2
MAX_PAYLOAD = 64 << 20

Msg = collections.namedtuple("Msg", "kind codec flags w h seq car_ns payload ver")


class OnbdError(ValueError):
    pass


def pack_msg(kind, payload=b"", codec=0, flags=0, w=0, h=0, seq=0, car_ns=0, ver=ONBD_VER):
    """One ONBD message: the 24-byte header, the 4-byte length, the payload."""
    payload = bytes(payload)
    return HDR.pack(ONBD_MAGIC, ver, kind, codec, flags, w, h, seq & 0xFFFFFFFF, car_ns) + LEN.pack(len(payload)) + payload


def _length(buf, offset=0):
    """Validate the fixed part at buf[offset:] -> the payload length."""
    magic, ver = HDR.unpack_from(buf, offset)[:2]
    if magic != ONBD_MAGIC:
        raise OnbdError(f"bad magic {bytes(magic)!r}")
    if ver != ONBD_VER:
        raise OnbdError(f"ONBD version {ver} (this host speaks {ONBD_VER})")
    (n,) = LEN.unpack_from(buf, offset + HDR.size)
    if n > MAX_PAYLOAD:
        raise OnbdError(f"payload {n} B > {MAX_PAYLOAD}")
    return n


def parse_msg(buf, offset=0):
    """-> (Msg, next_offset), or None when buf[offset:] does not yet hold a whole message.
    Raises OnbdError on a bad magic, an unknown version or an absurd length."""
    if len(buf) - offset < FIXED:
        return None
    end = offset + FIXED + _length(buf, offset)
    if len(buf) < end:
        return None
    _magic, ver, kind, codec, flags, w, h, seq, car_ns = HDR.unpack_from(buf, offset)
    return Msg(kind, codec, flags, w, h, seq, car_ns, bytes(buf[offset + FIXED:end]), ver), end


def pack_json(kind, obj):
    return pack_msg(kind, json.dumps(obj, separators=(",", ":")).encode())


def pack_ctrl(obj):
    return pack_json(CTRL, obj)


def msg_json(m):
    try:
        v = json.loads(m.payload.decode("utf-8", "replace") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def read_msg(sock):
    """One ONBD message off a stream socket. EOFError when the peer closes."""
    head = bytes(C.recv_exact(sock, FIXED))
    n = _length(head)
    return parse_msg(head + (bytes(C.recv_exact(sock, n)) if n else b""))[0]


# ============================================================================ frames
class Rec:
    """One received frame, either source: identity + the payload exactly as it came off the wire."""
    __slots__ = ("source", "flip", "vblank", "host_ns", "seq", "w", "h", "codec", "flags", "fps", "watch",
                 "data", "recv_t", "recv_epoch")

    def __init__(self, source, data, w, h, codec, flags=0, flip=None, vblank=None, host_ns=None, seq=None,
                 fps=None, watch=b"", recv_t=None, recv_epoch=None):
        self.source, self.data, self.w, self.h, self.codec, self.flags = source, data, w, h, codec, flags
        self.flip, self.vblank, self.host_ns, self.seq, self.fps, self.watch = flip, vblank, host_ns, seq, fps, watch
        self.recv_t = time.monotonic() if recv_t is None else recv_t
        self.recv_epoch = time.time() if recv_epoch is None else recv_epoch

    @classmethod
    def from_game(cls, f):
        return cls("game", bytes(f.data), f.w, f.h, f.codec, f.flags, flip=f.flip, vblank=f.vblank,
                   host_ns=f.host_ns, fps=f.fps, watch=bytes(f.watch), recv_t=f.recv_t)

    @classmethod
    def from_panel(cls, m):
        return cls("panel", m.payload, m.w, m.h, m.codec, m.flags, host_ns=m.car_ns, seq=m.seq)

    @property
    def decodable(self):
        return self.codec in (CODEC_RAW, CODEC_ZSTD, CODEC_JPEG)

    @property
    def label(self):
        return f"GAME flip {self.flip}" if self.source == "game" else f"PANEL seq {self.seq}"


def decode_rgb(rec):
    """-> numpy (h, w, 3) uint8 RGB, or None for H.264 (stateful: only the --gst decoder reads it)."""
    import numpy as np
    if rec.codec in (CODEC_RAW, CODEC_ZSTD):
        bgra = P.decode_image(rec.data, rec.codec, rec.w, rec.h)
        return np.ascontiguousarray(bgra[..., 2::-1])
    if rec.codec == CODEC_JPEG:
        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(rec.data)).convert("RGB"))
    return None


def to_image(rgb):
    from PIL import Image
    return Image.fromarray(rgb, "RGB")


def placeholder(text, size=(640, 360)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, (24, 24, 28))
    ImageDraw.Draw(im).text((16, size[1] // 2 - 6), text, fill=(220, 220, 220))
    return im


# ============================================================================ the ring
class Ring:
    """The last `secs` of received frames (both sources), capped at `max_bytes` of payload."""

    def __init__(self, secs=60.0, max_bytes=1 << 30):
        self.secs, self.max_bytes = float(secs), int(max_bytes)
        self.q = collections.deque()
        self.nbytes = 0
        self.added = 0
        self.lock = threading.Lock()

    def add(self, rec):
        with self.lock:
            self.q.append(rec)
            self.nbytes += len(rec.data)
            self.added += 1
            self._trim(rec.recv_t)

    def _trim(self, now):
        q = self.q
        while q and (now - q[0].recv_t > self.secs or (self.nbytes > self.max_bytes and len(q) > 1)):
            self.nbytes -= len(q.popleft().data)

    def snapshot(self, now=None):
        with self.lock:
            if now is not None:
                self._trim(now)
            return list(self.q)

    def __len__(self):
        return len(self.q)


INDEX_COLS = ("source", "flip", "vblank", "host_ns", "recv_t", "recv_epoch", "seq", "offset", "len", "w", "h",
              "codec", "flags", "fps", "watch")
SHEET_N, SHEET_TILE = 16, (240, 135)


def _cell(v, fmt=None):
    if v is None:
        return "-"
    return fmt % v if fmt else str(v)


def ring_dir(base):
    """base/ring, or base/ring.2, ring.3 ... when an earlier flush already holds it. Created here."""
    for n in range(1, 10000):
        path = os.path.join(base, "ring" if n == 1 else f"ring.{n}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise OSError(f"{base}: no free ring directory")


class RingWriter:
    """index.tsv + frames.bin as rows arrive; last.png + sheet.png + meta.json at close."""

    def __init__(self, path):
        self.path = path
        self.offset, self.rows = 0, 0
        self.counts = collections.Counter()
        self.tail = collections.deque(maxlen=SHEET_N)  # the last decodable frames, for the pictures
        self.first_epoch = self.last_epoch = None
        self.idx = self.bin = None

    def open(self):
        os.makedirs(self.path, exist_ok=True)
        self.idx = open(os.path.join(self.path, "index.tsv"), "w", buffering=1)
        self.bin = open(os.path.join(self.path, "frames.bin"), "wb")
        self.idx.write("\t".join(INDEX_COLS) + "\n")
        return self

    def write(self, r):
        if self.bin is None or self.bin.closed:
            return  # a frame that raced the recorder's close
        self.bin.write(r.data)
        self.idx.write("\t".join((
            r.source, _cell(r.flip), _cell(r.vblank), _cell(r.host_ns), "%.6f" % r.recv_t, "%.6f" % r.recv_epoch,
            _cell(r.seq), str(self.offset), str(len(r.data)), str(r.w), str(r.h),
            CODEC_NAMES.get(r.codec, str(r.codec)), str(r.flags), _cell(r.fps, "%.2f"),
            r.watch.hex() if r.watch else "-")) + "\n")
        self.offset += len(r.data)
        self.rows += 1
        self.counts[r.source] += 1
        self.first_epoch = r.recv_epoch if self.first_epoch is None else self.first_epoch
        self.last_epoch = r.recv_epoch
        if r.decodable:
            self.tail.append(r)

    def close(self, reason, meta=None):
        for f in (self.idx, self.bin):
            if f:
                f.close()
        pics = []
        for r in self.tail:
            try:
                pics.append((r, to_image(decode_rgb(r))))
            except Exception:  # a corrupt payload is still in frames.bin; it just has no picture
                continue
        if pics:
            pics[-1][1].save(os.path.join(self.path, "last.png"))
            contact_sheet(pics, self.last_epoch).save(os.path.join(self.path, "sheet.png"))
        now = time.time()
        m = {"reason": reason, "epoch": round(now, 3),
             "duration_s": round(now - self.first_epoch, 3) if self.first_epoch else 0.0,
             "first_recv_epoch": self.first_epoch, "last_recv_epoch": self.last_epoch,
             "frames": dict(self.counts), "bytes": self.offset, "columns": list(INDEX_COLS),
             "pictures": len(pics)}
        m.update(meta or {})
        with open(os.path.join(self.path, "meta.json"), "w") as f:
            json.dump(m, f, indent=1, default=str)
        return self.path


def contact_sheet(pics, t_ref=None):
    """4x4 tiles of the last 16 decodable frames (oldest top-left), each labelled flip/seq + age."""
    from PIL import Image, ImageDraw
    tw, th = SHEET_TILE
    lab = 14
    sheet = Image.new("RGB", (4 * tw, 4 * (th + lab)), (0, 0, 0))
    d = ImageDraw.Draw(sheet)
    pics = pics[-SHEET_N:]
    for i, (r, im) in enumerate(pics):
        x, y = (i % 4) * tw, (i // 4) * (th + lab)
        t = im.copy()
        t.thumbnail((tw, th))
        sheet.paste(t, (x + (tw - t.width) // 2, y + (th - t.height) // 2))
        age = f" {r.recv_epoch - t_ref:+.2f}s" if t_ref else ""
        d.text((x + 3, y + th + 1), f"{r.label}{age}", fill=(255, 220, 0))
    return sheet


class DiskWorker:
    """Every disk write happens here, in order: the receive threads only enqueue."""

    def __init__(self):
        self.q = queue.Queue()
        self.errors = []
        threading.Thread(target=self._run, name="onboard-disk", daemon=True).start()

    def submit(self, fn, *a):
        self.q.put((fn, a))

    def _run(self):
        while True:
            fn, a = self.q.get()
            try:
                fn(*a)
            except Exception as e:
                self.errors.append(repr(e))
                print(f"onboard: disk: {e!r}", file=sys.stderr)
            finally:
                self.q.task_done()

    def wait(self, timeout=30.0):
        t = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < t:
            time.sleep(0.01)
        return not self.q.unfinished_tasks


# ============================================================================ H.264 (--gst only)
class GstH264:
    """appsrc ! h264parse ! openh264dec ! videoconvert ! RGB appsink. push() Annex-B access units in
    arrival order; latest() -> the newest decoded RGB picture (or None)."""

    PIPE = ("appsrc name=src is-live=true do-timestamp=true format=time "
            "caps=video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! openh264dec ! "
            "videoconvert ! video/x-raw,format=RGB ! appsink name=sink sync=false max-buffers=2 drop=true")

    def __init__(self):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        self.Gst = Gst
        self.pipe = Gst.parse_launch(self.PIPE)
        self.src, self.sink = self.pipe.get_by_name("src"), self.pipe.get_by_name("sink")
        self.pipe.set_state(Gst.State.PLAYING)
        self._latest, self.decoded = None, 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self._pull, name="onboard-h264", daemon=True).start()

    def push(self, data):
        self.src.emit("push-buffer", self.Gst.Buffer.new_wrapped(bytes(data)))

    def _pull(self):
        import numpy as np
        while not self.stop.is_set():
            sample = self.sink.emit("try-pull-sample", 200 * 1000 * 1000)
            if sample is None:
                continue
            s = sample.get_caps().get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            buf = sample.get_buffer()
            ok, mi = buf.map(self.Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                a = np.frombuffer(mi.data, np.uint8)
                stride = len(a) // h
                rgb = a[:stride * h].reshape(h, stride)[:, :w * 3].reshape(h, w, 3).copy()
            finally:
                buf.unmap(mi)
            with self.lock:
                self._latest = rgb
                self.decoded += 1

    def latest(self):
        with self.lock:
            return self._latest

    def close(self):
        self.stop.set()
        self.pipe.set_state(self.Gst.State.NULL)


# ============================================================================ sources
GAME_STALE_S = 2.0       # GAME: flip # unchanged this long = STALE
GAME_LIVE_S = 1.0        # GAME frames "flowing" = one arrived this recently
PANEL_LIVE_MIN_S = 2.0   # PANEL "sending" = a frame within max(this, 3 intervals)
PANEL_STALE_FLOOR_S = 0.25
WHEEL_RETRY_S = 5.0      # after refusing the wheel: wait this long before the next HELLO
BACKOFF = (0.25, 0.5, 1.0, 2.0, 4.0)
SNAP_PANEL_GRACE_S = 2.0


class Source:
    def __init__(self, name):
        self.name = name
        self.connected = False
        self.link = "down"
        self.info = {}          # GAME: HELLO_OK · PANEL: the latest INFO
        self.latest = None      # Rec
        self.latest_ok = None   # the newest decodable Rec
        self.changed_t = None   # when flip (GAME) / seq (PANEL) last changed
        self.times = collections.deque(maxlen=120)
        self.frames = 0
        self.end_reason = None

    def on_frame(self, rec):
        key = rec.flip if self.name == "game" else rec.seq
        old = None if self.latest is None else (self.latest.flip if self.name == "game" else self.latest.seq)
        if key != old or self.changed_t is None:
            self.changed_t = rec.recv_t
        self.latest = rec
        if rec.decodable:
            self.latest_ok = rec
        self.times.append(rec.recv_t)
        self.frames += 1

    def fps(self, now):
        t = [x for x in self.times if now - x <= 2.0]
        if len(t) < 2 or now - t[-1] > 1.0:
            return 0.0
        return (len(t) - 1) / max(1e-6, t[-1] - t[0])

    def interval(self, now):
        """PANEL's expected frame interval: the slower of what INFO promises and what arrives."""
        nominal = 1.0 / self.info["fps"] if self.info.get("fps") else 0.0
        t = list(self.times)[-8:]
        measured = (t[-1] - t[0]) / (len(t) - 1) if len(t) >= 2 else 0.0
        return max(nominal, measured)

    def policy_off(self):
        """PANEL: the car says it is sending nothing (RUNNING with policy 0, or fps 0)."""
        i = self.info
        if not i:
            return False
        if "fps" in i and not i.get("fps"):
            return True
        return i.get("mode") == "RUNNING" and not i.get("policy_fps") and not i.get("pinned")


class Onboard:
    """The viewer minus the window: both links, the source policy, stale, the ring, snap, record."""

    def __init__(self, game_addr="usb", panel_addr=plusb.ONBOARD_SOCKET, want_game=True, want_panel=True,
                 pin=None, ring_base=None, ctrl=None, token="", gst=False, ring_secs=60.0, ring_bytes=1 << 30,
                 timeout=5.0, log=None):
        self.game_addr, self.panel_addr = game_addr, panel_addr
        self.want_game, self.want_panel = want_game, want_panel
        self.pinned = pin
        self.ring_base = ring_base
        self.ctrl = dict(ctrl if ctrl is not None else {"fps": 5, "quality": 70, "scale": 0.5})
        self.token, self.timeout = token, timeout
        self.log = log or (lambda *a: None)
        self.game, self.panel = Source("game"), Source("panel")
        self.ring = Ring(ring_secs, ring_bytes)
        self.events = collections.deque(maxlen=256)  # (epoch, source, text)
        self.disk = DiskWorker()
        self.flushes = []  # [(path, reason)]
        self.lock = threading.RLock()
        self.stop_ev = threading.Event()
        self.recorder = None
        self._flushed_mark = 0
        self._choice = "panel" if want_panel else "game"
        self._game_client = None
        self._panel_sock, self._panel_slock = None, threading.Lock()
        self._spawned = False
        self.t0 = time.monotonic()
        self.h264 = GstH264() if gst else None
        self.threads = []

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self):
        for want, fn, name in ((self.want_game, self._game_loop, "onboard-game"),
                               (self.want_panel, self._panel_loop, "onboard-panel")):
            if want:
                th = threading.Thread(target=fn, name=name, daemon=True)
                th.start()
                self.threads.append(th)
        return self

    def stop(self):
        self.stop_ev.set()
        c = self._game_client
        if c is not None:
            c.close()
        with self._panel_slock:
            s = self._panel_sock
        if s is not None:
            plusb._hangup(s)
        for th in self.threads:
            th.join(2.0)
        if self.recorder is not None:
            self.record(False)
        if self.h264:
            self.h264.close()
        self.disk.wait()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def _event(self, source, text):
        self.events.append((time.time(), source, text))
        self.log(f"{source}: {text}")

    def _wait(self, secs):
        return self.stop_ev.wait(secs)

    # ---- GAME: PLNK observer -----------------------------------------------------------
    def _game_loop(self):
        tries = 0
        while not self.stop_ev.is_set():
            try:
                c = _ObserverClient(self.game_addr, timeout=self.timeout, connect=False)
                c.on_frame = self._on_game
                c.connect()
            except OSError as e:
                self.game.link = f"down: {e}"
                self._wait(BACKOFF[min(tries, len(BACKOFF) - 1)])
                tries += 1
                continue
            got_link, why = False, "closed"
            try:
                info = c.hello(self.token)
                if c.controller:
                    # nobody else holds the wheel and the car just gave it to us: give it back NOW
                    c.close()
                    self.game.link = "no controller on the car: refused the wheel, retrying"
                    self._event("game", "HELLO landed the wheel: dropped the link (observer only)")
                    self._wait(WHEEL_RETRY_S)
                    continue
                with self.lock:
                    self.game.connected, self.game.info = True, info
                    self.game.link = "observer"
                    self._game_client = c
                got_link, tries = True, 0
                self._event("game", f"observer on {info.get('title_id')} {info.get('title')}")
                while not self.stop_ev.is_set() and not c.closed:
                    c.wait_any(c.mark, 0.5)
                why = c.closed or "closed"
            except PitlinkError as e:
                why = str(e)
            finally:
                with self.lock:
                    self.game.connected = False
                    if self._game_client is c:
                        self._game_client = None
                c.close()
            if self.stop_ev.is_set():
                return
            self.game.link = f"down: {why}"
            if got_link:
                self._link_died("game", why)
            self._wait(BACKOFF[min(tries, len(BACKOFF) - 1)])
            tries += 1

    def _on_game(self, frame):
        rec = Rec.from_game(frame)
        with self.lock:
            self.game.on_frame(rec)
        self._ingest(rec)

    # ---- PANEL: ONBD over @etk-onboard-usb ---------------------------------------------
    def _panel_connect(self):
        fam, sa = C.parse_addr(self.panel_addr)
        while True:
            s = socket.socket(fam, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            try:
                s.connect(sa)
                s.settimeout(None)
                return s
            except (ConnectionRefusedError, FileNotFoundError):
                s.close()
                # the broker serves @etk-onboard-usb: start it on first use, as PitlinkClient("usb") does
                # (unless the GAME link is about to: two brokers racing for one socket helps nobody)
                game_spawns = self.want_game and self.game_addr == "usb"
                if self.panel_addr != plusb.ONBOARD_SOCKET or self._spawned or game_spawns:
                    raise
                self._spawned = True
                C.spawn_broker()
                if self._wait(1.0):
                    raise
            except OSError:
                s.close()
                raise

    def _panel_loop(self):
        tries = 0
        while not self.stop_ev.is_set():
            try:
                s = self._panel_connect()
            except OSError as e:
                self.panel.link = f"down: {e}" + (" (broker without @etk-onboard-usb? restart it)"
                                                   if isinstance(e, ConnectionRefusedError) else "")
                self._wait(BACKOFF[min(tries, len(BACKOFF) - 1)])
                tries += 1
                continue
            with self._panel_slock:
                self._panel_sock = s
            got, why = False, "closed"
            try:
                s.sendall(pack_ctrl(self.ctrl))
                with self.lock:
                    self.panel.connected, self.panel.link, self.panel.end_reason = True, "open", None
                while not self.stop_ev.is_set():
                    m = read_msg(s)
                    got = True
                    tries = 0
                    if m.kind == INFO:
                        info = msg_json(m)
                        with self.lock:
                            self.panel.info = info
                        self._event("panel", "INFO " + json.dumps(info, separators=(",", ":")))
                    elif m.kind == FRAME:
                        self._on_panel(m)
                    elif m.kind == END:
                        reason = m.payload.decode("utf-8", "replace")
                        with self.lock:
                            self.panel.end_reason = reason
                        self._event("panel", f"END {reason}")
                    # CTRL from the car, or a kind from a newer car: ignored
            except (EOFError, OSError, OnbdError) as e:
                why = str(e) or e.__class__.__name__
            finally:
                with self._panel_slock:
                    self._panel_sock = None
                with self.lock:
                    self.panel.connected = False
                plusb._hangup(s)
            if self.stop_ev.is_set():
                return
            if self.panel.end_reason:
                why = f"END {self.panel.end_reason}"
            elif not got:
                why += " before any INFO (car daemon without the onboard target?)"
            self.panel.link = f"down: {why}"
            if got:
                self._link_died("panel", why)
            self._wait(BACKOFF[min(tries, len(BACKOFF) - 1)])
            tries += 1

    def _on_panel(self, m):
        rec = Rec.from_panel(m)
        if rec.codec == CODEC_H264 and self.h264 is not None:
            try:
                self.h264.push(rec.data)
            except Exception as e:
                self._event("panel", f"h264 decoder: {e!r}")
        with self.lock:
            self.panel.on_frame(rec)
        self._ingest(rec)

    def send_ctrl(self, obj):
        """One CTRL to the car (host -> car JSON); False when the PANEL channel is not open."""
        with self._panel_slock:
            s = self._panel_sock
            if s is None:
                return False
            try:
                s.sendall(pack_ctrl(obj))
                return True
            except OSError:
                return False

    # ---- ring / record ------------------------------------------------------------------
    def _ingest(self, rec):
        self.ring.add(rec)
        r = self.recorder
        if r is not None:
            self.disk.submit(r.write, rec)

    def _link_died(self, source, why):
        self._event(source, f"link died: {why}")
        with self.lock:
            fresh = self.ring.added > self._flushed_mark
        if fresh and self.recorder is None:
            self.flush(f"{source} link died: {why}")

    def _base(self):
        return self.ring_base or os.path.join(REPO, "state", "onboard", str(int(time.time())))

    def _meta(self):
        with self.lock:
            return {"game_info": dict(self.game.info), "panel_info": dict(self.panel.info),
                    "events": [(round(t, 3), s, x) for t, s, x in self.events],
                    "ring_secs": self.ring.secs}

    def flush(self, reason="flush"):
        """Write the ring to <base>/ring/ (in the background). -> the directory it goes to."""
        recs = self.ring.snapshot(time.monotonic())
        path = ring_dir(self._base())
        with self.lock:
            self._flushed_mark = self.ring.added
            self.flushes.append((path, reason))
        meta = self._meta()
        w = RingWriter(path)

        def job():
            w.open()
            for r in recs:
                w.write(r)
            w.close(reason, meta)
        self.disk.submit(job)
        self._event("ring", f"flush ({reason}) -> {path}")
        return path

    def record(self, on=None):
        """Recording on: the ring so far, then every frame live, into a new ring dir; off: finish it."""
        on = (self.recorder is None) if on is None else on
        if on and self.recorder is None:
            path = ring_dir(self._base())
            w = RingWriter(path)
            recs = self.ring.snapshot(time.monotonic())

            def opened():
                w.open()
                for r in recs:
                    w.write(r)
            self.disk.submit(opened)
            with self.lock:
                self.recorder = w
                self._flushed_mark = self.ring.added
            self._event("ring", f"recording -> {path}")
            return path
        if not on and self.recorder is not None:
            with self.lock:
                w, self.recorder = self.recorder, None
                self._flushed_mark = self.ring.added
                self.flushes.append((w.path, "record"))
            self.disk.submit(w.close, "record", self._meta())
            self._event("ring", f"recording stopped: {w.path}")
            return w.path
        return None

    # ---- policy -------------------------------------------------------------------------
    def pin(self, source=None):
        self.pinned = source

    def panel_sending(self, now=None):
        now = time.monotonic() if now is None else now
        p = self.panel
        if not p.connected or p.policy_off() or p.latest is None:
            return False
        return now - p.latest.recv_t <= max(PANEL_LIVE_MIN_S, 3 * p.interval(now))

    def game_flowing(self, now=None):
        now = time.monotonic() if now is None else now
        g = self.game
        return g.latest is not None and now - g.latest.recv_t <= GAME_LIVE_S

    def active(self, now=None):
        """PANEL by default; GAME when GAME frames flow and PANEL sends nothing; back when PANEL sends again.
        Neither live: hold the last choice (the picture goes STALE instead of flapping)."""
        if self.pinned:
            return self.pinned
        if not self.want_panel:
            return "game"
        if not self.want_game:
            return "panel"
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.panel_sending(now):
                self._choice = "panel"
            elif self.game_flowing(now):
                self._choice = "game"
            return self._choice

    def stale(self, source=None, now=None):
        now = time.monotonic() if now is None else now
        source = source or self.active(now)
        with self.lock:
            s = self.game if source == "game" else self.panel
            if s.latest is None or s.changed_t is None:
                return False
            if source == "game":
                return now - s.changed_t > GAME_STALE_S
            if s.connected and s.policy_off():
                return False  # deliberately silent (RUNNING policy): OFF, not STALE
            return now - s.changed_t > max(PANEL_STALE_FLOOR_S, 2 * s.interval(now))

    # ---- what the window shows ----------------------------------------------------------
    def frame_for(self, source):
        """-> (Rec or None, RGB ndarray or None, note) for `source`'s newest picture."""
        with self.lock:
            s = self.game if source == "game" else self.panel
            rec, ok = s.latest, s.latest_ok
        if rec is None:
            return None, None, f"waiting for {source.upper()} ({s.link})"
        if rec.codec == CODEC_H264:
            if self.h264 is not None:
                rgb = self.h264.latest()
                return rec, rgb, None if rgb is not None else "H.264: decoding"
            note = "H.264 (needs --gst)"
            if ok is not None:
                return ok, decode_rgb(ok), note
            return rec, None, note
        try:
            return rec, decode_rgb(rec), None
        except Exception as e:
            return rec, None, f"undecodable frame: {e}"

    def status(self, now=None):
        now = time.monotonic() if now is None else now
        src = self.active(now)
        with self.lock:
            s = self.game if src == "game" else self.panel
            gi, pi = self.game.info, self.panel.info
            return {
                "source": src, "pinned": self.pinned, "fps": round(s.fps(now), 1),
                "age_ms": None if s.latest is None else round((now - s.latest.recv_t) * 1000),
                "stale": self.stale(src, now),
                "game_link": self.game.link if self.want_game else "off",
                "panel_link": self.panel.link if self.want_panel else "off",
                "panel_mode": pi.get("mode"), "panel_backend": pi.get("backend"),
                "panel_off": self.panel.connected and self.panel.policy_off(),
                "flip": self.game.latest.flip if self.game.latest else None,
                "seq": self.panel.latest.seq if self.panel.latest else None,
                "title_id": gi.get("title_id"), "title": gi.get("title"),
                "recording": self.recorder.path if self.recorder else None,
                "ring_frames": len(self.ring), "flushes": len(self.flushes),
            }

    def status_line(self, now=None):
        st = self.status(now)
        src = st["source"].upper() + ("*" if st["pinned"] else "")
        age = "-" if st["age_ms"] is None else f"{st['age_ms']} ms"
        panel = st["panel_link"]
        if st["panel_backend"]:
            panel = f"{st['panel_backend']} {st['panel_mode'] or ''}".strip() + (" OFF" if st["panel_off"] else "")
        parts = [src, f"{st['fps']:.1f} fps", f"age {age}", f"game:{st['game_link']} panel:{panel}",
                 f"flip {_cell(st['flip'])}", f"seq {_cell(st['seq'])}",
                 f"{st['title_id'] or ''} {st['title'] or ''}".strip() or "no title"]
        if st["recording"]:
            parts.append("REC")
        if st["stale"]:
            parts.append("STALE")
        return " | ".join(parts)

    # ---- snap ---------------------------------------------------------------------------
    def _snap_source(self):
        if self.pinned:
            return self.pinned
        if not self.want_panel:
            return "game"
        if not self.want_game:
            return "panel"
        with self.lock:
            if self.panel.latest_ok is not None or self.panel.latest is not None:
                return self.active()
            panel_quiet = self.panel.connected and self.panel.policy_off()
            if self.game.latest is not None and (panel_quiet or time.monotonic() - self.t0 > SNAP_PANEL_GRACE_S):
                return "game"
        return None

    def write_still(self, out, timeout=10.0):
        """One frame from the active/selected source -> PNG at `out`. GAME reads the observer
        client's latest_frame/wait_frame only. -> (source, Rec)."""
        deadline = time.monotonic() + timeout
        while True:
            src = self._snap_source()
            if src == "game" and self._game_client is not None:
                c = self._game_client
                f = c.latest_frame()
                if f is None:
                    try:
                        f = c.wait_frame(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
                    except PitlinkError:
                        f = None
                if f is not None:
                    rec = Rec.from_game(f)
                    to_image(decode_rgb(rec)).save(out, "PNG")
                    return "game", rec
            elif src == "panel":
                rec, rgb, note = self.frame_for("panel")
                if rgb is not None:
                    to_image(rgb).save(out, "PNG")
                    return "panel", rec
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no {src or 'GAME/PANEL'} frame in {timeout:.0f} s "
                                   f"(game: {self.game.link}; panel: {self.panel.link})")
            time.sleep(0.02)

    def still_path(self):
        d = os.path.join(self.ring_base or os.path.join(REPO, "state", "onboard"), "snaps")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, time.strftime("onboard-%Y%m%d-%H%M%S") + f"-{self.active()}.png")


class _ObserverClient(PitlinkClient):
    """PitlinkClient that hands EVERY pushed FRAME to on_frame (the ring wants them all; the client's
    own latest-wins slot keeps serving latest_frame/wait_frame). Called on the reader thread."""
    on_frame = None

    def _dispatch(self, t, seq, p):
        super()._dispatch(t, seq, p)
        if t == P.FRAME and not seq and self.on_frame is not None:
            f = self._latest
            if f is not None:
                self.on_frame(f)


# ============================================================================ the window (Tk)
def run_window(ob):
    import tkinter as tk
    root = tk.Tk()
    root.title("ONBOARD")
    root.configure(bg="black")
    root.geometry("960x600")
    status = tk.Label(root, anchor="w", font=("monospace", 10), bg="#111", fg="#ddd", padx=6)
    status.pack(side="bottom", fill="x")
    view = tk.Label(root, bg="black")
    view.pack(side="top", fill="both", expand=True)
    st = {"key": None, "photo": None, "msg": None, "msg_t": 0.0}

    def say(msg):
        st["msg"], st["msg_t"] = msg, time.monotonic()

    def key(ev):
        k = (ev.char or ev.keysym or "").lower()
        if k == "q":
            root.destroy()
        elif k == "p":
            ob.pin("panel")
            say("pinned PANEL")
        elif k == "g":
            ob.pin("game")
            say("pinned GAME")
        elif k == "a":
            ob.pin(None)
            say("auto source")
        elif k == "r":
            path = ob.record()
            say(f"recording -> {path}" if ob.recorder else f"recorded: {path}")
        elif k == "s":
            try:
                out = ob.still_path()
                src, rec = ob.write_still(out, timeout=1.0)
                say(f"snap {rec.label} -> {out}")
            except (TimeoutError, OSError) as e:
                say(f"snap: {e}")
    root.bind("<Key>", key)

    def tick():
        now = time.monotonic()
        src = ob.active(now)
        rec, rgb, note = ob.frame_for(src)
        w, h = max(view.winfo_width(), 64), max(view.winfo_height(), 36)
        k = (id(rec), id(rgb), note, w, h)
        if k != st["key"]:
            st["key"] = k
            im = to_image(rgb) if rgb is not None else placeholder(note or "no picture", (w, h))
            if note and rgb is not None:
                from PIL import ImageDraw
                ImageDraw.Draw(im).text((8, 8), note, fill=(255, 220, 0))
            if (im.width, im.height) != (w, h):
                sc = min(w / im.width, h / im.height)
                im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))))
            b = io.BytesIO()
            im.save(b, "PPM")
            st["photo"] = tk.PhotoImage(data=b.getvalue(), format="PPM")
            view.configure(image=st["photo"])
        line = ob.status_line(now)
        if st["msg"] and now - st["msg_t"] < 4.0:
            line += "  ·  " + st["msg"]
        stale = ob.stale(src, now)
        status.configure(text=line, fg="#ff5050" if stale else "#ddd")
        root.after(16, tick)
    root.after(16, tick)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


# ============================================================================ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="onboard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--game", action="store_true", help="GAME only (Pitlink EYES, observer); no PANEL channel")
    g.add_argument("--panel", action="store_true", help="pin PANEL (GAME still observed for the ring)")
    ap.add_argument("--ring", metavar="DIR", default=None, help="flush the ring to DIR/ring/ "
                    "(default state/onboard/<epoch>/)")
    ap.add_argument("--record", action="store_true", help="record from the start until r / q")
    ap.add_argument("--snap", metavar="OUT.png", default=None, help="write one frame and exit (no window)")
    ap.add_argument("--addr", default=None, help="PLNK address (default $PITLINK_ADDR else usb = the broker)")
    ap.add_argument("--onboard-addr", default=os.environ.get("ONBOARD_ADDR", plusb.ONBOARD_SOCKET),
                    help=f"ONBD address (default $ONBOARD_ADDR else {plusb.ONBOARD_SOCKET})")
    ap.add_argument("--fps", type=float, default=5, help="PANEL frames/s asked of the car (<= 30)")
    ap.add_argument("--quality", type=int, default=70, help="PANEL JPEG quality")
    ap.add_argument("--scale", type=float, default=0.5, help="PANEL capture scale")
    ap.add_argument("--gst", action="store_true", help="decode PANEL H.264 with GStreamer (openh264dec)")
    ap.add_argument("--token", default=os.environ.get("PITLINK_TOKEN", ""))
    ap.add_argument("--timeout", type=float, default=10.0, help="--snap: seconds to wait for a frame")
    ap.add_argument("--ring-secs", type=float, default=60.0, help=argparse.SUPPRESS)
    ap.add_argument("--ring-mb", type=int, default=1024, help="ring payload cap (MB; default 1024)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    fps = int(a.fps) if float(a.fps).is_integer() else a.fps
    ob = Onboard(game_addr=a.addr or C.default_addr(), panel_addr=a.onboard_addr,
                 want_game=True, want_panel=not a.game, pin="game" if a.game else ("panel" if a.panel else None),
                 ring_base=a.ring, ctrl={"fps": fps, "quality": a.quality, "scale": a.scale}, token=a.token,
                 gst=a.gst, ring_secs=a.ring_secs, ring_bytes=a.ring_mb << 20,
                 log=(lambda m: print(f"onboard: {m}", file=sys.stderr)) if a.verbose else None)
    ob.start()
    try:
        if a.snap:
            try:
                src, rec = ob.write_still(a.snap, timeout=a.timeout)
            except TimeoutError as e:
                print(f"onboard: {e}", file=sys.stderr)
                return 1
            print(f"{a.snap}: {src.upper()} {rec.label} {rec.w}x{rec.h} {CODEC_NAMES.get(rec.codec, rec.codec)}")
            return 0
        if a.record:
            print(f"onboard: recording -> {ob.record(True)}", file=sys.stderr)
        run_window(ob)
        return 0
    finally:
        ob.stop()
        for path, reason in ob.flushes:
            print(f"onboard: ring ({reason}) -> {path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
