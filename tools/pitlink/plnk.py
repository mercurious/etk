#!/usr/bin/env python3
"""plnk — PLNK v1, the Pitlink wire (docs/PITLINK_SPEC.md §3), host side.

ONE module owns the bytes. The C++ server inside RPCS3 (GTK Pitlink) is written
against the same §3 tables; everything here is a literal transcription of them
(struct formats are the spec's own strings), so a byte-exact test of this file
is a byte-exact test of the contract. Nothing in here touches a socket.

Conventions (§3): little-endian, no padding; a string is u16 len + UTF-8 (no
NUL); every message is a 16-byte header `<IHHII` magic·type·flags·seq·len then
`len` payload bytes. Replies echo the request's seq; server pushes use seq 0
(so a client never sends seq 0).

Choices where §3 is silent are marked `CHOICE:` — the C++ side must agree.
"""
import struct

PROTO = 1
MAGIC = 0x4B4E4C50  # b"PLNK" on the wire
HDR = struct.Struct("<IHHII")  # magic, type, flags, seq, len
MAX_PAYLOAD = 64 << 20  # CHOICE: sanity cap; a native 1920x1080 BGRA frame is 8.3 MB

# ---- message types ----------------------------------------------------------
HELLO, PING = 0x0001, 0x0002
PAD, PAD_RELEASE = 0x0010, 0x0011
VIDEO, SNAP = 0x0020, 0x0021
RAM_READ, RAM_WRITE, WATCH = 0x0030, 0x0031, 0x0032
PAUSE, RESUME, STEP, EXIT = 0x0040, 0x0041, 0x0042, 0x0043
STATUS = 0x0050
HELLO_OK, PONG, ACK = 0x8001, 0x8002, 0x800F
RAM_DATA, STATUS_REPLY, FRAME, EVENT = 0x8030, 0x8050, 0x8090, 0x80A0
NAMES = {v: k for k, v in list(globals().items())
         if k.isupper() and isinstance(v, int) and k not in ("PROTO", "MAGIC", "MAX_PAYLOAD")}

# ---- enums -------------------------------------------------------------------
CAP_VIDEO, CAP_PAD, CAP_RAM, CAP_CLOCK = 1, 2, 4, 8
ROLE_CONTROLLER, ROLE_OBSERVER = 0, 1
ST_RUNNING, ST_PAUSED, ST_OTHER = 0, 1, 2
CODEC_RAW, CODEC_ZSTD = 0, 1  # FRAME codec: raw BGRA8 (w*h*4) / zstd of that
FRAME_SNAP, FRAME_OVERLAYS = 1, 2  # FRAME flags bit0 / bit1
MODE_OFF, MODE_MERGE, MODE_EXCLUSIVE, MODE_DEFAULT = 0, 1, 2, 255
EV_BOOT, EV_STATE, EV_TITLE, EV_DEADMAN, EV_RESCUE, EV_ERROR, EV_STEP = 1, 2, 3, 4, 5, 6, 7
EVENT_NAMES = {1: "boot", 2: "state", 3: "title", 4: "deadman", 5: "rescue", 6: "error", 7: "step"}
RAM_READ_MAX_ITEMS, RAM_READ_MAX_SIZE = 4096, 65536
WATCH_MAX, WATCH_SIZES = 256, (1, 2, 4, 8)

# buttons bit 0..15 (bits 0-7 = CELL_PAD_CTRL_* digital-1, 8-15 = digital-2 << 8)
BUTTONS = ("select", "l3", "r3", "start", "up", "right", "down", "left",
           "l2", "r2", "l1", "r1", "triangle", "circle", "cross", "square")
BIT = {n: i for i, n in enumerate(BUTTONS)}
# pressure[i] = cellPad press offset 8+i
PRESSURE = ("right", "left", "up", "down", "triangle", "circle", "cross", "square",
            "l1", "r1", "l2", "r2")
