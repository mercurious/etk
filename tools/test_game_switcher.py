#!/usr/bin/env python3
"""ETK — host-side regression tests for the PITSTOP GAME SWITCHER (0.9.0).

Run from the repo root:   python3 tools/test_game_switcher.py

Pitstop binds its target ONCE, at process start. Re-pointing it used to mean
launching the other game and R3-aborting it, which buys a UI action with a junk
abort row in the session ledger. The switcher is a held-SELECT chord instead —
and every part of it that can be wrong silently is a suite here.

  [WIRED]   The chord is a LAYER above tab dispatch, folded into the main
            loop's bounded drain. Testing the functions alone proves the
            switch turns; it does not prove anything is attached to it, so
            this suite reads the shipped source and checks the wiring —
            including that the chord is intercepted ABOVE the L1/R1 tab cycle
            and that SELECT's keycode agrees with input_d.py and gamepad_probe.

  [LIST]    Three sources, one precedence order, one ID rule. A malformed
            .psn that reached the menu would offer the operator a switch that
            comes back as ETK_NO_TARGET on the far side of the re-exec — a
            dead-end Pitstop with no game bound.

  [DECIDE]  The commit decision, including the gate that matters:
            session_postmortem.sh stamps the finished session's ledger row
            with game_id read from RECENT_ID_FILE. Re-pointing that anchor
            while a race is live would file the running session under the
            game the operator just picked. A mis-attributed row is worse than
            no row, so the switch refuses while a game runs.

  [ANCHOR]  The anchor write: the Sentry's exact format (bare ID + newline,
            install.sh `echo "$ID_STR" > "$RECENT_ID_FILE"`), tmp-then-replace
            in the same directory, and a write failure that returns instead of
            re-execing — a half-written anchor reads as unresolvable and boots
            the next Pitstop into ETK_NO_TARGET.

  [STICK]   The right stick is analog and rests near centre. The classifier
            arms only after it has seen a CENTRED sample, which is what makes
            the documented 0-255 range safe: a pad target reporting signed
            axes rests at 0, which reads as full-up under this scale, and an
            unarmed axis gives a dead selector and a no-op release instead of
            a switch to the wrong game.

  [LAYOUT]  The rig runs foot at size 28 — as little as 60x15 cells. The box
            must stay inside that, and the viewport must keep the highlight.

  [START]   ETK_START_TAB is POPPED, not read: a value left in the environment
            would be inherited by the next exec (a self-update restart) and
            override the default forever.

DISCRIMINATION: against the pre-switcher etk_pitstop.py every [WIRED] check
fails and [PRESENT] reports the missing functions, so the suite exits non-zero
before it can pretend to pass. Point it at any build with:

    ETK_PITSTOP=/tmp/pitstop_head.py python3 tools/test_game_switcher.py

No rig, no pad, no root.
"""
import importlib.util
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.environ.get("ETK_PITSTOP", os.path.join(ROOT, "bin",
                                                     "etk_pitstop.py"))
INPUT_D = os.path.join(ROOT, "bin", "input_d.py")
PROBE = os.path.join(ROOT, "bin", "gamepad_probe.py")

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


def done(tag=""):
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
        sys.exit(1)
    print("ALL GAME SWITCHER CHECKS PASSED" + tag)
    sys.exit(0)


# ==========================================================================
# FIXTURES — every path Pitstop resolves at MODULE level is pointed at a
# tempdir BEFORE the import, exactly as env.sh feeds the real process.
# ==========================================================================
TMP = tempfile.mkdtemp(prefix="etk-switcher-")
ROMS = os.path.join(TMP, "roms", "ps3")
VAULT = os.path.join(TMP, "etk", "vault")
os.makedirs(ROMS)
os.makedirs(VAULT)
RECENT = os.path.join(VAULT, "last_played_id.txt")
GAMES_YML = os.path.join(TMP, "games.yml")


def _psn(name, body):
    with open(os.path.join(ROMS, name + ".psn"), "w") as f:
        f.write(body + "\n")


