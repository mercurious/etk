#!/usr/bin/env python3
"""ETK — host-side regression tests for the PITLINK switch (Pitstop TOOLS).

Run from the repo root:   python3 tools/test_pitstop_pitlink.py

Operator decision 2026-10-10: "Pitlink will be toggled inside ETK Pitstop Tools
as a setting." The switch is a profile.d file, the same vector as the DRIVER
dials: ROCKNIX's /etc/profile sources /storage/.config/profile.d/* at every game
launch, so the flag reaches RPCS3 at the NEXT launch and survives a cold boot.
docs/PITLINK_SPEC.md §2.8 (flags) and §6 (kit laws: ships default-OFF).

  [STATE]     the reader: absent / unreadable / not-1 = off; on iff the LAST
              GTK_PITLINK assignment is 1 (what the shell actually exports).
  [TOGGLE]    off -> on writes EXACTLY the spec'd body (three exports); on -> off
              removes the file. Judged through the ROCKNIX profile loop itself
              (`for CONFIG in profile.d/*; do . $CONFIG; done`), not by grep.
  [FAIL]      a write that cannot land (read-only dir, failed replace) reports
              the UNCHANGED state, and strands nothing the profile glob would
              source.
  [STATUS]    the footer line says when it applies, only when it changed, and
              fits the 60-col panel's status budget (w-6).
  [MENU]      every _TOOLS_*_IDX names its intended label; indices are distinct
              and cover the menu exactly (the constant-indexed contract).
  [DISPATCH]  CONFIRM on each toggle row reaches ITS handler and no other; the
              rows after Pitlink (Firmware, Update) still reach theirs.
  [DRAW]      the row shows ": on|off", its help never lands on the footer or
              over a menu entry, at the contract grid sizes.
  [UNINSTALL] uninstall.sh removes 095-etk-pitlink inside the CLEAN heredoc,
              beside 097-etk-turnip-dials, with the path Pitstop writes.

DISCRIMINATION: against the files from before the Pitlink row every suite fails
(no reader, no toggle, no index, no uninstall line), and the old uninstall.sh
alone fails [UNINSTALL]:
    git show HEAD:bin/etk_pitstop.py > /tmp/pitstop_head.py
    git show HEAD:uninstall.sh > /tmp/uninstall_head.sh
    ETK_PITSTOP=/tmp/pitstop_head.py ETK_UNINSTALL=/tmp/uninstall_head.sh \
        python3 tools/test_pitstop_pitlink.py
Mutants it catches: a plain `095-etk-pitlink.tmp` (sourced by the glob), a
missing dispatch branch, a toggle that reports intent instead of read-back,
and Firmware/Update indices left unshifted.

No rig, no terminal, no emulator, no root (read-only checks SKIP as root, since
chmod does not bind root).
"""
import curses
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.environ.get("ETK_PITSTOP",
                         os.path.join(ROOT, "bin", "etk_pitstop.py"))
UNINSTALL = os.environ.get("ETK_UNINSTALL", os.path.join(ROOT, "uninstall.sh"))

# --- fixtures BEFORE the import: every path is a module-level os.environ.get,
#     so a late setenv would land after the constants are already frozen.
FIX = tempfile.mkdtemp(prefix="etk_pitlink_test_")
PROFILE_DIR = os.path.join(FIX, "storage", ".config", "profile.d")
PITLINK_FILE = os.path.join(PROFILE_DIR, "095-etk-pitlink")
TEL = os.path.join(FIX, "tel")
os.environ.update(
    ETK_ROOT=FIX,
    TELEMETRY_DIR=TEL,
    SHM_DIR=os.path.join(FIX, "shm"),
    PITLINK_PROFILE_D=PITLINK_FILE,
    # The USB unit's file lives in the fixture, absent unless a test creates it: the
    # toggle must NEVER reach this host's systemctl (2026-10-10: one polkit prompt per toggle).
    PITLINK_USB_UNIT_FILE=os.path.join(FIX, "system.d", "etk-pitlink-usb.service"),
    SCREENSHOT_MODE_FILE=os.path.join(TEL, "screenshot_mode.txt"),
    BOG_CHORD_FILE=os.path.join(TEL, "bog_chord.txt"),
    PKG_STAGING_DIR=os.path.join(FIX, "pkg_install_drop"),
    FIRMWARE_DROP_DIR=os.path.join(FIX, "firmware_drop"),
    TURNIP_PROFILE_D=os.path.join(PROFILE_DIR, "097-etk-turnip-dials"),
    RECENT_ID_FILE=os.path.join(FIX, "last_played_id.txt"),
    TARGET_ID="NPEA00050",
    ETK_NO_TARGET="0",
)

