#!/usr/bin/env python3
"""ETK -- host-side regression tests for the AUTONOMY switch (Pitstop TOOLS, hunt grants P3).

Run from the repo root:   python3 tools/test_pitstop_autonomy.py

docs/AUTONOMY_SPEC.md §5: the operator's kill switch for an Engineer's crash hunt, at the
car. /storage/.config/etk-autonomy holding "on" = on; anything else = off (the default).
Three parties read it -- Pitstop (this switch), the car daemon (garage put/pin) and the
launch wrapper (the hunt override) -- so the suites pin all three to one path and one test.

  [STATE]     absent / unreadable / anything but "on" = off
  [TOGGLE]    off -> on writes "on" atomically (dot-tmp swept); on -> off removes the file;
              the result is READ BACK
  [FAIL]      an unwritable dir reports the unchanged state, strands no tmp
  [STATUS]    the footer names the new state, says when it fails, fits w-6 at 60 cols
  [DISPATCH]  CONFIRM on the Autonomy row flips Autonomy and nothing else; Pitlink's row
              never touches it; Firmware/Update (shifted) still reach their flows
  [DRAW]      the row reads ": on|off"; at the rig's 22 rows ALL ten entries AND both help
              lines show (the breathing line above the title gives way); every entry is
              reachable (its row drawn when it is the cursor); nothing lands on the footer
  [CONTRACT]  Pitstop's default path == the daemon's AUTONOMY_FILE == the wrapper's path in
              install.sh, and all three treat only "on" as on
  [UNINSTALL] uninstall.sh removes the switch, the rig grant and the hunt dirs (CLEAN)
DISCRIMINATION: against the pre-P3 files every suite fails:
    git show 5865a7f:bin/etk_pitstop.py > /tmp/p.py
    ETK_PITSTOP=/tmp/p.py python3 tools/test_pitstop_autonomy.py
"""
import curses
import importlib.util
import os
import re
import shutil
import stat
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, os.pardir))
PITSTOP = os.environ.get("ETK_PITSTOP", os.path.join(ROOT, "bin", "etk_pitstop.py"))
DAEMON = os.path.join(ROOT, "bin", "etk_pitlink_usbd.py")
INSTALL = os.path.join(ROOT, "install.sh")
UNINSTALL = os.environ.get("ETK_UNINSTALL", os.path.join(ROOT, "uninstall.sh"))

FIX = tempfile.mkdtemp(prefix="etk_autonomy_test_")
CONF = os.path.join(FIX, "storage", ".config")
AUTO = os.path.join(CONF, "etk-autonomy")
TEL = os.path.join(FIX, "tel")
os.environ.update(
    ETK_ROOT=FIX, TELEMETRY_DIR=TEL, SHM_DIR=os.path.join(FIX, "shm"),
    AUTONOMY_FILE=AUTO,
    PITLINK_PROFILE_D=os.path.join(CONF, "profile.d", "095-etk-pitlink"),
    PITLINK_USB_UNIT_FILE=os.path.join(FIX, "system.d", "etk-pitlink-usb.service"),
    SCREENSHOT_MODE_FILE=os.path.join(TEL, "screenshot_mode.txt"),
    BOG_CHORD_FILE=os.path.join(TEL, "bog_chord.txt"),
    PKG_STAGING_DIR=os.path.join(FIX, "pkg_install_drop"),
    FIRMWARE_DROP_DIR=os.path.join(FIX, "firmware_drop"),
    TURNIP_PROFILE_D=os.path.join(CONF, "profile.d", "097-etk-turnip-dials"),
    RECENT_ID_FILE=os.path.join(FIX, "last_played_id.txt"),
    TARGET_ID="NPEA00050", ETK_NO_TARGET="0",
)
spec = importlib.util.spec_from_file_location("pit", PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)
curses.color_pair = lambda n: 0

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


def suite(name):
    def deco(fn):
        print(f"\n[{name}]  {fn.__doc__}")
        try:
            fn()
        except Exception as e:                       # noqa: BLE001
            print(f"    FAIL {name} raised: {e.__class__.__name__}: {e}")
            FAILS.append(f"{name}:raised")
        return fn
    return deco