def _touch(name):
    open(os.path.join(ROMS, name), "w").close()


# 1) .psn launchers (installed PKGs) — the top-precedence source.
_psn("Gran Turismo 5 Prologue", "NPEA00050")
_psn("MotorStorm", "NPUB30789")
# ...and the junk that must never reach the menu.
_psn("._Ghost", "ZZZZ00001")              # macOS AppleDouble sibling
_psn("bad", "not-an-id")                  # malformed ID
_psn("empty", "")                         # empty ID
# 2) disc titles carrying an IRISMAN-style serial tag.
_touch("Gran Turismo HD Concept (BCUS98158).iso")
_touch("Ridge Racer 7 [BLUS-30019].m3u")
_touch("(BCUS98114).iso")                 # nothing left after the strip
_touch("Duplicate Disc (NPEA00050).iso")  # already claimed by the .psn
_touch("untagged.iso")                    # no tag: invisible to source 2
_touch("lowercase (bcus98111).iso")       # tag rule is UPPERCASE-only
# 3) RPCS3's own registry.
with open(GAMES_YML, "w") as f:
    f.write('NPEA00050: "/roms/ps3/Duplicate Disc (NPEA00050).iso"\n'
            'BCUS98296: "/roms/ps3/Gran Turismo 5 (BCUS98296).iso"\n'
            'BLUS31156: /roms/ps3/untagged.iso\n'
            'badid: /roms/ps3/x.iso\n'
            '# a comment: /roms/ps3/y.iso\n')

FIXTURE_ENV = {
    "ETK_ROOT": os.path.join(TMP, "etk"),
    "PS3_ROMS_DIR": ROMS,
    "RECENT_ID_FILE": RECENT,
    "TELEMETRY_DIR": os.path.join(TMP, "etk", "etk_telemetry"),
    "VAULT_BASE": os.path.join(VAULT, "SM8250"),
}


def load_pitstop(name, **env):
    """Import a FRESH copy of the engine under `name` with these env vars in
    force. Module-level code (path derivation, the .psn name resolve) runs at
    import, so the environment has to be right before the loader runs."""
    keys = dict(FIXTURE_ENV)
    keys.update(env)
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update({k: str(v) for k, v in keys.items()})
    try:
        spec = importlib.util.spec_from_file_location(name, PITSTOP)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


# ==========================================================================
print("[WIRED] the chord is attached to the main loop, not just defined")
# ==========================================================================
SRC = open(PITSTOP).read()

check_true("SELECT is bound in the H1 gamepad-codes block",
           "BTN_SELECT = 314" in SRC)
check_true("right-stick vertical is bound", "ABS_RY = 4" in SRC)
# The keycode citation, verified rather than asserted: input_d.py runs
# SELECT+DPAD chords in-game and gamepad_probe.py documents the table.
check_true("input_d.py agrees SELECT is 314",
           "314 = BTN_SELECT" in open(INPUT_D).read())
check_true("gamepad_probe.py's reference table agrees",
           "314 BTN_SELECT" in open(PROBE).read())

check_true("stick samples are folded in the bounded drain (no queue lag)",
           "_switcher_stick(state, _v, time.time())" in SRC)
check_true("the press opens the overlay", "_switcher_open(state)" in SRC)
check_true("...gated on eligibility", "_switcher_eligible(state)" in SRC)
check_true("the release decides", "_switcher_release(state)" in SRC)
check_true("auto-repeat runs off the frame clock",
           "_switcher_tick(state, time.time())" in SRC)
check_true("the initial tab honours ETK_START_TAB", "_start_tab(" in SRC)
# ORDER is load-bearing: below the tab cycle, a held SELECT would still let
# L1/R1 change tabs behind the overlay.
check_true("the chord is intercepted ABOVE the L1/R1 tab cycle",
           0 < SRC.find("_switcher_open(state)")
           < SRC.find("elif etype == EV_KEY and val == 1 and code == BTN_TL"))