spec = importlib.util.spec_from_file_location("pit", PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

# curses.color_pair() needs an initscr()'d terminal; the draw code only uses it
# as an attribute mask, so stub it and the shipped draws run headless.
curses.color_pair = lambda n: 0

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
FAILS = []

SPEC_BODY = ("# ETK Pitlink (Pitstop > TOOLS). Engineer link into RPCS3 -- see "
             "docs/PITLINK_SPEC.md.\n"
             "export GTK_PITLINK=1\n"
             "export GTK_PITLINK_TCP=gadget:47500\n"
             "export GTK_PITLINK_PAD=merge\n")
SPEC_EXPORTS = ["export GTK_PITLINK=1",
                "export GTK_PITLINK_TCP=gadget:47500",
                "export GTK_PITLINK_PAD=merge"]
EXPECTED_LABELS = {
    "_TOOLS_CACHE_IDX": "Manage Shaders & Caches",
    "_TOOLS_INSTALL_IDX": "Install a staged PS3 Package",
    "_TOOLS_UNINSTALL_IDX": "Uninstall a Game",
    "_TOOLS_TRIGCAL_IDX": "Trigger Calibration",
    "_TOOLS_SCREENSHOT_IDX": "Screenshot on L1+L2",
    "_TOOLS_BOG_IDX": "Bog Sampler",
    "_TOOLS_PITLINK_IDX": "Pitlink (Engineer link)",
    "_TOOLS_FIRMWARE_IDX": "Install PS3 Firmware",
    "_TOOLS_UPDATE_IDX": "Check for ETK Updates",
}


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


def skip(name, why):
    print(f"    skip {name} ({why})")


def suite(name):
    """Decorator: run a suite, and turn a missing attribute (which is exactly
    what the pre-change file gives us) into a FAIL rather than a traceback."""
    def deco(fn):
        print(f"\n[{name}]  {fn.__doc__}")
        try:
            fn()
        except Exception as e:                       # noqa: BLE001
            print(f"    FAIL {name} raised: {e.__class__.__name__}: {e}")
            FAILS.append(f"{name}:raised")
        return fn
    return deco


def reset_profile_dir():
    """Fresh, writable, EMPTY profile.d (chmod back first: a failed suite may
    have left it read-only)."""
    if os.path.isdir(PROFILE_DIR):
        os.chmod(PROFILE_DIR, 0o755)
        shutil.rmtree(PROFILE_DIR)
    os.makedirs(PROFILE_DIR)


def write(path, text, mode=None):
    with open(path, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def sourced_env():
    """What RPCS3 would inherit: run ROCKNIX's own user-profile loop (copied
    from packages/sysutils/busybox/config/profile, glob and all) over the
    fixture profile.d in a clean POSIX sh, and return the GTK_PITLINK* vars."""
    script = ('for CONFIG in "$1"/*; do\n'
              '  if [ -f "${CONFIG}" ] ; then\n'
              '    . "${CONFIG}"\n'
              '  fi\n'
              'done\n'
              'env\n')
    out = subprocess.run(["sh", "-c", script, "sh", PROFILE_DIR],
                         env={"PATH": "/usr/bin:/bin"}, capture_output=True,
                         text=True, check=True).stdout
    return {k: v for k, v in (ln.split("=", 1) for ln in out.splitlines()
                              if ln.startswith("GTK_PITLINK"))}


# ==========================================================
class FakeScr:
    """A screen that raises where curses raises: outside the window, past the
    right edge, or ON the bottom-right cell (the test_cache_screen fake)."""
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


# ==========================================================
@suite("STATE")
def _state():
    """absent/unreadable/not-1 = off; on iff the LAST GTK_PITLINK is 1."""
    reset_profile_dir()
    check("module path honours PITLINK_PROFILE_D", pit.PITLINK_PROFILE_D,
          PITLINK_FILE)
    check("default (no file) = off", pit._read_pitlink_state(), "off")
    cases = [
        ("the spec'd body", SPEC_BODY, "on"),
        ("bare assignment (no export)", "GTK_PITLINK=1\n", "on"),
        ("quoted value", 'export GTK_PITLINK="1"\n', "on"),
        ("explicit 0", "export GTK_PITLINK=0\n", "off"),
        ("later 0 overrides an earlier 1 (shell semantics)",
         "export GTK_PITLINK=1\nexport GTK_PITLINK=0\n", "off"),
        ("commented-out 1 only", "# export GTK_PITLINK=1\n", "off"),
        ("10 is not 1", "export GTK_PITLINK=10\n", "off"),
        ("a different var with the prefix",
         "export GTK_PITLINK_PAD=merge\n", "off"),
        ("empty file", "", "off"),
    ]
    for name, body, want in cases:
        write(PITLINK_FILE, body)
        check(f"reader: {name} = {want}", pit._read_pitlink_state(), want)
    with open(PITLINK_FILE, "wb") as f:
        f.write(b"\xff\xfe\x00garbage GTK_PITLINK=1\xff")
    check("reader: undecodable bytes = off (never raises)",
          pit._read_pitlink_state(), "off")
    os.remove(PITLINK_FILE)
    os.makedirs(PITLINK_FILE)
    check("reader: a DIRECTORY at the path = off", pit._read_pitlink_state(),
          "off")
    os.rmdir(PITLINK_FILE)
    if IS_ROOT:
        skip("reader: unreadable file = off", "running as root")
    else:
        write(PITLINK_FILE, SPEC_BODY, mode=0)
        check("reader: unreadable (mode 000) file = off",
              pit._read_pitlink_state(), "off")
        os.chmod(PITLINK_FILE, 0o644)
    reset_profile_dir()


@suite("USB UNIT")
def _usb_unit():
    """the toggle drives etk-pitlink-usb.service on the rig, and only there."""
    calls = []
    real_run = pit.subprocess.run

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    pit.subprocess.run = fake_run
    try:
        reset_profile_dir()
        unit = pit.PITLINK_USB_UNIT_FILE
        if os.path.exists(unit):
            os.remove(unit)
        pit._toggle_pitlink()
        pit._toggle_pitlink()
        check("no unit file (any host but the rig): systemctl never runs", calls, [])
        os.makedirs(os.path.dirname(unit), exist_ok=True)
        write(unit, "[Unit]\n")
        pit._toggle_pitlink()
        pit._toggle_pitlink()
        check("unit installed: on starts it, off stops it, never asking a password", calls,
              [["systemctl", "--no-ask-password", "--no-block", "start", "etk-pitlink-usb.service"],
               ["systemctl", "--no-ask-password", "--no-block", "stop", "etk-pitlink-usb.service"]])
        os.remove(unit)
    finally:
        pit.subprocess.run = real_run
        reset_profile_dir()


@suite("TOGGLE")
def _toggle():
    """off -> on writes exactly the spec body; on -> off removes it."""
    reset_profile_dir()
    check("sourced env before any toggle: nothing", sourced_env(), {})
    check("toggle from off returns 'on'", pit._toggle_pitlink(), "on")
    check("read-back agrees: on", pit._read_pitlink_state(), "on")
    with open(PITLINK_FILE) as f:
        body = f.read()
    check("file body is EXACTLY the spec'd text", body, SPEC_BODY)
    check("exactly the three exports, in order",
          [ln for ln in body.splitlines()
           if ln.strip() and not ln.lstrip().startswith("#")], SPEC_EXPORTS)
    check("profile.d holds the switch and nothing else (no tmp left)",
          sorted(os.listdir(PROFILE_DIR)), ["095-etk-pitlink"])
    check("ROCKNIX profile loop exports the three vars", sourced_env(),
          {"GTK_PITLINK": "1", "GTK_PITLINK_TCP": "gadget:47500",
           "GTK_PITLINK_PAD": "merge"})
    check("toggle from on returns 'off'", pit._toggle_pitlink(), "off")
    check("read-back agrees: off", pit._read_pitlink_state(), "off")
    check_true("off = the file is GONE (absent = off)",
               not os.path.exists(PITLINK_FILE))
    check("profile.d is empty again", os.listdir(PROFILE_DIR), [])
    check("ROCKNIX profile loop exports nothing", sourced_env(), {})
    check("third toggle: on again", pit._toggle_pitlink(), "on")
    check("fourth toggle: off again", pit._toggle_pitlink(), "off")

    # A hand-edited file that says 0 reads off; ON must REPLACE it, not append.
    write(PITLINK_FILE, "export GTK_PITLINK=0\nexport GTK_PITLINK_PAD=exclusive\n")
    check("hand-written GTK_PITLINK=0: toggle -> on", pit._toggle_pitlink(), "on")
    with open(PITLINK_FILE) as f:
        check("...and the body is the spec's, not a merge", f.read(), SPEC_BODY)
    pit._toggle_pitlink()

    # A fresh rig before the first /etc/profile run may have no profile.d yet.
    shutil.rmtree(PROFILE_DIR)
    check("missing profile.d: toggle creates it and lands 'on'",
          pit._toggle_pitlink(), "on")
    check_true("...the file exists", os.path.isfile(PITLINK_FILE))
    reset_profile_dir()


@suite("FAIL")
def _fail():
    """a write that cannot land reports the UNCHANGED state; nothing sourced."""
    reset_profile_dir()
    if IS_ROOT:
        skip("read-only profile.d checks", "running as root")
    else:
        os.chmod(PROFILE_DIR, 0o555)
        try:
            check("read-only dir, off: toggle reports 'off' (unchanged)",
                  pit._toggle_pitlink(), "off")
            check("...no file, no stranded tmp", os.listdir(PROFILE_DIR), [])
        finally:
            os.chmod(PROFILE_DIR, 0o755)
        write(PITLINK_FILE, SPEC_BODY)
        os.chmod(PROFILE_DIR, 0o555)
        try:
            check("read-only dir, on: toggle reports 'on' (unchanged)",
                  pit._toggle_pitlink(), "on")
            check_true("...the switch is still there",
                       os.path.isfile(PITLINK_FILE))
        finally:
            os.chmod(PROFILE_DIR, 0o755)
        reset_profile_dir()
        # A parent that cannot be created: the honest answer is still 'off'.
        shutil.rmtree(PROFILE_DIR)
        os.chmod(os.path.dirname(PROFILE_DIR), 0o555)
        try:
            check("uncreatable profile.d: toggle reports 'off'",
                  pit._toggle_pitlink(), "off")
        finally:
            os.chmod(os.path.dirname(PROFILE_DIR), 0o755)
        reset_profile_dir()

    # The replace itself failing (full disk, EXDEV...): the tmp must not stay.
    real_replace = pit.os.replace
    try:
        def boom(*_a, **_k):
            raise OSError(28, "No space left on device")
        pit.os.replace = boom
        check("failed os.replace: toggle reports 'off'", pit._toggle_pitlink(),
              "off")
    finally:
        pit.os.replace = real_replace
    check("...and leaves profile.d empty (tmp cleaned up)",
          os.listdir(PROFILE_DIR), [])

    # Belt and braces: even a tmp that DID strand (power cut between write and
    # replace) is invisible to the profile glob, because it is a dotfile.
    stray = [n for n in os.listdir(PROFILE_DIR)]
    check("precondition: empty dir", stray, [])
    write(os.path.join(PROFILE_DIR, ".095-etk-pitlink.tmp"), SPEC_BODY)
    check("a stranded dot-tmp is NOT sourced by the ROCKNIX loop",
          sourced_env(), {})
    check("...and does not read as on", pit._read_pitlink_state(), "off")
    # And the shipped toggle must actually use a dot-tmp (a plain
    # 095-etk-pitlink.tmp WOULD be sourced): watch the open() it makes.
    seen = []
    real_open = open

    def spy_open(p, *a, **k):
        seen.append(os.path.basename(str(p)))
        return real_open(p, *a, **k)
    reset_profile_dir()
    pit.open = spy_open           # module-global shadows the builtin
    try:
        pit._toggle_pitlink()
    finally:
        del pit.open
    tmps = [n for n in seen if n.endswith(".tmp")]
    check_true("the toggle's tmp name is a dotfile (glob-invisible)",
               tmps and all(n.startswith(".") for n in tmps),
               f"opened {seen}")
    reset_profile_dir()


@suite("STATUS")
def _status():
    """says when it applies, only when it changed; fits w-6 at 60 cols."""
    budget = 60 - 6
    for was, now in (("off", "on"), ("on", "off")):
        s = pit._pitlink_status(was, now)
        check_true(f"{was}->{now}: names the new state", f": {now} " in s, s)
        check_true(f"{was}->{now}: says next game launch",
                   "next game launch" in s, s)
        check_true(f"{was}->{now}: fits the footer ({len(s)} <= {budget})",
                   len(s) <= budget, s)
    for st in ("off", "on"):
        s = pit._pitlink_status(st, st)
        check_true(f"unchanged {st}: never claims it applies",
                   "next game launch" not in s and "could not" in s, s)
        check_true(f"unchanged {st}: still names the real state",
                   f"still {st}" in s, s)
        check_true(f"unchanged {st}: fits the footer", len(s) <= budget, s)


@suite("MENU")
def _menu():
    """every _TOOLS_*_IDX names its label; indices cover the menu exactly."""
    found = {n: getattr(pit, n) for n in dir(pit)
             if re.fullmatch(r"_TOOLS_[A-Z]+_IDX", n)}
    check("the index constants are exactly the expected set",
          sorted(found), sorted(EXPECTED_LABELS))
    for name, label in sorted(EXPECTED_LABELS.items(), key=lambda kv: kv[0]):
        idx = getattr(pit, name)
        got = pit._TOOLS_MENU[idx] if 0 <= idx < len(pit._TOOLS_MENU) else None
        check(f"{name} = {idx} -> {label!r}", got, label)
    check("indices are distinct and cover 0..len-1",
          sorted(found.values()), list(range(len(pit._TOOLS_MENU))))
    check("Pitlink sits after Bog Sampler (closes the toggle group)",
          pit._TOOLS_PITLINK_IDX, pit._TOOLS_BOG_IDX + 1)


@suite("DISPATCH")
def _dispatch():
    """CONFIRM on each toggle row reaches its handler and no other."""
    reset_profile_dir()
    os.makedirs(TEL, exist_ok=True)
    write(pit.SCREENSHOT_MODE_FILE, "in-game\n")
    write(pit.BOG_CHORD_FILE, "enabled\n")

    def snap():
        return (pit._read_screenshot_mode(), pit._read_bog_chord_state(),
                pit._read_pitlink_state())

    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_PITLINK_IDX}
    before = snap()
    check("Pitlink row: select returns continue", pit._tools_select(st),
          "continue")
    after = snap()
    check("Pitlink row: pitlink flipped off -> on", after[2], "on")
    check("Pitlink row: screenshot + bog untouched", after[:2], before[:2])
    check("Pitlink row: status is the honest line",
          st.get("status"), pit._pitlink_status("off", "on"))
    check("Pitlink row: queues no blocking action",
          st.get("tools_action"), None)
    check("Pitlink row: stays on the menu", st.get("tools_mode"), "menu")
    pit._tools_select(st)
    check("Pitlink row again: back to off", pit._read_pitlink_state(), "off")
    check("Pitlink row again: status says off + next launch",
          st.get("status"), pit._pitlink_status("on", "off"))

    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_SCREENSHOT_IDX}
    before = snap()
    pit._tools_select(st)
    after = snap()
    check_true("Screenshot row: screenshot mode cycled",
               after[0] != before[0])
    check("Screenshot row: bog + pitlink untouched", after[1:], before[1:])

    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_BOG_IDX}
    before = snap()
    pit._tools_select(st)
    after = snap()
    check_true("Bog row: bog chord toggled", after[1] != before[1])
    check("Bog row: screenshot + pitlink untouched",
          (after[0], after[2]), (before[0], before[2]))

    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_FIRMWARE_IDX}
    before = snap()
    pit._tools_select(st)
    check_true("Firmware row (shifted): reaches the firmware flow",
               st.get("tools_mode") in ("result", "firmware_confirm")
               and any("firmware" in ln.lower()
                       for ln in (st.get("tools_result") or (None, []))[1]),
               repr(st.get("tools_result")))
    check("Firmware row: no toggle touched", snap(), before)

    st = {"tools_mode": "menu", "tools_cursor": pit._TOOLS_UPDATE_IDX}
    before = snap()
    pit._tools_select(st)
    check("Update row (shifted): queues the update check",
          st.get("tools_action"), ("update_check",))
    check("Update row: no toggle touched", snap(), before)
    reset_profile_dir()