PIDX = {n: i for i, n in enumerate(PRESSURE)}
STICKS = ("lx", "ly", "rx", "ry")
ALIASES = {"x": "cross", "o": "circle", "tri": "triangle", "sq": "square"}

# ---- fixed structs (the spec's own format strings) --------------------------
S_HELLO = struct.Struct("<HH")
S_PONG = struct.Struct("<QQ")
S_PAD = struct.Struct("<BBH12sBBBBHHQ")  # 32 B
S_VIDEO = struct.Struct("<HHHBBB3x")  # 12 B
S_U8, S_U16, S_U32 = struct.Struct("<B"), struct.Struct("<H"), struct.Struct("<I")
S_RANGE = struct.Struct("<II")
S_WATCH = struct.Struct("<IB3x")  # 8 B
S_HELLO_OK = struct.Struct("<HHI")
S_ACK = struct.Struct("<Hh")
S_RAMITEM = struct.Struct("<BI")
S_STATUS = struct.Struct("<IQQ")
S_FRAME = struct.Struct("<QQQHHBBHf")  # 36 B
S_EVENT = struct.Struct("<H")
assert S_PAD.size == 32 and S_FRAME.size == 36 and HDR.size == 16 and S_VIDEO.size == 12


class ProtocolError(ValueError):
    pass


# ---- framing -----------------------------------------------------------------
def encode(mtype, payload=b"", seq=0):
    return HDR.pack(MAGIC, mtype, 0, seq, len(payload)) + payload