# ...and the overlay is painted from the ONE render point, last, so it sits
# over the tab body instead of under it. Checked inside _draw's own body: a
# match anywhere in the file would just be finding the definition.
_DRAW = SRC.split("def _draw(stdscr, state):", 1)[-1].split("\ndef ", 1)[0]
check_true("the overlay is drawn by _draw",
           "_draw_switcher(stdscr, state)" in _DRAW)
check_true("...after the footer (it is modal)",
           0 < _DRAW.find("_draw_footer(")
           < _DRAW.find("_draw_switcher(stdscr, state)"))

# ==========================================================================
print("[PRESENT] the functions the rest of this suite drives")
# ==========================================================================
pit = load_pitstop("pit_switcher", TARGET_ID="NPEA00050", ETK_NO_TARGET="0")
REQUIRED = ["list_installed_games", "switch_decision", "_write_recent_id",
            "_start_tab", "_switcher_state", "_switcher_eligible",
            "_switcher_open", "_switcher_stick", "_switcher_tick",
            "_switcher_step", "_switcher_release", "_switcher_apply",
            "_switcher_layout", "_draw_switcher", "_stick_dir",
            "SWITCH_NOOP", "SWITCH_REFUSE_RUNNING", "SWITCH_GO"]
_before = len(FAILS)
for sym in REQUIRED:
    check_true(f"engine exports {sym}", hasattr(pit, sym))
if len(FAILS) > _before:
    # Nothing below can run against an engine that predates the switcher, and
    # a suite that quietly skipped would be a suite that cannot discriminate.
    print(f"\n  -> {len(FAILS) - _before} function(s) missing: this engine "
          "predates the GAME SWITCHER. Behavioural suites skipped.")
    done()

# Import-time fail-soft, verified rather than assumed: the fixture .psn
# resolves the header name, and a MISSING roms dir must still import clean.
check("module-level name resolution used the fixture",
      pit.GAME_NAME, "Gran Turismo 5 Prologue")
_gone = load_pitstop("pit_missing_dir", TARGET_ID="NPEA00050",
                     ETK_NO_TARGET="0",
                     PS3_ROMS_DIR=os.path.join(TMP, "no-such-dir"))
check("an unreadable roms dir degrades to the bare ID, never an exception",
      _gone.GAME_NAME, "NPEA00050")

# ==========================================================================
print("[LIST] source union, precedence, and the ID rule")
# ==========================================================================
games = pit.list_installed_games(roms_dir=ROMS, games_yml=GAMES_YML)
check("full union, alphabetical by title", games, [
    ("BCUS98114", "BCUS98114"),                  # title fell back to the ID
    ("BCUS98296", "Gran Turismo 5"),             # games.yml only
    ("NPEA00050", "Gran Turismo 5 Prologue"),    # .psn beat the tagged .iso
    ("BCUS98158", "Gran Turismo HD Concept"),    # tagged .iso
    ("NPUB30789", "MotorStorm"),                 # .psn
    ("BLUS30019", "Ridge Racer 7"),              # [BLUS-30019] m3u
    ("BLUS31156", "untagged"),                   # games.yml, untagged disc
])
ids = [g for g, _ in games]
check_true("a macOS AppleDouble sibling is not a game",
           "ZZZZ00001" not in ids)
check_true("a malformed .psn ID is dropped", "not-an-id" not in ids)
check_true("an empty .psn is dropped", "" not in ids)
check_true("a lowercase serial tag is not an ID", "BCUS98111" not in ids)
check_true("games.yml junk keys are dropped", "badid" not in ids)
check_true("every ID matches the launcher's own pattern",
           all(pit._TITLE_ID_RE.match(g) for g in ids))
check_true("sort is case-insensitive on the title",
           [t for _, t in games] == sorted((t for _, t in games),
                                           key=str.lower))

_EMPTY = os.path.join(TMP, "empty-roms")
os.makedirs(_EMPTY, exist_ok=True)
check("empty sources give an empty list, not an error",
      pit.list_installed_games(roms_dir=_EMPTY,
                               games_yml=os.path.join(TMP, "nope.yml")), [])
