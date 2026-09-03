#!/usr/bin/env python3
"""ETK — host-side regression tests for the Pitstop PS3 installers.

Run from the repo root:   python3 tools/test_installers.py

Covers bin/etk_pitstop.py's two install paths, which are twins as of 0.8.1:
both drive `rpcs3 --headless --install{pkg,fw}`, take process exit as the
completion signal, and read their verdict out of RPCS3's own log rather than
by watching the filesystem.

Three suites:
  [1-8]  unit    — verdict parsing, log-slice/rotation handling, the RPCS3
                   config-dir link pre-flight, size-scaled timeouts
  [E2E]  package — full _run_install against a fake rpcs3 reproducing the
                   rig's observed behaviour, INCLUDING its exit code 143
                   (SIGTERM in static teardown) after a SUCCESSFUL install
  [FW]   firmware— _run_install_fw, the reference implementation

The log lines are verbatim from the rig (2026-07-24) or from RPCS3's own
format strings in rpcs3qt/main_window.cpp. No rig, no network, no root.
"""
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
PITSTOP = os.environ.get("ETK_PITSTOP_PY",
                         os.path.join(_HERE, os.pardir, "bin", "etk_pitstop.py"))
spec = importlib.util.spec_from_file_location('pit', PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

# Always wrap the PRISTINE Popen — wrapping a wrapper nests and mangles argv.
REAL_POPEN = subprocess.Popen
REAL_SDL_NAMES = pit._sdl_gamepad_names
FAILS = []

# SILENCE THE NOTIFICATION SURFACES FOR THE WHOLE SUITE.
# The fixtures patch `pit.subprocess.Popen`, and `pit.subprocess` IS the shared
# subprocess module object — `subprocess.run()` resolves Popen from module
# globals, so the patch intercepts run() too. The installers raise a
# _ProgressCard whose internal notifier shells out to dbus-send on a heartbeat
# thread, and every one of those calls was landing in the fake rpcs3 script.
# For the firmware fixture that is not merely noise: its fake ignores argv and
# unconditionally writes version.txt and the success line, so a notification
# PERFORMED THE INSTALL and [FW-1] passed even when the real --installfw
# subprocess did nothing. Stubbing the surfaces here keeps the suite measuring
# the installers instead of its own toasts.
class _NoCard:
    def __init__(self, *a, **kw):
        pass

    def start(self):
        return self

    def stop(self):
        pass


pit._ProgressCard = _NoCard
pit._es_reload_gamelists = lambda: None


def check(name, got, want):
    if got == want:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}")
        FAILS.append(name)


class FakeNotifier:
    def post(self, *a, **k):
        pass


PKG = ("/storage/games-internal/roms/etk/pkg_install_drop/"
       "jwx3AOv0gH0Gj2QLdMBVDQTUovfEEXq0Dc7dOIOAtkre5WUES9twg0NIvUpmdprwjXee0KqkeTk6M8p4I1cVEWIXJDhkOlfIsI7RJ.pkg")

# --- verdict regex -------------------------------------------------
print("\n[1] verdict parsing")

# Verbatim from the rig's RPCS3.log, headless run 13:55.
real = (u"·! 0:00:00.112649 GUI: About to install packages:\n"
        u"·S 0:00:36.773968 GUI: Successfully installed " + PKG +
        " (title_id=NPEA90002, title=GRAN TURISMO HD Concept, version=02.00).\n")
ms = [m for m in pit._PKG_VERDICT_RE.finditer(real) if m.group('path') == PKG]
check("success matched", len(ms), 1)
check("title_id", ms[0].group('tid'), "NPEA90002")
check("title", ms[0].group('title'), "GRAN TURISMO HD Concept")
check("version", ms[0].group('ver'), "02.00")
check("verb", ms[0].group('verb'), "Successfully installed")

# A title whose name contains commas and parens — the greedy-match trap.
tricky = ("Successfully installed /d/x.pkg (title_id=BLUS30019, "
          "title=Test (Game), Deluxe, version=01.01).")
mt = list(pit._PKG_VERDICT_RE.finditer(tricky))
check("comma/paren title parsed", mt[0].group('title'), "Test (Game), Deluxe")
check("comma/paren tid", mt[0].group('tid'), "BLUS30019")

for verb in ("Failed to install", "Partially installed", "Aborted installation of"):
    line = f"{verb} {PKG} (title_id=NPEA90002, title=GT HD, version=02.00)."
    mm = [m for m in pit._PKG_VERDICT_RE.finditer(line) if m.group('path') == PKG]
    check(f"{verb!r} matched", len(mm), 1)
    check(f"{verb!r} not counted as success",
          [m for m in mm if m.group('verb') == 'Successfully installed'], [])

print("\n[2] path guard (a PREVIOUS run's success must not count as ours)")
other = ("Successfully installed /storage/other/thing.pkg "
         "(title_id=NPEA00050, title=GT5P, version=01.00).")
check("foreign path rejected",
      [m for m in pit._PKG_VERDICT_RE.finditer(other) if m.group('path') == PKG], [])

print("\n[3] bare-form errors")
check("app_version error matched",
      [m.group('path') for m in pit._PKG_VERSION_ERR_RE.finditer(f"Cannot install {PKG}.")],
      [PKG])
check("invalid package matched",
      [m.group('path') for m in pit._PKG_INVALID_RE.finditer(
          f"Cannot install invalid package: '{PKG}'")], [PKG])
# "Cannot install invalid package: '<p>'" must NOT read as an app_version error.
check("invalid pkg not read as version error",
      list(pit._PKG_VERSION_ERR_RE.finditer(
          f"Cannot install invalid package: '{PKG}'")), [])
check("bare extraction failure matched",
      [m.group('path') for m in pit._PKG_FAILED_RE.finditer(
          u"·E 0:00:12.5 GUI: Failed to install " + PKG + ".")], [PKG])