def decode_header(b):
    magic, mtype, flags, seq, n = HDR.unpack(b)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic {magic:#010x}")
    if n > MAX_PAYLOAD:
        raise ProtocolError(f"payload {n} B over cap")
    return mtype, flags, seq, n  # flags: 0 on send, ignored on receipt


def pstr(s):
    b = (s or "").encode("utf-8")
    if len(b) > 0xFFFF:
        raise ProtocolError("string over 65535 B")
    return S_U16.pack(len(b)) + b


class Rd:
    """Cursor over a payload; every short read is a ProtocolError."""
    def __init__(self, b):
        self.b, self.o = memoryview(b), 0

    def take(self, st):
        if self.o + st.size > len(self.b):
            raise ProtocolError("short payload")
        v = st.unpack_from(self.b, self.o)
        self.o += st.size
        return v

    def raw(self, n):
        if self.o + n > len(self.b):
            raise ProtocolError("short payload")
        v = bytes(self.b[self.o:self.o + n])
        self.o += n
        return v

    def str(self):
        return self.raw(self.take(S_U16)[0]).decode("utf-8", "replace")

    def rest(self):
        v = bytes(self.b[self.o:])
        self.o = len(self.b)
        return v


# ---- client -> server ----------------------------------------------------------
def pack_hello(token="", proto=PROTO):
    return S_HELLO.pack(proto, 0) + pstr(token)


def unpack_hello(p):
    r = Rd(p)
    proto, _ = r.take(S_HELLO)
    return {"proto": proto, "token": r.str()}


def pack_pad_release(port=0):
    return S_U8.pack(port)


def unpack_pad_release(p):
    return Rd(p).take(S_U8)[0]


def pack_video(w=640, h=360, hz=30, codec=CODEC_RAW, zstd_level=1, overlays=0):
    return S_VIDEO.pack(w, h, hz, codec, zstd_level, overlays)


def unpack_video(p):
    return dict(zip(("w", "h", "hz", "codec", "zstd_level", "overlays"), Rd(p).take(S_VIDEO)))


def pack_snap(codec=CODEC_RAW):
    return S_U8.pack(codec)


def unpack_snap(p):
    return Rd(p).take(S_U8)[0]


def pack_ram_read(items):
    items = list(items)
    return S_U32.pack(len(items)) + b"".join(S_RANGE.pack(a, s) for a, s in items)


def unpack_ram_read(p):
    r = Rd(p)
    return [r.take(S_RANGE) for _ in range(r.take(S_U32)[0])]


def pack_ram_write(addr, data):
    return S_RANGE.pack(addr, len(data)) + bytes(data)


def unpack_ram_write(p):
    r = Rd(p)
    addr, n = r.take(S_RANGE)
    return addr, r.raw(n)


def pack_watch(items):
    items = list(items)
    return S_U32.pack(len(items)) + b"".join(S_WATCH.pack(a, s) for a, s in items)


def unpack_watch(p):
    r = Rd(p)
    return [r.take(S_WATCH) for _ in range(r.take(S_U32)[0])]


def pack_step(flips):
    return S_U32.pack(flips)


def unpack_step(p):
    return Rd(p).take(S_U32)[0]


def pack_exit(savestate=False):
    return S_U8.pack(1 if savestate else 0)


def unpack_exit(p):
    return bool(Rd(p).take(S_U8)[0])


# PING, PAUSE, RESUME, STATUS: empty payload.


# ---- server -> client ----------------------------------------------------------
def pack_hello_ok(proto=PROTO, caps=0xF, role=0, version="", title_id="", title=""):
    return S_HELLO_OK.pack(proto, caps, role) + pstr(version) + pstr(title_id) + pstr(title)


def unpack_hello_ok(p):
    r = Rd(p)
    proto, caps, role = r.take(S_HELLO_OK)
    return {"proto": proto, "caps": caps, "role": role,
            "version": r.str(), "title_id": r.str(), "title": r.str()}


def pack_pong(flip, host_ns):
    return S_PONG.pack(flip, host_ns)


def unpack_pong(p):
    return Rd(p).take(S_PONG)  # (flip, host_ns)


def pack_ack(for_type, status=0, message=""):
    return S_ACK.pack(for_type, status) + pstr(message)


def unpack_ack(p):
    r = Rd(p)
    t, st = r.take(S_ACK)
    return t, st, r.str()


def pack_ram_data(items):
    """items: bytes per readable request item, None per unreadable one."""
    out = [S_U32.pack(len(items))]
    for b in items:
        out.append(S_RAMITEM.pack(0, 0) if b is None else S_RAMITEM.pack(1, len(b)) + bytes(b))
    return b"".join(out)


def unpack_ram_data(p):
    r = Rd(p)
    out = []
    for _ in range(r.take(S_U32)[0]):
        ok, n = r.take(S_RAMITEM)
        b = r.raw(n)
        out.append(b if ok else None)
    return out


def pack_status_reply(state, flip, vblank, title_id="", title=""):
    return S_STATUS.pack(state, flip, vblank) + pstr(title_id) + pstr(title)


def unpack_status_reply(p):
    r = Rd(p)
    state, flip, vblank = r.take(S_STATUS)
    return {"state": state, "flip": flip, "vblank": vblank, "title_id": r.str(), "title": r.str()}


FRAME_KEYS = ("flip", "vblank", "host_ns", "w", "h", "codec", "flags", "watch_len", "fps")


def pack_frame(flip, vblank, host_ns, w, h, codec, flags, fps, watch=b"", image=b""):
    return S_FRAME.pack(flip, vblank, host_ns, w, h, codec, flags, len(watch), fps) + bytes(watch) + bytes(image)


def unpack_frame(p):
    """-> (meta dict, watch bytes, image bytes)."""
    r = Rd(p)
    meta = dict(zip(FRAME_KEYS, r.take(S_FRAME)))
    watch = r.raw(meta["watch_len"])
    return meta, watch, r.rest()


def pack_event(kind, text=""):
    return S_EVENT.pack(kind) + pstr(text)


def unpack_event(p):
    r = Rd(p)
    return r.take(S_EVENT)[0], r.str()


# ---- the pad -------------------------------------------------------------------
def stick_byte(v):
    """-1..1 -> 0..255, 128 centre (-1 -> 0, +1 -> 255). Y: -1 = up -> 0."""
    v = max(-1.0, min(1.0, float(v)))
    return max(0, min(255, int(round(128 + v * (128 if v < 0 else 127)))))


def stick_float(b):
    return (b - 128) / (128.0 if b <= 128 else 127.0)


def button_name(n):
    n = ALIASES.get(n.lower(), n.lower())
    if n not in BIT:
        raise KeyError(f"unknown button {n!r} (have {', '.join(BUTTONS)})")
    return n


class PadState:
    """One PAD (§3): buttons by name, per-button pressure, sticks as -1..1 floats.

    `pressure` holds EXPLICIT pressures only (0..255). A pressed pressure-class
    button with no explicit pressure goes on the wire as 0, which §3 defines as
    255 (full press); `.pressure_of()` reports the effective value.
    """
    __slots__ = ("port", "mode", "buttons", "pressure", "lx", "ly", "rx", "ry", "ttl_ms", "at_flip")

    def __init__(self, port=0, buttons=(), pressure=None, lx=0.0, ly=0.0, rx=0.0, ry=0.0,
                 mode=MODE_DEFAULT, ttl_ms=0, at_flip=0):
        self.port, self.mode, self.ttl_ms, self.at_flip = port, mode, ttl_ms, at_flip
        self.buttons = {button_name(b) for b in buttons}
        self.pressure = {}
        for k, v in (pressure or {}).items():
            self.press(k, v)
        self.lx, self.ly, self.rx, self.ry = lx, ly, rx, ry

    def press(self, name, value=1):
        """value: 0/False release · 1/True full · float in (0,1) = analog · int 2..255 raw."""
        name = button_name(name)
        if value is True or value == 1:
            self.buttons.add(name)
            self.pressure.pop(name, None)
            return self
        if not value:
            self.buttons.discard(name)
            self.pressure.pop(name, None)
            return self
        raw = int(round(value * 255)) if isinstance(value, float) and value <= 1 else int(value)
        raw = max(1, min(255, raw))
        self.buttons.add(name)
        if name in PIDX:
            self.pressure[name] = raw
        return self

    def set(self, key, value):
        """CLI/MCP form: lx=-0.3 · r2=0.6 · cross=1."""
        k = key.lower()
        if k in STICKS:
            setattr(self, k, float(value))
        else:
            self.press(k, value)
        return self

    def pressure_of(self, name):
        name = button_name(name)
        if name not in self.buttons:
            return 0
        return self.pressure.get(name, 255)

    @property
    def bits(self):
        return sum(1 << BIT[b] for b in self.buttons)

    def copy(self, **kw):
        c = PadState(self.port, self.buttons, dict(self.pressure), self.lx, self.ly, self.rx, self.ry,
                     self.mode, self.ttl_ms, self.at_flip)
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def is_neutral(self):
        return not self.buttons and all(stick_byte(getattr(self, s)) == 128 for s in STICKS)

    def pack(self):
        pr = bytes(self.pressure.get(n, 0) if n in self.buttons else 0 for n in PRESSURE)
        return S_PAD.pack(self.port, self.mode, self.bits, pr,
                          stick_byte(self.lx), stick_byte(self.ly), stick_byte(self.rx), stick_byte(self.ry),
                          self.ttl_ms, 0, self.at_flip)

    @classmethod
    def unpack(cls, p):
        port, mode, bits, pr, lx, ly, rx, ry, ttl, _res, at = Rd(p).take(S_PAD)
        st = cls(port=port, mode=mode, ttl_ms=ttl, at_flip=at)
        st.buttons = {n for i, n in enumerate(BUTTONS) if bits >> i & 1}
        st.pressure = {n: pr[i] for i, n in enumerate(PRESSURE) if pr[i] and n in st.buttons}
        st.lx, st.ly, st.rx, st.ry = (stick_float(b) for b in (lx, ly, rx, ry))
        return st

    def raw(self):
        """Wire view: what the server sees (sticks as bytes, effective pressure)."""
        return {"port": self.port, "mode": self.mode, "buttons": self.bits,
                "pressure": [self.pressure_of(n) for n in PRESSURE],
                "sticks": [stick_byte(getattr(self, s)) for s in STICKS],
                "ttl_ms": self.ttl_ms, "at_flip": self.at_flip}

    def __repr__(self):
        b = ",".join(n + (f"@{self.pressure[n]}" if n in self.pressure else "")
                     for n in BUTTONS if n in self.buttons)
        s = " ".join(f"{k}={getattr(self, k):+.2f}" for k in STICKS if stick_byte(getattr(self, k)) != 128)
        return f"PadState(port={self.port} [{b}] {s} ttl={self.ttl_ms} at={self.at_flip})"


def pack_pad(state):
    return state.pack()


def unpack_pad(p):
    return PadState.unpack(p)


# ---- image codecs ----------------------------------------------------------------
# zstd: stdlib compression.zstd (3.14+) first, else the zstandard module, else codec 0 only.
try:
    from compression import zstd as _zstd
    ZSTD = "compression.zstd"

    def zstd_compress(b, level=1):
        return _zstd.compress(bytes(b), level=level or 3)

    def zstd_decompress(b, size_hint=0):
        return _zstd.decompress(bytes(b))
except ImportError:
    try:
        import zstandard as _zstd
        ZSTD = "zstandard"

        def zstd_compress(b, level=1):
            return _zstd.ZstdCompressor(level=level or 3).compress(bytes(b))

        def zstd_decompress(b, size_hint=0):
            return _zstd.ZstdDecompressor().decompress(bytes(b), max_output_size=size_hint or (64 << 20))
    except ImportError:
        ZSTD = None

        def zstd_compress(b, level=1):
            raise ProtocolError("no zstd on this host (python < 3.14 and no zstandard): use codec 0")

        zstd_decompress = zstd_compress

CODECS = (CODEC_RAW, CODEC_ZSTD) if ZSTD else (CODEC_RAW,)


def encode_image(bgra, codec=CODEC_RAW, level=1):
    raw = bgra.tobytes() if hasattr(bgra, "tobytes") else bytes(bgra)
    if codec == CODEC_RAW:
        return raw
    if codec == CODEC_ZSTD:
        return zstd_compress(raw, level)
    raise ProtocolError(f"codec {codec} not in PLNK v1")


def decode_image(data, codec, w, h):
    """-> numpy uint8 (h, w, 4) in BGRA order."""
    import numpy as np
    n = w * h * 4
    if codec == CODEC_ZSTD:
        data = zstd_decompress(data, n)
    elif codec != CODEC_RAW:
        raise ProtocolError(f"codec {codec} not in PLNK v1")
    if len(data) != n:
        raise ProtocolError(f"frame {w}x{h}: {len(data)} B, want {n}")
    return np.frombuffer(data, dtype=np.uint8).reshape(h, w, 4)


# ---- WATCH helpers ----------------------------------------------------------------
def split_watch(watch, items):
    """Frame WATCH bytes -> per-entry raw bytes, given the WATCH list that produced them
    (None if the lengths disagree: the frame predates a WATCH change)."""
    sizes = [s for _, s in items]
    if sum(sizes) != len(watch):
        return None
    out, o = [], 0
    for s in sizes:
        out.append(bytes(watch[o:o + s]))
        o += s
    return out


def be_value(b, kind="u"):
    """Decode one big-endian guest value: kind u (unsigned) · s (signed) · f (float, 4/8 B)."""
    if kind == "f":
        if len(b) not in (4, 8):
            raise ValueError("float watch needs size 4 or 8")
        return struct.unpack(">f" if len(b) == 4 else ">d", b)[0]
    return int.from_bytes(b, "big", signed=(kind == "s"))