@suite("DRAW")
def _draw():
    """': on|off' on the row; help never on the footer or over an entry."""
    reset_profile_dir()
    cur = pit._TOOLS_PITLINK_IDX
    markers = ("engineer's computer", "CONFIRM toggles")
    for want in ("off", "on"):
        if pit._read_pitlink_state() != want:
            pit._toggle_pitlink()
        for h, w in ((15, 60), (12, 40), (18, 60), (19, 60), (22, 60),
                     (23, 60), (30, 100)):
            scr = FakeScr(h, w)
            pit.draw_tools(scr, {"tools_mode": "menu", "tools_cursor": cur,
                                 "gamepad_status": ""})
            rows = [(r, t) for r, c, t in scr.writes if c == 6
                    and t.startswith(f"{cur + 1}. ")]
            full = f"{cur + 1}. Pitlink (Engineer link): {want}"
            check_true(f"{w}x{h} [{want}]: the Pitlink row is drawn",
                       rows and full.startswith(rows[0][1].rstrip()),
                       repr(rows))
            if w >= 60:
                check(f"{w}x{h} [{want}]: the row reads in full",
                      rows[0][1] if rows else None, full)
            check(f"{w}x{h} [{want}]: nothing lands on the footer rows",
                  [r for r, _, _ in scr.writes if r > h - 4], [])
            items = {r for r, c, t in scr.writes
                     if c == 6 and re.match(r"^\d+\. ", t)}
            helps = [r for r, c, t in scr.writes
                     if c == 4 and any(m in t for m in markers)]
            check(f"{w}x{h} [{want}]: help never over a menu entry",
                  sorted(set(helps) & items), [])
            # The help band needs the whole list + a spacer row + 2 lines above
            # h-4. With nine entries from row 8 that is h >= 23 (eight entries
            # managed it at 22); below that the band is suppressed by design
            # (_draw_tools' `room`), never drawn over the list.
            if h >= 23:
                check(f"{w}x{h} [{want}]: both help lines shown", len(helps), 2)
            else:
                check_true(f"{w}x{h} [{want}]: help all-or-nothing",
                           len(helps) in (0, 2), repr(helps))
    reset_profile_dir()


