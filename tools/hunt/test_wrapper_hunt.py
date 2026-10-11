#!/usr/bin/env python3
"""The per-title core launch wrapper's HUNT OVERRIDE (P3, docs/AUTONOMY_SPEC.md §4), run for real.

  python3 tools/hunt/test_wrapper_hunt.py [--against REV]

Extracts the wrapper install.sh generates (the 'WRAP' heredoc of STEP 6.55), points its
fixed rig paths into a sandbox, and launches a game through it. Each core and the
certified build are stub scripts that print who they are and the Vulkan ICD they got.
  [HONOUR]  grant for this title + unexpired + this car's serial + Autonomy on -> the hunt
            core runs (ledger token hunt:<name>); a driver pin exports a per-launch ICD whose
            library_path is the hunt .so
  [IGNORE]  no grant, Autonomy off/absent, expired, the car clock behind the issue time,
            another car's serial, another title, a traversal or non-hunt name, a missing
            file: the wrapper's own choice stands (the core_map pin, else certified)
  [BASE]    with no hunt at all a core_map pin and the certified default behave as before
DISCRIMINATION: --against <rev> takes install.sh from <rev>; against 5865a7f (pre-P3) every
HONOUR case fails and IGNORE/BASE pass (there is nothing to ignore).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
AGAINST = None
SERIAL = "32906f627cfd1e17bcbecbc8a2f0e2cd"
GAME = "BCUS98296"
HCORE = "rpcs3-etk_hunt-20261011-gt6-m01_armsx3-a74a0f3e0_linux_aarch64.AppImage"
HDRV = "etk_turnip_hunt-26.2.3-x.so"
STUB = '#!/bin/sh\necho "{who} icd=${{VK_ICD_FILENAMES:-none}}"\n'


def wrapper_source():
    if AGAINST:
        text = subprocess.run(["git", "-C", ETK, "show", f"{AGAINST}:install.sh"], capture_output=True, text=True,
                              check=True).stdout
    else:
        with open(os.path.join(ETK, "install.sh")) as f:
            text = f.read()
    m = re.search(r"cat << 'WRAP' > /storage/\.config/etk-rpcs3-launch\.sh\n(.*?)\nWRAP\n", text, re.S)
    return m.group(1)


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.td = td = tempfile.mkdtemp()
        self.st = os.path.join(td, "storage")
        self.etk = os.path.join(self.st, "games-internal", "roms", "etk")
        src = wrapper_source()
        for real, fake in (("/storage/", self.st + "/"),
                           ("/sys/kernel/config/usb_gadget/cdc/strings/0x409/serialnumber", os.path.join(td, "serial")),
                           ("/usr/share/vulkan/icd.d/", os.path.join(td, "icd.d") + "/"),
                           ("/tmp/etk-hunt-icd.json", os.path.join(td, "hunt-icd.json"))):
            src = src.replace(real, fake)
        self.wrap = os.path.join(td, "launch.sh")
        self.w(self.wrap, src, 0o755)
        self.w(os.path.join(self.etk, "scripts", "env.sh"),
               f'export ETK_ROOT="{self.etk}"\nexport RPCS3_CORES_DIR="$ETK_ROOT/emulators"\n'
               f'export RPCS3_CORE_MAP="$RPCS3_CORES_DIR/core_map.tsv"\n'
               f'export ACTIVE_CORE_FILE="{td}/active_core.txt"\nexport TRIPWIRE_LOG="{td}/tripwire.log"\n')
        self.w(os.path.join(self.st, "rpcs3", "rpcs3-sa.custom"), STUB.format(who="CERT"), 0o755)
        self.w(os.path.join(self.etk, "emulators", "pinned.AppImage"), STUB.format(who="PINNED"), 0o755)
        self.w(os.path.join(self.etk, "emulators", "hunt", HCORE), STUB.format(who="HUNT"), 0o644)
        self.w(os.path.join(self.etk, "drivers", "hunt", HDRV), "so")
        self.w(os.path.join(td, "icd.d", "freedreno_icd.aarch64.json"),
               '{"file_format_version": "1.0.0", "ICD": {"library_path": "/usr/lib/libvulkan_freedreno.so", '
               '"api_version": "1.4.318"}}\n')
        self.w(os.path.join(td, "serial"), SERIAL + "\n")
        self.override(HCORE, "-")
        self.grant()
        self.autonomy("on")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def w(self, path, body, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, mode)

    def grant(self, **over):
        now = int(time.time())
        g = {"id": "hunt-20261011-gt6", "game": GAME, "expires_epoch": now + 3600, "issued_epoch": now - 60,
             "usb_serial": SERIAL}
        g.update(over)
        self.w(os.path.join(self.st, ".config", "etk-hunt.grant"), "".join(f"{k}={v}\n" for k, v in g.items()))

    def autonomy(self, state):
        self.w(os.path.join(self.st, ".config", "etk-autonomy"), state + "\n")

    def override(self, core, drv, game=GAME):
        self.w(os.path.join(self.etk, "emulators", "hunt", "override.tsv"), f"# x\n{game}\t{core}\t{drv}\n")

    def launch(self, serial=GAME):
        r = subprocess.run([self.wrap, f"/storage/roms/ps3/game/{serial}/USRDIR/EBOOT.BIN"], capture_output=True,
                           text=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
        token = ""
        if os.path.exists(os.path.join(self.td, "active_core.txt")):
            with open(os.path.join(self.td, "active_core.txt")) as f:
                token = f.read().strip()
        return r.stdout.strip(), token


class Honour(Wrapper):
    def test_hunt_core(self):
        out, token = self.launch()
        self.assertEqual((out, token), ("HUNT icd=none", f"hunt:{HCORE}"))

    def test_hunt_driver(self):
        self.override(HCORE, HDRV)
        out, token = self.launch()
        icd = os.path.join(self.td, "hunt-icd.json")
        self.assertEqual(out, f"HUNT icd={icd}")
        self.assertEqual(token, f"hunt:{HCORE}+hunt:{HDRV}")
        with open(icd) as f:
            body = f.read()
        self.assertIn(f'"library_path": "{self.etk}/drivers/hunt/{HDRV}"', body)
        self.assertIn('"api_version": "1.4.318"', body)


class Ignore(Wrapper):
    def falls_back(self):
        out, token = self.launch()
        self.assertEqual(out, "CERT icd=none")
        self.assertNotIn("hunt", token)

    def test_no_grant(self):
        os.remove(os.path.join(self.st, ".config", "etk-hunt.grant"))
        self.falls_back()

    def test_autonomy_off(self):
        self.autonomy("off")
        self.falls_back()
        self.autonomy(" on ")                    # Pitstop's and the daemon's test too: exactly "on"
        self.falls_back()
        os.remove(os.path.join(self.st, ".config", "etk-autonomy"))
        self.falls_back()

    def test_expired(self):
        self.grant(expires_epoch=int(time.time()) - 1)
        self.falls_back()

    def test_clock_behind(self):
        self.grant(issued_epoch=int(time.time()) + 3600, expires_epoch=int(time.time()) + 7200)
        self.falls_back()

    def test_other_car(self):
        self.grant(usb_serial="ffff")
        self.falls_back()

    def test_other_title(self):
        self.grant(game="NPEA00050")
        self.falls_back()

    def test_bad_names(self):
        for core in ("../../rpcs3/rpcs3-sa.custom", "rpcs3-etk_hunt-x/../y.AppImage", "evil.AppImage",
                     "rpcs3-etk_hunt-missing.AppImage"):
            self.override(core, "-")
            self.falls_back()

    def test_keeps_core_map_pin(self):
        self.w(os.path.join(self.etk, "emulators", "core_map.tsv"), f"{GAME}\tpinned.AppImage\n")
        self.autonomy("off")
        self.assertEqual(self.launch(), ("PINNED icd=none", "pinned.AppImage"))


class Base(Wrapper):
    def test_certified_default(self):
        shutil.rmtree(os.path.join(self.etk, "emulators", "hunt"))
        self.assertEqual(self.launch()[0], "CERT icd=none")

    def test_core_map_pin(self):
        shutil.rmtree(os.path.join(self.etk, "emulators", "hunt"))
        self.w(os.path.join(self.etk, "emulators", "core_map.tsv"), f"{GAME}\tpinned.AppImage\n")
        self.assertEqual(self.launch()[0], "PINNED icd=none")


if __name__ == "__main__":
    if "--against" in sys.argv:
        k = sys.argv.index("--against")
        AGAINST = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    unittest.main(verbosity=1)
