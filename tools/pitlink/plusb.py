"""plusb -- the Pitlink USB link (PLUSB v1): PLNK v1 sessions over one raw USB bulk pipe pair.

No IP, no TCP, no NetworkManager between the Engineer and the car. The car exposes a
vendor-class USB interface (FunctionFS, `bin/etk_pitlink_usbd.py`); the M1 claims it with
libusb (`usb_broker.py`). Each PLNK session (one per local client) is a CHANNEL; the link
carries them as SEGMENTS on the one bulk pipe pair, and the car relays each channel into
RPCS3's own Pitlink socket (@etk-pitlink). PLNK v1 itself is untouched end to end.

Segment = 12-byte header + payload:  <4sHBBI  magic b"PLUB", chan, kind, flags, len
  chan 0 = the link itself (HELLO/HELLO_ACK/PING/PONG); sessions use 1..65535.
  DATA      chan's byte stream (split freely: PLNK frames itself)
  OPEN      host -> car: start chan; payload = target ("" / "pitlink" = RPCS3's @etk-pitlink,
            "garage" = the car daemon's own JSON-line control service: launch games, ...).
            DATA may follow at once
  CLOSE     either way: chan is over (payload: reason, utf-8)
  HELLO     host -> car, chan 0: link (re)start; payload = 16-byte nonce. Drops every chan.
  HELLO_ACK car -> host, chan 0: same nonce + the car's status (utf-8 after the nonce)
  PING/PONG chan 0: link-level round trip (the car's daemon answers; RPCS3 not involved)

Resync: a host that dies mid-segment leaves the car's parser mid-payload. The new host
first sends the vendor control request VREQ_RESET (out of band, ep0), which drops every
channel and puts the car's parser into HUNT mode (discard until a HELLO header); then the
HELLO. The host likewise discards IN bytes until the exact HELLO_ACK + its nonce, so a
stale frame still in flight cannot be mistaken for the link.
"""
import collections
import os
import queue
import socket
import struct
import threading
import time

MAGIC = b"PLUB"
SEG = struct.Struct("<4sHBBI")
DATA, OPEN, CLOSE, HELLO, HELLO_ACK, PING, PONG = range(7)
KINDS = ("DATA", "OPEN", "CLOSE", "HELLO", "HELLO_ACK", "PING", "PONG")
LINK = 0
NONCE = 16
MAX_PAYLOAD = 256 * 1024 - SEG.size  # one FunctionFS write per segment stays <= 256 KiB
IO_CHUNK = 256 * 1024                 # bulk read size (a multiple of every wMaxPacketSize)

# The function's USB identity (interface descriptor) and its vendor control request.
VID, PID = 0x1D6B, 0x0104             # ROCKNIX's composite "cdc" gadget (NCM + us)
IF_CLASS, IF_SUBCLASS, IF_PROTOCOL = 0xFF, 0x50, 0x4C   # vendor, 'P', 'L'
IF_STRING = "ETK Pitlink"
VREQ_RESET = 0x01                     # bmRequestType 0x41 (vendor, interface, OUT), wLength 0

CAR_TARGET = "@etk-pitlink"           # RPCS3's own Pitlink socket (abstract unix)
HOST_SOCKET = "@etk-pitlink-usb"      # the M1 broker's local socket for PLNK sessions
GARAGE_SOCKET = "@etk-garage-usb"     # ... and for garage (car control) sessions
TARGET_PITLINK, TARGET_GARAGE = b"pitlink", b"garage"
CHAN_QUEUE_CAP = 64 << 20             # bytes a slow local reader may owe before its chan closes


def seg(chan, kind, payload=b"", flags=0):
    return SEG.pack(MAGIC, chan, kind, flags, len(payload)) + bytes(payload)


def data_segs(chan, payload):
    mv = memoryview(payload)
    return [seg(chan, DATA, mv[i:i + MAX_PAYLOAD]) for i in range(0, len(mv), MAX_PAYLOAD)] or []


def hello_prefix():
    """The 12 header bytes every HELLO starts with (the car hunts for exactly these)."""
    return SEG.pack(MAGIC, LINK, HELLO, 0, NONCE)


def hello_ack_prefix(nonce):
    """HELLO_ACK's header has a variable length (status text), so the host hunts for the
    magic + chan + kind, then checks the nonce that follows."""
    return MAGIC + struct.pack("<HBB", LINK, HELLO_ACK, 0), nonce


