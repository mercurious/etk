#!/usr/bin/env python3
"""Harness for tools/card_transplant.py — the whole engine on temp-dir "cards" (no root, no devices).

Fixture = the 2026-10-23 car8 → car12 shape in miniature: a ROCKNIX boot card
(src) and a crash card whose root is a stale June /storage plus car8's
games-external projects (dst). Run: python3 tools/test_card_transplant.py
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_transplant as ct  # noqa: E402

OLD, MID, NEW = 1_780_000_000, 1_790_000_000, 1_800_000_000
SAVES = "games-internal/roms/bios/rpcs3/dev_hdd0/home/00000001/savedata"
PARK = "parked-car8-20261023"


def put(root, rel, data, t=MID):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.utime(p, (t, t))
    return p


def snapshot(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime, hashlib.sha256(Path(p).read_bytes()).hexdigest())
    return out


class Transplant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.sb, self.ss, self.db, self.ds = (t / n for n in ("sb", "ss", "db", "ds"))
        for d in (self.sb, self.ss, self.db, self.ds):
            d.mkdir()
        # recipient boot card (car12)
        put(self.sb, "KERNEL", b"ANDROID!" + b"\0" * 56 + b"boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet" + b"\0" * 500)
        system = b"hsqs" + os.urandom(4096)
        put(self.sb, "SYSTEM", system)
        put(self.sb, "SYSTEM.md5", hashlib.md5(system).hexdigest() + "  target/SYSTEM\n")
        put(self.sb, "KERNEL.etk-stock", b"stock")
        put(self.sb, "rocknix_abl/abl.elf", b"abl")
        put(self.sb, "DCIM/.keep", b"")
        put(self.ss, ".config/system/configs/system.cfg", "car12 config", NEW)
        put(self.ss, ".cache/usbgadget/network.conf", "car12 usb", NEW)
        put(self.ss, "sketchgen/app", "car12 project", NEW)
        (self.ss / "roms").mkdir()
        put(self.ss, "games-external/models/m1.gguf", "model-one", NEW)          # copy of car8's
        put(self.ss, "games-external/autogamer/new.py", "car12 autogamer", NEW)
        put(self.ss, "games-external/ollamadreno/ocl", "car12 ocl", NEW)
        (self.ss / "games-external/roms").mkdir()
        put(self.ss, "games-internal/roms/etk/.kit_commit", "car12-kit", NEW)
        put(self.ss, f"{SAVES}/GT6-GAME/GT6.1", "car12 progress", NEW)
        put(self.ss, f"{SAVES}/GT6-GAME/PARAM.SFO", "sfo", NEW)
        put(self.ss, f"{SAVES}/OLD-SAVE/DATA", "car12 stale", OLD)
        put(self.ss, f"{SAVES}/NEW12/DATA", "only on car12", NEW)
        put(self.ss, f"{SAVES}/SAME/DATA", "same", MID)
        put(self.ss, "games-internal/roms/ps3/gamelist.xml", "<gameList>2</gameList>", NEW)
        put(self.ss, "games-internal/roms/ps3/game.iso", "iso-bytes", NEW)       # same content, newer mtime
        put(self.ss, "games-internal/roms/ps3/gt6.iso", "gt6-bytes", NEW)
        put(self.ss, "games-internal/roms/nes/gamelist.xml", "<gameList>nes</gameList>", NEW)
        put(self.ss, "games-internal/roms/bios/rpcs3/custom_configs/c.yml", "tuned on car12", NEW)
        # donor crash card (car8)
        put(self.db, "LOST.DIR/346090", b"hsqs-june-orphan")
        put(self.db, "Android/data/x", b"android")
        put(self.ds, ".config/system/configs/system.cfg", "june config", OLD)
        (self.ds / ".update").mkdir()
        put(self.ds, "roms/gc/GameCube.zip", "bulk archive", OLD)
        put(self.ds, "Alarms/.keep", "", OLD)
        put(self.ds, "etk_crash_report.log", "june", OLD)
        put(self.ds, "autogamer/old.py", "car8 autogamer", OLD)
        put(self.ds, "autogamer-aarch64.tar.gz", "tarball", MID)
        put(self.ds, "models/m1.gguf", "model-one", OLD)
        put(self.ds, "models/m2.gguf", "model-two", OLD)
        put(self.ds, "enigma-dev/x", "car8 only", MID)
        put(self.ds, "ollamadreno/ocl", "car8 ocl", MID)
        put(self.ds, "games-external/.DS_Store", "mac", OLD)
        (self.ds / "games-external/roms").mkdir()
        put(self.ds, "games-internal/roms/etk/ledger.tsv", "car8 history", MID)
        put(self.ds, f"{SAVES}/GT6-GAME/GT6.1", "car8 progress", MID)
        put(self.ds, f"{SAVES}/GT6-GAME/PARAM.SFO", "sfo", MID)
        put(self.ds, f"{SAVES}/OLD-SAVE/DATA", "car8 newer", NEW)
        put(self.ds, f"{SAVES}/SAME/DATA", "same", MID)
        put(self.ds, "games-internal/roms/ps3/gamelist.xml", "<gameList>library</gameList>", MID)
        put(self.ds, "games-internal/roms/ps3/game.iso", "iso-bytes", OLD)
        put(self.ds, "games-internal/roms/ps3/gt5.iso", "library", OLD)
        put(self.ds, "games-internal/roms/bios/rpcs3/custom_configs/c.yml", "tuned on car8", OLD)
        put(self.ss, "games-internal/roms/bios/rpcs3/custom_configs/d.yml", "car12 older tune", OLD)
        put(self.ds, "games-internal/roms/bios/rpcs3/custom_configs/d.yml", "car8 newer tune", NEW)
        self.out = Path(self.tmp.name) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def run_ct(self, cmd):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ct.main([cmd, "--car", "car12", "--donor", "car8", "--date", "20261023",
                          "--out", str(self.out), "--dirs", str(self.sb), str(self.ss), str(self.db), str(self.ds)])
        return rc, buf.getvalue()

    def txt(self, root, rel):
        return Path(root, rel).read_text()

    def test_apply_end_state(self):
        src_before = snapshot(self.ss), snapshot(self.sb)
        rc, out = self.run_ct("apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: TRANSPLANT OK", out)
        ds, P = self.ds, self.ds / PARK
        # boot partition: payload in, orphan parked, Android furniture untouched
        for f in ("KERNEL", "SYSTEM", "SYSTEM.md5", "KERNEL.etk-stock", "rocknix_abl/abl.elf"):
            self.assertEqual(Path(self.db, f).read_bytes(), Path(self.sb, f).read_bytes(), f)
        self.assertFalse((self.db / "LOST.DIR/346090").exists())
        self.assertEqual((P / "boot/LOST.DIR/346090").read_bytes(), b"hsqs-june-orphan")
        self.assertTrue((self.db / "Android/data/x").exists())
        self.assertFalse((self.db / "DCIM").exists())
        # root: car12 state live, car8's stale state parked, nothing lost
        self.assertEqual(self.txt(ds, ".config/system/configs/system.cfg"), "car12 config")
        self.assertEqual(self.txt(P, "root/.config/system/configs/system.cfg"), "june config")
        self.assertEqual(self.txt(P, "root/roms/gc/GameCube.zip"), "bulk archive")
        self.assertTrue((P / "root/Alarms").is_dir() and (P / "root/etk_crash_report.log").exists())
        self.assertEqual(self.txt(ds, "sketchgen/app"), "car12 project")
        self.assertTrue((ds / "roms").is_dir())
        # games-external: projects relocated; superset kept; newer whole-entry wins
        self.assertEqual(self.txt(ds, "games-external/models/m2.gguf"), "model-two")
        self.assertEqual(self.txt(ds, "games-external/models/m1.gguf"), "model-one")
        self.assertEqual(self.txt(ds, "games-external/enigma-dev/x"), "car8 only")
        self.assertTrue((ds / "games-external/autogamer-aarch64.tar.gz").exists())
        self.assertEqual(self.txt(ds, "games-external/autogamer/new.py"), "car12 autogamer")
        self.assertFalse((ds / "games-external/autogamer/old.py").exists(), "two versions of a project must not mix")
        self.assertEqual(self.txt(P, "games-external/autogamer/old.py"), "car8 autogamer")
        self.assertEqual(self.txt(ds, "games-external/ollamadreno/ocl"), "car12 ocl")
        self.assertEqual(self.txt(P, "games-external/ollamadreno/ocl"), "car8 ocl")
        for e in ("autogamer", "models", "enigma-dev", "ollamadreno", "autogamer-aarch64.tar.gz"):
            self.assertFalse((ds / e).exists(), f"{e} left at the root")
        # games-internal: kit = car12's, car8's parked whole
        self.assertEqual(self.txt(ds, "games-internal/roms/etk/.kit_commit"), "car12-kit")
        self.assertFalse((ds / "games-internal/roms/etk/ledger.tsv").exists())
        self.assertEqual(self.txt(P, "games-internal/roms/etk/ledger.tsv"), "car8 history")
        # saves as units
        self.assertEqual(self.txt(ds, f"{SAVES}/GT6-GAME/GT6.1"), "car12 progress")
        self.assertEqual(self.txt(P, f"{SAVES}/GT6-GAME/GT6.1"), "car8 progress")
        self.assertEqual(self.txt(ds, f"{SAVES}/OLD-SAVE/DATA"), "car8 newer")
        self.assertEqual(self.txt(P, f"from-car12/{SAVES}/OLD-SAVE/DATA"), "car12 stale")
        self.assertEqual(self.txt(ds, f"{SAVES}/NEW12/DATA"), "only on car12")
        # gamelists: the library's stays, car12's stashed, filled where missing
        self.assertEqual(self.txt(ds, "games-internal/roms/ps3/gamelist.xml"), "<gameList>library</gameList>")
        self.assertEqual(self.txt(P, "from-car12/games-internal/roms/ps3/gamelist.xml"), "<gameList>2</gameList>")
        self.assertEqual(self.txt(ds, "games-internal/roms/nes/gamelist.xml"), "<gameList>nes</gameList>")
        # content merge: new files land, newer differing file wins + backup, library untouched
        self.assertEqual(self.txt(ds, "games-internal/roms/ps3/gt6.iso"), "gt6-bytes")
        self.assertEqual(self.txt(ds, "games-internal/roms/ps3/gt5.iso"), "library")
        self.assertEqual(self.txt(ds, "games-internal/roms/bios/rpcs3/custom_configs/c.yml"), "tuned on car12")
        self.assertEqual(self.txt(P, "replaced/games-internal/roms/bios/rpcs3/custom_configs/c.yml"), "tuned on car8")
        self.assertFalse((P / "replaced/games-internal/roms/ps3/game.iso").exists(), "identical content must not be rewritten")
        self.assertEqual(self.txt(ds, "games-internal/roms/bios/rpcs3/custom_configs/d.yml"), "car8 newer tune",
                         "a file newer on the donor must not be overwritten by an older one")
        # the source card is never written
        self.assertEqual((snapshot(self.ss), snapshot(self.sb)), src_before)
        st = json.loads((ds / ct.MARKER).read_text())
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["labels"], ["ROCKNIX", "STORAGE"])

    def test_nothing_deleted(self):
        """Every donor file's content survives somewhere on the destination."""
        def contents(*roots):
            return sorted(h for r in roots for (_s, _t, h) in snapshot(r).values())
        before = contents(self.db, self.ds)
        rc, out = self.run_ct("apply")
        self.assertEqual(rc, 0, out)
        after = contents(self.db, self.ds)
        missing = [h for h in set(before) if h not in set(after)]
        self.assertEqual(missing, [])

    def test_plan_writes_nothing(self):
        before = snapshot(self.ds), snapshot(self.db), snapshot(self.ss), snapshot(self.sb)
        rc, out = self.run_ct("plan")
        self.assertEqual(rc, 0, out)
        self.assertIn("labels after: p1 → ROCKNIX, p2 → STORAGE (from the KERNEL baked cmdline)", out)
        self.assertEqual((snapshot(self.ds), snapshot(self.db), snapshot(self.ss), snapshot(self.sb)), before)

    def test_resume_after_interruption(self):
        real, calls = ct.do_action, [0]

        def flaky(roots, act):
            calls[0] += 1
            if calls[0] == 12:
                raise KeyboardInterrupt
            return real(roots, act)
        ct.do_action = flaky
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.run_ct("apply")
        finally:
            ct.do_action = real
        self.assertEqual(json.loads((self.ds / ct.MARKER).read_text())["state"], "in-progress")
        rc, out = self.run_ct("apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESUMING", out)
        self.assertEqual(self.txt(self.ds, "games-internal/roms/etk/.kit_commit"), "car12-kit")
        self.assertEqual(self.txt(self.ds, f"{PARK}/games-internal/roms/etk/ledger.tsv"), "car8 history")

    def test_second_apply_refused(self):
        self.assertEqual(self.run_ct("apply")[0], 0)
        with self.assertRaises(SystemExit) as cm:
            self.run_ct("apply")
        self.assertIn("already transplanted", str(cm.exception))

    def test_space_refusal(self):
        real = ct._free
        ct._free = lambda p: 1 << 20
        try:
            rc, out = self.run_ct("plan")
            self.assertEqual(rc, 2)
            self.assertIn("INSUFFICIENT", out)
            with self.assertRaises(SystemExit):
                self.run_ct("apply")
        finally:
            ct._free = real
        self.assertFalse((self.ds / ct.MARKER).exists(), "a refused apply must not start")

    def test_not_a_boot_card(self):
        (self.sb / "SYSTEM").unlink()
        with self.assertRaises(SystemExit) as cm:
            self.run_ct("plan")
        self.assertIn("not a ROCKNIX boot card", str(cm.exception))

    def test_unmount_before_the_clean_shutdown_check(self):
        """A desktop-mounted (rw) ext4 always reads needs_recovery: judge it only once unmounted."""
        calls, mounted = [], {"/dev/sdb2"}
        part = lambda path, fs, mp: {"path": path, "fstype": fs, "label": None, "mountpoints": [mp] if mp else []}
        src = [part("/dev/sdb1", "vfat", "/run/media/x/ROCKNIX"), part("/dev/sdb2", "ext4", "/run/media/x/STORAGE")]
        dst = [part("/dev/sda1", "vfat", None), part("/dev/sda2", "ext4", None)]

        class R:
            returncode, stdout = 0, ""

        def fake_run(cmd, *a, **kw):
            calls.append(cmd[0])
            if cmd[0] == "umount" and cmd[1] == "/run/media/x/STORAGE":
                mounted.discard("/dev/sdb2")
            r = R()
            if cmd[0] == "dumpe2fs":
                r.stdout = "Filesystem features: has_journal" + (" needs_recovery" if cmd[-1] in mounted else "") + "\n"
            if cmd[0] == "mount":
                raise RuntimeError("stop before mounting")
            return r
        real_run, real_lsblk = ct.subprocess.run, ct.lsblk
        real_base, ct.MOUNT_BASE = ct.MOUNT_BASE, self.tmp.name + "/mnt"
        ct.subprocess.run = fake_run
        ct.lsblk = lambda dev: src if dev == "/dev/sdb" else dst
        try:
            args = type("Args", (), {"src": "/dev/sdb", "dst": "/dev/sda"})()
            with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as cm:
                ct.device_roots(args, mount=True)
            self.assertIn("stop before mounting", str(cm.exception))   # got past the clean-shutdown check
        finally:
            ct.subprocess.run, ct.lsblk = real_run, real_lsblk
            ct.MOUNT_BASE = real_base
        self.assertLess(calls.index("umount"), calls.index("dumpe2fs"))

    def test_labels_follow_the_kernel(self):
        put(self.sb, "KERNEL", b"ANDROID!" + b"boot=LABEL=GTKBOOT disk=LABEL=GTKDATA")
        self.assertEqual(ct.kernel_labels(self.sb / "KERNEL"), (("GTKBOOT", "GTKDATA"), True))
        put(self.sb, "KERNEL", b"ANDROID! no labels")
        self.assertEqual(ct.kernel_labels(self.sb / "KERNEL"), (ct.DEFAULT_LABELS, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
