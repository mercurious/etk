#!/usr/bin/env python3
"""test_onboard -- the ONBOARD host viewer (WP-A), headless: no rig, no window, loopback only.

  [WIRE]    ONBD v1 pack/parse: byte-exact against the §8.3 table, partial input, bad magic/version
  [RING]    60 s window + byte cap, ring.N directories
  [ROLES]   GAME is an observer beside a controller; with no controller it refuses the wheel
  [WHEEL]   --wheel keeps a landed wheel as an idle controller (GAME shows, no control op sent)
  [POLICY]  PANEL by default, GAME when PANEL goes quiet (RUNNING, policy 0), back; p/g pins
  [STALE]   GAME flips stop for 2 s; PANEL seq stops for 2 intervals
  [FLUSH]   link death (PitlinkClosed / socket EOF) writes index.tsv, frames.bin, last.png, sheet.png
            with rows that decode back to the flip/seq they claim; H.264 stored raw
  [SNAP]    a PNG from either source (API and CLI); --help lists the flags
  [CTRL]    the CTRL sent at open carries --fps/--quality/--scale; the PANEL channel reconnects
  [END]     a car END is not a link death: no flush storm, 30 s reopen, ETK_ONBOARD=0 stops; one flush
            in flight; death flushes per source, rate-limited
  [CLI]     --panel never opens PLNK; CTRL carries only the keys the operator set
  [BROKER]  @...-onboard on usb_broker opens a car channel with target b"onboard" (fake USB pipe)
  [LAW]     onboard.py has no PAUSE / STEP / SNAP-request call (grep)

Run: python3 tools/onboard/test_onboard.py     (exit 0 = pass)
"""
import io
import itertools
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PITLINK = os.path.join(HERE, "..", "pitlink")
sys.path.insert(0, HERE)
sys.path.insert(0, PITLINK)
sys.path.insert(0, os.path.join(HERE, "..", "..", "bin"))
import onboard as O  # noqa: E402
import plnk as P  # noqa: E402
import plusb as L  # noqa: E402
from client import PitlinkClient  # noqa: E402
from fake_server import FakeCar, block_index, cells  # noqa: E402

_uniq = itertools.count()
GAME_W, GAME_H = 64, 36
PANEL_W, PANEL_H = 96, 64


def name(tag):
    return f"@onboard-test-{tag}-{os.getpid()}-{next(_uniq)}"


def wait_until(pred, timeout=3.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def panel_image(seq, w=PANEL_W, h=PANEL_H):
    import numpy as np
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 0] = (seq * 37) % 256
    img[..., 1] = 100
    img[..., 2] = 200
    img[..., 3] = 255
    return img


