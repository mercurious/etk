#!/usr/bin/env python3
"""ETK -- the HUD dash: the in-game HUD preset, picked in Pitstop TOOLS (2026-10-27).

  python3 tools/test_hud_dash.py [--against REV]

The operator could only change the HUD's instrument set (ETK_HUD_MODE) by editing etk.conf
and reinstalling. TOOLS > HUD dash now cycles G-INSTR -> BASIC -> LEAN -> COMPACT on the rig;
COMPACT is a new MangoHud view (config/MangoHud.compact.conf: hud_compact, battery icon + watts,
GPU load + clock, RAM) carrying the LEAN body. The battery icon (autogamer's) rides every ETK dash.

  [ENV]     scripts/env.sh: a picked dash outranks etk.conf's mode (read live: the bridge
            re-sources env.sh every tick); no file / unknown = etk.conf's mode, full view; a file
            with no newline still reads; a set -e caller survives a missing file
  [APPLY]   bin/hud_apply.sh builds top/bottom/off from the compact conf when the view is compact
            (from the master otherwise, and when the compact conf is missing); 'default' is stock
  [CONF]    every ETK dash conf keeps the control socket, the ETK field, the per-frame CSV the
            ledger's fps columns read, the no_display/position lines the punchbox seds, and the
            battery icon; COMPACT is compact; Latin-1 only
  [PITSTOP] the cycle order, the fallback to etk.conf, the live conf rebuilt at its own stop on a
            cycle (stock 'default' left alone), a write that can't land reports the unchanged dash
  [SHIP]    install.sh STEP 4, the self-update set and the PS port (whole config/) all carry it

DISCRIMINATION: against the pre-dash tree (--against HEAD before this landed) every suite fails.
"""
import curses
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, os.pardir))
REV = sys.argv[sys.argv.index("--against") + 1] if "--against" in sys.argv else None
SRC = tempfile.mkdtemp(prefix="etk_hud_dash_src_")
FILES = ("bin/etk_pitstop.py", "bin/hud_apply.sh", "scripts/env.sh", "install.sh",
         "config/MangoHud.conf", "config/MangoHud.default.conf", "config/MangoHud.compact.conf",
         "windows_installer/etk-install.ps1")
for rel in FILES:
    os.makedirs(os.path.join(SRC, os.path.dirname(rel)), exist_ok=True)
    if REV:
        r = subprocess.run(["git", "-C", ROOT, "show", f"{REV}:{rel}"], capture_output=True)
        if r.returncode == 0:
            open(os.path.join(SRC, rel), "wb").write(r.stdout)
    elif os.path.exists(os.path.join(ROOT, rel)):
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(SRC, rel))

FIX = tempfile.mkdtemp(prefix="etk_hud_dash_")
TEL = os.path.join(FIX, "tel")
LIVE = os.path.join(FIX, "MangoHud.conf")
os.makedirs(TEL)
os.environ.update(ETK_ROOT=SRC, TELEMETRY_DIR=TEL, HUD_DASH_FILE=os.path.join(TEL, "hud_dash.txt"),
                  RIG_MANGO_CONF=LIVE, ETK_HUD_APPLY=os.path.join(SRC, "bin", "hud_apply.sh"),
                  SHM_DIR=os.path.join(FIX, "shm"), ETK_HUD_MODE="GINSTR", TARGET_ID="NPEA00050",
                  ETK_NO_TARGET="0")
DASH = os.environ["HUD_DASH_FILE"]
FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, detail=""):
    check(name + (f" ({detail})" if detail and not cond else ""), bool(cond), True)


def suite(tag):
    def wrap(fn):
        print(f"\n[{tag}]  {fn.__doc__}")
        try:
            fn()
        except Exception as e:      # a missing piece fails the suite, never the run
            check(f"{tag} ran", repr(e), "no exception")
        return fn
    return wrap


def read(path):
    try:
        return open(path, encoding="latin-1").read()
    except Exception:
        return ""


def dash(value):
    if value is None:
        if os.path.exists(DASH):
            os.remove(DASH)
    else:
        open(DASH, "w").write(value)