check("an unreadable roms dir costs its two sources, never the menu",
      pit.list_installed_games(roms_dir=os.path.join(TMP, "no-such-dir"),
                               games_yml=GAMES_YML),
      [("NPEA00050", "Duplicate Disc"), ("BCUS98296", "Gran Turismo 5"),
       ("BLUS31156", "untagged")])
check("a missing games.yml costs its source only",
      pit.list_installed_games(roms_dir=ROMS,
                               games_yml=os.path.join(TMP, "nope.yml")),
      [g for g in games if g[0] not in ("BCUS98296", "BLUS31156")])
# The defaults have to resolve at CALL time or the injectable params are a
# lie and the shipped menu would read a fixture path (or vice versa).
_saved_yml = pit.RPCS3_GAMES_YML
pit.RPCS3_GAMES_YML = GAMES_YML
check("no-arg call resolves the module globals",
      pit.list_installed_games(), games)
pit.RPCS3_GAMES_YML = _saved_yml

# ==========================================================================
print("[DECIDE] what a SELECT release means")
# ==========================================================================
NOOP, REFUSE, GO = pit.SWITCH_NOOP, pit.SWITCH_REFUSE_RUNNING, pit.SWITCH_GO
CUR, OTHER = "NPEA00050", "BCUS98158"
for moved in (False, True):
    for pick in (CUR, OTHER):
        for running in (False, True):
            want = NOOP
            if moved and pick != CUR:
                want = REFUSE if running else GO
            check(f"moved={moved} pick={'current' if pick == CUR else 'other'}"
                  f" running={running}",
                  pit.switch_decision(moved, pick, CUR, running), want)
check("an empty list can only ever be a no-op",
      pit.switch_decision(True, None, CUR, False), NOOP)
check("no-target: any moved pick is a switch",
      pit.switch_decision(True, OTHER, None, False), GO)
check("no-target still refuses mid-session",
      pit.switch_decision(True, OTHER, None, True), REFUSE)
check_true("the refusal cites the ledger, not tidiness",
           "session_postmortem" in (pit.switch_decision.__doc__ or ""))

# ==========================================================================
print("[ANCHOR] the last-played write is atomic and Sentry-shaped")
# ==========================================================================
REPLACES = []
_real_replace = pit.os.replace


def _spy_replace(src, dst):
    REPLACES.append((src, dst))
    return _real_replace(src, dst)


pit.os.replace = _spy_replace
try:
    check("write succeeds", pit._write_recent_id("BCUS98158", RECENT), True)
    check("content is the Sentry's exact format (bare ID + newline)",
          open(RECENT).read(), "BCUS98158\n")
    check_true("it went through os.replace", len(REPLACES) == 1)
    check("...from a tmp file in the SAME directory (atomic rename)",
          (os.path.dirname(REPLACES[0][0]), REPLACES[0][1]),
          (os.path.dirname(RECENT), RECENT))
    check("no .tmp litter is left behind",
          [f for f in os.listdir(VAULT) if f.endswith(".tmp")], [])

    # THE failure that must not re-exec: parent is a file, so makedirs blows up.
    _blocker = os.path.join(TMP, "a-file")
    open(_blocker, "w").close()
    del REPLACES[:]
    check("a storage failure returns False instead of raising",
          pit._write_recent_id("NPUB30789",
                               os.path.join(_blocker, "last_played_id.txt")),
          False)
    check("...and nothing was replaced", REPLACES, [])
    check("...and the good anchor is untouched",
          open(RECENT).read(), "BCUS98158\n")
finally:
    pit.os.replace = _real_replace

# ==========================================================================
print("[STICK] arming, edge-trigger, deadzone, auto-repeat")
# ==========================================================================
check("centre reads neutral", pit._stick_dir(128), 0)
check("full up is -1 (evdev Y grows downward)", pit._stick_dir(0), -1)
check("full down is +1", pit._stick_dir(255), 1)
check("just inside the deadzone is neutral (up)", pit._stick_dir(90), 0)
check("just outside it steps (up)", pit._stick_dir(89), -1)
check("just inside the deadzone is neutral (down)", pit._stick_dir(166), 0)
check("just outside it steps (down)", pit._stick_dir(167), 1)
check_true("the deadzone is at least 30% of half-travel",
           pit._SWITCHER_DEADZONE >= 0.30)

