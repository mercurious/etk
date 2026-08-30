#!/usr/bin/env python3
"""ETK — host-side regression tests for MANAGE SHADERS & CACHES (TOOLS entry 0).

Run from the repo root:   python3 tools/test_cache_screen.py

The screen the operator reported as "too convoluted and basically doesn't
render or drifted out", with its RPCS3 cache controls inert. Four suites, one
per mechanism that produced that verdict.

  [GEOM]   THE defect. _draw() paints _draw_footer AFTER the tab body, and the
           footer owns h-3 (rule) and h-2 (hint), so the last usable body row is
           h-4. The old screen flowed a per-game bar graph and then 4 action
           rows downward while reserving only 8 lines from `h` — over-committing
           by 3-4 rows. The rows were drawn and then ERASED, every frame, while
           staying cursor-selectable: a control you can move onto and never see.
           It is CONTENT-DEPENDENT (it appears as the vault fills), which is why
           it survived a release. This suite drives the SHIPPED draw functions
           against a fake screen that raises exactly where curses raises, and
           runs against the old file too — see the discrimination note below.

  [ROWS]   The row/label builders: both clear scopes, the no-target disabled
           row, "?" for a size the scan could not get, and truncation at widths
           no rig has (a label must never raise and never overflow its budget).

  [SAY]    The confirm contract. These deletes cost the operator a slow first
           load; the text has to say so BEFORE the delete, and has to say the
           banked vault is untouched, because that is the only question that
           actually matters here.

  [GATE]   Refusal while ANY RPCS3 is alive — game OR installer (an install
           writes dev_hdd1/caches) — plus the install worker, which can sit
           between emulator launches with a job queued and no rpcs3 process at
           all. Runs through the real _rpcs3_pids() argv[0] matcher on a fixture
           /proc; a copy of that rule here could drift from the shipped one.

  [PATHS]  Per-game vs all-games target selection, executed for real against a
           fixture cache tree: one title's clear must leave every other title's
           cache standing, and an all-clear must put the two roots back.

DISCRIMINATION: this suite is meaningful only if it fails against the code it
fixes. [GEOM] therefore resolves the draw functions by name and falls back to
the pre-change ones, so it tests the DEFECT rather than a rename. Point
ETK_PITSTOP at the file from before the "rebuild TOOLS entry 0" commit:
    git show <that commit>^:bin/etk_pitstop.py > /tmp/pitstop_head.py
    ETK_PITSTOP=/tmp/pitstop_head.py python3 tools/test_cache_screen.py
[GEOM] then fails at 60x15 and at 60x22-with-a-full-vault while still PASSING
at 60x22-with-3-games — the content-dependence that let the bug ship.

No rig, no terminal, no emulator, no root.
"""
import curses
import importlib.util
import os
import re
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.environ.get("ETK_PITSTOP",
                         os.path.join(ROOT, "bin", "etk_pitstop.py"))

# --- fixtures BEFORE the import: every path is a module-level os.environ.get,
#     so a late setenv would land after the constants are already frozen.
FIX = tempfile.mkdtemp(prefix="etk_cache_test_")
VAULT = os.path.join(FIX, "vault", "SM8250")
RUNTIME_CACHE = os.path.join(FIX, "rpcs3_cache")
HDD1_CACHE = os.path.join(FIX, "dev_hdd1", "caches")
SWEEP = os.path.join(FIX, "vault_sweep.sh")
os.environ.update(
    ETK_ROOT=FIX,
    VAULT_BASE=VAULT,
    VAULT_SWEEP=SWEEP,
    RECENT_ID_FILE=os.path.join(FIX, "last_played_id.txt"),
    TELEMETRY_DIR=os.path.join(FIX, "tel"),
    SHM_DIR=os.path.join(FIX, "shm"),
    RPCS3_RUNTIME_CACHE=RUNTIME_CACHE,
    RPCS3_HDD1_CACHE=HDD1_CACHE,
    TARGET_ID="NPEA00050",
    ETK_NO_TARGET="0",
)

