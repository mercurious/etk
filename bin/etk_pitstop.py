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
import subprocess
import glob
import fcntl
import re
import shutil


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

# Sessions shorter than this are aborts, not real attempts. Sourced from
# env.sh; the TELEMETRY tab dims sub-threshold rows so a force-quit or a
# misclassified 2s "crash" doesn't read as signal.
try:
    TELEMETRY_MIN_SESSION_S = int(os.environ.get('TELEMETRY_MIN_SESSION_S', 60))
except ValueError:
    TELEMETRY_MIN_SESSION_S = 60


# === ETK_NO_TARGET (dossier §3) ===
# The launcher sets ETK_NO_TARGET=1 when it could not resolve a PS3 title
# (a fresh rig with no installed game). In that mode TUNING and TELEMETRY
# render an inert panel — no config or ledger is ever touched — and TOOLS
# is the live, default tab so the user can install their first game.
ETK_NO_TARGET = os.environ.get('ETK_NO_TARGET', '0').strip() == '1'


# === TOOLS TAB: INSTALLER PATHS ===
# Routed through env.sh; the fallbacks mirror the existing ETK_ROOT pattern
# so an isolated dev run still has sane values.
RPCS3_BIN = os.environ.get('RPCS3_BIN', '/usr/bin/rpcs3-sa')
RPCS3_GAME_DIR = os.environ.get(
    'RPCS3_GAME_DIR',
    '/storage/games-internal/roms/bios/rpcs3/dev_hdd0/game')
RPCS3_EXDATA_DIR = os.environ.get(
    'RPCS3_EXDATA_DIR',
    '/storage/games-internal/roms/bios/rpcs3/dev_hdd0/home/00000001/exdata')
RPCS3_CUSTOM_CONFIGS = os.environ.get(
    'RPCS3_CUSTOM_CONFIGS',
    '/storage/games-internal/roms/bios/rpcs3/custom_configs')
RPCS3_HDD1_CACHE = os.environ.get(
    'RPCS3_HDD1_CACHE',
    '/storage/games-internal/roms/bios/rpcs3/dev_hdd1/caches')
RPCS3_RUNTIME_CACHE = os.environ.get(
    'RPCS3_RUNTIME_CACHE', '/storage/.cache/rpcs3/cache')
RPCS3_LOG = os.environ.get('RPCS3_LOG', '/storage/.cache/rpcs3/RPCS3.log')
PKG_STAGING_DIR = os.environ.get(
    'PKG_STAGING_DIR', f"{os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')}/pkg_install_drop")
ETK_TEMPLATE_CONFIG = os.environ.get(
    'ETK_TEMPLATE_CONFIG', f"{os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')}/config/etk_template.yml")
ROCKNIX_SYSTEM_CFG = os.environ.get(
    'ROCKNIX_SYSTEM_CFG', '/storage/.config/system/configs/system.cfg')
SHM_DIR = os.environ.get('SHM_DIR', '/dev/shm/etk_shm')
ETK_INSTALL_LOCK = os.environ.get('ETK_INSTALL_LOCK', f"{SHM_DIR}/etk_install_lock")
# L1-screenshot gating mode, shared with bin/input_d.py (which reads it live
# on each L1 press). Cycled from the TOOLS tab. See env.sh for semantics.
SCREENSHOT_MODE_FILE = os.environ.get(
    'SCREENSHOT_MODE_FILE', f"{TELEMETRY_DIR}/screenshot_mode.txt")
SCREENSHOT_MODES = ("always", "in-game", "disabled")
SCREENSHOT_MODE_DEFAULT = "in-game"


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
# All gamepad-related codes live in this single block so the next pad
# target swap is a one-place edit. Mirror any changes here into
# bin/input_d.py.
#
# Stable across Xbox and DS5 virtual pad models:
EV_KEY = 1                  # button event type
EV_ABS = 3                  # axis event type
ABS_HAT0X = 16              # d-pad left/right
ABS_HAT0Y = 17              # d-pad up/down
BTN_TL = 310                # L1 / LB shoulder
BTN_TR = 311                # R1 / RB shoulder
#
# Face buttons — flipped at the InputPlumber DS5 target switch (Rocknix
# nightly-20260520, "inputplumber: use target ds5"). Pre-20260520 the
# Xbox virtual target on this rig was non-standard (confirm=305, back=304);
# the DS5 target uses the standard PlayStation mapping below. PROBE FIRST
# on first boot with bin/gamepad_probe.py and confirm before trusting:
# InputPlumber virtual targets don't always follow physical convention.
BTN_CONFIRM = 304           # BTN_SOUTH = Cross (X)  = confirm  (DS5 standard)
BTN_BACK = 305              # BTN_EAST  = Circle (O) = back     (DS5 standard)


# === TAB DISPATCH ===
# Tab IDs are stable identifiers; the TABS list sets the left-to-right
# DISPLAY + cycle order. L1/R1 and [/] step through TABS, clamped at ends.
CURRENT_TAB_TUNING = 0
CURRENT_TAB_TELEMETRY = 1
CURRENT_TAB_TOOLS = 2

