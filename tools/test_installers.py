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

_HERE = os.path.dirname(os.path.abspath(__file__))
PITSTOP = os.environ.get("ETK_PITSTOP_PY",
                         os.path.join(_HERE, os.pardir, "bin", "etk_pitstop.py"))
spec = importlib.util.spec_from_file_location('pit', PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

# Always wrap the PRISTINE Popen — wrapping a wrapper nests and mangles argv.
REAL_POPEN = subprocess.Popen
FAILS = []


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

print("\n[4] log slice: rotation / no-write / append")
tmp = tempfile.mkdtemp()
try:
    log = os.path.join(tmp, "RPCS3.log")
    pit.RPCS3_LOG = log
    with open(log, "w") as f:
        f.write("PREVIOUS RUN\nSuccessfully installed old.pkg (title_id=X, title=Y, version=1).\n")
    mark = pit._rpcs3_log_mark()

    # (a) no write at all -> empty, so a stale success can't be re-read
    check("unchanged log -> empty slice", pit._rpcs3_log_since(mark), "")

    # (b) rotated (new inode, smaller) -> whole file is ours
    os.remove(log)
    with open(log, "w") as f:
        f.write("FRESH\n")
    check("rotated log -> whole file", pit._rpcs3_log_since(mark), "FRESH\n")

    # (c) appended in place -> only the new tail
    with open(log, "w") as f:
        f.write("PREVIOUS RUN\nSuccessfully installed old.pkg (title_id=X, title=Y, version=1).\n")
    mark = pit._rpcs3_log_mark()
    with open(log, "a") as f:
        f.write("NEW TAIL\n")
    check("appended log -> tail only", pit._rpcs3_log_since(mark), "NEW TAIL\n")
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
if os.path.exists(log):
    with open(log, "rb") as f, gzip.open(log + ".gz", "wb") as g:
        g.write(f.read())
    os.remove(log)
out = open(log, "w")
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
    pit._kill_rpcs3 = lambda: None
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
    os.remove(log)
out = open(log,"w"); out.write("RPCS3 v0.0.41\n")
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
    pit._kill_rpcs3 = lambda: None
    pit._storage_incoherent_msg = lambda: None

    def popen(cmd, **kw):
        return REAL_POPEN([sys.executable, fake] + list(cmd[1:]), **kw)
    pit.subprocess.Popen = popen

    pup = os.path.join(drop, "PS3UPDAT.PUP")
    open(pup, "wb").write(b"SCEUF\0" * 10)
    os.environ["FAKE_LOG"] = pit.RPCS3_LOG
    os.environ["FAKE_DEVFLASH"] = os.path.join(bios, "dev_flash")
    return cfg, bios, pup


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
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL INSTALLER CHECKS PASSED")