class Parser:
    """Byte stream -> (chan, kind, flags, payload). Starts (and resets into) HUNT mode:
    nothing is believed until `anchor` is seen; a malformed header re-enters HUNT."""

    def __init__(self, anchor=MAGIC, max_payload=MAX_PAYLOAD):
        self.anchor, self.max_payload = anchor, max_payload
        self.buf = bytearray()
        self.hunting = True
        self.dropped = 0

    def reset(self, anchor=None):
        self.buf.clear()
        self.hunting = True
        if anchor is not None:
            self.anchor = anchor

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            if self.hunting:
                i = self.buf.find(self.anchor)
                if i < 0:
                    keep = len(self.anchor) - 1
                    self.dropped += max(0, len(self.buf) - keep)
                    del self.buf[:max(0, len(self.buf) - keep)]
                    return out
                self.dropped += i
                del self.buf[:i]
                self.hunting = False
            if len(self.buf) < SEG.size:
                return out
            magic, chan, kind, flags, n = SEG.unpack_from(self.buf)
            if magic != MAGIC or kind >= len(KINDS) or n > self.max_payload:
                self.dropped += 1
                del self.buf[:1]
                self.hunting = True
                self.anchor = MAGIC
                continue
            if len(self.buf) < SEG.size + n:
                return out
            payload = bytes(self.buf[SEG.size:SEG.size + n])
            del self.buf[:SEG.size + n]
            out.append((chan, kind, flags, payload))


class OutQueue:
    """Bounded queue of wire bytes toward the USB pipe: producers block when it is full
    (USB backpressure reaches RPCS3's own latest-wins gate), `clear()` on link reset."""

    def __init__(self, max_items=64):
        self.q = queue.Queue(max_items)
        self.held = None  # an item take() pulled but could not fit (consumer-thread only)

    def put(self, b, timeout=None):
        self.q.put(b, timeout=timeout)

    def take(self, timeout=0.2, coalesce=64 * 1024, cap=IO_CHUNK):
        """One write's worth: the next item, plus small ones queued behind it (<= cap)."""
        if self.held is not None:
            b, self.held = self.held, None
        else:
            b = self.q.get(timeout=timeout)
        if len(b) >= coalesce:
            return b
        parts, n = [b], len(b)
        while n < coalesce:
            try:
                nxt = self.q.get_nowait()
            except queue.Empty:
                break
            if n + len(nxt) > cap:
                self.held = nxt
                break
            parts.append(nxt)
            n += len(nxt)
        return b"".join(parts)

    def clear(self):
        self.held = None
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return


def connect_target(addr, timeout=1.0):
    if addr.startswith("@"):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sa = "\0" + addr[1:]
    else:
        host, _, port = addr.rpartition(":")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sa = (host, int(port))
    s.settimeout(timeout)
    try:
        s.connect(sa)
    except OSError:
        s.close()
        raise
    s.settimeout(None)
    return s