THREE = [("AAAA00001", "Alpha"), ("BBBB00002", "Bravo"), ("CCCC00003", "Cain")]


def st(games_=None, current="BBBB00002"):
    return {"switcher": pit._switcher_state(games_ if games_ is not None
                                            else THREE, current)}


s = st()
check("the overlay opens on the CURRENT game", s["switcher"]["cursor"], 1)
check("...and nothing has moved yet", s["switcher"]["moved"], False)
check("a current game that isn't installed falls back to row 0",
      pit._switcher_state(THREE, "ZZZZ99999")["cursor"], 0)
check("no current game (ETK_NO_TARGET) highlights row 0",
      pit._switcher_state(THREE, None)["cursor"], 0)

# THE arming rule: a full deflection before any centred sample is inert.
s = st()
pit._switcher_stick(s, 0, 0.0)
check("an UNARMED axis never steps (signed-pad safety)",
      (s["switcher"]["cursor"], s["switcher"]["moved"]), (1, False))
pit._switcher_stick(s, 128, 0.1)          # arm
pit._switcher_stick(s, 0, 0.2)
check("once armed, crossing out of the deadzone steps up",
      (s["switcher"]["cursor"], s["switcher"]["moved"]), (0, True))
pit._switcher_stick(s, 5, 0.3)
check("a held deflection does not re-step (edge-triggered)",
      s["switcher"]["cursor"], 0)
check("...and it clamps at the top", s["switcher"]["cursor"], 0)

# Auto-repeat: 350ms to the first repeat, then 150ms.
s = st()
pit._switcher_stick(s, 128, 0.0)
pit._switcher_stick(s, 255, 0.0)
check("first step on the crossing", s["switcher"]["cursor"], 2)
pit._switcher_tick(s, 0.34)
check("no repeat before the initial delay", s["switcher"]["cursor"], 2)
s["switcher"]["cursor"] = 0
pit._switcher_tick(s, 0.35)
check("the first repeat lands at 350ms", s["switcher"]["cursor"], 1)
pit._switcher_tick(s, 0.49)
check("no second repeat before 150ms more", s["switcher"]["cursor"], 1)
pit._switcher_tick(s, 0.50)
check("...and one at 150ms", s["switcher"]["cursor"], 2)
pit._switcher_tick(s, 9.9)
check("repeat clamps at the end of the list", s["switcher"]["cursor"], 2)
pit._switcher_stick(s, 128, 10.0)
pit._switcher_tick(s, 99.0)
check("returning to centre stops the repeat",
      (s["switcher"]["cursor"], s["switcher"]["repeat_at"]), (2, None))

# A one-row list on a fresh rig: the selector CANNOT move, so `moved` has to
# mean the STICK moved or the first bind could never be committed.
s = st([("AAAA00001", "Only Game")], None)
pit._switcher_stick(s, 128, 0.0)
pit._switcher_stick(s, 0, 0.1)
check("a nudge on a one-row list still counts as movement",
      (s["switcher"]["cursor"], s["switcher"]["moved"]), (0, True))
check("...so the fresh-rig first bind is committable",
      pit.switch_decision(s["switcher"]["moved"], "AAAA00001", None, False),
      GO)

s = st([], None)
pit._switcher_stick(s, 128, 0.0)
pit._switcher_stick(s, 0, 0.1)
check("an empty list survives a stick sweep", s["switcher"]["cursor"], 0)

# ==========================================================================
print("[LAYOUT] the box fits a 60x15 foot grid and keeps the highlight")
# ==========================================================================
lay = pit._switcher_layout(15, 60, 7, 40, 0)
check_true("60x15, 7 games: inside the screen",
           lay["y"] >= 0 and lay["x"] >= 0
           and lay["y"] + lay["h"] <= 15 and lay["x"] + lay["w"] <= 60)
