#!/usr/bin/env python3
"""test_plusb -- Pitlink over raw USB (PLUSB v1), without the hardware.

  [WIRE]  segment framing, HUNT/resync, the OutQueue write cap
  [LINK]  HostMux <-> CarRelay: HELLO/nonce, stale-host garbage, CLOSE on a dead target,
          PING/PONG, and a reset mid-session
  [E2E]   the whole PLNK suite (test_pitlink.LinkBase) through client -> usb_broker ->
          fake USB pipe -> CarRelay -> fake_server: PLNK v1 must not notice the transport
  [FFS]   the car daemon's FunctionFS descriptor/string blobs against the kernel ABI

Run: python3 tools/pitlink/test_plusb.py
"""
import itertools
import os
import queue
import socket
import struct
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "bin"))
import plusb as L  # noqa: E402
import usb_broker  # noqa: E402
from fake_server import FakeCar  # noqa: E402
import test_pitlink  # noqa: E402

_uniq = itertools.count()


def name(tag):
    return f"@plusb-test-{tag}-{os.getpid()}-{next(_uniq)}"


# ========================================================================== [WIRE]
class TestWire(unittest.TestCase):
    def test_header_is_12_bytes_exact(self):
        self.assertEqual(L.SEG.size, 12)
        self.assertEqual(L.seg(7, L.DATA, b"abc"), b"PLUB" + struct.pack("<HBBI", 7, 0, 0, 3) + b"abc")

    def test_roundtrip_any_split(self):
        wire = b"".join([L.seg(1, L.OPEN), L.seg(1, L.DATA, b"x" * 1000), L.seg(2, L.CLOSE, b"bye")])
        for step in (1, 5, 13, 999, len(wire)):
            p, got = L.Parser(), []
            for i in range(0, len(wire), step):
                got += p.feed(wire[i:i + step])
            self.assertEqual([(c, k, pl) for c, k, _f, pl in got],
                             [(1, L.OPEN, b""), (1, L.DATA, b"x" * 1000), (2, L.CLOSE, b"bye")])

    def test_hunt_skips_garbage_to_anchor(self):
        p = L.Parser(anchor=L.hello_prefix())
        stale = L.seg(5, L.DATA, b"PLUB\x00\x00\x00\x00\xff\xff\xff\x7f half a fake header")[:-3]
        got = p.feed(stale + L.seg(L.LINK, L.HELLO, b"n" * 16) + L.seg(1, L.OPEN))
        self.assertEqual([(c, k) for c, k, _f, _p in got], [(L.LINK, L.HELLO), (1, L.OPEN)])
        self.assertGreater(p.dropped, 0)

    def test_malformed_header_rehunts(self):
        p = L.Parser()
        p.hunting = False
        bad = b"PLUB" + struct.pack("<HBBI", 1, 99, 0, 4) + b"zzzz"
        got = p.feed(bad + L.seg(3, L.DATA, b"ok"))
        self.assertEqual([(c, pl) for c, _k, _f, pl in got], [(3, b"ok")])

    def test_oversize_length_rejected(self):
        p = L.Parser()
        got = p.feed(b"PLUB" + struct.pack("<HBBI", 1, 0, 0, L.MAX_PAYLOAD + 1) + L.seg(1, L.DATA, b"y"))
        self.assertEqual([pl for _c, _k, _f, pl in got], [b"y"])

    def test_data_segs_split_at_max(self):
        segs = L.data_segs(9, b"a" * (L.MAX_PAYLOAD * 2 + 5))
        self.assertEqual([len(s) - 12 for s in segs], [L.MAX_PAYLOAD, L.MAX_PAYLOAD, 5])
        self.assertTrue(all(len(s) <= L.IO_CHUNK for s in segs))

    def test_outqueue_coalesces_small_and_caps(self):
        q = L.OutQueue()
        for _ in range(10):
            q.put(b"p" * 44)
        self.assertEqual(len(q.take(timeout=0.1)), 440)
        q.put(b"s" * 100)
        q.put(b"B" * L.IO_CHUNK)
        first = q.take(timeout=0.1)
        self.assertEqual(first, b"s" * 100)      # the big one does not ride along past the cap
        self.assertEqual(len(q.take(timeout=0.1)), L.IO_CHUNK)


