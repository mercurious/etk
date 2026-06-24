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
import threading
import shutil
import glob
import fcntl
import re
import shutil
import hashlib


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

# DRIVER tab (Turnip env-var dials). These are NOT RPCS3 config keys — they
# inject through the proven profile.d path (same mechanism as 098-etk-stage3),
# so start_rpcs3.sh picks them up from /etc/profile at the NEXT game launch and
# they survive a cold boot (ROCKNIX leaves foreign profile.d entries). The
# active-tune signature is what session_postmortem.sh stamps onto each ledger
# row (tune_tag), making genuine-play sessions attributable to their dial set.
TURNIP_PROFILE_D = os.environ.get(
    'TURNIP_PROFILE_D', "/storage/.config/profile.d/097-etk-turnip-dials")
ACTIVE_TUNE_FILE = os.environ.get(
    'ACTIVE_TUNE_FILE', f"{TELEMETRY_DIR}/active_tune.txt")

# DRIVER BUILD selector (Stage IV — catalog of bindable Turnip .so builds).
# Distinct from the env-var dials above: the dials tune whatever driver is
# loaded (next-launch); the BUILD selector picks WHICH .so binds over the stock
# /usr/lib driver (reboot-gated, since a bind-mount can't swap a live driver).
#   drivers/   = the catalog (install.sh stages every host drivers/*.so here)
#   selected   = operator's pick (a catalog filename, or the synthetic "stock")
#   loaded     = ground truth stamped by etk-turnip-bind.sh at boot — what is
#                ACTUALLY bound right now (so the header never claims a build is
#                live when it's only pending a reboot). See install.sh Step 6.5.
TURNIP_DIR = os.environ.get('TURNIP_DIR', "/storage/turnip")
TURNIP_DRIVERS_DIR = os.path.join(TURNIP_DIR, "drivers")
TURNIP_SELECTED_FILE = os.path.join(TURNIP_DIR, "selected")
TURNIP_LOADED_FILE = os.path.join(TURNIP_DIR, "loaded")
STOCK_BUILD = "stock"  # synthetic catalog id: unbind, run ROCKNIX's own Turnip

# POWER tab (CPU/GPU governors + clock pinning). Schema-driven (power_profiles.json:
# OPP-bounded pulldowns + named presets). The tab resolves a preset/knob set into a
# flat path<TAB>value profile that etk-power.service re-applies at boot (govs/freqs
# reset on reboot); APPLY also writes the knobs live. NO OC — the OPP table caps the
# pulldowns at rated max. Stamps pwr= into the ledger (session_postmortem).
POWER_PROFILES_JSON = os.environ.get('POWER_PROFILES_JSON', f"{ETK_ROOT}/config/power_profiles.json")
POWER_DIR = os.environ.get('ETK_POWER_DIR', "/storage/etk-power")
POWER_PROFILE_FILE = os.path.join(POWER_DIR, "profile")  # path<TAB>value + '# pwr=' header

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
# Crash-signature catalog (id -> summary/explanation/suggested_changes), shared
# with the Sentry. Backs the TELEMETRY session-detail crash card.
CRASH_SIG_FILE = os.environ.get("SIGNATURES_FILE", f"{ETK_ROOT}/config/crash_signatures.json")


# === VAULT HYGIENE (Manage Shaders) ===
# The Manage Shaders screen is a front-end over tools/vault_sweep.sh (the ONE
# sweep engine) — same single-injector ethos as PADDOCK over paddock_sync.sh.
VAULT_SWEEP = os.environ.get('VAULT_SWEEP', f"{ETK_ROOT}/tools/vault_sweep.sh")
# Chipset key for the vault path. env.sh exports CHIPSET; off-rig fall back to
# the hostname (which IS the SoC on these rigs), then the SM8250 reference.
_CHIPSET = os.environ.get('CHIPSET') or os.environ.get('ETK_CHIPSET') or ''
if not _CHIPSET:
    try:
        _CHIPSET = os.uname().nodename or 'SM8250'
    except Exception:
        _CHIPSET = 'SM8250'
VAULT_BASE = os.environ.get('VAULT_BASE', f"{ETK_ROOT}/vault/{_CHIPSET}")
# Persistent last-played anchor (env.sh RECENT_ID_FILE) — resolves the 'Current'
# scope when Pitstop runs idle from the Tools menu (no rpcs3 process).
RECENT_ID_FILE = os.environ.get(
    'RECENT_ID_FILE', f"{ETK_ROOT}/vault/last_played_id.txt")


# === PADDOCK TAB: PRIVATE PADDOCK (0.3.0) ===
# PADDOCK is the on-device sync client for the USER'S OWN private GitHub
# repo (dossiers/Release030PrivatePaddockDossier.md): push/pull your own
# vaults, tunes, and saves. ETK shares nothing — there is no public index
# (the 0.5.0 subscribe-client direction was retired with the distribution
# withdrawal). The tab only exists when install.sh's PADDOCK LINK step has
# written the credential file (PADDOCK_TOKEN in etk.conf → paddock.json).
PADDOCK_CRED = os.environ.get(
    'PADDOCK_CRED', "/storage/roms/etk/config/paddock.json")
PADDOCK_SYNC = os.environ.get(
    'PADDOCK_SYNC', f"{ETK_ROOT}/bin/paddock_sync.sh")
PROTUNE_INSTALLER = os.environ.get(
    'PROTUNE_INSTALLER', f"{ETK_ROOT}/pro-tuning/install-protune.sh")
# The homologation primitive: sha256 of the first 64 KB of the Turnip driver,
# the exact gate install.sh and install-protune.sh use. Match => the shared
# shader vault is guaranteed loadable on this rig.
FREEDRENO_SO = os.environ.get('FREEDRENO_SO', '/usr/lib/libvulkan_freedreno.so')
# Chipset key for index matching. On these rigs the hostname IS the SoC
# (e.g. "SM8250"); ETK_CHIPSET overrides for dev/testing.
PADDOCK_CHIPSET = os.environ.get('ETK_CHIPSET', '').strip()
# known_repo hatch: LOCAL, gitignored game-source pointers (pkg+rap URLs the
# operator supplies for a game they own). NEVER published — keeping these out
# of the curated index is the defensibility line (dossier §8).
PADDOCK_REPOS_JSON = os.environ.get(
    'PADDOCK_REPOS_JSON', f"{ETK_ROOT}/config/paddock_repos.json")


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
CURRENT_TAB_PADDOCK = 3
CURRENT_TAB_DRIVER = 4
CURRENT_TAB_POWER = 5

# Tab registry — order here is the on-screen order AND the [/] · L1/R1 cycle
# order (clamped, no wrap). PADDOCK is LAST on purpose: it is the only tab that
# fires a network event (its sync-status fetch on first entry) and it is
# optional (gated below on the private-paddock credential). Parking it at the
# end keeps the three default OFFLINE tabs (TELEMETRY/TUNING/TOOLS) all
# reachable by cycling without ever landing on PADDOCK and triggering a fetch.
# Adding a future tab is one line here plus a new CURRENT_TAB_* constant and a
# matching draw_/handle_ pair. Geometry math handles itself.
TABS = [
    ("TELEMETRY", CURRENT_TAB_TELEMETRY),
    ("TUNING", CURRENT_TAB_TUNING),
    ("TOOLS", CURRENT_TAB_TOOLS),
    ("DRIVER", CURRENT_TAB_DRIVER),
    ("POWER", CURRENT_TAB_POWER),
    ("PADDOCK", CURRENT_TAB_PADDOCK),
]

# Private Paddock gating (0.3.0, operator requirement): the PADDOCK tab
# only appears when the rig is wired to the user's own private repo —
# install.sh's PADDOCK LINK step writes the credential. Unconfigured rigs
# see three tabs and never know; discovery lives in the README, not the UI.
if not os.path.exists(PADDOCK_CRED):
    TABS = [t for t in TABS if t[1] != CURRENT_TAB_PADDOCK]


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


_DRIVER_LABEL_CACHE = None


def _build_label(build_id):
    """Short, human-friendly name for a catalog build id (a .so filename).
    'libvulkan_freedreno-rocknix-26.1.3-lsd.so' -> 'rocknix-26.1.3-lsd';
    'stock'/'' -> 'stock'. Filename IS the catalog id (operator drops the .so
    into drivers/ and we name it by what they called it)."""
    if not build_id or build_id == STOCK_BUILD:
        return STOCK_BUILD
    name = re.sub(r"\.so$", "", build_id)
    name = re.sub(r"^libvulkan_freedreno[-_]?", "", name)
    return name or build_id


def _read_build_pointer(path):
    """Read a one-line build pointer file (selected/loaded). Returns the
    stripped id, or None if absent/empty/unreadable."""
    try:
        with open(path) as f:
            v = f.read().strip()
        return v or None
    except OSError:
        return None


def _list_builds():
    """The selectable catalog: every drivers/*.so id plus the synthetic
    'stock'. Sorted, with stock last so the dial reads builds-then-stock."""
    builds = []
    try:
        for n in sorted(os.listdir(TURNIP_DRIVERS_DIR)):
            if n.endswith(".so"):
                builds.append(n)
    except OSError:
        pass
    builds.append(STOCK_BUILD)
    return builds


