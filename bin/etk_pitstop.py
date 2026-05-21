#!/usr/bin/env python3
# ==========================================================
# ETK PITSTOP // TABBED TUNER + TELEMETRY ENGINE
# Version: 13.0.0 - TABS REFACTOR
# ==========================================================
# Refactor for the Simple Telemetry layer. The monolithic editor is split
# into a tab-dispatched architecture: TUNING (the existing schematic
# editor) and TELEMETRY (Task-2 ledger renderer; scaffolded here).
#
# Task-1 deliverables:
#   - Tab strip on row 1, active label inverse-video. Forward-compatible
#     via the TABS list: a future 3rd/4th tab is a one-line addition.
#   - Tab switching: L1/R1 (gamepad BTN_TL/BTN_TR) and [/] (keyboard).
#     State preserved across switches — cursor position, unsaved edits,
#     gamepad status, transient errors per dossier Test 4.
#   - TUNING behavior byte-identical to v12.3.0 (all FIX 1-5 invariants
#     preserved verbatim).
#   - TELEMETRY tab is a skeletal scaffold — Task 2 wires real ledger
#     reads into the `--` placeholders, geometry already locked.
#   - On every successful TUNING save, append one row per changed field
#     to $CONFIG_CHANGES_LEDGER for downstream TELEMETRY rendering.
#
# Robustness hardenings added in this version:
#   H1: Gamepad button codes hoisted to a single constants block. The
#       upcoming PS-pad Rocknix nightly (Xbox -> PlayStation virtual
#       gamepad via InputPlumber) is now a one-place update instead of
#       a treasure hunt.
#   H2: save_menu_matrix uses atomic tmp+mv (os.replace). Power loss or
#       kernel panic mid-write no longer leaves a half-written corrupt
#       YAML config — the original survives any partial write.
#   H3: _fatal() now waits on gamepad (A/B) OR keyboard (ENTER) with a
#       5-minute timeout, instead of stdin-blocking forever. Critical for
#       headless field recovery — a corrupted schema no longer soft-bricks
#       the rig until a power-press.
# ==========================================================
# PRESERVED FIXES (from v12.3.0):
#   FIX 1: CONFIG_PATH/FIELDS_JSON pinned to canonical ETK_ROOT.
#   FIX 2: Gamepad open failure degrades to keyboard, no hard exit.
#   FIX 3: EVENT_FORMAT 'llHHi' (signed) for d-pad val=-1 reception.
#   FIX 4: Per-field YAML parse errors fall through to bulletproof
#          defaults; JSON parse errors report line/col.
#   FIX 5: Section-aware schema matching gates Audio vs Video Renderer
#          disambiguation. Appender refuses to write a section-bound
#          key into an absent section.
# ==========================================================
import os
import select
import sys
import json
import struct
import curses
import time
import traceback


# === GLOBALS ===

# Safely handle empty strings or whitespace leaking from the shell.
raw_id = os.environ.get('TARGET_ID', '').strip()
TARGET_ID = raw_id if raw_id else 'NPUA80075'

# Inherit ETK_ROOT from env.sh, falling back to the canonical single-card path.
ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')

CONFIG_PATH = f"/storage/games-internal/roms/bios/rpcs3/custom_configs/config_{TARGET_ID}.yml"
FIELDS_JSON = f"{ETK_ROOT}/config/pitstop_fields.json"
LOG_PATH = f"{ETK_ROOT}/log/etk_pitstop.log"

# Rocknix's PS3 launchers: each "<Game Name>.psn" file contains the title
# ID as text, so the directory is a static name<->ID map resolvable even
# when Pitstop runs idle from the tools menu (no rpcs3 process).
PS3_ROMS_DIR = "/storage/games-internal/roms/ps3"

# Telemetry path resolution. env.sh exports these; we fall back to derived
# paths so a developer running pitstop in isolation (no shell-source) still
# gets sane defaults. Python does its own mkdir before write — the shell
# telemetry_init_dirs() helper is shell-only.
TELEMETRY_DIR = os.environ.get('TELEMETRY_DIR', f"{ETK_ROOT}/etk_telemetry")
CONFIG_CHANGES_LEDGER = os.environ.get(
    'CONFIG_CHANGES_LEDGER', f"{TELEMETRY_DIR}/config_changes.tsv"
)
SESSIONS_LEDGER = os.environ.get(
    'SESSIONS_LEDGER', f"{TELEMETRY_DIR}/sessions.tsv"
)
CAREER_DIR = os.environ.get('CAREER_DIR', f"{TELEMETRY_DIR}/career")
PIT_NOTE_FILE = os.environ.get('PIT_NOTE_FILE', f"{TELEMETRY_DIR}/pit_note.txt")
CONFIG_CHANGES_HEADER = "epoch\tgame_id\tfield_label\told_value\tnew_value\n"


# === COLOR PAIRS ===
# Pitstop curses UI is exempt from the manifest's no-ANSI rule
# (that rule applies to commander.sh's pit-wall pane, where ANSI codes
# would corrupt copy-paste). Here we use them for at-a-glance status
# triage in the TELEMETRY tab.
PAIR_TITLE = 1     # cyan on black — existing
PAIR_CLEAN = 2     # green on black — successful sessions
PAIR_CRASH = 3     # red on black   — PANIC / hard failures
PAIR_RECOV = 4     # yellow on black — recoverable crashes
PAIR_CONFIG = 5    # cyan dim — config-change events in the ledger


# === GAMEPAD CODES (H1) ===
# All gamepad-related codes live in this single block so the upcoming
# PS-pad Rocknix nightly migration (Xbox -> PlayStation virtual gamepad
# via InputPlumber) is a one-place edit. Mirror any changes here into
# bin/input_d.py.
#
# Stable across Xbox and PlayStation virtual pad models:
EV_KEY = 1                  # button event type
EV_ABS = 3                  # axis event type
ABS_HAT0X = 16              # d-pad left/right
ABS_HAT0Y = 17              # d-pad up/down
BTN_TL = 310                # L1 / LB shoulder
BTN_TR = 311                # R1 / RB shoulder
#
# May shift on the PS-pad nightly:
# This rig's InputPlumber virtual-Xbox layout swaps the conventional
# confirm/back buttons — the physical confirm button emits BTN_EAST(305),
# not BTN_SOUTH(304). Verified on-device. A PS-style layout may revert
# to standard (confirm=BTN_SOUTH=304, back=BTN_EAST=305). Swap these two
# constants when that nightly is installed.
BTN_CONFIRM = 305           # BTN_EAST today; may become 304 post-nightly
BTN_BACK = 304              # BTN_SOUTH today; may become 305 post-nightly