@suite("UNINSTALL")
def _uninstall():
    """095-etk-pitlink removed in the CLEAN heredoc, beside 097, same path."""
    with open(UNINSTALL) as f:
        lines = f.read().splitlines()
    with open(PITSTOP) as f:
        m = re.search(r"""['"]PITLINK_PROFILE_D['"],\s*['"]([^'"]+)['"]""",
                      f.read())
    default = m.group(1) if m else None
    check("Pitstop's built-in default path",
          default, "/storage/.config/profile.d/095-etk-pitlink")
    try:
        start = next(i for i, ln in enumerate(lines)
                     if re.search(r"<<\s*'?CLEAN'?\s*$", ln))
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i] == "CLEAN")
    except StopIteration:
        check_true("uninstall.sh has a CLEAN heredoc", False)
        return
    rm = [i for i in range(start + 1, end)
          if re.match(r"\s*rm -f\b", lines[i]) and default
          and re.search(re.escape(default) + r"(\s|$)", lines[i])]
    check_true("an `rm -f <pitlink path>` runs on the rig (CLEAN heredoc)",
               rm, f"heredoc lines {start + 1}..{end + 1}")
    if not rm:
        return
    check_true("...with nothing the LOCAL shell would expand (unquoted heredoc)",
               not re.search(r"[$`\\]", lines[rm[0]]), lines[rm[0]])
    turnip = [i for i in range(start + 1, end)
              if "rm -f /storage/.config/profile.d/097-etk-turnip-dials" in lines[i]]
    check_true("...beside the 097-etk-turnip-dials removal",
               turnip and 0 < rm[0] - turnip[0] <= 8,
               f"097 at {turnip}, 095 at {rm}")
    check_true("...and reported to the operator",
               any("095-etk-pitlink" in lines[i] and "echo" in lines[i]
                   for i in range(rm[0], min(rm[0] + 3, end))))


if __name__ == "__main__":
    try:
        print()
        if FAILS:
            print(f"FAILED: {len(FAILS)} check(s) -> {FAILS[:12]}"
                  + (" ..." if len(FAILS) > 12 else ""))
            sys.exit(1)
        print("ALL PITLINK CHECKS PASSED")
    finally:
        for d, _sub, _f in os.walk(FIX):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(FIX, ignore_errors=True)