def reset():
    if os.path.isdir(CONF):
        os.chmod(CONF, 0o755)
        shutil.rmtree(CONF)
    os.makedirs(os.path.join(CONF, "profile.d"))


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class FakeScr:
    def __init__(self, h, w):
        self.h, self.w, self.writes = h, w, []

    def getmaxyx(self):
        return (self.h, self.w)

    def addstr(self, row, col, text, attr=0):
        if row < 0 or row >= self.h or col < 0 or col + len(text) > self.w:
            raise curses.error("addwstr() returned ERR")
        if row == self.h - 1 and col + len(text) == self.w:
            raise curses.error("addwstr() returned ERR")
        self.writes.append((row, col, text))

    def attron(self, a):
        pass

    def attroff(self, a):
        pass

    def move(self, r, c):
        pass

    def clrtoeol(self):
        pass


@suite("STATE")
def _state():
    """absent / unreadable / anything but "on" = off."""
    reset()
    check("absent -> off", pit._read_autonomy_state(), "off")
    for body, want in (("on\n", "on"), ("on", "on"), ("on\n\n", "on"), (" on \n", "off"), ("off\n", "off"), ("1\n", "off"),
                       ("ON\n", "off"), ("", "off"), ("on\nx\n", "off")):
        write(AUTO, body)
        check(f"{body!r} -> {want}", pit._read_autonomy_state(), want)
    os.remove(AUTO)
    os.makedirs(AUTO)
    check("a directory there -> off", pit._read_autonomy_state(), "off")
    os.rmdir(AUTO)


@suite("TOGGLE")
def _toggle():
    """off -> on writes "on"; on -> off removes; read back."""
    reset()
    check("off -> on returns on", pit._toggle_autonomy(), "on")
    with open(AUTO) as f:
        check("the file holds exactly 'on'", f.read(), "on\n")
    check("no tmp left", [n for n in os.listdir(CONF) if n.endswith(".tmp")], [])
    check("on -> off returns off", pit._toggle_autonomy(), "off")
    check("off means NO file (the default)", os.path.exists(AUTO), False)


@suite("FAIL")
def _fail():
    """an unwritable dir reports the unchanged state."""
    if IS_ROOT:
        print("    skip (root ignores chmod)")
        return
    reset()
    os.chmod(CONF, 0o555)
    try:
        check("unwritable: still off", pit._toggle_autonomy(), "off")
        check("unwritable: no tmp stranded", [n for n in os.listdir(CONF) if "tmp" in n], [])
    finally:
        os.chmod(CONF, 0o755)


@suite("STATUS")
def _status():
    """names the new state; failure says so; fits 54 cols."""
    for was, now in (("off", "on"), ("on", "off")):
        s = pit._autonomy_status(was, now)
        check_true(f"{was}->{now}: names {now}", f": {now} " in s, s)
        check_true(f"{was}->{now}: fits ({len(s)} <= 54)", len(s) <= 54, s)
    check_true("off says hunts stop now", "now" in pit._autonomy_status("on", "off"))
    for st in ("on", "off"):
        s = pit._autonomy_status(st, st)
        check_true(f"unchanged {st}: says it could not save", "could not" in s and f"still {st}" in s, s)


@suite("DISPATCH")
def _dispatch():
    """CONFIRM on Autonomy flips it alone; Pitlink's row never touches it."""
    reset()
    os.makedirs(TEL, exist_ok=True)
    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_AUTONOMY_IDX}
    pl = pit._read_pitlink_state()
    check("select returns continue", pit._tools_select(st), "continue")
    check("autonomy flipped off -> on", pit._read_autonomy_state(), "on")
    check("pitlink untouched", pit._read_pitlink_state(), pl)
    check("status is the honest line", st.get("status"), pit._autonomy_status("off", "on"))
    check("no blocking action", st.get("tools_action"), None)
    pit._tools_select(st)
    check("again: off", pit._read_autonomy_state(), "off")
    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_PITLINK_IDX}
    pit._tools_select(st)
    check("Pitlink row: autonomy still off", pit._read_autonomy_state(), "off")
    pit._tools_select(st)
    check("the Autonomy row follows Pitlink", pit._TOOLS_AUTONOMY_IDX, pit._TOOLS_PITLINK_IDX + 1)
    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_UPDATE_IDX}
    pit._tools_select(st)
    check("Update row (shifted) queues the update check", st.get("tools_action"), ("update_check",))
    check("...autonomy untouched", pit._read_autonomy_state(), "off")
    reset()


