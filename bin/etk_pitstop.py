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
CONFIG_CHANGES_HEADER = "epoch\tgame_id\tfield_label\told_value\tnew_value\n"


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
            footer = "L1/R1 [/]: Switch Tab  B: Quit                      [Task 2: scrolling]"
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


# === TELEMETRY TAB (Task 1 stub; Task 2 wires real data) ===

def draw_telemetry_stub(stdscr, state):
    """Skeletal scaffold matching dossier §11 layout. Task 2 swaps the
    -- placeholders for live ledger reads — geometry is locked here so
    layout regressions get caught before data goes live."""
    h, w = stdscr.getmaxyx()
    _draw_meta_line(stdscr, w, state["gamepad_status"], "")

    y = 5
    stdscr.addstr(y, 2, f"CAREER — {GAME_NAME} ({TARGET_ID})", curses.A_BOLD)
    y += 1
    stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
    y += 1
    stdscr.addstr(y, 2, "--h --m total  --  -- sessions  --  --% clean  --  -- crashes (-- rcv / -- pnc)"[:w - 4], curses.A_DIM)
    y += 1
    stdscr.addstr(y, 2, "-- shaders banked  --  +-- avg/session  --  streak -- (best --)"[:w - 4], curses.A_DIM)
    y += 1
    stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
    y += 2

    # Session table
    if y < h - 6:
        stdscr.addstr(y, 2, "TIME      STATUS              DUR    TEMP    LOAD    RAM     SHD  DRAIN"[:w - 4], curses.A_BOLD)
        y += 1
        stdscr.addstr(y, 2, "-" * (w - 4), curses.A_DIM)
        y += 1
        # Placeholder rows — exact shape Task 2 fills in.
        rows = [
            "--:--     [Task 2: session rows render here]",
            "--:--     [CONFIG events interleave chronologically]",
            "--:--     [Day separators group entries by date]",
        ]
        for r in rows:
            if y >= h - 5:
                break
            stdscr.addstr(y, 2, r[:w - 4], curses.A_DIM)
            y += 1

    # Bottom PIT NOTE block — Task 2 reads $PIT_NOTE_FILE and suppresses
    # this entire band when the file is absent (dossier §13).
    if h > 8:
        stdscr.addstr(h - 5, 2, "-" * (w - 4), curses.A_DIM)
        stdscr.addstr(h - 4, 2, "PIT NOTE  --  [Task 2: optional AI summary when pit_note.txt exists]"[:w - 4], curses.A_DIM)


def handle_telemetry_kb(state, ch):
    """Stub keyboard handler. Only quit honored — scrolling lands in Task 2.
    Tab-switch keys are intercepted upstream."""
    if ch == ord('q') or ch == ord('Q'):
        return "quit"
    return "continue"


def handle_telemetry_pad(state, etype, code, val):
    """Stub gamepad handler. BTN_BACK quits; everything else is ignored.
    Tab-switch buttons are intercepted upstream."""
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    return "continue"


# === TAB SWITCH ===

def _switch_tab(state, target_tab):
    """Switch tabs. Clears the transient status string so a TUNING save
    failure doesn't follow the user into TELEMETRY. Persistent state
    (matrix edits, cursor position, gamepad status) is preserved per
    dossier Test 4."""
    if state["current_tab"] != target_tab:
        state["current_tab"] = target_tab
        state["status"] = ""


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
        draw_telemetry_stub(stdscr, state)
    _draw_footer(stdscr, h, w, state["current_tab"], state["status"])
    stdscr.refresh()


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
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
        "telemetry_scroll": 0,  # reserved for Task 2 — unused by stub
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