check_true("...with a usable viewport", lay["view_h"] >= 1)
check_true("a short list needs no scroll", lay["top"] == 0)
_off, _out, _seen = [], [], []
for cur in range(40):
    lay = pit._switcher_layout(15, 60, 40, 40, cur)
    if not (lay["y"] >= 0 and lay["x"] >= 0 and lay["y"] + lay["h"] <= 15
            and lay["x"] + lay["w"] <= 60):
        _off.append(cur)
    if not lay["top"] <= cur < lay["top"] + lay["view_h"]:
        _out.append(cur)
    if not 0 <= lay["top"] <= 40 - lay["view_h"]:
        _seen.append(cur)
check("40 games: every box stays inside 60x15", _off, [])
check("40 games: every cursor stays in the viewport", _out, [])
check("40 games: every scroll offset is in range", _seen, [])
lay = pit._switcher_layout(15, 60, 0, len("  (no games found)"), 0)
check_true("the empty list still gets a box",
           lay["h"] >= 5 and lay["y"] + lay["h"] <= 15)
lay = pit._switcher_layout(6, 24, 12, 60, 11)
check_true("an absurdly small grid degrades instead of going negative",
           lay["h"] > 0 and lay["w"] > 0 and lay["y"] >= 0 and lay["x"] >= 0
           and lay["view_h"] >= 1)

# ==========================================================================
print("[RENDER] no curses write escapes the screen, at any size")
# ==========================================================================
# The fail-soft law in its sharpest form: an addstr that raises while the
# operator is holding the chord takes the whole app down. Real curses needs a
# tty, so drive the painter against a screen stub that raises exactly where
# curses would, and a curses stub for the attribute constants.
import curses as _real_curses


class FakeScr:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.writes = []
        self.escaped = []

    def getmaxyx(self):
        return (self.h, self.w)

    def addstr(self, y, x, text, attr=0):
        if y < 0 or y >= self.h or x < 0 or x + len(text) > self.w:
            self.escaped.append((y, x, text))
            raise _real_curses.error("addstr out of bounds")
        self.writes.append((y, x, text, attr))


class CursesStub:
    error = _real_curses.error
    A_NORMAL, A_BOLD, A_DIM, A_REVERSE = 0, 1, 2, 4

    @staticmethod
    def color_pair(n):
        return n << 8


def render(h, w, sw_state):
    scr = FakeScr(h, w)
    saved = pit.curses
    pit.curses = CursesStub
    try:
        pit._draw_switcher(scr, sw_state)
    finally:
        pit.curses = saved
    return scr


def text_of(scr):
    return "\n".join(t for _, _, t, _ in scr.writes)


scr = render(15, 60, st())
check("60x15: nothing escaped the grid", scr.escaped, [])
check_true("the title is drawn", pit._SWITCHER_TITLE in text_of(scr))
check_true("the chord hint is drawn", pit._SWITCHER_HINT in text_of(scr))
check_true("the current game is marked with *",
           any(t.startswith("* BBBB00002") for _, _, t, _ in scr.writes))
check_true("...and highlighted",
           any(t.startswith("* BBBB00002") and a == CursesStub.A_REVERSE
               for _, _, t, a in scr.writes))
check_true("a non-current row carries no marker",
           any(t.startswith("  AAAA00001") for _, _, t, _ in scr.writes))
check_true("every ID and title is on screen",
           all(g in text_of(scr) and t in text_of(scr) for g, t in THREE))

big = {"switcher": pit._switcher_state(
    [(f"BCUS{i:05d}", f"Game Number {i}") for i in range(40)], "BCUS00039")}
scr = render(15, 60, big)
check("60x15 with 40 games: nothing escaped", scr.escaped, [])
check_true("...and the highlighted row is on screen",
           any("BCUS00039" in t for _, _, t, _ in scr.writes))

scr = render(15, 60, {"switcher": pit._switcher_state([], None)})
check("empty list: nothing escaped", scr.escaped, [])
check_true("...and it says so", pit._SWITCHER_EMPTY in text_of(scr))