spec = importlib.util.spec_from_file_location('pit', PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

# curses.color_pair() needs an initscr()'d terminal; the draw code only uses it
# as an attribute mask, so stub it and the shipped draws run headless.
curses.color_pair = lambda n: 0

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


# ==========================================================
# A fake screen that raises where curses raises: outside the window, past the
# right edge, or ON THE BOTTOM-RIGHT CELL -- writing there advances the cursor
# off the window and real curses returns ERR even though the glyph lands. That
# last case is why every put() in this file clips at w-col-1 rather than w-col.
class FakeScr:
    def __init__(self, h, w):
        self.h, self.w, self.writes = h, w, []

    def getmaxyx(self):
        return (self.h, self.w)

    def addstr(self, row, col, text, attr=0):
        if row < 0 or row >= self.h or col < 0 or col + len(text) > self.w:
            raise curses.error("addwstr() returned ERR")
        if row == self.h - 1 and col + len(text) == self.w:
            raise curses.error("addwstr() returned ERR")   # bottom-right cell
        self.writes.append((row, col, text))

    # The TOOLS menu draws real chrome; these are the no-op parts of it.
    def attron(self, a):
        pass

    def attroff(self, a):
        pass

    def move(self, r, c):
        pass

    def clrtoeol(self):
        pass


def geom_model(n_games=8, current="BLUS30000", running=False):
    """A model carrying BOTH the rebuilt screen's keys and the pre-change
    screen's, so one geometry suite drives either draw function."""
    games = [{"id": f"BLUS{30000 + i}", "disk_kb": (n_games - i) * 100000,
              "stale_kb": (n_games - i) * 60000,
              "fresh_kb": (n_games - i) * 30000, "stale_files": 10}
             for i in range(n_games)]
    return {
        # rebuilt screen
        "current": current, "current_name": "Gran Turismo 5 Prologue Spec III",
        "n_games": n_games, "vault_cur_mb": 528, "fresh_cur_mb": 512,
        "stale_cur_mb": 16, "vault_all_mb": 1024, "stale_all_mb": 468,
        "cache_cur_mb": 120, "cache_all_mb": 400, "scan_ok": True,
        # pre-change screen
        "games": games, "by_id": {g["id"]: g for g in games},
        "rpcs3_all_mb": 400, "rpcs3_cur_mb": 120,
        # shared
        "boundary_ok": True, "sweep_reason": "ok", "rpcs3_running": running,
    }


_draw_screen = getattr(pit, "_draw_cache_screen", None) or \
    getattr(pit, "_draw_shader_screen", None)
_draw_confirm = getattr(pit, "_draw_cache_confirm", None) or \
    getattr(pit, "_draw_shader_confirm", None)


# ==========================================================
@suite("GEOM")
def _geom():
    """the body must stop at h-4 -- the footer repaints h-3 and h-2 after it"""
    # The rig runs a size-28 font in a fullscreen foot: a SMALL grid. 60x15 is
    # the contract; 60x22 is the roomier reading of the same panel, and it must
    # hold with a FULL vault, which is when the old layout let go.
    for h, w, n in ((15, 60, 8), (15, 60, 0), (22, 60, 8), (22, 60, 3),
                    (30, 100, 8), (12, 40, 8)):
        tag = f"{w}x{h} vault={n}"
        scr = FakeScr(h, w)
        state = {"tools_cursor": 0, "shaders_scope_all": False,
                 "cache_model": geom_model(n), "shaders_model": geom_model(n)}
        _draw_screen(scr, state, geom_model(n), 5, h, w)
        rows = [r for r, _, _ in scr.writes]
        check_true(f"screen {tag}: wrote something", bool(rows))
        check(f"screen {tag}: nothing lands on the footer rows",
              [r for r in sorted(set(rows)) if r > h - 4], [])
        labels = [(r, t) for r, c, t in scr.writes if c == 6]
        check_true(f"screen {tag}: every action row is visible",
                   labels and all(r <= h - 4 for r, _ in labels),
                   "a row the footer erases is still cursor-selectable")
        if hasattr(pit, "_CACHE_ROWS"):
            check(f"screen {tag}: all {len(pit._CACHE_ROWS)} actions drawn",
                  len(labels), len(pit._CACHE_ROWS))
            check_true(f"screen {tag}: the cache clears are on screen",
                       sum("Clear RPCS3 caches" in t for _, t in labels) == 2)

    # A live emulator changes what the screen has to say; it must not change
    # where it says it.
    scr = FakeScr(15, 60)
    _draw_screen(scr, {"tools_cursor": 3, "cache_model": geom_model(8, running=True),
                       "shaders_model": geom_model(8, running=True),
                       "shaders_scope_all": False},
                 geom_model(8, running=True), 5, 15, 60)
    check("screen 60x15 rpcs3-running: still nothing on the footer rows",
          [r for r, _, _ in scr.writes if r > 15 - 4], [])

    for op in ("clear_cur", "clear_all", "sweep", "clear_cache", "delete_vault"):
        for h, w in ((15, 60), (13, 60), (22, 60)):
            scr = FakeScr(h, w)
            _draw_confirm(scr, {"cache_model": geom_model(), "cache_pending": op,
                                "shaders_model": geom_model(),
                                "shaders_pending": op,
                                "shaders_scope_all": False}, 5, h, w)
            check(f"confirm {op} {w}x{h}: nothing on the footer rows",
                  [r for r, _, _ in scr.writes if r > h - 4], [])
            if op in ("clear_cur", "clear_all") and hasattr(pit, "_cache_confirm"):
                body = [t for r, c, t in scr.writes if c == 4]
                check_true(f"confirm {op} {w}x{h}: the vault promise survives",
                           any("vault is NOT touched" in t for t in body),
                           "shedding from the tail drops the only line that "
                           "answers the question the operator actually has")

    # THE MENU'S on-select help band. It anchors from the footer, and anchoring
    # with max() flowed it DOWNWARD onto h-3/h-2 (drawn, then erased) whenever
    # the 8-item list reached that far -- the same defect class as the graph.
    # Note: the menu LIST itself still overflows below h=19 (8 fixed rows from
    # row 8); that is pre-existing and tracked separately, so the strict
    # whole-screen assertion runs only where the list fits.
    _HELP_MARKERS = ("Free up space", "Staging drop folder", "Hold L1 + L2",
                     "always / in-game", "R1 + DPAD-Down", "Turn it off",
                     "Firmware drop folder", "Checks GitHub", "in place -",
                     "or sweep shaders")
    for h, w in ((15, 60), (18, 60), (19, 60), (20, 60), (22, 60), (30, 100)):
        for cur in range(len(pit._TOOLS_MENU)):
            scr = FakeScr(h, w)
            pit.draw_tools(scr, {"tools_mode": "menu", "tools_cursor": cur,
                                 "gamepad_status": ""})
            help_rows = [r for r, c, t in scr.writes
                         if c == 4 and any(m in t for m in _HELP_MARKERS)]
            check(f"menu help {w}x{h} cur={cur}: never on the footer rows",
                  [r for r in help_rows if r > h - 4], [])
            # Menu entries are the "N. label" writes; the install/firmware help
            # also writes a bare path at col 6, so match the numbering.
            item_rows = {r for r, c, t in scr.writes
                         if c == 6 and re.match(r"^\d+\. ", t)}
            check(f"menu help {w}x{h} cur={cur}: never over a menu entry",
                  sorted(set(help_rows) & item_rows), [])
            if h >= 19:                      # the whole list fits; be strict
                check(f"menu {w}x{h} cur={cur}: nothing lands on the footer rows",
                      [r for r, _, _ in scr.writes if r > h - 4], [])
                check(f"menu {w}x{h} cur={cur}: every entry is drawn",
                      len(item_rows), len(pit._TOOLS_MENU))

    if hasattr(pit, "_cache_layout"):
        # The budget itself: furniture is shed, controls never are.
        slot, actions = pit._cache_layout(5, 11, 4,
                                          [("title", 0), ("rule", 5), ("cur", 1),
                                           ("all", 3), ("note", 2), ("gap", 4)])
        check("layout 60x15: 4 action rows survive", len(actions), 4)
        check("layout 60x15: the last action sits on the last usable row",
              actions[-1], 11)
        check("layout 60x15: decoration is what gives way",
              sorted(slot), ["cur", "note", "title"])
        check("layout keeps display order", [slot["title"], slot["cur"],
                                             slot["note"]], [5, 6, 7])
        slot2, act2 = pit._cache_layout(5, 18, 4,
                                        [("title", 0), ("rule", 5), ("cur", 1),
                                         ("all", 3), ("note", 2), ("gap", 4)])
        check("layout 60x22: everything fits", len(slot2), 6)
        _, act3 = pit._cache_layout(5, 6, 4, [("title", 0)])
        check_true("layout clips rather than overrunning a tiny band",
                   all(r <= 6 for r in act3))


# ==========================================================
@suite("ROWS")
def _rows():
    """row building: both scopes, no-target, unknown sizes, narrow widths"""
    m = geom_model()
    rows = pit._cache_rows(m, 60)
    check("rows are in _CACHE_ROWS order",
          [r["key"] for r in rows], list(pit._CACHE_ROWS))
    check("current-game row names the title",
          "Gran Turismo 5 Prologue Spec III"
          in pit._cache_rows(m, 80)[0]["label"], True)
    check("current-game row carries its size", "(~120 MB)" in rows[0]["label"], True)
    check("all-games row is explicit about scope",
          rows[1]["label"], "Clear RPCS3 caches - ALL games (~400 MB)")
    check("sweep row is the vault action",
          rows[2]["label"], "Sweep stale vault shaders (~468 MB)")
    check("back row present", rows[3]["label"], "Back")
    check_true("every row enabled on a healthy model",
               all(r["enabled"] for r in rows))

    # ETK_NO_TARGET: no title resolved -> the per-game row is inert and says so.
    nt = dict(m, current=None, current_name="(no game resolved)")
    rows = pit._cache_rows(nt, 60)
    check("no-target: current-game row is disabled", rows[0]["enabled"], False)
    check("no-target: it says why on the row",
          rows[0]["label"], "Clear RPCS3 caches - (no game resolved)")
    check_true("no-target: selecting it explains itself", bool(rows[0]["why"]))
    check_true("no-target: ALL games is still live", rows[1]["enabled"])

    # Sizes are ADVISORY: unknown means "?", never a disabled control.
    unk = dict(m, cache_cur_mb=None, cache_all_mb=None, stale_all_mb=None)
    rows = pit._cache_rows(unk, 60)
    check_true("unknown size renders as ?",
               all("~? MB" in r["label"] for r in rows[:3]))
    check_true("unknown size does NOT disable the action",
               all(r["enabled"] for r in rows[:3]))

    # A sweep with no engine / no boundary is genuinely unavailable.
    whys = {}
    for reason in ("no_boundary", "engine", "error"):
        bad = dict(m, boundary_ok=False, sweep_reason=reason)
        r = pit._cache_rows(bad, 60)[2]
        whys[reason] = tuple(r["why"])
        check(f"sweep unavailable ({reason}): row disabled", r["enabled"], False)
        check_true(f"sweep unavailable ({reason}): explains itself",
                   bool(r["why"]))
        check_true(f"sweep unavailable ({reason}): clears stay live",
                   all(x["enabled"] for x in pit._cache_rows(bad, 60)[:2]))
    check("each unavailable reason gives a DIFFERENT fix",
          len(set(whys.values())), 3)
    check_true("a missing engine sends the operator to install.sh",
               any("install.sh" in ln for ln in whys["engine"]))
    check_true("an absent boundary is NOT reported as a fault",
               any("Nothing is stale yet" in ln for ln in whys["no_boundary"]),
               "telling them to reinstall for a problem install.sh cannot fix")

    # Narrow panels: clip, never raise, never overflow the budget.
    for width in (80, 60, 53, 40, 24, 12, 6, 1, 0):
        rows = pit._cache_rows(m, width)
        over = [r["label"] for r in rows if len(r["label"]) > max(width, 0)]
        check(f"width {width}: no label overflows its budget", over, [])
        if width >= 34:
            check_true(f"width {width}: the size survives truncation",
                       rows[0]["label"].endswith("(~120 MB)"),
                       "the number is what the operator is deciding on")
    check("a long title elides rather than dropping the size",
          pit._cache_label("Clear RPCS3 caches - ", "A" * 90, "(~120 MB)", 40),
          "Clear RPCS3 caches - " + "A" * 6 + "... (~120 MB)")
    check("...and the elided label uses its whole budget",
          len(pit._cache_label("Clear RPCS3 caches - ", "A" * 90,
                               "(~120 MB)", 40)), 40)
    st = pit._cache_status_lines(m, 55)
    check("status block is two compact lines", len(st), 2)
    check_true("status names the current vault and its split",
               "BLUS30000" in st[0] and "512 MB fresh" in st[0]
               and "16 MB stale" in st[0])
    check_true("status carries units on every figure",
               st[0].count("MB") >= 3 and "MB" in st[1],
               "'512 fresh' next to '~528 MB' reads as a second unit")
    check_true("status names the all-games total and its stale share",
               "~1024 MB" in st[1] and "468 MB stale" in st[1])
    check_true("no-target status does not invent a vault",
               "no game resolved" in pit._cache_status_lines(nt, 55)[0])
    check("compact status folds both totals onto one line",
          len(pit._cache_status_lines(m, 55, compact=True)), 1)
    check_true("compact status still carries BOTH totals",
               "~528 MB" in pit._cache_status_lines(m, 55, compact=True)[0]
               and "~1024 MB" in pit._cache_status_lines(m, 55, compact=True)[0])
    # A CLIP would turn a six-digit figure into a plausible smaller one; the
    # ladder has to drop the detail instead of corrupting the number.
    big = dict(m, vault_cur_mb=1048576, vault_all_mb=2097152,
               fresh_cur_mb=1048560, stale_cur_mb=16)
    for width in (55, 40, 30, 20, 10, 4, 1, 0):
        for compact in (False, True):
            lines = pit._cache_status_lines(big, width, compact=compact)
            check(f"status width {width} compact={compact}: fits its budget",
                  [ln for ln in lines if len(ln) > max(width, 0)], [])
        full = pit._cache_status_lines(big, width)
        if width >= 30:
            check_true(f"status width {width}: the total is never truncated",
                       "~1048576 MB" in full[0] and "~2097152 MB" in full[1],
                       "a clipped MB figure is a WRONG number, not a short one")
    check_true("every user-facing string is plain ASCII",
               all(s.isascii() for r in pit._cache_rows(m, 60)
                   for s in [r["label"]] + list(r["why"]))
               and all(s.isascii() for s in pit._cache_status_lines(m, 55)),
               "Pitstop never calls locale.setlocale")

    # The note that sits between the status and the actions. Order matters: a
    # blank model also carries boundary_ok=False, so a boundary-first test
    # reported every broken scan as "sweep unavailable" -- a wrong cause that
    # sends the operator to re-run install.sh for something it cannot fix.
    check("a failed scan names the SCAN, not the sweep",
          pit._cache_note(pit._cache_model_blank()),
          "Sizes unavailable - the actions still work.")
    check("a live emulator outranks a missing boundary",
          pit._cache_note(dict(m, scan_ok=True, rpcs3_running=True,
                               boundary_ok=False)),
          "RPCS3 is running - actions are blocked.")
    check_true("a good scan with no boundary names the boundary",
               "Sweep" in pit._cache_note(dict(m, scan_ok=True,
                                               rpcs3_running=False,
                                               boundary_ok=False,
                                               sweep_reason="engine")))
    check("a healthy model needs no note", pit._cache_note(m), None)


# ==========================================================
@suite("SAY")
def _say():
    """the confirm has to be true before the delete, not after"""
    m = geom_model()
    for op, who in (("clear_cur", "this game"), ("clear_all", "every game")):
        title, body = pit._cache_confirm(m, op, 55)
        text = " ".join(body).lower()
        check_true(f"{op}: names PPU/SPU", "ppu/spu" in text)
        check_true(f"{op}: names the shader cache", "shader cache" in text)
        check_true(f"{op}: warns the first load is slow",
                   "rebuild" in text and "slow" in text)
        check_true(f"{op}: warns about stutter", "stutter" in text)
        check_true(f"{op}: promises the vault is untouched",
                   "vault is not touched" in text)
        check_true(f"{op}: states the scope", who in text)
        check_true(f"{op}: shows the size", "MB" in " ".join(body))
        check_true(f"{op}: title says what it does", "CLEAR" in title)
        check_true(f"{op}: plain ASCII", all(s.isascii() for s in body + [title]))

    title, body = pit._cache_confirm(m, "sweep", 55)
    text = " ".join(body).lower()
    check_true("sweep: stale entries ONLY", "only" in text and "stale" in text)
    check_true("sweep: fresh banked shaders are kept",
               "fresh" in text and "kept" in text)
    check_true("sweep: says the RPCS3 caches are not in scope",
               "rpcs3 caches are not touched" in text)
    check_true("sweep: shows the size", "MB" in " ".join(body))
    check_true("sweep title names the action", "SWEEP" in title)

    unk = dict(m, cache_cur_mb=None)
    check_true("an unknown size still confirms honestly",
               "~? MB" in " ".join(pit._cache_confirm(unk, "clear_cur", 55)[1]))
    for width in (55, 40, 20, 4, 1, 0):
        for op in ("clear_cur", "clear_all", "sweep"):
            _t, b = pit._cache_confirm(m, op, width)
            check(f"confirm {op} width {width}: EVERY line fits its budget",
                  [ln for ln in b if len(ln) > max(width, 0)], [])

    # Shedding on a panel too short for the whole confirm. The mandated truths
    # are the scope (line 0) and the promise (last line); dropping from the
    # tail -- the obvious implementation -- loses the promise first.
    for op in ("clear_cur", "clear_all", "sweep"):
        _t, b = pit._cache_confirm(m, op, 55)
        for room in range(len(b), 0, -1):
            fit = pit._cache_fit_lines(b, room)
            check(f"fit {op} room={room}: exactly {room} lines", len(fit), room)
            check_true(f"fit {op} room={room}: the promise survives",
                       fit[-1] == b[-1],
                       "the last line is the only one that answers "
                       "'will this eat my banked shaders?'")
            if room >= 2:
                check(f"fit {op} room={room}: the scope survives", fit[0], b[0])
            check_true(f"fit {op} room={room}: keeps source order",
                       [b.index(ln) for ln in fit]
                       == sorted(b.index(ln) for ln in fit))
    check("fit below one line yields nothing rather than raising",
          pit._cache_fit_lines(["a", "b"], 0), [])


# ==========================================================
@suite("GATE")
def _gate():
    """no destructive action while ANY rpcs3 -- game, installer -- or the worker"""
    check("idle: allowed", pit._cache_gate(False, False), None)
    check("a game is live: refused", pit._cache_gate(True, False),
          pit._CACHE_BUSY_MSG)
    check("the install worker is live: refused", pit._cache_gate(False, True),
          pit._CACHE_BUSY_MSG)
    check("both: refused", pit._cache_gate(True, True), pit._CACHE_BUSY_MSG)
    check_true("the refusal names both causes",
               "game" in pit._CACHE_BUSY_MSG and "install" in pit._CACHE_BUSY_MSG)

    # Through the SHIPPED matcher, on a fixture /proc. An installer must refuse
    # exactly as loudly as a game: it writes dev_hdd1/caches.
    cases = [
        ("idle", {}, False),
        ("game", {103: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/GT5P.psn"}, True),
        ("installer_pkg",
         {101: "/usr/bin/rpcs3-sa --headless --installpkg /d/x.pkg"}, True),
        ("installer_fw",
         {102: "/usr/bin/rpcs3-sa --headless --installfw /d/PS3UPDAT.PUP"}, True),
        ("both", {104: "/usr/bin/rpcs3-sa --headless --installpkg /d/x.pkg",
                  105: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/GT5P.psn"}, True),
        ("unrelated", {106: "python3 /roms/etk/bin/etk_pitstop.py"}, False),
    ]
    orig_pids, orig_worker = pit._rpcs3_pids, pit._worker_alive
    tmp = tempfile.mkdtemp()
    try:
        pit._worker_alive = lambda: False
        for name, procs, want_block in cases:
            d = os.path.join(tmp, name)
            for pid, argv in procs.items():
                os.makedirs(os.path.join(d, str(pid)), exist_ok=True)
                with open(os.path.join(d, str(pid), "cmdline"), "wb") as f:
                    f.write(argv.replace(" ", "\0").encode())
            os.makedirs(d, exist_ok=True)
            pit._rpcs3_pids = (lambda dd: (
                lambda installer=None, procfs=None: orig_pids(installer, dd)))(d)
            check(f"live gate: {name}",
                  pit._cache_gate_live() is not None, want_block)
        # Worker alive with no emulator at all: the queued-job window.
        pit._rpcs3_pids = (lambda dd: (
            lambda installer=None, procfs=None: orig_pids(installer, dd)))(
                os.path.join(tmp, "idle"))
        pit._worker_alive = lambda: True
        check("live gate: worker between launches still blocks",
              pit._cache_gate_live(), pit._CACHE_BUSY_MSG)

        # And the op itself re-checks: the confirm is on screen for as long as
        # the operator takes to read it, and ES stays live behind Pitstop.
        posted = []

        class Stub:
            def post(self, s, b="", **kw):
                posted.append((s, b))

        ok, lines = pit._run_cache_op("clear_all", None, Stub())
        check("the op refuses on its own, not just the menu", ok, False)
        check_true("the refusal toasts the verdict verbatim",
                   any(b == pit._CACHE_BUSY_MSG for _, b in posted))
        check_true("the refusal is readable on the panel",
                   lines and all(len(ln) <= 55 for ln in lines))
    finally:
        pit._rpcs3_pids, pit._worker_alive = orig_pids, orig_worker
        shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================
@suite("PATHS")
def _paths():
    """per-game clears one title's subtrees under BOTH roots; all-clear resets"""
    def build():
        shutil.rmtree(RUNTIME_CACHE, ignore_errors=True)
        shutil.rmtree(HDD1_CACHE, ignore_errors=True)
        # The banked vault sits under a DIFFERENT root and must survive both
        # clears untouched -- that is the promise printed on the confirm.
        for gid in ("NPEA00050", "BLUS31156"):
            d = os.path.join(VAULT, gid, "shaders", "a1")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "shader.bin"), "w") as f:
                f.write("banked" * 512)
        # RPCS3 has keyed these by bare serial and by "<serial>_<suffix>";
        # 'NPEA00050x' is the trap a bare prefix match would fall into.
        for root, names in ((RUNTIME_CACHE, ["NPEA00050", "BLUS31156",
                                             "NPEA00050x"]),
                            (HDD1_CACHE, ["NPEA00050_NPEA00050",
                                          "NPEA00050-disc", "BCUS98114_x"])):
            for n in names:
                os.makedirs(os.path.join(root, n, "ppu-1"), exist_ok=True)
                with open(os.path.join(root, n, "ppu-1", "obj"), "w") as f:
                    f.write("x" * 4096)

    build()
    got = sorted(os.path.basename(p)
                 for p in pit._cache_dirs_for_id("NPEA00050"))
    check("per-game selection takes the id and its suffixed forms",
          got, ["NPEA00050", "NPEA00050-disc", "NPEA00050_NPEA00050"])
    check_true("a bare-prefix neighbour is NOT swept in",
               "NPEA00050x" not in got,
               "matching must anchor on the id plus a separator")
    check("a title with no cache selects nothing",
          pit._cache_dirs_for_id("NPUB30892"), [])
    check("absent roots do not raise",
          pit._cache_dirs_for_id("NPEA00050",
                                 roots=[os.path.join(FIX, "nope")]), [])
    # Garbage in the id must never widen the match. These are the shapes a
    # broken .psn, a hand-edited last_played_id.txt or an empty env can hand us.
    for junk in ("", ".", "..", "/", "*", "?", "NPEA*", "NPEA0005",
                 "NPEA00050/../BLUS31156", "../BLUS31156", "BLUS31156\n",
                 " NPEA00050", "NPEA00050 ", "npea00050",
                 "\x00", "-", "_"):
        hit = pit._cache_dirs_for_id(junk)
        stray = [p for p in hit
                 if os.path.basename(p) not in ("NPEA00050", "NPEA00050-disc",
                                                "NPEA00050_NPEA00050")]
        check(f"gid fuzz {junk!r}: selects no foreign directory", stray, [])
        check_true(f"gid fuzz {junk!r}: stays inside the roots",
                   all(os.path.dirname(p) in (RUNTIME_CACHE, HDD1_CACHE)
                       for p in hit))

    class Stub:
        def __init__(self):
            self.posts = []

        def post(self, s, b="", **kw):
            self.posts.append((s, b))

    # The banked vault is the asset this whole screen promises not to touch.
    # Pin it: the promise is in the confirm text, so it has to be in the code.
    vault_before = sorted(
        os.path.join(dp, f)
        for dp, _dn, fn in os.walk(VAULT) for f in fn)

    n = Stub()
    ok, lines = pit._run_cache_op("clear_cur", "NPEA00050", n)
    check("per-game clear succeeds", ok, True)
    check("per-game clear removed the title under BOTH roots",
          [os.path.exists(os.path.join(r, x))
           for r, x in ((RUNTIME_CACHE, "NPEA00050"),
                        (HDD1_CACHE, "NPEA00050_NPEA00050"),
                        (HDD1_CACHE, "NPEA00050-disc"))],
          [False, False, False])
    check("per-game clear left the OTHER titles alone",
          [os.path.exists(os.path.join(r, x))
           for r, x in ((RUNTIME_CACHE, "BLUS31156"),
                        (RUNTIME_CACHE, "NPEA00050x"),
                        (HDD1_CACHE, "BCUS98114_x"))],
          [True, True, True])
    check_true("per-game clear toasts a verdict",
               any(s == "CACHE CLEARED" for s, _ in n.posts))
    check_true("the result warns the first load is slow",
               any("first load is slow" in ln for ln in lines))
    check_true("the result says the vault survived",
               any("vault was not touched" in ln for ln in lines))

    def vault_now():
        return sorted(os.path.join(dp, f)
                      for dp, _dn, fn in os.walk(VAULT) for f in fn)

    check("per-game clear left the banked VAULT byte-for-byte intact",
          vault_now(), vault_before)

    n2 = Stub()
    ok, _ = pit._run_cache_op("clear_all", "NPEA00050", n2)
    check("all-games clear succeeds", ok, True)
    check("all-games clear emptied both roots",
          [sorted(os.listdir(RUNTIME_CACHE)), sorted(os.listdir(HDD1_CACHE))],
          [[], []])
    check("all-games clear RECREATED the two roots",
          [os.path.isdir(RUNTIME_CACHE), os.path.isdir(HDD1_CACHE)],
          [True, True])
    check("all-games clear left the banked VAULT intact too",
          vault_now(), vault_before)

    # THE FALSE SUCCESS. A cache root replaced by a symlink cannot be rmtree'd
    # (shutil refuses outright), and measuring `freed` BEFORE the delete then
    # reporting it unconditionally produced "CACHE CLEARED / 0 MB freed" with
    # every byte still on disk. A delete that deleted nothing is a failure.
    build()
    real = os.path.join(FIX, "elsewhere")
    os.makedirs(os.path.join(real, "keepme"), exist_ok=True)
    with open(os.path.join(real, "keepme", "obj"), "w") as f:
        f.write("x" * 4096)
    shutil.rmtree(RUNTIME_CACHE, ignore_errors=True)
    shutil.rmtree(HDD1_CACHE, ignore_errors=True)
    os.symlink(real, RUNTIME_CACHE)
    os.symlink(real, HDD1_CACHE)
    n3 = Stub()
    ok, lines = pit._run_cache_op("clear_all", "NPEA00050", n3)
    check("a clear that removed NOTHING reports failure", ok, False)
    check_true("...and says so in plain language",
               any("Could not clear" in ln for ln in lines))
    check_true("...and toasts NOT CLEARED, not CLEARED",
               any(s == "NOT CLEARED" for s, _ in n3.posts)
               and not any(s == "CACHE CLEARED" for s, _ in n3.posts))
    check("...and the symlink target is untouched",
          os.path.exists(os.path.join(real, "keepme", "obj")), True)
    os.remove(RUNTIME_CACHE)
    os.remove(HDD1_CACHE)

    # Partial: one root real, one symlinked. That IS progress, so it succeeds --
    # but it must not hide the half that would not go.
    build()
    shutil.rmtree(HDD1_CACHE, ignore_errors=True)
    os.symlink(real, HDD1_CACHE)
    n4 = Stub()
    ok, lines = pit._run_cache_op("clear_all", "NPEA00050", n4)
    check("a partial clear still succeeds", ok, True)
    check_true("...but names the folders that would not delete",
               any("would not delete" in ln for ln in lines))
    check_true("...and still keeps the vault promise on screen",
               any("vault was not touched" in ln for ln in lines))
    # The toast is the surface the operator sees after walking away from the
    # panel. Review round 3: a partial clear toasted an unqualified
    # "CACHE CLEARED" while a root full of caches still stood -- the two
    # loudest signals must agree with the screen's PARTLY CLEARED.
    check_true("...and the TOAST says PARTLY CLEARED, not CACHE CLEARED",
               any(s == "PARTLY CLEARED" for s, _ in n4.posts)
               and not any(s == "CACHE CLEARED" for s, _ in n4.posts))
    check_true("...naming the count that would not go",
               any("would not" in b for _, b in n4.posts))
    check("...and the real root really was emptied",
          sorted(os.listdir(RUNTIME_CACHE)), [])
    os.remove(HDD1_CACHE)

    # The verdict builder on its own, since it is what decides ok/not-ok.
    check("verdict: nothing removed and something left = failure",
          pit._cache_clear_verdict("clear_all", None, 0, 0, 2, True)[0], False)
    check("verdict: nothing removed and nothing left = success",
          pit._cache_clear_verdict("clear_all", None, 0, 0, 0, False)[0], True)
    check("verdict: partial = success",
          pit._cache_clear_verdict("clear_cur", "NPEA00050", 5, 1, 1, True)[0],
          True)
    for args in (("clear_all", None, 0, 0, 2, True),
                 ("clear_all", None, 0, 0, 0, False),
                 ("clear_cur", "NPEA00050", 5, 1, 1, True),
                 ("clear_cur", "NPEA00050", 9, 3, 0, False)):
        v = pit._cache_clear_verdict(*args)[1]
        check(f"verdict {args[0]}/{args[3]}/{args[4]}: fits the 4-row band",
              len(v) <= 4, True)
        check_true(f"verdict {args[0]}/{args[3]}/{args[4]}: plain ASCII",
                   all(s.isascii() for s in v))

    # Nothing cached, no roots at all: sizes read 0 and the action is a no-op,
    # never an exception and never a "?" (there is genuinely nothing there).
    shutil.rmtree(RUNTIME_CACHE, ignore_errors=True)
    shutil.rmtree(HDD1_CACHE, ignore_errors=True)
    check("missing cache dirs size as 0, not unknown",
          pit._du_mb(RUNTIME_CACHE), 0)
    check("a missing path deeper down is also 0",
          pit._du_mb(os.path.join(FIX, "nope", "deeper")), 0)
    check("no paths at all is 0", pit._du_mb(), 0)
    # THE None PATH, actually exercised: du RAN and failed. Substituting a
    # stub `du` on PATH is the only way to reach it without a broken rig, and
    # an unreached branch is an untested branch.
    os.makedirs(RUNTIME_CACHE, exist_ok=True)
    stub_bin = os.path.join(FIX, "stubbin")
    os.makedirs(stub_bin, exist_ok=True)
    real_path = os.environ["PATH"]
    try:
        with open(os.path.join(stub_bin, "du"), "w") as f:
            f.write("#!/bin/sh\necho 'du: cannot read' >&2\nexit 1\n")
        os.chmod(os.path.join(stub_bin, "du"), 0o755)
        os.environ["PATH"] = stub_bin + os.pathsep + real_path
        check("a du that RAN and FAILED reads as unknown",
              pit._du_mb(RUNTIME_CACHE), None)
        check("...and _du_kb degrades to 0 rather than raising",
              pit._du_kb(RUNTIME_CACHE), 0)
        # A du that returns 0 but skips an operand would UNDERSTATE a delete.
        with open(os.path.join(stub_bin, "du"), "w") as f:
            f.write("#!/bin/sh\necho '4\t/one'\nexit 0\n")
        os.chmod(os.path.join(stub_bin, "du"), 0o755)
        os.makedirs(HDD1_CACHE, exist_ok=True)
        check("a short du read is unknown, never a smaller number",
              pit._du_mb(RUNTIME_CACHE, HDD1_CACHE), None)
    finally:
        os.environ["PATH"] = real_path
        shutil.rmtree(stub_bin, ignore_errors=True)
    shutil.rmtree(RUNTIME_CACHE, ignore_errors=True)
    shutil.rmtree(HDD1_CACHE, ignore_errors=True)
    ok, _ = pit._run_cache_op("clear_all", "NPEA00050", Stub())
    check("all-games clear on an empty rig still succeeds", ok, True)
    check("...and still leaves the roots in place",
          [os.path.isdir(RUNTIME_CACHE), os.path.isdir(HDD1_CACHE)],
          [True, True])
    ok, _ = pit._run_cache_op("clear_cur", None, Stub())
    check("a per-game clear with no game resolved refuses", ok, False)

    # The scan, end to end, over a fake engine: it must never raise and must
    # always leave a renderable model behind.
    os.makedirs(os.path.join(VAULT, "NPEA00050", "shaders"), exist_ok=True)
    os.makedirs(os.path.join(VAULT, "BLUS31156", "shaders"), exist_ok=True)
    with open(SWEEP, "w") as f:
        f.write("#!/bin/sh\n"
                "echo 'GAME NPEA00050 120 16384 900 540672'\n"
                "echo 'GAME BLUS31156 40 8192 100 102400'\n"
                "echo 'TOTAL 160 24576 1000 643072'\n")
    st = {}
    m = pit._scan_cache_model(st)
    check("scan populates the model", st.get("cache_model") is m, True)
    check("scan counts the vaulted titles", m["n_games"], 2)
    check("scan reads the current game's split",
          (m["fresh_cur_mb"], m["stale_cur_mb"]), (528, 16))
    check("scan totals all games", m["vault_all_mb"], (24576 + 643072) // 1024)
    check("scan marks the sweep available", m["boundary_ok"], True)
    check("scan succeeded", m["scan_ok"], True)

    with open(SWEEP, "w") as f:
        f.write("#!/bin/sh\necho '[ABORT] boundary absent' >&2\nexit 1\n")
    m = pit._scan_cache_model({})
    check("a missing boundary is not a crash", m["sweep_reason"], "no_boundary")
    check("...and the vault sizes go unknown, not zero", m["vault_all_mb"], None)
    check_true("...and the two cache clears stay available",
               all(r["enabled"] for r in pit._cache_rows(m, 60)[:2]))

    os.remove(SWEEP)
    m = pit._scan_cache_model({})
    check("a missing engine is named as such", m["sweep_reason"], "engine")
    check_true("a blank model still renders four rows",
               len(pit._cache_rows(pit._cache_model_blank(), 60)) == 4)
    scr = FakeScr(15, 60)
    pit._draw_cache_screen(scr, {"tools_cursor": 0},
                           pit._cache_model_blank(), 5, 15, 60)
    check_true("a totally failed scan still draws a usable screen",
               len([1 for _r, c, _t in scr.writes if c == 6]) == 4,
               "a dead screen is worse than a '?' one")

    # A shed row must not stay selectable. Below h=12 the four actions no longer
    # fit, and a cursor that still cycles mod 4 puts the operator back on an
    # invisible control -- the exact defect this screen was rebuilt to remove.
    for h in (15, 12, 11, 10, 9):
        scr = FakeScr(h, 60)
        st = {"tools_cursor": 0, "tools_mode": "cache"}
        pit._draw_cache_screen(scr, st, geom_model(), 5, h, 60)
        drawn = len([1 for _r, c, _t in scr.writes if c == 6])
        check(f"h={h}: the cursor can only reach drawn rows",
              pit._cache_selectable(st), max(1, drawn))
        seen = set()
        for _ in range(12):
            pit._tools_move(st, 1)
            seen.add(st["tools_cursor"])
        check(f"h={h}: DPAD never lands past the last drawn row",
              [c for c in sorted(seen) if c >= max(1, drawn)], [])
    check("before the first frame the whole set is selectable",
          pit._cache_selectable({}), len(pit._CACHE_ROWS))


# ==========================================================
@suite("UNINST")
def _uninst():
    """uninstall must not leave behind the hdd1 cache the clear now finds"""
    # DIRECTED SCOPE: _run_uninstall carried the same hardcoded "<id>_<id>"
    # hdd1 literal the diagnosis condemned, so a removed game kept its whole
    # hdd1 cache. Same wrong-path bug, same file, sibling path.
    code = [ln for ln in open(PITSTOP).read().splitlines()
            if not ln.lstrip().startswith("#")]
    check("no hardcoded <id>_<id> hdd1 literal survives in the CODE",
          [ln.strip() for ln in code
           if 'f"{tid}_{tid}"' in ln or 'f"{gid}_{gid}"' in ln], [])
    check_true("uninstall resolves its caches through the shared resolver",
               any("_cache_dirs_for_id(tid)" in ln for ln in code),
               "one resolver, both callers")

    shutil.rmtree(RUNTIME_CACHE, ignore_errors=True)
    shutil.rmtree(HDD1_CACHE, ignore_errors=True)
    roms = os.path.join(FIX, "ps3")
    games = os.path.join(FIX, "hdd0", "game")
    os.makedirs(roms, exist_ok=True)
    for root, names in ((RUNTIME_CACHE, ["BCUS98114", "BLUS31156"]),
                        (HDD1_CACHE, ["BCUS98114_BCUS98114", "BCUS98114-disc",
                                      "BLUS31156_BLUS31156"])):
        for n in names:
            os.makedirs(os.path.join(root, n), exist_ok=True)
            with open(os.path.join(root, n, "obj"), "w") as f:
                f.write("x" * 2048)
    os.makedirs(os.path.join(games, "BCUS98114"), exist_ok=True)
    psn = os.path.join(roms, "Test Game.psn")
    with open(psn, "w") as f:
        f.write("BCUS98114\n")

    real_dirs = (pit.RPCS3_GAME_DIR, pit.RPCS3_CFG_GAME_DIR,
                 pit.RPCS3_EXDATA_DIR, pit.RPCS3_CFG_EXDATA_DIR)
    real_pids, real_reload = pit._rpcs3_pids, pit._es_reload_gamelists

    class Stub:
        def post(self, *a, **kw):
            pass

    try:
        pit.RPCS3_GAME_DIR = pit.RPCS3_CFG_GAME_DIR = games
        pit.RPCS3_EXDATA_DIR = pit.RPCS3_CFG_EXDATA_DIR = \
            os.path.join(FIX, "exdata")
        os.makedirs(pit.RPCS3_EXDATA_DIR, exist_ok=True)
        pit._rpcs3_pids = lambda installer=None, procfs=None: []
        pit._es_reload_gamelists = lambda *a, **kw: None
        ok, _lines = pit._run_uninstall(
            {"title_id": "BCUS98114", "name": "Test Game", "psn": psn},
            Stub())
        check("uninstall succeeds", ok, True)
        check("uninstall removed EVERY hdd1 cache form of the title",
              sorted(os.listdir(HDD1_CACHE)), ["BLUS31156_BLUS31156"])
        check("uninstall removed the runtime cache too",
              sorted(os.listdir(RUNTIME_CACHE)), ["BLUS31156"])
        check("uninstall left the other title's caches alone",
              [os.path.exists(os.path.join(RUNTIME_CACHE, "BLUS31156", "obj")),
               os.path.exists(os.path.join(HDD1_CACHE, "BLUS31156_BLUS31156",
                                           "obj"))],
              [True, True])
        check("uninstall removed the game and its launcher",
              [os.path.exists(os.path.join(games, "BCUS98114")),
               os.path.exists(psn)], [False, False])
    finally:
        (pit.RPCS3_GAME_DIR, pit.RPCS3_CFG_GAME_DIR,
         pit.RPCS3_EXDATA_DIR, pit.RPCS3_CFG_EXDATA_DIR) = real_dirs
        pit._rpcs3_pids, pit._es_reload_gamelists = real_pids, real_reload


print()
shutil.rmtree(FIX, ignore_errors=True)
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS[:12]}"
          + (" ..." if len(FAILS) > 12 else ""))
    sys.exit(1)
print("ALL SHADERS & CACHES CHECKS PASSED")