# Tab registry — order here is the on-screen order: [TELEMETRY][TUNING][TOOLS].
# Adding a future tab is one line here plus a new CURRENT_TAB_* constant and
# a matching draw_/handle_ pair. Geometry math handles itself.
TABS = [
    ("TELEMETRY", CURRENT_TAB_TELEMETRY),
    ("TUNING", CURRENT_TAB_TUNING),
    ("TOOLS", CURRENT_TAB_TOOLS),
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
    sys.stderr.write("\n Press B or A (gamepad) or ENTER to close.\n")
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


GAME_NAME = "(no game resolved)" if ETK_NO_TARGET else resolve_game_name(TARGET_ID)


# === GAMEPAD DISCOVERY ===

# Pad-model-agnostic substrings. Rocknix has flipped the InputPlumber
# virtual target between models across nightlies (Xbox -> DS5 in 20260520);
# matching any known virtual-pad signature means a future target swap won't
# strand us on the event9 fallback. Append new substrings here if a probe
# discovers a name we don't already cover. Keep lowercase.
PAD_HINTS = ("xbox", "dualsense", "dual sense", "playstation",
             "sony", "ds5", "wireless controller", "inputplumber")
#
# DS5 (and probably any future model) presents as a *cluster* of sibling
# nodes: the buttons/sticks/d-pad device PLUS Touchpad, Motion Sensors,
# Headset Jack, Battery. They all share the parent name "... Wireless
# Controller", so PAD_HINTS matches all of them. PAD_EXCLUDE filters out
# the sub-nodes so we land on the buttons device. "keyboard" excludes the
# separate "InputPlumber Keyboard" node (a real keyboard, not a gamepad).
PAD_EXCLUDE = ("touchpad", "motion sensor", "headset", "battery", "keyboard")


def _event_num(entry):
    """Sort key: extract the integer suffix of 'eventN' so event2 sorts
    before event10 (lexical sort puts 'event10' before 'event2', which
    landed us on the DS5 Touchpad node post-20260520)."""
    try:
        return int(entry[len('event'):])
    except (ValueError, TypeError):
        return 1 << 30


def find_gamepad():
    """Locate the InputPlumber virtual controller, pad-model-agnostic.
    Returns the matched /dev/input/eventN, or /dev/input/event9 as the
    last-resort fallback. Logs the matched name so a silent wrong-node
    fallback is one grep away from diagnosis."""
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir), key=_event_num):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        name = f.read().strip()
                    nl = name.lower()
                    if any(h in nl for h in PAD_HINTS) and \
                       not any(x in nl for x in PAD_EXCLUDE):
                        try:
                            _log(f"find_gamepad matched /dev/input/{entry} name='{name}'")
                        except Exception:
                            pass
                        return f"/dev/input/{entry}"
    except Exception:
        pass
    try:
        _log("find_gamepad: no PAD_HINTS match, falling back to /dev/input/event9")
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
            footer = "DPAD UP/DN: Move  LT/RT: Change  B: Save  A: Quit  L1/R1: Tabs"
        elif current_tab == CURRENT_TAB_TELEMETRY:
            footer = "DPAD UP/DN: Scroll  B: Refresh  A: Quit  L1/R1: Tabs"
        else:
            footer = "DPAD UP/DN: Move  B: Select  A: Back  L1/R1: Tabs"
        stdscr.addstr(h - 2, 4, footer[:w - 6], curses.A_BOLD)


# === INERT PANEL (ETK_NO_TARGET) ===

def _draw_inert_panel(stdscr, state, tabname):
    """Rendered for TUNING / TELEMETRY when the launcher could not resolve
    a PS3 title (ETK_NO_TARGET). Touches no config and no ledger — it just
    steers the user to the TOOLS tab to install their first game."""
    h, w = stdscr.getmaxyx()
    _draw_meta_line(stdscr, w, state.get("gamepad_status", ""), "")
    y = 6
    stdscr.addstr(y, 4, f"{tabname} unavailable - no PS3 game resolved yet.",
                  curses.A_BOLD)
    y += 2
    for line in (
        "ETK could not determine an active PS3 title, so it will",
        "not edit a default config or ledger (that would strand",
        "your tuning in the wrong file).",
        "",
        "Open the TOOLS tab (L1/R1) to install your first game,",
        "then relaunch Pitstop.",
    ):
        if y >= h - 4:
            break
        stdscr.addstr(y, 4, line[:w - 6],
                      curses.A_BOLD if line.startswith("Open") else curses.A_DIM)
        y += 1


# === TUNING TAB ===

def draw_tuning(stdscr, state):
    """Tuning matrix editor. start_y shifts to row 5 (was row 4 pre-tabs)
    to leave room for the tab strip on row 1."""
    if ETK_NO_TARGET:
        _draw_inert_panel(stdscr, state, "TUNING")
        return
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
                        "peak_load": float(fields[5] or 0),
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
    if status == "ABORTED":
        return curses.A_DIM
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

    if ETK_NO_TARGET:
        _draw_inert_panel(stdscr, state, "TELEMETRY")
        return

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

    # Column header — built from the same _TEL_W_* constants the data
    # rows use, so labels always sit directly above their columns.
    header_line = _telemetry_header()
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


# TELEMETRY session-table column widths. The header and every data row
# are built from these same constants so labels always sit directly
# above their data. Column order (dossier Feature #10): RAM promoted next
# to DUR since it's the live discriminator on Eiger; SHD demoted to the
# rightmost slot since it is all-zero on a saturated vault.
_TEL_W_TIME = 6
_TEL_W_MARK = 3
# 15 chars: fits "RECOVERY:Adreno", "RECOVERY:VkLost", "RECOVERY:Silent".
# Per user feedback 2026-05-23 the R3 origin is NOT shown in STATUS
# (it's in crash_sig) — limited column width is reserved for the
# crash-signature label, which is what drives TUNING decisions.
_TEL_W_STATUS = 15
_TEL_W_DUR = 6
_TEL_W_RAM = 5
_TEL_W_LOAD = 5
_TEL_W_TEMP = 7
_TEL_W_DRAIN = 5
_TEL_W_SHD = 4


def _telemetry_header():
    """Build the session-table column header from the shared _TEL_W_*
    width constants so it can never drift from the data rows."""
    return (
        f"{'TIME':<{_TEL_W_TIME}}"
        f"{'':<{_TEL_W_MARK}}"
        f"{'STATUS':<{_TEL_W_STATUS}}"
        f"  {'DUR':>{_TEL_W_DUR}}"
        f"  {'RAM':>{_TEL_W_RAM}}"
        f"  {'LOAD':>{_TEL_W_LOAD}}"
        f"  {'TEMP':>{_TEL_W_TEMP}}"
        f"  {'DRAIN':>{_TEL_W_DRAIN}}"
        f"  {'SHD':>{_TEL_W_SHD}}"
    )