# ======================================================================== the car's side
class CarRelay:
    """The car's half, USB-agnostic. `feed(bytes)` takes what the bulk OUT endpoint read;
    `out` holds what the IN endpoint must write. Each OPENed chan becomes one connection to
    `target` (RPCS3's @etk-pitlink), relayed both ways."""

    def __init__(self, target=CAR_TARGET, status=lambda: "", log=lambda *a: None, services=None):
        self.target, self.status, self.log = target, status, log
        self.services = dict(services or {})  # name -> serve(sock): in-daemon targets (garage)
        self.parser = Parser(anchor=hello_prefix())
        self.out = OutQueue()
        self.chans = {}  # chan -> socket
        self.lock = threading.Lock()
        self.gen = 0
        self.stats = collections.Counter()

    # -- link control --------------------------------------------------------------
    def vendor_reset(self):
        """VREQ_RESET (ep0): drop everything, hunt for the next HELLO."""
        self._drop_all("link reset")
        self.parser.reset(anchor=hello_prefix())
        self.stats["resets"] += 1

    def link_down(self):
        """Host gone (DISABLE / unplug): same as a reset, nothing can be in flight."""
        self.vendor_reset()

    def _drop_all(self, why):
        with self.lock:
            self.gen += 1
            chans, self.chans = self.chans, {}
        for s in chans.values():
            _hangup(s)
        self.out.clear()
        if chans:
            self.log(f"dropped {len(chans)} channel(s): {why}")

    # -- OUT endpoint --------------------------------------------------------------
    def feed(self, data):
        self.stats["out_bytes"] += len(data)
        for chan, kind, _flags, payload in self.parser.feed(data):
            if chan == LINK:
                self._link_seg(kind, payload)
            elif kind == OPEN:
                self._open(chan, payload)
            elif kind == DATA:
                self._data(chan, payload)
            elif kind == CLOSE:
                with self.lock:
                    s = self.chans.pop(chan, None)
                if s is not None:
                    _hangup(s)

    def _link_seg(self, kind, payload):
        if kind == HELLO:
            self._drop_all("HELLO")
            self.out.put(seg(LINK, HELLO_ACK, payload[:NONCE] + self.status().encode()))
            self.stats["hellos"] += 1
            self.log("link HELLO")
        elif kind == PING:
            self.out.put(seg(LINK, PONG, payload))

    def _open(self, chan, target=b""):
        with self.lock:
            old = self.chans.pop(chan, None)
        if old is not None:
            _hangup(old)
        name = (bytes(target) or TARGET_PITLINK).decode(errors="replace")
        try:
            if name in self.services:
                s, peer = socket.socketpair()
                threading.Thread(target=self._serve, args=(name, peer), name=f"plusb-{name}-{chan}",
                                 daemon=True).start()
            elif name == TARGET_PITLINK.decode():
                s = connect_target(self.target)
            else:
                self.out.put(seg(chan, CLOSE, f"no such target on the car: {name!r}".encode()))
                return
        except OSError as e:
            why = "RPCS3 Pitlink is not running" if isinstance(e, (ConnectionRefusedError, FileNotFoundError)) else str(e)
            self.out.put(seg(chan, CLOSE, why.encode()))
            self.stats["open_fail"] += 1
            return
        s.settimeout(2.0)  # a stuck RPCS3 must not wedge the OUT pipe forever
        with self.lock:
            self.chans[chan] = s
            gen = self.gen
        self.stats["opens"] += 1
        threading.Thread(target=self._pump, args=(chan, s, gen), name=f"plusb-car-{chan}", daemon=True).start()

    def _serve(self, name, sock):
        try:
            self.services[name](sock)
        except Exception as e:  # a service bug must not take the link down
            self.log(f"{name} service: {e!r}")
        finally:
            _hangup(sock)

    def _data(self, chan, payload):
        with self.lock:
            s = self.chans.get(chan)
        if s is None:
            return
        try:
            s.sendall(payload)
        except OSError:
            self._close(chan, s, "RPCS3 stopped reading")

    def _pump(self, chan, s, gen):
        """RPCS3 -> IN endpoint, for one chan."""
        why = "RPCS3 closed the session"
        try:
            while True:
                try:
                    b = s.recv(MAX_PAYLOAD)
                except socket.timeout:  # idle session (the timeout exists for sendall)
                    if self.gen != gen or self.chans.get(chan) is not s:
                        return
                    continue
                if not b:
                    break
                for sg in data_segs(chan, b):
                    while True:
                        if self.gen != gen:
                            return
                        try:
                            self.out.put(sg, timeout=0.5)
                            break
                        except queue.Full:
                            continue
        except OSError as e:
            why = str(e)
        if self.gen == gen:
            self._close(chan, s, why)

    def _close(self, chan, s, why):
        with self.lock:
            live = self.chans.get(chan) is s
            if live:
                del self.chans[chan]
        _hangup(s)
        if live:
            self.out.put(seg(chan, CLOSE, why.encode()))


def _hangup(s):
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    s.close()