class FakePanel:
    """The car's ONBD producer (WP-B's job, faked): INFO first, then FRAMEs at `fps` while the mode
    allows (IDLE, or RUNNING with policy_fps > 0), an INFO after every mode change and every CTRL.
    serve(sock) is one channel; listen(addr) serves an abstract socket with it."""

    def __init__(self, fps=10, codecs=(O.CODEC_JPEG, O.CODEC_RAW, O.CODEC_ZSTD), end_reason=None):
        self.fps, self.codecs, self.end_reason = fps, list(codecs), end_reason
        self.mode, self.policy_fps, self.frames_on = "IDLE", 0, True
        self.ctrls, self.conns, self.live = [], 0, []
        self.seq, self.sent = 0, []
        self.version = 0
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.srv = None

    def listen(self, addr):
        self.addr = addr
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind("\0" + addr[1:])
        self.srv.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def _accept(self):
        while not self.stop_ev.is_set():
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def info(self):
        eff = self.fps if self.mode == "IDLE" else self.policy_fps
        return {"backend": "jpeg-grim", "encoder": "fake", "mode": self.mode, "fps": eff, "quality": 70,
                "scale": 0.5, "transform": 90, "panel": [1080, 1920], "policy_fps": self.policy_fps,
                "pinned": False, "daemon": "test"}

    def set_mode(self, mode, policy_fps=0):
        with self.lock:
            self.mode, self.policy_fps = mode, policy_fps
            self.version += 1

    def drop(self):
        with self.lock:
            socks, self.live = self.live, []
        for s in socks:
            L._hangup(s)

    def stop(self):
        self.stop_ev.set()
        if self.srv:
            L._hangup(self.srv)
        self.drop()

    def _reader(self, s, ctrl_ev):
        buf = b""
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    return
                buf += b
                while True:
                    r = O.parse_msg(buf)
                    if r is None:
                        break
                    m, n = r
                    buf = buf[n:]
                    if m.kind == O.CTRL:
                        with self.lock:
                            self.ctrls.append(json.loads(m.payload))
                        ctrl_ev.set()
        except OSError:
            return

    def frame_bytes(self, seq):
        codec = self.codecs[seq % len(self.codecs)]
        img = panel_image(seq)
        if codec == O.CODEC_RAW:
            data = img.tobytes()
        elif codec == O.CODEC_ZSTD:
            data = P.zstd_compress(img.tobytes(), 1)
        elif codec == O.CODEC_JPEG:
            from PIL import Image
            b = io.BytesIO()
            Image.fromarray(img[..., 2::-1].copy(), "RGB").save(b, "JPEG", quality=70)
            data = b.getvalue()
        else:  # H.264: an Annex-B access unit as far as the wire knows (not decodable: the host stores it)
            data = b"\x00\x00\x00\x01\x09\xf0\x00\x00\x00\x01\x65" + bytes([seq & 0xFF]) * 40
        return codec, data

    def serve(self, s):
        with self.lock:
            self.conns += 1
            self.live.append(s)
        ctrl_ev = threading.Event()
        threading.Thread(target=self._reader, args=(s, ctrl_ev), daemon=True).start()
        seen = None
        try:
            s.sendall(O.pack_json(O.INFO, self.info()))
            if self.end_reason is not None:  # capture refused / failed at once: INFO, END, close
                s.sendall(O.pack_msg(O.END, self.end_reason.encode()))
                time.sleep(0.05)
                return
            nxt = time.monotonic()
            while not self.stop_ev.is_set():
                if ctrl_ev.is_set() or seen != self.version:
                    ctrl_ev.clear()
                    seen = self.version
                    s.sendall(O.pack_json(O.INFO, self.info()))
                eff = self.info()["fps"]
                if self.frames_on and eff:
                    with self.lock:
                        self.seq += 1
                        seq = self.seq
                    codec, data = self.frame_bytes(seq)
                    s.sendall(O.pack_msg(O.FRAME, data, codec=codec, flags=O.FLAG_KEY if codec == 3 else 0,
                                         w=PANEL_W, h=PANEL_H, seq=seq, car_ns=time.monotonic_ns()))
                    with self.lock:
                        self.sent.append((seq, codec))
                    nxt += 1.0 / eff
                    time.sleep(max(0.0, nxt - time.monotonic()))
                else:
                    nxt = time.monotonic()
                    time.sleep(0.02)
        except OSError:
            pass
        finally:
            L._hangup(s)


class Rig:
    """A fake car (GAME, 60 fps) with a controller already holding the wheel + a fake PANEL producer."""

    def __init__(self, tc, game=True, panel=True, controller=True, panel_kw=None, **ob_kw):
        self.tmp = tempfile.mkdtemp(prefix="onboard-test-")
        tc.addCleanup(shutil.rmtree, self.tmp, True)
        self.car = self.ctl = self.panel = None
        if game:
            self.car = FakeCar(listen=(name("car"),), fps=60, video=(GAME_W, GAME_H, 60, P.CODEC_RAW),
                               native=(128, 72)).start()
            tc.addCleanup(self.car.stop)
            if controller:
                self.ctl = PitlinkClient(self.car.addrs[0], timeout=3)
                self.ctl.hello()
                assert self.ctl.controller
                tc.addCleanup(self.ctl.close)
        self.panel_addr = name("panel")
        if panel:
            self.panel = FakePanel(**(panel_kw or {})).listen(self.panel_addr)
            tc.addCleanup(self.panel.stop)
        kw = dict(game_addr=self.car.addrs[0] if self.car else name("nocar"), panel_addr=self.panel_addr,
                  ring_base=self.tmp, ctrl={"fps": 10, "quality": 55, "scale": 0.25}, timeout=3.0)
        kw.update(ob_kw)
        self.ob = O.Onboard(**kw).start()
        tc.addCleanup(self.ob.stop)


def img_size(path):
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        return im.size


def read_text(path):
    with open(path) as f:
        return f.read()


def read_index(path):
    with open(os.path.join(path, "index.tsv")) as f:
        rows = [ln.rstrip("\n").split("\t") for ln in f]
    head = rows[0]
    return head, [dict(zip(head, r)) for r in rows[1:]]


