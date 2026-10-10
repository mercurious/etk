#!/usr/bin/env python3
"""usb_broker -- the M1's end of Pitlink over raw USB (PLUSB v1, see plusb.py).

Claims the car's "ETK Pitlink" USB interface with libusb (ctypes; no pip packages) and serves
PLNK v1 to local clients on the abstract socket @etk-pitlink-usb: each client is its own
channel (its own PLNK session, roles and all), multiplexed over the one bulk pipe pair.
Clients don't start it by hand: PitlinkClient("usb") spawns it on first use.

  usb_broker.py serve [--socket @etk-pitlink-usb]   run (foreground; logs to stderr)
  usb_broker.py probe                               find + claim the interface, HELLO, 20 link pings

Permissions: the M1 needs tools/pitlink/71-etk-pitlink.rules in /etc/udev/rules.d (once,
operator) for a non-root user to claim the device.
"""
import argparse
import ctypes
import ctypes.util
import os
import socket
import statistics
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plusb as L  # noqa: E402

E_TIMEOUT, E_NO_DEVICE, E_ACCESS, E_BUSY, E_PIPE, E_INTERRUPTED, E_NOT_FOUND = -7, -4, -3, -6, -9, -10, -5
ERRNAMES = {-1: "IO", -2: "INVALID_PARAM", -3: "ACCESS", -4: "NO_DEVICE", -5: "NOT_FOUND", -6: "BUSY",
            -7: "TIMEOUT", -8: "OVERFLOW", -9: "PIPE", -10: "INTERRUPTED", -11: "NO_MEM", -12: "NOT_SUPPORTED"}


class UsbError(OSError):
    def __init__(self, what, rc):
        super().__init__(f"{what}: LIBUSB_ERROR_{ERRNAMES.get(rc, rc)}")
        self.rc = rc


def _lib():
    name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
    lib = ctypes.CDLL(name)
    P, I, U8, U16, U32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32
    sig = {
        "libusb_init": (I, [ctypes.POINTER(P)]),
        "libusb_exit": (None, [P]),
        "libusb_open_device_with_vid_pid": (P, [P, U16, U16]),
        "libusb_close": (None, [P]),
        "libusb_set_auto_detach_kernel_driver": (I, [P, I]),
        "libusb_claim_interface": (I, [P, I]),
        "libusb_release_interface": (I, [P, I]),
        "libusb_clear_halt": (I, [P, ctypes.c_ubyte]),
        "libusb_control_transfer": (I, [P, U8, U8, U16, U16, ctypes.c_char_p, U16, ctypes.c_uint]),
        "libusb_bulk_transfer": (I, [P, ctypes.c_ubyte, ctypes.c_char_p, I, ctypes.POINTER(I), ctypes.c_uint]),
        "libusb_get_string_descriptor_ascii": (I, [P, U8, ctypes.c_char_p, I]),
    }
    for fn, (res, args) in sig.items():
        f = getattr(lib, fn)
        f.restype, f.argtypes = res, args
    _ = U32
    return lib