long_title = {"switcher": pit._switcher_state(
    [("BCUS98158", "A" * 120), ("NPEA00050", "B" * 120)], "NPEA00050")}
for hh, ww in ((15, 60), (24, 80), (6, 24), (3, 12), (40, 200)):
    scr = render(hh, ww, long_title)
    check(f"{ww}x{hh} with 120-char titles: nothing escaped", scr.escaped, [])

scr = render(15, 60, {"switcher": None})
check("a closed switcher paints nothing", scr.writes, [])

# ==========================================================================
print("[START] ETK_START_TAB is validated, then POPPED")
# ==========================================================================
LIVE = [t for _, t in pit.TABS]
os.environ.pop("ETK_START_TAB", None)
check("absent: the caller's default stands",
      pit._start_tab(pit.CURRENT_TAB_TELEMETRY), pit.CURRENT_TAB_TELEMETRY)
os.environ["ETK_START_TAB"] = str(pit.CURRENT_TAB_TOOLS)
check("a live tab id is honoured",
      pit._start_tab(pit.CURRENT_TAB_TELEMETRY), pit.CURRENT_TAB_TOOLS)
check_true("...and the variable is consumed, not left to haunt the next exec",
           "ETK_START_TAB" not in os.environ)
os.environ["ETK_START_TAB"] = " 1 "
check("whitespace is tolerated", pit._start_tab(2), 1)
for junk in ("banana", "", "1.5", "-1", "99"):
    os.environ["ETK_START_TAB"] = junk
    check(f"junk {junk!r} is ignored", pit._start_tab(2), 2)
    check_true(f"...and popped anyway ({junk!r})",
               "ETK_START_TAB" not in os.environ)
if pit.CURRENT_TAB_PADDOCK not in LIVE:
    os.environ["ETK_START_TAB"] = str(pit.CURRENT_TAB_PADDOCK)
    check("a tab this rig does not show is not a valid start tab",
          pit._start_tab(2), 2)
os.environ.pop("ETK_START_TAB", None)

# ==========================================================================
print("[GATE] eligibility: an open modal is a conversation already running")
# ==========================================================================


def elig(**kw):
    s = {"current_tab": pit.CURRENT_TAB_TELEMETRY, "switcher": None}
    s.update(kw)
    return pit._switcher_eligible(s)


check("TELEMETRY table", elig(), True)
check("TELEMETRY detail card", elig(telemetry_mode="detail"), False)
check("TUNING", elig(current_tab=pit.CURRENT_TAB_TUNING), True)
check("TOOLS menu",
      elig(current_tab=pit.CURRENT_TAB_TOOLS, tools_mode="menu"), True)
for mode in ("shaders", "shaders_confirm", "uninstall_list", "trigcal",
             "result", "update_confirm", "install_confirm"):
    check(f"TOOLS sub-screen {mode}",
          elig(current_tab=pit.CURRENT_TAB_TOOLS, tools_mode=mode), False)
check("DRIVER", elig(current_tab=pit.CURRENT_TAB_DRIVER), False)
check("POWER", elig(current_tab=pit.CURRENT_TAB_POWER), False)
check("PADDOCK", elig(current_tab=pit.CURRENT_TAB_PADDOCK), False)
check("already open", elig(switcher={"games": []}), False)
check("crash-frame preview up", elig(_preview_proc=object()), False)