# ========================================================================== [LINK]
class FakeUsbPipe:
    """Stands in for usb_broker.UsbPipe: OUT bytes go to CarRelay.feed, IN bytes come from
    CarRelay.out. Optional `stale` IN bytes model a previous host's half-read frame."""

    intf, ep_in, ep_out, mps = 2, 0x81, 0x02, 512

    def __init__(self, relay, stale=b""):
        self.relay, self.stale = relay, stale
        self.closed = False

    def reset(self):
        self.relay.vendor_reset()

    def read(self, timeout_ms=200):
        if self.closed:
            raise usb_broker.UsbError("bulk IN", usb_broker.E_NO_DEVICE)
        if self.stale:
            b, self.stale = self.stale, b""
            return b
        try:
            return self.relay.out.take(timeout=timeout_ms / 1000.0)
        except queue.Empty:
            return b""

    def write(self, data, timeout_ms=2000):
        if self.closed:
            raise usb_broker.UsbError("bulk OUT", usb_broker.E_NO_DEVICE)
        self.relay.feed(bytes(data))

    def close(self):
        self.closed = True


class Chain:
    """fake_server <- CarRelay <- FakeUsbPipe <- Broker(@sock): the whole USB path."""

    def __init__(self, stale=b""):
        self.car = FakeCar(listen=(name("car"),), fps=60, video=(64, 36, 60, test_pitlink.P.CODEC_RAW),
                           ram=(test_pitlink.RAM, 0x10000), native=(128, 72)).start()
        self.relay = L.CarRelay(target=self.car.addrs[0], status=lambda: "fake car")
        self.pipes = []

        def open_pipe():
            p = FakeUsbPipe(self.relay, stale=stale if not self.pipes else b"")
            self.pipes.append(p)
            return p
        self.sock = name("broker")
        self.garage = name("garage")
        self.broker = usb_broker.Broker(open_pipe=open_pipe, sock_name=self.sock, garage_name=self.garage)
        threading.Thread(target=self.broker.serve, daemon=True).start()
        if not self.broker.mux.up.wait(3):
            raise RuntimeError("fake link never came up")

    def stop(self):
        self.broker.shutdown()
        for p in self.pipes:
            p.close()
        self.car.stop()


class TestLink(unittest.TestCase):
    def test_stale_garbage_and_foreign_hello_ack_are_ignored(self):
        foreign = L.seg(L.LINK, L.HELLO_ACK, b"\x00" * 16 + b"someone else's")
        ch = Chain(stale=os.urandom(3000) + b"PLUB" + foreign + L.seg(4, L.DATA, b"old frame bytes"))
        try:
            self.assertEqual(ch.broker.mux.car_status, "fake car")
        finally:
            ch.stop()

    def test_dead_target_closes_channel_with_reason(self):
        relay = L.CarRelay(target=name("nobody"))
        relay.feed(L.seg(L.LINK, L.HELLO, b"n" * 16) + L.seg(3, L.OPEN) + L.seg(3, L.DATA, b"hello?"))
        p = L.Parser()
        got = []
        while True:
            try:
                got += p.feed(relay.out.take(timeout=0.2))
            except queue.Empty:
                break
        kinds = [(c, k, pl) for c, k, _f, pl in got]
        self.assertEqual(kinds[0][:2], (L.LINK, L.HELLO_ACK))
        self.assertIn((3, L.CLOSE, b"RPCS3 Pitlink is not running"), kinds)

    def test_link_ping_rtt(self):
        ch = Chain()
        try:
            for _ in range(3):
                ch.broker.mux.ping()
            deadline = time.time() + 2
            while len(ch.broker.mux.rtt_ms) < 3 and time.time() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(len(ch.broker.mux.rtt_ms), 3)
        finally:
            ch.stop()

    def test_unplug_closes_clients_and_relinks(self):
        ch = Chain()
        try:
            from client import PitlinkClient, PitlinkError
            c = PitlinkClient(ch.sock, timeout=3)
            c.hello()
            ch.pipes[-1].close()  # yank the cable
            test_pitlink.wait_until(lambda: c.closed, 3)
            self.assertTrue(c.closed)
            test_pitlink.wait_until(lambda: len(ch.pipes) >= 2 and ch.broker.mux.up.is_set(), 5)
            c2 = PitlinkClient(ch.sock, timeout=3)
            self.assertIn("role", c2.hello() or {"role": 0})
            c2.close()
            with self.assertRaises(PitlinkError):
                c.ping()
        finally:
            ch.stop()