def _draw_session_row(stdscr, y, w, row):
    """Render one session row. Builds the full line as a single clipped
    string from the shared _TEL_W_* constants, writes it once, then
    overlays the colored STATUS column. Single-write + overlay is
    overflow-safe — a narrow TTY truncates the base line cleanly rather
    than tripping addwstr() returned ERR.

    A sub-threshold session (duration < TELEMETRY_MIN_SESSION_S) is a
    low-confidence row — a force-quit abort, or a short 'crash' whose
    fence/thermal data the post-mortem could not trust. It renders dimmed
    with no status color. A zero-duration row (no reliable session
    anchor at all) additionally gets a '?' mark.

    Mark alphabet (priority order):
      ?  no session anchor (duration == 0)
      +  any session that harvested shaders (CLEAN or crashed)
      *  crash-free run with zero harvest (CLEAN, vault saturated)
      !  PANIC with no harvest
         blank otherwise (RECOVERY/ABORTED, no harvest)
    Reuses the '+'='shader gain' meaning from the HUD vault string
    (e.g. '345 0+'). Together '+'/'*' stack down the ledger as the
    positive markers — a glanceable indicator of progress over time."""
    status = row["status"]
    duration = row["duration_s"]
    shaders = row["shaders_harvested"]
    low_conf = duration < TELEMETRY_MIN_SESSION_S

    if duration == 0:
        mark = " ? "
    elif shaders > 0:
        mark = " + "
    elif status == "CLEAN":
        mark = " * "
    elif status == "PANIC":
        mark = " ! "
    else:
        mark = "   "

    time_str = _time_label(row["epoch"])
    dur_str = _format_duration(duration)

    peak_t = row["peak_temp"]
    avg_t = row["avg_temp"]
    temp_str = f"{avg_t}/{peak_t}C" if peak_t > 0 else "----"

    load = row["peak_load"]
    load_str = f"{load:.1f}" if load > 0 else "----"

    ram = row["peak_ram_mb"]
    if ram >= 1000:
        ram_str = f"{ram // 1000}.{(ram % 1000) // 100}G"
    elif ram > 0:
        ram_str = f"{ram}MB"
    else:
        ram_str = "----"

    drain = row["drain_pct"]
    drain_str = f"{drain}%"
    shd_str = str(row["shaders_harvested"])

    base = (
        f"{time_str:<{_TEL_W_TIME}}"
        f"{mark:<{_TEL_W_MARK}}"
        f"{status[:_TEL_W_STATUS]:<{_TEL_W_STATUS}}"
        f"  {dur_str:>{_TEL_W_DUR}}"
        f"  {ram_str:>{_TEL_W_RAM}}"
        f"  {load_str:>{_TEL_W_LOAD}}"
        f"  {temp_str:>{_TEL_W_TEMP}}"
        f"  {drain_str:>{_TEL_W_DRAIN}}"
        f"  {shd_str:>{_TEL_W_SHD}}"
    )
    base = base[:w - 4]
    base_attr = curses.A_DIM if low_conf else curses.A_NORMAL
    try:
        stdscr.addstr(y, 2, base, base_attr)
    except curses.error:
        return

    # Overlay the STATUS substring with its color. Column start is
    # computed from the fixed prefix widths (TIME + MARK). A low-
    # confidence row keeps the dim attr instead of a status color.
    status_col = 2 + _TEL_W_TIME + _TEL_W_MARK
    status_text = status[:_TEL_W_STATUS]
    status_attr = curses.A_DIM if low_conf else _status_attr(status)
    if status_col + len(status_text) <= w - 1:
        try:
            stdscr.addstr(y, status_col, status_text, status_attr)
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


# ============================================================
# === TOOLS TAB — PS3 PACKAGE INSTALLER / UNINSTALLER ===
# ============================================================
# Proven on-device in spike/ (see project memory + dossier).
# RPCS3 has no headless install: --installpkg always pops a GUI
# confirm dialog. The installer launches RPCS3 attached to the
# live sway session, polls for the "PKG Installation" window,
# focuses it and injects Enter via /dev/uinput to confirm — no
# screen coordinates needed (Enter triggers the default button).
# Uninstall is a pure filesystem rm. Progress is surfaced through
# mako notifications (dbus-send). RPCS3 owns the screen during an
# install; this curses UI is only visible before and after.
# ============================================================

_TOOLS_MENU = ["Install a staged PS3 Package", "Uninstall a Game",
               "Screenshot on L1"]
# Index of the in-place toggle item (label gets ": <mode>" appended at draw).
_TOOLS_SCREENSHOT_IDX = 2


def _read_screenshot_mode():
    """Current L1-screenshot mode. Absent / unreadable / unrecognized file
    falls back to the in-game default -- matches input_d.py's reader."""
    try:
        with open(SCREENSHOT_MODE_FILE) as f:
            mode = f.read().strip().lower()
        return mode if mode in SCREENSHOT_MODES else SCREENSHOT_MODE_DEFAULT
    except Exception:
        return SCREENSHOT_MODE_DEFAULT


def _cycle_screenshot_mode():
    """Advance always -> in-game -> disabled -> always and persist atomically
    (H2 tmp+mv idiom). Returns the new mode, or the unchanged current mode if
    the write failed (so the UI status can report honestly)."""
    cur = _read_screenshot_mode()
    nxt = SCREENSHOT_MODES[(SCREENSHOT_MODES.index(cur) + 1) % len(SCREENSHOT_MODES)]
    try:
        os.makedirs(os.path.dirname(SCREENSHOT_MODE_FILE), exist_ok=True)
        tmp = SCREENSHOT_MODE_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(nxt + "\n")
        os.replace(tmp, SCREENSHOT_MODE_FILE)
        return nxt
    except Exception:
        return cur


def _tools_env():
    """Environment for the install subprocesses. Pitstop runs inside the
    ES sway session (launched from Tools), so WAYLAND_DISPLAY /
    XDG_RUNTIME_DIR are normally inherited; fill sane fallbacks and derive
    SWAYSOCK so the installer still works if a var didn't propagate."""
    env = dict(os.environ)
    env.setdefault('XDG_RUNTIME_DIR', '/var/run/0-runtime-dir')
    env.setdefault('WAYLAND_DISPLAY', 'wayland-1')
    env['QT_QPA_PLATFORM'] = 'wayland'
    if not env.get('SWAYSOCK'):
        socks = sorted(glob.glob(f"{env['XDG_RUNTIME_DIR']}/sway-ipc.*.sock"))
        if socks:
            env['SWAYSOCK'] = socks[0]
    return env


class _Notifier:
    """Posts mako notifications via dbus-send, reusing one notification id
    (replaces_id) so progress updates one toast in place. Best-effort —
    a notification failure must never abort an install."""

    def __init__(self):
        self._id = "0"
        self._env = _tools_env()

    def post(self, summary, body, timeout=6000):
        try:
            r = subprocess.run(
                ["dbus-send", "--session", "--print-reply",
                 "--dest=org.freedesktop.Notifications",
                 "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications.Notify",
                 "string:ETK Pitstop", "uint32:" + self._id, "string:",
                 "string:" + summary, "string:" + body,
                 "array:string:", "dict:string:variant:",
                 "int32:%d" % timeout],
                env=self._env, capture_output=True, text=True, timeout=10)
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if ln.startswith("uint32"):
                    self._id = ln.split()[1]
                    break
        except Exception as e:
            _log(f"mako notify failed: {e}")


# --- /dev/uinput virtual keyboard (confirms the install dialog) ---

def _uinput_ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord('U') << 8) | nr

_UI_SET_EVBIT = _uinput_ioc(1, 100, 4)
_UI_SET_KEYBIT = _uinput_ioc(1, 101, 4)
_UI_DEV_SETUP = _uinput_ioc(1, 3, 92)
_UI_DEV_CREATE = _uinput_ioc(0, 1, 0)
_UI_DEV_DESTROY = _uinput_ioc(0, 2, 0)
_KEY_ENTER = 28