# ============================================================================ [WIRE]
class TestWire(unittest.TestCase):
    def test_header_bytes_match_the_spec_table(self):
        b = O.pack_msg(O.FRAME, b"abc", codec=2, flags=1, w=960, h=540, seq=7, car_ns=0x0102030405060708)
        want = (b"ONBD" + bytes([1, 1, 2, 1]) + (960).to_bytes(2, "little") + (540).to_bytes(2, "little")
                + (7).to_bytes(4, "little") + (0x0102030405060708).to_bytes(8, "little") + (3).to_bytes(4, "little")
                + b"abc")
        self.assertEqual(b, want)
        self.assertEqual((O.HDR.size, O.FIXED), (24, 28))

    def test_roundtrip_every_kind(self):
        for kind, payload, kw in ((O.INFO, b'{"mode":"IDLE"}', {}),
                                  (O.FRAME, os.urandom(1000), dict(codec=3, flags=3, w=1920, h=1080, seq=2 ** 32 - 1,
                                                                   car_ns=2 ** 64 - 1)),
                                  (O.CTRL, b'{"fps":5}', {}), (O.END, "policy".encode(), {})):
            m, n = O.parse_msg(O.pack_msg(kind, payload, **kw))
            self.assertEqual((m.kind, m.payload, m.ver), (kind, payload, 1))
            for k, v in kw.items():
                self.assertEqual(getattr(m, k), v, k)

    def test_stream_parse_partial_and_offset(self):
        wire = O.pack_ctrl({"fps": 3}) + O.pack_msg(O.END, b"bye")
        self.assertIsNone(O.parse_msg(wire[:27]))
        self.assertIsNone(O.parse_msg(wire[:30]))
        m1, n1 = O.parse_msg(wire)
        m2, n2 = O.parse_msg(wire, n1)
        self.assertEqual((json.loads(m1.payload), m2.payload, n2), ({"fps": 3}, b"bye", len(wire)))

    def test_bad_magic_and_version_rejected(self):
        good = O.pack_msg(O.INFO, b"{}")
        with self.assertRaises(O.OnbdError):
            O.parse_msg(b"ONBX" + good[4:])
        with self.assertRaises(O.OnbdError):
            O.parse_msg(good[:4] + b"\x02" + good[5:])


# ============================================================================ [RING]
def rec(t, n=100, source="game", i=0):
    return O.Rec(source, b"x" * n, 1, 1, O.CODEC_RAW, flip=i, recv_t=t, recv_epoch=1e9 + t)


class TestRing(unittest.TestCase):
    def test_holds_at_most_60_s(self):
        r = O.Ring(60.0)
        for i in range(0, 900):
            r.add(rec(i / 10.0, i=i))  # 90 s at 10 Hz
        snap = r.snapshot()
        self.assertLessEqual(snap[-1].recv_t - snap[0].recv_t, 60.0)
        self.assertGreaterEqual(snap[-1].recv_t - snap[0].recv_t, 59.8)
        self.assertEqual(snap[-1].flip, 899)
        self.assertEqual(len(r.snapshot(now=1000.0)), 0)  # all older than 60 s by then

    def test_byte_cap(self):
        r = O.Ring(60.0, max_bytes=1000)
        for i in range(50):
            r.add(rec(i * 0.01, n=100, i=i))
        self.assertLessEqual(r.nbytes, 1000)
        self.assertEqual([x.flip for x in r.snapshot()], list(range(40, 50)))

    def test_ring_dirs_never_overwrite(self):
        base = tempfile.mkdtemp(prefix="onboard-ringdir-")
        self.addCleanup(shutil.rmtree, base, True)
        self.assertEqual([os.path.basename(O.ring_dir(base)) for _ in range(3)], ["ring", "ring.2", "ring.3"])


# ============================================================================ [ROLES]
class TestRoles(unittest.TestCase):
    def test_observer_beside_a_controller(self):
        rig = Rig(self, panel=False, want_panel=False)
        self.assertTrue(wait_until(lambda: rig.ob.game.frames > 5))
        self.assertEqual(rig.ob.game.link, "observer")
        self.assertFalse(rig.ob._game_client.controller)
        self.assertEqual(rig.car.controller.role, P.ROLE_CONTROLLER)
        self.assertEqual(rig.ob.status()["title_id"], "NPEA00050")

    def test_refuses_the_wheel_when_nobody_holds_it(self):
        rig = Rig(self, panel=False, controller=False, want_panel=False)
        self.assertTrue(wait_until(lambda: "refused the wheel" in rig.ob.game.link))
        self.assertTrue(wait_until(lambda: rig.car.controller is None))
        hunt = PitlinkClient(rig.car.addrs[0], timeout=3)  # the hunt arrives: the wheel is free for it
        self.addCleanup(hunt.close)
        hunt.hello()
        self.assertTrue(hunt.controller)
        self.assertIsNone(rig.ob._game_client)