# === TAB DISPATCH ===
CURRENT_TAB_TUNING = 0
CURRENT_TAB_TELEMETRY = 1

# Tab registry. Adding a future 3rd/4th tab is one line here plus a new
# CURRENT_TAB_* constant and matching draw_/handle_ pair. Geometry math
# handles itself — no per-tab layout work needed.
TABS = [
    ("TUNING", CURRENT_TAB_TUNING),
    ("TELEMETRY", CURRENT_TAB_TELEMETRY),
]


# === EVDEV WIRE FORMAT ===
# FIX 3: SIGNED int ('i') so D-PAD axis val=-1 arrives as -1 (not
# 4294967295). Matches input_d.py.
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


# === LOGGING ===

def _log(msg):
    """Append a timestamped line to LOG_PATH. Must never raise — a
    logging failure (read-only fs, missing parent, full disk) must not
    cascade into the tuner's own error handling."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, 'a') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _fatal(msg, exc=None):
    """Final-error path. Print, log, and block on dismissal. The handheld
    is headless by default, so a fatal that requires ENTER on a missing
    keyboard would soft-brick the device until a power-press.
    _wait_for_dismiss accepts gamepad A/B too. Caller is responsible for
    ensuring curses has been torn down (curses.wrapper does this on
    exception)."""
    _log(msg)
    if exc is not None:
        _log(traceback.format_exc())
    sys.stderr.write("\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(" ETK PITSTOP // FATAL\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(f" {msg}\n")
    if exc is not None:
        sys.stderr.write("\n")
        traceback.print_exc()
    sys.stderr.write("\n")
    sys.stderr.write(f" Log: {LOG_PATH}\n")
    sys.stderr.write("\n Press A or B (gamepad) or ENTER to close.\n")
    sys.stderr.write(" Auto-close in 5 min.\n")
    sys.stderr.flush()
    _wait_for_dismiss(timeout=300)
    sys.exit(1)


def _wait_for_dismiss(timeout=300):
    """Block until gamepad A/B, keyboard ENTER, or timeout. Without this,
    a fatal screen on a headless rig forces a power-press to recover.
    Polls both sources nonblocking with a 100ms wake cadence."""
    try:
        fd = os.open(find_gamepad(), os.O_RDONLY | os.O_NONBLOCK)
    except Exception:
        fd = None

    deadline = time.time() + timeout
    while time.time() < deadline:
        if fd is not None:
            try:
                data = os.read(fd, EVENT_SIZE)
                if data and len(data) == EVENT_SIZE:
                    _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)
                    if etype == EV_KEY and val == 1 and code in (BTN_CONFIRM, BTN_BACK):
                        break
            except BlockingIOError:
                pass
            except OSError:
                fd = None
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if r:
                sys.stdin.readline()
                break
        except Exception:
            time.sleep(0.1)

    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass


# === YAML HELPERS (section-aware, FIX 5) ===

def _section_of(line):
    """Detect a top-level YAML section header ('Audio:', 'Video:', ...).
    Returns the section name, or None if not a section header.
    Sub-mappings (4+ space indent like 'Performance Overlay:' nested
    under Video) are NOT treated as sections — they stay inside their
    parent's scope, which is what section-pinned schema rows want."""
    s = line.rstrip()
    if not s or s[0] in (' ', '\t', '#'):
        return None
    if not s.endswith(":"):
        return None
    return s[:-1]


def _line_matches_item(line, current_section, item):
    """True iff the YAML line matches the schema item's key AND, if the
    item declares a parent section, the cursor is currently inside it.
    The section gate is the whole point of FIX 5."""
    if not line.rstrip("\n").startswith(item["yaml_key"] + ":"):
        return False
    sect = item.get("section")
    if sect and sect != current_section:
        return False
    return True


def _find_section_range(lines, section):
    """Return (body_start_idx, body_end_idx) for the named top-level
    section. None if absent. The appender uses this so a section-bound
    key lands INSIDE its named block instead of at EOF (which
    historically dumped it into whichever block ended the file)."""
    start = None
    for i, line in enumerate(lines):
        sec = _section_of(line)
        if sec == section and start is None:
            start = i + 1
            continue
        if start is not None and sec is not None:
            return (start, i)
    if start is not None:
        return (start, len(lines))
    return None


# === ID / NAME RESOLUTION ===

def resolve_game_name(target_id):
    """Map a title ID to its human display name via the .psn launchers.
    Falls back to the ID itself so the header is never blank."""
    try:
        for entry in os.listdir(PS3_ROMS_DIR):
            if not entry.endswith(".psn"):
                continue
            with open(os.path.join(PS3_ROMS_DIR, entry), 'r') as f:
                if f.read().strip() == target_id:
                    return entry[:-4]
    except Exception:
        pass
    return target_id


GAME_NAME = resolve_game_name(TARGET_ID)


# === GAMEPAD DISCOVERY ===

def find_gamepad():
    """Dynamically capture the InputPlumber Virtual Xbox target."""
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir)):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        if "xbox" in f.read().strip().lower():
                            return f"/dev/input/{entry}"
    except Exception:
        pass
    return '/dev/input/event9'


# === SCHEMA & CONFIG IO (TUNING TAB) ===

