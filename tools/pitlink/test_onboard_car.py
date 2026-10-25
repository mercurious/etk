#!/usr/bin/env python3
"""test_onboard_car -- the car's half of ONBOARD (ONBD v1 over PLUSB target "onboard"), no hardware.

The daemon's own Daemon object is built with its USB endpoints stubbed, so the relay under test
is exactly the one `serve` runs (garage + onboard services). A fake host feeds HELLO / OPEN /
DATA / CLOSE segments into it and drains its IN queue; a fake `grim` on PATH stands in for the
compositor (it logs its argv + env and pid, writes a fixed JPEG, and can be slow, fail, or print
nothing); a temp SHM dir carries active_id.txt / etk_mode.txt; a temp etk.conf carries the knobs.
The real bin/onboard_cap.sh runs between the daemon and the fake grim.

  [WIRE]    pack_msg / parse_msg (the 24 + 4 byte ONBD v1 header), jpeg_size
  [STREAM]  OPEN -> INFO then FRAMEs at the requested rate; CTRL fps/quality/scale/snap/pin, junk
  [POLICY]  RUNNING + ETK_ONBOARD_RUNNING_FPS=0 -> INFO only, no capture process; pin; mode flips
  [STOP]    CLOSE / link loss kill a capture in flight within 1 s; grim failure -> END; kill-switch
  [LINK]    a stalled host gets no backlog: capture waits for the link, seq jumps over the gap
  [CAP]     bin/onboard_cap.sh: the grim argv, the Wayland env, argument validation

Run:  python3 tools/pitlink/test_onboard_car.py                 (this tree: must PASS)
      python3 tools/pitlink/test_onboard_car.py --against main  (a pre-ONBOARD tree: must FAIL)
"""
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DAEMON_DIR = os.environ.get("ONBOARD_DAEMON_DIR") or os.path.join(ROOT, "bin")
sys.path.insert(0, HERE)
sys.path.insert(0, DAEMON_DIR)


def against(rev):
    """Run this file against `rev`'s daemon + capture script (+ its plusb.py)."""
    tmp = tempfile.mkdtemp(prefix="onboard-against-")
    try:
        for src in ("bin/etk_pitlink_usbd.py", "bin/onboard_cap.sh", "tools/pitlink/plusb.py"):
            r = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:{src}"], capture_output=True)
            if r.returncode == 0:
                with open(os.path.join(tmp, os.path.basename(src)), "wb") as f:
                    f.write(r.stdout)
            else:
                print(f"[against {rev}] {src}: not in that tree")
        env = dict(os.environ, ONBOARD_DAEMON_DIR=tmp)
        rc = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env).returncode
        print(f"[against {rev}] {'PASS' if rc == 0 else 'FAIL'} (exit {rc})")
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--against":
    sys.exit(against(sys.argv[2]))

import plusb as L  # noqa: E402
import etk_pitlink_usbd as D  # noqa: E402

ONBD = struct.Struct("<4sBBBBHHIQ")
INFO, FRAME, CTRL, END = 0, 1, 2, 3


def fake_jpeg(w, h, body=b""):
    """SOI, a JFIF APP0, SOF0 (w x h, 3 comps), filler, EOI: enough for any SOF reader."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + body + b"\xff\xd9"


SMALL = fake_jpeg(960, 540, b"lobby" * 200)
NATIVE = fake_jpeg(1920, 1080, b"native" * 400)

FAKE_GRIM = r"""#!/bin/sh
# fake grim: log argv/env/pid, then behave as $T/mode says
T="@T@"
echo "$$" >> "$T/pids"
echo "$* | XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR WAYLAND_DISPLAY=$WAYLAND_DISPLAY" >> "$T/argv"
MODE=$(cat "$T/mode" 2>/dev/null)
S=""
while [ $# -gt 0 ]; do [ "$1" = "-s" ] && S="$2"; shift; done
case "$MODE" in
    fail)  echo "failed to create display: compositor gone" >&2; exit 3 ;;
    empty) exit 0 ;;
    slow)  exec sleep 30 ;;