def _uinput_open_keyboard():
    """Create a /dev/uinput virtual keyboard that can emit KEY_ENTER.
    Returns the fd, or None if uinput is unavailable. UI_SET_EVBIT /
    UI_SET_KEYBIT take the bit number as a plain integer arg."""
    try:
        fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, _UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, _UI_SET_EVBIT, 0)          # EV_SYN
        fcntl.ioctl(fd, _UI_SET_KEYBIT, _KEY_ENTER)
        setup = (struct.pack('HHHH', 0x03, 0x1234, 0x5678, 1)
                 + b'ETK Virtual Keyboard'.ljust(80, b'\0')
                 + struct.pack('I', 0))
        fcntl.ioctl(fd, _UI_DEV_SETUP, setup)
        fcntl.ioctl(fd, _UI_DEV_CREATE)
        return fd
    except Exception as e:
        _log(f"uinput keyboard create failed: {e}")
        return None


def _uinput_tap_enter(fd):
    """Emit one KEY_ENTER press+release on the virtual keyboard."""
    def emit(t, c, v):
        os.write(fd, struct.pack('llHHi', 0, 0, t, c, v))
    try:
        emit(EV_KEY, _KEY_ENTER, 1); emit(0, 0, 0)   # 0,0,0 = SYN_REPORT
        time.sleep(0.06)
        emit(EV_KEY, _KEY_ENTER, 0); emit(0, 0, 0)
    except Exception as e:
        _log(f"uinput tap failed: {e}")


def _uinput_close(fd):
    try:
        fcntl.ioctl(fd, _UI_DEV_DESTROY)
        os.close(fd)
    except Exception:
        pass


# --- sway window helpers ------------------------------------

def _swaymsg(args, env):
    try:
        return subprocess.run(["swaymsg", *args], env=env,
                              capture_output=True, text=True,
                              timeout=10).stdout
    except Exception as e:
        _log(f"swaymsg failed: {e}")
        return ""


def _find_install_dialog(env):
    """Walk the sway tree for RPCS3's 'PKG Installation' dialog window.
    Returns its con_id, or None if not yet mapped."""
    try:
        tree = json.loads(_swaymsg(["-t", "get_tree"], env))
    except Exception:
        return None
    found = [None]

    def walk(n):
        nm = (n.get("name") or "").lower()
        if found[0] is None and "pkg" in nm and "install" in nm:
            found[0] = n.get("id")
        for key in ("nodes", "floating_nodes"):
            for child in n.get(key, []):
                walk(child)

    walk(tree)
    return found[0]


def _restore_pitstop_window(env):
    """Re-assert the Pitstop (foot) window as fullscreen. Launching RPCS3
    knocks foot out of fullscreen in sway's tree, which would otherwise
    leave the curses UI tiled and clipped off-screen on return. The next
    main-loop _draw picks up the restored size via getmaxyx()."""
    try:
        _swaymsg(['[app_id="foot"]', 'fullscreen', 'enable'], env)
        time.sleep(0.3)
    except Exception as e:
        _log(f"restore pitstop window failed: {e}")