# The TUPLE failure line must not come back with OUR path (it is a different
# message class and is handled by the verdict regex).
check("tuple failure not claimed by bare matcher",
      [m.group('path') for m in pit._PKG_FAILED_RE.finditer(
          f"Failed to install {PKG} (title_id=X, title=Y, version=1).")
       if m.group('path') == PKG], [])
# A bare failure for someone ELSE's package must not be claimed as ours.
check("foreign bare failure rejected",
      [m.group('path') for m in pit._PKG_FAILED_RE.finditer(
          "Failed to install /storage/other.pkg.") if m.group('path') == PKG], [])

print("\n[4] log freshness (RPCS3 rewrites RPCS3.log IN PLACE every launch)")
tmp = tempfile.mkdtemp()
try:
    log = os.path.join(tmp, "RPCS3.log")
    pit.RPCS3_LOG = log
    STALE = ("PREVIOUS RUN\nSuccessfully installed old.pkg "
             "(title_id=X, title=Y, version=1).\n")
    with open(log, "w") as f:
        f.write(STALE)
    os.utime(log, (time.time() - 600, time.time() - 600))   # last run, 10 min ago

    # (a) RPCS3 never ran -> nothing is ours, so the stale success is invisible.
    check("untouched log -> empty slice", pit._rpcs3_log_since(time.time()), "")

    # (b) THE 0.8.1 REGRESSION. RPCS3 gzips the old log and rewrites the
    # original IN PLACE: same inode, and re-running the SAME package yields a
    # BYTE-IDENTICAL log. The shipped (size, inode) guard read "size unchanged"
    # as "RPCS3 wrote nothing" and threw away a real success.
    mark = pit._rpcs3_log_mark()
    ino_before = os.stat(log).st_ino
    fresh = ("THIS RUN\nSuccessfully installed new.pkg "
             "(title_id=Z, title=W, version=2).\n")
    assert len(fresh) != len(STALE), "make the sizes differ for the shaping below"
    # Pad so the new log is EXACTLY the same size as the old one, in place.
    fresh = fresh.rstrip("\n") + " " * (len(STALE) - len(fresh)) + "\n"
    with open(log, "r+") as f:          # r+ = rewrite in place, inode preserved
        f.write(fresh)
        f.truncate()
    check("rewrite kept the inode (matches RPCS3)", os.stat(log).st_ino, ino_before)
    check("identical-SIZE rewrite is still read",
          os.stat(log).st_size, len(STALE))
    got = pit._rpcs3_log_since(mark)
    check("same-size in-place rewrite -> content returned", got.strip(), fresh.strip())
    check("and it is THIS run's line, not the stale one",
          "new.pkg" in got and "old.pkg" not in got, True)

    # (c) a genuinely new file (should RPCS3 ever recreate rather than rewrite)
    mark = pit._rpcs3_log_mark()
    os.remove(log)
    with open(log, "w") as f:
        f.write("RECREATED\n")
    check("recreated log -> whole file", pit._rpcs3_log_since(mark), "RECREATED\n")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[5] link pre-flight: fresh rig, real dirs holding a stranded install")
tmp = tempfile.mkdtemp()
try:
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    pit.RPCS3_CFG_DIR, pit.RPCS3_BIOS_DIR = cfg, bios
    # config side: RPCS3 created real dirs and installed into them
    os.makedirs(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "USRDIR"))
    with open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "USRDIR", "EBOOT.BIN"), "w") as f:
        f.write("game bytes")
    os.makedirs(os.path.join(cfg, "dev_flash", "vsh", "etc"))
    with open(os.path.join(cfg, "dev_flash", "vsh", "etc", "version.txt"), "w") as f:
        f.write("release:04.9300:")
    # games tree: what install.sh STEP 1 provisions (empty skeleton)
    os.makedirs(os.path.join(bios, "dev_hdd0", "game"))
    os.makedirs(os.path.join(bios, "dev_hdd0", "home", "00000001", "exdata"))

    warn = pit._ensure_rpcs3_storage_links()
    check("no warnings", warn, [])
    for folder in ("dev_flash", "dev_hdd0", "dev_hdd1", "custom_configs"):
        check(f"{folder} is now a symlink",
              os.path.islink(os.path.join(cfg, folder)), True)
    check("game migrated to games tree",
          open(os.path.join(bios, "dev_hdd0", "game", "NPEA90002", "USRDIR", "EBOOT.BIN")).read(),
          "game bytes")
    check("firmware migrated to games tree",
          os.path.isfile(os.path.join(bios, "dev_flash", "vsh", "etc", "version.txt")), True)
    check("pre-existing exdata skeleton preserved",
          os.path.isdir(os.path.join(bios, "dev_hdd0", "home", "00000001", "exdata")), True)
    check("config path now resolves into the games tree",
          os.path.realpath(os.path.join(cfg, "dev_hdd0", "game")),
          os.path.realpath(os.path.join(bios, "dev_hdd0", "game")))

    # idempotent second run
    check("re-run is a no-op", pit._ensure_rpcs3_storage_links(), [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[6] link pre-flight: same file on BOTH sides -> refuse, lose nothing")
tmp = tempfile.mkdtemp()
try:
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    pit.RPCS3_CFG_DIR, pit.RPCS3_BIOS_DIR = cfg, bios
    os.makedirs(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002"))
    os.makedirs(os.path.join(bios, "dev_hdd0", "game", "NPEA90002"))
    with open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "PARAM.SFO"), "w") as f:
        f.write("config-side copy")
    with open(os.path.join(bios, "dev_hdd0", "game", "NPEA90002", "PARAM.SFO"), "w") as f:
        f.write("games-tree copy")

    warn = pit._ensure_rpcs3_storage_links()
    check("conflict reported", any("dev_hdd0" in w for w in warn), True)
    check("dev_hdd0 NOT linked over live data",
          os.path.islink(os.path.join(cfg, "dev_hdd0")), False)
    check("config-side copy intact",
          open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "PARAM.SFO")).read(),
          "config-side copy")
    check("games-tree copy intact",
          open(os.path.join(bios, "dev_hdd0", "game", "NPEA90002", "PARAM.SFO")).read(),
          "games-tree copy")
    check("unaffected folders still linked",
          os.path.islink(os.path.join(cfg, "dev_flash")), True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[6b] link pre-flight under EXDEV (the games-card bind mount)")
# ROCKNIX bind-mounts the games card at /storage/roms, so rename(2) fails with
# EXDEV even though st_dev matches on both sides. Live failure, 2026-07-24:
# every migration logged "[Errno 18] Invalid cross-device link" and nothing
# moved. Simulate by making os.rename refuse across the two trees.
tmp = tempfile.mkdtemp()
try:
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    pit.RPCS3_CFG_DIR, pit.RPCS3_BIOS_DIR = cfg, bios
    os.makedirs(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "USRDIR"))
    open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "USRDIR",
                      "EBOOT.BIN"), "w").write("game bytes")
    os.makedirs(os.path.join(bios, "dev_hdd0", "game"))

    real_rename = os.rename

    def exdev_rename(a, b):
        # Refuse only when crossing between the two trees, like the bind mount.
        if (str(a).startswith(cfg) and str(b).startswith(bios)):
            raise OSError(18, "Invalid cross-device link", str(a), None, str(b))
        return real_rename(a, b)

    os.rename = exdev_rename
    try:
        warn = pit._ensure_rpcs3_storage_links()
    finally:
        os.rename = real_rename

    check("EXDEV: migration still succeeds", warn, [])
    check("EXDEV: game copied into the games tree",
          open(os.path.join(bios, "dev_hdd0", "game", "NPEA90002",
                            "USRDIR", "EBOOT.BIN")).read(), "game bytes")
    check("EXDEV: config side is now a symlink",
          os.path.islink(os.path.join(cfg, "dev_hdd0")), True)
    check("EXDEV: no .etk-part staging left behind",
          [p for p in os.listdir(os.path.join(bios, "dev_hdd0", "game"))
           if p.endswith(".etk-part")], [])