class TestWheel(unittest.TestCase):
    """--wheel (field finding, car8 with no driver): keep the wheel the HELLO lands, idle."""

    def test_wheel_keeps_it_and_shows_game(self):
        rig = Rig(self, panel=False, controller=False, want_panel=False, wheel=True)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.game.frames > 10, 3.0), ob.game.link)
        self.assertTrue(ob._game_client.controller)
        self.assertEqual(ob.game.link, O.WHEEL_LINK)
        self.assertIn("holding the wheel (--wheel): a hunt cannot start until this window quits", ob.status_line())
        self.assertEqual(ob.active(), "game")
        rec, rgb, note = ob.frame_for("game")
        self.assertEqual((rgb.shape, note), ((GAME_H, GAME_W, 3), None))
        self.assertEqual(rig.car.pads, [])  # idle: no PAD, no control op ever reached the car
        self.assertFalse(rig.car.paused)
        hunt = PitlinkClient(rig.car.addrs[0], timeout=3)
        self.addCleanup(hunt.close)
        hunt.hello()
        self.assertFalse(hunt.controller)  # exactly what the status bar warns about

    def test_wheel_still_observer_beside_a_controller(self):
        rig = Rig(self, panel=False, want_panel=False, wheel=True)
        self.assertTrue(wait_until(lambda: rig.ob.game.frames > 5))
        self.assertEqual(rig.ob.game.link, "observer")
        self.assertFalse(rig.ob._game_client.controller)

    def test_cli_wheel_snap(self):
        rig = Rig(self, panel=False, controller=False, want_panel=False)  # the default viewer refuses meanwhile
        out = os.path.join(rig.tmp, "w.png")
        p = subprocess.run([sys.executable, os.path.join(HERE, "onboard.py"), "--snap", out, "--game", "--wheel",
                            "--addr", rig.car.addrs[0], "--ring", rig.tmp, "--timeout", "5"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(img_size(out), (GAME_W, GAME_H))
        p = subprocess.run([sys.executable, os.path.join(HERE, "onboard.py"), "--snap", out, "--panel", "--wheel"],
                           capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)


# ============================================================================ [POLICY]
class TestPolicy(unittest.TestCase):
    def test_auto_switch_game_and_back(self):
        rig = Rig(self)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 2 and ob.game.frames > 5))
        self.assertEqual(ob.active(), "panel")  # both live: PANEL is the default
        rig.panel.set_mode("RUNNING", policy_fps=0)  # the car starts a game: PANEL goes quiet
        self.assertTrue(wait_until(lambda: ob.active() == "game", 2.0), ob.status())
        st = ob.status()
        self.assertTrue(st["panel_off"])
        self.assertFalse(st["stale"])
        n = rig.panel.seq
        time.sleep(0.3)
        self.assertEqual(rig.panel.seq, n)  # policy 0: no frames
        rig.panel.set_mode("IDLE")  # back at ES
        self.assertTrue(wait_until(lambda: ob.active() == "panel", 2.0), ob.status())

    def test_panel_link_gone_falls_back_to_game(self):
        rig = Rig(self)
        self.assertTrue(wait_until(lambda: rig.ob.active() == "panel" and rig.ob.game.frames > 2))
        rig.panel.stop()
        self.assertTrue(wait_until(lambda: rig.ob.active() == "game", 4.0), rig.ob.status())

    def test_pins(self):
        rig = Rig(self)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 2 and ob.game.frames > 2))
        ob.pin("game")
        self.assertEqual(ob.active(), "game")
        self.assertTrue(ob.status_line().startswith("GAME*"))
        rig.panel.set_mode("RUNNING")
        ob.pin("panel")
        self.assertEqual(ob.active(), "panel")
        ob.pin(None)
        self.assertTrue(wait_until(lambda: ob.active() == "game"))