@suite("DRAW")
def _draw():
    """': on|off'; whole list + help at 22 rows; every entry reachable; footer clear."""
    reset()
    a = pit._TOOLS_AUTONOMY_IDX
    for want in ("off", "on"):
        if pit._read_autonomy_state() != want:
            pit._toggle_autonomy()
        for h, w in ((15, 60), (18, 60), (19, 60), (22, 60), (23, 60), (30, 100)):
            scr = FakeScr(h, w)
            pit.draw_tools(scr, {"tools_mode": "menu", "tools_cursor": a, "gamepad_status": ""})
            full = f"{a + 1}. Autonomy (Engineer hunts): {want}"
            check(f"{w}x{h} [{want}]: the row reads in full",
                  [t for r, c, t in scr.writes if c == 6 and t.startswith(f"{a + 1}. ")], [full])
            check(f"{w}x{h} [{want}]: nothing on the footer", [r for r, _, _ in scr.writes if r > h - 4], [])
            helps = [t for r, c, t in scr.writes if c == 4 and ("granted Engineer hunt" in t or "CONFIRM toggles" in t)]
            if h >= 22:
                check(f"{w}x{h} [{want}]: both help lines", len(helps), 2)
                check(f"{w}x{h} [{want}]: every entry drawn",
                      len({t.split('.')[0] for r, c, t in scr.writes if c == 6 and re.match(r"^\d+\. ", t)}),
                      len(pit._TOOLS_MENU))
    for h in (15, 18, 22):
        for cur in range(len(pit._TOOLS_MENU)):
            scr = FakeScr(h, 60)
            pit.draw_tools(scr, {"tools_mode": "menu", "tools_cursor": cur, "gamepad_status": ""})
            check_true(f"60x{h}: entry {cur + 1} drawn when it is the cursor",
                       any(c == 6 and t.startswith(f"{cur + 1}. ") for r, c, t in scr.writes))
    reset()


@suite("CONTRACT")
def _contract():
    """one path, one test: Pitstop == daemon == wrapper."""
    with open(PITSTOP) as f:
        m = re.search(r"""['"]AUTONOMY_FILE['"],\s*['"]([^'"]+)['"]""", f.read())
    path = m.group(1) if m else None
    check("Pitstop's default path", path, "/storage/.config/etk-autonomy")
    with open(DAEMON) as f:
        d = f.read()
    check_true("the daemon reads the same path", f'"ETK_AUTONOMY_FILE", "{path}"' in d)
    check_true("the daemon's test is rstrip('\\n') == 'on'", '.rstrip("\\n") == "on"' in d)
    with open(INSTALL) as f:
        ins = f.read()
    wrap = re.search(r"cat << 'WRAP' > /storage/\.config/etk-rpcs3-launch\.sh\n(.*?)\nWRAP\n", ins, re.S)
    check_true("the wrapper tests the same path for exactly 'on'",
               wrap and f'"$(cat {path} 2>/dev/null)" = "on"' in wrap.group(1))


@suite("UNINSTALL")
def _uninstall():
    """the switch, the rig grant and the hunt dirs go (CLEAN heredoc)."""
    with open(UNINSTALL) as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if re.search(r"<<\s*'?CLEAN'?\s*$", ln))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "CLEAN")
    body = "\n".join(lines[start + 1:end])
    for p in ("/storage/.config/etk-autonomy", "/storage/.config/etk-hunt.grant"):
        check_true(f"rm -f {p}", re.search(r"rm -f [^\n]*" + re.escape(p) + r"(\s|$)", body))
    check_true("rm -rf the hunt cores + drivers",
               re.search(r"rm -rf \$ETK_ROOT/emulators/hunt \$ETK_ROOT/drivers/hunt", body))


if __name__ == "__main__":
    try:
        print()
        if FAILS:
            print(f"FAILED: {len(FAILS)} check(s) -> {FAILS[:12]}" + (" ..." if len(FAILS) > 12 else ""))
            sys.exit(1)
        print("ALL AUTONOMY CHECKS PASSED")
    finally:
        for d, _sub, _f in os.walk(FIX):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(FIX, ignore_errors=True)
