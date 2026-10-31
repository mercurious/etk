#!/usr/bin/env python3
"""Host tests for the hunt grant (P1, docs/AUTONOMY_SPEC.md §10). No sudo, no network, no rig.

  python3 tools/hunt/test_hunt.py

  [GRANT]   load_grant: a valid grant passes; expired, over 12 h, future-issued, wrong owner,
            group-writable, symlinked, writable parent, bad JSON, kernel lane, an overnight
            grant with no verified reserve, a tampered node fingerprint each fail.
  [FREE]    the always-free judge: etk-cloud's A1 4/24/150 GiB passes; 6 OCPU, 32 GB, an E4
            shape or a 250 GiB disk each fail; any resize changes the fingerprint.
  [AUDIT]   the chain verifies; an edited, dropped or reordered line, or a log from another
            grant, is caught at the right line.
  [ISSUE]   grant.sh issue with probes stubbed: refuses without the guard hook, a non-free node, a car check failure, a
            hunt car not on the host USB, an unverified reserve without --supervised, a second
            grant, a wrong typed id, an installed file that differs from the one read; signs
            a supervised grant and an overnight one; the rig mirror carries the expiry.
  [HUNT]    hunt.py: status/check without a grant; mint refused without one; with a grant it
            audits the attempt and exits 3 (not built); a broken chain refuses.
  [GUARD]   the PreToolUse decision table (atoms, publish, power, hunt.py, freeze).
  [MUTANT]  each suite fails against a do-nothing implementation (permit-all guard, a
            validator that finds no problems), so the tests discriminate.
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grantlib as gl  # noqa: E402
import grantctl  # noqa: E402
import guard  # noqa: E402
import hunt  # noqa: E402

UID = os.getuid()
NOW = gl.parse_iso("2026-10-11T02:00:00Z")
META = {"shape": "VM.Standard.A1.Flex", "shapeConfig": {"ocpus": 4.0, "memoryInGBs": 24.0},
        "image": "ocid1.image.oc1.iad.x"}
FP = gl.node_fingerprint(META, {"arch": "aarch64", "disk_bytes": 161061273600})
SERIAL = "32906f627cfd1e17bcbecbc8a2f0e2cd"


def grant(**over):
    g = {
        "schema": gl.SCHEMA, "id": "hunt-20261011-gt6", "issued_at": "2026-10-11T01:00:00Z",
        "expires_at": "2026-10-11T11:00:00Z", "hours": 10, "game": "BCUS98296", "mode": "overnight",
        "rig": {"car": "car8", "ssh": "root@SM8250.local", "usb_serial": SERIAL},
        "reserve": {"car": "car12", "verified": True},
        "node": {"host": "etk-cloud", "fingerprint": dict(FP), "sha": gl.fingerprint_sha(FP)},
        "mint": {"lanes": ["rpcs3"], "one_at_a_time": True, "budget": "hours"},
        "inject": list(gl.INJECT), "never": list(gl.NEVER),
    }
    g.update(over)
    return g


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.chmod(self.d, 0o755)
        self.path = os.path.join(self.d, "hunt.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, g, mode=0o644):
        with open(self.path, "w") as f:
            f.write(g if isinstance(g, str) else json.dumps(g))
        os.chmod(self.path, mode)

    def problems(self, now=NOW):
        return gl.load_grant(self.path, UID, now)[2]


class GrantTests(Tmp):
    def test_valid(self):
        self.write(grant())
        self.assertEqual(self.problems(), [])

    def test_absent(self):
        self.assertEqual(self.problems(), ["no grant"])

    def test_expired(self):
        self.write(grant())
        self.assertTrue(any("expired" in p for p in self.problems(now=gl.parse_iso("2026-10-11T11:00:00Z"))))

    def test_over_cap(self):
        self.write(grant(expires_at="2026-10-11T13:30:00Z"))
        self.assertTrue(any("cap" in p for p in self.problems()))

    def test_future(self):
        self.write(grant(issued_at="2026-10-11T03:00:00Z"))
        self.assertTrue(any("future" in p for p in self.problems()))

    def test_wrong_owner(self):
        self.write(grant())
        self.assertTrue(any("owned by uid" in p for p in gl.load_grant(self.path, UID + 1, NOW)[2]))

    def test_group_writable(self):
        self.write(grant(), 0o664)
        self.assertTrue(any("writable" in p for p in self.problems()))

    def test_symlink(self):
        real = os.path.join(self.d, "real.json")
        with open(real, "w") as f:
            json.dump(grant(), f)
        os.symlink(real, self.path)
        self.assertEqual(self.problems(), ["grant is not a regular file"])

    def test_panics_field(self):
        self.write(grant(panics=3))
        self.assertEqual(self.problems(), [])
        for bad in (7, -1, True, "3", 2.0):
            self.write(grant(panics=bad))
            self.assertTrue(any("panics" in p for p in self.problems()), bad)

    def test_writable_parent(self):
        self.write(grant())
        os.chmod(self.d, 0o777)
        self.assertTrue(any("directory" in p for p in self.problems()))

    def test_bad_json(self):
        self.write("{not json")
        self.assertTrue(any("unreadable" in p for p in self.problems()))

    def test_kernel_lane(self):
        self.write(grant(mint={"lanes": ["rpcs3", "kernel"]}))
        self.assertTrue(any("lanes" in p for p in self.problems()))

    def test_overnight_needs_reserve(self):
        self.write(grant(reserve={"car": "car12", "verified": False}))
        self.assertTrue(any("reserve" in p for p in self.problems()))
        self.write(grant(mode="supervised", reserve={"car": "car12", "verified": False}))
        self.assertEqual(self.problems(), [])

    def test_a_car_can_have_any_name(self):
        self.write(grant(rig={"car": "red-flip_2", "ssh": "root@x", "usb_serial": SERIAL}))
        self.assertEqual(self.problems(), [])
        for bad in ("a b", "../x", "-x", "a;b", "x" * 33):
            self.write(grant(rig={"car": bad, "ssh": "root@x", "usb_serial": SERIAL}))
            self.assertTrue(any("rig must name" in p for p in self.problems()), bad)

    def test_reserve_is_not_the_hunt_car(self):
        self.write(grant(reserve={"car": "car8", "verified": True}))
        self.assertTrue(any("reserve is the hunt car" in p for p in self.problems()))

    def test_node_tamper(self):
        fp = dict(FP, ocpus=8.0)
        self.write(grant(node={"host": "etk-cloud", "fingerprint": fp, "sha": gl.fingerprint_sha(FP)}))
        self.assertTrue(any("fingerprint" in p for p in self.problems()))

    def test_scope(self):
        g = grant()
        self.assertEqual(gl.in_scope(g, "BCUS98296", "rpcs3"), [])
        self.assertTrue(gl.in_scope(g, "NPEA00050"))
        self.assertTrue(gl.in_scope(g, lane="turnip"))


class FreeTests(unittest.TestCase):
    def test_etk_cloud_is_free(self):
        self.assertEqual(gl.always_free(FP), [])

    def test_not_free(self):
        for over in ({"ocpus": 6.0}, {"memory_gb": 32.0}, {"shape": "VM.Standard.E4.Flex"},
                     {"disk_bytes": 250 * 1024 ** 3}):
            self.assertTrue(gl.always_free(dict(FP, **over)), over)

    def test_fingerprint_moves(self):
        self.assertNotEqual(gl.fingerprint_sha(FP), gl.fingerprint_sha(dict(FP, memory_gb=12.0)))
        self.assertEqual(gl.fingerprint_sha(FP), gl.fingerprint_sha(dict(FP)))


class AuditTests(Tmp):
    def log(self, n=4):
        ap = os.path.join(self.d, "audit.jsonl")
        for k in range(n):
            gl.audit_append(ap, "g" * 64, f"a{k}", [k], "ok", NOW + k)
        with open(ap) as f:
            return ap, f.read().splitlines()

    def test_intact(self):
        ap, _ = self.log()
        e, p = gl.audit_verify(ap, "g" * 64)
        self.assertEqual((len(e), p), (4, []))

    def rewrite(self, ap, lines):
        with open(ap, "w") as f:
            f.write("\n".join(lines) + "\n")

    def test_edit(self):
        ap, lines = self.log()
        lines[2] = lines[2].replace('"ok"', '"PASS"')
        self.rewrite(ap, lines)
        self.assertIn("line 2", gl.audit_verify(ap, "g" * 64)[1][0])

    def test_drop(self):
        ap, lines = self.log()
        del lines[1]
        self.rewrite(ap, lines)
        self.assertIn("line 1", gl.audit_verify(ap, "g" * 64)[1][0])

    def test_reorder(self):
        ap, lines = self.log()
        lines[1], lines[2] = lines[2], lines[1]
        self.rewrite(ap, lines)
        self.assertTrue(gl.audit_verify(ap, "g" * 64)[1])

    def test_other_grant(self):
        ap, _ = self.log()
        self.assertIn("line 0", gl.audit_verify(ap, "h" * 64)[1][0])


def args(**kw):
    a = dict(game="BCUS98296", hours=10.0, lanes=["rpcs3"], name="gt6", rig="car8", reserve="car12",
             supervised=False, auto_mode=False, panics=0)
    a.update(kw)
    return argparse.Namespace(**a)


class IssueTests(Tmp):
    def setUp(self):
        super().setUp()
        self.gp = os.path.join(self.d, "grants", "hunt.json")
        os.makedirs(os.path.dirname(self.gp), mode=0o755)
        self.rig_bodies = []
        self.car = {"car8": {"ok": True, "message": "CAR CHECK: car8 verified", "usb_serial": SERIAL,
                             "booted_at": "2026-10-10T20:00:00Z", "pitstop": "/storage/x/etk_pitstop.py"},
                    "car12": {"ok": True, "message": "CAR CHECK: car12 verified", "usb_serial": "other",
                              "booted_at": "2026-10-10T21:00:00Z", "pitstop": "/storage/y/etk_pitstop.py"}}
        self.fp = dict(FP)
        self.usb = [SERIAL]
        self.guard = "/home/dave/etk/.claude/settings.local.json"

    def probes(self):
        return {"conf": {"CAR": "car12", "CAR8_SSH": "root@SM8250.local", "CAR12_SSH": "root@192.168.1.5"},
                "node": lambda h: self.fp, "car": lambda t, c: self.car[c], "usb": lambda: self.usb,
                "tools": lambda: {"tools_hunt": "abc1234", "clean": True}, "guard": lambda: self.guard}

    def signer(self, tamper=False):
        def install(staged):
            with open(staged, "rb") as f:
                raw = f.read()
            with open(self.gp, "wb") as f:
                f.write(raw.replace(b'"rpcs3"', b'"turnip"') if tamper else raw)
            os.chmod(self.gp, 0o644)
            return True
        return {"install": install, "remove": lambda: os.remove(self.gp) or True,
                "rig_write": lambda t, b: self.rig_bodies.append(b) or True}

    def issue(self, a=None, typed=None, tamper=False):
        out = []
        g = grantctl.issue(a or args(), self.probes(),
                           lambda p: typed if typed is not None else re.search(r"\((hunt-[^)]+)\)", p).group(1),
                           self.signer(tamper), out.append, NOW, self.gp, UID)
        return g, "\n".join(out)

    def refused(self, needle, **kw):
        with self.assertRaises(grantctl.Refused) as cm:
            self.issue(**kw)
        self.assertIn(needle, str(cm.exception))
        self.assertFalse(os.path.exists(self.gp), "a refused issue must leave no grant")

    def test_overnight(self):
        g, text = self.issue()
        self.assertEqual(g["mode"], "overnight")
        self.assertEqual(g["id"], f"hunt-{dt.datetime.fromtimestamp(NOW):%Y%m%d}-gt6")  # local, not UTC
        self.assertEqual(gl.load_grant(self.gp, UID, NOW)[2], [])
        self.assertIn("inside always-free", text)
        self.assertIn("expires_at=2026-10-11T12:00:00Z", self.rig_bodies[0])
        self.assertIn(f"issued_epoch={int(NOW)}", self.rig_bodies[0])   # the car judges its own clock
        e, p = gl.audit_verify(os.path.join(self.d, "grants", g["id"], "audit.jsonl"),
                               gl.sha256(gl.load_grant(self.gp, UID, NOW)[1]))
        self.assertEqual((p, e[0]["action"]), ([], "issued"))

    def test_no_guard(self):
        self.guard = None
        self.refused("guard")

    def test_not_free(self):
        self.fp = dict(FP, ocpus=8.0)
        self.refused("always-free")

    def test_car_check(self):
        self.car["car8"] = dict(self.car["car8"], ok=False, message="CAR CHECK REFUSED: 12 GB unit")
        self.refused("car check failed")

    def test_not_on_usb(self):
        self.usb = ["someone-else"]
        self.refused("plug car8")

    def test_reserve_unverified(self):
        self.car["car12"] = dict(self.car["car12"], ok=False, message="cannot read the unit")
        self.refused("--supervised")
        g, text = self.issue(a=args(supervised=True))
        self.assertEqual((g["mode"], g["reserve"]["verified"]), ("supervised", False))
        self.assertIn("SUPERVISED", text)

    def front_door(self):
        self.car["car12"] = dict(self.car["car12"], usb_serial=SERIAL)
        return args(rig=None, reserve=None, auto_mode=True)

    def test_front_door_one_car_in_service(self):
        # car8 out of commission (operator 2026-10-31): etk.conf picks car12, the failed reserve check picks supervised
        a = self.front_door()
        self.car["car8"] = dict(self.car["car8"], ok=False, message="cannot read the unit", pitstop=None)
        g, text = self.issue(a=a)
        self.assertEqual((g["rig"]["car"], g["reserve"]["car"], g["mode"], g["reserve"]["verified"]),
                         ("car12", "car8", "supervised", False))
        self.assertIn("SUPERVISED", text)

    def test_front_door_reserve_back(self):
        # no code changes when car8 returns: a reserve that verifies is an overnight grant again
        g, _ = self.issue(a=self.front_door())
        self.assertEqual((g["mode"], g["reserve"]["verified"]), ("overnight", True))

    def test_cars_come_from_etk_conf(self):
        c = {"CAR": "car12", "CAR8_SSH": "a", "CAR12_SSH": "b"}
        self.assertEqual(grantctl.hunt_cars(c), ("car12", "car8"))
        self.assertEqual(grantctl.hunt_cars(dict(c, CAR="car8")), ("car8", "car12"))
        self.assertEqual(grantctl.hunt_cars(c, "car8"), ("car8", "car12"))
        self.assertEqual(grantctl.hunt_cars(c, "car8", "car9"), ("car8", "car9"))
        free = {"CAR": "red", "CARS": "red,root@a,8 blue,root@b,8"}
        self.assertEqual(grantctl.hunt_cars(free), ("red", "blue"))
        self.assertEqual(grantctl.etkconf.car_ssh(free, "blue"), "root@b")
        for bad, why in (({"CAR8_SSH": "a"}, "no CAR="), ({"CAR": "car12", "CAR12_SSH": "b"}, "--reserve")):
            with self.assertRaises(grantctl.Refused) as cm:
                grantctl.hunt_cars(bad)
            self.assertIn(why, str(cm.exception))

    def test_reserve_without_pitstop(self):
        self.car["car12"] = dict(self.car["car12"], pitstop=None)
        self.refused("no Pitstop")

    def test_one_at_a_time(self):
        self.issue()
        with self.assertRaises(grantctl.Refused) as cm:
            self.issue()
        self.assertIn("revoke it first", str(cm.exception))

    def revoke(self, tty):
        removed, rigs, out = [], [], []
        signer = dict(self.signer(), remove=lambda: removed.append(1) or True,
                      rig_remove=lambda t: rigs.append(t) or True)
        grantctl.revoke(self.probes()["conf"], signer, out.append, self.gp, UID, tty=tty)
        return removed, rigs, "\n".join(out)

    def test_revoke_needs_no_sudo(self):
        # the Run button / `!` prompt has no terminal: sudo cannot ask, so revoke must not need it
        self.issue()
        removed, rigs, text = self.revoke(tty=False)
        self.assertEqual(removed, [])
        self.assertEqual(len(rigs), 1)
        self.assertIn("revoked", text)
        self.assertEqual(gl.load_grant(self.gp, UID, NOW)[2], ["revoked"])

    def test_revoke_tidies_with_a_terminal(self):
        self.issue()
        removed, _, _ = self.revoke(tty=True)
        self.assertEqual(removed, [1])

    def test_revoke_without_a_grant_reaches_the_hunt_car(self):
        _, rigs, _ = self.revoke(tty=False)
        self.assertEqual(rigs, ["root@192.168.1.5"])

    def test_reissue_after_revoke(self):
        self.issue()
        self.revoke(tty=False)
        sign = lambda p: re.search(r"\((hunt-[^)]+)\)", p).group(1)  # noqa: E731
        g = grantctl.issue(args(), self.probes(), sign, self.signer(), lambda *_: None, NOW + 60, self.gp, UID)
        self.assertEqual(gl.load_grant(self.gp, UID, NOW + 60)[2], [], g["id"])  # same id, new sha: not revoked

    def test_wrong_id(self):
        self.refused("not signed", typed="yes")

    def test_swapped_file(self):
        self.refused("not the one you read", tamper=True)

    def test_panics(self):
        g, text = self.issue(a=args(panics=3))
        self.assertEqual(g["panics"], 3)
        self.assertIn("rides through up to 3 kernel panic", text)
        self.assertEqual(gl.load_grant(self.gp, UID, NOW)[2], [])

    def test_panics_default_stops(self):
        g, text = self.issue()
        self.assertEqual(g["panics"], 0)
        self.assertIn("STOPS at the first kernel panic", text)

    def test_bad_args(self):
        for kw, needle in (({"hours": 13}, "--hours"), ({"lanes": ["kernel"]}, "--lanes"),
                           ({"game": "gt6"}, "PS3 serial"), ({"reserve": "car8"}, "reserve car"),
                           ({"panics": 7}, "--panics"), ({"panics": -1}, "--panics")):
            self.refused(needle, a=args(**kw))


class ProbeTests(Tmp):
    def test_node_probe_parses_pretty_json(self):
        meta = json.dumps(dict(META, shapeConfig={"ocpus": 4.0, "memoryInGBs": 24.0}), indent=2)
        real = grantctl.run
        grantctl.run = lambda cmd, **kw: (0, meta + "\n@@ETK@@\narch=aarch64\ndisk=161061273600\n", "")
        try:
            self.assertEqual(grantctl.probe_node("etk-cloud"), FP)
        finally:
            grantctl.run = real

    def test_node_probe_without_metadata(self):
        real = grantctl.run
        grantctl.run = lambda cmd, **kw: (0, "\n@@ETK@@\narch=aarch64\ndisk=1\n", "")
        try:
            with self.assertRaises(grantctl.Refused):
                grantctl.probe_node("etk-cloud")
        finally:
            grantctl.run = real

    def test_usb_serials(self):
        for name, files in (("1-1.1", {"idVendor": "1d6b", "idProduct": "0104", "manufacturer": "ROCKNIX",
                                       "serial": SERIAL}),
                            ("1-1.2", {"idVendor": "1d6b", "idProduct": "0104", "manufacturer": "Linux",
                                       "serial": "x"}),
                            ("1-2", {"idVendor": "05ac", "idProduct": "0104", "manufacturer": "ROCKNIX",
                                     "serial": "y"})):
            os.makedirs(os.path.join(self.d, name))
            for k, v in files.items():
                with open(os.path.join(self.d, name, k), "w") as f:
                    f.write(v + "\n")
        self.assertEqual(grantctl.host_usb_serials(self.d), [SERIAL])


class HuntTests(Tmp):
    def run_hunt(self, *argv):
        out = []
        rc = hunt.main(list(argv), out.append, self.path, UID, NOW)
        return rc, "\n".join(out)

    def test_no_grant(self):
        self.assertEqual(self.run_hunt("status")[0], 1)
        self.assertEqual(self.run_hunt("check")[0], 1)
        rc, text = self.run_hunt("mint", "--base", "abc1234")
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", text)

    def test_with_grant(self):
        self.write(grant())
        raw = gl.load_grant(self.path, UID, NOW)[1]
        ap = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(ap, gl.sha256(raw), "issued", [], "ok", NOW)
        self.assertEqual(self.run_hunt("check", "--game", "BCUS98296", "--lane", "rpcs3")[0], 0)
        self.assertEqual(self.run_hunt("check", "--lane", "turnip")[0], 1)
        rc, text = self.run_hunt("trial", "--label", "x")
        self.assertEqual(rc, 3)
        self.assertIn("P4", text)
        e, p = gl.audit_verify(ap, gl.sha256(raw))
        self.assertEqual((p, [x["action"] for x in e]), ([], ["issued", "trial"]))
        rc, text = self.run_hunt("status")
        self.assertEqual(rc, 0)
        self.assertIn("chain intact", text)

    def test_panic_budget(self):
        self.write(grant(game="BCUS98114", panics=2))
        raw = gl.load_grant(self.path, UID, NOW)[1]
        ap = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(ap, gl.sha256(raw), "issued", [], "ok", NOW)
        rc, text = self.run_hunt("check", "--game", "BCUS98114", "--panic")
        self.assertEqual((rc, "2 of 2 left" in text), (0, True))
        self.assertEqual(self.run_hunt("panic", "--trial", "t/1", "--pon", "SOC_RESET")[0], 0)
        rc, text = self.run_hunt("check", "--game", "BCUS98114", "--panic")
        self.assertEqual((rc, "1 of 2 left" in text), (0, True))
        rc, text = self.run_hunt("panic", "--trial", "t/2", "--pon", "SOC_RESET")
        self.assertIn("budget SPENT", text)
        rc, text = self.run_hunt("check", "--game", "BCUS98114", "--panic")
        self.assertEqual((rc, "panic budget spent (2 of 2)" in text), (1, True))
        self.assertEqual(self.run_hunt("check", "--game", "BCUS98114")[0], 0)   # non-panicking work goes on
        self.assertIn("panics    2 of 2", self.run_hunt("status")[1])
        e, p = gl.audit_verify(ap, gl.sha256(raw))
        self.assertEqual((p, [x["action"] for x in e]), ([], ["issued", "panic", "panic"]))
        self.assertEqual(e[1]["args"], {"trial": "t/1", "pon": "SOC_RESET"})

    def test_panic_needs_budget(self):
        self.write(grant(game="BCUS98114"))           # an existing grant: no panics field = 0
        raw = gl.load_grant(self.path, UID, NOW)[1]
        gl.audit_append(os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl"), gl.sha256(raw), "issued", [], "ok", NOW)
        rc, text = self.run_hunt("check", "--game", "BCUS98114", "--panic")
        self.assertEqual((rc, "0 of 0" in text), (1, True))

    def test_panic_recorded_after_expiry(self):
        self.write(grant(game="BCUS98114", panics=1))
        raw = gl.load_grant(self.path, UID, NOW)[1]
        ap = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(ap, gl.sha256(raw), "issued", [], "ok", NOW)
        out = []
        rc = hunt.main(["panic", "--trial", "t/9", "--pon", "SOC_RESET"], out.append, self.path, UID,
                       gl.parse_iso("2026-10-11T11:30:00Z"))
        self.assertEqual(rc, 0)
        e, _ = gl.audit_verify(ap, gl.sha256(raw))
        self.assertIn("expired", e[-1]["result"]["grant"])

    def test_broken_chain_refuses(self):
        self.write(grant())
        ap = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(ap, "wrong" * 8, "issued", [], "ok", NOW)
        rc, text = self.run_hunt("pin", "m01")
        self.assertEqual(rc, 1)
        self.assertIn("chain is broken", text)


FAKE_FORGE = r"""#!/usr/bin/env python3
import json, os, sys
rec = {"argv": sys.argv[1:], "env": {k: v for k, v in os.environ.items() if k.startswith("HUNT_")}}
rec["patch"] = open(os.environ["HUNT_PATCH"]).read()
rec["scripts"] = sorted(os.listdir(os.path.join(os.environ["HUNT_FORK"], "scripts")))
json.dump(rec, open(os.environ["FAKE_RECORD"], "w"))
print("fake forge:", " ".join(sys.argv[1:]))
rc = int(os.environ.get("FAKE_RC", "0"))
if rc == 0 and "--dry-run" not in sys.argv:
    os.makedirs(os.environ["FAKE_STAGE"], exist_ok=True)
    open(os.path.join(os.environ["FAKE_STAGE"], os.environ["HUNT_ARTIFACT"]), "wb").write(b"appimage")