class UsbPipe:
    """The claimed interface: bulk IN/OUT + the vendor reset. Thread-safe per direction."""

    def __init__(self, vid=L.VID, pid=L.PID):
        self.lib = _lib()
        self.ctx = ctypes.c_void_p()
        rc = self.lib.libusb_init(ctypes.byref(self.ctx))
        if rc:
            raise UsbError("libusb_init", rc)
        self.h = self.lib.libusb_open_device_with_vid_pid(self.ctx, vid, pid)
        if not self.h:
            self.lib.libusb_exit(self.ctx)
            raise UsbError(f"no car on USB ({vid:04x}:{pid:04x} not found or not permitted -- "
                           "is the rig plugged in, and is 71-etk-pitlink.rules installed?)", E_NOT_FOUND)
        try:
            self.intf, self.ep_in, self.ep_out, self.mps = self._find()
            self.lib.libusb_set_auto_detach_kernel_driver(self.h, 1)
            rc = self.lib.libusb_claim_interface(self.h, self.intf)
            if rc:
                raise UsbError(f"claim interface {self.intf}", rc)
        except Exception:
            self.lib.libusb_close(self.h)
            self.lib.libusb_exit(self.ctx)
            raise
        self.lib.libusb_clear_halt(self.h, self.ep_in)
        self.lib.libusb_clear_halt(self.h, self.ep_out)
        self.rbuf = ctypes.create_string_buffer(L.IO_CHUNK)

    def _ctrl_in(self, rtype, req, value, index, n):
        buf = ctypes.create_string_buffer(n)
        rc = self.lib.libusb_control_transfer(self.h, rtype, req, value, index, buf, n, 1000)
        if rc < 0:
            raise UsbError("control IN", rc)
        return buf.raw[:rc]

    def _find(self):
        """Walk the active config descriptor for the vendor interface (class/sub/proto)."""
        head = self._ctrl_in(0x80, 6, 0x0200, 0, 9)  # GET_DESCRIPTOR(CONFIGURATION 0)
        total = struct.unpack_from("<H", head, 2)[0]
        raw = self._ctrl_in(0x80, 6, 0x0200, 0, total)
        i, cur, eps = 0, None, {}
        while i + 2 <= len(raw):
            ln, typ = raw[i], raw[i + 1]
            if ln < 2:
                break
            if typ == 4 and ln >= 9:  # INTERFACE
                num, alt, _n, cls, sub, proto = raw[i + 2], raw[i + 3], raw[i + 4], raw[i + 5], raw[i + 6], raw[i + 7]
                cur = num if (cls, sub, proto) == (L.IF_CLASS, L.IF_SUBCLASS, L.IF_PROTOCOL) and alt == 0 else None
            elif typ == 5 and ln >= 7 and cur is not None:  # ENDPOINT
                addr, attr, mps = raw[i + 2], raw[i + 3], struct.unpack_from("<H", raw, i + 4)[0]
                if attr & 3 == 2:
                    eps["in" if addr & 0x80 else "out"] = (addr, mps & 0x7FF)
                if "in" in eps and "out" in eps:
                    return cur, eps["in"][0], eps["out"][0], eps["out"][1]
            i += ln
        raise UsbError("the car is on USB but has no 'ETK Pitlink' interface (Pitlink USB not attached on the rig)",
                       E_NOT_FOUND)

    def reset(self):
        rc = self.lib.libusb_control_transfer(self.h, 0x41, L.VREQ_RESET, 0, self.intf, None, 0, 1000)
        if rc < 0:
            raise UsbError("VREQ_RESET", rc)

    def read(self, timeout_ms=200):
        n = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.h, self.ep_in, self.rbuf, L.IO_CHUNK, ctypes.byref(n), timeout_ms)
        if rc and rc != E_TIMEOUT:
            raise UsbError("bulk IN", rc)
        return ctypes.string_at(self.rbuf, n.value)

    def write(self, data, timeout_ms=2000):
        n = ctypes.c_int(0)
        off = 0
        while off < len(data):
            chunk = data[off:off + L.IO_CHUNK]
            rc = self.lib.libusb_bulk_transfer(self.h, self.ep_out, chunk, len(chunk), ctypes.byref(n), timeout_ms)
            if rc:
                raise UsbError("bulk OUT", rc)
            off += n.value
        if len(data) % self.mps == 0:  # a ZLP ends a transfer that fills its last packet
            rc = self.lib.libusb_bulk_transfer(self.h, self.ep_out, b"", 0, ctypes.byref(n), timeout_ms)
            if rc:
                raise UsbError("bulk OUT (ZLP)", rc)

    def close(self):
        if self.h:
            self.lib.libusb_release_interface(self.h, self.intf)
            self.lib.libusb_close(self.h)
            self.lib.libusb_exit(self.ctx)
            self.h = None


def log(*a):
    print(time.strftime("%H:%M:%S"), "[pitlink-usb]", *a, file=sys.stderr, flush=True)


