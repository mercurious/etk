#!/usr/bin/env python3
"""Host tests for the car's side of a hunt (P3): the daemon's garage hunt ops.

  python3 tools/hunt/test_car_hunt.py [--daemon PATH]

bin/etk_pitlink_usbd.py runs against temp files (grant, Autonomy switch, gadget serial,
ETK_ROOT, debug env). docs/AUTONOMY_SPEC.md §3.3.
  [GATE]   no rig grant, expired, the car's clock behind the issue time, another car's
           serial, Autonomy off, another game: each refuses put/pin; all clear -> allowed
  [PUT]    chunks land in order, the last verifies sha256 and renames (+ .sha256, +x for a
           core); a wrong sha discards; out-of-sequence, a traversal name, an over-cap size
           refuse; an identical re-put is idempotent; different bytes never replace
  [PIN]    writes override.tsv for the grant's game; refuses a missing or tampered artifact
  [UNDO]   unpin and hunt_end work with no grant and Autonomy off; hunt_end leaves no
           artifact, no override, no debug env, no rig grant
DISCRIMINATION: --daemon <old copy> (e.g. `git show 5865a7f:bin/etk_pitlink_usbd.py`) fails
every suite: the ops do not exist.
"""
import base64
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ETK, "tools", "pitlink"))
DAEMON = os.path.join(ETK, "bin", "etk_pitlink_usbd.py")
SERIAL = "32906f627cfd1e17bcbecbc8a2f0e2cd"
CORE = "rpcs3-etk_hunt-20261011-gt6-m01_armsx3-a74a0f3e0_linux_aarch64.AppImage"


def load():
    spec = importlib.util.spec_from_file_location("etk_pitlink_usbd_under_test", DAEMON)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Car(unittest.TestCase):
    def setUp(self):
        self.D = load()
        self.td = tempfile.mkdtemp()
        j = lambda *p: os.path.join(self.td, *p)  # noqa: E731
        self.D.HUNT_GRANT_FILE, self.D.AUTONOMY_FILE = j("etk-hunt.grant"), j("etk-autonomy")
        self.D.ETK_ROOT, self.D.GADGET_SERIAL = j("etk"), j("serialnumber")
        self.D.DEBUG_ENV_FILE = j("profile.d", "099-etk-debug-env")
        os.makedirs(j("profile.d"))
        self.put_file(self.D.GADGET_SERIAL, SERIAL + "\n")
        self.grant()
        self.autonomy("on")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def put_file(self, path, body):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)

    def grant(self, **over):
        now = int(time.time())
        g = {"id": "hunt-20261011-gt6", "game": "BCUS98296", "expires_at": "x", "expires_epoch": str(now + 3600),
             "issued_epoch": str(now - 60), "usb_serial": SERIAL, "lanes": "rpcs3"}
        g.update(over)
        self.put_file(self.D.HUNT_GRANT_FILE, "".join(f"{k}={v}\n" for k, v in g.items()))

    def autonomy(self, state):
        self.put_file(self.D.AUTONOMY_FILE, state + "\n")

    def op(self, **req):
        return self.D.garage(req)

    def put(self, data, name=CORE, kind="core", chunk=4, sha=None):
        sha = sha or hashlib.sha256(data).hexdigest()
        rep = None
        for off in range(0, len(data), chunk):
            rep = self.op(op="put", kind=kind, name=name, total=len(data), sha256=sha, offset=off,
                          data=base64.b64encode(data[off:off + chunk]).decode())
        return rep

    def core_path(self, name=CORE):
        return os.path.join(self.D.ETK_ROOT, "emulators", "hunt", name)


class GateTests(Car):
    def refused(self, needle):
        with self.assertRaises(PermissionError) as cm:
            self.put(b"abcdefgh")
        self.assertIn(needle, str(cm.exception))
        self.assertFalse(os.path.exists(self.core_path()))

    def test_allowed(self):
        self.assertEqual(self.op(op="hunt_status")["refusals"], [])

    def test_no_grant(self):
        os.remove(self.D.HUNT_GRANT_FILE)
        self.refused("no rig grant")

    def test_expired(self):
        self.grant(expires_epoch=str(int(time.time()) - 1))
        self.refused("expired")

    def test_clock_behind(self):
        self.grant(issued_epoch=str(int(time.time()) + 3600), expires_epoch=str(int(time.time()) + 7200))
        self.refused("clock is behind")

    def test_other_car(self):
        self.grant(usb_serial="ffff")
        self.refused("another car")

    def test_autonomy_off(self):
        self.autonomy("off")
        self.refused("Autonomy is off")
        os.remove(self.D.AUTONOMY_FILE)
        self.refused("Autonomy is off")

    def test_other_game(self):
        self.put(b"abcdefgh")
        with self.assertRaises(PermissionError) as cm:
            self.op(op="pin", game="NPEA00050", core=CORE)
        self.assertIn("outside the grant", str(cm.exception))