# ============================================================================ [STALE]
class TestStale(unittest.TestCase):
    def test_game_stale_when_flips_stop_2s(self):
        rig = Rig(self, panel=False, want_panel=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.game.frames > 5))
        self.assertFalse(ob.stale("game"))
        with rig.car.lock:
            rig.car.paused = True  # the fake car stops flipping (test-side; ONBOARD never asks for this)
        t0 = time.monotonic()
        time.sleep(1.5)
        self.assertFalse(ob.stale("game"), "fired before 2 s")
        self.assertTrue(wait_until(lambda: ob.stale("game"), 2.0))
        self.assertLess(time.monotonic() - t0, 3.2)
        self.assertIn("STALE", ob.status_line())
        with rig.car.lock:
            rig.car.paused = False
        self.assertTrue(wait_until(lambda: not ob.stale("game"), 2.0))

    def test_panel_stale_after_two_intervals(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 4))
        self.assertFalse(ob.stale("panel"))
        rig.panel.frames_on = False  # capture wedged; the channel stays open, INFO still says 10 fps
        t0 = time.monotonic()
        self.assertTrue(wait_until(lambda: ob.stale("panel"), 2.0))
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertIn("STALE", ob.status_line())


# ============================================================================ [FLUSH]
class TestFlush(unittest.TestCase):
    def check_ring(self, path, sources):
        for f in ("index.tsv", "frames.bin", "last.png", "sheet.png", "meta.json"):
            self.assertTrue(os.path.isfile(os.path.join(path, f)), f)
        head, rows = read_index(path)
        for col in ("source", "flip", "vblank", "host_ns", "recv_t", "seq", "offset", "len", "w", "h", "codec"):
            self.assertIn(col, head)
        with open(os.path.join(path, "frames.bin"), "rb") as f:
            blob = f.read()
        off = 0
        for r in rows:
            self.assertEqual(int(r["offset"]), off)
            off += int(r["len"])
        self.assertEqual(off, len(blob))
        self.assertEqual({r["source"] for r in rows}, set(sources))
        self.assertGreater(img_size(os.path.join(path, "last.png"))[0], 0)
        self.assertEqual(img_size(os.path.join(path, "sheet.png")), (4 * O.SHEET_TILE[0], 4 * (O.SHEET_TILE[1] + 14)))
        return rows, blob

    def test_game_link_death_flushes_rows_that_decode_to_their_flip(self):
        rig = Rig(self)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.game.frames > 30 and ob.panel.frames > 4))
        rig.car.stop()  # the car dies: PitlinkClosed on the observer link
        self.assertTrue(wait_until(lambda: ob.flushes, 3.0))
        path, reason = ob.flushes[0]
        self.assertEqual(path, os.path.join(rig.tmp, "ring"))
        self.assertIn("game link died", reason)
        self.assertTrue(ob.disk.wait(10))
        rows, blob = self.check_ring(path, {"game", "panel"})
        game = [r for r in rows if r["source"] == "game"]
        self.assertGreater(len(game), 30)
        import numpy as np
        for r in game:
            for col in ("flip", "vblank", "host_ns", "recv_t"):
                self.assertNotEqual(r[col], "-", col)
            o, n = int(r["offset"]), int(r["len"])
            bgra = np.frombuffer(blob[o:o + n], np.uint8).reshape(GAME_H, GAME_W, 4)
            self.assertEqual(block_index(bgra), int(r["flip"]) % cells(GAME_W, GAME_H), r)
        flips = [int(r["flip"]) for r in game]
        self.assertEqual(flips, sorted(flips))
        panel = [r for r in rows if r["source"] == "panel"]
        self.assertEqual([int(r["seq"]) for r in panel], sorted(int(r["seq"]) for r in panel))
        self.assertEqual({r["codec"] for r in panel}, {"jpeg", "raw", "zstd"})
        meta = json.loads(read_text(os.path.join(path, "meta.json")))
        self.assertEqual(meta["frames"]["game"], len(game))
        self.assertGreater(meta["duration_s"], 0)

    def test_panel_eof_flushes(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 5))
        rig.panel.drop()  # socket EOF (broker gone / car daemon stopped)
        self.assertTrue(wait_until(lambda: ob.flushes, 3.0))
        self.assertTrue(ob.disk.wait(10))
        rows, _ = self.check_ring(ob.flushes[0][0], {"panel"})
        self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(wait_until(lambda: rig.panel.conns >= 2, 3.0), "PANEL never reconnected")

    def test_h264_stored_raw_pictures_from_last_decodable(self):
        rig = Rig(self, game=False, want_game=False, panel_kw={"codecs": (O.CODEC_JPEG, O.CODEC_H264)})
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 6))
        self.assertTrue(wait_until(lambda: ob.panel.latest.codec == O.CODEC_H264, 2.0))
        rec, rgb, note = ob.frame_for("panel")
        self.assertEqual(note, "H.264 (needs --gst)")
        self.assertIsNotNone(rgb)  # the last decodable (JPEG) picture stands in
        path = ob.flush("test")
        self.assertTrue(ob.disk.wait(10))
        rows, blob = self.check_ring(path, {"panel"})
        h264 = [r for r in rows if r["codec"] == "h264"]
        self.assertTrue(h264)
        o = int(h264[0]["offset"])
        self.assertEqual(blob[o:o + 4], b"\x00\x00\x00\x01")

    def test_record_writes_ring_then_live(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 3))
        path = ob.record(True)
        n0 = ob.panel.frames
        self.assertTrue(wait_until(lambda: ob.panel.frames > n0 + 3))
        self.assertEqual(ob.record(False), path)
        self.assertTrue(ob.disk.wait(10))
        rows, _ = self.check_ring(path, {"panel"})
        self.assertGreaterEqual(len(rows), n0 + 3)

    def test_flush_does_not_block_the_receiver(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 3))
        gate = threading.Event()
        ob.disk.submit(gate.wait, 5)  # the disk is stuck
        t0 = time.monotonic()
        ob.flush("slow disk")
        self.assertLess(time.monotonic() - t0, 0.2)
        n = ob.panel.frames
        self.assertTrue(wait_until(lambda: ob.panel.frames > n + 2, 2.0))
        self.assertIsNone(ob.flush("second while the first is queued"))  # one in flight, never two
        gate.set()
        self.assertTrue(ob.disk.wait(10))
        self.assertEqual(len(ob.flushes), 1)
        self.assertIsNotNone(ob.flush("after"))  # the slot is free again