esac
sleep "$(cat "$T/delay" 2>/dev/null || echo 0.02)"
if [ "$S" = "1" ]; then cat "$T/native.jpg"; else cat "$T/small.jpg"; fi
"""


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class Host:
    """The fake pit wall: one relay, a drain thread parsing IN segments into ONBD messages."""

    def __init__(self, relay):
        self.relay = relay
        self.parser = L.Parser()
        self.bufs, self.msgs, self.closes = {}, [], []
        self.lock = threading.Lock()
        self.drain = threading.Event()
        self.drain.set()
        self.stop = threading.Event()
        relay.feed(L.seg(L.LINK, L.HELLO, b"n" * 16))
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop.is_set():
            if not self.drain.wait(0.05):
                continue
            try:
                b = self.relay.out.take(timeout=0.05)
            except queue.Empty:
                continue
            t = time.monotonic()
            for chan, kind, _f, payload in self.parser.feed(b):
                if chan == L.LINK:
                    continue
                with self.lock:
                    if kind == L.CLOSE:
                        self.closes.append((chan, payload.decode(errors="replace")))
                    elif kind == L.DATA:
                        buf = self.bufs.setdefault(chan, bytearray())
                        buf += payload
                        while True:
                            m, n = D.parse_msg(buf)
                            if m is None:
                                break
                            del buf[:n]
                            self.msgs.append((t, chan, m))

    def open(self, chan, target=b"onboard"):
        self.relay.feed(L.seg(chan, L.OPEN, target))

    def ctrl(self, chan, obj):
        payload = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.relay.feed(L.seg(chan, L.DATA, D.pack_msg(CTRL, payload)))

    def close(self, chan):
        self.relay.feed(L.seg(chan, L.CLOSE, b"host client left"))

    def of(self, chan, kind=None):
        with self.lock:
            return [(t, m) for t, c, m in self.msgs if c == chan and (kind is None or m.kind == kind)]

    def infos(self, chan):
        return [json.loads(m.payload) for _t, m in self.of(chan, INFO)]

    def wait(self, pred, timeout=3.0):
        t = time.monotonic() + timeout
        while time.monotonic() < t:
            if pred():
                return True
            time.sleep(0.01)
        return bool(pred())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onboard-car-")
        self.bin = os.path.join(self.tmp, "bin")
        self.shm = os.path.join(self.tmp, "shm")
        os.makedirs(self.bin)
        os.makedirs(self.shm)
        with open(os.path.join(self.bin, "grim"), "w") as f:
            f.write(FAKE_GRIM.replace("@T@", self.tmp))
        os.chmod(os.path.join(self.bin, "grim"), 0o755)
        for n, b in (("small.jpg", SMALL), ("native.jpg", NATIVE)):
            with open(os.path.join(self.tmp, n), "wb") as f:
                f.write(b)
        self.conf = os.path.join(self.tmp, "etk.conf")
        self.write_conf("")
        self.set_shm(active_id="IDLE", etk_mode="RACE")   # what a kit car shows at ES
        self.patches = [mock.patch.dict(os.environ),                  # restored whole at tearDown
                        mock.patch.object(D, "ONBOARD_SHM", self.shm),
                        mock.patch.object(D, "ONBOARD_CONF", self.conf)]
        if not os.environ.get("ONBOARD_TEST_LOG"):                     # ONBOARD_TEST_LOG=1: daemon log
            self.patches.append(mock.patch.object(D, "log", lambda *a: None))
        for p in self.patches:
            p.start()
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        for k in ("ETK_ONBOARD", "ETK_ONBOARD_RUNNING_FPS", "ETK_ONBOARD_QUALITY", "XDG_RUNTIME_DIR",
                  "WAYLAND_DISPLAY"):
            os.environ.pop(k, None)
        with mock.patch.object(D.os, "open", lambda *a, **k: -1):
            self.daemon = D.Daemon(-1)       # the relay `serve` runs, endpoints stubbed
        self.relay = self.daemon.relay
        self.host = Host(self.relay)

    def tearDown(self):
        self.relay.vendor_reset()
        self.host.wait(lambda: not D.onboard_sessions(), 3)
        self.host.stop.set()
        for p in reversed(self.patches):
            p.stop()
        self.assertEqual(self.capture_pids(), [], "a capture process outlived its channel")
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------------
    def write_conf(self, text):
        with open(self.conf, "w") as f:
            f.write(text)

    def set_shm(self, active_id=None, etk_mode=None):
        for name, v in (("active_id.txt", active_id), ("etk_mode.txt", etk_mode)):
            p = os.path.join(self.shm, name)
            if v is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                with open(p, "w") as f:
                    f.write(v + "\n")

    def grim_mode(self, mode, delay=None):
        with open(os.path.join(self.tmp, "mode"), "w") as f:
            f.write(mode)
        if delay is not None:
            with open(os.path.join(self.tmp, "delay"), "w") as f:
                f.write(str(delay))

    def grim_calls(self):
        try:
            with open(os.path.join(self.tmp, "argv")) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def capture_pids(self):
        """Every fake-grim pid still alive, plus any process whose argv names our temp dir."""
        pids = []
        try:
            with open(os.path.join(self.tmp, "pids")) as f:
                pids = [int(x) for x in f.read().split()]
        except OSError:
            pass
        live = [p for p in pids if alive(p)]
        for d in os.listdir("/proc"):
            if d.isdigit() and int(d) != os.getpid():
                try:
                    with open(f"/proc/{d}/cmdline", "rb") as f:
                        if self.tmp.encode() in f.read() and alive(int(d)):
                            live.append(int(d))
                except OSError:
                    pass
        return sorted(set(live))

    def frames(self, chan):
        return [(t, m) for t, m in self.host.of(chan, FRAME)]


# =========================================================================== [WIRE]
class TestWire(unittest.TestCase):
    def test_header_layout_is_onbd_v1(self):
        b = D.pack_msg(FRAME, b"abc", codec=2, flags=3, w=960, h=540, seq=7, car_ns=123456789)
        self.assertEqual(D.ONBD_HDR.size, 24)
        self.assertEqual(len(b), 28 + 3)
        self.assertEqual(ONBD.unpack_from(b), (b"ONBD", 1, FRAME, 2, 3, 960, 540, 7, 123456789))
        self.assertEqual(struct.unpack_from("<I", b, 24), (3,))
        self.assertEqual(b[28:], b"abc")

    def test_parse_roundtrip_any_split_and_partial(self):
        wire = (D.pack_msg(INFO, {"mode": "IDLE"}) + D.pack_msg(FRAME, SMALL, codec=2, w=960, h=540, seq=1)
                + D.pack_msg(END, "bye"))
        for step in (1, 7, 29, len(wire)):
            buf, got = bytearray(), []
            for i in range(0, len(wire), step):
                buf += wire[i:i + step]
                while True:
                    m, n = D.parse_msg(buf)
                    if m is None:
                        self.assertEqual(n, 0)
                        break
                    del buf[:n]
                    got.append(m)
            self.assertEqual([m.kind for m in got], [INFO, FRAME, END])
            self.assertEqual(json.loads(got[0].payload), {"mode": "IDLE"})
            self.assertEqual((got[1].w, got[1].h, got[1].seq, got[1].payload), (960, 540, 1, SMALL))

    def test_parse_rejects_bad_magic_and_version(self):
        with self.assertRaises(ValueError):
            D.parse_msg(b"XNBD" + bytes(24))
        bad_ver = bytearray(D.pack_msg(INFO, b"{}"))
        bad_ver[4] = 2
        with self.assertRaises(ValueError):
            D.parse_msg(bytes(bad_ver))

    def test_daemon_imports_without_termios(self):
        code = ("import sys; sys.modules['termios'] = None; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
                "import etk_pitlink_usbd as D; print(hex(D._ONBOARD_TIOCOUTQ), callable(D.garage))")
        r = subprocess.run([sys.executable, "-c", code, DAEMON_DIR, HERE], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "0x5411 True"), r.stderr)

    def test_jpeg_size(self):
        self.assertEqual(D.jpeg_size(SMALL), (960, 540))
        self.assertEqual(D.jpeg_size(NATIVE), (1920, 1080))
        self.assertEqual(D.jpeg_size(b"not a jpeg"), (0, 0))


# ========================================================================= [STREAM]
class TestStream(Base):
    def test_daemon_dispatch_has_onboard_beside_garage(self):
        self.assertIn("garage", self.relay.services)
        self.assertIn("onboard", self.relay.services)

    def test_open_info_then_frames_at_rate(self):
        self.host.open(1)
        self.assertTrue(self.host.wait(lambda: len(self.frames(1)) >= 6, 4))
        first = self.host.of(1)[0][1]
        self.assertEqual(first.kind, INFO, "INFO must be the first message")
        info = json.loads(first.payload)
        for k in ("backend", "encoder", "mode", "fps", "quality", "scale", "transform", "panel", "policy_fps",
                  "pinned", "daemon"):
            self.assertIn(k, info)
        self.assertEqual((info["backend"], info["mode"], info["pinned"], info["transform"], info["panel"]),
                         ("jpeg-grim", "IDLE", False, 90, [1080, 1920]))
        fr = self.frames(1)
        _t, m = fr[0]
        self.assertEqual((m.codec, m.w, m.h, m.payload, m.flags), (2, 960, 540, SMALL, 0))
        self.assertEqual([m.seq for _t, m in fr[:6]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(b.car_ns > a.car_ns for (_a, a), (_b, b) in zip(fr, fr[1:])))
        self.assertIn("-o DSI-1 -t jpeg -q 70 -s 0.5 -", self.grim_calls()[0])
        # default 5 fps: frames 2..6 span ~1.0 s
        span = fr[5][0] - fr[1][0]
        self.assertTrue(0.6 < span < 1.4, f"5 fps stream: 4 intervals took {span:.2f} s")

    def test_ctrl_fps_changes_rate_and_is_answered(self):
        self.host.open(2)
        self.assertTrue(self.host.wait(lambda: len(self.frames(2)) >= 1))
        self.host.ctrl(2, {"fps": 12})
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(2)) >= 2))
        self.assertEqual(self.host.infos(2)[1]["fps"], 12)
        n0, t0 = len(self.frames(2)), time.monotonic()
        time.sleep(1.5)
        rate = (len(self.frames(2)) - n0) / (time.monotonic() - t0)
        self.assertTrue(8 <= rate <= 14, f"asked 12 fps, got {rate:.1f}")
        self.host.ctrl(2, {"fps": 99})                                  # clamped to 30
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(2)) >= 3))
        self.assertEqual(self.host.infos(2)[2]["fps"], 30)
        self.host.ctrl(2, {"fps": 0})                                   # host pause: no capture
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(2)) >= 4))
        time.sleep(0.3)
        n1, calls = len(self.frames(2)), len(self.grim_calls())
        time.sleep(1.0)
        self.assertEqual((len(self.frames(2)), len(self.grim_calls())), (n1, calls))

    def test_ctrl_quality_scale_snap_and_junk(self):
        self.host.open(3)
        self.assertTrue(self.host.wait(lambda: len(self.frames(3)) >= 1))
        self.host.ctrl(3, {"quality": 40, "scale": 0.25})
        self.assertTrue(self.host.wait(lambda: any("-q 40 -s 0.25 -" in c for c in self.grim_calls())))
        info = self.host.infos(3)[-1]
        self.assertEqual((info["quality"], info["scale"]), (40, 0.25))
        self.host.ctrl(3, {"snap": True})
        self.assertTrue(self.host.wait(lambda: any(m.flags & 2 for _t, m in self.frames(3))))
        snap = [m for _t, m in self.frames(3) if m.flags & 2][0]
        self.assertEqual((snap.w, snap.h, snap.payload), (1920, 1080, NATIVE))
        self.assertTrue(any("-q 100 -s 1 -" in c for c in self.grim_calls()))
        n_info = len(self.host.infos(3))
        self.host.ctrl(3, {"bogus": 1, "fps": "fast", "quality": True, "rotate": 180})
        self.host.ctrl(3, b"this is not json")
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(3)) >= n_info + 2))
        junk, notjson = self.host.infos(3)[n_info:n_info + 2]
        self.assertEqual(junk["ignored"], ["bogus", "fps", "quality", "rotate"])
        self.assertEqual((junk["fps"], junk["quality"]), (5, 40))     # untouched
        self.assertIn("err", notjson)
        n = len(self.frames(3))
        self.assertTrue(self.host.wait(lambda: len(self.frames(3)) >= n + 3), "stream died on junk CTRL")
        # every CTRL answered by exactly one INFO: open + 4 CTRLs
        self.assertEqual(len(self.host.infos(3)), 5)


# ========================================================================= [POLICY]
class TestPolicy(Base):
    def test_running_policy_zero_sends_info_and_never_captures(self):
        self.set_shm(active_id="NPEA00050", etk_mode="RACE")
        self.host.open(4)
        self.assertTrue(self.host.wait(lambda: self.host.infos(4)))
        info = self.host.infos(4)[0]
        self.assertEqual((info["mode"], info["fps"], info["policy_fps"], info["pinned"]), ("RUNNING", 0, 0, False))
        time.sleep(1.6)
        self.assertEqual(self.frames(4), [])
        self.assertEqual(self.grim_calls(), [], "a capture ran under RUNNING policy 0")
        self.assertEqual(self.capture_pids(), [])
        self.assertEqual([s.proc for s in D.onboard_sessions()], [None])
        self.host.ctrl(4, {"snap": True})                               # a snap is a capture too
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(4)) >= 2))
        self.assertIn("refused", self.host.infos(4)[1]["snap"])
        time.sleep(0.3)
        self.assertEqual((self.frames(4), self.grim_calls()), ([], []))
        self.host.ctrl(4, {"pin": True})                                # the operator's override
        self.assertTrue(self.host.wait(lambda: len(self.frames(4)) >= 3))
        self.assertTrue(self.host.infos(4)[2]["pinned"])
        self.assertEqual(self.host.infos(4)[2]["fps"], 5)

    def test_mode_is_active_id_only(self):
        for aid, want in ((None, "IDLE"), ("", "IDLE"), ("IDLE", "IDLE"),
                          ("NPEA00050", "RUNNING"), ("UNKNOWN_ID", "RUNNING")):
            for mode in (None, "RACE", "PIT", "RUNNING", "IDLE"):     # etk_mode.txt is ignored
                self.set_shm(active_id=aid, etk_mode=mode)
                self.assertEqual(D.onboard_mode(), want, (aid, mode))

    def test_running_rate_from_etk_conf_and_mode_flips(self):
        self.write_conf('# kit conf\nETK_ONBOARD_RUNNING_FPS="2"  # lobby cam during a game\n')
        self.host.open(5)
        self.assertTrue(self.host.wait(lambda: len(self.frames(5)) >= 2))
        self.set_shm(active_id="NPEA00050")
        self.assertTrue(self.host.wait(lambda: any(i["mode"] == "RUNNING" for i in self.host.infos(5)), 2.5))
        ri = [i for i in self.host.infos(5) if i["mode"] == "RUNNING"][0]
        self.assertEqual((ri["fps"], ri["policy_fps"], ri["why"]), (2, 2, "mode"))
        time.sleep(0.6)
        n0, t0 = len(self.frames(5)), time.monotonic()
        time.sleep(2.0)
        rate = (len(self.frames(5)) - n0) / (time.monotonic() - t0)
        self.assertTrue(1.0 <= rate <= 3.0, f"RUNNING cap 2 fps, got {rate:.1f}")
        self.set_shm(active_id="IDLE")
        self.assertTrue(self.host.wait(lambda: self.host.infos(5)[-1]["mode"] == "IDLE", 2.5))
        self.assertEqual(self.host.infos(5)[-1]["fps"], 5)

    def test_policy_to_zero_stops_capture_within_a_second(self):
        self.host.open(6)
        self.assertTrue(self.host.wait(lambda: len(self.frames(6)) >= 2))
        self.set_shm(active_id="BCUS98158")
        t0 = time.monotonic()
        self.assertTrue(self.host.wait(lambda: self.host.infos(6)[-1]["mode"] == "RUNNING", 2.5))
        self.assertLess(time.monotonic() - t0, 1.5)
        time.sleep(0.3)
        n, calls = len(self.frames(6)), len(self.grim_calls())
        time.sleep(1.2)
        self.assertEqual((len(self.frames(6)), len(self.grim_calls())), (n, calls))
        self.assertEqual(self.capture_pids(), [])

    def test_kill_switch_ends_the_channel(self):
        for chan, val in ((7, "0"), (17, "false"), (18, "00")):   # enabled means exactly "1" (install.sh: = "1")
            self.write_conf(f"ETK_ONBOARD={val}\n")
            self.host.open(chan)
            self.assertTrue(self.host.wait(lambda: self.host.of(chan, END)), val)
            self.assertIn("ETK_ONBOARD=0", self.host.of(chan, END)[0][1].payload.decode())
            self.assertTrue(self.host.wait(lambda: any(c == chan for c, _w in self.host.closes)))
        self.assertEqual(self.grim_calls(), [])
        for chan, val in ((20, "1"), (21, '"1"'), (19, '""'), (22, "")):   # empty = on, like install.sh's ${ETK_ONBOARD:-1}
            self.write_conf(f"ETK_ONBOARD={val}\n")
            self.host.open(chan)
            self.assertTrue(self.host.wait(lambda: len(self.frames(chan)) >= 1), val)
            self.assertEqual(self.host.of(chan, END), [])
            self.host.close(chan)
        self.assertTrue(self.host.wait(lambda: not D.onboard_sessions(), 1.0))
        self.write_conf("")                                             # unset = default on
        self.assertTrue(D.onboard_conf()["enabled"])

    def test_link_busy_survives_a_failing_ioctl(self):
        import socket
        a, b = socket.socketpair()
        try:
            sess = D.OnboardCapture(a, relay=None)
            a.sendall(b"x" * 100)
            self.assertTrue(sess.link_busy())                           # unread bytes = busy
            with mock.patch.object(D.fcntl, "ioctl", side_effect=OSError(25, "ENOTTY")):
                self.assertFalse(sess.link_busy())                      # unknown = not busy
        finally:
            a.close()
            b.close()


# =========================================================================== [STOP]
class TestStop(Base):
    def _slow_capture_running(self, chan):
        self.grim_mode("slow")
        self.host.open(chan)
        self.assertTrue(self.host.wait(lambda: self.capture_pids(), 3), "the slow capture never started")

    def test_close_kills_capture_within_1s(self):
        self._slow_capture_running(8)
        t0 = time.monotonic()
        self.host.close(8)
        self.assertTrue(self.host.wait(lambda: not self.capture_pids(), 1.0),
                        f"capture still alive {time.monotonic() - t0:.2f} s after CLOSE")
        self.assertTrue(self.host.wait(lambda: not D.onboard_sessions(), 1.0))

    def test_link_loss_kills_capture_within_1s(self):
        self._slow_capture_running(9)
        self.relay.link_down()                                          # DISABLE / unplug
        self.assertTrue(self.host.wait(lambda: not self.capture_pids(), 1.0))
        self.assertTrue(self.host.wait(lambda: not D.onboard_sessions(), 1.0))

    def test_grim_failure_sends_end_and_daemon_lives_on(self):
        self.grim_mode("fail")
        self.host.open(10)
        self.assertTrue(self.host.wait(lambda: self.host.of(10, END)))
        why = self.host.of(10, END)[0][1].payload.decode()
        self.assertIn("exit 3", why)
        self.assertIn("compositor gone", why)
        self.assertTrue(self.host.wait(lambda: any(c == 10 for c, _w in self.host.closes)))
        self.grim_mode("empty")
        self.host.open(11)
        self.assertTrue(self.host.wait(lambda: self.host.of(11, END)))
        self.assertIn("no image", self.host.of(11, END)[0][1].payload.decode())
        self.grim_mode("ok")                                            # the daemon still serves
        self.host.open(12)
        self.assertTrue(self.host.wait(lambda: len(self.frames(12)) >= 2))

    def test_no_channel_no_process(self):
        self.host.open(13)
        self.assertTrue(self.host.wait(lambda: len(self.frames(13)) >= 2))
        self.host.close(13)
        self.assertTrue(self.host.wait(lambda: not D.onboard_sessions(), 1.0))
        time.sleep(0.4)
        calls = len(self.grim_calls())
        time.sleep(1.0)
        self.assertEqual(len(self.grim_calls()), calls)
        self.assertEqual(self.capture_pids(), [])


# =========================================================================== [LINK]
class TestSlowLink(Base):
    def test_stalled_host_gets_no_backlog(self):
        self.host.open(14)
        self.assertTrue(self.host.wait(lambda: len(self.frames(14)) >= 1))
        self.host.ctrl(14, {"fps": 20})
        self.assertTrue(self.host.wait(lambda: len(self.host.infos(14)) >= 2))
        self.host.drain.clear()                                         # the host stops reading
        time.sleep(0.3)
        calls0 = len(self.grim_calls())
        time.sleep(2.0)
        stalled = len(self.grim_calls()) - calls0
        queued = self.relay.out.q.qsize()
        self.assertLessEqual(stalled, 2, f"{stalled} captures while the link was stalled (20 fps asked)")
        self.assertLessEqual(queued, 3, f"{queued} segments queued at the car")
        before = max(m.seq for _t, m in self.frames(14))
        self.host.drain.set()
        self.assertTrue(self.host.wait(lambda: len(self.host.of(14, FRAME)) >= 8, 3))
        seqs = [m.seq for _t, m in self.frames(14)]
        self.assertTrue(any(b - a > 10 for a, b in zip(seqs, seqs[1:]) if a >= before - 3),
                        f"no seq gap over the 2 s stall: {seqs}")


# ============================================================================ [CAP]
class TestCapScript(Base):
    def test_cap_script_runs_grim_with_wayland_env(self):
        cap = os.path.join(DAEMON_DIR, "onboard_cap.sh")
        r = subprocess.run(["/bin/sh", cap, "70", "0.5"], capture_output=True, timeout=10)
        self.assertEqual((r.returncode, r.stdout), (0, SMALL), r.stderr)
        line = self.grim_calls()[-1]
        self.assertTrue(line.startswith("-o DSI-1 -t jpeg -q 70 -s 0.5 -"), line)
        self.assertIn("XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1", line)

    def test_cap_script_refuses_bad_arguments(self):
        cap = os.path.join(DAEMON_DIR, "onboard_cap.sh")
        for args in (["abc", "0.5"], ["0", "0.5"], ["101", "0.5"], ["70", "x"], ["70", "0.5.1"], ["70", ""],
                     ["70", "0.5", "-oops"], ["70", "0.5", "DSI 1"]):
            r = subprocess.run(["/bin/sh", cap] + args, capture_output=True, timeout=10)
            self.assertEqual(r.returncode, 2, args)
        self.assertEqual(self.grim_calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