def load_menu_matrix():
    """Initialize schema definition and parse real values live from the
    target YAML file. Captures each item's original rendered value into
    item['original_render'] so the CONFIG ledger diff can baseline
    against load-time state."""
    if not os.path.exists(FIELDS_JSON):
        raise RuntimeError(f"Missing schema definitions: {FIELDS_JSON}")

    try:
        with open(FIELDS_JSON, 'r') as f:
            matrix = json.load(f)
    except json.JSONDecodeError as e:
        # Surface file/line/column so the operator sees exactly which row
        # of the schema is malformed (trailing comma, stray period, etc.).
        # Default JSONDecodeError str() leaves out the path.
        raise RuntimeError(
            f"Schema JSON parse error in {FIELDS_JSON} "
            f"at line {e.lineno}, col {e.colno}: {e.msg}"
        ) from e

    if not isinstance(matrix, list):
        raise RuntimeError(f"Schema JSON must be a list of field objects, got {type(matrix).__name__}")

    # Duplicate (section, yaml_key) pairs silently dedupe on save (first
    # wins), which makes "I edited it but it didn't stick" almost
    # impossible to debug by inspection. Log so the operator can correct.
    seen_keys = set()
    for item in matrix:
        sk = (item.get("section"), item.get("yaml_key"))
        if sk in seen_keys:
            label = f"section={sk[0]!r}, yaml_key={sk[1].strip() if sk[1] else sk[1]!r}"
            _log(f"Schema warning: duplicate ({label}) — second occurrence will be ignored on save")
        seen_keys.add(sk)

    yaml_lines = []
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                yaml_lines = f.readlines()
        except OSError as e:
            # A missing file is normal (handled above); a read failure on an
            # existing file is not — surface it instead of silently editing
            # a phantom empty config.
            raise RuntimeError(f"Cannot read config {CONFIG_PATH}: {e}")

    for item in matrix:
        item["current_val"] = None
        item["enum_idx"] = 0

        # Required-key sanity: a malformed field should not kill the tuner —
        # log it, skip its YAML scan, let the bulletproof fallback populate
        # enough state for the row to render harmlessly.
        if "yaml_key" not in item or "type" not in item or "label" not in item:
            _log(f"Schema warning: field missing required keys, skipping: {item!r}")
            item.setdefault("label", "<malformed>")
            item.setdefault("type", "bool")
            item.setdefault("yaml_key", "")
            continue

        # Walk lines from the top each time so we can track which top-level
        # section the line lives in. An item with section="Audio" only
        # matches inside an Audio: block; without that gate the bare key
        # "  Renderer" attaches to whichever Renderer line came first.
        current_section = None
        for line in yaml_lines:
            sec = _section_of(line)
            if sec is not None:
                current_section = sec
                continue
            if _line_matches_item(line, current_section, item):
                raw_val = line.split(":", 1)[1].strip().replace('"', '')
                # Per-field parse failures (non-numeric int value, missing
                # options list on enum, etc.) must NOT abort the load — a
                # verbose RPCS3 config has ~280 lines of unmanaged keys
                # around the few we care about, and any one of them turning
                # weird would otherwise lock the whole tuner.
                try:
                    if item["type"] == "int":
                        item["current_val"] = int(raw_val)
                    elif item["type"] == "bool":
                        item["current_val"] = True if raw_val.lower() == "true" else False
                    elif item["type"] == "enum":
                        item["current_val"] = raw_val
                        # Adopt a live value that's not in the curated options
                        # so it round-trips and remains selectable via the
                        # D-pad, instead of silently coercing to options[0]
                        # on the next save.
                        if raw_val not in item["options"]:
                            item["options"] = [raw_val] + item["options"]
                        item["enum_idx"] = item["options"].index(raw_val)
                    else:
                        _log(f"Schema warning: unknown type '{item['type']}' for '{item['yaml_key'].strip()}'")
                except (ValueError, KeyError, TypeError) as e:
                    _log(f"Field '{item['yaml_key'].strip()}' raw={raw_val!r} parse failed ({e.__class__.__name__}: {e}); using fallback")
                    item["current_val"] = None
                break

        # Bulletproof fallback
        if item["current_val"] is None:
            if item["type"] == "int":
                item["current_val"] = item.get("min", 0)
            elif item["type"] == "bool":
                item["current_val"] = False
            elif item["type"] == "enum":
                opts = item.get("options") or ["<unset>"]
                item["options"] = opts
                item["current_val"] = opts[0]
                item["enum_idx"] = 0

        # CONFIG ledger baseline. Captured after the live YAML value has
        # been ingested AND any options-list adoption is settled, so it is
        # a stable string representing the on-disk state at load time.
        item["original_render"] = _render_value(item)

    return matrix


def _render_value(item):
    if item["type"] == "enum":
        return item['options'][item['enum_idx']]
    if item["type"] == "bool":
        return 'true' if item['current_val'] else 'false'
    return str(item['current_val'])


def save_menu_matrix(matrix):
    """Section-aware line-by-line injector. Atomic write via tmp+mv (H2)
    so a power loss mid-save never leaves the config half-written and
    unreadable on next launch."""
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        lines = []
    else:
        with open(CONFIG_PATH, 'r') as f:
            lines = f.readlines()

    # readlines() preserves the missing-newline state of the final line if
    # the source file doesn't end in '\n' (several golden templates don't).
    # An insert at end-of-section/file then concatenates onto that orphan
    # line, producing malformed YAML. Normalize.
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    # Track resolution by item identity, not just yaml_key, so two rows
    # sharing a key but pinned to different sections (Audio.Renderer vs
    # Video.Renderer) are each written exactly once.
    updated = set()
    current_section = None
    for i in range(len(lines)):
        sec = _section_of(lines[i])
        if sec is not None:
            current_section = sec
            continue
        for item in matrix:
            if id(item) in updated:
                continue
            if _line_matches_item(lines[i], current_section, item):
                lines[i] = f"{item['yaml_key']}: {_render_value(item)}\n"
                updated.add(id(item))
                break

    # Appender layer for keys that weren't present in the file.
    # Section-bound items insert into their named block; if the block is
    # absent we REFUSE to append (logging instead). Spilling an Audio key
    # into a sibling section is the exact corruption FIX 5 prevents.
    for item in matrix:
        if id(item) in updated:
            continue
        val_str = _render_value(item)
        sect = item.get("section")
        if sect:
            rng = _find_section_range(lines, sect)
            if rng is None:
                _log(
                    f"Cannot append '{item['yaml_key'].strip()}': section "
                    f"'{sect}' not present in {CONFIG_PATH} — skipping to "
                    f"avoid cross-section corruption"
                )
                continue
            lines.insert(rng[1], f"{item['yaml_key']}: {val_str}\n")
        else:
            lines.append(f"{item['yaml_key']}: {val_str}\n")
        updated.add(id(item))

    # H2: atomic write. os.replace is atomic on POSIX — either the new
    # file is fully in place, or the original is untouched.
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, 'w') as f:
        f.writelines(lines)
    os.replace(tmp_path, CONFIG_PATH)