class Broker:
    """USB <-> HostMux, with a reconnect loop: unplug / rig reboot / daemon restart all end
    the link (every channel closes; clients reconnect) and the broker waits for the car."""

    def __init__(self, open_pipe=UsbPipe, sock_name=L.HOST_SOCKET, garage_name=L.GARAGE_SOCKET):
        self.open_pipe, self.sock_name, self.garage_name = open_pipe, sock_name, garage_name
        self.mux = L.HostMux(log=log)
        self.pipe = None
        self.stop = threading.Event()
        self.link_err = "starting"
        self.srvs = []

    def shutdown(self):
        self.stop.set()
        for srv in self.srvs:
            try:
                srv.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            srv.close()

    def serve(self):
        """PLNK clients on sock_name, garage clients on garage_name; each its own chan."""
        for name, target in ((self.sock_name, L.TARGET_PITLINK), (self.garage_name, L.TARGET_GARAGE)):
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind("\0" + name.lstrip("@"))
            srv.listen(16)
            self.srvs.append(srv)
            if target is not L.TARGET_PITLINK:
                threading.Thread(target=self._accept, args=(srv, target), daemon=True).start()
        threading.Thread(target=self._link_loop, name="usb-link", daemon=True).start()
        log(f"serving {self.sock_name} (PLNK) + {self.garage_name} (garage)")
        self._accept(self.srvs[0], L.TARGET_PITLINK)

    def _accept(self, srv, target):
        while not self.stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                break
            threading.Thread(target=self._admit, args=(c, target), daemon=True).start()

    def _admit(self, c, target):
        if not self.mux.up.wait(5.0):
            log(f"client refused: {self.link_err}")
            L._hangup(c)
            return
        self.mux.attach(c, target)

    def _link_loop(self):
        last = None
        while not self.stop.is_set():
            try:
                self.pipe = self.open_pipe()
            except OSError as e:
                self.link_err = str(e)
                if self.link_err != last:
                    log(f"waiting for the car: {e}")
                    last = self.link_err
                self.stop.wait(1.0)
                continue
            last = None
            log(f"car found: interface {self.pipe.intf}, IN 0x{self.pipe.ep_in:02x} OUT 0x{self.pipe.ep_out:02x}, "
                f"{self.pipe.mps}-byte packets")
            dead = threading.Event()
            try:
                self.pipe.reset()
                self.mux.link_start()
                rx = threading.Thread(target=self._rx, args=(dead,), name="usb-in", daemon=True)
                tx = threading.Thread(target=self._tx, args=(dead,), name="usb-out", daemon=True)
                rx.start()
                tx.start()
                if not self.mux.up.wait(3.0):
                    raise UsbError("no HELLO_ACK from the car's daemon within 3 s", E_TIMEOUT)
                while not dead.wait(2.0) and not self.stop.is_set():
                    self.mux.ping()
            except OSError as e:
                self.link_err = str(e)
                log(f"link: {e}")
            dead.set()
            self.mux.link_down(self.link_err)
            time.sleep(0.3)  # let rx/tx notice `dead`
            self.pipe.close()
            self.pipe = None
            self.stop.wait(0.5)

    def _rx(self, dead):
        while not dead.is_set():
            try:
                b = self.pipe.read(200)
            except OSError as e:
                self.link_err = str(e)
                dead.set()
                return
            if b:
                self.mux.feed(b)

    def _tx(self, dead):
        while not dead.is_set():
            try:
                b = self.mux.out.take(timeout=0.2)
            except Exception:  # queue.Empty
                continue
            try:
                self.pipe.write(b)
            except OSError as e:
                self.link_err = str(e)
                dead.set()
                return


def probe(n=20):
    pipe = UsbPipe()
    print(f"interface {pipe.intf}  IN 0x{pipe.ep_in:02x}  OUT 0x{pipe.ep_out:02x}  maxpacket {pipe.mps}")
    mux = L.HostMux()
    pipe.reset()
    mux.link_start()
    stop = threading.Event()

    def rx():
        while not stop.is_set():
            try:
                b = pipe.read(100)
            except OSError:
                return
            if b:
                mux.feed(b)
    reader = threading.Thread(target=rx, daemon=True)
    reader.start()
    pipe.write(mux.out.take(timeout=1))
    if not mux.up.wait(3):
        print("no HELLO_ACK within 3 s")
        return 1
    print(f"link up: {mux.car_status}")
    for _ in range(n):
        mux.ping()
        pipe.write(mux.out.take(timeout=1))
        time.sleep(0.05)
    time.sleep(0.2)
    stop.set()
    reader.join(1.0)
    r = sorted(mux.rtt_ms)
    if r:
        print(f"link RTT over {len(r)} pings: min {r[0]:.3f}  median {statistics.median(r):.3f}  max {r[-1]:.3f} ms")
    pipe.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--socket", default=L.HOST_SOCKET)
    p.add_argument("--garage-socket", default=L.GARAGE_SOCKET)
    sub.add_parser("probe")
    a = ap.parse_args()
    if a.cmd == "probe":
        return probe()
    Broker(sock_name=a.socket, garage_name=a.garage_socket).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