@suite("ENV")
def _env():
    """a picked dash outranks etk.conf; absent/unknown = etk.conf's mode in the full view."""
    env = read(os.path.join(SRC, "scripts", "env.sh"))
    m = re.search(r'^export HUD_DASH_FILE=.*?^export ETK_HUD_MODE ETK_HUD_VIEW$', env, re.S | re.M)
    check_true("env.sh has the dash block", m)
    block = m.group(0) if m else "exit 9"

    def run(conf_mode, value, set_e=False):
        dash(value)
        sh = ("set -e; " if set_e else "") + f'ETK_HUD_MODE={conf_mode}; {block}\necho "$ETK_HUD_MODE $ETK_HUD_VIEW"'
        r = subprocess.run(["bash", "-c", sh], capture_output=True, text=True,
                           env=dict(os.environ, TELEMETRY_DIR=TEL))
        return r.stdout.strip() or f"rc={r.returncode}"
    check("no file: etk.conf's mode, full", run("BASIC", None), "BASIC full")
    check("no file under set -e: still runs", run("LEAN", None, set_e=True), "LEAN full")
    for v, want in (("ginstr\n", "GINSTR full"), ("basic\n", "BASIC full"), ("lean\n", "LEAN full"),
                    ("full\n", "FULL full"), ("compact\n", "LEAN compact")):
        check(f"picked {v.strip()} over etk.conf GINSTR", run("GINSTR", v), want)
    check("no trailing newline still reads", run("GINSTR", "compact"), "LEAN compact")
    check("unknown value: etk.conf's mode", run("BASIC", "turbo\n"), "BASIC full")
    check("the bridge re-reads env.sh every tick (live)",
          "source /storage/games-internal/roms/etk/scripts/env.sh" in
          read(os.path.join(ROOT, "bin", "mango_bridge.sh")).split("while true; do", 1)[-1], True)
    dash(None)


def apply(state, view, cfg_root=None):
    subprocess.run(["bash", os.path.join(SRC, "bin", "hud_apply.sh"), state],
                   env=dict(os.environ, ETK_HUD_VIEW=view, ETK_ROOT=cfg_root or SRC),
                   capture_output=True, timeout=20)
    return read(LIVE)


@suite("APPLY")
def _apply():
    """top/bottom/off come from the compact conf in the compact view, else the master."""
    for st, pos in (("top", "top-left"), ("bottom", "bottom-left")):
        body = apply(st, "compact")
        check_true(f"compact {st}: hud_compact", re.search(r"^hud_compact$", body, re.M))
        check_true(f"compact {st}: {pos}", re.search(rf"^position={pos}$", body, re.M))
        body = apply(st, "full")
        check_true(f"full {st}: the master (no hud_compact)",
                   "live_stat.txt" in body and not re.search(r"^hud_compact", body, re.M))
        check_true(f"full {st}: {pos}", re.search(rf"^position={pos}$", body, re.M))
    body = apply("off", "compact")
    check_true("compact off: hidden, still the compact conf",
               re.search(r"^no_display=1$", body, re.M) and re.search(r"^hud_compact$", body, re.M))
    body = apply("default", "compact")
    check_true("default: the stock readout, whatever the dash", "live_stat.txt" not in body and "gpu_stats" in body)
    bare = tempfile.mkdtemp(prefix="etk_hud_bare_")
    os.makedirs(os.path.join(bare, "config"))
    shutil.copy2(os.path.join(SRC, "config", "MangoHud.conf"), os.path.join(bare, "config"))
    body = apply("top", "compact", cfg_root=bare)
    check_true("compact with no compact conf: the master, not a blank HUD",
               "live_stat.txt" in body and re.search(r"^position=top-left$", body, re.M))


@suite("CONF")
def _conf():
    """every ETK dash keeps the socket, field, ledger CSV, punchbox lines and the battery icon."""
    for name in ("MangoHud.conf", "MangoHud.compact.conf"):
        body = read(os.path.join(SRC, "config", name))
        check_true(f"{name} exists", body)
        for key in ("control=mangohud", "exec=cat /dev/shm/etk_shm/live_stat.txt",
                    "output_folder=/dev/shm/etk_shm/mangolog", "log_interval=0", "autostart_log=1",
                    "battery_icon", "battery"):
            check_true(f"{name}: {key}", re.search(rf"^{re.escape(key)}\b", body, re.M))
        for key in ("no_display", "position"):
            check_true(f"{name}: a {key}= line for the punchbox sed", re.search(rf"^{key}=", body, re.M))
        try:
            open(os.path.join(SRC, "config", name), encoding="latin-1").read().encode("latin-1")
            ok = all(ord(c) < 256 for c in body)
        except Exception:
            ok = False
        check_true(f"{name}: Latin-1 only", ok)
    body = read(os.path.join(SRC, "config", "MangoHud.compact.conf"))
    for key in ("hud_compact", "battery_watt", "gpu_stats", "gpu_core_clock", "ram"):
        check_true(f"compact: {key}", re.search(rf"^{key}$", body, re.M))