def commit_and_verify(matrix):
    """Save, then re-read CONFIG_PATH from disk and confirm every managed
    key actually holds the intended value. A silent save failure (read-
    only path, wrong target, lost write) must never look like a success."""
    try:
        save_menu_matrix(matrix)
    except OSError as e:
        return False, f"WRITE FAIL: {e.__class__.__name__} {e}"

    try:
        with open(CONFIG_PATH, 'r') as f:
            disk = f.readlines()
    except OSError as e:
        return False, f"READBACK FAIL: {e.__class__.__name__} {e}"

    # Section-aware readback keyed by item identity, mirroring save's match
    # semantics so a value verified for Audio.Renderer cannot accidentally
    # confirm against the Video.Renderer line further down the file.
    seen = {}
    current_section = None
    for line in disk:
        sec = _section_of(line)
        if sec is not None:
            current_section = sec
            continue
        for item in matrix:
            if id(item) in seen:
                continue
            if _line_matches_item(line, current_section, item):
                seen[id(item)] = line.split(":", 1)[1].strip().replace('"', '')
                break

    bad = []
    for item in matrix:
        k = item["yaml_key"]
        if id(item) not in seen:
            bad.append(k.strip() + "(absent)")
            continue
        got = seen[id(item)]
        if item["type"] == "enum":
            want = item["options"][item["enum_idx"]]
            ok = got == want
        elif item["type"] == "bool":
            want = "true" if item["current_val"] else "false"
            ok = got.lower() == want
        else:
            want = str(item["current_val"])
            ok = got == want
        if not ok:
            bad.append(f"{k.strip()}={got}!={want}")

    if bad:
        return False, "VERIFY MISMATCH: " + ", ".join(bad[:3])
    return True, "SAVED OK"


# === CONFIG CHANGE LEDGER (Task 2 hook in Task 1) ===

def _diff_matrix(matrix):
    """Return list of (label, old_render, new_render) for fields whose
    current rendered value differs from their load-time baseline."""
    changes = []
    for item in matrix:
        cur = _render_value(item)
        base = item.get("original_render")
        if base is not None and cur != base:
            changes.append((item.get("label", "<unlabeled>"), base, cur))
    return changes


def _append_config_change_rows(changes):
    """Append one TSV row per change to $CONFIG_CHANGES_LEDGER. Creates
    the ledger with a header row on first write (tmp+mv for atomic
    header). Logs and returns False on any failure — a side-effect
    ledger write must never fail the user's TUNING save."""
    if not changes:
        return True
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_CHANGES_LEDGER):
            tmp = CONFIG_CHANGES_LEDGER + ".tmp"
            with open(tmp, 'w') as f:
                f.write(CONFIG_CHANGES_HEADER)
            os.replace(tmp, CONFIG_CHANGES_LEDGER)
        epoch = int(time.time())
        with open(CONFIG_CHANGES_LEDGER, 'a') as f:
            for label, old, new in changes:
                f.write(f"{epoch}\t{TARGET_ID}\t{label}\t{old}\t{new}\n")
        return True
    except Exception as e:
        _log(f"CONFIG ledger write failed: {e.__class__.__name__}: {e}")
        return False


def _refresh_baseline(matrix):
    """After a successful save, snap every item's baseline to its current
    rendered value so subsequent diffs only see new edits."""
    for item in matrix:
        item["original_render"] = _render_value(item)


# === CHROME (rendered around every tab) ===

def _draw_title_bar(stdscr, w):
    stdscr.attron(curses.color_pair(1))
    stdscr.addstr(0, 2, " ETK PITSTOP // SCHEMATIC DRIVEN EMULATOR TUNER ", curses.A_BOLD)
    stdscr.attroff(curses.color_pair(1))


def _draw_tab_strip(stdscr, current_tab, w):
    """Forward-compatible tab strip on row 1. Adding a 3rd/4th tab is a
    one-line addition to TABS — geometry handles itself."""
    stdscr.move(1, 0)
    stdscr.clrtoeol()
    col = 2
    for label, tab_id in TABS:
        is_active = (tab_id == current_tab)
        label_attr = (curses.A_REVERSE | curses.A_BOLD) if is_active else (curses.A_BOLD | curses.A_DIM)
        connector = "==="
        bracket_l = "[ "
        bracket_r = " ]"
        if col + len(connector) + len(bracket_l) + len(label) + len(bracket_r) >= w - 2:
            break
        stdscr.addstr(1, col, connector, curses.A_DIM)
        col += len(connector)
        stdscr.addstr(1, col, bracket_l, curses.color_pair(1) | curses.A_BOLD)
        col += len(bracket_l)
        stdscr.addstr(1, col, label, label_attr)
        col += len(label)
        stdscr.addstr(1, col, bracket_r, curses.color_pair(1) | curses.A_BOLD)
        col += len(bracket_r)
    # Fill the rest of the row so the strip reads as a continuous rule.
    if col < w - 2:
        stdscr.addstr(1, col, "=" * (w - col - 2), curses.A_DIM)


