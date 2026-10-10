#!/usr/bin/env python3
"""Pitlink host side — regression tests (no rig, no mint, no network beyond loopback).

Run from the repo root:   python3 tools/pitlink/test_pitlink.py

  [WIRE]  byte-exact PLNK v1 (docs/PITLINK_SPEC.md §3): every message is checked
          against bytes built BY HAND from the spec table, not against the same
          struct strings the module uses — a test that packs with plnk's own
          formats would pass a wrong format forever. The C++ server is written
          to the same tables, so these expectations are its contract too.
  [LINK]  client <-> fake_server end to end, over BOTH transports (abstract unix
          @name and TCP loopback): HELLO/roles/token, frames (raw + zstd) that
          decode to the flip they claim, WATCH bytes that match the flip they
          ride, RAM, PADs applied at_flip, the TTL dead-man, PAUSE/STEP/RESUME.
  [COND]  the wait_for condition language (tiny, no eval: hostile input is a ValueError).
  [TOOLS] the MCP server (JSON-RPC over stdio) and the CLI, as subprocesses.
"""
import base64
import itertools
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plnk as P  # noqa: E402
from client import Event, PitlinkClient, PitlinkClosed, PitlinkError  # noqa: E402
from fake_server import FakeCar, block_index, cells  # noqa: E402
import mcp_server as M  # noqa: E402

RAM = 0x10000
_uniq = itertools.count()


def le(n, width):
    return int(n).to_bytes(width, "little", signed=n < 0)


def s(txt):
    b = txt.encode()
    return le(len(b), 2) + b