@suite("PITSTOP")
def _pitstop():
    """the cycle, the etk.conf fallback, the live conf rebuilt at its stop, honest failure."""
    spec = importlib.util.spec_from_file_location("pit", os.path.join(SRC, "bin", "etk_pitstop.py"))
    pit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pit)
    curses.color_pair = lambda n: 0
    dash(None)
    os.environ["ETK_HUD_MODE"] = "LEAN"
    check("no file: etk.conf's mode", pit._read_hud_dash(), "lean")
    os.environ["ETK_HUD_MODE"] = "GINSTR"
    seen = [pit._read_hud_dash()]
    for _ in range(5):
        seen.append(pit._cycle_hud_dash())
    check("cycle order", seen, ["ginstr", "basic", "lean", "full", "compact", "ginstr"])
    check("persisted", read(DASH), "ginstr\n")
    apply("top", "full")                                    # the live conf at top, full view
    dash("full\n")
    check("cycle to compact", pit._cycle_hud_dash(), "compact")
    body = read(LIVE)
    check_true("...the live conf is the compact dash", re.search(r"^hud_compact$", body, re.M))
    check_true("...at its own stop (top)", re.search(r"^position=top-left$", body, re.M))
    apply("off", "compact")
    pit._cycle_hud_dash()                                   # compact -> ginstr
    body = read(LIVE)
    check_true("an off HUD stays off, on the new dash",
               re.search(r"^no_display=1$", body, re.M) and not re.search(r"^hud_compact", body, re.M))
    apply("default", "full")
    before = read(LIVE)
    pit._cycle_hud_dash()
    check("the stock 'default' stop is left alone", read(LIVE), before)
    os.remove(LIVE)
    pit._cycle_hud_dash()
    check_true("no live conf: none is made", not os.path.exists(LIVE))
    ro = os.path.join(FIX, "ro")
    os.makedirs(ro)
    os.chmod(ro, 0o500)
    real = pit.HUD_DASH_FILE
    pit.HUD_DASH_FILE = os.path.join(ro, "hud_dash.txt")
    try:
        was = pit._read_hud_dash()
        check("a write that can't land returns the unchanged dash", pit._cycle_hud_dash(), was)
        st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_HUDDASH_IDX}
        pit._tools_select(st)
        check_true("...and the footer says so", "could not save" in st.get("status", ""), st.get("status"))
    finally:
        pit.HUD_DASH_FILE = real
        os.chmod(ro, 0o700)
    dash("basic\n")
    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_HUDDASH_IDX}
    pit._tools_select(st)
    check("the footer names the new dash", st.get("status"), "HUD dash: LEAN - next game launch")

    class FakeScr:
        def __init__(self, h, w):
            self.h, self.w, self.writes = h, w, []

        def getmaxyx(self):
            return self.h, self.w

        def addstr(self, r, c, t, a=0):
            if r >= self.h or c >= self.w:
                raise curses.error
            self.writes.append((r, c, t))

        def __getattr__(self, n):
            return lambda *a, **k: None
    scr = FakeScr(22, 60)
    i = pit._TOOLS_HUDDASH_IDX
    pit.draw_tools(scr, {"tools_mode": "menu", "tools_cursor": i, "gamepad_status": ""})
    check("the row reads the dash", [t for r, c, t in scr.writes if c == 6 and t.startswith(f"{i + 1}. ")],
          [f"{i + 1}. HUD dash: LEAN"])
    check("60x22: both help lines", len([t for r, c, t in scr.writes if c == 4 and
                                         ("G-INSTR/BASIC" in t or "next game launch" in t)]), 2)
    dash(None)


@suite("SHIP")
def _ship():
    """install.sh STEP 4, the self-update set and the PS port all carry the compact conf."""
    ins = read(os.path.join(SRC, "install.sh"))
    check_true("install.sh pushes it to $ETK_ROOT/config/",
               re.search(r"tui_rsync .*\./config/MangoHud\.compact\.conf \$RIG_SSH:\$ETK_ROOT/config/", ins))
    pit = read(os.path.join(SRC, "bin", "etk_pitstop.py"))
    m = re.search(r"cfg_set = \((.*?)\)", pit, re.S)
    check_true("the self-update set has it", m and '"MangoHud.compact.conf"' in m.group(1))
    ps = read(os.path.join(SRC, "windows_installer", "etk-install.ps1"))
    check_true("the PS port pushes the whole config/ dir", 'Push-Dir -LocalDir (Join-Path $RepoRoot "config")' in ps)


shutil.rmtree(FIX, ignore_errors=True)
shutil.rmtree(SRC, ignore_errors=True)
print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL HUD DASH CHECKS PASSED")