sys.exit(rc)
"""


class MintTests(Tmp):
    """hunt.py mint (P2): committed branch inputs -> forge.sh --hunt env -> audit."""

    def setUp(self):
        super().setUp()
        import subprocess
        self.sp = subprocess
        self.fork = os.path.join(self.d, "fork")
        self.stage = os.path.join(self.d, "stage")
        os.makedirs(os.path.join(self.fork, "patches"))
        os.makedirs(os.path.join(self.fork, "scripts"))
        for rel, body in (("patches/gt6.patch", "COMMITTED PATCH\n"), ("scripts/package-appimage.sh", "pkg\n"),
                          ("scripts/verify-markers.sh", "vm\n")):
            with open(os.path.join(self.fork, rel), "w") as f:
                f.write(body)
        g = ["git", "-C", self.fork, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        self.sp.run(g[:3] + ["init", "-q", "-b", "main"], check=True)
        self.sp.run(g + ["add", "-A"], check=True)
        self.sp.run(g + ["commit", "-q", "--no-verify", "-m", "x"], check=True)
        self.sp.run(g[:3] + ["branch", "hunt/hunt-20261011-gt6"], check=True)
        with open(os.path.join(self.fork, "patches/gt6.patch"), "w") as f:
            f.write("UNCOMMITTED EDIT\n")             # the working tree must never be minted
        fake = os.path.join(self.d, "forge.sh")
        with open(fake, "w") as f:
            f.write(FAKE_FORGE)
        os.chmod(fake, 0o755)
        self.record = os.path.join(self.d, "record.json")
        os.environ.update(FAKE_RECORD=self.record, FAKE_STAGE=self.stage, FAKE_RC="0")
        self.saved = (hunt.FORGE, hunt.HUNT_STAGE, hunt.fork_dir)
        hunt.FORGE, hunt.HUNT_STAGE, hunt.fork_dir = [fake], self.stage, lambda: self.fork
        self.write(grant())
        self.raw = gl.load_grant(self.path, UID, NOW)[1]
        self.audit = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(self.audit, gl.sha256(self.raw), "issued", [], "ok", NOW)

    def tearDown(self):
        hunt.FORGE, hunt.HUNT_STAGE, hunt.fork_dir = self.saved
        for k in ("FAKE_RECORD", "FAKE_STAGE", "FAKE_RC"):
            os.environ.pop(k, None)
        super().tearDown()

    def mint(self, *argv):
        out = []
        rc = hunt.main(["mint", *argv], out.append, self.path, UID, NOW)
        return rc, "\n".join(out)

    def actions(self):
        e, p = gl.audit_verify(self.audit, gl.sha256(self.raw))
        self.assertEqual(p, [])
        return e

    def test_minted(self):
        rc, text = self.mint("--base", "a74a0f3e0aa", "--label", "endpoint good")
        self.assertEqual(rc, 0, text)
        with open(self.record) as f:
            rec = json.load(f)
        self.assertEqual(rec["argv"], ["--hunt", "hunt-20261011-gt6", "rpcs3", "--verbose"])
        self.assertEqual(rec["patch"], "COMMITTED PATCH\n")
        self.assertEqual(rec["scripts"], ["package-appimage.sh", "verify-markers.sh"])
        art = "rpcs3-etk_hunt-20261011-gt6-m01_armsx3-a74a0f3e0_linux_aarch64.AppImage"
        self.assertEqual((rec["env"]["HUNT_BASE"], rec["env"]["HUNT_ARTIFACT"]), ("a74a0f3e0aa", art))
        e = self.actions()
        self.assertEqual([x["action"] for x in e], ["issued", "mint", "minted"])
        self.assertEqual(e[1]["args"]["patch_sha"], gl.sha256(b"COMMITTED PATCH\n"))
        self.assertEqual(e[2]["result"]["sha256"], gl.sha256(b"appimage"))
        rc, _ = self.mint("--base", "8290349e5")
        self.assertIn("m02_armsx3-8290349e5", self.actions()[-1]["result"]["artifact"])

    def test_forge_fails(self):
        os.environ["FAKE_RC"] = "1"
        rc, text = self.mint("--base", "a74a0f3e0")
        self.assertEqual(rc, 1)
        self.assertEqual(self.actions()[-1]["action"], "mint failed")
        self.assertIn("fake forge", self.actions()[-1]["result"]["tail"])

    def test_dry_run(self):
        rc, _ = self.mint("--base", "a74a0f3e0", "--dry-run")
        self.assertEqual(rc, 0)
        with open(self.record) as f:
            self.assertIn("--dry-run", json.load(f)["argv"])
        self.assertEqual(self.actions()[-1]["action"], "mint dry-run")

    def test_refusals(self):
        self.assertEqual(self.mint("--base", "not-a-sha")[0], 1)
        self.sp.run(["git", "-C", self.fork, "branch", "-q", "-m", "hunt/hunt-20261011-gt6", "hunt/other"], check=True)
        rc, text = self.mint("--base", "a74a0f3e0")
        self.assertEqual(rc, 1)
        self.assertIn("refused", self.actions()[-1]["result"])
        self.assertFalse(os.path.exists(self.record), "forge must not run on a refusal")

    def test_unsafe_names(self):
        self.assertEqual(self.mint("--base", "a74a0f3e0", "--patch", "patches/x;reboot.patch")[0], 1)
        self.assertIn("must be patches/", self.actions()[-1]["result"]["refused"])
        self.assertEqual(self.mint("--base", "a74a0f3e0", "--marker", "a b")[0], 1)
        self.assertFalse(os.path.exists(self.record))

    def test_lane_not_granted(self):
        self.write(grant(mint={"lanes": ["turnip"], "one_at_a_time": True, "budget": "hours"}))
        self.raw = gl.load_grant(self.path, UID, NOW)[1]
        os.remove(self.audit)
        gl.audit_append(self.audit, gl.sha256(self.raw), "issued", [], "ok", NOW)
        rc, text = self.mint("--base", "a74a0f3e0")
        self.assertEqual(rc, 1)
        self.assertIn("lane rpcs3 is not granted", text)

    def test_one_at_a_time(self):
        import fcntl
        os.makedirs(os.path.dirname(self.audit), exist_ok=True)
        held = open(os.path.join(os.path.dirname(self.audit), "mint.lock"), "w")
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            rc, text = self.mint("--base", "a74a0f3e0")
        finally:
            held.close()
        self.assertEqual(rc, 1)
        self.assertIn("one at a time", text)


class FakeGarage:
    """Stands in for client.GarageSession: records requests, answers like the car daemon."""
    def __init__(self, log, fail=None):
        self.log, self.fail, self.data = log, fail, bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *e):
        pass

    def call(self, req, timeout=None):
        import base64
        if self.fail and req["op"] == self.fail:
            raise RuntimeError("garage put: hunt refused at the car: Pitstop TOOLS -> Autonomy is off")
        self.log.append({k: v for k, v in req.items() if k != "data"})
        if req["op"] == "put":
            self.data += base64.b64decode(req["data"])
            return {"ok": True, "received": len(self.data), "done": len(self.data) == req["total"]}
        if req["op"] == "pin":
            return {"ok": True, "override": {req["game"]: {"core": req["core"], "driver": "-"}}}
        if req["op"] == "hunt_end":
            return {"ok": True, "removed": ["emulators/hunt/x"]}
        return {"ok": True, "override": {}}


class CarTests(Tmp):
    """hunt.py put / pin / unpin / rollback / end (P3) against a fake garage."""

    def setUp(self):
        super().setUp()
        self.stage = os.path.join(self.d, "stage")
        os.makedirs(self.stage)
        self.art = "rpcs3-etk_hunt-20261011-gt6-m01_armsx3-a74a0f3e0_linux_aarch64.AppImage"
        self.blob = os.urandom(2 * hunt.PUT_CHUNK + 123)
        with open(os.path.join(self.stage, self.art), "wb") as f:
            f.write(self.blob)
        self.calls, self.fail = [], None
        self.saved = (hunt.HUNT_STAGE, hunt.garage_session)
        hunt.HUNT_STAGE = self.stage
        self.garages = []

        def fake(timeout):
            g = FakeGarage(self.calls, self.fail)
            self.garages.append(g)
            return g
        hunt.garage_session = fake
        self.write(grant())
        self.raw = gl.load_grant(self.path, UID, NOW)[1]
        self.audit = os.path.join(self.d, "hunt-20261011-gt6", "audit.jsonl")
        gl.audit_append(self.audit, gl.sha256(self.raw), "issued", [], "ok", NOW)
        gl.audit_append(self.audit, gl.sha256(self.raw), "minted", {"m": 1, "base": "a74a0f3e0"},
                        {"artifact": self.art, "sha256": gl.sha256(self.blob)}, NOW)

    def tearDown(self):
        hunt.HUNT_STAGE, hunt.garage_session = self.saved
        super().tearDown()

    def run_hunt(self, *argv, now=NOW):
        out = []
        rc = hunt.main(list(argv), out.append, self.path, UID, now)
        return rc, "\n".join(out)

    def last(self):
        e, p = gl.audit_verify(self.audit, gl.sha256(self.raw))
        self.assertEqual(p, [])
        return e[-1]

    def test_put_chunked(self):
        rc, text = self.run_hunt("put", "m01")
        self.assertEqual(rc, 0, text)
        self.assertEqual(bytes(self.garages[0].data), self.blob)
        self.assertEqual([c["offset"] for c in self.calls], [0, hunt.PUT_CHUNK, 2 * hunt.PUT_CHUNK])
        self.assertTrue(all(c["sha256"] == gl.sha256(self.blob) and c["total"] == len(self.blob) for c in self.calls))
        self.assertEqual((self.last()["action"], self.last()["result"]["bytes"]), ("put", len(self.blob)))

    def test_put_only_what_was_minted_here(self):
        rc, text = self.run_hunt("put", "rpcs3-etk_hunt-20261011-gt6-m09_armsx3-deadbeef0_linux_aarch64.AppImage")
        self.assertEqual(rc, 1)
        self.assertIn("not minted under", text)
        with open(os.path.join(self.stage, self.art), "r+b") as f:
            f.write(b"X")
        rc, text = self.run_hunt("put", self.art)
        self.assertEqual(rc, 1)
        self.assertIn("no longer matches", text)
        self.assertEqual(self.calls, [])

    def test_pin_uses_the_grants_game(self):
        rc, _ = self.run_hunt("pin", "m01")
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [{"op": "pin", "game": "BCUS98296", "core": self.art}])

    def test_car_refusal_is_audited(self):
        self.fail = "pin"
        rc, text = self.run_hunt("pin", "m01")
        self.assertEqual(rc, 1)
        self.assertIn("Autonomy", text)
        self.assertIn("error", self.last()["result"])

    def test_undo_without_a_valid_grant(self):
        later = gl.parse_iso("2026-10-11T12:00:00Z")      # the grant expired at 11:00
        self.assertEqual(self.run_hunt("put", "m01", now=later)[0], 1)
        for cmd, op in (("unpin", {"op": "unpin", "game": "BCUS98296"}), ("rollback", {"op": "unpin", "all": True}),
                        ("end", {"op": "hunt_end"})):
            rc, text = self.run_hunt(cmd, now=later)
            self.assertEqual(rc, 0, text)
            self.assertEqual(self.calls[-1], op)
            self.assertEqual(self.last()["action"], cmd)
        os.remove(self.path)
        self.assertEqual(self.run_hunt("end")[0], 0)      # no grant at all: still undoes


ETK = guard.ETK
DENY, ALLOW = True, False
# (command, grant valid?, denied?)
BASH_TABLE = [
    ("./install.sh", False, DENY),
    ("cd /home/dave/etk && ./install.sh", False, DENY),
    ("bash install.sh", False, DENY),
    ("FOO=1 sudo -u dave /home/dave/etk/uninstall.sh --yes", False, DENY),
    ("timeout 600 ./forge.sh --dry-run", False, DENY),
    ("tools/forge/lane_rpcs3.sh", False, DENY),
    ("bash -c 'cd /x; ./forge.sh rpcs3'", False, DENY),
    ("echo $(./install.sh)", False, DENY),
    ("os-install/build/build_gtk_image_v2.sh", False, DENY),
    ("bash -n install.sh", False, ALLOW),
    ("cat install.sh | grep -n STEP", False, ALLOW),
    ("sed -n 1,40p forge.sh && git diff install.sh", False, ALLOW),
    ("git add install.sh uninstall.sh", False, ALLOW),
    ("gh release create v0.10.0", False, DENY),
    ("gh release view v0.9.0", False, ALLOW),
    ("git tag", False, ALLOW),
    ("git tag -l 'v0.*'", False, ALLOW),
    ("git tag --sort=-v:refname", False, ALLOW),
    ("git tag v0.10.0", False, DENY),
    ("git tag -a v0.10.0 -m cut", False, DENY),
    ("git tag -d v0.9.0", False, DENY),
    ("git push origin main", False, ALLOW),
    ("git push --tags", False, DENY),
    ("git push origin v0.10.0", False, DENY),
    ("git push origin :refs/tags/v0.9.0", False, DENY),
    # car8 lift access (2026-10-24): power actions ON car8 are allowed; on any other car or this host, never
    ("ssh root@SM8250.local reboot", False, ALLOW),
    ("ssh -o ConnectTimeout=5 root@169.254.170.2 'sync; systemctl reboot'", False, ALLOW),
    ("ssh root@169.254.170.2 'cat /proc/uptime'", False, ALLOW),
    ("ssh root@169.254.170.2 sh -s <<'EOF'\nuptime\nreboot\nEOF", False, ALLOW),
    ("ssh -o BatchMode=yes etk-rig-usb 'systemctl reboot'", False, ALLOW),
    ("ssh flip2-12g reboot", False, DENY),
    ("ssh root@sm8250-12gb.local 'sync; systemctl reboot'", False, DENY),
    ("ssh -o ConnectTimeout=5 root@169.254.170.6 'systemctl poweroff'", False, DENY),
    ("ssh flip2-12g-usb sh -s <<'EOF'\nuptime\nreboot\nEOF", False, DENY),
    ("ssh etk-cloud 'sudo reboot'", False, DENY),
    ("reboot", False, DENY),
    ("sudo systemctl reboot", False, DENY),
    ("bash -c 'ssh etk-rig-usb reboot; reboot'", False, DENY),
    ("ssh evil-etk-rig-usb reboot", False, DENY),
    ("git commit -F - <<'EOF'\nreboot gate notes\n./install.sh is the operator's\nEOF", False, ALLOW),
    ("tools/hunt/grant.sh issue --game BCUS98296 --hours 10", False, DENY),
    ("tools/hunt/grant.sh show", False, ALLOW),
    ("./hunt start rr7", False, DENY),
    ("/home/dave/etk/hunt start gt6 8 --panics 0", False, DENY),
    ("python3 hunt start gt5p", False, DENY),
    ("./hunt status", False, ALLOW),
    ("./hunt stop", False, ALLOW),
    ("./hunt games prologue", False, ALLOW),
    ("python3 /home/dave/etk/tools/hunt/hunt.py status", False, ALLOW),
    ("python3 -I tools/hunt/hunt.py check --game BCUS98296", False, ALLOW),
    ("python3 /home/dave/etk/tools/hunt/hunt.py mint --commit abc", False, DENY),
    ("python3 /home/dave/etk/tools/hunt/hunt.py mint --commit abc", True, ALLOW),
    ("tools/hunt/hunt.py pin BCUS98296", False, DENY),
    ("sed -i s/x/y/ tools/hunt/grantlib.py", False, ALLOW),
    ("sed -i s/x/y/ tools/hunt/grantlib.py", True, DENY),
    ("cat tools/hunt/grantlib.py", True, ALLOW),
    ("echo '{}' > .claude/settings.local.json", True, DENY),
    ("cp /tmp/x ~/.claude/hooks/git-identity-firewall.py", True, DENY),
    ("git reset --hard HEAD~1", True, DENY),
    ("git -C /home/dave/etk checkout main", True, DENY),
    ("git checkout main", False, ALLOW),
    ("git -C /home/dave/etk-rpcs3-gtk checkout -b hunt/hunt-20261011-gt6", True, ALLOW),
    ("python3 tools/pitlink/pitlink.py garage launch game=BCUS98296", True, ALLOW),
    ("python3 /home/dave/etk/tools/pitlink/pitlink.py garage put kind=core name=x", True, DENY),
    ("python3 tools/pitlink/pitlink.py garage pin game=BCUS98296 core=x", True, DENY),
    ("python3 tools/pitlink/pitlink.py garage hunt_status", False, ALLOW),
    # car8 lift access: car8's hunt state is car8's; any other car's still changes only through hunt.py/Pitstop
    ("ssh root@SM8250.local 'echo on > /storage/.config/etk-autonomy'", False, ALLOW),
    ("ssh root@169.254.170.2 \"sed -i s/expires_epoch=.*/expires_epoch=9999999999/ /storage/.config/etk-hunt.grant\"", True, ALLOW),
    ("scp x.AppImage root@SM8250.local:/storage/games-internal/roms/etk/emulators/hunt/", True, ALLOW),
    ("ssh flip2-12g 'echo on > /storage/.config/etk-autonomy'", False, DENY),
    ("scp x.AppImage root@sm8250-12gb.local:/storage/games-internal/roms/etk/emulators/hunt/", True, DENY),
    ("scp x.AppImage root@SM8250.local:/tmp/ root@sm8250-12gb.local:/storage/.config/etk-hunt.grant", False, DENY),
    ("ssh root@SM8250.local 'cat /proc/uptime'", True, ALLOW),
    ("python3 /home/dave/etk/tools/hunt/hunt.py end", False, ALLOW),
    ("python3 /home/dave/etk/tools/hunt/hunt.py rollback", False, ALLOW),
    ("python3 /home/dave/etk/tools/hunt/hunt.py put m01", False, DENY),
    ("python3 /home/dave/etk/tools/hunt/hunt.py pin m01", True, ALLOW),
    ("git -C /home/dave/etk-rpcs3-gtk tag --sort=creatordate 2>/dev/null | tail -3", False, ALLOW),
    ("ssh flip2-12g 'cd /storage/k && ./kexec --version 2>&1'", False, ALLOW),
    ("ssh flip2-12g 'cd /storage/k && ./kexec -e'", False, DENY),
    ("timeout 20 ./forge.sh --status 2>&1", False, DENY),
    ("python3 - <<'EOF'\np='tools/forge/lane_kernel.sh'; s=\"it's\nEOF\necho don't", False, ALLOW),
    ("echo it's; ./install.sh", False, DENY),
    ("cd /home/dave/etk/etk-dossiers && git pull --ff-only", True, ALLOW),
    ("cat <<<EOF\n./install.sh\nEOF", False, DENY),
    ("bash -lc 'cd /home/dave/etk; ./uninstall.sh'", False, DENY),
    ("bash -ec 'echo ok'", False, ALLOW),
    ("git stash list", True, ALLOW),
    ("git pull -q --rebase origin main && git push -q origin main", True, ALLOW),
    ("git checkout HEAD~3 -- tools/hunt/grantlib.py", True, DENY),
    ("git stash pop", True, DENY),
    ("cd $S && git apply /tmp/p.diff", True, ALLOW),
]


# car8 lift access, the checkout half: with etk.conf CAR="car8" the bytes-to-atoms scripts run freely;
# publish, tags, signing a grant and power actions off car8 stay denied. (command, grant valid?, denied?)
LIFT_TABLE = [
    ("./install.sh", False, ALLOW),
    ("cd /home/dave/etk && ./install.sh", False, ALLOW),
    ("FOO=1 sudo -u dave /home/dave/etk/uninstall.sh --yes", False, ALLOW),
    ("FORGE_KERNEL_VER=0.6.4.6 ETK_SD_POLL=1 ./forge.sh kernel", False, ALLOW),
    ("tools/forge/lane_rpcs3.sh", False, ALLOW),
    ("python3 tools/pitlink/pitlink.py garage put kind=core name=x", False, ALLOW),
    ("gh release create v0.10.0", False, DENY),
    ("git tag v0.10.1", False, DENY),
    ("git push --tags", False, DENY),
    ("tools/hunt/grant.sh issue --game BCUS98296 --hours 10", False, DENY),
    ("reboot", False, DENY),
    ("ssh flip2-12g reboot", False, DENY),
]


class GuardTests(unittest.TestCase):
    def decide(self, payload, ok, car8=False):
        return guard.decide(dict(payload, cwd=payload.get("cwd", ETK)), lambda: ok, lambda: car8)

    def test_bash_table(self):
        bad = []
        for cmd, ok, deny in BASH_TABLE:
            r = self.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}, ok)
            if bool(r) != deny:
                bad.append(f"{'DENY' if deny else 'ALLOW'} expected, got {r!r}: {cmd!r} (grant={ok})")
        self.assertEqual(bad, [])

    def test_lift_table(self):
        bad = []
        for cmd, ok, deny in LIFT_TABLE:
            r = self.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}, ok, car8=True)
            if bool(r) != deny:
                bad.append(f"{'DENY' if deny else 'ALLOW'} expected, got {r!r}: {cmd!r} (car8 lift)")
        self.assertEqual(bad, [])

    def test_lift_follows_etk_conf(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write('# CAR="car8" in a comment does not count\nCAR="car12"\nCAR8_SSH="root@SM8250.local"\n')
        self.assertEqual(guard.conf_car(f.name), "car12")
        with open(f.name, "w") as g:
            g.write('CAR="car12"\n# switched for the headless car\nCAR="car8"\n')
        self.assertEqual(guard.conf_car(f.name), "car8")
        os.remove(f.name)
        self.assertIsNone(guard.conf_car("/nonexistent/etk.conf"))

    def test_edit_freeze(self):
        for path, ok, deny in ((os.path.join(ETK, "tools/hunt/hunt.py"), True, DENY),
                               (os.path.join(ETK, "tools/hunt/hunt.py"), False, ALLOW),
                               (os.path.join(ETK, ".claude/settings.local.json"), True, DENY),
                               (os.path.expanduser("~/.claude/settings.json"), True, DENY),
                               (os.path.join(ETK, "docs/AUTONOMY_SPEC.md"), True, ALLOW)):
            for tool in ("Edit", "Write"):
                r = self.decide({"tool_name": tool, "tool_input": {"file_path": path}}, ok)
                self.assertEqual(bool(r), deny, (tool, path, ok, r))

    def test_hook_protocol(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "guard.py")], capture_output=True, text=True,
                           input=json.dumps({"tool_name": "Bash", "cwd": ETK,
                                             "tool_input": {"command": "sudo systemctl reboot"}}))  # denied with or without car8 lift
        d = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual((r.returncode, d["permissionDecision"], d["hookEventName"]), (0, "deny", "PreToolUse"))
        r = subprocess.run([sys.executable, os.path.join(HERE, "guard.py")], capture_output=True, text=True,
                           input=json.dumps({"tool_name": "Bash", "cwd": ETK, "tool_input": {"command": "ls"}}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, os.path.join(HERE, "guard.py")], capture_output=True, text=True,
                           input="not json")
        self.assertNotIn(r.returncode, (0, 2), "a crash must be a non-blocking hook error, never exit 2")


class FrontDoorTests(unittest.TestCase):
    """./hunt: the operator's verbs compose exactly the grant.sh / hunt.py lines the spec documents."""

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        self.H = SourceFileLoader("hunt_front_door", os.path.join(ETK, "hunt")).load_module()

    def started(self, *args, tty=True):
        calls = []
        real, real_tty = self.H.os.execv, self.H.sys.stdin.isatty
        self.H.os.execv = lambda path, argv: calls.append(argv)
        self.H.sys.stdin.isatty = lambda: tty
        try:
            self.H.start(list(args))
        finally:
            self.H.os.execv, self.H.sys.stdin.isatty = real, real_tty
        return calls[0]

    def test_start_without_a_terminal_says_so(self):
        with self.assertRaises(SystemExit) as cm:
            self.started("rr7", tty=False)
        self.assertIn("real terminal", str(cm.exception))

    def test_start_defaults(self):
        argv = self.started("rr7")
        self.assertEqual(argv[:8], [self.H.GRANT, "issue", "--game", "NPUB30457", "--hours", "12", "--name", "rr7"])
        self.assertEqual(argv[8:], ["--lanes", "rpcs3", "--panics", "2", "--auto-mode"])

    def test_no_car_is_baked_in(self):
        ns = grantctl_parse(["issue", "--game", "NPUB30457"])
        self.assertEqual((ns.rig, ns.reserve), (None, None))

    def test_start_hours_and_overrides_win(self):
        argv = self.started("ridge racer", "8h", "--panics", "0", "--supervised")
        self.assertEqual(argv[5], "8")
        self.assertEqual(argv[-3:], ["--panics", "0", "--supervised"])   # after the defaults: argparse keeps the last
        ns = grantctl_parse(argv[1:])
        self.assertEqual((ns.game, ns.hours, ns.panics, ns.supervised, ns.rig), ("NPUB30457", 8.0, 0, True, None))

    def test_resolve(self):
        self.assertEqual(self.H.resolve("NPEA00050"), ("NPEA00050", "gt5p"))
        self.assertEqual(self.H.resolve("hd concept"), ("NPEA90002", "gthd"))
        with self.assertRaises(SystemExit):
            self.H.resolve("prologue")   # three serials: never guess
        with self.assertRaises(SystemExit):
            self.H.resolve("no such racer")