def _kill_rpcs3():
    """Force-kill any RPCS3 process. Pitstop's own cmdline does not contain
    these patterns, so pkill -f cannot self-match."""
    for pat in ("rpcs3-sa", "AppRun.wrapped"):
        try:
            subprocess.run(["pkill", "-9", "-f", pat],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    time.sleep(1)


def _rpcs3_running():
    try:
        return subprocess.run(["pgrep", "-f", "rpcs3-sa|AppRun.wrapped"],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


# --- package / config inspection ----------------------------

def _pkg_title_id(pkg_path):
    """Extract the PS3 title ID (AAAA00000) from a .pkg header — the
    content ID is a 36+ byte ASCII field at header offset 0x30."""
    try:
        with open(pkg_path, 'rb') as f:
            head = f.read(0x80)
        if head[0:4] != b'\x7fPKG':
            return None
        cid = head[0x30:0x30 + 48].decode('latin-1', 'ignore')
        m = re.search(r'[A-Z]{4}[0-9]{5}', cid)
        return m.group(0) if m else None
    except Exception as e:
        _log(f"pkg title id read failed: {e}")
        return None


def _sfo_title(game_dir):
    """Parse PARAM.SFO for the human TITLE string. None on any failure —
    the caller falls back to the title ID (dossier §7.2 step 5)."""
    path = os.path.join(game_dir, "PARAM.SFO")
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[0:4] != b'\x00PSF':
            return None
        key_tbl = struct.unpack('<I', data[0x08:0x0C])[0]
        data_tbl = struct.unpack('<I', data[0x0C:0x10])[0]
        n_entries = struct.unpack('<I', data[0x10:0x14])[0]
        for i in range(n_entries):
            e = 0x14 + i * 16
            key_off = struct.unpack('<H', data[e:e + 2])[0]
            data_len = struct.unpack('<I', data[e + 4:e + 8])[0]
            data_off = struct.unpack('<I', data[e + 12:e + 16])[0]
            key_end = data.index(b'\x00', key_tbl + key_off)
            key = data[key_tbl + key_off:key_end].decode('ascii', 'ignore')
            if key == "TITLE":
                raw = data[data_tbl + data_off:data_tbl + data_off + data_len]
                title = raw.split(b'\x00', 1)[0].decode('utf-8', 'ignore').strip()
                return title or None
    except Exception as e:
        _log(f"PARAM.SFO parse failed: {e}")
    return None


def _sanitize_psn_name(name):
    """Make a string safe as a .psn filename. The .psn CONTENT stays the
    raw title ID regardless — only the filename is sanitized."""
    out = []
    for ch in name:
        if ch in '/\\:"\'`' or ord(ch) < 32:
            out.append(' ')
        else:
            out.append(ch)
    cleaned = ' '.join(''.join(out).split()).strip()
    return cleaned or "PS3 Game"


def _scan_staging():
    """Return (pkgs, raps) — sorted lists of absolute paths in the staging
    drop folder. Case-insensitive extension match. Dotfiles are skipped:
    copying from macOS litters the folder with '._name.pkg' AppleDouble
    siblings and '.DS_Store', and '._name.pkg' would otherwise read as a
    second package ("2 packages staged - install one at a time")."""
    pkgs, raps = [], []
    try:
        for entry in sorted(os.listdir(PKG_STAGING_DIR)):
            if entry.startswith('.'):
                continue
            full = os.path.join(PKG_STAGING_DIR, entry)
            if not os.path.isfile(full):
                continue
            low = entry.lower()
            if low.endswith('.pkg'):
                pkgs.append(full)
            elif low.endswith('.rap'):
                raps.append(full)
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"staging scan failed: {e}")
    return pkgs, raps


def _list_psn_games():
    """Enumerate installed PS3 games from the .psn launchers. Returns a
    list of {name, title_id, psn} dicts, sorted by name."""
    games = []
    try:
        for entry in sorted(os.listdir(PS3_ROMS_DIR)):
            # Skip dotfiles — macOS '._name.psn' AppleDouble siblings would
            # otherwise show up as junk entries in the uninstall list.
            if entry.startswith('.') or not entry.endswith(".psn"):
                continue
            full = os.path.join(PS3_ROMS_DIR, entry)
            try:
                with open(full) as f:
                    tid = f.read().strip()
            except Exception:
                tid = ""
            games.append({"name": entry[:-4], "title_id": tid, "psn": full})
    except Exception as e:
        _log(f"psn enumerate failed: {e}")
    return games


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except Exception:
                pass
    return total


# --- post-install onboarding writers ------------------------

def _write_psn(title_id, human_name):
    """Write PS3_ROMS_DIR/<human_name>.psn containing the raw title ID
    (no trailing newline — matches the existing .psn convention). Atomic
    tmp+replace. Returns the .psn basename."""
    fname = _sanitize_psn_name(human_name) + ".psn"
    path = os.path.join(PS3_ROMS_DIR, fname)
    tmp = path + ".etk.tmp"
    with open(tmp, 'w') as f:
        f.write(title_id)
    os.replace(tmp, path)
    return fname


def _enable_mangohud(psn_filename):
    """Enable the per-game MangoHud overlay by upserting the key into
    Rocknix's system.cfg with a LITERAL-string match.

    We deliberately do NOT call Rocknix's set_setting: its del_setting does
    `sed "/^${key}=/d"`, and our key — ps3["Game.psn"].rocknix.mangohud.enabled
    — contains regex metacharacters (the [ ] " of the bracket index), so the
    sed never matches and the old line is never removed. set_setting then
    just appends a duplicate on every install; duplicate lines make
    get_setting emit a multi-line value ("1 1 1") which fails runemu.sh's
    `= "1"` test, so MangoHud silently never turns on. This upsert removes
    every prior line for the exact key (literal compare) and appends one,
    so it is idempotent and self-heals any existing duplicates."""
    key = f'ps3["{psn_filename}"].rocknix.mangohud.enabled'
    try:
        if os.path.exists(ROCKNIX_SYSTEM_CFG):
            with open(ROCKNIX_SYSTEM_CFG) as f:
                lines = f.readlines()
        else:
            lines = []
        kept = [ln for ln in lines if ln.split("=", 1)[0] != key]
        if kept and not kept[-1].endswith("\n"):
            kept[-1] += "\n"
        kept.append(key + "=1\n")
        tmp = ROCKNIX_SYSTEM_CFG + ".etk.tmp"
        with open(tmp, 'w') as f:
            f.writelines(kept)
        os.replace(tmp, ROCKNIX_SYSTEM_CFG)
        return True
    except Exception as e:
        _log(f"mangohud enable failed: {e}")
        return False


def _deploy_template_config(title_id):
    """Copy the ETK template to custom_configs/config_<ID>.yml so the
    game's first launch runs tuned. Never clobbers a config that already
    exists. Returns a short status word for the result panel."""
    dest = os.path.join(RPCS3_CUSTOM_CONFIGS, f"config_{title_id}.yml")
    if os.path.exists(dest):
        return "kept existing"
    try:
        if not os.path.exists(ETK_TEMPLATE_CONFIG):
            _log(f"template config missing: {ETK_TEMPLATE_CONFIG}")
            return "template missing"
        os.makedirs(RPCS3_CUSTOM_CONFIGS, exist_ok=True)
        shutil.copyfile(ETK_TEMPLATE_CONFIG, dest)
        return "applied"
    except Exception as e:
        _log(f"template config deploy failed: {e}")
        return "failed"


# --- the blocking install / uninstall sequences -------------

def _run_install(pkg_path, rap_path, notify):
    """Blocking install of one staged .pkg. Returns (ok, [result lines]).
    RPCS3 owns the screen for the duration; progress goes to mako."""
    env = _tools_env()
    title_id = _pkg_title_id(pkg_path) or "????"

    if _rpcs3_running():
        return False, ["RPCS3 is already running.",
                       "Close the game first, then retry."]

    # Install lock — the Sentry stays parked in IDLE while we drive RPCS3
    # so no phantom telemetry session fires. Clear any stale lock first
    # (we own it exclusively; installs are idle-time only).
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        open(ETK_INSTALL_LOCK, 'w').close()
    except Exception as e:
        _log(f"install lock write failed: {e}")

    proc = None
    kfd = None
    try:
        notify.post("Installing PS3 package",
                    f"Preparing {os.path.basename(pkg_path)}\n"
                    "RPCS3 will open on screen - do not touch the controller.")
        before = set(os.listdir(RPCS3_GAME_DIR)) if os.path.isdir(RPCS3_GAME_DIR) else set()

        proc = subprocess.Popen(
            [RPCS3_BIN, "--installpkg", pkg_path], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

        # Wait for the install-dialog WINDOW to map in sway (RPCS3 parses
        # the PKG header before the window is mappable, so poll the tree).
        dlg = None
        t0 = time.time()
        while time.time() - t0 < 90:
            time.sleep(1)
            dlg = _find_install_dialog(env)
            if dlg is not None:
                break
        if dlg is None:
            return False, ["RPCS3 did not show the install dialog.",
                           "The package may be unreadable.",
                           "Staged files were kept."]

        notify.post("Installing PS3 package", "Confirming install dialog...")
        _swaymsg(["[con_id=%d]" % dlg, "focus"], env)
        time.sleep(0.6)
        kfd = _uinput_open_keyboard()
        if kfd is None:
            return False, ["Could not create the virtual keyboard",
                           "needed to confirm the install dialog."]
        time.sleep(1.8)             # let sway register the new keyboard
        confirmed = False
        for _attempt in range(4):
            _swaymsg(["[con_id=%d]" % dlg, "focus"], env)
            time.sleep(0.3)
            _uinput_tap_enter(kfd)
            time.sleep(3)
            if _find_install_dialog(env) is None:
                confirmed = True
                break
        if not confirmed:
            return False, ["Could not confirm the install dialog.",
                           "Staged files were kept."]

        notify.post("Installing PS3 package",
                    "Extracting package files - please wait...")
        new_id = None
        t1 = time.time()
        proc_dead_since = None
        while time.time() - t1 < 600:
            time.sleep(3)
            try:
                fresh = [d for d in (set(os.listdir(RPCS3_GAME_DIR)) - before)
                         if "lock" not in d.lower()]
            except Exception:
                fresh = []
            if fresh:
                new_id = fresh[0]
                eboot = os.path.join(RPCS3_GAME_DIR, new_id,
                                     "USRDIR", "EBOOT.BIN")
                if os.path.exists(eboot):
                    break
            if proc.poll() is not None:
                if new_id:
                    break
                # RPCS3 exited without producing a game folder — give the
                # filesystem a short grace window to settle, then stop so a
                # failed install can't hang the loop for the full 600s.
                if proc_dead_since is None:
                    proc_dead_since = time.time()
                elif time.time() - proc_dead_since > 15:
                    break
        if not new_id:
            return False, ["Install did not complete - no game folder",
                           "appeared. Staged files were kept so you",
                           "can retry."]

        _kill_rpcs3()    # RPCS3 self-exits after install; make sure it is gone

        human = _sfo_title(os.path.join(RPCS3_GAME_DIR, new_id)) or new_id

        rap_done = "none staged"
        if rap_path:
            try:
                os.makedirs(RPCS3_EXDATA_DIR, exist_ok=True)
                shutil.copyfile(
                    rap_path,
                    os.path.join(RPCS3_EXDATA_DIR, os.path.basename(rap_path)))
                rap_done = "copied to exdata"
            except Exception as e:
                _log(f"rap copy failed: {e}")
                rap_done = "COPY FAILED"

        psn_name = _write_psn(new_id, human)
        mh_ok = _enable_mangohud(psn_name)
        cfg_status = _deploy_template_config(new_id)

        # Delete staged files — success path only. A failed install above
        # returns early and leaves them untouched for a retry. Also sweep
        # macOS AppleDouble / .DS_Store cruft so the drop folder is clean
        # for the next game: the common workflow is dropping the .pkg over
        # SMB from a Mac Finder, which litters the folder with '._' files
        # (Rocknix even ships a "Remove ._ Files" Tools utility for this).
        cleanup = [f for f in (pkg_path, rap_path) if f]
        try:
            for entry in os.listdir(PKG_STAGING_DIR):
                if entry.startswith('.'):
                    cleanup.append(os.path.join(PKG_STAGING_DIR, entry))
        except Exception:
            pass
        for staged in cleanup:
            try:
                os.remove(staged)
            except Exception as e:
                _log(f"staging cleanup failed for {staged}: {e}")

        notify.post("Install complete",
                    f"{human} installed.\n"
                    "Press START > Game Settings > Update Gamelists "
                    "to add it to your library.", timeout=15000)
        return True, [
            f"INSTALLED:  {human}",
            f"  title id   : {new_id}",
            f"  licence    : {rap_done}",
            f"  launcher   : {psn_name}",
            f"  mangohud   : {'enabled' if mh_ok else 'FAILED - enable manually'}",
            f"  etk config : {cfg_status}",
            "",
            "Press START > Game Settings > Update Gamelists",
            "on the handheld to see it in your library.",
        ]
    except Exception as e:
        _log(f"install flow error: {e}\n{traceback.format_exc()}")
        return False, ["Install error:",
                       f"  {e.__class__.__name__}: {str(e)[:48]}"]
    finally:
        if kfd is not None:
            _uinput_close(kfd)
        _kill_rpcs3()
        # RPCS3 knocked the Pitstop (foot) window out of fullscreen in
        # sway; re-assert it so the curses UI is not left tiled/clipped.
        _restore_pitstop_window(env)
        try:
            os.remove(ETK_INSTALL_LOCK)
        except Exception:
            pass


def _run_uninstall(game, notify):
    """Blocking uninstall of one game (a dict from _list_psn_games).
    Removes artifacts + RPCS3 caches; preserves savedata and the ETK
    shader vault. Returns (ok, [result lines])."""
    tid = game.get("title_id") or ""
    name = game.get("name", "?")

    if not re.match(r'^[A-Z]{4}[0-9]{5}$', tid):
        # Orphan .psn (no valid title ID) — just remove the launcher file.
        try:
            os.remove(game["psn"])
            return True, [f"Removed orphan launcher: {name}",
                          "(its .psn held no valid title ID)"]
        except Exception as e:
            return False, [f"Could not remove launcher: {e}"]

    if _rpcs3_running():
        return False, ["RPCS3 is running - close the game first."]

    dir_targets = [
        os.path.join(RPCS3_GAME_DIR, tid),
        os.path.join(RPCS3_RUNTIME_CACHE, tid),
        os.path.join(RPCS3_HDD1_CACHE, f"{tid}_{tid}"),
    ]
    file_targets = [game["psn"]]
    try:
        for entry in os.listdir(RPCS3_EXDATA_DIR):
            if tid in entry and entry.lower().endswith('.rap'):
                file_targets.append(os.path.join(RPCS3_EXDATA_DIR, entry))
    except Exception:
        pass

    freed = sum(_dir_size(d) for d in dir_targets if os.path.isdir(d))
    removed = 0
    for d in dir_targets:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                removed += 1
            except Exception as e:
                _log(f"uninstall rmtree {d}: {e}")
    for f in file_targets:
        if os.path.isfile(f):
            try:
                os.remove(f)
                removed += 1
            except Exception as e:
                _log(f"uninstall rm {f}: {e}")

    mb = freed // (1024 * 1024)
    notify.post("Game uninstalled",
                f"{name} removed - {mb} MB freed.\n"
                "Press START > Game Settings > Update Gamelists to refresh.",
                timeout=15000)
    return True, [
        f"UNINSTALLED:  {name}",
        f"  title id : {tid}",
        f"  removed  : {removed} items, {mb} MB freed",
        "  kept     : your save data + ETK shader vault",
        "",
        "Press START > Game Settings > Update Gamelists",
        "on the handheld to refresh your library.",
    ]


# === TOOLS TAB — DRAW ===

def _draw_tools_busy(stdscr, kind):
    """Paint a 'working' frame before a blocking TOOLS op. RPCS3 covers
    this during an install; mako carries the live progress."""
    try:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        _draw_title_bar(stdscr, w)
        _draw_tab_strip(stdscr, CURRENT_TAB_TOOLS, w)
        if kind == "install":
            msg = "Installing - RPCS3 will open. Do not touch the controller."
        else:
            msg = "Uninstalling - please wait..."
        stdscr.addstr(h // 2, max(2, (w - len(msg)) // 2), msg[:w - 4],
                      curses.A_BOLD)
        hint = "Watch the on-screen notifications for progress."
        stdscr.addstr(h // 2 + 2, max(2, (w - len(hint)) // 2), hint[:w - 4],
                      curses.A_DIM)
        stdscr.refresh()
    except curses.error:
        pass


def draw_tools(stdscr, state):
    """TOOLS tab — a 2-item menu (Install / Uninstall) with confirm,
    game-list and result sub-screens. A simple sub-mode state machine
    keyed on state['tools_mode']."""
    h, w = stdscr.getmaxyx()
    _draw_meta_line(stdscr, w, state.get("gamepad_status", ""), "")
    mode = state.get("tools_mode", "menu")
    y = 5

    def put(row, col, text, attr=curses.A_NORMAL):
        try:
            stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    if mode == "menu":
        put(y, 2, "TOOLS", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        for i, label in enumerate(_TOOLS_MENU):
            if i == _TOOLS_SCREENSHOT_IDX:
                label = f"{label}: {_read_screenshot_mode()}"
            sel = (i == state.get("tools_cursor", 0))
            put(y, 4, "> " if sel else "  ",
                curses.color_pair(1) if sel else curses.A_NORMAL)
            put(y, 6, f"{i + 1}. {label}",
                curses.A_REVERSE if sel else curses.A_NORMAL)
            y += 2
        y += 1
        put(y, 4, "Screenshot on L1: always / in-game / disabled "
                  "(CONFIRM cycles)", curses.A_DIM); y += 2
        put(y, 4, "Staging drop folder (place ONE .pkg + .rap):",
            curses.A_DIM); y += 1
        put(y, 6, PKG_STAGING_DIR, curses.A_DIM)

    elif mode == "install_confirm":
        pkg, rap, tid = state["tools_pkg"]
        put(y, 2, "INSTALL - CONFIRM", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        put(y, 4, f"Package : {os.path.basename(pkg)}"); y += 1
        put(y, 4, f"Title ID: {tid}"); y += 1
        rap_txt = os.path.basename(rap) if rap else "none staged"
        put(y, 4, f"Licence : {rap_txt}"); y += 2
        put(y, 4, "RPCS3 will open on screen for about a minute.",
            curses.A_BOLD); y += 1
        put(y, 4, "Do NOT touch the controller while it installs.",
            curses.A_BOLD); y += 2
        put(y, 4, "B: Install     A: Cancel",
            curses.color_pair(1) | curses.A_BOLD)

    elif mode == "uninstall_list":
        games = state.get("tools_games", [])
        put(y, 2, f"UNINSTALL - SELECT A GAME  ({len(games)})",
            curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
        if not games:
            put(y, 4, "No installed PS3 games found.", curses.A_DIM); y += 2
            put(y, 4, "A: Back", curses.color_pair(1) | curses.A_BOLD)
        else:
            cur = state.get("tools_cursor", 0)
            cap = max(1, (h - 4) - y)
            if len(games) <= cap:
                off = 0
            else:
                off = min(max(0, cur - cap // 2), len(games) - cap)
            for row, idx in enumerate(range(off, min(off + cap, len(games)))):
                g = games[idx]
                sel = (idx == cur)
                put(y + row, 4, "> " if sel else "  ",
                    curses.color_pair(1) if sel else curses.A_NORMAL)
                put(y + row, 6, f"{g['name']}  ({g['title_id']})",
                    curses.A_REVERSE if sel else curses.A_NORMAL)

    elif mode == "uninstall_confirm":
        g = state["tools_games"][state["tools_cursor"]]
        put(y, 2, "UNINSTALL - CONFIRM", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        put(y, 4, f"Delete:   {g['name']}", curses.A_BOLD); y += 1
        put(y, 4, f"Title ID: {g['title_id']}"); y += 2
        put(y, 4, "Removes the game, licence, launcher and caches."); y += 1
        put(y, 4, "Keeps your save data and ETK shader vault."); y += 2
        put(y, 4, "B: Delete     A: Cancel",
            curses.color_pair(1) | curses.A_BOLD)

    elif mode == "result":
        ok, lines = state.get("tools_result") or (False, ["(no result)"])
        put(y, 2, "DONE" if ok else "FAILED", curses.A_BOLD | (
            curses.color_pair(PAIR_CLEAN) if ok else curses.color_pair(PAIR_CRASH)))
        y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        for ln in lines:
            if y >= h - 4:
                break
            put(y, 4, ln)
            y += 1
        if y < h - 4:
            y += 1
            put(y, 4, "B / A: Back to menu",
                curses.color_pair(1) | curses.A_BOLD)


# === TOOLS TAB — INPUT ===

def _tools_move(state, delta):
    mode = state.get("tools_mode", "menu")
    if mode == "menu":
        state["tools_cursor"] = (state.get("tools_cursor", 0) + delta) % len(_TOOLS_MENU)
    elif mode == "uninstall_list":
        n = len(state.get("tools_games", []))
        if n:
            state["tools_cursor"] = (state.get("tools_cursor", 0) + delta) % n


def _tools_select(state):
    """A / CONFIRM action. May queue a blocking op via state['tools_action']
    for the main loop to run. Always returns 'continue' (TOOLS never
    save_exits — B from the menu is the only quit path)."""
    mode = state.get("tools_mode", "menu")

    if mode == "menu":
        if state.get("tools_cursor", 0) == 0:        # Install
            pkgs, raps = _scan_staging()
            if len(pkgs) == 0:
                state["tools_result"] = (False, [
                    "No package staged.",
                    "",
                    "Drop ONE .pkg file (plus its .rap, if the game",
                    "needs one) into the staging folder:",
                    "  " + PKG_STAGING_DIR,
                    "then choose Install again."])
                state["tools_mode"] = "result"
            elif len(pkgs) > 1:
                state["tools_result"] = (False, [
                    f"{len(pkgs)} packages staged - install one at a time.",
                    "",
                    "Leave only ONE .pkg in the staging folder:",
                    "  " + PKG_STAGING_DIR])
                state["tools_mode"] = "result"
            else:
                pkg = pkgs[0]
                rap = raps[0] if raps else None
                state["tools_pkg"] = (pkg, rap, _pkg_title_id(pkg) or "unknown")
                state["tools_mode"] = "install_confirm"
        elif state.get("tools_cursor", 0) == 1:      # Uninstall
            state["tools_games"] = _list_psn_games()
            state["tools_cursor"] = 0
            state["tools_mode"] = "uninstall_list"
        else:                                        # Screenshot-on-L1 toggle
            state["status"] = f"Screenshot on L1: {_cycle_screenshot_mode()}"

    elif mode == "install_confirm":
        pkg, rap, _tid = state["tools_pkg"]
        state["tools_action"] = ("install", pkg, rap)

    elif mode == "uninstall_list":
        if state.get("tools_games"):
            state["tools_mode"] = "uninstall_confirm"

    elif mode == "uninstall_confirm":
        g = state["tools_games"][state["tools_cursor"]]
        state["tools_action"] = ("uninstall", g)

    elif mode == "result":
        state["tools_mode"] = "menu"
        state["tools_cursor"] = 0

    return "continue"


def _tools_back(state):
    """B action. Returns 'quit' only from the top menu (matching the other
    tabs' B-quits-the-app behavior); sub-screens just step back."""
    mode = state.get("tools_mode", "menu")
    if mode == "menu":
        return "quit"
    if mode == "uninstall_confirm":
        state["tools_mode"] = "uninstall_list"
    else:
        state["tools_mode"] = "menu"
        state["tools_cursor"] = 0
    return "continue"


def handle_tools_kb(state, ch):
    """Keyboard input for TOOLS. Tab-switch keys [/] intercepted upstream."""
    if ch == ord('q') or ch == ord('Q'):
        return "quit"
    if ch == curses.KEY_UP:
        _tools_move(state, -1)
    elif ch == curses.KEY_DOWN:
        _tools_move(state, 1)
    elif ch == ord('\n') or ch == ord('s'):
        return _tools_select(state)
    elif ch in (curses.KEY_BACKSPACE, 8, 127):
        return _tools_back(state)
    return "continue"


def handle_tools_pad(state, etype, code, val):
    """Gamepad input for TOOLS. L1/R1 intercepted upstream for tabs."""
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _tools_select(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return _tools_back(state)
    if etype == EV_ABS and code == ABS_HAT0Y:
        if val == -1:
            _tools_move(state, -1)
        elif val == 1:
            _tools_move(state, 1)
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


def _cycle_tab(state, direction):
    """Step to the previous/next tab in TABS display order, clamped at the
    ends. Extends the original two-tab [/] idiom cleanly to N tabs."""
    order = [tab_id for _, tab_id in TABS]
    try:
        i = order.index(state["current_tab"])
    except ValueError:
        i = 0
    j = max(0, min(len(order) - 1, i + direction))
    if order[j] != state["current_tab"]:
        _switch_tab(state, order[j])


# === MAIN LOOP ===

def _draw(stdscr, state):
    """Single point of render: composes chrome + active tab body."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _draw_title_bar(stdscr, w)
    _draw_tab_strip(stdscr, state["current_tab"], w)
    if state["current_tab"] == CURRENT_TAB_TUNING:
        draw_tuning(stdscr, state)
    elif state["current_tab"] == CURRENT_TAB_TELEMETRY:
        draw_telemetry(stdscr, state)
    else:
        draw_tools(stdscr, state)
    _draw_footer(stdscr, h, w, state["current_tab"], state["status"])
    stdscr.refresh()


def _dispatch_kb(state, ch):
    """Route a keyboard event to the active tab's handler. In ETK_NO_TARGET
    mode TUNING/TELEMETRY are inert — only TOOLS receives input (plus a
    plain 'q' quit) so no config or ledger is ever touched."""
    ct = state["current_tab"]
    if ct == CURRENT_TAB_TOOLS:
        return handle_tools_kb(state, ch)
    if ETK_NO_TARGET:
        return "quit" if ch in (ord('q'), ord('Q')) else "continue"
    if ct == CURRENT_TAB_TUNING:
        return handle_tuning_kb(state, ch)
    return handle_telemetry_kb(state, ch)


def _dispatch_pad(state, etype, code, val):
    """Gamepad counterpart of _dispatch_kb."""
    ct = state["current_tab"]
    if ct == CURRENT_TAB_TOOLS:
        return handle_tools_pad(state, etype, code, val)
    if ETK_NO_TARGET:
        if etype == EV_KEY and val == 1 and code == BTN_BACK:
            return "quit"
        return "continue"
    if ct == CURRENT_TAB_TUNING:
        return handle_tuning_pad(state, etype, code, val)
    return handle_telemetry_pad(state, etype, code, val)


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(PAIR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CLEAN, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CRASH, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(PAIR_RECOV, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(PAIR_CONFIG, curses.COLOR_CYAN, curses.COLOR_BLACK)
    stdscr.timeout(50)

    # ETK_NO_TARGET: no PS3 title resolved — skip the schema/config load
    # entirely. TUNING/TELEMETRY render inert panels and TOOLS is the live
    # tab, so the matrix is never drawn, edited or saved; loading a fallback
    # game's config here would be misleading and pointless.
    matrix = [] if ETK_NO_TARGET else load_menu_matrix()
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
        # Default tab: TOOLS when no game resolved (the install front door,
        # dossier §3), otherwise TELEMETRY.
        "current_tab": CURRENT_TAB_TOOLS if ETK_NO_TARGET else CURRENT_TAB_TELEMETRY,
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
        # TOOLS tab sub-mode state machine (menu / install_confirm /
        # uninstall_list / uninstall_confirm / result).
        "tools_mode": "menu",
        "tools_cursor": 0,
        "tools_pkg": None,
        "tools_games": [],
        "tools_result": None,
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
            # Tab switching (keyboard) — intercepted before per-tab dispatch.
            # [/] now CYCLE through the TABS display order (3 tabs).
            if ch == ord('['):
                _cycle_tab(state, -1)
            elif ch == ord(']'):
                _cycle_tab(state, 1)
            else:
                verb = _dispatch_kb(state, ch)
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

                # Tab switching (gamepad) — L1/R1 cycle through the tabs.
                if etype == EV_KEY and val == 1 and code == BTN_TL:
                    _cycle_tab(state, -1)
                elif etype == EV_KEY and val == 1 and code == BTN_TR:
                    _cycle_tab(state, 1)
                else:
                    verb = _dispatch_pad(state, etype, code, val)
                    if verb in ("quit", "save_exit"):
                        running = False

        # Execute a queued TOOLS long-operation (install / uninstall).
        # Runs here in the main loop so it has stdscr for the 'busy' frame;
        # the op itself blocks (RPCS3 owns the screen during an install)
        # and surfaces live progress through mako notifications.
        action = state.pop("tools_action", None)
        if action:
            _draw_tools_busy(stdscr, action[0])
            notifier = _Notifier()
            if action[0] == "install":
                ok, lines = _run_install(action[1], action[2], notifier)
            else:
                ok, lines = _run_uninstall(action[1], notifier)
            state["tools_result"] = (ok, lines)
            state["tools_mode"] = "result"
            # Drain input buffered during the (multi-second) blocking op so
            # a button pressed mid-install doesn't skip past the result.
            if fd is not None:
                try:
                    while os.read(fd, EVENT_SIZE):
                        pass
                except (BlockingIOError, OSError):
                    pass
            try:
                curses.flushinp()
            except Exception:
                pass

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