def driver_string():
    """Header label for the LOADED Turnip driver — what is actually bound right
    now, not what's pending a reboot. e.g. 'Turnip 26.1.3 (rocknix-26.1.3-lsd)'
    for a bound catalog build, or 'Turnip 26.1.3 (stock)' for the OS driver.
    Computed once, cached.

    Ground truth comes from /storage/turnip/loaded (stamped by etk-turnip-bind.sh
    at boot). We fall back to the live bind-mount probe for pre-catalog rigs that
    have no 'loaded' stamp yet. Version is read from the live /usr/lib .so (the
    Mesa string is baked in). Degrades to a bare 'Turnip' on any probe error
    (never blanks the header)."""
    global _DRIVER_LABEL_CACHE
    if _DRIVER_LABEL_CACHE is not None:
        return _DRIVER_LABEL_CACHE
    so = "/usr/lib/libvulkan_freedreno.so"
    ver = ""
    try:
        out = subprocess.run(
            ["sh", "-c", f"strings '{so}' 2>/dev/null | grep -oE 'Mesa [0-9]+\\.[0-9]+\\.[0-9]+' | head -1"],
            capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        if m:
            ver = " " + m.group(1)
    except Exception:
        pass
    loaded = _read_build_pointer(TURNIP_LOADED_FILE)
    if loaded is None:
        # Pre-catalog fallback: a live freedreno bind == a custom build is up.
        try:
            out = subprocess.run(["sh", "-c", "mount | grep -c freedreno"],
                                 capture_output=True, text=True, timeout=4).stdout.strip()
            loaded = "custom" if (out.isdigit() and int(out) > 0) else STOCK_BUILD
        except Exception:
            loaded = None
    tail = f" ({_build_label(loaded)})" if loaded else ""
    _DRIVER_LABEL_CACHE = f"Turnip{ver}{tail}"
    return _DRIVER_LABEL_CACHE


def _draw_meta_line(stdscr, w, gamepad_status, lap_str):
    target = f"GAME: {GAME_NAME}  |  DRIVER: {driver_string()}"
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
            footer = "DPAD: Select  A: Back/Quit  B: Detail  L1/R1: Tabs"
        elif current_tab == CURRENT_TAB_PADDOCK:
            footer = "DPAD: Game/Col  B: Toggle/APPLY  A: Quit  L1/R1: Tabs"
        elif current_tab == CURRENT_TAB_DRIVER:
            footer = "DPAD UP/DN: Move  LT/RT: Change  B: Toggle/APPLY  A: Quit  L1/R1: Tabs"
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
                        # Trailing cols (added 2026-06: DRIVER tab + crash frame).
                        # len-guarded so older 14/15-col rows parse fine.
                        "tune_tag": fields[14] if len(fields) > 14 else "",
                        "crash_shot": fields[15] if len(fields) > 15 else "",
                        # G-INSTR fps (col 17-19) + tune attribution (col 20-21).
                        # len-guarded so older narrower rows parse fine.
                        "fps_med": float(fields[16]) if len(fields) > 16 and fields[16] else 0.0,
                        "fps_1low": float(fields[17]) if len(fields) > 17 and fields[17] else 0.0,
                        "ft_p99_ms": float(fields[18]) if len(fields) > 18 and fields[18] else 0.0,
                        "res_scale": int(fields[19]) if len(fields) > 19 and fields[19].strip().isdigit() else 0,
                        "gpu_mhz": int(fields[20]) if len(fields) > 20 and fields[20].strip().isdigit() else 0,
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

    # === DETAIL SUB-MODE === full-screen card for the selected row.
    if state.get("telemetry_mode", "table") == "detail":
        _draw_session_detail(stdscr, state, sessions, config_changes)
        # ASCII scroll indicators (the card sets _detail_content_h/view_bot/scroll).
        h, w = stdscr.getmaxyx()
        sc = state.get("telemetry_detail_scroll", 0)
        bot = state.get("_detail_view_bot", h - 4)
        try:
            if sc > 0:
                stdscr.addstr(5, w - 11, "[^ DPAD]", curses.A_DIM)
            if state.get("_detail_content_h", 0) - sc > bot:
                stdscr.addstr(bot, w - 11, "[v DPAD]", curses.A_DIM)
        except curses.error:
            pass
        return

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

    # Cursor-follows-scroll. telemetry_cursor is an absolute index into
    # merged; derive the scroll window so the cursor stays on-screen (same
    # idiom as the TOOLS uninstall list).
    n = len(merged)
    cursor = max(0, min(state.get("telemetry_cursor", 0), n - 1)) if n else 0
    state["telemetry_cursor"] = cursor
    scroll = state.get("telemetry_scroll", 0)
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + table_capacity:
        scroll = cursor - table_capacity + 1
    max_scroll = max(0, n - table_capacity)
    scroll = max(0, min(scroll, max_scroll))
    state["telemetry_scroll"] = scroll
    visible = merged[scroll: scroll + table_capacity]

    if not visible:
        stdscr.addstr(y, 2, "No telemetry recorded for this game yet."[:w - 4], curses.A_DIM)
        y += 1
    else:
        prev_day = None
        for vis_i, row in enumerate(visible):
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

            sel = (scroll + vis_i == cursor)
            if row["_kind"] == "session":
                _draw_session_row(stdscr, y, w, row, selected=sel)
            else:
                _draw_config_row(stdscr, y, w, row, selected=sel)
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
# G-INSTR + tune-attribution columns. FPS sits next to DUR (headline, safe from
# right-edge clipping); RES/CLK ride the rightmost slots (context — clip-tolerant
# on a narrow panel, where base[:w-4] truncates them cleanly).
_TEL_W_FPS = 4
_TEL_W_RES = 4
_TEL_W_CLK = 4


# --- Session-detail data-viz (ASCII; no Unicode — Pitstop renders pure ASCII,
#     no locale.setlocale, so block glyphs are unsafe) -----------------------

def _envint(key, default):
    try:
        return int(float(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return default

# Gauge ceilings. Temp from the device profile's RACE_THRESHOLD where env.sh
# exported it (SM8250.sh RACE_THRESHOLD=86); fixed sane fallbacks otherwise.
_GAUGE_TEMP_MAX = _envint("RACE_THRESHOLD", 90)
_GAUGE_LOAD_MAX = 16          # ~8 cores * 2.0 loadavg
_GAUGE_RAM_MAX_MB = 8192      # 8 GB reference
_GAUGE_DRAIN_MAX = 30         # |drain%| over a session
# FPS gauges (G-INSTR). FPS bars: fuller = better (toward PS3-native 60).
# Frametime bar: fuller = WORSE (toward a 200ms severe-stutter ceiling) —
# same "full = bad" sense as the temp/drain gauges.
_GAUGE_FPS_MAX = 60
_GAUGE_FT_MAX = 200
_GAUGE_W = 18

_CRASH_SIG_CACHE = None

def _load_crash_sigs():
    """id -> signature dict from crash_signatures.json (cached once). Returns
    {} on any error so the detail view always degrades gracefully."""
    global _CRASH_SIG_CACHE
    if _CRASH_SIG_CACHE is None:
        try:
            data = json.load(open(CRASH_SIG_FILE, encoding="utf-8"))
            _CRASH_SIG_CACHE = {s["id"]: s for s in data if isinstance(s, dict) and "id" in s}
        except Exception:
            _CRASH_SIG_CACHE = {}
    return _CRASH_SIG_CACHE

def _gauge_bar(value, vmax, width=_GAUGE_W):
    """ASCII proportional bar '[####......]', clamped to [0, vmax]."""
    if vmax <= 0:
        vmax = 1
    frac = max(0.0, min(1.0, float(value) / vmax))
    filled = int(round(frac * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _telemetry_header():
    """Build the session-table column header from the shared _TEL_W_*
    width constants so it can never drift from the data rows."""
    return (
        f"{'TIME':<{_TEL_W_TIME}}"
        f"{'':<{_TEL_W_MARK}}"
        f"{'STATUS':<{_TEL_W_STATUS}}"
        f"  {'DUR':>{_TEL_W_DUR}}"
        f"  {'FPS':>{_TEL_W_FPS}}"
        f"  {'RAM':>{_TEL_W_RAM}}"
        f"  {'LOAD':>{_TEL_W_LOAD}}"
        f"  {'TEMP':>{_TEL_W_TEMP}}"
        f"  {'DRAIN':>{_TEL_W_DRAIN}}"
        f"  {'SHD':>{_TEL_W_SHD}}"
        f"  {'RES':>{_TEL_W_RES}}"
        f"  {'CLK':>{_TEL_W_CLK}}"
    )


def _draw_session_row(stdscr, y, w, row, selected=False):
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

    fps = row.get("fps_med", 0.0)
    fps_str = f"{fps:.0f}" if fps > 0 else "--"
    res = row.get("res_scale", 0)
    res_str = f"{res}%" if res > 0 else "--"
    clk = row.get("gpu_mhz", 0)
    clk_str = str(clk) if clk > 0 else "--"

    base = (
        f"{time_str:<{_TEL_W_TIME}}"
        f"{mark:<{_TEL_W_MARK}}"
        f"{status[:_TEL_W_STATUS]:<{_TEL_W_STATUS}}"
        f"  {dur_str:>{_TEL_W_DUR}}"
        f"  {fps_str:>{_TEL_W_FPS}}"
        f"  {ram_str:>{_TEL_W_RAM}}"
        f"  {load_str:>{_TEL_W_LOAD}}"
        f"  {temp_str:>{_TEL_W_TEMP}}"
        f"  {drain_str:>{_TEL_W_DRAIN}}"
        f"  {shd_str:>{_TEL_W_SHD}}"
        f"  {res_str:>{_TEL_W_RES}}"
        f"  {clk_str:>{_TEL_W_CLK}}"
    )
    base = base[:w - 4]
    base_attr = curses.A_DIM if low_conf else curses.A_NORMAL
    if selected:
        base_attr |= curses.A_REVERSE
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
    if selected:
        status_attr |= curses.A_REVERSE
    if status_col + len(status_text) <= w - 1:
        try:
            stdscr.addstr(y, status_col, status_text, status_attr)
        except curses.error:
            pass


def _draw_session_detail(stdscr, state, sessions, config_changes):
    """Full-screen card for the cursor-selected row (telemetry_mode='detail').
    CLEAN/ABORTED -> ASCII gauges; RECOVERY/PANIC -> crash_signatures.json
    summary/explanation + fence + suggested fix, degrading gracefully when no
    signature matches (R3_PANIC / PANIC_REBOOT / empty). B returns to table."""
    h, w = stdscr.getmaxyx()
    merged = sorted(list(sessions) + list(config_changes),
                    key=lambda r: r["epoch"], reverse=True)
    cur = state.get("telemetry_cursor", 0)
    y = 5
    # Scroll window: the detail card can exceed the panel height (the G-INSTR
    # FRAMERATE gauges pushed a CLEAN card past the bottom), so render through a
    # scroll offset. put() tracks the content extent into _detail_content_h and
    # clips to the visible window; handle_telemetry_pad/_kb scroll via D-pad and
    # clamp against what put() measured. Same cursor-window spirit as the
    # DRIVER-tab scroll fix (587b652), minus the cursor (a card has none).
    _D_TOP, _D_BOT = 5, h - 4
    _dscroll = state.get("telemetry_detail_scroll", 0)
    state["_detail_view_bot"] = _D_BOT
    state["_detail_content_h"] = _D_TOP

    def put(row_y, col, text, attr=curses.A_NORMAL):
        if row_y > state["_detail_content_h"]:
            state["_detail_content_h"] = row_y
        ry = row_y - _dscroll
        if ry < _D_TOP or ry > _D_BOT:
            return
        try:
            stdscr.addstr(ry, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    def rule(row_y):
        put(row_y, 2, "-" * (w - 4), curses.A_DIM)

    if not merged or cur >= len(merged):
        put(y, 2, "No session selected.  (B: back)", curses.A_DIM)
        state["_detail_row"] = None
        return
    row = merged[cur]
    # Cache for the input handler (the [up] crash-frame preview reads it).
    state["_detail_row"] = row

    def ram_str(mb):
        if mb >= 1000:
            return f"{mb / 1000:.1f}GB"
        return f"{mb}MB" if mb > 0 else "----"

    # --- CONFIG-change row: minimal card ---
    if row.get("_kind") == "config":
        put(y, 2, "CONFIG CHANGE", curses.A_BOLD); y += 1
        rule(y); y += 2
        put(y, 4, f"{_day_label(row['epoch'])}  {_time_label(row['epoch'])}", curses.A_DIM); y += 2
        put(y, 4, row["field_label"], curses.A_BOLD); y += 1
        put(y, 4, f"{row['old_value']}  ->  {row['new_value']}", curses.color_pair(PAIR_CONFIG))
        return

    # --- SESSION row ---
    status = row["status"]
    is_crash = status.startswith("RECOVERY") or status == "PANIC"
    # crash_sig may stack multiple signatures, comma-joined (e.g.
    # "R3_PANIC,GPU_FENCE_TIMEOUT"). Resolve each against the catalog, then
    # float the real CAUSE to the front: R3_PANIC ("how recovery fired") and
    # THERMAL_INFERRED ("a guess") are meta-signals, not diagnoses, so they
    # never headline a card when a concrete cause is also present.
    _META_SIGS = ("R3_PANIC", "THERMAL_INFERRED")
    _cat = _load_crash_sigs()
    _components = [c.strip() for c in row.get("crash_sig", "").split(",") if c.strip()] if is_crash else []
    _matched = [_cat[c] for c in _components if c in _cat]
    _matched.sort(key=lambda s: s.get("id") in _META_SIGS)  # stable: causes first, meta last
    sig = _matched[0] if _matched else None
    sev = f"   [{sig['severity'].upper()}]" if sig and sig.get("severity") else ""

    put(y, 2, f"{GAME_NAME} - {TARGET_ID}    {_day_label(row['epoch'])} {_time_label(row['epoch'])}",
        curses.A_BOLD); y += 1
    put(y, 2, f"{status}{sev}", _status_attr(status) | curses.A_BOLD); y += 1
    rule(y); y += 1

    dur_h = _format_duration(row["duration_s"])
    peak_t, avg_t = row["peak_temp"], row["avg_temp"]
    load, ram = row["peak_load"], row["peak_ram_mb"]

    if is_crash:
        if _matched:
            put(y, 2, sig.get("summary", ""), curses.A_BOLD); y += 1
            for ln in _wrap_text(sig.get("explanation", ""), w - 6, max_lines=4):
                put(y, 2, ln, curses.A_DIM); y += 1
            # Friendly secondary line: labels of any OTHER concrete causes,
            # plus an explicit manual-recovery note when R3 fired but isn't
            # the headline. No raw signature IDs in the player-facing card.
            others = [m.get("label") or m.get("id") for m in _matched
                      if m is not sig and m.get("id") not in _META_SIGS]
            if others:
                put(y, 2, "Also flagged: " + ", ".join(others), curses.A_DIM); y += 1
            if sig.get("id") != "R3_PANIC" and any(m.get("id") == "R3_PANIC" for m in _matched):
                put(y, 2, "Recovered manually (R3).", curses.A_DIM); y += 1
        else:
            put(y, 2, f"No crash-signature record for '{row.get('crash_sig','') or 'unknown'}'.",
                curses.A_BOLD); y += 1
            put(y, 2, "Manual recovery (R3) or kernel panic - no diagnostic detail captured.",
                curses.A_DIM); y += 1
        y += 1; rule(y); y += 1
        fence = row.get("fence_at_crash", 0)
        put(y, 4, f"Died at fence: {fence if fence else 'n/a'}     Ran: {dur_h}"
                  f"     Temp peak: {peak_t}C     RAM peak: {ram_str(ram)}"); y += 2
        # The DRIVER-dial condition + the captured frame — the link from the
        # ledger row to the tune / the visual. ONE compact line so it never
        # pushes the SUGGESTED FIX off a short, non-scrolling card.
        # [up] fires a mako thumbnail of the frame (handle_telemetry_pad).
        tune = row.get("tune_tag", "")
        shot = row.get("crash_shot", "")
        meta = []
        if tune and tune != "default":
            meta.append("dial " + tune)
        if shot and shot != "-":
            meta.append("frame ↑ preview")
        if meta:
            put(y, 4, "  ·  ".join(meta), curses.color_pair(PAIR_CONFIG)); y += 1
        # Union of suggested changes across every matched signature, deduped
        # by yaml_key (first wins) — a multi-cause crash gets the combined dials.
        seen, changes = set(), []
        for m in _matched:
            for ch in (m.get("suggested_changes") or []):
                k = ch.get("yaml_key", "").strip()
                if k and k not in seen:
                    seen.add(k); changes.append(ch)
        if changes:
            put(y, 2, "SUGGESTED FIX  (TUNING tab):",
                curses.color_pair(PAIR_CLEAN) | curses.A_BOLD); y += 1
            for ch in changes:
                put(y, 4, f"* {ch.get('yaml_key','').strip()}  ->  {ch.get('new_value','')}"); y += 1
    else:
        put(y, 4, f"Duration   {dur_h}", curses.A_BOLD); y += 1
        put(y, 4, f"Shaders +  {row['shaders_harvested']}    (vault delta this run)"); y += 2
        put(y, 4, f"{'Temp':<8}{('peak '+str(peak_t)+'C avg '+str(avg_t)+'C'):<20}"
                  f"{_gauge_bar(peak_t, _GAUGE_TEMP_MAX)}"); y += 1
        put(y, 4, f"{'Load':<8}{('peak '+format(load,'.1f')):<20}"
                  f"{_gauge_bar(load, _GAUGE_LOAD_MAX)}"); y += 1
        put(y, 4, f"{'RAM':<8}{('peak '+ram_str(ram)):<20}"
                  f"{_gauge_bar(ram, _GAUGE_RAM_MAX_MB)}"); y += 1
        drain = row["drain_pct"]
        put(y, 4, f"{'Drain':<8}{(str(drain)+'%'):<20}"
                  f"{_gauge_bar(abs(drain), _GAUGE_DRAIN_MAX)}"); y += 1
        put(y, 4, f"Thermal overrides: {row['thermal_overrides']}"); y += 1

        # G-INSTR framerate — the newly-collected per-run fps/frametime, rendered
        # as gauges: median (headline), 1%-low (the stutter floor), and P99
        # frametime (the hitch tail; fuller bar = worse). Skipped on pre-G-INSTR
        # rows (fps_med == 0) so old sessions render unchanged.
        fps_med = row.get("fps_med", 0.0)
        if fps_med > 0:
            y += 1
            put(y, 4, "FRAMERATE  (G-INSTR)",
                curses.color_pair(PAIR_CLEAN) | curses.A_BOLD); y += 1
            fps_low = row.get("fps_1low", 0.0)
            ft_p99 = row.get("ft_p99_ms", 0.0)
            put(y, 4, f"{'FPS med':<8}{(format(fps_med, '.1f')+' fps'):<20}"
                      f"{_gauge_bar(fps_med, _GAUGE_FPS_MAX)}"); y += 1
            put(y, 4, f"{'1%-low':<8}{(format(fps_low, '.1f')+' fps'):<20}"
                      f"{_gauge_bar(fps_low, _GAUGE_FPS_MAX)}"); y += 1
            hitch = (1000.0 / ft_p99) if ft_p99 > 0 else 0.0
            put(y, 4, f"{'P99 ft':<8}{(format(ft_p99, '.0f')+'ms ~'+format(hitch, '.0f')+'fps'):<20}"
                      f"{_gauge_bar(ft_p99, _GAUGE_FT_MAX)}"); y += 1
            res = row.get("res_scale", 0)
            clk = row.get("gpu_mhz", 0)
            ctx = []
            if res:
                ctx.append(f"{res}% res")
            if clk:
                ctx.append(f"{clk}MHz")
            if ctx:
                put(y, 4, "Tune:    " + "  ".join(ctx), curses.A_DIM); y += 1


def _draw_config_row(stdscr, y, w, row, selected=False):
    """Render one CONFIG row: time, marker, field label, old->new
    transition. Other columns blank. Single-write + colored overlay
    pattern matches _draw_session_row for overflow safety."""
    time_str = _time_label(row["epoch"])
    field = row["field_label"]
    transition = f"{row['old_value']} -> {row['new_value']}"
    body = f"{field}  {transition}"

    base = f"{time_str:<6} > {body}"
    base = base[:w - 4]
    base_attr = curses.A_REVERSE if selected else curses.A_NORMAL
    try:
        stdscr.addstr(y, 2, base, base_attr)
    except curses.error:
        return
    # Overlay just the body text with the CONFIG color so the row is
    # visually distinct from session rows but the time prefix reads
    # uniformly across both kinds.
    overlay_col = 2 + 6 + 3
    overlay_text = body[: max(0, (w - 4) - 9)]
    if overlay_text:
        try:
            stdscr.addstr(y, overlay_col, overlay_text,
                          curses.color_pair(PAIR_CONFIG) | (curses.A_REVERSE if selected else 0))
        except curses.error:
            pass


def handle_telemetry_kb(state, ch):
    """Keyboard input for TELEMETRY. table mode: arrows/page move the row
    cursor, Enter opens the detail card, 'r' refreshes. detail mode:
    Backspace/Enter return to the table. Tab keys [/] intercepted upstream."""
    if ch == ord('q') or ch == ord('Q'):
        return "quit"
    if state.get("telemetry_mode", "table") == "detail":
        if ch in (curses.KEY_BACKSPACE, 8, 127, ord('\n'), ord('\r')):
            state["telemetry_mode"] = "table"
        elif ch == ord('p'):
            _preview_crash_frame(state, (state.get("_detail_row") or {}).get("crash_shot", ""))
        elif ch == curses.KEY_UP:
            state["telemetry_detail_scroll"] = max(0, state.get("telemetry_detail_scroll", 0) - 1)
        elif ch == curses.KEY_DOWN:
            _dmax = max(0, state.get("_detail_content_h", 5) - state.get("_detail_view_bot", 20))
            state["telemetry_detail_scroll"] = min(_dmax, state.get("telemetry_detail_scroll", 0) + 1)
        return "continue"
    if ch == curses.KEY_UP:
        state["telemetry_cursor"] = max(0, state.get("telemetry_cursor", 0) - 1)
    elif ch == curses.KEY_DOWN:
        # Upper bound is clamped in draw_telemetry once it knows the count.
        state["telemetry_cursor"] = state.get("telemetry_cursor", 0) + 1
    elif ch == curses.KEY_PPAGE:
        state["telemetry_cursor"] = max(0, state.get("telemetry_cursor", 0) - 5)
    elif ch == curses.KEY_NPAGE:
        state["telemetry_cursor"] = state.get("telemetry_cursor", 0) + 5
    elif ch in (ord('\n'), ord('\r'), ord('s')):
        state["telemetry_mode"] = "detail"
        state["telemetry_detail_scroll"] = 0
    elif ch == ord('r') or ch == ord('R'):
        # Manual refresh: useful when a post-mortem lands while Pitstop is open.
        _refresh_telemetry_caches(state)
    return "continue"


def handle_telemetry_pad(state, etype, code, val):
    """Gamepad input for TELEMETRY. table mode: D-pad vertical moves the row
    cursor, CONFIRM opens the detail card, BACK quits. detail mode: CONFIRM or
    BACK return to the table. (Manual refresh on tab re-entry is automatic, so
    CONFIRM is repurposed from refresh -> open-detail per the dossier.)"""
    mode = state.get("telemetry_mode", "table")
    if mode == "detail":
        if etype == EV_KEY and val == 1 and code == BTN_BACK:
            state["telemetry_mode"] = "table"
        elif etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
            # D-pad is now the scroll axis, so CONFIRM carries the crash-frame
            # preview (full-screen swayimg of the frame, on crash cards).
            _preview_crash_frame(state, (state.get("_detail_row") or {}).get("crash_shot", ""))
        elif etype == EV_ABS and code == ABS_HAT0Y and val == -1:
            state["telemetry_detail_scroll"] = max(0, state.get("telemetry_detail_scroll", 0) - 1)
        elif etype == EV_ABS and code == ABS_HAT0Y and val == 1:
            _dmax = max(0, state.get("_detail_content_h", 5) - state.get("_detail_view_bot", 20))
            state["telemetry_detail_scroll"] = min(_dmax, state.get("telemetry_detail_scroll", 0) + 1)
        return "continue"
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        state["telemetry_mode"] = "detail"
        state["telemetry_detail_scroll"] = 0
        return "continue"
    if etype == EV_ABS and code == ABS_HAT0Y:
        if val == -1:
            state["telemetry_cursor"] = max(0, state.get("telemetry_cursor", 0) - 1)
        elif val == 1:
            state["telemetry_cursor"] = state.get("telemetry_cursor", 0) + 1
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
               "Manage Shaders", "Screenshot on L1"]
# Index of the Manage Shaders sub-screen entry.
_TOOLS_SHADERS_IDX = 2
# Index of the in-place toggle item (label gets ": <mode>" appended at draw).
_TOOLS_SCREENSHOT_IDX = 3


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


def _preview_crash_frame(state, shot):
    """Show a crash frame FULL-SCREEN via swayimg — the on-device way to see
    where a crash happened. curses can't render a PNG, and mako can't either on
    this ROCKNIX build (the gdk-pixbuf loader modules are stripped, so it has no
    image decoder — diagnosed live 2026-06-18). swayimg decodes PNG itself.
    Fire-and-forget + a 30s `timeout` BACKSTOP so a window can never pin open;
    the Popen handle is stashed in state['_preview_proc'] so the main loop can
    dismiss it on any button press (Pitstop owns the pad here — input_d is
    in-game-only and never runs in this context). Falls back to a mako TEXT
    toast (the SMB path) if swayimg is absent."""
    state["_preview_proc"] = None
    if not shot or shot == "-":
        return
    path = f"{ETK_ROOT}/screenshots/{shot}"
    if not os.path.isfile(path):
        _log(f"crash-frame preview: missing {path}")
        return
    if not shutil.which("swayimg"):
        # No image viewer on this build — point at the file over SMB instead.
        _Notifier().post("Crash frame", f"{shot}  (SMB: roms/etk/screenshots/)")
        return
    env = _tools_env()   # carries XDG_RUNTIME_DIR / WAYLAND_DISPLAY / SWAYSOCK
    try:
        # Launch with a known app_id; do NOT pass --fullscreen (that tiles in
        # sway). Instead fullscreen it via swaymsg once mapped — matching the
        # PKG-installer pattern — and re-assert foot fullscreen on close so
        # Pitstop's terminal isn't left split (the install path's fix).
        state["_preview_proc"] = subprocess.Popen(
            ["timeout", "30", "swayimg", "--class=" + _PREVIEW_APPID, path],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state["_preview_env"] = env
        _fullscreen_preview(env)
    except Exception as e:
        _log(f"swayimg preview failed: {e}")
        state["_preview_proc"] = None


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
    """Re-assert the Pitstop (foot) window as fullscreen. Launching RPCS3 OR
    the swayimg crash-frame preview knocks foot out of fullscreen in sway's
    tree, which would otherwise leave the curses UI tiled and clipped
    off-screen on return. The next main-loop _draw picks up the restored size
    via getmaxyx()."""
    try:
        _swaymsg(['[app_id="foot"]', 'fullscreen', 'enable'], env)
        time.sleep(0.3)
    except Exception as e:
        _log(f"restore pitstop window failed: {e}")


_PREVIEW_APPID = "etk-crash-preview"


def _fullscreen_preview(env):
    """Once the swayimg preview window maps, fullscreen it so it covers the
    foot terminal cleanly (no tiling split DURING the preview). Brief blocking
    poll mirroring _find_install_dialog; breaks as soon as the window appears."""
    for _ in range(12):
        try:
            tree = json.loads(_swaymsg(["-t", "get_tree"], env))
        except Exception:
            tree = None
        if tree is not None:
            stack = [tree]
            while stack:
                n = stack.pop()
                if n.get("app_id") == _PREVIEW_APPID:
                    _swaymsg(['[app_id="%s"]' % _PREVIEW_APPID,
                              'fullscreen', 'enable'], env)
                    return True
                for k in ("nodes", "floating_nodes"):
                    stack.extend(n.get(k, []))
        time.sleep(0.08)
    return False


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
        last_size = -1
        stable_since = None
        done = False
        # COMPLETION FIX (do NOT stop the instant EBOOT.BIN appears): RPCS3
        # keeps extracting the rest of the package after EBOOT.BIN lands, so
        # killing it then truncates the install — zero-byte / missing late
        # files — and the race resolves differently per card speed (the bug
        # that broke installs on the fast A2 card but not A1/UFS). Wait for a
        # REAL finish: RPCS3 self-exits on a successful install; as a fallback
        # (in case it doesn't), the game-folder size going stable for 20s means
        # extraction has drained. Only then kill.
        while time.time() - t1 < 600:
            time.sleep(3)
            try:
                fresh = [d for d in (set(os.listdir(RPCS3_GAME_DIR)) - before)
                         if "lock" not in d.lower()]
            except Exception:
                fresh = []
            if fresh and new_id is None:
                new_id = fresh[0]

            if proc.poll() is not None:
                # RPCS3 exited. With a game folder present, the install is done.
                if new_id:
                    done = True
                    break
                # exited without producing a folder -> failed install; give the
                # fs a short settle window, then stop (don't hang for 600s).
                if proc_dead_since is None:
                    proc_dead_since = time.time()
                elif time.time() - proc_dead_since > 15:
                    break
                continue

            # RPCS3 still running: fallback completion = the install folder has
            # stopped growing for 20s (extraction drained).
            if new_id:
                sz = _dir_size(os.path.join(RPCS3_GAME_DIR, new_id))
                if sz >= 0 and sz == last_size:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since > 20:
                        done = True
                        break
                else:
                    stable_since = None
                    last_size = sz
        if not new_id:
            return False, ["Install did not complete - no game folder",
                           "appeared. Staged files were kept so you",
                           "can retry."]
        if not done:
            _log("install: 600s cap reached without a clean completion "
                 "signal; install may be partial")

        _kill_rpcs3()    # ensure RPCS3 is gone (it should have self-exited)

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


# === TOOLS TAB — SHADER HYGIENE (Manage Shaders) ===
# Three escalating, NON-overlapping actions (the symlink makes them distinct):
#   sweep        -> vault_sweep.sh --apply : deletes only files older than the
#                   Mesa-rebuild boundary; keeps the live/fresh Turnip cache.
#   delete_vault -> rm the game's ENTIRE ETK Turnip shader cache (fresh+stale).
#   clear_cache  -> rm RPCS3's OWN runtime cache (PPU/SPU/ISO), separate from
#                   the Mesa vault; RPCS3 rebuilds it on next launch.
# The on-disk cache has no "shader layer" taxonomy (the 256 dirs are hash
# buckets, filenames are opaque hashes), so the only robust, actionable graph
# axis is fresh vs stale by Mesa-rebuild generation — exactly what sweep acts on.

# Row order on the Manage Shaders screen (index 0 is the scope toggle).
_SHADER_ROWS = ("scope", "sweep", "delete_vault", "clear_cache")


def _vault_game_ids():
    """GAMEIDs with a vault under VAULT_BASE (valid PSN-id shape + shaders dir)."""
    out = []
    try:
        for name in sorted(os.listdir(VAULT_BASE)):
            if re.match(r'^[A-Z]{4}[0-9]{5}$', name) and \
               os.path.isdir(os.path.join(VAULT_BASE, name, "shaders")):
                out.append(name)
    except Exception:
        pass
    return out


def _resolve_current_vault_id(game_ids):
    """Which game the 'Current' scope targets: the resolved TARGET_ID if it has
    a vault, else the last-played id, else TARGET_ID (may have no vault -> 0)."""
    if TARGET_ID in game_ids:
        return TARGET_ID
    try:
        with open(RECENT_ID_FILE) as f:
            rid = f.read().strip()
        if rid in game_ids:
            return rid
    except Exception:
        pass
    return TARGET_ID


def _du_kb(path):
    """BusyBox-safe dir size in KB (du -ks). 0 on absence/failure."""
    try:
        r = subprocess.run(["du", "-k", "-s", path],
                           capture_output=True, text=True, timeout=180)
        parts = r.stdout.split()
        return int(parts[0]) if r.returncode == 0 and parts else 0
    except Exception:
        return 0


def _sweep_porcelain():
    """vault_sweep.sh --all-games --porcelain (DRY-RUN, read-only). Returns
    ({gid: {stale_files, stale_kb, fresh_files, fresh_kb}}, boundary_ok, reason).
    reason distinguishes the failure modes that all collapse to boundary_ok=False:
      "ok"          -> engine ran, boundary present
      "no_boundary" -> vault/.last_mesa.hash absent (vault_sweep ABORT, exit 1)
      "engine"      -> vault_sweep.sh missing/unrunnable (exit 127 / FileNotFound)
                       — i.e. it was never deployed to the rig (install.sh push).
      "error"       -> any other non-zero / exception
    The split matters: "no boundary" and "engine missing" need different operator
    fixes (wait for next Mesa rebuild vs. re-run install.sh), and conflating them
    once hid a missing-deploy bug behind a "no boundary" label.
    fresh_* share the stale basis (shard-tree files), so fresh+stale is the
    honest shader byte total (excludes Mesa bookkeeping / du block overhead)."""
    per = {}
    try:
        r = subprocess.run(["bash", VAULT_SWEEP, "--all-games", "--porcelain"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            # bash exits 127 when it cannot find/read the script path.
            if r.returncode == 127 or not os.path.isfile(VAULT_SWEEP):
                _log(f"sweep porcelain: engine missing at {VAULT_SWEEP} (rc={r.returncode})")
                return per, False, "engine"
            # vault_sweep's own no-boundary ABORT is exit 1 + a [ABORT] line.
            if "[ABORT]" in (r.stdout + r.stderr) and "absent" in (r.stdout + r.stderr):
                return per, False, "no_boundary"
            _log(f"sweep porcelain: engine error rc={r.returncode}: {r.stderr.strip()[:200]}")
            return per, False, "error"
        for ln in r.stdout.splitlines():
            p = ln.split()
            if len(p) >= 6 and p[0] == "GAME":
                try:
                    per[p[1]] = {"stale_files": int(p[2]), "stale_kb": int(p[3]),
                                 "fresh_files": int(p[4]), "fresh_kb": int(p[5])}
                except ValueError:
                    pass
        return per, True, "ok"
    except FileNotFoundError as e:
        _log(f"sweep porcelain: engine missing ({e})")
        return per, False, "engine"
    except Exception as e:
        _log(f"sweep porcelain failed: {e}")
        return per, False, "error"


def _scan_vault_hygiene(state):
    """Build the Manage Shaders model: per-game total/fresh/stale sizes + the
    RPCS3 runtime-cache sizes. Scope-agnostic (reclaim derived per-scope at
    draw). Slow-ish (du + the dry-run stat pass) so callers draw a busy frame
    first. Cached in state['shaders_model'] until an action invalidates it."""
    game_ids = _vault_game_ids()
    current = _resolve_current_vault_id(game_ids)
    stale_map, boundary_ok, sweep_reason = _sweep_porcelain()

    games = []
    for gid in game_ids:
        # disk_kb = true on-disk footprint (what Delete Vault frees). stale_kb /
        # fresh_kb = shard-tree shader bytes by generation (what the graph splits
        # and Sweep frees). disk_kb >= stale_kb+fresh_kb; the gap is Mesa
        # bookkeeping + block overhead, shown as a dim tail on the bar.
        disk_kb = _du_kb(os.path.join(VAULT_BASE, gid, "shaders"))
        st = stale_map.get(gid, {})
        games.append({
            "id": gid,
            "disk_kb": disk_kb,
            "stale_kb": st.get("stale_kb", 0),
            "fresh_kb": st.get("fresh_kb", 0),
            "stale_files": st.get("stale_files", 0),
        })
    games.sort(key=lambda g: g["disk_kb"], reverse=True)

    model = {
        "games": games,
        "by_id": {g["id"]: g for g in games},
        "current": current,
        "boundary_ok": boundary_ok,
        "sweep_reason": sweep_reason,
        "rpcs3_all_mb": _du_kb(RPCS3_RUNTIME_CACHE) // 1024,
        "rpcs3_cur_mb": _du_kb(os.path.join(RPCS3_RUNTIME_CACHE, current)) // 1024,
        "rpcs3_running": _rpcs3_running(),
    }
    state["shaders_model"] = model
    return model


def _shader_reclaim(model, scope_all):
    """Reclaim MB for the three actions under the chosen scope. Sweep frees the
    stale shard bytes; Delete Vault frees the whole on-disk footprint (disk_kb)."""
    games = model["games"]
    if scope_all:
        return {"sweep_mb": sum(g["stale_kb"] for g in games) // 1024,
                "vault_mb": sum(g["disk_kb"] for g in games) // 1024,
                "cache_mb": model["rpcs3_all_mb"]}
    g = model["by_id"].get(model["current"]) or {"stale_kb": 0, "disk_kb": 0}
    return {"sweep_mb": g["stale_kb"] // 1024, "vault_mb": g["disk_kb"] // 1024,
            "cache_mb": model["rpcs3_cur_mb"]}


def _draw_shader_screen(stdscr, state, model, y, h, w):
    """Manage Shaders: fresh/stale-per-game graph + scope toggle + 3 actions."""
    def put(row, col, text, attr=curses.A_NORMAL):
        try:
            stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    scope_all = state.get("shaders_scope_all", False)
    cur = model["current"]
    rec = _shader_reclaim(model, scope_all)
    boundary_ok = model["boundary_ok"]
    sweep_reason = model.get("sweep_reason", "ok")
    # Short tag for why Sweep is unavailable — each needs a different operator
    # fix, so don't collapse them all into "no boundary".
    _SWEEP_LEGEND = {
        "no_boundary": "(stale unknown — no boundary)",
        "engine":      "(sweep engine missing — run install.sh)",
        "error":       "(sweep unavailable — see logs)",
    }
    _SWEEP_NA = {
        "no_boundary": "n/a (no boundary)",
        "engine":      "n/a (engine missing — reinstall)",
        "error":       "n/a (unavailable)",
    }
    running = model["rpcs3_running"]

    put(y, 2, "MANAGE SHADERS", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1

    # --- Graph: per-game bar; length ∝ on-disk footprint, segmented
    # green=fresh / red=stale / dim=overhead (Mesa bookkeeping + block slack).
    games = [g for g in model["games"] if g["disk_kb"] > 0]
    if not games:
        put(y, 4, "Vault is empty — nothing to clean.", curses.A_DIM); y += 1
    else:
        legend = "vault by game  " + (
            "(green=fresh  red=stale)" if boundary_ok
            else _SWEEP_LEGEND.get(sweep_reason, "(stale unknown — no boundary)"))
        put(y, 4, legend, curses.A_DIM); y += 1
        max_kb = max(g["disk_kb"] for g in games) or 1
        BARW = 18
        # Leave room below for the 4 rows + hints (~8 lines).
        cap = max(1, (h - y) - 8)
        shown = games[:cap]
        for g in shown:
            marker = "›" if g["id"] == cur else " "
            barw = max(1, int(round(BARW * g["disk_kb"] / max_kb)))
            col = 4
            put(y, col, f"{marker}{g['id']:<9}"); col += 11
            if boundary_ok:
                fw = int(round(barw * g["fresh_kb"] / g["disk_kb"]))
                sw = int(round(barw * g["stale_kb"] / g["disk_kb"]))
                if fw + sw > barw:
                    sw = barw - fw
                ow = max(0, barw - fw - sw)            # overhead tail
                put(y, col, "#" * fw, curses.color_pair(PAIR_CLEAN)); col += fw
                put(y, col, "#" * sw, curses.color_pair(PAIR_CRASH)); col += sw
                put(y, col, "#" * ow, curses.A_DIM); col += ow
            else:
                put(y, col, "#" * barw, curses.color_pair(PAIR_CONFIG)); col += barw
            put(y, col, "." * (BARW - barw), curses.A_DIM); col += (BARW - barw) + 1
            put(y, col, f"{g['disk_kb'] // 1024}MB", curses.A_DIM)
            y += 1
        if len(games) > len(shown):
            rest = games[len(shown):]
            put(y, 4, f" +{len(rest)} more game(s)  "
                      f"{sum(g['disk_kb'] for g in rest) // 1024}MB", curses.A_DIM)
            y += 1
    y += 1

    # --- Action rows (cursor-selected). Row 0 is the scope toggle.
    cursor = state.get("tools_cursor", 0)
    scope_txt = "ALL GAMES" if scope_all else f"Current ({cur})"
    sweep_txt = (f"Sweep stale shaders       ~{rec['sweep_mb']} MB" if boundary_ok
                 else "Sweep stale shaders       " + _SWEEP_NA.get(sweep_reason, "n/a (no boundary)"))
    rows = [
        f"Scope: {scope_txt}    (CONFIRM toggles)",
        sweep_txt,
        f"Delete vault              ~{rec['vault_mb']} MB",
        f"Clear RPCS3 cache         ~{rec['cache_mb']} MB",
    ]
    for i, label in enumerate(rows):
        sel = (i == cursor)
        put(y, 4, "> " if sel else "  ",
            curses.color_pair(1) if sel else curses.A_NORMAL)
        put(y, 6, label, curses.A_REVERSE if sel else curses.A_NORMAL)
        y += 1
    y += 1
    if running:
        put(y, 4, "RPCS3 is running — deletes are blocked; numbers are live.",
            curses.color_pair(PAIR_RECOV)); y += 1
    put(y, 4, "CONFIRM: select    BACK: menu",
        curses.color_pair(1) | curses.A_BOLD)


def _draw_shader_confirm(stdscr, state, y, h, w):
    """Per-action confirm for a destructive shader op."""
    def put(row, col, text, attr=curses.A_NORMAL):
        try:
            stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    model = state.get("shaders_model") or {}
    op = state.get("shaders_pending")
    scope_all = state.get("shaders_scope_all", False)
    rec = _shader_reclaim(model, scope_all) if model else \
        {"sweep_mb": 0, "vault_mb": 0, "cache_mb": 0}
    scope_txt = "ALL GAMES" if scope_all else model.get("current", "?")

    meta = {
        "sweep": ("SWEEP STALE SHADERS", rec["sweep_mb"], [
            "Deletes only shaders orphaned by the last Mesa rebuild.",
            "Keeps the live/fresh cache — replay re-banks anything lost."]),
        "delete_vault": ("DELETE VAULT", rec["vault_mb"], [
            f"Deletes the ENTIRE Turnip shader cache for {scope_txt}.",
            "Next launch recompiles from scratch — slow first laps."]),
        "clear_cache": ("CLEAR RPCS3 CACHE", rec["cache_mb"], [
            "Clears RPCS3's own PPU/SPU/ISO runtime cache.",
            "RPCS3 rebuilds it automatically on next launch."]),
    }
    title, mb, notes = meta.get(op, ("CONFIRM", 0, []))

    put(y, 2, title + " - CONFIRM", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
    put(y, 4, f"Scope   : {scope_txt}", curses.A_BOLD); y += 1
    put(y, 4, f"Reclaim : ~{mb} MB"); y += 2
    for n in notes:
        put(y, 4, n); y += 1
    y += 1
    put(y, 4, "B: Confirm     A: Cancel",
        curses.color_pair(1) | curses.A_BOLD)


def _run_shader_op(op, scope_all, gid, notify):
    """Blocking shader-hygiene op. Returns (ok, [result lines]). Refuses while
    RPCS3 runs (Mesa cache open / mid-flux mtimes)."""
    if _rpcs3_running():
        return False, ["RPCS3 is running - quit the game first, then retry."]

    if op == "sweep":
        cmd = ["bash", VAULT_SWEEP, "--apply", "--porcelain"]
        cmd += ["--all-games"] if scope_all else ["--game", gid]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except Exception as e:
            return False, [f"sweep failed: {e}"]
        if r.returncode != 0:
            return False, ["vault_sweep error:",
                           (r.stderr.strip() or "see etk_pitstop.log")[:200]]
        files = kb = 0
        for ln in r.stdout.splitlines():
            p = ln.split()
            if len(p) >= 3 and p[0] == "TOTAL":   # TOTAL <stale_f> <stale_kb> ...
                try:
                    files, kb = int(p[1]), int(p[2])
                except ValueError:
                    pass
        mb = kb // 1024
        notify.post("Shaders swept",
                    f"{files} stale files removed, {mb} MB freed.", timeout=12000)
        return True, [
            f"SWEPT stale shaders ({'all games' if scope_all else gid})",
            f"  removed : {files} files, {mb} MB freed",
            "  kept    : the live/fresh Turnip cache",
            "",
            "Stale shaders re-accrue after each Mesa rebuild (nightly).",
            "Re-run this after a Rocknix update."]

    if op == "delete_vault":
        ids = _vault_game_ids() if scope_all else [gid]
        freed = removed = 0
        for g in ids:
            d = os.path.join(VAULT_BASE, g, "shaders")
            if os.path.isdir(d):
                freed += _du_kb(d)
                try:
                    shutil.rmtree(d)
                    os.makedirs(d, exist_ok=True)  # keep mesa symlink target valid
                    removed += 1
                except Exception as e:
                    _log(f"delete_vault rmtree {d}: {e}")
        mb = freed // 1024
        notify.post("Vault deleted",
                    f"{mb} MB freed ({removed} game(s)). Shaders recompile next launch.",
                    timeout=15000)
        return True, [
            f"DELETED vault ({'all games' if scope_all else gid})",
            f"  freed   : {mb} MB across {removed} game(s)",
            "  note    : next launch recompiles shaders from scratch",
            "",
            "Expect slower first laps until the cache rebuilds."]

    if op == "clear_cache":
        if scope_all:
            targets = [RPCS3_RUNTIME_CACHE, RPCS3_HDD1_CACHE]
        else:
            targets = [os.path.join(RPCS3_RUNTIME_CACHE, gid),
                       os.path.join(RPCS3_HDD1_CACHE, f"{gid}_{gid}")]
        freed = removed = 0
        for t in targets:
            if os.path.isdir(t):
                freed += _du_kb(t)
                try:
                    shutil.rmtree(t)
                    # Recreate the two cache ROOTS (all-games) so RPCS3 finds them.
                    if scope_all:
                        os.makedirs(t, exist_ok=True)
                    removed += 1
                except Exception as e:
                    _log(f"clear_cache rmtree {t}: {e}")
        mb = freed // 1024
        notify.post("RPCS3 cache cleared",
                    f"{mb} MB freed. RPCS3 rebuilds it next launch.", timeout=12000)
        return True, [
            f"CLEARED RPCS3 runtime cache ({'all games' if scope_all else gid})",
            f"  freed   : {mb} MB, {removed} dir(s)",
            "  note    : RPCS3 rebuilds PPU/SPU/ISO cache automatically",
            "",
            "This does NOT touch your ETK Turnip shader vault."]

    return False, [f"unknown shader op: {op}"]


# === TOOLS TAB — DRAW ===

# ROCKNIX-style ASCII throbber. Plain ASCII (hyphen, not em-dash) so it stays
# single-width in any TTY font the rig's curses falls back to.
_SPINNER_FRAMES = "|/-\\"


def _tools_busy_msg(stdscr, msg, spin=None):
    """Generic 'working' frame for a blocking TOOLS op that doesn't hand the
    screen to RPCS3 (shader scans/cleans). When `spin` is a single glyph it is
    drawn centered on the line below the message as a throbber frame."""
    try:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        _draw_title_bar(stdscr, w)
        _draw_tab_strip(stdscr, CURRENT_TAB_TOOLS, w)
        stdscr.addstr(h // 2, max(2, (w - len(msg)) // 2), msg[:w - 4],
                      curses.A_BOLD)
        if spin:
            stdscr.addstr(h // 2 + 2, max(2, (w - 1) // 2), spin[:1],
                          curses.A_BOLD)
        stdscr.refresh()
    except curses.error:
        pass


def _run_with_spinner(stdscr, msg, work, *args, draw=_tools_busy_msg, **kwargs):
    """Run a blocking, curses-free `work(*args, **kwargs)` on a background
    thread while animating the ROCKNIX throbber under `msg` on the main thread.
    `draw(stdscr, msg, spin)` paints each frame — defaults to the TOOLS busy
    frame; pass `draw=_paddock_busy` for the PADDOCK tab so the right tab strip
    shows. Returns work()'s value (None if it raised — logged). The worker MUST
    NOT touch stdscr: curses is single-threaded, so only this (main) thread draws."""
    box = {}

    def _runner():
        try:
            box["val"] = work(*args, **kwargs)
        except Exception as e:                       # noqa: BLE001 — surface, don't crash UI
            box["err"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    i = 0
    while t.is_alive():
        draw(stdscr, msg, _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)])
        i += 1
        curses.napms(120)                            # ~8 fps; cheap, smooth enough
    t.join()
    if "err" in box:
        _log(f"spinner work failed: {box['err']}")
    return box.get("val")


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

    elif mode == "shaders":
        model = state.get("shaders_model")
        if not model:
            put(y, 4, "Scanning shader vault…", curses.A_DIM)
        else:
            _draw_shader_screen(stdscr, state, model, y, h, w)

    elif mode == "shaders_confirm":
        _draw_shader_confirm(stdscr, state, y, h, w)

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
    elif mode == "shaders":
        state["tools_cursor"] = (state.get("tools_cursor", 0) + delta) % len(_SHADER_ROWS)


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
        elif state.get("tools_cursor", 0) == _TOOLS_SHADERS_IDX:   # Manage Shaders
            state["tools_mode"] = "shaders"
            state["tools_cursor"] = 0
            state["shaders_scope_all"] = False
            state["shaders_model"] = None
            state["shaders_scan_request"] = True     # main loop scans w/ busy frame
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

    elif mode == "shaders":
        idx = state.get("tools_cursor", 0)
        if idx == 0:                                  # toggle scope (no rescan;
            state["shaders_scope_all"] = not state.get("shaders_scope_all", False)
        else:                                         # model is scope-agnostic)
            op = _SHADER_ROWS[idx]
            model = state.get("shaders_model") or {}
            if op == "sweep" and not model.get("boundary_ok"):
                reason = model.get("sweep_reason", "no_boundary")
                if reason == "engine":
                    msg = [
                        "Sweep engine missing on the rig.",
                        "tools/vault_sweep.sh is not deployed —",
                        "re-run install.sh from the host to push it."]
                elif reason == "error":
                    msg = [
                        "Sweep engine returned an error.",
                        "Check the ETK Pitstop log on the rig,",
                        "then re-run install.sh if needed."]
                else:
                    msg = [
                        "No Mesa-rebuild boundary (vault/.last_mesa.hash).",
                        "Run install.sh once from the host to seed it,",
                        "then sweep after the next Rocknix nightly."]
                state["tools_result"] = (False, msg)
                state["tools_mode"] = "result"
            elif model.get("rpcs3_running"):
                state["tools_result"] = (False, [
                    "RPCS3 is running.",
                    "Quit the game first, then retry."])
                state["tools_mode"] = "result"
            else:
                state["shaders_pending"] = op
                state["tools_mode"] = "shaders_confirm"

    elif mode == "shaders_confirm":
        state["tools_action"] = ("shader", state.get("shaders_pending"),
                                 state.get("shaders_scope_all", False),
                                 (state.get("shaders_model") or {}).get("current"))

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
    elif mode == "shaders_confirm":
        state["tools_mode"] = "shaders"
    elif mode == "shaders":
        state["tools_mode"] = "menu"
        state["tools_cursor"] = _TOOLS_SHADERS_IDX
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


# === DRIVER TAB (Turnip env-var dials) ===
#
# Surfaces the Mesa/Turnip knobs that the Stage IV audit named — the untested
# big lever TU_AUTOTUNE_ALGO plus the TU_DEBUG isolation ladder — as gamepad
# dials. They inject via profile.d (TURNIP_PROFILE_D), effective next launch,
# and stamp active_tune.txt so the ledger can attribute genuine-play sessions
# to their dial set. Discipline lives in the UI copy: one knob per soak.

_AUTOTUNE_VALUES = ("default", "bandwidth", "profiled", "profiled_imm",
                    "prefer_sysmem", "prefer_gmem")
_TU_DEBUG_PRIMARY = ("nolrz", "noubwc", "sysmem", "gmem")        # isolation ladder
_TU_DEBUG_ADVANCED = ("nobin", "forcebin", "nocb",
                      "noconcurrentresolves", "syncdraw", "flushall",
                      # ETK LSD gears — lighter-than-syncdraw barriers; REQUIRE
                      # the fork driver (.so). Inert on stock Turnip.
                      # Patch #2: sdgate = depth-clean gated to depth-writing
                      # draws (the scalpel); sdclean = depth-clean alone.
                      "sddepth", "sdmem", "sdme", "sdclean", "sdgate",
                      # Stage IV §5 resolution-alignment investigation: dimlog
                      # logs the GMEM render dims + ragged remainder at tiling
                      # setup (diagnostic, inert; REQUIRES the lsd-dim fork .so).
                      "dimlog")
_TU_DEBUG_KNOWN = set(_TU_DEBUG_PRIMARY) | set(_TU_DEBUG_ADVANCED)


def _driver_default_model():
    return {"autotune_idx": 0, "tu_debug": set(),
            "show_advanced": False, "applied_sig": "default",
            # BUILD selector: which .so binds over stock (reboot-gated).
            "builds": [STOCK_BUILD], "build_idx": 0,
            "selected_build": STOCK_BUILD,   # persisted pending pick
            "loaded_build": STOCK_BUILD,     # live (what's bound now)
            "reboot_arm": False}             # two-press guard on REBOOT


def _driver_sig(model):
    """Compact, stable signature for active_tune.txt + the ledger tune_tag.
    'default' when nothing is set; flags sorted so the tag is order-stable."""
    parts = []
    av = _AUTOTUNE_VALUES[model["autotune_idx"]]
    if av != "default":
        parts.append("autotune=" + av)
    flags = sorted(model["tu_debug"])
    if flags:
        parts.append("tu_debug=" + ",".join(flags))
    return ";".join(parts) if parts else "default"


def _driver_load(state):
    """Read the live dial state from the rig's profile.d injection into
    state['driver_model']. Tolerant of a missing file (= all default)."""
    model = _driver_default_model()
    try:
        with open(TURNIP_PROFILE_D) as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("export TU_AUTOTUNE_ALGO="):
                    v = ln.split("=", 1)[1].strip().strip('"')
                    if v in _AUTOTUNE_VALUES:
                        model["autotune_idx"] = _AUTOTUNE_VALUES.index(v)
                elif ln.startswith("export TU_DEBUG="):
                    v = ln.split("=", 1)[1].strip().strip('"')
                    model["tu_debug"] = {f for f in v.split(",")
                                         if f in _TU_DEBUG_KNOWN}
    except OSError:
        pass
    if model["tu_debug"] & set(_TU_DEBUG_ADVANCED):
        model["show_advanced"] = True
    model["applied_sig"] = _driver_sig(model)
    # BUILD selector state: catalog + the persisted pick + the live bind.
    builds = _list_builds()
    selected = _read_build_pointer(TURNIP_SELECTED_FILE) or STOCK_BUILD
    loaded = _read_build_pointer(TURNIP_LOADED_FILE) or STOCK_BUILD
    if selected not in builds:           # pick names a build no longer staged
        selected = STOCK_BUILD
    model["builds"] = builds
    model["selected_build"] = selected
    model["loaded_build"] = loaded
    model["build_idx"] = builds.index(selected)
    model["reboot_arm"] = False
    state["driver_model"] = model
    state.setdefault("driver_cursor", 0)
    state.setdefault("driver_scroll", 0)
    state["driver_notice"] = None


def _driver_apply(model):
    """Write the profile.d injection + the active_tune tag. All-default removes
    the injection so Turnip reverts to its built-in autotune. Effective on the
    NEXT game launch. Returns (ok, lines)."""
    av = _AUTOTUNE_VALUES[model["autotune_idx"]]
    flags = sorted(model["tu_debug"])
    sig = _driver_sig(model)
    try:
        if sig == "default":
            try:
                os.remove(TURNIP_PROFILE_D)
            except OSError:
                pass
        else:
            out = ["# ETK Turnip dials — written by Pitstop DRIVER tab.",
                   "# Sourced by /etc/profile at game launch; survives reboot.",
                   "# tune_tag: " + sig]
            if av != "default":
                out.append("export TU_AUTOTUNE_ALGO=" + av)
            if flags:
                out.append("export TU_DEBUG=" + ",".join(flags))
            os.makedirs(os.path.dirname(TURNIP_PROFILE_D), exist_ok=True)
            with open(TURNIP_PROFILE_D, "w") as f:
                f.write("\n".join(out) + "\n")
        os.makedirs(os.path.dirname(ACTIVE_TUNE_FILE), exist_ok=True)
        with open(ACTIVE_TUNE_FILE, "w") as f:
            f.write(sig + "\n")
        model["applied_sig"] = sig
        return True, [sig]
    except OSError as e:
        _log(f"driver apply failed: {e}")
        return False, [str(e)[:60]]


def _build_pending(model):
    """The build the cursor currently points at (pending pick)."""
    return model["builds"][model["build_idx"] % len(model["builds"])]


def _build_dirty(model):
    """Pending pick differs from what's persisted in `selected`."""
    return _build_pending(model) != model["selected_build"]


def _build_reboot_pending(model):
    """Persisted pick differs from what's actually bound — a reboot will
    change the live driver. (Distinct from _build_dirty, which is unsaved.)"""
    return model["selected_build"] != model["loaded_build"]


def _build_apply(model):
    """Persist the BUILD pick to /storage/turnip/selected. Takes effect on the
    NEXT cold boot (etk-turnip-bind.sh rebinds at boot — a live bind-mount can't
    hot-swap an in-use driver). 'stock' is written verbatim (= unbind). Returns
    (ok, msg)."""
    pick = _build_pending(model)
    try:
        os.makedirs(TURNIP_DIR, exist_ok=True)
        with open(TURNIP_SELECTED_FILE, "w") as f:
            f.write(pick + "\n")
        model["selected_build"] = pick
        return True, pick
    except OSError as e:
        _log(f"build apply failed: {e}")
        return False, str(e)[:60]


def _reboot_rig():
    """Cold-boot the rig so etk-turnip-bind.sh binds the freshly-selected build.
    The only honest validation of a driver swap (always-reboot gate)."""
    try:
        subprocess.Popen(["sh", "-c", "sleep 1; systemctl reboot"])
        return True
    except Exception as e:
        _log(f"reboot failed: {e}")
        return False


def _driver_rows(model):
    """Flat selectable-row list, rebuilt each frame so draw + input agree.
    Each entry = (kind, payload). The BUILD selector leads (it's the bigger
    lever — which driver), then the env-var dials that tune it."""
    rows = [("build", None), ("build_apply", None), ("reboot", None)]
    rows += [("autotune", None)]
    rows += [("flag", f) for f in _TU_DEBUG_PRIMARY]
    rows.append(("advanced", None))
    if model["show_advanced"]:
        rows += [("flag", f) for f in _TU_DEBUG_ADVANCED]
    rows += [("apply", None), ("reset", None)]
    return rows


def draw_driver(stdscr, state):
    """DRIVER tab — Turnip env-var dials. Global (not per-game): injected via
    profile.d, effective next launch. No ETK_NO_TARGET gate (driver knobs are
    title-independent)."""
    if state.get("driver_model") is None:
        _driver_load(state)
    model = state["driver_model"]
    h, w = stdscr.getmaxyx()
    rows = _driver_rows(model)
    cursor = state.get("driver_cursor", 0) % len(rows)
    state["driver_cursor"] = cursor
    sig = _driver_sig(model)
    applied = model.get("applied_sig", "default")
    dirty = sig != applied

    def put(r, c, t, a=curses.A_NORMAL):
        try:
            stdscr.addstr(r, c, t[:max(0, w - c - 1)], a)
        except curses.error:
            pass

    y = 5
    put(y, 2, "TURNIP DRIVER", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
    put(y, 4, "BUILD = which .so loads (reboot to apply). Dials tune it (next launch).",
        curses.A_DIM); y += 1
    put(y, 4, "One change per soak: swap one thing, drive real laps, read the ledger.",
        curses.A_DIM); y += 2

    cap = max(1, (h - y) - 7)
    # Cursor-follows-scroll window (same pattern as draw_telemetry). Without it
    # a row list taller than the pane clips the advanced flags + APPLY out of
    # reach — the 2026-06-18 DRIVER-tab scroll regression. cursor is an absolute
    # index into rows; keep it inside [scroll, scroll+cap).
    scroll = state.get("driver_scroll", 0)
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + cap:
        scroll = cursor - cap + 1
    scroll = max(0, min(scroll, max(0, len(rows) - cap)))
    state["driver_scroll"] = scroll
    if scroll > 0:
        put(y - 1, w - 10, "(more ^)", curses.A_DIM)
    for vis_i, (kind, payload) in enumerate(rows[scroll:scroll + cap]):
        i = scroll + vis_i
        sel = (i == cursor)
        base = curses.A_REVERSE if sel else curses.A_NORMAL
        put(y, 4, "> " if sel else "  ",
            curses.color_pair(1) if sel else curses.A_NORMAL)
        if kind == "build":
            pend = _build_pending(model)
            mark = " *" if _build_dirty(model) else ""
            put(y, 6, f"DRIVER BUILD   < {_build_label(pend)} >{mark}", base)
        elif kind == "build_apply":
            bd = _build_dirty(model)
            put(y, 6, "Apply build" + ("   *unsaved pick*" if bd else
                      ("   (reboot to load)" if _build_reboot_pending(model)
                       else "   (no change)")),
                base | curses.A_BOLD)
        elif kind == "reboot":
            armed = model.get("reboot_arm")
            put(y, 6, "REBOOT to load build" + ("   CONFIRM?" if armed else ""),
                base | (curses.A_BOLD if armed else curses.A_DIM))
        elif kind == "autotune":
            put(y, 6, f"TU_AUTOTUNE_ALGO   < {_AUTOTUNE_VALUES[model['autotune_idx']]} >", base)
        elif kind == "flag":
            on = payload in model["tu_debug"]
            tail = curses.color_pair(PAIR_CLEAN) if (on and not sel) else base
            put(y, 6, f"TU_DEBUG  {'[x]' if on else '[ ]'} {payload}", tail)
        elif kind == "advanced":
            put(y, 6, f"Advanced flags  {'v' if model['show_advanced'] else '>'}",
                base | curses.A_DIM)
        elif kind == "apply":
            put(y, 6, "APPLY" + ("   *unsaved changes*" if dirty else "   (no change)"),
                base | curses.A_BOLD)
        elif kind == "reset":
            put(y, 6, "Reset to default", base)
        y += 1
    if scroll + cap < len(rows):
        put(y, w - 10, "(more v)", curses.A_DIM)
    y += 1
    loaded_lbl = _build_label(model["loaded_build"])
    sel_lbl = _build_label(model["selected_build"])
    if _build_reboot_pending(model):
        put(y, 4, f"Build   : {loaded_lbl} loaded -> {sel_lbl} on reboot",
            curses.color_pair(PAIR_RECOV) | curses.A_BOLD); y += 1
    else:
        put(y, 4, f"Build   : {loaded_lbl} loaded", curses.A_DIM); y += 1
    put(y, 4, "Pending : " + sig, curses.A_BOLD); y += 1
    put(y, 4, "Applied : " + applied, curses.A_DIM); y += 1
    notice = state.get("driver_notice")
    if notice:
        ok, msg = notice
        put(y, 4, msg[:w - 6],
            curses.color_pair(PAIR_CLEAN if ok else PAIR_CRASH) | curses.A_BOLD)
    elif _build_dirty(model):
        put(y, 4, "Apply build to save the pick, then REBOOT to load it.",
            curses.color_pair(PAIR_RECOV))
    elif dirty:
        put(y, 4, "APPLY to write — takes effect next game launch.",
            curses.color_pair(PAIR_RECOV))


def _driver_move(state, delta):
    rows = _driver_rows(state["driver_model"])
    state["driver_cursor"] = (state.get("driver_cursor", 0) + delta) % len(rows)
    state["driver_model"]["reboot_arm"] = False  # leaving the row disarms it
    state["driver_notice"] = None


def _driver_adjust(state, delta):
    """Left/Right on the BUILD row cycles the catalog; on the autotune row
    cycles its value; on a flag row toggles it (direction-agnostic)."""
    model = state["driver_model"]
    rows = _driver_rows(model)
    kind, payload = rows[state.get("driver_cursor", 0) % len(rows)]
    if kind == "build":
        model["build_idx"] = (model["build_idx"] + delta) % len(model["builds"])
        model["reboot_arm"] = False
        state["driver_notice"] = None
    elif kind == "autotune":
        model["autotune_idx"] = (model["autotune_idx"] + delta) % len(_AUTOTUNE_VALUES)
        state["driver_notice"] = None
    elif kind == "flag":
        _driver_toggle_flag(state, payload)


def _driver_toggle_flag(state, flag):
    s = state["driver_model"]["tu_debug"]
    s.discard(flag) if flag in s else s.add(flag)
    state["driver_notice"] = None


def _driver_select(state):
    """CONFIRM on the focused row."""
    model = state["driver_model"]
    rows = _driver_rows(model)
    kind, payload = rows[state.get("driver_cursor", 0) % len(rows)]
    if kind != "reboot":
        model["reboot_arm"] = False
    if kind == "build":
        # CONFIRM also cycles the catalog (parity with the dpad), so a one-build
        # catalog still reads as "stock <-> build" without needing left/right.
        model["build_idx"] = (model["build_idx"] + 1) % len(model["builds"])
        state["driver_notice"] = None
    elif kind == "build_apply":
        ok, msg = _build_apply(model)
        if ok:
            note = (f"BUILD SET: {_build_label(msg)} — REBOOT to load"
                    if _build_reboot_pending(model)
                    else f"BUILD SET: {_build_label(msg)} (already loaded)")
            state["driver_notice"] = (True, note)
        else:
            state["driver_notice"] = (False, "BUILD SET FAILED: " + msg)
    elif kind == "reboot":
        if not model.get("reboot_arm"):
            model["reboot_arm"] = True
            state["driver_notice"] = (True, "Press CONFIRM again to REBOOT now.")
        else:
            model["reboot_arm"] = False
            ok = _reboot_rig()
            state["driver_notice"] = (ok, "REBOOTING…" if ok else "REBOOT FAILED")
    elif kind == "flag":
        _driver_toggle_flag(state, payload)
    elif kind == "advanced":
        model["show_advanced"] = not model["show_advanced"]
        state["driver_notice"] = None
    elif kind == "reset":
        model["autotune_idx"] = 0
        model["tu_debug"] = set()
        state["driver_notice"] = None
    elif kind == "apply":
        ok, info = _driver_apply(model)
        state["driver_notice"] = (ok, ("APPLIED: " + info[0] + " — effective next launch"
                                       if ok else "APPLY FAILED: " + info[0]))
    return "continue"


def handle_driver_pad(state, etype, code, val):
    """Gamepad input for DRIVER. L1/R1 intercepted upstream for tabs."""
    if state.get("driver_model") is None:
        _driver_load(state)
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _driver_select(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_ABS and code == ABS_HAT0Y and val != 0:
        _driver_move(state, 1 if val == 1 else -1)
    elif etype == EV_ABS and code == ABS_HAT0X and val != 0:
        _driver_adjust(state, 1 if val == 1 else -1)
    return "continue"


def handle_driver_kb(state, ch):
    """Keyboard input for DRIVER (dev/desktop parity with the pad)."""
    if state.get("driver_model") is None:
        _driver_load(state)
    if ch in (ord('q'), ord('Q')):
        return "quit"
    if ch == curses.KEY_UP:
        _driver_move(state, -1)
    elif ch == curses.KEY_DOWN:
        _driver_move(state, 1)
    elif ch == curses.KEY_LEFT:
        _driver_adjust(state, -1)
    elif ch == curses.KEY_RIGHT:
        _driver_adjust(state, 1)
    elif ch in (ord('\n'), ord(' ')):
        return _driver_select(state)
    return "continue"


# === POWER TAB (CPU/GPU governors + clock pinning) ===
#
# Runtime sysfs power knobs — NO OC (the OPP table caps every pulldown at rated
# max). Schema in power_profiles.json: knobs (OPP-bounded options) + named presets
# (RACE/BALANCED/COOL). Picking a preset sets every knob; overriding a knob flips
# the preset to CUSTOM (ECU-map behaviour). APPLY writes the resolved path<TAB>value
# set to POWER_PROFILE_FILE (etk-power.service re-applies at boot — govs/freqs reset
# on reboot) AND writes them live now. Stamps pwr= into the ledger. thermal_d's PIT
# cooldown still overrides on overheat, then returns to this profile.

_POWER_SCHEMA_CACHE = None


def _power_schema():
    global _POWER_SCHEMA_CACHE
    if _POWER_SCHEMA_CACHE is None:
        try:
            with open(POWER_PROFILES_JSON) as f:
                _POWER_SCHEMA_CACHE = json.load(f)
        except Exception as e:
            _log(f"power schema load failed: {e}")
            _POWER_SCHEMA_CACHE = {"knobs": [], "presets": []}
    return _POWER_SCHEMA_CACHE


def _power_knob(schema, kid):
    for k in schema.get("knobs", []):
        if k["id"] == kid:
            return k
    return None


def _power_opt_label(knob, value):
    for o in (knob or {}).get("options", []):
        if o["v"] == value:
            return o["l"]
    return value


def _power_match_preset(schema, values):
    """The preset id whose values all match the current knobs, else 'CUSTOM'."""
    for p in schema.get("presets", []):
        if all(values.get(k) == v for k, v in p["values"].items()):
            return p["id"]
    return "CUSTOM"


def _power_read_saved():
    """(pwr_tag, {knob_id: value}) from POWER_PROFILE_FILE — the persisted state.
    Knob values live in '# knob <id> <value>' records so the boot applier (which
    only reads the path<TAB>value lines) and the UI share one source of truth."""
    tag = None
    vals = {}
    try:
        with open(POWER_PROFILE_FILE) as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if ln.startswith("# pwr="):
                    tag = ln.split("=", 1)[1].strip()
                elif ln.startswith("# knob "):
                    parts = ln.split()
                    if len(parts) >= 4:
                        vals[parts[2]] = parts[3]
    except OSError:
        pass
    return tag, vals


def _power_preset_values(schema, preset_id):
    for p in schema.get("presets", []):
        if p["id"] == preset_id:
            return dict(p["values"])
    return None


def _power_load(state):
    schema = _power_schema()
    saved_tag, saved_vals = _power_read_saved()
    vals = _power_preset_values(schema, "BALANCED") or {
        k["id"]: (k["options"][0]["v"] if k.get("options") else "")
        for k in schema.get("knobs", [])}
    vals.update({k: v for k, v in saved_vals.items() if _power_knob(schema, k)})
    state["power_model"] = {
        "schema": schema,
        "values": vals,
        "saved_values": dict(vals) if saved_tag else {},
        "applied_tag": saved_tag or "none",
    }
    state.setdefault("power_cursor", 0)
    state.setdefault("power_scroll", 0)
    state["power_notice"] = None


def _power_rows(model):
    rows = [("preset", None)]
    rows += [("knob", k["id"]) for k in model["schema"].get("knobs", [])]
    rows += [("apply", None), ("reset", None)]
    return rows


def _power_cur_preset(model):
    return _power_match_preset(model["schema"], model["values"])


def _power_tag(model):
    p = _power_cur_preset(model)
    return p.lower() if p != "CUSTOM" else "custom"


def _power_dirty(model):
    return model["values"] != model.get("saved_values")


def _power_cycle_preset(model, delta):
    presets = [p["id"] for p in model["schema"].get("presets", [])]
    if not presets:
        return
    cur = _power_cur_preset(model)
    i = presets.index(cur) if cur in presets else 0
    pv = _power_preset_values(model["schema"], presets[(i + delta) % len(presets)])
    if pv:
        model["values"].update(pv)


def _power_cycle_knob(model, kid, delta):
    k = _power_knob(model["schema"], kid)
    if not k or not k.get("options"):
        return
    opts = [o["v"] for o in k["options"]]
    cur = model["values"].get(kid, opts[0])
    i = opts.index(cur) if cur in opts else 0
    model["values"][kid] = opts[(i + delta) % len(opts)]


def _power_apply(model):
    """Persist the resolved profile + apply live to sysfs. Returns (ok, msg)."""
    schema = model["schema"]
    vals = model["values"]
    tag = _power_tag(model)
    out = ["# ETK POWER profile — written by Pitstop POWER tab.",
           "# Re-applied at boot by etk-power.service (govs/freqs reset on reboot).",
           "# pwr=" + tag]
    writes = []
    for k in schema.get("knobs", []):
        v = vals.get(k["id"])
        if v is None:
            continue
        out.append(f"# knob {k['id']} {v}")
        for p in k.get("paths", []):
            writes.append((p, v))
    out += [f"{p}\t{v}" for p, v in writes]
    try:
        os.makedirs(POWER_DIR, exist_ok=True)
        with open(POWER_PROFILE_FILE, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError as e:
        _log(f"power apply write failed: {e}")
        return False, str(e)[:60]
    live = 0
    for p, v in writes:
        try:
            with open(p, "w") as f:
                f.write(v)
            live += 1
        except OSError:
            pass  # a knob may reject live (min/max ordering); the boot applier retries
    model["saved_values"] = dict(vals)
    model["applied_tag"] = tag
    return True, f"{tag} — {live}/{len(writes)} knobs live + reboot-safe"


def draw_power(stdscr, state):
    """POWER tab — schema-driven gov/clock dials with named presets. Global
    (not per-game); applied live + boot-persistent."""
    if state.get("power_model") is None:
        _power_load(state)
    model = state["power_model"]
    h, w = stdscr.getmaxyx()
    rows = _power_rows(model)
    cursor = state.get("power_cursor", 0) % max(1, len(rows))
    state["power_cursor"] = cursor
    dirty = _power_dirty(model)
    cur_preset = _power_cur_preset(model)
    raw_build = os.environ.get('ETK_BUILD_TYPE', 'FULL') == 'RAW'

    def put(r, c, t, a=curses.A_NORMAL):
        try:
            stdscr.addstr(r, c, t[:max(0, w - c - 1)], a)
        except curses.error:
            pass

    y = 5
    put(y, 2, "POWER PROFILE", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
    put(y, 4, "CPU/GPU governors + clock pinning — no OC (OPP-capped). Live + reboot-safe.",
        curses.A_DIM); y += 1
    put(y, 4, "Coordinates with thermal_d: PIT cooldown overrides, then returns here.",
        curses.A_DIM); y += 2

    cap = max(1, (h - y) - 7)
    scroll = state.get("power_scroll", 0)
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + cap:
        scroll = cursor - cap + 1
    scroll = max(0, min(scroll, max(0, len(rows) - cap)))
    state["power_scroll"] = scroll
    if scroll > 0:
        put(y - 1, w - 10, "(more ^)", curses.A_DIM)
    for vis_i, (kind, payload) in enumerate(rows[scroll:scroll + cap]):
        i = scroll + vis_i
        sel = (i == cursor)
        base = curses.A_REVERSE if sel else curses.A_NORMAL
        put(y, 4, "> " if sel else "  ",
            curses.color_pair(1) if sel else curses.A_NORMAL)
        if kind == "preset":
            put(y, 6, f"PRESET   < {cur_preset} >", base | curses.A_BOLD)
            if not sel:
                for p in model["schema"].get("presets", []):
                    if p["id"] == cur_preset:
                        put(y, 32, p.get("desc", "")[:w - 34], curses.A_DIM)
                        break
        elif kind == "knob":
            k = _power_knob(model["schema"], payload)
            lbl = _power_opt_label(k, model["values"].get(payload, ""))
            put(y, 6, f"{k['label']:<16} < {lbl} >", base)
            if k.get("note") and not sel:
                put(y, 46, k["note"][:w - 48], curses.A_DIM)
        elif kind == "apply":
            put(y, 6, "APPLY" + ("   *unsaved*" if dirty else "   (no change)"),
                base | curses.A_BOLD)
        elif kind == "reset":
            put(y, 6, "Reset to BALANCED", base)
        y += 1
    if scroll + cap < len(rows):
        put(y, w - 10, "(more v)", curses.A_DIM)
    y += 1
    put(y, 4, f"Active : {model['applied_tag']}    Ledger : pwr={_power_tag(model)}",
        curses.A_BOLD); y += 1
    put(y, 4, "Thermal guard: " + ("ARMED" if not raw_build else "OFF — RAW build, no protection!"),
        curses.color_pair(PAIR_CLEAN if not raw_build else PAIR_CRASH) | curses.A_BOLD); y += 1
    notice = state.get("power_notice")
    if notice:
        ok, msg = notice
        put(y, 4, msg[:w - 6],
            curses.color_pair(PAIR_CLEAN if ok else PAIR_CRASH) | curses.A_BOLD)
    elif dirty:
        put(y, 4, "APPLY to set live + persist across reboot.",
            curses.color_pair(PAIR_RECOV))


def _power_move(state, delta):
    rows = _power_rows(state["power_model"])
    state["power_cursor"] = (state.get("power_cursor", 0) + delta) % len(rows)
    state["power_notice"] = None


def _power_adjust(state, delta):
    model = state["power_model"]
    rows = _power_rows(model)
    kind, payload = rows[state.get("power_cursor", 0) % len(rows)]
    if kind == "preset":
        _power_cycle_preset(model, delta)
        state["power_notice"] = None
    elif kind == "knob":
        _power_cycle_knob(model, payload, delta)
        state["power_notice"] = None


def _power_select(state):
    model = state["power_model"]
    rows = _power_rows(model)
    kind, payload = rows[state.get("power_cursor", 0) % len(rows)]
    if kind == "preset":
        _power_cycle_preset(model, 1)
        state["power_notice"] = None
    elif kind == "knob":
        _power_cycle_knob(model, payload, 1)
        state["power_notice"] = None
    elif kind == "reset":
        pv = _power_preset_values(model["schema"], "BALANCED")
        if pv:
            model["values"].update(pv)
        state["power_notice"] = None
    elif kind == "apply":
        ok, msg = _power_apply(model)
        state["power_notice"] = (ok, ("APPLIED: " + msg) if ok
                                 else ("APPLY FAILED: " + msg))
    return "continue"


def handle_power_pad(state, etype, code, val):
    """Gamepad input for POWER. L1/R1 intercepted upstream for tabs."""
    if state.get("power_model") is None:
        _power_load(state)
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _power_select(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_ABS and code == ABS_HAT0Y and val != 0:
        _power_move(state, 1 if val == 1 else -1)
    elif etype == EV_ABS and code == ABS_HAT0X and val != 0:
        _power_adjust(state, 1 if val == 1 else -1)
    return "continue"


def handle_power_kb(state, ch):
    """Keyboard input for POWER (dev/desktop parity)."""
    if state.get("power_model") is None:
        _power_load(state)
    if ch in (ord('q'), ord('Q')):
        return "quit"
    if ch == curses.KEY_UP:
        _power_move(state, -1)
    elif ch == curses.KEY_DOWN:
        _power_move(state, 1)
    elif ch == curses.KEY_LEFT:
        _power_adjust(state, -1)
    elif ch == curses.KEY_RIGHT:
        _power_adjust(state, 1)
    elif ch in (ord('\n'), ord(' ')):
        return _power_select(state)
    return "continue"


# === PADDOCK TAB (0.3.0 — Private Paddock sync client) ===
#
# Lists local vaults vs the user's OWN private repo (via paddock_sync.sh
# status), and pushes/pulls per game. The known_repo GET hatch survives
# (operator-supplied game sources, local + gitignored — private by nature).
# All API work lives in bin/paddock_sync.sh; this tab is a thin gamepad
# front-end over it (one engine — the single-injector invariant holds).

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# Per-row field cursor: PUSH and PULL action cells. dpad left/right moves
# between them, CONFIRM executes the focused one — the whole tab drives
# with CONFIRM + BACK + dpad only.
_PF_PUSH, _PF_PULL = 0, 1
_PF_COUNT = 2
_PENDING_SENTINELS = ("", "PENDING_CLEAN_ROOM", None, "null")


def _strip_ansi(s):
    return _ANSI_RE.sub('', s)


RPCS3_GAMES_YML = "/storage/.config/rpcs3/games.yml"
RPCS3_GAME_DIR = "/storage/roms/bios/rpcs3/dev_hdd0/game"


def _sfo_title(tid):
    """TITLE from an installed game's PARAM.SFO (covers stub/update dirs the
    launcher files don't). None on any failure — this is the deep fallback."""
    import struct
    path = os.path.join(RPCS3_GAME_DIR, tid, "PARAM.SFO")
    try:
        with open(path, 'rb') as f:
            d = f.read()
        _, _, kto, dto, n = struct.unpack_from("<4sIIII", d, 0)
        for i in range(n):
            ko, _, ln, _, do = struct.unpack_from("<HHIII", d, 20 + i * 16)
            key = d[kto + ko:d.index(b"\x00", kto + ko)].decode()
            if key == "TITLE":
                return d[dto + do:dto + do + ln].rstrip(b"\x00").decode(
                    "utf-8", "replace").strip() or None
    except Exception:
        return None
    return None


def _resolve_game_names():
    """Title-ID -> display-name map + installed-ID set, from (in preference
    order): ES gamelist.xml pretty names > .psn launcher stems > games.yml
    ISO filenames (bracket-ID stripped). PARAM.SFO is applied lazily by the
    caller for IDs none of these cover (vault-only/stub titles)."""
    names, installed, pretty = {}, set(), {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(PS3_ROMS_DIR, "gamelist.xml")).getroot()
        for g in root.iter("game"):
            p = (g.findtext("path") or "").strip()
            n = (g.findtext("name") or "").strip()
            if p and n:
                pretty[os.path.basename(p)] = n
    except Exception as e:
        _log(f"paddock: gamelist parse failed: {e}")
    for g in _list_psn_games():
        tid = g["title_id"]
        if not tid:
            continue
        installed.add(tid)
        names[tid] = pretty.get(os.path.basename(g["psn"]), g["name"])
    # games.yml: serial -> path (ISO/disc titles have no .psn launcher)
    try:
        with open(RPCS3_GAMES_YML) as f:
            for ln in f:
                if ':' not in ln or ln.lstrip().startswith('#'):
                    continue
                tid, path = ln.split(':', 1)
                tid, path = tid.strip(), path.strip().strip('"')
                if not tid:
                    continue
                installed.add(tid)
                if tid in names:
                    continue
                base = os.path.basename(path)
                nm = pretty.get(base)
                if not nm:
                    # "Gran Turismo 5 [BCUS98114].iso" -> "Gran Turismo 5"
                    nm = re.sub(r'\s*\[[^\]]*\]', '',
                                os.path.splitext(base)[0]).strip()
                names[tid] = nm or tid
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"paddock: games.yml parse failed: {e}")
    return names, installed


def _paddock_chipset():
    if PADDOCK_CHIPSET:
        return PADDOCK_CHIPSET
    try:
        return os.uname().nodename
    except Exception:
        return "SM8250"


def _local_mesa_hash():
    """sha256 of the first 64 KB of the Turnip driver — the homologation
    gate primitive. None off-rig / on read failure (shaders then grey out)."""
    try:
        with open(FREEDRENO_SO, 'rb') as f:
            return hashlib.sha256(f.read(65536)).hexdigest()
    except Exception as e:
        _log(f"paddock: mesa hash read failed: {e}")
        return None


def _paddock_load_cred():
    """Read the private-paddock credential written by install.sh's PADDOCK
    LINK step. Returns {"repo":…} or None (tab is gated on file presence,
    so None here means it was deleted mid-session)."""
    try:
        with open(PADDOCK_CRED) as f:
            return json.load(f)
    except Exception as e:
        _log(f"paddock: cred read failed: {e}")
        return None


def _paddock_sync_status():
    """Shell out to paddock_sync.sh status. Returns (rows, None) or
    (None, error_str). Each row: (game_id, local_n, local_kb, remote_kb,
    epoch_tag, state)."""
    try:
        r = subprocess.run(["bash", PADDOCK_SYNC, "status"],
                           capture_output=True, text=True, timeout=40)
    except Exception as e:
        return None, f"sync error: {e}"
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return None, (err[-1] if err else f"status failed ({r.returncode})")
    rows = []
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 6:          # engine without the names column
            parts.append("")
        if len(parts) == 7:
            rows.append(parts)
    return rows, None


def _sha256_file(path):
    """Streaming sha256 of a (possibly multi-GB) file. None on failure."""
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        _log(f"paddock: sha256 failed {path}: {e}")
        return None


def _paddock_load_repos():
    """Load the LOCAL, gitignored known_repo pointers. Returns
    {game_id: {pkg:{url,sha256}, rap:{url,sha256}, name}}. Only entries with a
    non-empty pkg.url are usable; keys starting with '_' (README/schema) are
    skipped. This file is never published — it carries the operator's own
    game-source URLs (dossier §8)."""
    repos = {}
    try:
        with open(PADDOCK_REPOS_JSON) as f:
            data = json.load(f)
        for gid, ent in data.items():
            if gid.startswith('_') or not isinstance(ent, dict):
                continue
            if (ent.get("pkg") or {}).get("url"):
                repos[gid] = ent
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"paddock: repos load failed: {e}")
    return repos


def _paddock_refresh(state):
    """Sync state with the user's own paddock via paddock_sync.sh status.
    Network/API failure is non-fatal: keep the last good rows and surface
    an offline status."""
    cred = _paddock_load_cred()
    repo_name = (cred or {}).get("repo", "?")
    raw, err = _paddock_sync_status()
    if err:
        state["paddock_status"] = f"offline — {err}"
        if state.get("paddock_rows") is None:
            state["paddock_rows"] = []
        state["paddock_repo"] = repo_name
        return

    names, installed = _resolve_game_names()

    def _name(gid, rname=""):
        # local sources > name banked in the paddock itself > PARAM.SFO > ID
        return names.get(gid) or rname or _sfo_title(gid) or gid

    repos = _paddock_load_repos()
    rows = []
    for gid, ln, lkb, rkb, epoch, pstate, rname in raw:
        rows.append({
            "game_id": gid,
            "name": _name(gid, rname),
            "installed": gid in installed,
            "local_n": int(ln or 0),
            "local_kb": int(lkb or 0),
            "remote_kb": int(rkb or 0),
            "epoch": epoch,
            "pstate": pstate,
            # known_repo: a local pkg+rap pointer exists for this (missing) game
            "has_repo": gid in repos,
        })
    # Games with a known_repo pointer but neither vault nor remote bundle
    # still get a row, so GET remains reachable.
    seen = {r["game_id"] for r in rows}
    for gid in repos:
        if gid not in seen:
            rows.append({"game_id": gid, "name": _name(gid),
                         "installed": gid in installed, "local_n": 0,
                         "local_kb": 0, "remote_kb": 0, "epoch": "-",
                         "pstate": "NO-VAULT", "has_repo": True})
    rows.sort(key=lambda r: r["name"].lower())

    n_local = sum(1 for r in rows if r["local_n"] > 0)
    n_remote = sum(1 for r in rows if r["remote_kb"] > 0)
    note = f"{n_local} local vaults · {n_remote} banked in your paddock"

    state["paddock_rows"] = rows
    state["paddock_repos"] = repos
    state["paddock_repo"] = repo_name
    state["paddock_chipset"] = _paddock_chipset()
    state["paddock_driver_note"] = note
    state["paddock_status"] = ""
    state["paddock_sel"] = min(state.get("paddock_sel", 0), max(0, len(rows) - 1))
    state["paddock_field"] = 0


def _paddock_busy(stdscr, msg, spin=None):
    """PADDOCK 'working' frame. When `spin` is a glyph it animates the ROCKNIX
    throbber centered between the message and the notifications hint."""
    try:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        _draw_title_bar(stdscr, w)
        _draw_tab_strip(stdscr, CURRENT_TAB_PADDOCK, w)
        stdscr.addstr(h // 2, max(2, (w - len(msg)) // 2), msg[:w - 4],
                      curses.A_BOLD)
        if spin:
            stdscr.addstr(h // 2 + 2, max(2, (w - 1) // 2), spin[:1],
                          curses.A_BOLD)
        hint = "Watch the on-screen notifications for progress."
        stdscr.addstr(h // 2 + 4, max(2, (w - len(hint)) // 2), hint[:w - 4],
                      curses.A_DIM)
        stdscr.refresh()
    except curses.error:
        pass


def draw_paddock(stdscr, state):
    """PADDOCK render: matched-game list with TUNE/SHADERS/SAVE toggles +
    an APPLY action, the per-rig driver verdict, and a detail line."""
    h, w = stdscr.getmaxyx()

    def put(row, col, text, attr=curses.A_NORMAL):
        try:
            stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    # Header (rows 2-3): PAD + index date, then content header.
    put(2, 2, f"PADDOCK  |  PAD: {state.get('gamepad_status', '')}", curses.A_DIM)
    idx_date = state.get("paddock_index_date", "")
    if idx_date:
        istr = f"index {idx_date}"
        put(2, max(2, w - len(istr) - 2), istr, curses.A_DIM)
    put(3, 2, "-" * (w - 4), curses.A_DIM)

    # Result sub-screen after an APPLY.
    if state.get("paddock_mode") == "result":
        ok, lines = state.get("paddock_result") or (False, ["(no result)"])
        put(5, 2, "DONE" if ok else "FAILED", curses.A_BOLD | (
            curses.color_pair(PAIR_CLEAN) if ok else curses.color_pair(PAIR_CRASH)))
        put(6, 2, "-" * (w - 4), curses.A_DIM)
        y = 8
        for ln in lines:
            if y >= h - 4:
                break
            put(y, 4, ln)
            y += 1
        if y < h - 4:
            put(y + 1, 4, "B / A: Back to list",
                curses.color_pair(1) | curses.A_BOLD)
        return

    # known_repo confirm sub-screen (install a missing game from YOUR source).
    if state.get("paddock_mode") == "pkg_confirm":
        rows = state.get("paddock_rows") or []
        sel = state.get("paddock_sel", 0)
        row = rows[sel] if 0 <= sel < len(rows) else {"game_id": "?", "name": "?"}
        ent = (state.get("paddock_repos") or {}).get(row.get("game_id"), {})
        pkg = ent.get("pkg") or {}
        url = pkg.get("url", "")
        host = url.split("/")[2] if "://" in url else url[:40]
        sha = (pkg.get("sha256") or "")
        put(5, 2, "KNOWN_REPO — INSTALL MISSING GAME", curses.A_BOLD)
        put(6, 2, "-" * (w - 4), curses.A_DIM)
        put(8, 4, f"Game   : {row.get('name', '')}  ({row.get('game_id', '')})")
        put(9, 4, f"Source : {host}")
        put(10, 4, f"pkg sha: {sha[:32] or '(none — UNVERIFIED)'}")
        put(12, 4, "This downloads a game from a source YOU supplied and installs", curses.A_DIM)
        put(13, 4, "it with the headless PKG installer. ETK hosts nothing.", curses.A_DIM)
        if not sha:
            put(15, 4, "WARNING: no sha256 in paddock_repos.json — integrity NOT checked.",
                curses.color_pair(PAIR_CRASH) | curses.A_BOLD)
        put(h - 4, 4, "B: Download & install     A: Cancel",
            curses.color_pair(1) | curses.A_BOLD)
        return

    put(5, 2, f"PRIVATE PADDOCK · {state.get('paddock_repo', '?')}", curses.A_BOLD)
    put(6, 2, f"chipset: {state.get('paddock_chipset', '')}   "
              f"{state.get('paddock_driver_note', '')}", curses.A_DIM)
    put(7, 2, "-" * (w - 4), curses.A_DIM)

    rows = state.get("paddock_rows")
    if rows is None:
        put(9, 4, "Syncing with your paddock…", curses.A_BOLD)
        return
    if not rows:
        st = state.get("paddock_status") or "No vaults yet — race a game to harvest, then PUSH it here."
        put(9, 4, st, curses.A_DIM)
        put(11, 4, "Your paddock is YOUR private GitHub repo. ETK shares nothing.",
            curses.A_DIM)
        return

    # Column header + rows.
    C_LOCAL, C_REMOTE, C_PUSH, C_PULL = 26, 38, 50, 58
    put(8, 4, "GAME", curses.A_DIM)
    put(8, C_LOCAL, "LOCAL", curses.A_DIM)
    put(8, C_REMOTE, "PADDOCK", curses.A_DIM)
    put(8, C_PUSH, "ACTION", curses.A_DIM)

    sel = state.get("paddock_sel", 0)
    field = state.get("paddock_field", 0)
    top = 10
    cap = max(1, (h - 6) - top)
    if len(rows) <= cap:
        off = 0
    else:
        off = min(max(0, sel - cap // 2), len(rows) - cap)

    def cell(ry, col, text, focused, enabled=True):
        attr = curses.A_NORMAL if enabled else curses.A_DIM
        if focused:
            attr |= curses.A_REVERSE
        put(ry, col, text, attr)

    def _fmt_kb(kb):
        if kb <= 0:
            return "—"
        if kb >= 1048576:
            return f"{kb / 1048576:.1f}G"
        if kb >= 1024:
            return f"{kb / 1024:.0f}M"
        return f"{kb}K"

    for r_i, idx in enumerate(range(off, min(off + cap, len(rows)))):
        row = rows[idx]
        y = top + r_i
        is_sel = (idx == sel)
        name_attr = curses.A_NORMAL if row["installed"] else curses.A_DIM
        if is_sel:
            name_attr = curses.A_REVERSE
        put(y, 2, "> " if is_sel else "  ",
            curses.color_pair(1) if is_sel else curses.A_NORMAL)
        put(y, 4, f"{row['name'][:20]:<20}", name_attr)

        loc = f"{row['local_n']}·{_fmt_kb(row['local_kb'])}" if row["local_n"] else "—"
        rem = _fmt_kb(row["remote_kb"])
        if row["pstate"] == "EPOCH-OLD":
            rem += "*"          # banked under an older driver epoch
        put(y, C_LOCAL, f"{loc:<11}", curses.A_DIM if not row["local_n"] else curses.A_NORMAL)
        put(y, C_REMOTE, f"{rem:<11}", curses.A_DIM if row["remote_kb"] <= 0 else curses.A_NORMAL)

        can_push = row["local_n"] > 0
        # GET replaces PULL for a missing game with a known_repo pointer.
        if not row["installed"] and row.get("has_repo"):
            cell(y, C_PUSH, "[PUSH]", is_sel and field == _PF_PUSH, enabled=can_push)
            cell(y, C_PULL, "[GET ]", is_sel and field == _PF_PULL, enabled=True)
        else:
            can_pull = row["remote_kb"] > 0 and row["pstate"] != "EPOCH-OLD"
            cell(y, C_PUSH, "[PUSH]", is_sel and field == _PF_PUSH, enabled=can_push)
            cell(y, C_PULL, "[PULL]", is_sel and field == _PF_PULL, enabled=can_pull)

    # Detail line for the selected row.
    if 0 <= sel < len(rows):
        row = rows[sel]
        ps = row["pstate"]
        if ps == "LOCAL-ONLY":
            det = "not banked — PUSH saves this vault to your paddock"
        elif ps == "REMOTE-ONLY":
            det = "banked, no local vault — PULL restores it"
        elif ps == "BOTH":
            det = f"banked @ {row['epoch']} — PUSH updates · PULL restores"
        elif ps == "EPOCH-OLD":
            det = f"banked under OLD driver ({row['epoch']}) — PUSH re-banks on current"
        elif ps == "NO-VAULT" and row.get("has_repo"):
            det = "missing game — known_repo ready; GET downloads + installs it"
        else:
            det = ps
        put(h - 5, 2, f"› {row['name']}: {det}"[:w - 4],
            curses.color_pair(1) | curses.A_BOLD)


def _paddock_apply(state):
    """CONFIRM on a row: queue the focused action (PUSH or PULL/GET) for the
    main loop, which owns the busy frame. All gates re-checked here so a
    stale screen can't queue an impossible action."""
    rows = state.get("paddock_rows") or []
    if not rows:
        return "continue"
    row = rows[state.get("paddock_sel", 0)]
    field = state.get("paddock_field", 0)
    if field == _PF_PUSH:
        if row["local_n"] <= 0:
            state["status"] = "Nothing local to push — race it first"
            return "continue"
        state["paddock_action"] = {"verb": "push", "row": row}
        return "continue"
    # _PF_PULL: GET for missing known_repo games, PULL otherwise.
    if not row["installed"] and row.get("has_repo"):
        state["paddock_mode"] = "pkg_confirm"
        return "continue"
    if row["remote_kb"] <= 0:
        state["status"] = "Nothing banked for this game — PUSH first"
        return "continue"
    if row["pstate"] == "EPOCH-OLD":
        state["status"] = "Banked under an older driver — gated (re-push on current)"
        return "continue"
    state["paddock_action"] = {"verb": "pull", "row": row}
    return "continue"


def _paddock_queue_pkg(state):
    """Confirm pressed in pkg_confirm: queue the known_repo install for the
    selected row and return to the list (the main loop executes it)."""
    rows = state.get("paddock_rows") or []
    sel = state.get("paddock_sel", 0)
    if 0 <= sel < len(rows):
        ent = (state.get("paddock_repos") or {}).get(rows[sel]["game_id"])
        if ent:
            state["paddock_pkg_action"] = (rows[sel], ent)
    state["paddock_mode"] = "list"


def _run_paddock_sync(verb, row, notifier):
    """Shell out to paddock_sync.sh push/pull for one game, streaming its
    output to the mako overlay. Returns (ok, lines) for the result
    sub-screen. The mesa_hash homologation gate lives in the engine; this
    front-end NEVER passes --force (an epoch-gated PULL is greyed out in
    the UI — forcing config-only restores stays a manual SSH decision)."""
    gid = row["game_id"]
    name = row["name"]
    if not os.path.exists(PADDOCK_SYNC):
        return False, ["sync engine not found on rig:", PADDOCK_SYNC,
                       "(install.sh deploys bin/paddock_sync.sh)"]
    cmd = ["bash", PADDOCK_SYNC, verb, gid]
    if verb == "push" and name and name != gid:
        cmd.append(name)     # bank the display name in paddock_names.json
    notifier.post("PADDOCK", f"{name}: {verb} starting…")
    lines = []
    try:
        proc = subprocess.Popen(cmd, env=_tools_env(),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for raw in proc.stdout:
            ln = _strip_ansi(raw.rstrip())
            if not ln:
                continue
            lines.append(ln)
            notifier.post("PADDOCK", f"{name}: {ln[:60]}")
        proc.wait(timeout=1800)
        ok = proc.returncode == 0
    except Exception as e:
        return False, [f"{verb} failed: {e}"] + lines[-10:]
    notifier.post("PADDOCK", f"{name}: {verb} {'done ✓' if ok else 'FAILED'}")
    tail = lines[-12:]
    head = [f"{verb.upper()} {'OK' if ok else 'FAILED'}: {name}", ""]
    return ok, head + tail


def _ascii_bar(frac, width=18):
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    fill = int(frac * width)
    return "[" + "#" * fill + "-" * (width - fill) + "]"


def _curl_total_bytes(url):
    """Best-effort total size via HEAD (follows redirects). 0 if the server
    doesn't report Content-Length (then progress shows MB downloaded only)."""
    try:
        r = subprocess.run(["curl", "-sIL", url], capture_output=True,
                           text=True, timeout=30)
        total = 0
        for ln in r.stdout.splitlines():
            if ln.lower().startswith("content-length:"):
                try:
                    total = int(ln.split(":", 1)[1].strip())
                except ValueError:
                    pass
        return total
    except Exception:
        return 0


def _curl_with_progress(url, dest, notifier, label, timeout=7200):
    """Download `url` to `dest` with a LIVE mako progress bar. mako has no
    progress widget and a static toast self-expires in ~1.5s, so we re-post an
    ASCII bar every ~1.5s — which both shows progress AND keeps the toast on
    screen. This is the QoL fix for multi-GB PKG downloads that otherwise read
    as hung. Returns curl's return code (0 = ok, 124 = timeout)."""
    total = _curl_total_bytes(url)
    notifier.post("PADDOCK", f"{label}  starting…")
    try:
        proc = subprocess.Popen(["curl", "-fsSL", "-o", dest, url],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:
        _log(f"curl spawn failed: {e}")
        return 1
    start = time.time()
    while proc.poll() is None:
        if time.time() - start > timeout:
            proc.kill()
            return 124
        try:
            got = os.path.getsize(dest)
        except OSError:
            got = 0
        mb = got >> 20
        if total > 0:
            body = (f"{label}  {_ascii_bar(got / total)} "
                    f"{int(100 * got / total)}%  {mb}/{total >> 20} MB")
        else:
            body = f"{label}  downloading… {mb} MB"
        notifier.post("PADDOCK", body, timeout=4000)
        time.sleep(1.5)
    return proc.returncode


def _run_paddock_pkg_install(row, ent, notifier):
    """known_repo hatch: download the operator-supplied pkg (+rap), sha256-
    verify, then hand to the existing headless PKG installer. The URLs are the
    operator's own (pure-data invariant: ETK runs the installer, never hosts
    the bytes). Refuses on a sha mismatch; warns loudly when no sha is given."""
    gid = row["game_id"]
    name = row["name"]
    pkg = ent.get("pkg") or {}
    rap = ent.get("rap") or {}
    pkg_url = pkg.get("url")
    pkg_sha = (pkg.get("sha256") or "").lower()
    if not pkg_url:
        return False, ["no pkg url in paddock_repos.json for this game"]
    try:
        os.makedirs(PKG_STAGING_DIR, exist_ok=True)
    except Exception:
        pass
    tmp_pkg = os.path.join(PKG_STAGING_DIR, f"_paddock_{gid}.pkg")
    tmp_rap = os.path.join(PKG_STAGING_DIR, f"_paddock_{gid}.rap")
    lines = []
    try:
        rc = _curl_with_progress(pkg_url, tmp_pkg, notifier, f"{name} PKG")
        if rc != 0:
            return False, [f"pkg download failed (curl {rc})"]
        # Only hash when there's something to compare against — a 2+ GB hash on
        # an UNVERIFIED install is wasted work (and looks like another hang).
        if pkg_sha:
            notifier.post("PADDOCK", f"{name}: verifying sha256…")
            got = (_sha256_file(tmp_pkg) or "").lower()
            if got != pkg_sha:
                return False, ["pkg sha256 MISMATCH — refusing to install",
                               f"want {pkg_sha[:24]}…", f"got  {got[:24]}…"]
            lines.append("pkg downloaded + sha256 verified")
        else:
            lines.append("pkg downloaded (NO sha256 in repos — UNVERIFIED)")
        rap_path = None
        if rap.get("url"):
            rrc = _curl_with_progress(rap["url"], tmp_rap, notifier, f"{name} RAP")
            if rrc == 0:
                rsha = (rap.get("sha256") or "").lower()
                if rsha:
                    rgot = (_sha256_file(tmp_rap) or "").lower()
                    if rgot != rsha:
                        return False, ["rap sha256 MISMATCH — refusing", lines]
                    lines.append("rap downloaded + verified")
                else:
                    lines.append("rap downloaded (UNVERIFIED)")
                rap_path = tmp_rap
            else:
                lines.append(f"rap download failed (curl {rrc}) — pkg only")
        notifier.post("PADDOCK", f"{name}: installing PKG (RPCS3 opens)…")
        ok, ilines = _run_install(tmp_pkg, rap_path, notifier)
        head = [f"{'INSTALLED' if ok else 'FAILED'}: {name}", ""]
        return ok, head + lines + [""] + (ilines or [])[-10:]
    except Exception as e:
        return False, [f"pkg install error: {e}"] + lines
    finally:
        for p in (tmp_pkg, tmp_rap):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def handle_paddock_kb(state, ch):
    """Keyboard input for PADDOCK. Tab-switch keys [/] intercepted upstream."""
    if ch in (ord('q'), ord('Q')):
        return "quit"
    if state.get("paddock_mode") == "result":
        if ch in (ord('\n'), ord('s'), curses.KEY_BACKSPACE, 8, 127):
            state["paddock_mode"] = "list"
        return "continue"
    if state.get("paddock_mode") == "pkg_confirm":
        if ch in (ord('\n'), ord('s')):
            _paddock_queue_pkg(state)
        elif ch in (curses.KEY_BACKSPACE, 8, 127):
            state["paddock_mode"] = "list"
        return "continue"
    rows = state.get("paddock_rows") or []
    if ch == ord('r'):
        state["paddock_refresh_request"] = True
    elif ch == curses.KEY_UP and rows:
        state["paddock_sel"] = (state["paddock_sel"] - 1) % len(rows)
        state["status"] = ""
    elif ch == curses.KEY_DOWN and rows:
        state["paddock_sel"] = (state["paddock_sel"] + 1) % len(rows)
        state["status"] = ""
    elif ch == curses.KEY_LEFT:
        state["paddock_field"] = (state.get("paddock_field", 0) - 1) % _PF_COUNT
        state["status"] = ""
    elif ch == curses.KEY_RIGHT:
        state["paddock_field"] = (state.get("paddock_field", 0) + 1) % _PF_COUNT
        state["status"] = ""
    elif ch in (ord('\n'), ord('s')):
        return _paddock_apply(state)
    return "continue"


def handle_paddock_pad(state, etype, code, val):
    """Gamepad input for PADDOCK. L1/R1 intercepted upstream for tabs."""
    if state.get("paddock_mode") == "result":
        if etype == EV_KEY and val == 1 and code in (BTN_CONFIRM, BTN_BACK):
            state["paddock_mode"] = "list"
        return "continue"
    if state.get("paddock_mode") == "pkg_confirm":
        if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
            _paddock_queue_pkg(state)
        elif etype == EV_KEY and val == 1 and code == BTN_BACK:
            state["paddock_mode"] = "list"
        return "continue"
    rows = state.get("paddock_rows") or []
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _paddock_apply(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return "quit"
    if etype == EV_ABS and code == ABS_HAT0Y and rows:
        if val == -1:
            state["paddock_sel"] = (state["paddock_sel"] - 1) % len(rows)
            state["status"] = ""
        elif val == 1:
            state["paddock_sel"] = (state["paddock_sel"] + 1) % len(rows)
            state["status"] = ""
    elif etype == EV_ABS and code == ABS_HAT0X and val != 0:
        state["paddock_field"] = (state.get("paddock_field", 0) + val) % _PF_COUNT
        state["status"] = ""
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
        # Always return to the TELEMETRY table on a tab switch — never strand
        # the user in a stale detail card after they navigate away and back.
        state["telemetry_mode"] = "table"
        if target_tab == CURRENT_TAB_TELEMETRY:
            state["_sessions_cache"] = None
            state["_config_changes_cache"] = None
            state["_career_cache"] = None
            state["_pit_note_cache"] = None
        if target_tab == CURRENT_TAB_PADDOCK:
            # Always return to the list (never strand in a stale result card),
            # and lazily fetch the index on first entry (the actual network
            # call runs in the main loop, where there's a stdscr busy frame).
            state["paddock_mode"] = "list"
            if state.get("paddock_rows") is None:
                state["paddock_refresh_request"] = True
        if target_tab == CURRENT_TAB_DRIVER:
            # Re-read the live profile.d injection on every entry so the dials
            # reflect what's actually armed on the rig (cheap local file read).
            state["driver_model"] = None
        if target_tab == CURRENT_TAB_POWER:
            state["power_model"] = None  # re-read the persisted profile on entry


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
    elif state["current_tab"] == CURRENT_TAB_PADDOCK:
        draw_paddock(stdscr, state)
    elif state["current_tab"] == CURRENT_TAB_DRIVER:
        draw_driver(stdscr, state)
    elif state["current_tab"] == CURRENT_TAB_POWER:
        draw_power(stdscr, state)
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
    # PADDOCK is live regardless of ETK_NO_TARGET: it never touches the TUNING
    # target's config/ledger, and is useful for subscribing a first tune.
    if ct == CURRENT_TAB_PADDOCK:
        return handle_paddock_kb(state, ch)
    if ct == CURRENT_TAB_DRIVER:
        return handle_driver_kb(state, ch)
    if ct == CURRENT_TAB_POWER:
        return handle_power_kb(state, ch)
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
    if ct == CURRENT_TAB_PADDOCK:
        return handle_paddock_pad(state, etype, code, val)
    if ct == CURRENT_TAB_DRIVER:
        return handle_driver_pad(state, etype, code, val)
    if ct == CURRENT_TAB_POWER:
        return handle_power_pad(state, etype, code, val)
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
        # TELEMETRY row selection + sub-mode. telemetry_cursor is an absolute
        # index into the merged (sessions+config) list; telemetry_scroll is
        # derived to keep it on-screen. CONFIRM on a row opens the detail view.
        "telemetry_cursor": 0,
        "telemetry_mode": "table",   # "table" | "detail"
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
        # PADDOCK tab (0.5.0 subscribe/install client). rows=None means the
        # index hasn't been fetched yet — it loads lazily on first tab entry.
        "paddock_rows": None,
        "paddock_repos": {},
        "paddock_sel": 0,
        "paddock_field": 0,            # _PF_PUSH | _PF_PULL
        "paddock_mode": "list",        # "list" | "result" | "pkg_confirm"
        "paddock_result": None,
        "paddock_index_date": "",
        "paddock_chipset": "",
        "paddock_driver_note": "",
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

                # Crash-frame preview (swayimg) intercept: while it's up, ANY
                # button press dismisses it early and is SWALLOWED, so the press
                # never also navigates / switches tabs underneath the image. The
                # 30s timeout is just the backstop. On EVERY close path we
                # re-assert foot fullscreen (the install-path fix) so Pitstop
                # isn't left split. Pitstop owns the pad here.
                pp = state.get("_preview_proc")
                preview_up = pp is not None and pp.poll() is None
                if pp is not None and not preview_up:
                    # auto-closed by the timeout backstop — restore Pitstop.
                    state["_preview_proc"] = None
                    _restore_pitstop_window(state.pop("_preview_env", None) or _tools_env())
                if preview_up:
                    # Any button OR D-pad PRESS dismisses (not a release: val==0
                    # is the up-release of the press that opened it).
                    is_press = (etype == EV_KEY and val == 1) or (
                        etype == EV_ABS and code in (ABS_HAT0X, ABS_HAT0Y) and val != 0)
                    if is_press:
                        subprocess.run(["pkill", "-x", "swayimg"],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                        state["_preview_proc"] = None
                        _restore_pitstop_window(state.pop("_preview_env", None) or _tools_env())
                    # swallow everything while the preview is on screen
                # Tab switching (gamepad) — L1/R1 cycle through the tabs.
                elif etype == EV_KEY and val == 1 and code == BTN_TL:
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
        # Manage Shaders: lazy vault scan (du + dry-run stat pass). Runs here so
        # a busy frame covers the multi-second read.
        if state.pop("shaders_scan_request", None):
            _run_with_spinner(stdscr, "Scanning shader vault…",
                              _scan_vault_hygiene, state)

        action = state.pop("tools_action", None)
        if action:
            notifier = _Notifier()
            kind = action[0]
            if kind == "install":
                _draw_tools_busy(stdscr, "install")
                ok, lines = _run_install(action[1], action[2], notifier)
            elif kind == "uninstall":
                _draw_tools_busy(stdscr, "uninstall")
                ok, lines = _run_uninstall(action[1], notifier)
            elif kind == "shader":
                res = _run_with_spinner(
                    stdscr, "Cleaning shaders — please wait…",
                    _run_shader_op, action[1], action[2], action[3], notifier)
                ok, lines = res if res else (False, ["shader op failed — see log"])
                state["shaders_model"] = None     # force rescan on re-entry
            else:
                ok, lines = (False, [f"unknown action: {kind}"])
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

        # PADDOCK: lazy sync-status fetch (network) — run here so a busy
        # frame can be drawn while the API round-trip blocks.
        if state.pop("paddock_refresh_request", None):
            _run_with_spinner(stdscr, "Syncing with your paddock…",
                              _paddock_refresh, state, draw=_paddock_busy)

        # PADDOCK: queued PUSH/PULL — shell out to paddock_sync.sh for the
        # selected game, streaming progress to mako. Same long-op pattern as
        # the TOOLS install above (no RPCS3 launch, so no screen handoff).
        pact = state.pop("paddock_action", None)
        if pact:
            prow = pact["row"]
            res = _run_with_spinner(
                stdscr, f"{pact['verb'].upper()}: {prow['name']} ↔ your paddock",
                _run_paddock_sync, pact["verb"], prow, _Notifier(),
                draw=_paddock_busy)
            ok, lines = res if res else (False, ["sync failed — see log"])
            state["paddock_result"] = (ok, lines)
            state["paddock_mode"] = "result"
            # Sync changed local or remote state — refresh the list next entry.
            if ok:
                state["paddock_refresh_request"] = True
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

        # PADDOCK: queued known_repo install — download the operator-supplied
        # pkg/rap, verify, hand to the headless installer (RPCS3 opens, like a
        # TOOLS install). Pure-data invariant: ETK runs the installer, the
        # bytes are the operator's own.
        pkg_act = state.pop("paddock_pkg_action", None)
        if pkg_act:
            prow2, pent = pkg_act
            _paddock_busy(stdscr, f"Getting {prow2['name']}: downloading, then RPCS3 opens — watch notifications")
            ok, lines = _run_paddock_pkg_install(prow2, pent, _Notifier())
            state["paddock_result"] = (ok, lines)
            state["paddock_mode"] = "result"
            # A successful install changes the library — refresh on next entry.
            if ok:
                state["paddock_refresh_request"] = True
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