finally:
    os.rename = real_rename if 'real_rename' in dir() else os.rename
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[6c] a copy that dies mid-way leaves NO partial-looking real tree")
tmp = tempfile.mkdtemp()
try:
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    pit.RPCS3_CFG_DIR, pit.RPCS3_BIOS_DIR = cfg, bios
    os.makedirs(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002"))
    open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002", "EBOOT.BIN"),
         "w").write("game bytes")
    os.makedirs(os.path.join(bios, "dev_hdd0", "game"))

    real_rename, real_move = os.rename, pit.shutil.move

    def exdev_rename(a, b):
        if str(a).startswith(cfg) and str(b).startswith(bios):
            raise OSError(18, "Invalid cross-device link")
        return real_rename(a, b)

    def dying_move(a, b):
        os.makedirs(b, exist_ok=True)          # start writing...
        raise OSError(28, "No space left on device")

    os.rename, pit.shutil.move = exdev_rename, dying_move
    try:
        warn = pit._ensure_rpcs3_storage_links()
    finally:
        os.rename, pit.shutil.move = real_rename, real_move

    check("failed copy is reported", any("dev_hdd0" in w for w in warn), True)
    check("source data untouched",
          open(os.path.join(cfg, "dev_hdd0", "game", "NPEA90002",
                            "EBOOT.BIN")).read(), "game bytes")
    check("NOT linked over a failed migration",
          os.path.islink(os.path.join(cfg, "dev_hdd0")), False)
    check("no half-written NPEA90002 in the games tree",
          os.path.exists(os.path.join(bios, "dev_hdd0", "game", "NPEA90002")), False)
    check("no .etk-part left behind",
          [p for p in os.listdir(os.path.join(bios, "dev_hdd0", "game"))
           if p.endswith(".etk-part")], [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[7] link pre-flight: no config dir -> do nothing (don't suppress ROCKNIX seeding)")
tmp = tempfile.mkdtemp()
try:
    pit.RPCS3_CFG_DIR = os.path.join(tmp, "nope", "rpcs3")
    pit.RPCS3_BIOS_DIR = os.path.join(tmp, "roms", "bios", "rpcs3")
    check("returns clean", pit._ensure_rpcs3_storage_links(), [])
    check("config dir NOT created", os.path.exists(pit.RPCS3_CFG_DIR), False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[8] install timeout scales with package size")
tmp = tempfile.mkdtemp()
try:
    small = os.path.join(tmp, "s.pkg")
    with open(small, "wb") as f:
        f.write(b"\0" * 1024)
    check("small pkg -> 900s floor", pit._pkg_install_timeout(small), 900)
    check("missing pkg -> floor", pit._pkg_install_timeout(os.path.join(tmp, "nope")), 900)
    # 20 GB (GT5-class) without actually writing 20 GB
    big = os.path.join(tmp, "b.pkg")
    with open(big, "wb") as f:
        f.truncate(20 * 1024 * 1024 * 1024)
    t = pit._pkg_install_timeout(big)
    check("20GB pkg gets >1h and <=2h", 3600 < t <= 7200, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

FAKE_RPCS3 = r'''#!/usr/bin/env python3
"""Stand-in for rpcs3-sa --headless --installpkg. Mode comes from $FAKE_MODE."""
import os, sys, gzip
mode = os.environ.get("FAKE_MODE", "success")
log  = os.environ["FAKE_LOG"]
game = os.environ["FAKE_GAMEDIR"]      # where RPCS3 resolves dev_hdd0/game
pkg  = sys.argv[sys.argv.index("--installpkg") + 1]

# RPCS3 rotates its log on every launch (RPCS3.log -> RPCS3.log.gz).
# RPCS3 gzips the old log then REWRITES RPCS3.log IN PLACE (same inode,
# util/logs.cpp:476). Modelling that faithfully matters: an earlier fake
# did os.remove + create, handing the code a fresh inode and hiding the
# 0.8.1 same-size regression that then bit on the rig.
if os.path.exists(log):
    with open(log, "rb") as f, gzip.open(log + ".gz", "wb") as g:
        g.write(f.read())
out = open(log, "r+" if os.path.exists(log) else "w")
out.truncate(0); out.seek(0)
out.write("RPCS3 v0.0.41 Alpha | GTK Edition v0.7.5\nCurrent Time: now\n")
out.write("·! 0:00:00.11 GUI: About to install packages:\n%s\n" % pkg)

if mode == "success":
    d = os.path.join(game, "NPEA90002", "USRDIR")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "EBOOT.BIN"), "w").write("eboot")
    out.write("·S 0:00:36.77 GUI: Successfully installed %s "
              "(title_id=NPEA90002, title=GRAN TURISMO HD Concept, "
              "version=02.00).\n" % pkg)
elif mode == "version":
    out.write("·E 0:00:01.2 GUI: Cannot install %s.\n" % pkg)
elif mode == "invalid":
    out.write("·E 0:00:00.9 GUI: Cannot install invalid package: '%s'\n" % pkg)
elif mode == "extract_fail":
    out.write("·E 0:00:09.4 GUI: Failed to install %s.\n" % pkg)
elif mode == "silent":
    pass                       # exits saying nothing at all
elif mode == "phantom":
    # Claims success but writes no game folder.
    out.write("·S 0:00:36.77 GUI: Successfully installed %s "
              "(title_id=NPEA90002, title=GT HD, version=02.00).\n" % pkg)
elif mode == "id_mismatch":
    # Disc-to-PKG conversion: PARAM.SFO keeps the DISC serial, extraction
    # goes to the CONTENT id. Observed live 2026-08-05 (Demon's Souls:
    # verdict said BLUS30443, files landed in NPUB30910). The installer must
    # follow the extracted path, not the verdict tuple.
    d = os.path.join(game, "NPUB30910", "USRDIR")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "EBOOT.BIN"), "w").write("eboot")
    out.write("·! 0:05:47.07 {PKG Installer 2} PKG: Created file "
              "%s/NPUB30910/USRDIR/EBOOT.BIN\n" % game)
    out.write("·S 0:05:47.45 GUI: Successfully installed %s "
              "(title_id=BLUS30443, title=Demon's Souls, version=01.00).\n" % pkg)
out.close()
# The rig's observed exit: killed by SIGTERM in teardown, AFTER the work.
sys.exit(143)
'''


def build_pkg_rig(tmp):
    """A fresh rig: ROCKNIX has seeded the config dir but no game has ever
    been launched, so the dev_* links do not exist yet."""
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    etk = os.path.join(tmp, "etk")
    os.makedirs(cfg)
    open(os.path.join(cfg, "config.yml"), "w").write("VFS:\n")
    os.makedirs(os.path.join(bios, "dev_hdd0", "game"))
    os.makedirs(os.path.join(etk, "pkg_install_drop"))
    os.makedirs(os.path.join(tmp, "ps3"))
    os.makedirs(os.path.join(tmp, "shm"))

    fake = os.path.join(tmp, "fake_rpcs3.py")
    open(fake, "w").write(FAKE_RPCS3)
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)

    pit.RPCS3_CFG_DIR = cfg
    pit.RPCS3_BIOS_DIR = bios
    pit.RPCS3_CFG_GAME_DIR = os.path.join(cfg, "dev_hdd0", "game")
    pit.RPCS3_GAME_DIR = os.path.join(bios, "dev_hdd0", "game")
    pit.RPCS3_CFG_EXDATA_DIR = os.path.join(cfg, "dev_hdd0", "home", "00000001", "exdata")
    pit.RPCS3_EXDATA_DIR = os.path.join(bios, "dev_hdd0", "home", "00000001", "exdata")
    pit.RPCS3_CUSTOM_CONFIGS = os.path.join(bios, "custom_configs")
    pit.RPCS3_LOG = os.path.join(tmp, "RPCS3.log")
    pit.RPCS3_BIN = sys.executable
    pit.PKG_STAGING_DIR = os.path.join(etk, "pkg_install_drop")
    pit.PS3_ROMS_DIR = os.path.join(tmp, "ps3")
    pit.SHM_DIR = os.path.join(tmp, "shm")
    pit.ETK_INSTALL_LOCK = os.path.join(tmp, "shm", "etk_install_lock")
    pit.ROCKNIX_SYSTEM_CFG = os.path.join(tmp, "system.cfg")
    pit.ETK_TEMPLATE_CONFIG = os.path.join(tmp, "etk_template.yml")
    open(pit.ETK_TEMPLATE_CONFIG, "w").write("Video:\n  Resolution Scale: 100\n")

    pit._rpcs3_running = lambda: False
    pit._game_running = lambda: False
    pit._kill_rpcs3 = lambda: None
    pit._kill_installer_rpcs3 = lambda: None
    pit._storage_incoherent_msg = lambda: None
    # RPCS3_BIN is python3; prepend the fake script so argv lines up. Always
    # wrap the PRISTINE Popen — wrapping the wrapper nests and mangles argv.
    def popen(cmd, **kw):
        return REAL_POPEN([sys.executable, fake] + list(cmd[1:]), **kw)
    pit.subprocess.Popen = popen
    return cfg, bios, etk


def stage_pkg(etk, name="game.pkg", rap=True):
    p = os.path.join(etk, "pkg_install_drop", name)
    with open(p, "wb") as f:
        f.write(b"\x7fPKG" + b"\0" * 0x2c + b"EP9000-NPEA90002_00-XXXX".ljust(48, b"\0"))
    raps = []
    if rap:
        r = os.path.join(etk, "pkg_install_drop", "EP9000-NPEA90002_00-AAA.rap")
        open(r, "wb").write(b"\0" * 16)
        raps.append(r)
    # AppleDouble junk a Mac Finder copy leaves behind
    open(os.path.join(etk, "pkg_install_drop", "._" + name), "w").write("x")
    return p, raps


def run(mode, tmp, **kw):
    cfg, bios, etk = build_pkg_rig(tmp)
    pkg, raps = stage_pkg(etk, **kw)
    os.environ["FAKE_MODE"] = mode
    os.environ["FAKE_LOG"] = pit.RPCS3_LOG
    # The fake writes where RPCS3 would: through the config dir, resolved.
    os.environ["FAKE_GAMEDIR"] = pit.RPCS3_CFG_GAME_DIR
    n = FakeNotifier()
    ok, lines = pit._run_install(pkg, raps, n)
    return ok, lines, cfg, bios, etk, pkg, raps


print("\n[9] pad binding repair (ROCKNIX ships a device that does not exist)")
# The stock ROCKNIX pad config, verbatim head from /usr/config/rpcs3/
# input_configs/global/Default.yml on the rig (2026-07-24). "InputPlumber
# GameController 1" is the OLD Xbox-style virtual pad; InputPlumber now targets
# a DualSense, so RPCS3 matches nothing and falls back to NullPadHandler
# (pad_thread.cpp:206) — pad completely dead in game.
STOCK_PAD = """Player 1 Input:
  Handler: SDL
  Device: InputPlumber GameController 1
  Config:
    Left Stick Left: LS X-
    Cross: South
    Left Trigger Threshold: 25
    Right Trigger Threshold: 25
    Left Stick Deadzone: 8000
Player 2 Input:
  Handler: Null
  Device: Null
  Config:
    Left Stick Left: ""
"""


def pad_rig(tmp, text=STOCK_PAD, names=("DualSense Wireless Controller 1",),
            running=False):
    cfgp = os.path.join(tmp, "Default.yml")
    open(cfgp, "w").write(text)
    pit.RPCS3_PAD_CONFIG = cfgp
    pit._sdl_gamepad_names = lambda: list(names)
    pit._rpcs3_running = lambda: running
    pit._game_running = lambda: running
    return cfgp


tmp = tempfile.mkdtemp()
try:
    cfgp = pad_rig(tmp)
    check("reads the stale device",
          pit._pad_player1(["Handler", "Device"]),
          {"Handler": "SDL", "Device": "InputPlumber GameController 1"})

    note = pit._ensure_pad_binding()
    body = open(cfgp).read()
    check("repair reported", any("DualSense Wireless Controller 1" in l for l in note), True)
    check("Device rewritten",
          pit._pad_player1(["Device"])["Device"], "DualSense Wireless Controller 1")
    check("indentation preserved",
          "  Device: DualSense Wireless Controller 1\n" in body, True)
    # The rest of the file is the operator's: trigger cal, deadzones, buttons.
    check("trigger calibration untouched", "Left Trigger Threshold: 25" in body, True)
    check("button map untouched", "Cross: South" in body, True)
    check("deadzone untouched", "Left Stick Deadzone: 8000" in body, True)
    check("Player 2 block untouched",
          "Player 2 Input:\n  Handler: Null\n  Device: Null\n" in body, True)
    check("only ONE Device line changed", body.count("Device:"), 2)

    # Idempotent: a correct config is left completely alone.
    before = open(cfgp).read()
    check("second run is silent", pit._ensure_pad_binding(), [])
    check("second run byte-identical", open(cfgp).read(), before)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[9b] pad binding: refuses to guess")
tmp = tempfile.mkdtemp()
try:
    # No pad attached / SDL unavailable -> we have no better name, so do nothing.
    cfgp = pad_rig(tmp, names=())
    check("no SDL names -> no change", pit._ensure_pad_binding(), [])
    check("config untouched",
          pit._pad_player1(["Device"])["Device"], "InputPlumber GameController 1")

    # RPCS3 running -> it rewrites this file on exit, so a write now vanishes.
    cfgp = pad_rig(tmp, running=True)
    check("RPCS3 running -> no change", pit._ensure_pad_binding(), [])
    check("config untouched while running",
          pit._pad_player1(["Device"])["Device"], "InputPlumber GameController 1")

    # A non-SDL handler uses a different naming scheme; leave the operator's.
    cfgp = pad_rig(tmp, text=STOCK_PAD.replace("Handler: SDL", "Handler: Evdev", 1),
                   running=False)
    check("non-SDL handler left alone", pit._ensure_pad_binding(), [])

    # Already correct, but with a SECOND identical pad attached.
    cfgp = pad_rig(tmp, text=STOCK_PAD.replace(
        "InputPlumber GameController 1", "DualSense Wireless Controller 2", 1),
        names=("DualSense Wireless Controller 1", "DualSense Wireless Controller 2"))
    check("player on pad 2 of 2 left alone", pit._ensure_pad_binding(), [])

    # No pad config at all (fresh rig before RPCS3 ever ran).
    pit.RPCS3_PAD_CONFIG = os.path.join(tmp, "nope.yml")
    check("missing config -> silent", pit._ensure_pad_binding(), [])

    # Kill-switch (ETK_PAD_BIND=0) must leave a broken config exactly as-is.
    cfgp = pad_rig(tmp)
    pit.PAD_BIND_ENABLED = False
    try:
        check("kill-switch -> no repair", pit._ensure_pad_binding(), [])
        check("kill-switch -> config untouched",
              pit._pad_player1(["Device"])["Device"], "InputPlumber GameController 1")
    finally:
        pit.PAD_BIND_ENABLED = True
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[9c] SDL name -> RPCS3 device string (the ' <N>' counter)")
def fake_probe(stdout, rc=0):
    """Drive the REAL _sdl_gamepad_names with a canned SDL probe result."""
    class R:
        returncode = rc
    R.stdout = stdout
    R.stderr = ""
    pit.subprocess.run = lambda *a, **k: R
    return REAL_SDL_NAMES


saved_run = pit.subprocess.run
try:
    f = fake_probe("DualSense Wireless Controller\n")
    check("single pad -> ' 1'", f(), ["DualSense Wireless Controller 1"])
    f = fake_probe("DualSense Wireless Controller\nDualSense Wireless Controller\n")
    check("two identical pads -> ' 1', ' 2'", f(),
          ["DualSense Wireless Controller 1", "DualSense Wireless Controller 2"])
    f = fake_probe("Xbox 360 Controller\nDualSense Wireless Controller\n")
    check("mixed pads keep order + own counters", f(),
          ["Xbox 360 Controller 1", "DualSense Wireless Controller 1"])
    f = fake_probe("", rc=2)
    check("probe failure -> empty (never guesses)", f(), [])
finally:
    pit.subprocess.run = saved_run

print("\n[E2E-1] happy path on a FRESH rig (links absent, rpcs3 exits 143)")
tmp = tempfile.mkdtemp()
try:
    ok, lines, cfg, bios, etk, pkg, raps = run("success", tmp)
    check("reported success DESPITE exit 143", ok, True)
    check("named the game from the log",
          any("GRAN TURISMO HD Concept" in l for l in lines), True)
    check("named the title id", any("NPEA90002" in l for l in lines), True)
    check("reported the version", any("v02.00" in l for l in lines), True)
    check("game landed in the GAMES tree, not the config tree",
          os.path.isfile(os.path.join(bios, "dev_hdd0", "game",
                                      "NPEA90002", "USRDIR", "EBOOT.BIN")), True)
    check("config dir is now a symlink into the games tree",
          os.path.islink(os.path.join(cfg, "dev_hdd0")), True)
    psn = os.listdir(pit.PS3_ROMS_DIR)
    check("launcher written", psn, ["GRAN TURISMO HD Concept.psn"])
    check("launcher holds the raw title id",
          open(os.path.join(pit.PS3_ROMS_DIR, psn[0])).read(), "NPEA90002")
    check("mangohud key upserted",
          'ps3["GRAN TURISMO HD Concept.psn"].rocknix.mangohud.enabled=1'
          in open(pit.ROCKNIX_SYSTEM_CFG).read(), True)
    check("etk config seeded",
          os.path.isfile(os.path.join(pit.RPCS3_CUSTOM_CONFIGS,
                                      "config_NPEA90002.yml")), True)
    check("rap installed to exdata",
          os.listdir(os.path.realpath(pit.RPCS3_CFG_EXDATA_DIR)),
          ["EP9000-NPEA90002_00-AAA.rap"])
    check("staging drained (pkg + rap + AppleDouble)",
          os.listdir(os.path.join(etk, "pkg_install_drop")), [])
    check("install lock released", os.path.exists(pit.ETK_INSTALL_LOCK), False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[E2E-2] failure modes keep the staged files and explain themselves")
cases = [
    ("version", "UPDATE", "version-mismatch"),
    ("invalid", "could not read", "corrupt-package"),
    ("extract_fail", "unpacking", "extraction-failure"),
    ("silent", "without confirming", "silent-exit"),
    ("phantom", "folder is missing", "success-but-no-folder"),
]
for mode, needle, label in cases:
    tmp = tempfile.mkdtemp()
    try:
        ok, lines, cfg, bios, etk, pkg, raps = run(mode, tmp)
        body = " ".join(lines)
        check(f"{label}: reported failure", ok, False)
        check(f"{label}: explained ({needle!r})", needle in body, True)
        check(f"{label}: .pkg kept for retry", os.path.isfile(pkg), True)
        check(f"{label}: no launcher written", os.listdir(pit.PS3_ROMS_DIR), [])
        check(f"{label}: lock released", os.path.exists(pit.ETK_INSTALL_LOCK), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n[E2E-3] a PREVIOUS run's success in the log cannot be read as ours")
tmp = tempfile.mkdtemp()
try:
    cfg, bios, etk = build_pkg_rig(tmp)
    pkg, raps = stage_pkg(etk)
    # Seed the log with a stale success for THIS same path, then make the
    # fake rpcs3 fail to launch at all (log untouched).
    with open(pit.RPCS3_LOG, "w") as f:
        f.write("Successfully installed %s (title_id=NPEA90002, "
                "title=GT HD, version=02.00).\n" % pkg)
    pit.subprocess.Popen = lambda cmd, **kw: REAL_POPEN(
        [sys.executable, "-c", "raise SystemExit(1)"], **kw)
    ok, lines = pit._run_install(pkg, raps, FakeNotifier())
    check("stale success not claimed", ok, False)
    check("no launcher written", os.listdir(pit.PS3_ROMS_DIR), [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[E2E-4] no rap staged is fine")
tmp = tempfile.mkdtemp()
try:
    ok, lines, cfg, bios, etk, pkg, raps = run("success", tmp, rap=False)
    check("installs without a licence", ok, True)
    check("says none staged", any("none staged" in l for l in lines), True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

FAKE_FW = r'''#!/usr/bin/env python3
import os, sys, gzip
mode = os.environ["FAKE_MODE"]
log  = os.environ["FAKE_LOG"]
df   = os.environ["FAKE_DEVFLASH"]
if os.path.exists(log):
    with open(log,"rb") as f, gzip.open(log+".gz","wb") as g: g.write(f.read())
out = open(log, "r+" if os.path.exists(log) else "w")
out.truncate(0); out.seek(0); out.write("RPCS3 v0.0.41\n")
if mode == "success":
    d = os.path.join(df,"vsh","etc"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d,"version.txt"),"w").write("release:04.9300:\nbuild:1,2:x\n")
    out.write("·S 0:00:41.2 SYS: Successfully installed PS3 firmware version 4.93\n")
    rc = 0
elif mode == "error":
    out.write("·E 0:00:02.1 SYS: Error while installing firmware: Firmware file is invalid.\n")
    rc = 1
else:
    rc = 1
out.close(); sys.exit(rc)
'''


def build_fw_rig(tmp):
    cfg = os.path.join(tmp, ".config", "rpcs3")
    bios = os.path.join(tmp, "roms", "bios", "rpcs3")
    drop = os.path.join(tmp, "firmware_drop")
    os.makedirs(cfg)
    open(os.path.join(cfg, "config.yml"), "w").write("VFS:\n")
    os.makedirs(bios)
    os.makedirs(drop)
    fake = os.path.join(tmp, "fake.py")
    open(fake, "w").write(FAKE_FW)

    pit.RPCS3_CFG_DIR, pit.RPCS3_BIOS_DIR = cfg, bios
    pit.RPCS3_DEV_FLASH = os.path.join(cfg, "dev_flash")
    pit.RPCS3_FW_VERSION_FILE = os.path.join(pit.RPCS3_DEV_FLASH, "vsh", "etc", "version.txt")
    pit.RPCS3_LOG = os.path.join(tmp, "RPCS3.log")
    pit.RPCS3_BIN = sys.executable
    pit.SHM_DIR = os.path.join(tmp, "shm")
    pit.ETK_INSTALL_LOCK = os.path.join(tmp, "shm", "lock")
    pit.FIRMWARE_DROP_DIR = drop
    pit._rpcs3_running = lambda: False
    pit._game_running = lambda: False
    pit._kill_rpcs3 = lambda: None
    pit._kill_installer_rpcs3 = lambda: None
    pit._storage_incoherent_msg = lambda: None

    def popen(cmd, **kw):
        return REAL_POPEN([sys.executable, fake] + list(cmd[1:]), **kw)
    pit.subprocess.Popen = popen

    pup = os.path.join(drop, "PS3UPDAT.PUP")
    open(pup, "wb").write(b"SCEUF\0" * 10)
    os.environ["FAKE_LOG"] = pit.RPCS3_LOG
    os.environ["FAKE_DEVFLASH"] = os.path.join(bios, "dev_flash")
    return cfg, bios, pup


print("\n[E2E-5] REPEAT install of the SAME package (the 0.8.1 field failure)")
# This is the case that actually bit: installing the same .pkg twice writes a
# BYTE-IDENTICAL RPCS3.log the second time, rewritten in place over the first.
# 0.8.1 marked (size, inode) before launch and read "size unchanged" as "RPCS3
# wrote nothing", so run 2 of a perfectly good install reported failure. No
# single-run case can catch this — it needs two runs against one log file.
tmp = tempfile.mkdtemp()
try:
    cfg, bios, etk = build_pkg_rig(tmp)
    os.environ["FAKE_MODE"] = "success"
    os.environ["FAKE_LOG"] = pit.RPCS3_LOG
    os.environ["FAKE_GAMEDIR"] = pit.RPCS3_CFG_GAME_DIR

    pkg1, raps1 = stage_pkg(etk)
    ok1, _ = pit._run_install(pkg1, raps1, FakeNotifier())
    size1 = os.path.getsize(pit.RPCS3_LOG)
    ino1 = os.stat(pit.RPCS3_LOG).st_ino
    check("run 1 succeeds", ok1, True)

    # Re-stage the identical package and install it again, as the operator did.
    for f in os.listdir(pit.PS3_ROMS_DIR):
        os.remove(os.path.join(pit.PS3_ROMS_DIR, f))
    pkg2, raps2 = stage_pkg(etk)
    ok2, lines2 = pit._run_install(pkg2, raps2, FakeNotifier())
    size2 = os.path.getsize(pit.RPCS3_LOG)
    ino2 = os.stat(pit.RPCS3_LOG).st_ino

    check("run 2 wrote a same-SIZE log (the trap)", size2, size1)
    check("run 2 reused the inode (the trap)", ino2, ino1)
    check("run 2 ALSO succeeds", ok2, True)
    check("run 2 wrote the launcher",
          os.listdir(pit.PS3_ROMS_DIR), ["GRAN TURISMO HD Concept.psn"])
    check("run 2 drained staging", os.listdir(os.path.join(etk, "pkg_install_drop")), [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[E2E-4] disc-to-PKG id mismatch: follow the extracted id, not the verdict")
# Live 2026-08-05 (Demon's Souls): PARAM.SFO carried the DISC serial
# (BLUS30443) so RPCS3's verdict line named it, but extraction went to the
# CONTENT id (NPUB30910). 0.8.3 trusted the verdict, found no folder, and
# abandoned a complete 8 GB install with no launcher, licence or config.
tmp = tempfile.mkdtemp()
try:
    cfg, bios, etk = build_pkg_rig(tmp)
    os.environ["FAKE_MODE"] = "id_mismatch"
    os.environ["FAKE_LOG"] = pit.RPCS3_LOG
    os.environ["FAKE_GAMEDIR"] = pit.RPCS3_CFG_GAME_DIR

    pkg, raps = stage_pkg(etk)
    ok, lines = pit._run_install(pkg, raps, FakeNotifier())
    check("install reported success", ok, True)
    check("launcher keyed to the EXTRACTED id",
          open(os.path.join(pit.PS3_ROMS_DIR,
                            os.listdir(pit.PS3_ROMS_DIR)[0])).read(), "NPUB30910")
    check("staging drained",
          os.listdir(os.path.join(etk, "pkg_install_drop")), [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[FW-1] success on a fresh rig")
tmp = tempfile.mkdtemp()
try:
    cfg, bios, pup = build_fw_rig(tmp)
    os.environ["FAKE_MODE"] = "success"
    ok, lines = pit._run_install_fw(pup, FakeNotifier())
    check("reported success", ok, True)
    check("version parsed from RPCS3's log",
          any("4.93" in l for l in lines), True)
    check("dev_flash linked into the games tree",
          os.path.islink(os.path.join(cfg, "dev_flash")), True)
    check("firmware landed in the GAMES tree",
          os.path.isfile(os.path.join(bios, "dev_flash", "vsh", "etc", "version.txt")), True)
    check(".pup KEPT (reusable asset)", os.path.isfile(pup), True)
    check("lock released", os.path.exists(pit.ETK_INSTALL_LOCK), False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[FW-2] RPCS3's own error reason is surfaced")
tmp = tempfile.mkdtemp()
try:
    cfg, bios, pup = build_fw_rig(tmp)
    os.environ["FAKE_MODE"] = "error"
    ok, lines = pit._run_install_fw(pup, FakeNotifier())
    check("reported failure", ok, False)
    check("quoted RPCS3's reason",
          any("Firmware file is invalid" in l for l in lines), True)
    check(".pup kept", os.path.isfile(pup), True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[FW-3] silent exit with no firmware present -> failure")
tmp = tempfile.mkdtemp()
try:
    cfg, bios, pup = build_fw_rig(tmp)
    os.environ["FAKE_MODE"] = "silent"
    ok, lines = pit._run_install_fw(pup, FakeNotifier())
    check("reported failure", ok, False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()

print("\n[N] notebook seed: a shipped per-title tune outranks the generic template")
# 0.9.0: config/config_<ID>.yml (the committed notebook, bundled on the card
# and pushed by install.sh) used to ride along unused — every fresh rig seeded
# every title from etk_template.yml. Now a title WITH a shipped tune seeds from
# it; a title without one still gets the template; an existing config is never
# touched; the kill-switch restores template-only. Discriminating: the old code
# copies the template for both titles.
with tempfile.TemporaryDirectory() as tmp:
    cfg, bios, etk = build_pkg_rig(tmp)
    nb = os.path.join(etk, "config"); os.makedirs(nb, exist_ok=True)
    pit.ETK_NOTEBOOK_DIR = nb
    pit.NOTEBOOK_SEED_ENABLED = True
    pit.TELEMETRY_DIR = os.path.join(etk, "etk_telemetry")
    pit.CONFIG_CHANGES_LEDGER = os.path.join(pit.TELEMETRY_DIR, "config_changes.tsv")
    open(os.path.join(nb, "config_NPEA00050.yml"), "w").write("Video:\n  Resolution Scale: 66\n")
    check("notebook: tuned title seeds from its shipped tune", pit._golden_seed_config("NPEA00050"), "seeded")
    check("notebook: shipped tune content landed",
          open(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA00050.yml")).read(), "Video:\n  Resolution Scale: 66\n")
    check("notebook: untuned title still seeds the template", pit._golden_seed_config("NPUB30457"), "seeded")
    check("notebook: template content landed for the untuned title",
          open(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPUB30457.yml")).read(), "Video:\n  Resolution Scale: 100\n")
    check("notebook: existing config kept", pit._golden_seed_config("NPEA00050"), "kept existing")
    led = open(pit.CONFIG_CHANGES_LEDGER).read()
    check("notebook: ledger names the provenance", "NOTEBOOK SEED" in led and "GOLDEN SEED" in led, True)
    os.remove(pit.CONFIG_CHANGES_LEDGER)
    check("notebook: installer path uses the shipped tune too",
          (pit._deploy_template_config("NPEA90002"), os.path.exists(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA90002.yml"))), ("applied", True))
    open(os.path.join(nb, "config_NPEA90002.yml"), "w").write("Video:\n  Resolution Scale: 85\n")
    os.remove(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA90002.yml"))
    check("notebook: installer path copies the tune when present",
          (pit._deploy_template_config("NPEA90002"), open(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA90002.yml")).read()),
          ("applied", "Video:\n  Resolution Scale: 85\n"))
    # Found on the 0.9.0 card (2026-09-03): the PKG installer seeded the shipped
    # GT5P tune byte-for-byte and config_changes.tsv did not exist afterwards —
    # only the startup sweep ever ledgered its seeds. Provenance is the point.
    led2 = open(pit.CONFIG_CHANGES_LEDGER).read() if os.path.exists(pit.CONFIG_CHANGES_LEDGER) else ""
    check("notebook: installer path ledgers its seeds (template then notebook)",
          ("NPEA90002\tGOLDEN SEED" in led2, "NPEA90002\tNOTEBOOK SEED" in led2), (True, True))
    pit.NOTEBOOK_SEED_ENABLED = False
    os.remove(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA00050.yml"))
    pit._golden_seed_config("NPEA00050")
    check("notebook: kill-switch falls back to the template",
          open(os.path.join(pit.RPCS3_CUSTOM_CONFIGS, "config_NPEA00050.yml")).read(), "Video:\n  Resolution Scale: 100\n")
    pit.NOTEBOOK_SEED_ENABLED = True

if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL INSTALLER CHECKS PASSED")