def wait_until(pred, timeout=2.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


# ===================================================================== [WIRE]
class TestWire(unittest.TestCase):
    def test_type_codes_match_spec(self):
        self.assertEqual(
            [P.HELLO, P.PING, P.PAD, P.PAD_RELEASE, P.VIDEO, P.SNAP, P.RAM_READ, P.RAM_WRITE, P.WATCH,
             P.PAUSE, P.RESUME, P.STEP, P.EXIT, P.STATUS],
            [0x0001, 0x0002, 0x0010, 0x0011, 0x0020, 0x0021, 0x0030, 0x0031, 0x0032,
             0x0040, 0x0041, 0x0042, 0x0043, 0x0050])
        self.assertEqual([P.HELLO_OK, P.PONG, P.ACK, P.RAM_DATA, P.STATUS_REPLY, P.FRAME, P.EVENT],
                         [0x8001, 0x8002, 0x800F, 0x8030, 0x8050, 0x8090, 0x80A0])
        self.assertEqual((P.EV_BOOT, P.EV_STATE, P.EV_TITLE, P.EV_DEADMAN, P.EV_RESCUE, P.EV_ERROR, P.EV_STEP),
                         (1, 2, 3, 4, 5, 6, 7))

    def test_header(self):
        b = P.encode(P.STATUS, b"xyz", seq=0x01020304)
        self.assertEqual(b[:16], b"PLNK" + b"\x50\x00" + b"\x00\x00" + b"\x04\x03\x02\x01" + b"\x03\x00\x00\x00")
        self.assertEqual(b[16:], b"xyz")
        self.assertEqual(P.decode_header(b[:16]), (P.STATUS, 0, 0x01020304, 3))
        with self.assertRaises(P.ProtocolError):
            P.decode_header(b"PLNX" + b[4:16])

    def test_pad_is_32_bytes_exact(self):
        st = P.PadState(port=1, buttons=["cross"], mode=P.MODE_EXCLUSIVE, lx=-1.0, ly=-1.0, rx=1.0, ry=0.0,
                        ttl_ms=500, at_flip=0x0102030405)
        st.press("r2", 0.6)  # analog trigger: bit + pressure 153
        pressure = bytearray(12)
        pressure[11] = 153  # R2 = pressure index 11; cross has no explicit pressure -> 0 (= 255)
        want = (b"\x01" + b"\x02" + le((1 << 14) | (1 << 9), 2) + bytes(pressure)
                + bytes([0, 0, 255, 128]) + le(500, 2) + b"\x00\x00" + le(0x0102030405, 8))
        self.assertEqual(len(want), 32)
        self.assertEqual(st.pack(), want)
        back = P.PadState.unpack(want)
        self.assertEqual(back.buttons, {"cross", "r2"})
        self.assertEqual((back.pressure_of("cross"), back.pressure_of("r2"), back.pressure_of("l2")), (255, 153, 0))
        self.assertEqual((back.port, back.mode, back.ttl_ms, back.at_flip), (1, 2, 500, 0x0102030405))
        self.assertEqual([P.stick_byte(back.lx), P.stick_byte(back.ly), P.stick_byte(back.rx), P.stick_byte(back.ry)],
                         [0, 0, 255, 128])

    def test_button_bits(self):
        order = ["select", "l3", "r3", "start", "up", "right", "down", "left",
                 "l2", "r2", "l1", "r1", "triangle", "circle", "cross", "square"]
        for i, name in enumerate(order):
            b = P.PadState(buttons=[name]).pack()
            self.assertEqual(b[2:4], le(1 << i, 2), name)
            self.assertEqual(b[4:16], bytes(12), name)  # full press: pressure 0 (= 255 by §3)
        self.assertEqual(P.PadState(buttons=["x", "o"]).bits, (1 << 14) | (1 << 13))  # aliases

    def test_pressure_index(self):
        order = ["right", "left", "up", "down", "triangle", "circle", "cross", "square", "l1", "r1", "l2", "r2"]
        for i, name in enumerate(order):
            st = P.PadState(pressure={name: 100})
            pr = st.pack()[4:16]
            self.assertEqual(pr[i], 100, name)
            self.assertEqual(sum(pr), 100, name)
            self.assertIn(name, st.buttons)  # a pressure implies the digital bit
        for name in ("select", "l3", "r3", "start"):  # no pressure slot
            self.assertEqual(P.PadState(pressure={name: 0.5}).pack()[4:16], bytes(12))
        st = P.PadState()
        st.press("cross", 0.5)
        self.assertEqual(st.pack()[4 + 6], 128)
        st.press("cross", 0)
        self.assertEqual(st.pack()[2:16], bytes(14))

    def test_sticks(self):
        for v, b in [(-1, 0), (-0.5, 64), (0, 128), (0.5, 192), (1, 255), (-7, 0), (7, 255)]:
            self.assertEqual(P.stick_byte(v), b, v)
        for b in (0, 64, 128, 192, 255):
            self.assertEqual(P.stick_byte(P.stick_float(b)), b)
        self.assertEqual(P.PadState(ly=-1).pack()[17], 0)  # Y: -1 = up = 0
        self.assertEqual(P.PadState().pack()[16:20], bytes([128] * 4))

    def test_frame_meta_36_exact(self):
        watch = b"\x00\x00\x01\x2c"
        p = P.pack_frame(flip=300, vblank=600, host_ns=123456789012, w=640, h=360, codec=1, flags=3,
                         fps=29.5, watch=watch, image=b"IMG")
        want = (le(300, 8) + le(600, 8) + le(123456789012, 8) + le(640, 2) + le(360, 2) + b"\x01" + b"\x03"
                + le(4, 2) + struct.pack("<f", 29.5))
        self.assertEqual(len(want), 36)
        self.assertEqual(p, want + watch + b"IMG")
        meta, w, img = P.unpack_frame(p)
        self.assertEqual((meta["flip"], meta["w"], meta["h"], meta["codec"], meta["flags"], meta["watch_len"]),
                         (300, 640, 360, 1, 3, 4))
        self.assertAlmostEqual(meta["fps"], 29.5)
        self.assertEqual((w, img), (watch, b"IMG"))

    def test_client_messages_exact(self):
        cases = [
            (P.pack_hello("abc"), b"\x01\x00" + b"\x00\x00" + s("abc"), P.unpack_hello, {"proto": 1, "token": "abc"}),
            (P.pack_pad_release(2), b"\x02", P.unpack_pad_release, 2),
            (P.pack_video(640, 360, 30, 1, 3, 1), le(640, 2) + le(360, 2) + le(30, 2) + b"\x01\x03\x01" + bytes(3),
             P.unpack_video, {"w": 640, "h": 360, "hz": 30, "codec": 1, "zstd_level": 3, "overlays": 1}),
            (P.pack_snap(1), b"\x01", P.unpack_snap, 1),
            (P.pack_ram_read([(0x10000, 4), (0x20000, 65536)]),
             le(2, 4) + le(0x10000, 4) + le(4, 4) + le(0x20000, 4) + le(65536, 4),
             P.unpack_ram_read, [(0x10000, 4), (0x20000, 65536)]),
            (P.pack_ram_write(0x10020, b"\xde\xad"), le(0x10020, 4) + le(2, 4) + b"\xde\xad",
             P.unpack_ram_write, (0x10020, b"\xde\xad")),
            (P.pack_watch([(0x10000, 4), (0x1000C, 1)]),
             le(2, 4) + le(0x10000, 4) + b"\x04" + bytes(3) + le(0x1000C, 4) + b"\x01" + bytes(3),
             P.unpack_watch, [(0x10000, 4), (0x1000C, 1)]),
            (P.pack_step(5), le(5, 4), P.unpack_step, 5),
            (P.pack_exit(True), b"\x01", P.unpack_exit, True),
            (P.pack_exit(False), b"\x00", P.unpack_exit, False),
        ]
        for got, want, unpack, val in cases:
            self.assertEqual(got, want, unpack.__name__)
            self.assertEqual(unpack(want), val, unpack.__name__)
        self.assertEqual(len(P.pack_video()), 12)
        self.assertEqual(len(P.pack_watch([(0, 8)])), 4 + 8)

    def test_server_messages_exact(self):
        cases = [
            (P.pack_hello_ok(1, 0xF, 1, "v1", "NPEA00050", "GT5P"),
             le(1, 2) + le(0xF, 2) + le(1, 4) + s("v1") + s("NPEA00050") + s("GT5P"), P.unpack_hello_ok,
             {"proto": 1, "caps": 15, "role": 1, "version": "v1", "title_id": "NPEA00050", "title": "GT5P"}),
            (P.pack_pong(77, 5 * 10**12), le(77, 8) + le(5 * 10**12, 8), P.unpack_pong, (77, 5 * 10**12)),
            (P.pack_ack(P.PAUSE, -1, "no"), b"\x40\x00" + b"\xff\xff" + s("no"), P.unpack_ack, (0x40, -1, "no")),
            (P.pack_ack(P.WATCH, 0, ""), b"\x32\x00\x00\x00\x00\x00", P.unpack_ack, (0x32, 0, "")),
            (P.pack_ram_data([b"\x01\x02", None, b""]),
             le(3, 4) + b"\x01" + le(2, 4) + b"\x01\x02" + b"\x00" + le(0, 4) + b"\x01" + le(0, 4),
             P.unpack_ram_data, [b"\x01\x02", None, b""]),
            (P.pack_status_reply(1, 99, 198, "NPEA00050", "GT"),
             le(1, 4) + le(99, 8) + le(198, 8) + s("NPEA00050") + s("GT"), P.unpack_status_reply,
             {"state": 1, "flip": 99, "vblank": 198, "title_id": "NPEA00050", "title": "GT"}),
            (P.pack_event(7, "flip=5"), b"\x07\x00" + s("flip=5"), P.unpack_event, (7, "flip=5")),
        ]
        for got, want, unpack, val in cases:
            self.assertEqual(got, want, unpack.__name__)
            self.assertEqual(unpack(want), val, unpack.__name__)

    def test_strings_utf8_and_short_payloads(self):
        self.assertEqual(P.pack_event(6, "é"), b"\x06\x00\x02\x00\xc3\xa9")
        with self.assertRaises(P.ProtocolError):
            P.unpack_event(b"\x06\x00\x05\x00ab")
        with self.assertRaises(P.ProtocolError):
            P.PadState.unpack(bytes(31))

    def test_codecs(self):
        import numpy as np
        img = np.arange(8 * 4 * 4, dtype=np.uint8).reshape(4, 8, 4)
        for codec in P.CODECS:
            enc = P.encode_image(img, codec, 1)
            self.assertTrue((P.decode_image(enc, codec, 8, 4) == img).all(), codec)
        with self.assertRaises(P.ProtocolError):
            P.decode_image(b"\x00" * 10, P.CODEC_RAW, 8, 4)


# ===================================================================== [LINK]
class LinkBase:
    """Run against one transport; subclasses pick it."""
    LISTEN = None

    def setUp(self):
        self.clients = []
        self.car = FakeCar(listen=(self.listen(),), fps=60, video=(64, 36, 60, P.CODEC_RAW),
                           ram=(RAM, 0x10000), native=(128, 72)).start()
        self.addr = self.car.addrs[0]

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.car.stop()

    def client(self, hello=True, token=""):
        c = PitlinkClient(self.addr, timeout=3.0)
        self.clients.append(c)
        if hello:
            c.hello(token)
        return c

    def test_hello_roles(self):
        c1, c2 = self.client(), self.client()
        self.assertEqual((c1.role, c2.role), (P.ROLE_CONTROLLER, P.ROLE_OBSERVER))
        self.assertEqual(c1.info["caps"], 0xF)
        self.assertEqual(c2.info["title_id"], "NPEA00050")
        # observers: frames, RAM_READ, STATUS, PING
        self.assertEqual(c2.status()["state"], P.ST_RUNNING)
        self.assertIsNotNone(c2.ram_read([(RAM, 4)])[0])
        c2.ping()
        self.assertEqual(c2.wait_frame(2).w, 64)
        # ... and nothing else
        for op in (c2.pause, c2.resume, lambda: c2.step(1), lambda: c2.video(64, 36, 30, 0),
                   lambda: c2.watch([(RAM, 4)]), lambda: c2.snap(0), lambda: c2.ram_write(RAM + 0x40, b"x"),
                   lambda: c2.exit()):
            with self.assertRaises(PitlinkError):
                op()
        seq = c2.pad(P.PadState(buttons=["cross"]))
        self.assertTrue(wait_until(lambda: any(n[0] == seq for n in c2.nacks)))
        self.assertEqual([n[1:3] for n in c2.nacks if n[0] == seq], [(P.PAD, -1)])
        self.assertEqual(self.car.pads, [])  # the observer's PAD never reached the pad
        # the wheel frees when the controller leaves; an observer re-HELLOs to take it
        c1.close()
        self.assertTrue(wait_until(lambda: self.car.controller is None))
        self.assertEqual(c2.hello()["role"], P.ROLE_CONTROLLER)
        c2.pause()

    def test_pre_hello_is_observer_and_gets_no_pushes(self):
        c = self.client(hello=False)
        self.assertEqual(c.status()["title_id"], "NPEA00050")
        with self.assertRaises(PitlinkError):
            c.pause()
        time.sleep(0.1)
        self.assertIsNone(c.latest_frame())

    def test_token(self):
        car = FakeCar(listen=(self.listen(),), fps=60, video=(64, 36, 30, 0), token="s3cret").start()
        try:
            bad = PitlinkClient(car.addrs[0], timeout=2.0)
            with self.assertRaises(PitlinkClosed):
                bad.hello("wrong")
            bad.close()
            rude = PitlinkClient(car.addrs[0], timeout=2.0)  # no HELLO first -> closed
            with self.assertRaises(PitlinkClosed):
                rude.status()
            rude.close()
            good = PitlinkClient(car.addrs[0], timeout=2.0)
            self.assertEqual(good.hello("s3cret")["role"], P.ROLE_CONTROLLER)
            self.assertEqual(good.status()["state"], P.ST_RUNNING)
            good.close()
        finally:
            car.stop()

    def test_frames_decode_to_their_flip(self):
        c = self.client()
        for codec in P.CODECS:
            c.video(64, 36, 60, codec, zstd_level=1)
            f = c.wait_frame(2)
            while f.codec != codec:
                f = c.wait_frame(2)
            px = f.bgra
            self.assertEqual(px.shape, (36, 64, 4))
            self.assertEqual(block_index(px), f.flip % cells(64, 36), f"codec {codec}")
            self.assertTrue((f.rgb() == px[..., [2, 1, 0]]).all())
            self.assertEqual(f.image().size, (64, 36))
            self.assertEqual(f.png()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertFalse(f.snap)
        self.assertGreater(c.latest_frame().fps, 10)

    def test_video_rate_and_off(self):
        c = self.client()
        c.video(64, 36, 30, 0)  # every 2nd flip at 60 fps
        f1 = c.wait_frame(2)
        f2 = c.wait_frame(2)
        self.assertEqual(f2.flip - f1.flip, 2)
        c.video(64, 36, 0, 0)
        time.sleep(0.05)
        with self.assertRaises(PitlinkError):
            c.wait_frame(0.2)
        with self.assertRaises(PitlinkError):
            c.video(64, 36, 30, 9)  # not a PLNK v1 codec (client refuses before the wire)

    def test_snap_native_frame(self):
        c = self.client()
        f = c.snap(P.CODECS[-1], timeout=2)
        self.assertTrue(f.snap)
        self.assertEqual((f.w, f.h, f.codec), (128, 72, P.CODECS[-1]))
        self.assertEqual(block_index(f.bgra), f.flip % cells(128, 72))

    def test_watch_rides_frames(self):
        c = self.client()
        c.watch([(RAM, 4), (RAM + 0xC, 4), (0x0, 2)])  # flip u32 · speed f32 · unmapped
        seen = 0
        for _ in range(12):
            f = c.wait_frame(2)
            if len(f.watch) != 10:
                continue  # a frame from before the WATCH ACK
            flip, speed, dead = c.watch_values(f, ["u", "f", "u"])
            self.assertEqual(flip, f.flip)  # sampled AT the flip the frame carries
            self.assertAlmostEqual(speed, (f.flip % 3000) / 10.0, places=4)
            self.assertEqual(f.watch[8:], b"\x00\x00")  # unreadable entry = zeros
            seen += 1
        self.assertGreaterEqual(seen, 8)
        ring = [w for fl, _, w in c.watch_rows() if len(w) == 10]
        self.assertTrue(all(int.from_bytes(w[:4], "big") == fl for (fl, _, w) in c.watch_rows() if len(w) == 10))
        self.assertTrue(ring)
        with self.assertRaises(PitlinkError):
            c.watch([(RAM, 3)])  # sizes are 1/2/4/8
        with self.assertRaises(PitlinkError):
            c.watch([(RAM, 4)] * 257)
        c.watch([])
        self.assertTrue(wait_until(lambda: c.latest_frame().watch == b""))

    def test_ram_read_write(self):
        c = self.client()
        c.ram_write(RAM + 0x40, b"PITLINK!")
        got = c.ram_read([(RAM + 0x40, 8), (0x0, 4), (RAM, 65537), (RAM + 0x40, 0)])
        self.assertEqual(got, [b"PITLINK!", None, None, b""])
        with self.assertRaises(PitlinkError):
            c.ram_write(0x0, b"\x01")
        with self.assertRaises(PitlinkError):
            c.ram_write(RAM + 0xFFFF, b"\x01\x02")

    def test_pad_at_flip_lockstep(self):
        c = self.client()
        c.watch([(RAM + 4, 2)])  # the car's mirror of port-0 buttons, per flip
        c.pause()
        f0 = c.status()["flip"]
        down = P.PadState(buttons=["cross"], ttl_ms=5000, at_flip=f0 + 3)
        c.pad(down)
        c.pad(P.PadState(ttl_ms=5000, at_flip=f0 + 5))
        self.assertTrue(wait_until(lambda: len(self.car.pads) == 2))
        self.assertEqual(self.car.pads[0]["raw"], down.pack())  # byte-exact on the wire
        ev = c.step(8, wait=True, timeout=3)
        self.assertEqual(ev.text, f"flip={f0 + 8}")
        hist = {fl: bits for fl, bits in self.car.hist() if f0 < fl <= f0 + 8}
        self.assertEqual([fl for fl, bits in sorted(hist.items()) if bits], [f0 + 3, f0 + 4])
        self.assertEqual(hist[f0 + 3], 1 << 14)
        # the stepped flip's frame arrives BEFORE EVENT 7, so it is already the latest
        self.assertEqual(c.latest_frame().flip, f0 + 8)
        for fl, _, w in c.watch_rows():
            if f0 < fl <= f0 + 8 and len(w) == 2:
                self.assertEqual(int.from_bytes(w, "big"), hist[fl], fl)

    def test_press_counts_flips_on_the_car(self):
        c = self.client()
        c.wait_frame(2)
        c.pause()  # lockstep: exact whatever the scheduler does
        r = c.press("cross", ms=100)
        self.assertEqual(r["flips"], c.frames_for(100))
        self.assertEqual(r["up_at"] - r["down_at"], r["flips"])
        c.step(r["flips"] + 6, wait=True, timeout=3)
        on = [fl for fl, bits in self.car.hist() if bits & (1 << 14)]
        self.assertEqual(on, list(range(r["down_at"], r["up_at"])))
        c.resume()  # and live, at 60 fps: the lead absorbs the link
        r = c.press(["circle", "square"], ms=150)
        self.assertTrue(wait_until(lambda: self.car.flip > r["up_at"] + 2, 3))
        on = [fl for fl, bits in self.car.hist() if bits & (1 << 13)]
        self.assertEqual(on, list(range(r["down_at"], r["up_at"])))
        self.assertTrue(all(bits & (1 << 15) for fl, bits in self.car.hist() if fl in on))

    def test_ttl_deadman(self):
        c = self.client()
        mark = c.event_mark
        c.pad(P.PadState(buttons=["cross"], lx=-0.5, ttl_ms=100))
        self.assertTrue(wait_until(lambda: "cross" in self.car.agent[0].buttons))
        ev = c.wait_event(P.EV_DEADMAN, timeout=2, after=mark)
        self.assertIn("port 0", ev.text)
        self.assertTrue(self.car.agent[0].is_neutral())
        # a lapse that changes nothing is silent
        mark = c.event_mark
        c.pad(P.PadState(ttl_ms=50))
        time.sleep(0.25)
        self.assertEqual([e for e in c.events_since(mark) if e.kind == P.EV_DEADMAN], [])
        # a pending at_flip PAD dies with the dead-man too
        c.pause()
        f0 = c.status()["flip"]
        c.pad(P.PadState(buttons=["start"], ttl_ms=80, at_flip=f0 + 2))
        c.wait_event(P.EV_DEADMAN, timeout=2)
        c.step(4, wait=True)
        self.assertFalse(any(bits for fl, bits in self.car.hist() if fl > f0))

    def test_hold_keepalive_then_dead_link(self):
        c, obs = self.client(), self.client()
        mark = obs.event_mark
        c.hold(P.PadState(buttons=["r1"], rx=0.5, ttl_ms=150))
        time.sleep(0.6)  # 4x the ttl: alive only because of the heartbeat
        self.assertIn("r1", self.car.agent[0].buttons)
        self.assertEqual([e for e in obs.events_since(mark) if e.kind == P.EV_DEADMAN], [])
        c.close()  # the host dies: heartbeat stops, the car's dead-man releases
        ev = obs.wait_event(P.EV_DEADMAN, timeout=2, after=mark)
        self.assertTrue(self.car.agent[0].is_neutral(), ev)

    def test_release_clears_schedule(self):
        c = self.client()
        c.pause()
        f0 = c.status()["flip"]
        c.pad(P.PadState(buttons=["cross"], ttl_ms=5000))
        c.pad(P.PadState(buttons=["circle"], ttl_ms=5000, at_flip=f0 + 2))
        self.assertTrue(wait_until(lambda: len(self.car.pads) == 2))
        c.release(0)
        self.assertTrue(wait_until(lambda: self.car.agent[0].is_neutral() and not self.car.pending))
        c.step(4, wait=True)
        self.assertFalse(any(bits for fl, bits in self.car.hist() if fl > f0))

    def test_pause_step_resume(self):
        c = self.client()
        mark = c.event_mark
        c.pause()
        st = c.status()
        self.assertEqual(st["state"], P.ST_PAUSED)
        time.sleep(0.1)
        self.assertEqual(c.status()["flip"], st["flip"])  # frozen
        ev = c.step(5, wait=True, timeout=3)
        self.assertEqual(ev.text, f"flip={st['flip'] + 5}")
        st2 = c.status()
        self.assertEqual((st2["state"], st2["flip"]), (P.ST_PAUSED, st["flip"] + 5))
        self.assertEqual(c.ping()[0], st["flip"] + 5)
        with self.assertRaises(PitlinkError):
            c.step(0)
        c.resume()
        self.assertEqual(c.status()["state"], P.ST_RUNNING)
        self.assertTrue(wait_until(lambda: c.status()["flip"] > st["flip"] + 5))
        kinds = [(e.kind, e.text) for e in c.events_since(mark)]
        self.assertEqual(kinds, [(P.EV_STATE, "paused"), (P.EV_STEP, f"flip={st['flip'] + 5}"),
                                 (P.EV_STATE, "running")])

    def test_paused_car_still_has_a_picture(self):
        c = self.client()
        c.video(64, 36, 20, 0)  # every 3rd flip
        c.wait_frame(2)
        c.pause()
        for n in (1, 2, 4):  # the flip that completes a STEP is always tapped
            ev = c.step(n, wait=True, timeout=3)
            self.assertEqual(f"flip={c.latest_frame().flip}", ev.text)
        late = self.client()  # joins while paused: HELLO brings the last frame along
        self.assertEqual(late.wait_frame(2, after_flip=0).flip, c.latest_frame().flip)

    def test_exit(self):
        c, obs = self.client(), self.client()
        mark = obs.event_mark
        c.exit(savestate=True)
        self.assertTrue(self.car.exited.wait(2))
        self.assertTrue(self.car.exit_savestate)
        ev = obs.wait_event(P.EV_STATE, timeout=2, after=mark)
        self.assertIn("exit", ev.text)
        self.assertTrue(wait_until(lambda: c.closed and obs.closed))
        with self.assertRaises(PitlinkClosed):
            c.status()

    def test_unknown_type_acks_error(self):
        c = self.client()
        with self.assertRaises(PitlinkError):
            c._request(0x0077, b"")


class TestLinkUnix(LinkBase, unittest.TestCase):
    def listen(self):
        return f"@etk-pitlink-test-{os.getpid()}-{next(_uniq)}"


class TestLinkTcp(LinkBase, unittest.TestCase):
    def listen(self):
        return "127.0.0.1:0"


# ===================================================================== [COND]
class _F:
    def __init__(self, flip, watch, fps=60.0):
        self.flip, self.watch, self.fps, self.vblank, self.w, self.h = flip, watch, fps, flip * 2, 64, 36


class TestCond(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(M.parse_cond("watch[0] > 100 and flip >= 0x10"),
                         [("watch", 0, ">", 100), ("flip", None, ">=", 16)])
        self.assertEqual(M.parse_cond("watchf[1]<=-2.5 && event[7]>=1"),
                         [("watchf", 1, "<=", -2.5), ("event", 7, ">=", 1)])
        for bad in ("__import__('os').system('x') > 1", "watch > 1", "flip[0] > 1", "flip >= 1 or flip < 0",
                    "speed > 3", "flip > x", "", "flip >"):
            with self.assertRaises(ValueError, msg=bad):
                M.parse_cond(bad)

    def test_eval(self):
        items = [(RAM, 4), (RAM + 4, 2), (RAM + 8, 4)]
        f = _F(500, (500).to_bytes(4, "big") + (0xFFFE).to_bytes(2, "big") + struct.pack(">f", 12.5))
        ev = [Event(7, "flip=500", 0.0)]
        ok, vals = M.holds(M.parse_cond("watch[0] == 500 and watchs[1] == -2 and watchf[2] > 12 and dflip >= 100 "
                                        "and event[7] >= 1"), f, items, 400, ev)
        self.assertTrue(ok, vals)
        ok, vals = M.holds(M.parse_cond("watch[0] > 500"), f, items, 0, [])
        self.assertFalse(ok)
        ok, vals = M.holds(M.parse_cond("watch[5] > 0"), f, items, 0, [])  # no such entry: false, not a crash
        self.assertEqual((ok, vals), (False, [None]))
        ok, _ = M.holds(M.parse_cond("watch[0] > 0"), _F(1, b"\x00"), items, 0, [])  # stale WATCH layout
        self.assertFalse(ok)
        ok, _ = M.holds(M.parse_cond("flip > 0"), None, items, 0, [])  # no frame yet
        self.assertFalse(ok)


# ===================================================================== [TOOLS]
class TestTools(unittest.TestCase):
    def setUp(self):
        self.car = FakeCar(listen=("127.0.0.1:0",), fps=60, video=(64, 36, 30, 0), ram=(RAM, 0x10000),
                           native=(128, 72)).start()
        self.env = dict(os.environ, PITLINK_ADDR=self.car.addrs[0], PITLINK_TOKEN="")

    def tearDown(self):
        self.car.stop()

    def cli(self, *args, ok=True):
        r = subprocess.run([sys.executable, os.path.join(HERE, "pitlink.py"), *args], env=self.env,
                           capture_output=True, text=True, timeout=30)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_cli(self):
        st = json.loads(self.cli("status").stdout)
        self.assertEqual((st["role"], st["title_id"], st["proto"]), ("controller", "NPEA00050", 1))
        self.assertEqual(st["frame"]["w"], 64)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "f.png")
            self.assertIn("flip=", self.cli("frame", out).stdout)
            with open(out, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
            self.cli("frame", out, "--snap")
            from PIL import Image
            with Image.open(out) as im:
                self.assertEqual(im.size, (128, 72))
        self.assertIn("flips", self.cli("press", "cross", "--ms", "50").stdout)
        self.assertTrue(wait_until(lambda: any(b & (1 << 14) for _, b in self.car.hist())))
        self.cli("ram", hex(RAM + 0x20), "16")
        r = self.cli("watch", hex(RAM) + ":4", "--secs", "0.3")
        rows = [ln.split("\t") for ln in r.stdout.splitlines()[1:]]
        self.assertTrue(rows and all(a == b for a, b in rows), r.stdout)  # WATCH(flip) == frame flip
        self.cli("hold", "lx=-0.3", "r2=0.6", "cross=1", "--ttl", "2000")
        self.assertTrue(wait_until(lambda: self.car.agent[0].pressure_of("r2") == 153))
        self.assertEqual(P.stick_byte(self.car.agent[0].lx), P.stick_byte(-0.3))
        self.cli("release")
        self.assertTrue(wait_until(lambda: self.car.agent[0].is_neutral()))
        self.assertIn("paused", self.cli("pause").stdout)
        self.assertIn("complete", self.cli("step", "3", "--wait").stdout)
        with tempfile.TemporaryDirectory() as d:  # paused: the HELLO-time frame still gives a picture
            self.assertIn(f"flip={self.car.flip} ", self.cli("frame", os.path.join(d, "p.png")).stdout)
        self.cli("resume")
        r = self.cli("--addr", "127.0.0.1:1", "status", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_mcp(self):
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "mcp_server.py")], env=self.env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ids = itertools.count(1)

        def rpc(method, params=None, notify=False):
            msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
            if not notify:
                msg["id"] = next(ids)
            p.stdin.write(json.dumps(msg) + "\n")
            p.stdin.flush()
            if notify:
                return None
            r = json.loads(p.stdout.readline())
            self.assertEqual(r["id"], msg["id"])
            return r

        def call(name, **args):
            r = rpc("tools/call", {"name": name, "arguments": args})["result"]
            return r, (json.loads(r["content"][-1]["text"]) if r["content"][-1]["text"][:1] in "[{" else
                       r["content"][-1]["text"])

        try:
            init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})["result"]
            self.assertEqual((init["protocolVersion"], init["serverInfo"]["name"]), ("2025-06-18", "pitlink"))
            self.assertIn("tools", init["capabilities"])
            rpc("notifications/initialized", notify=True)
            names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
            self.assertEqual(names, {"frame", "status", "press", "hold", "release", "ram_read", "watch", "pause",
                                     "resume", "step", "wait_for"})
            r, meta = call("frame", fresh=True)
            self.assertFalse(r["isError"], r)
            img = r["content"][0]
            self.assertEqual((img["type"], img["mimeType"]), ("image", "image/png"))
            self.assertEqual(base64.b64decode(img["data"])[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(meta["flip"], 0)
            r, st = call("status")
            self.assertEqual((st["role"], st["state"]), ("controller", "running"))
            r, w = call("watch", items=[hex(RAM) + ":4"])
            self.assertFalse(r["isError"], r)
            r, got = call("wait_for", condition="watch[0] >= 1 and dflip >= 4", timeout=3)
            self.assertTrue(got["met"], got)
            self.assertEqual(got["values"][0], got["frame"]["flip"])
            r, got = call("wait_for", condition="watch[0] == 0", timeout=0.2)
            self.assertFalse(got["met"])
            r, got = call("wait_for", condition="__import__('os')", timeout=0.2)
            self.assertTrue(r["isError"])
            r, got = call("press", buttons=["cross"], ms=60)
            self.assertFalse(r["isError"], r)
            self.assertTrue(wait_until(lambda: any(b & (1 << 14) for _, b in self.car.hist())))
            r, got = call("hold", controls={"r2": 0.6, "ly": -1})
            self.assertTrue(wait_until(lambda: self.car.agent[0].pressure_of("r2") == 153))
            time.sleep(0.7)  # past the car's 500 ms default ttl: the heartbeat keeps it
            self.assertEqual(self.car.agent[0].pressure_of("r2"), 153)
            call("release")
            self.assertTrue(wait_until(lambda: self.car.agent[0].is_neutral()))
            r, got = call("ram_read", items=[hex(RAM) + ":4"])
            self.assertEqual(got[0]["size"], 4)
            call("pause")
            r, got = call("step", n=3)
            self.assertFalse(r["isError"], r)
            self.assertEqual(got["frame"]["flip"], int(got["event"].split("=")[1]))
            r, got = call("wait_for", condition="event[7] >= 1", timeout=0.2)  # only NEW events count
            self.assertFalse(got["met"])
            call("resume")
            # a CLI alongside is an observer: it can look, it cannot drive
            self.assertEqual(json.loads(self.cli("status").stdout)["role"], "observer")
            r = self.cli("pause", ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("observer", r.stderr)
            r, got = call("press", buttons=["nosuch"])
            self.assertTrue(r["isError"])
            self.assertEqual(rpc("nosuch/method")["error"]["code"], -32601)
        finally:
            p.stdin.close()
            p.wait(timeout=10)
            p.stdout.close()
            p.stderr.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