class PutTests(Car):
    def test_chunks(self):
        rep = self.put(b"0123456789", chunk=3)
        self.assertTrue(rep["done"])
        with open(self.core_path(), "rb") as f:
            self.assertEqual(f.read(), b"0123456789")
        with open(self.core_path() + ".sha256") as f:
            self.assertEqual(f.read().split()[0], hashlib.sha256(b"0123456789").hexdigest())
        self.assertTrue(os.access(self.core_path(), os.X_OK))
        self.assertEqual([x["name"] for x in self.op(op="hunt_status")["files"]["core"]], [CORE])

    def test_wrong_sha(self):
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            self.put(b"0123456789", sha="0" * 64)
        self.assertEqual(os.listdir(os.path.dirname(self.core_path())), [])

    def test_out_of_sequence(self):
        with self.assertRaisesRegex(ValueError, "out of sequence"):
            self.op(op="put", kind="core", name=CORE, total=8, sha256="0" * 64, offset=4,
                    data=base64.b64encode(b"abcd").decode())

    def test_bad_names(self):
        for kind, name in (("core", "../../rpcs3-sa.custom"), ("core", "rpcs3-etk_gtk-edition-0.10.0.AppImage"),
                           ("driver", "libvulkan_freedreno.so"), ("kernel", "KERNEL")):
            with self.assertRaisesRegex(ValueError, "not a hunt artifact name", msg=name):
                self.put(b"abcd", name=name, kind=kind)

    def test_over_cap(self):
        with self.assertRaisesRegex(ValueError, "out of bounds"):
            self.op(op="put", kind="driver", name="etk_turnip_hunt-x.so", total=(128 << 20) + 1,
                    sha256="0" * 64, offset=0, data="")

    def test_never_replaced(self):
        self.put(b"abcdefgh")
        self.assertTrue(self.put(b"abcdefgh")["already"])
        with self.assertRaises(FileExistsError):
            self.put(b"ABCDEFGH")


class PinTests(Car):
    def test_pin_and_status(self):
        self.put(b"abcdefgh")
        rep = self.op(op="pin", game="BCUS98296", core=CORE)
        self.assertEqual(rep["override"], {"BCUS98296": {"core": CORE, "driver": "-"}})
        with open(os.path.join(self.D.ETK_ROOT, "emulators", "hunt", "override.tsv")) as f:
            self.assertIn(f"BCUS98296\t{CORE}\t-\n", f.read())

    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.op(op="pin", game="BCUS98296", core=CORE)

    def test_tampered(self):
        self.put(b"abcdefgh")
        with open(self.core_path(), "wb") as f:
            f.write(b"evil!!!!")
        with self.assertRaises(ValueError):
            self.op(op="pin", game="BCUS98296", core=CORE)


class UndoTests(Car):
    def test_unpin_without_grant(self):
        self.put(b"abcdefgh")
        self.op(op="pin", game="BCUS98296", core=CORE)
        os.remove(self.D.HUNT_GRANT_FILE)
        self.autonomy("off")
        self.assertEqual(self.op(op="unpin", game="BCUS98296")["override"], {})

    def test_end(self):
        self.put(b"abcdefgh")
        self.put(b"drv", name="etk_turnip_hunt-26.2.3-x.so", kind="driver")
        self.op(op="pin", game="BCUS98296", core=CORE, driver="etk_turnip_hunt-26.2.3-x.so")
        self.op(op="debug_env", action="set", ARMSX3_PPU_INTERP="10000-20000")
        self.autonomy("off")
        self.op(op="hunt_end")
        for kind in ("emulators", "drivers"):
            self.assertEqual(os.listdir(os.path.join(self.D.ETK_ROOT, kind, "hunt")), [])
        self.assertFalse(os.path.exists(self.D.HUNT_GRANT_FILE))
        self.assertFalse(os.path.exists(self.D.DEBUG_ENV_FILE))


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        k = sys.argv.index("--daemon")
        DAEMON = os.path.abspath(sys.argv[k + 1])
        del sys.argv[k:k + 2]
    unittest.main(verbosity=1)