# ==========================================================================
print("[COMMIT] the release does exactly one of three things")
# ==========================================================================
EXECV = []
_real_execv = pit.os.execv
pit.os.execv = lambda *a: EXECV.append(a)
_real_running = pit._game_running
_saved_recent = pit.RECENT_ID_FILE
try:
    pit.RECENT_ID_FILE = RECENT

    # 1. dismissed — nothing touched.
    _real_replace(RECENT, RECENT)          # (no-op; keeps the file in place)
    with open(RECENT, "w") as f:
        f.write("NPEA00050\n")
    pit._game_running = lambda: False
    s = st()
    s["current_tab"] = pit.CURRENT_TAB_TUNING
    check("unmoved release dismisses", pit._switcher_release(s), NOOP)
    check("...the overlay closes", s["switcher"], None)
    check("...the anchor is untouched", open(RECENT).read(), "NPEA00050\n")
    check("...and nothing re-execs", EXECV, [])

    # 2. refused — a live game outranks the pick.
    pit._game_running = lambda: True
    s = st()
    s["current_tab"] = pit.CURRENT_TAB_TUNING
    s["switcher"]["moved"] = True
    s["switcher"]["cursor"] = 2
    check("a live session refuses the switch", pit._switcher_release(s), REFUSE)
    check("...with the operator's verdict on the footer",
          s["status"], "GAME RUNNING - exit the race first")
    check("...the anchor is untouched", open(RECENT).read(), "NPEA00050\n")
    check("...and nothing re-execs", EXECV, [])

    # 3. switched — anchor written, then a total restart.
    pit._game_running = lambda: False
    s = st()
    s["current_tab"] = pit.CURRENT_TAB_TOOLS
    s["switcher"]["moved"] = True
    s["switcher"]["cursor"] = 2
    check("an idle rig switches", pit._switcher_release(s), GO)
    check("...the anchor is re-pointed", open(RECENT).read(), "CCCC00003\n")
    check("...TARGET_ID is rebound for the exec",
          os.environ.get("TARGET_ID"), "CCCC00003")
    check("...ETK_NO_TARGET is cleared (first-bind case)",
          os.environ.get("ETK_NO_TARGET"), "0")
    check("...the tab is carried across",
          os.environ.get("ETK_START_TAB"), str(pit.CURRENT_TAB_TOOLS))
    check_true("...and it re-execs itself, mirroring the update restart",
               len(EXECV) == 1 and EXECV[0][0] == sys.executable
               and EXECV[0][1][0] == sys.executable
               and os.path.basename(EXECV[0][1][1]) ==
               os.path.basename(PITSTOP))

    # 4. storage failure — report, do NOT re-exec (a half-bound Pitstop is
    #    worse than none: the operator keeps the app they are looking at).
    del EXECV[:]
    pit.RECENT_ID_FILE = os.path.join(TMP, "a-file", "last_played_id.txt")
    s = st()
    s["current_tab"] = pit.CURRENT_TAB_TUNING
    s["switcher"]["moved"] = True
    s["switcher"]["cursor"] = 2
    check("a failed anchor write reports instead of switching",
          pit._switcher_release(s), "WRITE_FAILED")
    check("...with a plain-language verdict",
          s["status"], "SWITCH FAILED - storage error")
    check("...and Pitstop keeps running", EXECV, [])
finally:
    pit.os.execv = _real_execv
    pit._game_running = _real_running
    pit.RECENT_ID_FILE = _saved_recent
    os.environ.pop("ETK_START_TAB", None)

# ==========================================================================
print("[NOTARGET] a fresh rig can bind its first game without a launch")
# ==========================================================================
nt = load_pitstop("pit_no_target", TARGET_ID="", ETK_NO_TARGET="1")
check("the engine really is in no-target mode", nt.ETK_NO_TARGET, True)
check("...and knows it has no game", nt.GAME_NAME, "(no game resolved)")
nt.RPCS3_GAMES_YML = GAMES_YML
s = {"current_tab": nt.CURRENT_TAB_TOOLS, "tools_mode": "menu",
     "switcher": None}
check("the chord is live on the TOOLS tab with no target",
      nt._switcher_eligible(s), True)
nt._switcher_open(s)
check("the menu still lists every installed game",
      [g for g, _ in s["switcher"]["games"]],
      [g for g, _ in games])
check("nothing is marked as current", s["switcher"]["current"], None)
check("the first row is highlighted", s["switcher"]["cursor"], 0)
check("...and an unmoved release is still a dismissal",
      nt.switch_decision(s["switcher"]["moved"],
                         s["switcher"]["games"][0][0], None, False), NOOP)

shutil.rmtree(TMP, ignore_errors=True)
done()