# =========================================================================== [E2E]
class TestLinkUsb(test_pitlink.LinkBase, unittest.TestCase):
    """Every PLNK link test, through the USB transport."""

    def listen(self):
        return name("unused")

    def setUp(self):
        self.clients = []
        self.chain = Chain()
        self.car = self.chain.car
        self.addr = self.chain.sock

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.chain.stop()


# =========================================================================== [FFS]
class TestFunctionFs(unittest.TestCase):
    def test_descriptor_blob_matches_abi(self):
        import etk_pitlink_usbd as D
        b = D.descriptors()
        magic, length, flags, fs_n, hs_n, ss_n = struct.unpack_from("<6I", b)
        self.assertEqual((magic, length, flags, fs_n, hs_n, ss_n), (3, len(b), 7, 3, 3, 5))
        i, seen = 24, []
        while i < len(b):
            seen.append((b[i], b[i + 1]))
            i += b[i]
        self.assertEqual(i, len(b))
        self.assertEqual(seen, [(9, 4), (7, 5), (7, 5)] * 2 + [(9, 4), (7, 5), (6, 0x30), (7, 5), (6, 0x30)])
        # interface: vendor class/subclass/protocol, string 1
        self.assertEqual(tuple(b[24 + 5:24 + 9]), (L.IF_CLASS, L.IF_SUBCLASS, L.IF_PROTOCOL, 1))

    def test_strings_blob(self):
        import etk_pitlink_usbd as D
        s = D.strings()
        magic, length, n_str, n_lang = struct.unpack_from("<4I", s)
        self.assertEqual((magic, length, n_str, n_lang), (2, len(s), 1, 1))
        self.assertEqual(s[16:18], b"\x09\x04")
        self.assertEqual(s[18:], b"ETK Pitlink\0")

    def test_rebind_really_unbinds_before_linking(self):
        """configfs semantics: an empty write is a no-op, a newline unbinds, and linking a
        function into a BOUND gadget is EINVAL. The 2026-10-10 harness run hit exactly this."""
        import errno
        from unittest import mock
        import etk_pitlink_usbd as D
        state = {"udc": "a600000.usb", "links": set()}

        def wr(path, v):
            if path.endswith("/UDC") and v:  # f.write("") never reaches the kernel
                state["udc"] = v.strip()

        def rd(path):
            return state["udc"] if path.endswith("/UDC") else ""

        def symlink(src, dst):
            if state["udc"]:
                raise OSError(errno.EINVAL, "Invalid argument")
            state["links"].add(dst)

        with mock.patch.object(D, "wr", wr), mock.patch.object(D, "rd", rd), \
                mock.patch.object(D.os, "symlink", symlink), \
                mock.patch.object(D.os.path, "islink", lambda p: p in state["links"]), \
                mock.patch.object(D.os, "unlink", lambda p: state["links"].discard(p)):
            self.assertTrue(D.rebind("a600000.usb", with_us=True))
            self.assertIn(D.LINK, state["links"])
            self.assertEqual(state["udc"], "a600000.usb")
            self.assertTrue(D.rebind("a600000.usb", with_us=False))
            self.assertNotIn(D.LINK, state["links"])

    def test_event_struct_is_12(self):
        import etk_pitlink_usbd as D
        self.assertEqual(D.EVENT.size, 12)


if __name__ == "__main__":
    unittest.main(verbosity=1)