# ============================================================================ [END] (review finding 1)
def ring_dirs(base):
    return sorted(d for d in os.listdir(base) if d == "ring" or d.startswith("ring."))


class TestEnd(unittest.TestCase):
    def test_end_only_producer_beside_streaming_game_never_storms(self):
        """The car answers every OPEN with INFO+END (grim failing) while GAME streams at 60 fps:
        END is not a link death -- no flush, no fast reopen, nothing piles up in memory."""
        rig = Rig(self, panel_kw={"end_reason": "grim failed: exit 1"})
        ob = rig.ob
        peak = 0
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end:
            peak = max(peak, ob.disk.q.unfinished_tasks)
            time.sleep(0.02)
        self.assertGreater(ob.game.frames, 100)
        self.assertLessEqual(len(ring_dirs(rig.tmp)), 1, ring_dirs(rig.tmp))
        self.assertEqual(ob.flushes, [])
        self.assertLessEqual(peak, 1)
        self.assertEqual(rig.panel.conns, 1, "reopened before END_RETRY_S")
        self.assertIn("ended by the car", ob.panel.link)

    def test_onboard_off_end_stops_retrying(self):
        rig = Rig(self, game=False, want_game=False, panel_kw={"end_reason": "ETK_ONBOARD=0"})
        ob = rig.ob
        self.assertTrue(wait_until(lambda: "off on the car" in ob.panel.link, 3.0), ob.panel.link)
        self.assertTrue(wait_until(lambda: not any(t.is_alive() for t in ob.threads), 2.0))
        self.assertEqual((rig.panel.conns, ob.flushes), (1, []))

    def test_end_after_frames_is_not_a_flush(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        self.assertTrue(wait_until(lambda: ob.panel.frames > 3))
        rig.panel.end_reason = "policy"
        rig.panel.drop()  # the next OPEN is ENDed; this drop itself is a socket EOF after frames
        self.assertTrue(wait_until(lambda: "ended by the car" in ob.panel.link, 5.0), ob.panel.link)
        self.assertTrue(ob.disk.wait(10))
        self.assertEqual(len(ob.flushes), 1)  # the EOF after frames flushed; the END did not

    def test_panel_death_flush_is_rate_limited(self):
        rig = Rig(self, game=False, want_game=False)
        ob = rig.ob
        for _ in range(3):
            n = ob.panel.frames
            self.assertTrue(wait_until(lambda: ob.panel.frames > n + 2, 4.0))
            rig.panel.drop()
        self.assertTrue(ob.disk.wait(10))
        self.assertEqual(len(ob.flushes), 1, ob.flushes)


# ============================================================================ [SNAP]
class TestSnap(unittest.TestCase):
    def test_snap_from_game(self):
        rig = Rig(self, pin="game")
        out = os.path.join(rig.tmp, "g.png")
        src, r = rig.ob.write_still(out, timeout=5)
        self.assertEqual((src, img_size(out)), ("game", (GAME_W, GAME_H)))

    def test_snap_from_panel(self):
        rig = Rig(self, pin="panel")
        out = os.path.join(rig.tmp, "p.png")
        src, r = rig.ob.write_still(out, timeout=5)
        self.assertEqual((src, img_size(out)), ("panel", (PANEL_W, PANEL_H)))

    def test_cli_snap_both_sources(self):
        rig = Rig(self)
        for flag, size in (("--game", (GAME_W, GAME_H)), ("--panel", (PANEL_W, PANEL_H))):
            out = os.path.join(rig.tmp, f"cli{flag}.png")
            p = subprocess.run([sys.executable, os.path.join(HERE, "onboard.py"), "--snap", out, flag,
                                "--addr", rig.car.addrs[0], "--onboard-addr", rig.panel_addr, "--ring", rig.tmp,
                                "--timeout", "5"], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(img_size(out), size, flag)
        self.assertIsNotNone(rig.car.controller)  # the controller still holds the wheel
        self.assertTrue(rig.ctl.controller)

    def test_help_lists_the_flags(self):
        p = subprocess.run([sys.executable, os.path.join(HERE, "onboard.py"), "--help"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        for flag in ("--game", "--panel", "--ring", "--record", "--snap", "--addr", "--fps", "--quality",
                     "--scale", "--gst"):
            self.assertIn(flag, p.stdout)


# ============================================================================ [CTRL]
class TestCtrl(unittest.TestCase):
    def test_ctrl_at_open_carries_the_cli_values(self):
        rig = Rig(self, game=False, want_game=False)
        self.assertTrue(wait_until(lambda: rig.panel.ctrls))
        self.assertEqual(rig.panel.ctrls[0], {"fps": 10, "quality": 55, "scale": 0.25})
        self.assertTrue(rig.ob.send_ctrl({"pin": False}))
        self.assertTrue(wait_until(lambda: len(rig.panel.ctrls) == 2))


class TestCli(unittest.TestCase):
    """Review findings 2 + 3: --panel never opens PLNK; CTRL carries only what the operator set."""

    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "onboard.py"), *args], capture_output=True,
                              text=True, timeout=30)

    def test_panel_never_opens_plnk(self):
        accepts = []
        car = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # a "car" that only counts connections
        addr = name("counting-car")
        car.bind("\0" + addr[1:])
        car.listen(8)
        self.addCleanup(car.close)

        def acc():
            while True:
                try:
                    c, _ = car.accept()
                except OSError:
                    return
                accepts.append(1)
                c.close()
        threading.Thread(target=acc, daemon=True).start()
        panel = FakePanel().listen(name("panel"))
        self.addCleanup(panel.stop)
        tmp = tempfile.mkdtemp(prefix="onboard-cli-")
        self.addCleanup(shutil.rmtree, tmp, True)
        p = self.run_cli("--panel", "--snap", os.path.join(tmp, "p.png"), "--addr", addr, "--onboard-addr",
                         panel.addr, "--ring", tmp, "--timeout", "5")
        self.assertEqual(p.returncode, 0, p.stderr)
        time.sleep(0.2)
        self.assertEqual(accepts, [], "--panel opened the PLNK socket")

    def test_ctrl_carries_only_the_keys_set(self):
        panel = FakePanel().listen(name("panel"))
        self.addCleanup(panel.stop)
        tmp = tempfile.mkdtemp(prefix="onboard-cli-")
        self.addCleanup(shutil.rmtree, tmp, True)
        common = ("--panel", "--onboard-addr", panel.addr, "--ring", tmp, "--timeout", "5")
        p = self.run_cli("--snap", os.path.join(tmp, "a.png"), *common)
        self.assertEqual(p.returncode, 0, p.stderr)
        time.sleep(0.2)
        self.assertEqual(panel.ctrls, [])
        p = self.run_cli("--snap", os.path.join(tmp, "b.png"), "--quality", "40", *common)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(wait_until(lambda: panel.ctrls, 2.0))
        self.assertEqual(panel.ctrls, [{"quality": 40}])


# ============================================================================ [BROKER]
class TestBroker(unittest.TestCase):
    def test_default_socket_names(self):
        import usb_broker
        self.assertEqual((L.ONBOARD_SOCKET, L.TARGET_ONBOARD), ("@etk-onboard-usb", b"onboard"))
        self.assertEqual(usb_broker.Broker().onboard_name, "@etk-onboard-usb")
        self.assertEqual(usb_broker.Broker(sock_name="@x").onboard_name, "@x-onboard")
        self.assertEqual(usb_broker.Broker(sock_name="@x", onboard_name="@y").onboard_name, "@y")

    def test_onboard_socket_opens_the_onboard_target_over_usb(self):
        import usb_broker
        from test_plusb import FakeUsbPipe
        car = FakeCar(listen=(name("car"),), fps=60, video=(GAME_W, GAME_H, 60, P.CODEC_RAW)).start()
        self.addCleanup(car.stop)
        panel = FakePanel()
        self.addCleanup(panel.stop)
        targets = []

        def onboard_service(sock):
            targets.append("onboard")
            panel.serve(sock)
        relay = L.CarRelay(target=car.addrs[0], status=lambda: "fake car", services={"onboard": onboard_service})
        pipes = []

        def open_pipe():
            pipes.append(FakeUsbPipe(relay))
            return pipes[-1]
        sock = name("broker")
        broker = usb_broker.Broker(open_pipe=open_pipe, sock_name=sock, garage_name=name("garage"))
        threading.Thread(target=broker.serve, daemon=True).start()
        self.addCleanup(lambda: [broker.shutdown()] + [p.close() for p in pipes])
        self.assertTrue(broker.mux.up.wait(3))
        ctl = PitlinkClient(sock, timeout=3)
        self.addCleanup(ctl.close)
        ctl.hello()
        tmp = tempfile.mkdtemp(prefix="onboard-broker-")
        self.addCleanup(shutil.rmtree, tmp, True)
        ob = O.Onboard(game_addr=sock, panel_addr=broker.onboard_name, ring_base=tmp, timeout=3).start()
        self.addCleanup(ob.stop)
        self.assertTrue(wait_until(lambda: ob.panel.frames > 2 and ob.game.frames > 2, 5), ob.status())
        self.assertEqual(targets, ["onboard"])
        self.assertEqual(panel.ctrls, [])  # nothing set by the operator: no CTRL, the car's knobs rule
        self.assertFalse(ob._game_client.controller)
        pipes[-1].close()  # yank the cable: both links die, one flush
        self.assertTrue(wait_until(lambda: ob.flushes, 4))
        self.assertTrue(ob.disk.wait(10))
        _, rows = read_index(ob.flushes[0][0])
        self.assertEqual({r["source"] for r in rows}, {"game", "panel"})
        time.sleep(0.5)
        self.assertEqual(len(ob.flushes), 1, ob.flushes)  # one cable, one flush


# ============================================================================ [GST] (optional)
def _gst_h264_aus(n=12, w=160, h=96):
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        if not all(Gst.ElementFactory.find(e) for e in ("openh264enc", "openh264dec", "h264parse")):
            return None
        pipe = Gst.parse_launch(f"videotestsrc num-buffers={n} ! video/x-raw,width={w},height={h},framerate=30/1 ! "
                                "openh264enc ! h264parse ! video/x-h264,stream-format=byte-stream,alignment=au ! "
                                "appsink name=sink sync=false")
    except Exception:
        return None
    sink = pipe.get_by_name("sink")
    pipe.set_state(Gst.State.PLAYING)
    aus = []
    while True:
        s = sink.emit("try-pull-sample", 2 * 1000 * 1000 * 1000)
        if s is None:
            break
        b = s.get_buffer()
        aus.append(b.extract_dup(0, b.get_size()))
    pipe.set_state(Gst.State.NULL)
    return aus


class TestGst(unittest.TestCase):
    def test_h264_decodes_with_gst(self):
        aus = _gst_h264_aus()
        if not aus:
            self.skipTest("GStreamer openh264 not on this host (--gst is optional)")
        dec = O.GstH264()
        self.addCleanup(dec.close)
        for au in aus:
            dec.push(au)
        self.assertTrue(wait_until(lambda: dec.latest() is not None, 5))
        self.assertEqual(dec.latest().shape, (96, 160, 3))


# ============================================================================ [LAW]
class TestObserverOnly(unittest.TestCase):
    def test_no_pause_step_or_snap_request_in_onboard(self):
        src = read_text(os.path.join(HERE, "onboard.py"))
        self.assertEqual(re.findall(r"(?i)pause|step", src), [])
        self.assertEqual(re.findall(r"\.snap\(|P\.SNAP|\bPAD\b|\.press\(|\.hold\(|\.video\(|(?<!sys)\.exit\(", src), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