def grantctl_parse(argv):
    """grant.sh's own argparse over a composed line (no issue runs)."""
    seen = {}

    class Stop(Exception):
        pass

    real = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        seen["ns"] = real(self, args, namespace)
        raise Stop
    argparse.ArgumentParser.parse_args = grab
    try:
        grantctl.main(argv)
    except Stop:
        pass
    finally:
        argparse.ArgumentParser.parse_args = real
    return seen["ns"]


class MutantTests(unittest.TestCase):
    """The suites must fail against implementations that do nothing."""

    def failures(self, case):
        res = unittest.TestResult()
        with contextlib.redirect_stderr(io.StringIO()):
            unittest.defaultTestLoader.loadTestsFromTestCase(case).run(res)
        return len(res.failures) + len(res.errors)

    def test_permit_all_guard(self):
        real = guard.decide
        guard.decide = lambda payload, ok: None
        try:
            self.assertGreaterEqual(self.failures(GuardTests), 2)
        finally:
            guard.decide = real

    def test_no_problem_validator(self):
        real_f, real_o = gl.check_fields, gl.check_owner
        gl.check_fields = lambda g, now: []
        gl.check_owner = lambda path, uid=0: [] if os.path.lexists(path) else ["no grant"]
        try:
            self.assertGreaterEqual(self.failures(GrantTests), 10)
        finally:
            gl.check_fields, gl.check_owner = real_f, real_o


if __name__ == "__main__":
    unittest.main(verbosity=1)