# ======================================================================= the host's side
class HostMux:
    """The M1's half, USB-agnostic. Local clients `attach(sock)` -> a chan each; `feed()`
    takes what the bulk IN endpoint read; `out` holds what the OUT endpoint must write.
    `link_start()` (after VREQ_RESET) sends HELLO and arms the HELLO_ACK hunt."""

    def __init__(self, log=lambda *a: None):
        self.log = log
        self.out = OutQueue()
        self.parser = Parser()
        self.chans = {}  # chan -> _LocalChan
        self.lock = threading.Lock()
        self.next_chan = 1
        self.nonce = None
        self.up = threading.Event()
        self.car_status = ""
        self.pings = {}  # token -> t0
        self.rtt_ms = collections.deque(maxlen=64)
        self.stats = collections.Counter()
        self._await_nonce = False

    def link_start(self):
        self.drop_all("link restart")
        self.up.clear()
        self.nonce = os.urandom(NONCE)
        head, _ = hello_ack_prefix(self.nonce)
        self.parser.reset(anchor=head)
        self._await_nonce = True
        self.out.clear()
        self.out.put(seg(LINK, HELLO, self.nonce))

    def link_down(self, why="USB link lost"):
        self.up.clear()
        self.drop_all(why)

    def drop_all(self, why):
        with self.lock:
            chans, self.chans = self.chans, {}
        for c in chans.values():
            c.close(why)

    def ping(self):
        tok = os.urandom(8)
        self.pings[tok] = time.perf_counter()
        self.out.put(seg(LINK, PING, tok))

    # -- IN endpoint ---------------------------------------------------------------
    def feed(self, data):
        self.stats["in_bytes"] += len(data)
        for chan, kind, _flags, payload in self.parser.feed(data):
            if getattr(self, "_await_nonce", False):
                if chan == LINK and kind == HELLO_ACK and payload[:NONCE] == self.nonce:
                    self._await_nonce = False
                    self.car_status = payload[NONCE:].decode(errors="replace")
                    self.parser.anchor = MAGIC
                    self.up.set()
                    self.log(f"link up ({self.car_status or 'car ready'})")
                continue  # anything before our own HELLO_ACK is a previous host's
            if chan == LINK:
                if kind == PONG:
                    t0 = self.pings.pop(payload, None)
                    if t0 is not None:
                        self.rtt_ms.append((time.perf_counter() - t0) * 1000.0)
                continue
            with self.lock:
                c = self.chans.get(chan)
            if c is None:
                continue
            if kind == DATA:
                c.deliver(payload)
            elif kind == CLOSE:
                with self.lock:
                    self.chans.pop(chan, None)
                c.close(payload.decode(errors="replace") or "car closed the session")

    # -- local clients -------------------------------------------------------------
    def attach(self, sock, target=b""):
        if not self.up.is_set():
            _hangup(sock)
            return None
        with self.lock:
            for _ in range(0xFFFF):
                chan = self.next_chan
                self.next_chan = self.next_chan % 0xFFFF + 1
                if chan not in self.chans:
                    break
            c = _LocalChan(self, chan, sock)
            self.chans[chan] = c
        self.out.put(seg(chan, OPEN, target))
        c.start()
        self.stats["chans"] += 1
        return chan

    def detach(self, chan, notify=True):
        with self.lock:
            c = self.chans.pop(chan, None)
        if c is not None and notify:
            self.out.put(seg(chan, CLOSE, b"host client left"))


class _LocalChan:
    """One local client: its reads become DATA segments; IN data for it is queued and
    sent by its own thread (a slow reader stalls only itself, up to CHAN_QUEUE_CAP)."""

    def __init__(self, mux, chan, sock):
        self.mux, self.chan, self.sock = mux, chan, sock
        self.q = collections.deque()
        self.qbytes = 0
        self.cv = threading.Condition()
        self.closed = None

    def start(self):
        threading.Thread(target=self._rx, name=f"plusb-host-rx{self.chan}", daemon=True).start()
        threading.Thread(target=self._tx, name=f"plusb-host-tx{self.chan}", daemon=True).start()

    def _rx(self):
        try:
            while self.closed is None:
                b = self.sock.recv(MAX_PAYLOAD)
                if not b:
                    break
                for sg in data_segs(self.chan, b):
                    self.mux.out.put(sg)
        except OSError:
            pass
        if self.closed is None:
            self.mux.detach(self.chan)
            self.close("client left")

    def deliver(self, payload):
        with self.cv:
            if self.closed is not None:
                return
            if self.qbytes + len(payload) > CHAN_QUEUE_CAP:
                over = True
            else:
                over = False
                self.q.append(payload)
                self.qbytes += len(payload)
                self.cv.notify()
        if over:
            self.mux.detach(self.chan)
            self.close("local client too slow")

    def _tx(self):
        while True:
            with self.cv:
                while not self.q and self.closed is None:
                    self.cv.wait()
                if not self.q:
                    break
                b = self.q.popleft()
                self.qbytes -= len(b)
            try:
                self.sock.sendall(b)
            except OSError:
                self.mux.detach(self.chan)
                self.close("client left")
                break
        _hangup(self.sock)

    def close(self, why):
        with self.cv:
            if self.closed is None:
                self.closed = why
            self.cv.notify_all()


# ======================================================================= garage (JSON lines)
def serve_json_lines(sock, handle):
    """One garage session: each request line is a JSON object with "op"; each gets exactly
    one reply line {"ok": bool, ...}. `handle(req) -> dict` may raise to report an error."""
    import json
    f = sock.makefile("rwb")
    for line in f:
        try:
            req = json.loads(line)
            rep = dict(handle(req) or {})
            rep.setdefault("ok", True)
        except Exception as e:
            rep = {"ok": False, "err": str(e) or e.__class__.__name__}
        f.write((json.dumps(rep) + "\n").encode())
        f.flush()