def _draw_meta_line(stdscr, w, gamepad_status, lap_str):
    target = f"GAME: {GAME_NAME}  |  PAD: {gamepad_status}"
    stdscr.addstr(2, 2, target[:w - 4], curses.A_DIM)
    if w > len(lap_str) + 4 and lap_str:
        stdscr.addstr(2, w - len(lap_str) - 2, lap_str, curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(3, 2, "-" * (w - 4), curses.A_DIM)


def _draw_footer(stdscr, h, w, current_tab, status):
    """Footer hint, or the save status when one exists so a failed write
    is impossible to mistake for a clean save+exit."""
    stdscr.addstr(h - 3, 2, "-" * (w - 4), curses.A_DIM)
    if status:
        ok = status == "SAVED OK"
        stdscr.addstr(h - 2, 4, status[:w - 6],
                      curses.A_BOLD | (curses.A_NORMAL if ok else curses.A_REVERSE))
    else:
        if current_tab == CURRENT_TAB_TUNING:
            footer = "DPAD UP/DN: Move  LT/RT: Change  A: Save  B: Quit  L1/R1 [/]: Tabs"
        else:
            footer = "DPAD UP/DN: Scroll  A: Refresh  B: Quit  L1/R1 [/]: Tabs"
        stdscr.addstr(h - 2, 4, footer[:w - 6], curses.A_BOLD)


# === TUNING TAB ===

def draw_tuning(stdscr, state):
    """Tuning matrix editor. start_y shifts to row 5 (was row 4 pre-tabs)
    to leave room for the tab strip on row 1."""
    matrix = state["matrix"]
    active_idx = state["cursor_idx"]
    h, w = stdscr.getmaxyx()
    total = len(matrix)

    lap = f"LAP {active_idx + 1:02d}/{total:02d}"
    _draw_meta_line(stdscr, w, state["gamepad_status"], lap)

    start_y = 5
    capacity = max(1, (h - 3) - start_y)

    if total <= capacity:
        offset = 0
    else:
        offset = min(max(0, active_idx - capacity // 2), total - capacity)

    for row, idx in enumerate(range(offset, min(offset + capacity, total))):
        item = matrix[idx]
        y = start_y + row
        is_selected = (idx == active_idx)
        prefix = "> " if is_selected else "  "
        attr = curses.A_REVERSE if is_selected else curses.A_NORMAL

        stdscr.addstr(y, 4, prefix, curses.color_pair(1) if is_selected else curses.A_NORMAL)
        stdscr.addstr(y, 8, f"{item['label']:<30}")

        if item["type"] == "enum":
            val_str = f"[ {item['options'][item['enum_idx']]} ]"
        elif item["type"] == "bool":
            val_str = f"[ {'ON' if item['current_val'] else 'OFF'} ]"
        else:
            val_str = f"[ {item['current_val']} ]"

        stdscr.addstr(y, 40, val_str, attr)

    # Scroll telltales — race shift-light chevrons parked on the rules.
    # Drawn last so they sit on top of the header/footer separator lines.
    if w > 16:
        if offset > 0:
            stdscr.addstr(3, w - 12, " /\\ MORE ", curses.color_pair(1) | curses.A_BOLD)
        if offset + capacity < total:
            stdscr.addstr(h - 3, w - 12, " \\/ MORE ", curses.color_pair(1) | curses.A_BOLD)


def _adjust_item(item, direction):
    """Shared value adjustment for keyboard and gamepad."""
    if item["type"] == "int":
        delta = item["step"] * direction
        item["current_val"] = max(item["min"], min(item["max"], item["current_val"] + delta))
    elif item["type"] == "bool":
        item["current_val"] = not item["current_val"]
    elif item["type"] == "enum":
        item["enum_idx"] = (item["enum_idx"] + direction) % len(item["options"])


def _tuning_save(state):
    """Save, verify, emit CONFIG ledger row(s) on success. Returns the
    verb for the main loop: 'save_exit' on success (matches pre-tabs
    behavior of leaving the editor once the write is proven), 'continue'
    on failure (status string set, stay in tab so the operator can react)."""
    matrix = state["matrix"]
    pending = _diff_matrix(matrix)
    ok, status = commit_and_verify(matrix)
    state["status"] = status
    if ok:
        # Q3(a): emit CONFIG ledger rows immediately on success. Ledger
        # write failure is logged but does NOT fail the save — a
        # side-effect file shouldn't lose the user a real save.
        if pending:
            _append_config_change_rows(pending)
        _refresh_baseline(matrix)
        return "save_exit"
    return "continue"


def handle_tuning_kb(state, ch):
    """Keyboard input dispatch for TUNING. The tab-switch keys [/] are
    intercepted upstream, so they never reach here."""
    matrix = state["matrix"]
    if ch == ord('q') or ch == ord('Q'):
        return "quit"
    if ch == curses.KEY_UP:
        state["cursor_idx"] = (state["cursor_idx"] - 1) % len(matrix)
        state["status"] = ""
    elif ch == curses.KEY_DOWN:
        state["cursor_idx"] = (state["cursor_idx"] + 1) % len(matrix)
        state["status"] = ""
    elif ch == curses.KEY_LEFT:
        _adjust_item(matrix[state["cursor_idx"]], -1)
        state["status"] = ""
    elif ch == curses.KEY_RIGHT:
        _adjust_item(matrix[state["cursor_idx"]], 1)
        state["status"] = ""
    elif ch == ord('\n') or ch == ord('s'):
        return _tuning_save(state)
    return "continue"


def handle_tuning_pad(state, etype, code, val):
    """Gamepad input dispatch for TUNING. BTN_TL/BTN_TR (L1/R1) are
    intercepted upstream for tab switching, so they never reach here."""
    matrix = state["matrix"]
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _tuning_save(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_ABS and code == ABS_HAT0Y:
        if val == -1:
            state["cursor_idx"] = (state["cursor_idx"] - 1) % len(matrix)
            state["status"] = ""
        elif val == 1:
            state["cursor_idx"] = (state["cursor_idx"] + 1) % len(matrix)
            state["status"] = ""
    elif etype == EV_ABS and code == ABS_HAT0X and val != 0:
        _adjust_item(matrix[state["cursor_idx"]], val)
        state["status"] = ""
    return "continue"


# === TELEMETRY LOADERS ===

def load_sessions_ledger(filter_game_id=None):
    """Read $SESSIONS_LEDGER, return list of dicts (newest first).
    Tolerates missing file (returns []) and malformed rows (skipped)."""
    if not os.path.exists(SESSIONS_LEDGER):
        return []
    rows = []
    try:
        with open(SESSIONS_LEDGER) as f:
            header_seen = False
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                if not header_seen:
                    header_seen = True
                    continue
                fields = line.split("\t")
                if len(fields) < 14:
                    continue
                try:
                    row = {
                        "epoch": int(fields[0]),
                        "duration_s": int(fields[1] or 0),
                        "build": fields[2],
                        "game_id": fields[3],
                        "status": fields[4],
                        "peak_cpu_pct": int(fields[5] or 0),
                        "peak_ram_mb": int(fields[6] or 0),
                        "peak_temp": int(fields[7] or 0),
                        "avg_temp": int(fields[8] or 0),
                        "crash_sig": fields[9],
                        "fence_at_crash": int(fields[10] or 0),
                        "shaders_harvested": int(fields[11] or 0),
                        "drain_pct": int(fields[12] or 0),
                        "thermal_overrides": int(fields[13] or 0),
                        "_kind": "session",
                    }
                except ValueError:
                    continue
                if filter_game_id is None or row["game_id"] == filter_game_id:
                    rows.append(row)
    except Exception as e:
        _log(f"sessions ledger read failed: {e}")
    rows.sort(key=lambda r: r["epoch"], reverse=True)
    return rows


def load_config_changes(filter_game_id=None):
    """Read $CONFIG_CHANGES_LEDGER, return list of dicts (newest first)."""
    if not os.path.exists(CONFIG_CHANGES_LEDGER):
        return []
    rows = []
    try:
        with open(CONFIG_CHANGES_LEDGER) as f:
            header_seen = False
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                if not header_seen:
                    header_seen = True
                    continue
                fields = line.split("\t")
                if len(fields) < 5:
                    continue
                try:
                    row = {
                        "epoch": int(fields[0]),
                        "game_id": fields[1],
                        "field_label": fields[2],
                        "old_value": fields[3],
                        "new_value": fields[4],
                        "_kind": "config",
                    }
                except ValueError:
                    continue
                if filter_game_id is None or row["game_id"] == filter_game_id:
                    rows.append(row)
    except Exception as e:
        _log(f"config changes ledger read failed: {e}")
    rows.sort(key=lambda r: r["epoch"], reverse=True)
    return rows


def load_career_stats(game_id):
    """Read $CAREER_DIR/<game_id>.txt as key=value pairs. None if absent."""
    path = f"{CAREER_DIR}/{game_id}.txt"
    if not os.path.exists(path):
        return None
    stats = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.rstrip("\n")
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                stats[k.strip()] = v.strip()
    except Exception as e:
        _log(f"career stats read failed: {e}")
        return None
    return stats


def load_pit_note():
    """Read $PIT_NOTE_FILE. Returns string content, or None when absent
    or empty so the UI can suppress the entire PIT NOTE band cleanly."""
    if not os.path.exists(PIT_NOTE_FILE):
        return None
    try:
        with open(PIT_NOTE_FILE) as f:
            content = f.read().strip()
        return content if content else None
    except Exception:
        return None


# === TELEMETRY HELPERS ===

def _time_label(epoch):
    """Compact 12-hour time like '8:49a' or '12:34p' — dossier §11."""
    t = time.localtime(epoch)
    hour = t.tm_hour
    minute = t.tm_min
    if hour == 0:
        h12, ap = 12, 'a'
    elif hour < 12:
        h12, ap = hour, 'a'
    elif hour == 12:
        h12, ap = 12, 'p'
    else:
        h12, ap = hour - 12, 'p'
    return f"{h12}:{minute:02d}{ap}"


def _day_label(epoch, now=None):
    """Day-bucket label: Today / Yesterday / 'Wed 05/18' style.
    Uses zero-padded month/day (%m/%d) for portability — the %-m/%-d
    non-padded variants are GNU-only and silently fail on other libcs."""
    if now is None:
        now = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    yesterday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    epoch_day = time.strftime("%Y-%m-%d", time.localtime(epoch))
    if epoch_day == today:
        return "Today"
    if epoch_day == yesterday:
        return "Yesterday"
    return time.strftime("%a %m/%d", time.localtime(epoch))


def _format_duration(secs):
    """Compact session duration like '2m20s' or '1h05m'."""
    if secs <= 0:
        return "----"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def _wrap_text(text, width, max_lines=2):
    """Word-wrap with hard line cap. Used for the PIT NOTE block —
    dossier §13 caps visible text at ~4 lines but our 21-row TTY can't
    afford that. Two lines is the realistic max here; longer notes get
    truncated with an ellipsis on the last visible line."""
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip() if cur else w
        if len(candidate) <= width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and cur and len(words) > sum(len(l.split()) for l in lines):
        # Truncated — mark with ellipsis
        last = lines[-1]
        if len(last) > width - 3:
            last = last[:width - 3].rstrip()
        lines[-1] = last + " …"
    return lines


def _status_attr(status):
    """Map a session status to its display color pair."""
    if status == "CLEAN":
        return curses.color_pair(PAIR_CLEAN) | curses.A_BOLD
    if status == "PANIC":
        return curses.color_pair(PAIR_CRASH) | curses.A_BOLD
    if status.startswith("RECOVERY:"):
        return curses.color_pair(PAIR_RECOV)
    return curses.A_NORMAL


def _refresh_telemetry_caches(state):
    """Reload ledger data from disk. Called on tab-switch into TELEMETRY
    so newly-saved CONFIG rows or post-mortem sessions appear immediately."""
    state["_sessions_cache"] = load_sessions_ledger(TARGET_ID)
    state["_config_changes_cache"] = load_config_changes(TARGET_ID)
    state["_career_cache"] = load_career_stats(TARGET_ID)
    state["_pit_note_cache"] = load_pit_note()


# === TELEMETRY TAB ===

def draw_telemetry(stdscr, state):
    """Live TELEMETRY tab. Renders CAREER row anchor, scrollable session
    ledger with chronologically-interleaved CONFIG events and day-bucket
    separators, and an optional PIT NOTE band at the bottom (suppressed
    when pit_note.txt is absent — dossier §13)."""
    h, w = stdscr.getmaxyx()

    if state.get("_sessions_cache") is None:
        _refresh_telemetry_caches(state)
    sessions = state["_sessions_cache"]
    config_changes = state["_config_changes_cache"]
    career = state["_career_cache"]
    pit_note = state["_pit_note_cache"]

    # === CAREER ANCHOR ===
    y = 5
    stdscr.addstr(y, 2, f"CAREER - {GAME_NAME} ({TARGET_ID})", curses.A_BOLD)
    y += 1
    stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
    y += 1

    if career is None:
        if sessions:
            stdscr.addstr(y, 2, "First session — no career stats yet."[:w - 4], curses.A_DIM)
            y += 1
            y += 1  # keep block height stable
        else:
            stdscr.addstr(y, 2, "No sessions recorded for this game yet."[:w - 4], curses.A_DIM)
            y += 1
            stdscr.addstr(y, 2, "Launch a game to start collecting telemetry."[:w - 4], curses.A_DIM)
            y += 1
    else:
        line1 = (
            f"{career.get('total_duration_human', '0h 0m')} total"
            f"  *  {career.get('total_sessions', '0')} sessions"
            f"  *  {career.get('clean_rate_pct', '0')}% clean"
            f"  *  {career.get('crash_count', '0')} crashes"
            f" ({career.get('recov_count', '0')}r/{career.get('panic_count', '0')}p)"
        )
        stdscr.addstr(y, 2, line1[:w - 4], curses.A_BOLD)
        y += 1
        line2 = (
            f"{career.get('total_shaders', '0')} shaders banked"
            f"  *  +{career.get('avg_shaders_per_session', '0')} avg/session"
            f"  *  streak {career.get('current_streak', '0')} (best {career.get('best_streak', '0')})"
        )
        stdscr.addstr(y, 2, line2[:w - 4], curses.A_DIM)
        y += 1

    stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
    y += 2  # visual breather before the table

    # === PIT NOTE RESERVATION ===
    # Reserve bottom rows for the PIT NOTE block when present so the
    # session table doesn't render over it. dossier §13: suppress
    # entirely when pit_note.txt absent — gives the table more room.
    pit_lines = []
    pit_block_h = 0
    if pit_note:
        pit_lines = _wrap_text(pit_note, w - 14, max_lines=2)
        pit_block_h = 1 + len(pit_lines)  # 1 rule + N text lines

    # === SESSION TABLE ===
    table_top = y
    # Footer = h-2 (text), rule = h-3. Pit note (if any) sits above the rule.
    table_bottom = h - 3 - pit_block_h
    table_capacity = max(1, table_bottom - table_top - 2)  # -2 for col-header + rule

    # Column header (sized to fit 74-col Flip 2 TTY)
    header_line = "TIME    STATUS              DUR    TEMP    LOAD  RAM     SHD  DRAIN"
    stdscr.addstr(y, 2, header_line[:w - 4], curses.A_BOLD)
    y += 1
    stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
    y += 1

    # Merge sessions + config events by epoch, newest first.
    merged = []
    for s in sessions:
        merged.append(s)
    for c in config_changes:
        merged.append(c)
    merged.sort(key=lambda r: r["epoch"], reverse=True)

    # Clamp scroll cursor to current dataset extent.
    max_scroll = max(0, len(merged) - table_capacity)
    state["telemetry_scroll"] = min(state.get("telemetry_scroll", 0), max_scroll)
    scroll = state["telemetry_scroll"]
    visible = merged[scroll: scroll + table_capacity]

    if not visible:
        stdscr.addstr(y, 2, "No telemetry recorded for this game yet."[:w - 4], curses.A_DIM)
        y += 1
    else:
        prev_day = None
        for row in visible:
            if y >= table_bottom:
                break
            day = _day_label(row["epoch"])
            if prev_day is not None and day != prev_day:
                sep = f"- {day} "
                sep_line = sep + "-" * max(0, (w - 4) - len(sep))
                stdscr.addstr(y, 2, sep_line[:w - 4], curses.A_DIM)
                y += 1
                if y >= table_bottom:
                    break
            prev_day = day

            if row["_kind"] == "session":
                _draw_session_row(stdscr, y, w, row)
            else:
                _draw_config_row(stdscr, y, w, row)
            y += 1

    # === PAGE INDICATOR ===
    # Top-right corner of the meta row — shows scroll context. Pre-tabs
    # had a LAP counter here; TELEMETRY uses the equivalent slot for
    # Pg X/Y so the operator knows how much history is hidden.
    if max_scroll > 0:
        total_pages = max(1, (len(merged) + table_capacity - 1) // table_capacity)
        cur_page = (scroll // table_capacity) + 1
        page_str = f"Pg {cur_page}/{total_pages}"
    else:
        page_str = f"{len(merged)} entries"
    _draw_meta_line(stdscr, w, state["gamepad_status"], page_str)

    # === PIT NOTE BAND ===
    if pit_block_h > 0:
        pit_y = h - 3 - pit_block_h
        stdscr.addstr(pit_y, 2, "-" * (w - 4), curses.A_DIM)
        for i, line in enumerate(pit_lines):
            prefix = "PIT NOTE  " if i == 0 else "          "
            stdscr.addstr(pit_y + 1 + i, 2, (prefix + line)[:w - 4], curses.A_DIM)


def _draw_session_row(stdscr, y, w, row):
    """Render one session row. Builds the full line as a single clipped
    string, writes it once, then overlays the colored STATUS column.
    Single-write + overlay is overflow-safe — a narrow TTY truncates the
    base line cleanly rather than tripping addwstr() returned ERR."""
    status = row["status"]
    if status == "CLEAN":
        mark = " * "
    elif status == "PANIC":
        mark = " ! "
    else:
        mark = "   "

    time_str = _time_label(row["epoch"])
    dur_str = _format_duration(row["duration_s"])
    peak_t = row["peak_temp"]
    avg_t = row["avg_temp"]
    temp_str = f"{avg_t}/{peak_t}C" if peak_t > 0 else "----"
    load_str = f"{row['peak_cpu_pct']}%" if row['peak_cpu_pct'] > 0 else "----"
    ram = row["peak_ram_mb"]
    if ram >= 1000:
        ram_str = f"{ram // 1000}.{(ram % 1000) // 100}G"
    elif ram > 0:
        ram_str = f"{ram}MB"
    else:
        ram_str = "----"
    shd = row["shaders_harvested"]
    shd_str = str(shd) if shd > 0 else "0"
    drain = row["drain_pct"]
    drain_str = f"{drain}%" if drain != 0 else "0%"

    base = (
        f"{time_str:<6}"
        f"{mark:<3}"
        f"{status[:18]:<18}  "
        f"{dur_str:>6}  "
        f"{temp_str:>7}  "
        f"{load_str:>4}  "
        f"{ram_str:>5}  "
        f"{shd_str:>3}  "
        f"{drain_str:>5}"
    )
    base = base[:w - 4]
    try:
        stdscr.addstr(y, 2, base)
    except curses.error:
        return

    # Overlay the STATUS substring with its color. Position is computed
    # from the fixed prefix widths above (6 + 3 = 9 cols of preface).
    status_col = 2 + 9
    status_text = status[:18]
    if status_col + len(status_text) <= w - 1:
        try:
            stdscr.addstr(y, status_col, status_text, _status_attr(status))
        except curses.error:
            pass


def _draw_config_row(stdscr, y, w, row):
    """Render one CONFIG row: time, marker, field label, old->new
    transition. Other columns blank. Single-write + colored overlay
    pattern matches _draw_session_row for overflow safety."""
    time_str = _time_label(row["epoch"])
    field = row["field_label"]
    transition = f"{row['old_value']} -> {row['new_value']}"
    body = f"{field}  {transition}"

    base = f"{time_str:<6} > {body}"
    base = base[:w - 4]
    try:
        stdscr.addstr(y, 2, base)
    except curses.error:
        return
    # Overlay just the body text with the CONFIG color so the row is
    # visually distinct from session rows but the time prefix reads
    # uniformly across both kinds.
    overlay_col = 2 + 6 + 3
    overlay_text = body[: max(0, (w - 4) - 9)]
    if overlay_text:
        try:
            stdscr.addstr(y, overlay_col, overlay_text, curses.color_pair(PAIR_CONFIG))
        except curses.error:
            pass


def _draw_config_row(stdscr, y, w, row):
    """Render one CONFIG row: tighter shape than session — just time,
    marker, field label, and old->new transition. Other columns blank."""
    attr = curses.color_pair(PAIR_CONFIG)
    time_str = _time_label(row["epoch"])
    field = row["field_label"]
    transition = f"{row['old_value']} -> {row['new_value']}"
    # Truncate field if combining with transition would overflow
    avail = w - 4 - 11  # after TIME(6) gap(2) MARK(3)
    body = f"{field}  {transition}"
    if len(body) > avail:
        body = body[:avail - 1] + "…"
    stdscr.addstr(y, 2, time_str)
    stdscr.addstr(y, 8, " > ", attr)
    stdscr.addstr(y, 11, body, attr)


def handle_telemetry_kb(state, ch):
    """Keyboard input for TELEMETRY. Scroll via arrows + page keys.
    Tab-switch keys [/] intercepted upstream."""
    if ch == ord('q') or ch == ord('Q'):
        return "quit"
    if ch == curses.KEY_UP:
        state["telemetry_scroll"] = max(0, state.get("telemetry_scroll", 0) - 1)
    elif ch == curses.KEY_DOWN:
        # Upper-bound clamp happens in draw_telemetry once it knows
        # the capacity vs total — no need to clamp aggressively here.
        state["telemetry_scroll"] = state.get("telemetry_scroll", 0) + 1
    elif ch == curses.KEY_PPAGE:
        state["telemetry_scroll"] = max(0, state.get("telemetry_scroll", 0) - 5)
    elif ch == curses.KEY_NPAGE:
        state["telemetry_scroll"] = state.get("telemetry_scroll", 0) + 5
    elif ch == ord('r') or ch == ord('R'):
        # Manual refresh: useful when a session post-mortem just landed
        # while Pitstop was open.
        _refresh_telemetry_caches(state)
    return "continue"


def handle_telemetry_pad(state, etype, code, val):
    """Gamepad input for TELEMETRY. D-pad vertical = scroll. BTN_CONFIRM
    refreshes from disk. Tab-switch buttons intercepted upstream."""
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        _refresh_telemetry_caches(state)
        return "continue"
    if etype == EV_ABS and code == ABS_HAT0Y:
        if val == -1:
            state["telemetry_scroll"] = max(0, state.get("telemetry_scroll", 0) - 1)
        elif val == 1:
            state["telemetry_scroll"] = state.get("telemetry_scroll", 0) + 1
    return "continue"


# === TAB SWITCH ===

def _switch_tab(state, target_tab):
    """Switch tabs. Clears the transient status string so a TUNING save
    failure doesn't follow the user into TELEMETRY. Persistent state
    (matrix edits, cursor position, gamepad status) is preserved per
    dossier Test 4. Invalidates TELEMETRY data caches on entry so a
    newly-saved CONFIG row or post-mortem session appears immediately."""
    if state["current_tab"] != target_tab:
        state["current_tab"] = target_tab
        state["status"] = ""
        if target_tab == CURRENT_TAB_TELEMETRY:
            state["_sessions_cache"] = None
            state["_config_changes_cache"] = None
            state["_career_cache"] = None
            state["_pit_note_cache"] = None


# === MAIN LOOP ===

def _draw(stdscr, state):
    """Single point of render: composes chrome + active tab body."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _draw_title_bar(stdscr, w)
    _draw_tab_strip(stdscr, state["current_tab"], w)
    if state["current_tab"] == CURRENT_TAB_TUNING:
        draw_tuning(stdscr, state)
    else:
        draw_telemetry(stdscr, state)
    _draw_footer(stdscr, h, w, state["current_tab"], state["status"])
    stdscr.refresh()


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(PAIR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CLEAN, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CRASH, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(PAIR_RECOV, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CONFIG, curses.COLOR_CYAN, curses.COLOR_BLACK)
    stdscr.timeout(50)

    matrix = load_menu_matrix()
    device_path = find_gamepad()

    # FIX 2: gamepad open failure degrades to keyboard-only, not hard exit.
    fd = None
    gamepad_status = "NO PAD - KB ONLY"
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
        gamepad_status = f"OK ({device_path})"
    except Exception as e:
        gamepad_status = f"NO PAD ({e})"

    state = {
        "matrix": matrix,
        "cursor_idx": 0,
        "current_tab": CURRENT_TAB_TUNING,
        "status": "",
        "gamepad_status": gamepad_status,
        "telemetry_scroll": 0,
        # Telemetry data caches — populated lazily on first TELEMETRY
        # render, invalidated on every tab-switch into TELEMETRY so a
        # CONFIG save or new post-mortem appears without a relaunch.
        "_sessions_cache": None,
        "_config_changes_cache": None,
        "_career_cache": None,
        "_pit_note_cache": None,
    }

    running = True
    while running:
        _draw(stdscr, state)

        # Keyboard input. getch can raise on resize/interrupt — that is
        # the only thing we ignore. Save errors must not be swallowed.
        try:
            ch = stdscr.getch()
        except curses.error:
            ch = -1

        if ch != -1:
            # Tab switching (keyboard) — intercepted before per-tab dispatch
            if ch == ord('['):
                _switch_tab(state, CURRENT_TAB_TUNING)
            elif ch == ord(']'):
                _switch_tab(state, CURRENT_TAB_TELEMETRY)
            else:
                handler = handle_tuning_kb if state["current_tab"] == CURRENT_TAB_TUNING else handle_telemetry_kb
                verb = handler(state, ch)
                if verb in ("quit", "save_exit"):
                    running = False

        # Gamepad input — only if fd was successfully opened
        if fd is not None:
            try:
                data = os.read(fd, EVENT_SIZE)
            except BlockingIOError:
                data = None
            except OSError as e:
                data = None
                state["gamepad_status"] = f"PAD READ ERR ({e.errno})"
            if data and len(data) == EVENT_SIZE:
                _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)

                # Tab switching (gamepad) — intercepted before per-tab dispatch
                if etype == EV_KEY and val == 1 and code == BTN_TL:
                    _switch_tab(state, CURRENT_TAB_TUNING)
                elif etype == EV_KEY and val == 1 and code == BTN_TR:
                    _switch_tab(state, CURRENT_TAB_TELEMETRY)
                else:
                    handler = handle_tuning_pad if state["current_tab"] == CURRENT_TAB_TUNING else handle_telemetry_pad
                    verb = handler(state, etype, code, val)
                    if verb in ("quit", "save_exit"):
                        running = False

    if fd is not None:
        os.close(fd)


if __name__ == "__main__":
    # curses.wrapper restores the terminal on exception before re-raising,
    # so by the time _fatal runs, stderr is plain again and the operator
    # can actually read what we print. _wait_for_dismiss now waits on the
    # gamepad as well, so a corrupted schema or YAML no longer soft-bricks
    # the rig until a power-press.
    try:
        curses.wrapper(main)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        _fatal(f"{e.__class__.__name__}: {e}", exc=e)
