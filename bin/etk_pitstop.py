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
# RPCS3's own serial->path registry (rows committed on a title's first boot).
RPCS3_GAMES_YML = "/storage/.config/rpcs3/games.yml"
# IRISMAN-style serial tag in a filename: "(BCUS98158)" or "[BLUS-30019]".
_ISO_ID_TAG_RE = re.compile(r'[\[\(]\s*([A-Z]{4})\s*-?\s*([0-9]{5})\s*[\]\)]')


def _strip_serial_tag(stem):
    """Display name from a ROM filename stem: drop the '(BCUS98158)' /
    '[BLUS-30019]' serial tag and collapse the whitespace runs the removal
    (or the dump name itself) leaves behind."""
    return re.sub(r'\s+', ' ', _ISO_ID_TAG_RE.sub('', stem)).strip()

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

# The single user-facing version in the title bar ("// ETK PITSTOP vX //").
# ALIGNED TO THE RELEASE TAG at every cut (load-bearing since 0.8.0: the
# TOOLS self-update compares this against the latest GitHub release tag).
# The DRIVER tab names the actually-bound stack builds.
APP_VERSION = "0.8.3"

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

# TUNING > CORE (per-title RPCS3 core swap — multigame lane 2026-08-04).
# The DRIVER-tab catalog model, but per-title and launch-cadence: install.sh
# stages host emulators/*.AppImage to the card catalog; the launch wrapper
# (bound over /usr/bin/rpcs3-sa by etk-rpcs3-bind.sh) resolves serial -> core
# via core_map.tsv at every launch and execs it. No map entry = the certified
# default. A/B tooling — pins are the operator's; nothing ships pinned. The
# CORE section only surfaces when the wrapper is deployed (ETK_CORE_SWAP=1
# rigs), so a kill-switched or host-dev run renders the classic flat list.
RPCS3_CORES_DIR = os.environ.get('RPCS3_CORES_DIR', f"{ETK_ROOT}/emulators")
RPCS3_CORE_MAP = os.environ.get('RPCS3_CORE_MAP',
                                f"{RPCS3_CORES_DIR}/core_map.tsv")
CORE_WRAPPER_PATH = "/storage/.config/etk-rpcs3-launch.sh"
CERT_CORE = "certified"   # synthetic catalog id: no pin, exec rpcs3-sa.custom
CERT_CORE_SRC = "/storage/rpcs3/rpcs3-sa.custom.src"

# TUNING > PATCH (community patch toggles — multigame lane §3). NOT a new
# patch system: these are RPCS3's own files — the community patch.yml
# (install.sh STEP 6.554 keeps it fresh from the official endpoint) and the
# engine's patch_config.yml enablement file (the same one the desktop GUI
# writes). bin/etk_patchlib.py does the dependency-free parsing (no PyYAML on
# the rig); a broken/missing lib or patch file just means no PATCH section —
# never a dead Pitstop. Toggles apply at next game launch.
PATCHES_YML = "/storage/.config/rpcs3/patches/patch.yml"
PATCH_CONFIG_YML = "/storage/.config/rpcs3/patch_config.yml"
PATCH_PINS_FILE = os.environ.get('PATCH_PINS_FILE',
                                 f"{TELEMETRY_DIR}/patch_pins.tsv")
try:
    import etk_patchlib as _patchlib
except Exception as _e:  # pragma: no cover — import must never kill Pitstop
    _patchlib = None

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
# dev_flash holds the installed PS3 firmware. RPCS3's config dir is
# /storage/.config/rpcs3/ and its dev_flash is symlinked to the games tree, so
# use the CONFIG-DIR path — it's what RPCS3 actually resolves at runtime.
# version.txt is RPCS3's own firmware-version marker.
RPCS3_DEV_FLASH = os.environ.get(
    'RPCS3_DEV_FLASH', '/storage/.config/rpcs3/dev_flash')
RPCS3_FW_VERSION_FILE = os.environ.get(
    'RPCS3_FW_VERSION_FILE', f"{RPCS3_DEV_FLASH}/vsh/etc/version.txt")
# RPCS3's config-dir game path (dev_hdd0/game under /storage/.config/rpcs3/,
# symlinked to the games tree) — where RPCS3 ACTUALLY installs a PKG. On a
# coherent rig this resolves to the same place as RPCS3_GAME_DIR; a foreign SD
# card shadowing /storage/roms (split-brain) makes them diverge, so the PKG
# installer would extract where it isn't watching and read as a silent failure.
RPCS3_CFG_GAME_DIR = os.environ.get(
    'RPCS3_CFG_GAME_DIR', '/storage/.config/rpcs3/dev_hdd0/game')
# Same idea for the licence folder: RPCS3 writes .rap/.edat to
# get_hdd0_dir()/home/<usr>/exdata, resolved through the SAME config dir, so
# the ETK-side games-tree constant is only correct once the link exists.
RPCS3_CFG_EXDATA_DIR = os.environ.get(
    'RPCS3_CFG_EXDATA_DIR',
    '/storage/.config/rpcs3/dev_hdd0/home/00000001/exdata')
# RPCS3's pad config. NOT one of the four folders start_rpcs3.sh symlinks, so
# it stays in the config dir. Read+written by the TRIGGER CALIBRATION screen
# and by the pad-binding repair (see _ensure_pad_binding) — both edit single
# lines in place, never rewrite the file, so they cannot clobber each other.
RPCS3_PAD_CONFIG = os.environ.get(
    'RPCS3_PAD_CONFIG',
    '/storage/.config/rpcs3/input_configs/global/Default.yml')
PKG_STAGING_DIR = os.environ.get(
    'PKG_STAGING_DIR', f"{os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')}/pkg_install_drop")
# Firmware drop folder — the user places the official Sony PS3UPDAT.PUP here.
# Unlike PKG staging, the .pup is KEPT after a successful install (firmware is a
# reusable, system-wide asset installed once, not a per-game staging file).
FIRMWARE_DROP_DIR = os.environ.get(
    'FIRMWARE_DROP_DIR', f"{os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')}/firmware_drop")
ETK_TEMPLATE_CONFIG = os.environ.get(
    'ETK_TEMPLATE_CONFIG', f"{os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')}/config/etk_template.yml")
# Golden-default config seeding (0.7.1, default-ON; env.sh exports the
# etk.conf kill-switch ETK_GOLDEN_SEED=0). When ON, any playable title
# with no custom_configs/config_<ID>.yml is seeded from the golden
# template at Pitstop startup. (The 0.7.1 disc-only Strict Rendering Mode
# overlay was removed for 0.7.x — refuted as a false fix; both packaging
# models now seed the plain template.)
GOLDEN_SEED_ENABLED = os.environ.get('ETK_GOLDEN_SEED', '1').strip() != '0'
# ISO onboarding (0.7.2, default-ON; env.sh exports the etk.conf
# kill-switch ETK_ISO_ONBOARD=0). ES's ps3 system scans ONLY
# ".ps3 .psn .m3u" (es_systems.cfg, verified on-rig 2026-07-19), so a
# dropped .iso never appears until an .m3u launcher exists for it; and
# ROCKNIX's get_setting escapes ()& but NOT [] when it builds its awk
# regex from the ROM filename (001-functions, same rig read), so an
# IRISMAN-style "[BLUS-30019]" name silently disables every per-game
# setting including the MangoHud overlay. The startup sweep fixes both
# and seeds the golden config from the filename serial.
ISO_ONBOARD_ENABLED = os.environ.get('ETK_ISO_ONBOARD', '1').strip() != '0'
# Controller binding repair (0.8.1, default-ON; env.sh exports the etk.conf
# kill-switch ETK_PAD_BIND=0). ROCKNIX's stock RPCS3 pad config names a
# virtual device InputPlumber no longer presents, which leaves RPCS3 on
# NullPadHandler — a dead controller with no on-screen clue.
PAD_BIND_ENABLED = os.environ.get('ETK_PAD_BIND', '1').strip() != '0'
ROCKNIX_SYSTEM_CFG = os.environ.get(
    'ROCKNIX_SYSTEM_CFG', '/storage/.config/system/configs/system.cfg')
SHM_DIR = os.environ.get('SHM_DIR', '/dev/shm/etk_shm')
ETK_INSTALL_LOCK = os.environ.get('ETK_INSTALL_LOCK', f"{SHM_DIR}/etk_install_lock")
# Background installs (0.8.4, default-ON; env.sh exports the etk.conf
# kill-switch ETK_BG_INSTALL=0). An install used to hold the whole app
# hostage — one main-loop iteration, no input, no leaving. EmulationStation
# runs a scrape or a content install in the background behind a progress card
# while the frontend stays live, and the kit now does the same: Pitstop does
# the fast pre-flight, queues the job, and hands it to bin/etk_install_worker.py
# out of process, so the operator can keep using the Pitstop, browse ES, or
# close the terminal entirely. Set 0 to fall back to the old modal path.
BG_INSTALL_ENABLED = os.environ.get('ETK_BG_INSTALL', '1').strip() != '0'
INSTALL_QUEUE_DIR = os.path.join(SHM_DIR, 'install_queue')
INSTALL_WORKER_PID = os.path.join(SHM_DIR, 'install_worker.pid')
INSTALL_STAT_FILE = os.path.join(SHM_DIR, 'etk_install_stat')
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
ABS_Z = 2                   # L2 analog trigger, 0-255 on the DS5 target
ABS_RZ = 5                  # R2 analog trigger, 0-255 on the DS5 target
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
    """Map a title ID to its human display name: .psn launcher stem first
    (installed PKGs), then ISO/m3u filename stems via their '(SERIAL)' tag
    (disc titles have no .psn), then RPCS3's games.yml path for untagged
    discs that have booted at least once. Falls back to the ID itself so
    the header is never blank."""
    iso_stem = None
    try:
        for entry in sorted(os.listdir(PS3_ROMS_DIR)):
            if entry.endswith(".psn"):
                with open(os.path.join(PS3_ROMS_DIR, entry), 'r') as f:
                    if f.read().strip() == target_id:
                        return entry[:-4]
            elif entry.lower().endswith((".iso", ".m3u")) and iso_stem is None:
                m = _ISO_ID_TAG_RE.search(entry)
                if m and (m.group(1) + m.group(2)) == target_id:
                    iso_stem = _strip_serial_tag(entry[:-4])
    except Exception:
        pass
    if iso_stem:
        return iso_stem
    try:
        with open(RPCS3_GAMES_YML) as f:
            for ln in f:
                if ln.startswith(target_id + ':'):
                    base = os.path.basename(ln.split(':', 1)[1].strip().strip('"'))
                    nm = _strip_serial_tag(os.path.splitext(base)[0])
                    if nm:
                        return nm
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

# --- TUNING > CORE helpers (per-title RPCS3 core pin) ---

def _core_surface_live():
    """The CORE section exists only where the launch wrapper does — i.e. an
    ETK_CORE_SWAP=1 rig install. Host-dev runs and kill-switched rigs get
    the classic flat TUNING list."""
    return os.path.isfile(CORE_WRAPPER_PATH)


def _core_catalog():
    """Sorted catalog of stageable core builds on the card."""
    try:
        return sorted(f for f in os.listdir(RPCS3_CORES_DIR)
                      if f.endswith(".AppImage"))
    except OSError:
        return []


def _core_label(token):
    """Compact display label for a core token. MUST mirror env.sh's
    etk_rpcs3_core_tag compaction so what the operator reads on-screen is
    what the ledger tune_tag records."""
    if token == CERT_CORE:
        try:
            with open(CERT_CORE_SRC) as f:
                src = f.read().strip()
            if src:
                return "certified " + _core_label(src)
        except OSError:
            pass
        return CERT_CORE
    t = re.sub(r'\.AppImage$', '', token)
    t = re.sub(r'^rpcs3[-_]*', '', t)
    t = re.sub(r'^etk[-_]*', '', t)
    t = re.sub(r'^gtk-edition[-_]*', '', t)
    t = re.sub(r'_linux_aarch64$', '', t)
    return t or token


def _core_map_read():
    """core_map.tsv -> {serial: core_file}. Missing file = no pins."""
    m = {}
    try:
        with open(RPCS3_CORE_MAP) as f:
            for ln in f:
                if ln.startswith("#"):
                    continue
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 2 and parts[0] and parts[1]:
                    m[parts[0]] = parts[1]
    except OSError:
        pass
    return m


def _core_map_write(serial, pick):
    """Upsert this serial's core pin (pick == certified removes the row —
    an absent row IS the certified default, so the map stays a list of
    exceptions). Atomic tmp+mv; read-back verified. Returns bool."""
    m = _core_map_read()
    if pick == CERT_CORE:
        m.pop(serial, None)
    else:
        m[serial] = pick
    os.makedirs(RPCS3_CORES_DIR, exist_ok=True)
    tmp = RPCS3_CORE_MAP + ".tmp"
    with open(tmp, "w") as f:
        f.write("# serial<TAB>core-file — written by Pitstop TUNING > CORE;"
                " read by etk-rpcs3-launch.sh at every game launch\n")
        for s in sorted(m):
            f.write(f"{s}\t{m[s]}\n")
    os.replace(tmp, RPCS3_CORE_MAP)
    return _core_map_read().get(serial, CERT_CORE) == pick


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

    # --- TUNING sections (multigame lane): CORE leads, then CONFIG. ---
    # Headers are non-selectable furniture (type "header": drawn as a rule,
    # skipped by the cursor, ignored by every save/diff path — they carry an
    # empty current_val so _render_value stays total). The CORE row is an
    # enum-shaped item flagged kind="core": draw/adjust/diff all reuse the
    # enum machinery for free; only _tuning_save diverts it (core_map.tsv,
    # not the YAML injector).
    if _core_surface_live():
        cur = _core_map_read().get(TARGET_ID, CERT_CORE)
        options = [CERT_CORE] + _core_catalog()
        if cur not in options:
            # Pinned core no longer staged: adopt it so the pin round-trips
            # on save instead of silently unpinning (the enum-adoption idiom
            # above). The wrapper already fail-softs a missing file to the
            # certified core at launch.
            options.insert(1, cur)
        core_item = {
            "label": "Emulator Core", "kind": "core", "type": "enum",
            "section": None, "yaml_key": "",
            "options": options, "enum_idx": options.index(cur),
            "current_val": cur,
            "help": "Which RPCS3 core THIS title launches -- the LLVM 19-vs-22 "
                    "split made core choice per-title (RR7/Ratchet regress on 22 "
                    "while Sega/Namco titles gain). certified = the shipped "
                    "default; other entries come from host emulators/ via "
                    "install.sh. Applies at the NEXT launch, no reboot. Each "
                    "core keeps its own PPU cache, so the first boot after a "
                    "swap recompiles -- pin, don't flap.",
        }
        core_item["original_render"] = _render_value(core_item)
        matrix = ([{"label": "CORE", "type": "header", "current_val": ""},
                   core_item,
                   {"label": "CONFIG", "type": "header", "current_val": ""}]
                  + matrix)

    # --- PATCH section: auto-detected community patches for this title. ---
    # One bool-shaped row per patch declaring this serial in the community
    # file; enabled state read from RPCS3's own patch_config.yml. Fail-soft
    # to no-section on any parse trouble (logged) — the classic list must
    # never be hostage to a community file.
    if _patchlib is not None:
        try:
            found = _patchlib.parse_patch_yml(PATCHES_YML, TARGET_ID)
        except Exception as e:
            _log(f"PATCH: parse of {PATCHES_YML} failed: "
                 f"{e.__class__.__name__}: {e}")
            found = []
        if found:
            try:
                tree, dropped = _patchlib.read_patch_config(PATCH_CONFIG_YML)
                if dropped:
                    _log(f"PATCH: {dropped} Configurable Values subtree(s) in "
                         f"{PATCH_CONFIG_YML} are not preserved by ETK saves")
            except Exception as e:
                _log(f"PATCH: read of {PATCH_CONFIG_YML} failed: "
                     f"{e.__class__.__name__}: {e}")
                tree = {}
            matrix.append({"label": "PATCH", "type": "header",
                           "current_val": ""})
            for p in found:
                bits = []
                if p.get("notes"):
                    bits.append(p["notes"])
                if p.get("patch_version"):
                    bits.append(f"Patch v{p['patch_version']}")
                if p.get("author"):
                    bits.append(f"by {p['author']}")
                bits.append("Community patch (RPCS3 framework); applies at "
                            "next launch. Enabled under every app version "
                            "the patch declares, so a version mismatch can't "
                            "silently no-op.")
                if p.get("has_config_values"):
                    bits.append("Has advanced values -- ETK enables the "
                                "patch defaults.")
                item = {
                    "label": p["description"][:38], "kind": "patch",
                    "type": "bool", "section": None, "yaml_key": "",
                    "patch": p,
                    "current_val": _patchlib.patch_is_enabled(
                        tree, p, TARGET_ID),
                    "help": "  ".join(bits),
                }
                item["original_render"] = _render_value(item)
                matrix.append(item)

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
    # Single visible version: the ETK release. The bound driver/emulator
    # builds live on the DRIVER tab (driver_string()); the stack version
    # was dropped from the header for 0.7.1 (consolidate, simplify).
    title = f" // ETK PITSTOP v{APP_VERSION} // "
    stdscr.attron(curses.color_pair(1))
    stdscr.addstr(0, 2, title[:max(0, w - 3)], curses.A_BOLD)
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
    'etk_turnip_rocknix_26.1.3_gtk_0.1.so'      -> 'rocknix_26.1.3_gtk_0.1';
    'stock'/'' -> 'stock'. Filename IS the catalog id (operator drops the .so
    into drivers/ and we name it by what they called it). The ETK livery
    convention is [house]_[driver]_[os]_[base-version]_[game-target]_[fork-version]."""
    if not build_id or build_id == STOCK_BUILD:
        return STOCK_BUILD
    name = re.sub(r"\.so$", "", build_id)
    name = re.sub(r"^(?:libvulkan_freedreno|etk_turnip)[-_]?", "", name)
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


_POWERED_BY_CACHE = None


def _powered_by():
    """Raw loaded driver .so filename for the title bar's 'powered by' field
    (e.g. 'etk_turnip_rocknix_26.1.3_gtk_0.3.so'), or 'stock Turnip' when no
    catalog build is bound. The full filename (not the pretty label) is the
    point — it names the exact byte-identical build the rig is running. Cached
    (the bound build only changes on reboot) so the title redraw is file-free."""
    global _POWERED_BY_CACHE
    if _POWERED_BY_CACHE is not None:
        return _POWERED_BY_CACHE
    loaded = _read_build_pointer(TURNIP_LOADED_FILE)
    _POWERED_BY_CACHE = "stock Turnip" if (not loaded or loaded == STOCK_BUILD) else loaded
    return _POWERED_BY_CACHE


_CHASSIS_CACHE = None


def _chassis_string():
    """One-line rig identity for the DRIVER/POWER/PADDOCK headers — the SoC,
    GPU, and ROCKNIX build the kit is running on. Composed from env (CHIPSET,
    GPU_ADAPTER_STRING via env.sh) + /etc/os-release. Cached; degrades to just
    the SoC if the optional pieces are missing (never blanks)."""
    global _CHASSIS_CACHE
    if _CHASSIS_CACHE is not None:
        return _CHASSIS_CACHE
    soc = _CHIPSET or "SM8250"
    gpu = (os.environ.get("GPU_ADAPTER_STRING", "") or "").strip()
    gpu = re.sub(r"\(TM\)\s*", "", gpu).replace("Turnip", "").strip()
    gpu = re.sub(r"\s+", " ", gpu)
    # ROCKNIX os-release keys the human-legible NIGHTLY DATE on OS_VERSION
    # (e.g. 20260622); BUILD_ID is the git SHA and VERSION can be a hash — do
    # NOT use those (etk_drift.py uses the same OS_VERSION -> VERSION_ID order).
    osr = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    osr[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    osv = osr.get("OS_VERSION") or osr.get("VERSION_ID") or ""
    parts = [soc]
    if gpu:
        parts.append(gpu)
    parts.append(f"ROCKNIX {osv}" if osv else "ROCKNIX")
    _CHASSIS_CACHE = "  ·  ".join(parts)
    return _CHASSIS_CACHE


def _fmt_count(n):
    """Abbreviate a large count the way the in-game HUD does (29500 -> '29.5k',
    1.2M), to save header width. Below 1000 stays exact. Accepts int or str."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1000000:
        return f"{n / 1000000:.2f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _draw_meta_line(stdscr, w, gamepad_status, lap_str):
    # DRIVER now lives in the title bar (row 0); the meta line is the game line.
    target = f"GAME: {GAME_NAME}"
    stdscr.addstr(2, 2, target[:w - 4], curses.A_DIM)
    if w > len(lap_str) + 4 and lap_str:
        stdscr.addstr(2, w - len(lap_str) - 2, lap_str, curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(3, 2, "-" * (w - 4), curses.A_DIM)


def _draw_footer(stdscr, h, w, current_tab, status, tools_mode="menu"):
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
        elif current_tab == CURRENT_TAB_TOOLS and tools_mode != "menu":
            # One level in: A steps back, matching _tools_back's semantics.
            footer = "DPAD UP/DN: Move  B: Select  A: Back  L1/R1: Tabs"
        else:
            # Top-level TOOLS menu: A quits the app (what it actually does).
            footer = "DPAD UP/DN: Move  B: Select  A: Quit  L1/R1: Tabs"
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
    """Tuning matrix editor. List starts at row 4 (just under the GAME/rule meta
    line) to maximize scroll height; the pit-engineer hint band reserves 3 rows
    at the bottom only when the selected field carries help."""
    if ETK_NO_TARGET:
        _draw_inert_panel(stdscr, state, "TUNING")
        return
    matrix = state["matrix"]
    h, w = stdscr.getmaxyx()
    total = len(matrix)

    # Never rest the cursor on a section header (index 0 is one whenever the
    # CORE surface is live). Normalize here so every entry path — first draw,
    # tab return — lands on a real field without each caller knowing.
    active_idx = state["cursor_idx"] % max(1, total)
    for _ in range(total):
        if matrix[active_idx].get("type") != "header":
            break
        active_idx = (active_idx + 1) % total
    state["cursor_idx"] = active_idx

    # The position counter counts FIELDS, not rows — headers are furniture.
    fields = [i for i, it in enumerate(matrix) if it.get("type") != "header"]
    pos = (fields.index(active_idx) + 1) if active_idx in fields else 1
    setting = f"SETTING {pos:02d}/{len(fields):02d}"
    _draw_meta_line(stdscr, w, state["gamepad_status"], setting)

    start_y = 4
    # Reserve a pit-engineer hint band just above the footer separator (h-3):
    # a faint rule + up to 2 wrapped lines explaining the SELECTED field. Only
    # claimed when the active field actually carries help, so a schema without
    # help text renders the full-height list exactly as before.
    help_text = (matrix[active_idx].get("help") or "").strip() if matrix else ""
    # Only claim the 4-row band (rule + 3 wrapped text lines) when the panel is
    # tall enough to also render it (matches the h-7 > start_y guard below), so a
    # short terminal keeps its list. The 3rd text line costs 1 row of list scroll.
    help_band = 4 if (help_text and (h - 7) > start_y) else 0
    capacity = max(1, (h - 3) - start_y - help_band)

    if total <= capacity:
        offset = 0
    else:
        offset = min(max(0, active_idx - capacity // 2), total - capacity)

    for row, idx in enumerate(range(offset, min(offset + capacity, total))):
        item = matrix[idx]
        y = start_y + row

        # Section headers render as a dim rule, unselectable by construction.
        if item.get("type") == "header":
            rule = f"-- {item['label']} " + "-" * max(0, w - 14 - len(item["label"]))
            try:
                stdscr.addstr(y, 4, rule[:max(0, w - 6)], curses.A_DIM | curses.A_BOLD)
            except curses.error:
                pass
            continue

        is_selected = (idx == active_idx)
        prefix = "> " if is_selected else "  "
        attr = curses.A_REVERSE if is_selected else curses.A_NORMAL

        stdscr.addstr(y, 4, prefix, curses.color_pair(1) if is_selected else curses.A_NORMAL)
        stdscr.addstr(y, 8, f"{item['label']:<30}")

        if item.get("kind") == "core":
            # Catalog filenames are ~60 chars; show the compact build label
            # (same compaction the ledger tune_tag uses).
            val_str = f"[ {_core_label(item['options'][item['enum_idx']])} ]"
        elif item["type"] == "enum":
            val_str = f"[ {item['options'][item['enum_idx']]} ]"
        elif item["type"] == "bool":
            val_str = f"[ {'ON' if item['current_val'] else 'OFF'} ]"
        else:
            val_str = f"[ {item['current_val']} ]"

        # Clip to the pane so a long value can never throw addstr off the
        # right edge (curses raises on out-of-window writes).
        try:
            stdscr.addstr(y, 40, val_str[:max(0, w - 42)], attr)
        except curses.error:
            pass

    # Pit-engineer hint band: explain the SELECTED field (what it does, the
    # effect, the tradeoff) so the tuner teaches instead of just listing knobs.
    if help_text and h - 7 > start_y:
        avail = max(10, w - 22)  # text renders from col 18; keep wrap within it
        words, lines, cur = help_text.split(), [], ""
        for word in words:
            if len(cur) + len(word) + (1 if cur else 0) <= avail:
                cur = f"{cur} {word}".strip()
            else:
                lines.append(cur); cur = word
                if len(lines) == 3:
                    break
        if cur and len(lines) < 3:
            lines.append(cur)
        if len(lines) == 3 and cur != lines[2]:
            lines[2] = (lines[2][:avail - 1] + "…")
        stdscr.addstr(h - 7, 2, "-" * (w - 4), curses.A_DIM)
        stdscr.addstr(h - 6, 4, "PIT ENGINEER", curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(h - 6, 18, lines[0][:w - 20], curses.A_DIM)
        if len(lines) > 1:
            stdscr.addstr(h - 5, 18, lines[1][:w - 20], curses.A_DIM)
        if len(lines) > 2:
            stdscr.addstr(h - 4, 18, lines[2][:w - 20], curses.A_DIM)

    # Scroll telltales — race shift-light chevrons parked on the rules.
    # Drawn last so they sit on top of the header/footer separator lines.
    if w > 16:
        if offset > 0:
            stdscr.addstr(3, w - 12, " /\\ MORE ", curses.color_pair(1) | curses.A_BOLD)
        if offset + capacity < total:
            stdscr.addstr(h - 3 - help_band, w - 12, " \\/ MORE ", curses.color_pair(1) | curses.A_BOLD)


def _adjust_item(item, direction):
    """Shared value adjustment for keyboard and gamepad. Headers fall
    through every branch (no-op) by construction."""
    if item["type"] == "int":
        delta = item["step"] * direction
        item["current_val"] = max(item["min"], min(item["max"], item["current_val"] + delta))
    elif item["type"] == "bool":
        item["current_val"] = not item["current_val"]
    elif item["type"] == "enum":
        item["enum_idx"] = (item["enum_idx"] + direction) % len(item["options"])


def _tuning_step(state, matrix, delta):
    """Move the cursor, skipping section headers (wrap-around preserved).
    Bounded scan so an all-header matrix can never loop forever."""
    idx = state["cursor_idx"]
    for _ in range(len(matrix)):
        idx = (idx + delta) % len(matrix)
        if matrix[idx].get("type") != "header":
            break
    state["cursor_idx"] = idx


def _tuning_save(state):
    """Save, verify, emit CONFIG ledger row(s) on success. Returns the
    verb for the main loop: 'save_exit' on success (matches pre-tabs
    behavior of leaving the editor once the write is proven), 'continue'
    on failure (status string set, stay in tab so the operator can react).

    Multigame lane: the matrix may carry non-YAML rows. Section headers are
    skipped outright; the CORE row diverts to core_map.tsv (its own atomic
    write + read-back verify) BEFORE the YAML commit, so a core-pin failure
    surfaces without half-saving. Both stores succeeding = one save."""
    matrix = state["matrix"]
    pending = _diff_matrix(matrix)
    core_msg = ""
    for item in matrix:
        if item.get("kind") != "core":
            continue
        pick = item["options"][item["enum_idx"]]
        if pick == item.get("original_render"):
            continue
        try:
            ok = _core_map_write(TARGET_ID, pick)
        except OSError as e:
            _log(f"core map write failed: {e.__class__.__name__}: {e}")
            ok = False
        if not ok:
            state["status"] = "CORE PIN FAILED (core_map.tsv write)"
            return "continue"
        core_msg = f"  CORE={_core_label(pick)} next launch"
    # PATCH toggles: apply every changed row to the enablement tree in one
    # read-modify-write of RPCS3's patch_config.yml, then refresh the
    # per-serial pin TSV (the wrapper's tune_tag feed) from the FINAL state
    # of all patch rows. Same next-launch cadence as the config YAML.
    patch_items = [i for i in matrix if i.get("kind") == "patch"]
    patch_dirty = [i for i in patch_items
                   if _render_value(i) != i.get("original_render")]
    if patch_dirty and _patchlib is not None:
        try:
            tree, _dropped = _patchlib.read_patch_config(PATCH_CONFIG_YML)
            for it in patch_dirty:
                _patchlib.set_patch_enabled(tree, it["patch"], TARGET_ID,
                                            bool(it["current_val"]))
            ok = _patchlib.write_patch_config(PATCH_CONFIG_YML, tree)
        except Exception as e:
            _log(f"patch config write failed: {e.__class__.__name__}: {e}")
            ok = False
        if not ok:
            state["status"] = "PATCH SAVE FAILED (patch_config.yml)"
            return "continue"
        try:
            slugs = [_patchlib.patch_slug(i["patch"]["description"])
                     for i in patch_items if i["current_val"]]
            _patchlib.write_patch_pins(PATCH_PINS_FILE, TARGET_ID, slugs)
        except Exception as e:
            # The engine-side enable stands; only the ledger tag feed failed.
            _log(f"patch pins write failed: {e.__class__.__name__}: {e}")
        n_on = sum(1 for i in patch_items if i["current_val"])
        core_msg += f"  PATCHES={n_on} on, next launch"
    yaml_items = [i for i in matrix
                  if i.get("type") != "header" and not i.get("kind")]
    ok, status = commit_and_verify(yaml_items)
    state["status"] = status + core_msg
    if ok:
        # Q3(a): emit CONFIG ledger rows immediately on success. Ledger
        # write failure is logged but does NOT fail the save — a
        # side-effect file shouldn't lose the user a real save. The CORE
        # pin rides the same ledger (label "Emulator Core", raw tokens).
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
        _tuning_step(state, matrix, -1)
        state["status"] = ""
    elif ch == curses.KEY_DOWN:
        _tuning_step(state, matrix, 1)
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
            _tuning_step(state, matrix, -1)
            state["status"] = ""
        elif val == 1:
            _tuning_step(state, matrix, 1)
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
                        # ft_jitter_ms (col 23) — the road-feel chase metric: mean
                        # |Δframetime| over adjacent race frames. len-guarded so
                        # older rows (no jitter col) read 0 and render "----".
                        "ft_jitter_ms": float(fields[22]) if len(fields) > 22 and fields[22] else 0.0,
                        # Fable's Challenge KPI (cols 28-29, 2026-07-05):
                        # lock_pct = share of gameplay frames in the 15.5-18.0ms
                        # locked-60 window; perfect_pct = share of 5s windows
                        # >=95% locked with no >40ms hitch — THE winning metric.
                        # len-guarded; older rows read 0 and render "----".
                        "lock_pct": float(fields[27]) if len(fields) > 27 and fields[27] else 0.0,
                        "perfect_pct": float(fields[28]) if len(fields) > 28 and fields[28] else 0.0,
                        # Full split kept verbatim for the detail card's RAW
                        # LEDGER ROW dump — surfaces every stamped column,
                        # incl. the ones the analytics doesn't (aud/snd/rescues/
                        # gpu_fault/pwr).
                        "raw_fields": fields,
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
    if status.startswith("SURVIVED:"):
        # Keepalive absorbed the hang and the session ran on to a graceful
        # exit — green like CLEAN, unbolded so the absorbed fault is visible.
        return curses.color_pair(PAIR_CLEAN)
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
                stdscr.addstr(3, w - 11, "[^ DPAD]", curses.A_DIM)
            if state.get("_detail_content_h", 0) - sc > bot:
                stdscr.addstr(bot, w - 11, "[v DPAD]", curses.A_DIM)
        except curses.error:
            pass
        return

    # === CAREER ANCHOR ===
    y = 4   # right under the meta rule (row 3); the blank row-4 break is dropped
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
            f"{_fmt_count(career.get('total_shaders', '0'))} shaders banked"
            f"  *  +{career.get('avg_shaders_per_session', '0')} avg/session"
            f"  *  streak {career.get('current_streak', '0')} (best {career.get('best_streak', '0')})"
        )
        stdscr.addstr(y, 2, line2[:w - 4], curses.A_DIM)
        y += 1
        # LINE 3 — the new performance benchmarks to strive for: average run
        # length, race fps, and frame-pacing jitter. fps/jitter show only once
        # the MangoHud instrument has logged real race frames (else they're 0).
        avg_fps = str(career.get('avg_fps', '0'))
        avg_jit = str(career.get('avg_jitter', '0'))
        line3 = f"avg {career.get('avg_duration_human', '0m')}/run"
        if avg_fps not in ('', '0', '0.0'):
            line3 += f"  *  {avg_fps} fps"
        if avg_jit not in ('', '0', '0.0'):
            line3 += f"  *  {avg_jit}ms jitter"
        stdscr.addstr(y, 2, line3[:w - 4], curses.A_DIM)
        y += 1

    # (career-block bottom rule + breather removed — the session table draws its
    # own column-header + rule below, reclaiming 2 ledger rows of scroll height.)

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
# JITTER replaced TEMP as the visible chase metric (2026-06-24): thermals are a
# solved, uninteresting axis; frame-pacing jitter is the new road-feel target.
# peak_temp/avg_temp remain STORED (ledger cols 8-9, never sheared) and surface
# in the session-detail card for thermal forensics — just not as the table headline.
_TEL_W_JITTER = 7
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
# Jitter (mean |Δframetime| ms) — fuller = WORSE (slushy). ~30ms of frame-to-
# frame wobble is severe judder; smooth race feel sits in the low single digits.
_GAUGE_JITTER_MAX = 30
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
        f"  {'JITTER':>{_TEL_W_JITTER}}"
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

    jitter = row.get("ft_jitter_ms", 0.0)
    jitter_str = f"{jitter:.1f}" if jitter > 0 else "----"

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
        f"  {jitter_str:>{_TEL_W_JITTER}}"
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


_LEDGER_SCHEMA = (
    "epoch", "dur_s", "tier", "game", "status", "peak_load", "ram_mb",
    "temp_pk", "temp_avg", "crash_sig", "fence", "shaders", "drain%",
    "therm_ovr", "tune_tag", "crash_shot", "fps_med", "fps_1low", "ft_p99",
    "res%", "gpu_mhz", "pwr", "jitter_ms", "gpu_fault", "fault_fence",
    "aud", "snd", "lock%", "perfect%", "rescues",
)


def _draw_raw_ledger(put, y, w, raw):
    """Bottom-of-card dump of every column in the ledger row, two per line, so
    the top of the card is the story (gauges + advice) and the bottom is the raw
    data. Labels track $SESSIONS_LEDGER's column order (session_postmortem.sh);
    extra trailing columns beyond the schema still print under a numeric label."""
    put(y, 2, "RAW LEDGER ROW", curses.A_BOLD); y += 1
    cw = max(22, (w - 8) // 2)
    cells = []
    for i in range(max(len(_LEDGER_SCHEMA), len(raw))):
        lbl = _LEDGER_SCHEMA[i] if i < len(_LEDGER_SCHEMA) else f"col{i}"
        val = raw[i] if i < len(raw) else "-"
        cells.append(f"{lbl}={val}")
    for j in range(0, len(cells), 2):
        left = cells[j][:cw - 1].ljust(cw)
        right = cells[j + 1][:cw - 1] if j + 1 < len(cells) else ""
        put(y, 4, (left + right).rstrip(), curses.A_DIM); y += 1
    return y


def _draw_session_detail(stdscr, state, sessions, config_changes):
    """Full-screen card for the cursor-selected row (telemetry_mode='detail').
    CLEAN/ABORTED -> ASCII gauges; RECOVERY/PANIC -> crash_signatures.json
    summary/explanation + fence + suggested fix, degrading gracefully when no
    signature matches (R3_PANIC / PANIC_REBOOT / empty). B returns to table."""
    h, w = stdscr.getmaxyx()
    merged = sorted(list(sessions) + list(config_changes),
                    key=lambda r: r["epoch"], reverse=True)
    cur = state.get("telemetry_cursor", 0)
    y = 3   # start just under the tab row (detail mode draws no meta line)
    # Scroll window: the detail card can exceed the panel height (the G-INSTR
    # FRAMERATE gauges pushed a CLEAN card past the bottom), so render through a
    # scroll offset. put() tracks the content extent into _detail_content_h and
    # clips to the visible window; handle_telemetry_pad/_kb scroll via D-pad and
    # clamp against what put() measured. Same cursor-window spirit as the
    # DRIVER-tab scroll fix (587b652), minus the cursor (a card has none).
    _D_TOP, _D_BOT = 3, h - 4
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
        # Anti-lock context (col 30): keepalive rescues absorbed before the stop
        # — the crash-net era's headline, surfacing what the old advice couldn't:
        # many wedges are now caught, not fatal.
        try:
            _nres = (int(row["raw_fields"][29])
                     if len(row.get("raw_fields", [])) > 29 and row["raw_fields"][29]
                     else 0)
        except (ValueError, IndexError):
            _nres = 0
        if _nres > 0:
            put(y, 4, f"Anti-lock absorbed {_nres} rescue"
                      f"{'s' if _nres != 1 else ''} this session before the stop.",
                curses.color_pair(PAIR_CLEAN)); y += 2
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
        # Adreno "traction control": the DRIVER-tab dial is the primary lever for
        # the GPU hang (the TUNING knobs only ease load), so it leads the advice.
        dials, dseen = [], set()
        for m in _matched:
            d = (m.get("driver_dial") or "").strip()
            if d and d not in dseen:
                dseen.add(d); dials.append(d)
        if dials:
            put(y, 2, "TRACTION CONTROL  (DRIVER tab):",
                curses.color_pair(PAIR_CLEAN) | curses.A_BOLD); y += 1
            for d in dials:
                wl = _wrap_text(d, w - 10, max_lines=2)
                for i, ln in enumerate(wl):
                    put(y, 4, ("* " + ln) if i == 0 else ("  " + ln)); y += 1
        if changes:
            put(y, 2, "SUGGESTED FIX  (TUNING tab):",
                curses.color_pair(PAIR_CLEAN) | curses.A_BOLD); y += 1
            for ch in changes:
                put(y, 4, f"* {ch.get('yaml_key','').strip()}  ->  {ch.get('new_value','')}"); y += 1
    else:
        put(y, 4, f"Duration   {dur_h}", curses.A_BOLD); y += 1
        put(y, 4, f"Shaders +  {_fmt_count(row['shaders_harvested'])}    (vault delta this run)"); y += 2
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
            # jitter = mean |Δframetime| (road-feel; fuller bar = slushier).
            jitter = row.get("ft_jitter_ms", 0.0)
            if jitter > 0:
                put(y, 4, f"{'jitter':<8}{(format(jitter, '.1f')+'ms |dft|'):<20}"
                          f"{_gauge_bar(jitter, _GAUGE_JITTER_MAX)}"); y += 1
        # FABLE'S CHALLENGE (cols 28-29) — deliberately OUTSIDE the fps_med
        # gate: the race-gated fps cols are 0 on a locked-60 session (GT HD
        # Eiger lesson), which is exactly when these matter most. Fuller bar
        # = closer to the lock; the KPI is perfect_pct at res 100.
        lock_pct = row.get("lock_pct", 0.0)
        if lock_pct > 0:
            y += 1
            # Per-title target (2026-07-05): GT HD = locked 60 / 16.7ms;
            # GT5P family = locked 30 / 33.3ms. The row's game_id implies
            # which window its lock/PERFECT numbers were scored against.
            put(y, 4, "FABLE'S CHALLENGE  (frame-time lock)",
                curses.color_pair(PAIR_CLEAN) | curses.A_BOLD); y += 1
            put(y, 4, f"{'locked':<8}{(format(lock_pct, '.1f')+'% frames'):<20}"
                      f"{_gauge_bar(lock_pct, 100)}"); y += 1
            perfect = row.get("perfect_pct", 0.0)
            put(y, 4, f"{'PERFECT':<8}{(format(perfect, '.1f')+'% windows'):<20}"
                      f"{_gauge_bar(perfect, 100)}"); y += 1
        # Tune context rides with EITHER instrument block (a locked-60 session
        # has fps_med 0 but its res/clk attribution matters just as much).
        if fps_med > 0 or lock_pct > 0:
            res = row.get("res_scale", 0)
            clk = row.get("gpu_mhz", 0)
            ctx = []
            if res:
                ctx.append(f"{res}% res")
            if clk:
                ctx.append(f"{clk}MHz")
            if ctx:
                put(y, 4, "Tune:    " + "  ".join(ctx), curses.A_DIM); y += 1

    # RAW LEDGER ROW — every stamped column, below the analytics (top = the
    # story, bottom = the raw data). Both the crash and clean/survived cards
    # fall through here; config rows returned earlier, so raw_fields is present.
    raw = row.get("raw_fields")
    if raw:
        y += 1
        rule(y); y += 1
        y = _draw_raw_ledger(put, y, w, raw)


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
            # D-pad UP previews the crash frame when at the top of the card (the
            # natural first press — restores the documented behavior the scroll
            # remap had stolen); once scrolled into a long card, UP scrolls back.
            if state.get("telemetry_detail_scroll", 0) <= 0:
                _preview_crash_frame(state, (state.get("_detail_row") or {}).get("crash_shot", ""))
            else:
                state["telemetry_detail_scroll"] -= 1
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
# HEADLESS since 0.8.1 — brought to parity with the firmware installer.
# The old model ("RPCS3 has no headless install") was FALSE: reading the
# fork source, rpcs3.cpp:1211 routes BOTH --installfw and --installpkg
# through the non-GUI branch when the app is a headless_application, and
# `return 0`s straight after. Every prompt downstream is mw-gated
# (main_window.cpp:941 boot confirm, :1003 pkg_install_dialog, :1073
# progress dialog, :1322 error boxes), so with mw == nullptr RPCS3 builds
# the package list itself, extracts, logs a machine-readable verdict per
# package, and exits. So: no window poll, no /dev/uinput Enter tap, no
# sway focus juggling, no screen handoff — Pitstop keeps its own spinner
# up throughout, exactly like the .pup path.
#
# The verdict comes from RPCS3's OWN log, never from watching the
# filesystem. The old dir-diff + size-stable heuristic is what produced
# the 2026-07-24 field false-failure: GT HD Concept finished extracting
# at 0:00:42 and installed correctly, but RPCS3's GUI branch does not
# self-exit, and the extraction had landed in a tree Pitstop wasn't
# watching — so the installer polled an empty directory for its full
# 600 s cap and then reported "Install did not complete". No .psn was
# written and the game stayed invisible in ES.
#
# Uninstall is a pure filesystem rm. Progress is surfaced through mako
# notifications (dbus-send).
# ============================================================

# Manage Shaders leads the list now that stability work has made vault hygiene
# the most-reached tool (ROADMAP). Every entry is constant-indexed so the order
# is a one-line edit here with no hardcoded literals in the dispatch below.
_TOOLS_MENU = ["Manage Shaders", "Install a staged PS3 Package",
               "Uninstall a Game", "Trigger Calibration", "Screenshot on L1",
               "Install PS3 Firmware", "Check for ETK Updates"]
_TOOLS_SHADERS_IDX = 0      # Manage Shaders sub-screen entry
_TOOLS_INSTALL_IDX = 1      # staged-PKG installer
_TOOLS_UNINSTALL_IDX = 2    # game uninstaller
_TOOLS_TRIGCAL_IDX = 3      # L2/R2 trigger deadzone calibration (H7)
# In-place toggle item (label gets ": <mode>" appended at draw).
_TOOLS_SCREENSHOT_IDX = 4
_TOOLS_FIRMWARE_IDX = 5     # headless PS3 firmware (PS3UPDAT.PUP) installer
_TOOLS_UPDATE_IDX = 6       # hostless self-update (middleware layer)


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


# ==========================================================
# NOTIFICATION SURFACES (aligned to EmulationStation, mako 1.10.0)
# ==========================================================
# ROCKNIX's EmulationStation has exactly two notification surfaces, and the
# kit now mirrors them one-for-one so ETK reads as part of the system
# instead of a bolted-on style of its own:
#
#   TOAST     -> ES's GuiInfoPopup. Top-center, ~10s, centered text. This is
#                the VERDICT of a job ("INSTALL COMPLETE"), never its
#                progress. Shares the anchor with ROCKNIX's own volume /
#                brightness toasts, deliberately: same place, same manners.
#   PROGRESS  -> ES's AsyncNotificationComponent — the card the Scraper puts
#                in the upper-right. Left-aligned title + name, carries a
#                progress bar, lives exactly as long as the job, and is
#                DISMISSED at completion rather than left to fade (ES closes
#                its card and posts a separate popup; copying that grammar
#                is most of what makes this feel native).
#
# These strings are mako CRITERIA KEYS: mako matches app-name with a
# byte-exact strcmp (criteria.c:75), so they must equal the [app-name=...]
# sections install.sh writes into /storage/.config/mako/config. A mismatch
# does not error — it silently falls through to the stock black system
# style, which is why the two live here as constants rather than literals.
NOTIFY_APP = "ETK"
NOTIFY_APP_PROGRESS = "ETK Progress"

# Progress cards send an explicit, finite timeout and are re-posted on a
# heartbeat well inside it. That is deliberate: a never-expiring card would
# outlive a crashed installer and pin itself on screen until the next
# reboot. Each replace restarts mako's timer, so a live job holds the card
# open and a dead one lets it fall off by itself.
NOTIFY_PROGRESS_TTL = 20000


class _Notifier:
    """Posts mako notifications, reusing one notification id (replaces_id)
    so an update rewrites ONE toast in place instead of stacking a column.
    Best-effort throughout — a notification failure must never abort an
    install.

    Two transports, because the rig's `dbus-send` cannot marshal a nested
    a{sv} (its own man page rules out nested containers) and mako's progress
    bar is driven by the standard `value` hint:

      plain text  -> dbus-send   (proven, always present)
      with a bar  -> busctl      (systemd's bus tool: builds containers from
                                  a signature string)

    The hint MUST be int32. mako reads it as `v` of `i` (dbus/xdg.c:230)
    with no type fallback, so sending uint32 fails the WHOLE Notify call —
    the cost of getting this wrong is the notification, not just the bar.
    Where busctl is missing the bar degrades to an ASCII meter in the body,
    never to a silent toast."""

    _busctl = None      # None = unprobed; True/False = cached capability

    def __init__(self, app=NOTIFY_APP):
        self._id = "0"
        self._app = app
        self._env = _tools_env()

    @classmethod
    def _have_busctl(cls):
        if cls._busctl is None:
            cls._busctl = bool(shutil.which("busctl"))
            if not cls._busctl:
                _log("notify: busctl absent — progress bars fall back to ASCII")
        return cls._busctl

    def post(self, summary, body="", timeout=8000, value=None):
        """Post (or replace) this notifier's toast. `value` is 0..100 and
        draws mako's native progress bar; None leaves the bar off. Note that
        mako CLEARS the bar on any replace that omits the hint, so a live
        progress card must pass `value` on every single update."""
        try:
            if value is None:
                cmd = ["dbus-send", "--session", "--print-reply",
                       "--dest=org.freedesktop.Notifications",
                       "/org/freedesktop/Notifications",
                       "org.freedesktop.Notifications.Notify",
                       "string:" + self._app, "uint32:" + self._id, "string:",
                       "string:" + summary, "string:" + body,
                       "array:string:", "dict:string:variant:",
                       "int32:%d" % timeout]
            elif self._have_busctl():
                pct = max(0, min(100, int(value)))
                # susssasa{sv}i — actions = empty array (0), hints = one
                # entry (1) "value" as variant int32.
                cmd = ["busctl", "--user", "call",
                       "org.freedesktop.Notifications",
                       "/org/freedesktop/Notifications",
                       "org.freedesktop.Notifications", "Notify",
                       "susssasa{sv}i",
                       self._app, self._id, "", summary, body,
                       "0", "1", "value", "i", str(pct), str(timeout)]
            else:
                pct = max(0, min(100, int(value)))
                meter = _ascii_bar(pct / 100.0)
                return self.post(summary,
                                 f"{body}  {meter}" if body else meter,
                                 timeout=timeout)
            r = subprocess.run(cmd, env=self._env, capture_output=True,
                               text=True, timeout=10)
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                # dbus-send prints "   uint32 7"; busctl prints "u 7".
                if ln.startswith("uint32") or ln.startswith("u "):
                    tok = ln.split()
                    if len(tok) > 1 and tok[1].isdigit():
                        self._id = tok[1]
                    break
        except Exception as e:
            _log(f"mako notify failed: {e}")

    def close(self):
        """Dismiss this notifier's toast NOW. The progress card has to go the
        instant its job ends — that is what ES does, and a card left to fade
        would report work that has already finished. CloseNotification takes
        a bare uint32, so plain dbus-send can call it."""
        if not self._id or self._id == "0":
            return
        try:
            subprocess.run(
                ["dbus-send", "--session",
                 "--dest=org.freedesktop.Notifications",
                 "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications.CloseNotification",
                 "uint32:" + self._id],
                env=self._env, capture_output=True, text=True, timeout=10)
            self._id = "0"
        except Exception as e:
            _log(f"mako dismiss failed: {e}")


def _progress_notifier():
    """A notifier bound to the upper-right progress surface."""
    return _Notifier(NOTIFY_APP_PROGRESS)


class _ProgressCard:
    """ES's upper-right scraper card, driven from a background thread.

    A long op that blocks (RPCS3 installing a package, a git sync) cannot
    pump its own notification, and mako expires a toast the moment its
    timeout lapses — so the card is held open by a heartbeat well inside
    NOTIFY_PROGRESS_TTL. Every re-post carries the hint again because mako
    clears the bar on any replace that omits it.

    `frac` is an optional callable returning 0.0..1.0. With one, the card
    shows a real bar and percentage; without one it shows elapsed time and
    no bar — which is exactly what ES does when a job reports no percentage
    (AsyncNotificationComponent hides the bar while mPercent < 0). Elapsed
    time is honest movement; a fabricated percentage would not be.

    Fail-soft by construction: the worker only ever touches the notifier, so
    a broken notification path costs a card, never the install."""

    def __init__(self, title, name="", frac=None, interval=1.5):
        self._title = title
        self._name = name
        self._frac = frac
        self._interval = interval
        self._notifier = _progress_notifier()
        self._stop = threading.Event()
        self._thread = None
        self._t0 = time.time()

    def _body(self):
        if self._frac is None:
            el = int(time.time() - self._t0)
            stamp = f"{el // 60}:{el % 60:02d}"
            return f"{self._name}  {stamp}" if self._name else stamp
        return self._name

    def _tick(self):
        value = None
        if self._frac is not None:
            try:
                f = self._frac()
                value = None if f is None else int(max(0.0, min(1.0, f)) * 100)
            except Exception:
                value = None
        title = self._title if value is None else f"{self._title}  {value}%"
        self._notifier.post(title, self._body(),
                            timeout=NOTIFY_PROGRESS_TTL, value=value)

    def _run(self):
        while not self._stop.wait(self._interval):
            self._tick()

    def start(self):
        # Every step here is best-effort: this runs on the critical path of an
        # install, and a card that cannot be drawn must cost a card, not the
        # install.
        try:
            self._tick()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception as e:
            _log(f"progress card start failed: {e}")
        return self

    def stop(self):
        """Close the card. ES dismisses its card the instant the job ends and
        announces the outcome as a separate top-center popup — leaving the
        card up would keep reporting work that is already over.

        Safe to call twice: the installers stop the card explicitly before
        posting their verdict AND again in a finally, so the card cannot
        outlive a failure path."""
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=3)
                self._thread = None
            self._notifier.close()
        except Exception as e:
            _log(f"progress card stop failed: {e}")


def _es_reload_gamelists():
    """Ask EmulationStation to rescan its gamelists so a freshly installed
    game simply appears — the operator no longer has to know about
    START > Game Settings > Update Gamelists.

    ES serves an HTTP API on 127.0.0.1:1234 whenever it is running (compiled
    in unconditionally; localhost is always allowed regardless of the public
    web-access setting). The rescan is queued onto ES's UI thread, and posted
    work only runs inside Window::update() — so it executes on the first ES
    frame AFTER Pitstop exits, never while the terminal is up. Use the
    numeric address: the listener is IPv4-only and `localhost` can resolve to
    ::1 first. Fail-silent — no ES, no problem."""
    try:
        subprocess.run(["curl", "-fsS", "-m", "5",
                        "http://127.0.0.1:1234/reloadgames"],
                       capture_output=True, timeout=8)
        _log("ES gamelist reload requested")
    except Exception as e:
        _log(f"ES gamelist reload skipped: {e}")


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
        _Notifier().post("CRASH FRAME SAVED", shot[:40])
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


# --- sway window helpers ------------------------------------
# Retained for the crash-frame preview (swayimg). The installers no
# longer touch sway at all — see the section header.

def _swaymsg(args, env):
    try:
        return subprocess.run(["swaymsg", *args], env=env,
                              capture_output=True, text=True,
                              timeout=10).stdout
    except Exception as e:
        _log(f"swaymsg failed: {e}")
        return ""


def _restore_pitstop_window(env):
    """Re-assert the Pitstop (foot) window as fullscreen. The swayimg
    crash-frame preview knocks foot out of fullscreen in sway's tree, which
    would otherwise leave the curses UI tiled and clipped off-screen on
    return. The next main-loop _draw picks up the restored size via
    getmaxyx(). (The installers are headless now and map no window, so they
    no longer need this — it is the preview path's fix.)"""
    try:
        _swaymsg(['[app_id="foot"]', 'fullscreen', 'enable'], env)
        time.sleep(0.3)
    except Exception as e:
        _log(f"restore pitstop window failed: {e}")


_PREVIEW_APPID = "etk-crash-preview"


def _fullscreen_preview(env):
    """Once the swayimg preview window maps, fullscreen it so it covers the
    foot terminal cleanly (no tiling split DURING the preview). Brief blocking
    poll; breaks as soon as the window appears."""
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


# An installer and a game are BOTH RPCS3 processes, and once installs run in
# the background the two can be live at the same moment. Telling them apart by
# cmdline is what makes that safe: it is how the Sentry keeps recording a real
# race while an install runs, and how an install timeout kills its OWN emulator
# instead of the operator's game. The headless installer always carries one of
# these flags; a game launch never does.
_RPCS3_PATTERNS = ("rpcs3-sa", "AppRun.wrapped")
_INSTALLER_FLAGS = ("--installpkg", "--installfw")


def _rpcs3_pids(installer=None, procfs="/proc"):
    """PIDs of live RPCS3 processes.

    installer=True  -> only headless install instances
    installer=False -> only real game sessions
    installer=None  -> every RPCS3

    `procfs` exists so the discrimination can be tested against a fixture
    tree; nothing in the kit passes it.

    Reads /proc directly rather than shelling to pgrep: we need each match's
    cmdline anyway, and this cannot self-match (Pitstop's and the worker's own
    argv carry neither pattern)."""
    out = []
    try:
        entries = os.listdir(procfs)
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(procfs, entry, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue          # process exited between listdir and open
        # argv is NUL-separated. Split it and match the flags as WHOLE TOKENS:
        # a substring test would misread a game whose ROM path happened to
        # contain "--installfw" as an installer, and the Sentry would then give
        # that whole session no telemetry.
        argv = [a for a in raw.decode("utf-8", "ignore").split("\0") if a]
        if not argv:
            continue          # kernel thread or zombie
        # Match argv[0] — the EXECUTABLE — not "any argument contains rpcs3".
        # The AppImage spawns a dwarfs FUSE helper whose argv carries the
        # image's own path (".../rpcs3-sa.custom") but none of the install
        # flags, so an any-argument test saw the installer's own mount helper
        # as a GAME: the worker killed its install 2s in, requeued, and looped
        # forever between INSTALL FAILED and INSTALL PAUSED (live on the rig
        # 2026-08-06). Only the emulator itself has an rpcs3 argv[0].
        if not any(p in argv[0] for p in _RPCS3_PATTERNS):
            continue
        is_installer = any(a in _INSTALLER_FLAGS for a in argv)
        if installer is None or is_installer == installer:
            out.append(int(entry))
    return out


def _game_running():
    """True when a REAL game session is live (a background install is not).
    This is the guard installs must use — an installer must never mistake
    itself for a reason to refuse."""
    return bool(_rpcs3_pids(installer=False))


def _kill_installer_rpcs3():
    """Terminate only the headless install instances, by PID.

    NEVER pattern-kill from an install path: `pkill -f rpcs3-sa` also matches
    a game the operator has just launched, so an install timing out would take
    their race down with it. SIGTERM first so RPCS3 can unwind, SIGKILL only
    for what refuses."""
    pids = _rpcs3_pids(installer=True)
    for sig in (15, 9):
        alive = []
        for pid in pids:
            try:
                os.kill(pid, sig)
                alive.append(pid)
            except OSError:
                pass          # already gone
        if not alive:
            return
        pids = alive
        time.sleep(2 if sig == 15 else 0.5)


def _kill_rpcs3():
    """Force-kill EVERY RPCS3 process, game included. Blunt by design and kept
    for the recovery paths that mean it; install paths want
    _kill_installer_rpcs3() instead."""
    for pat in _RPCS3_PATTERNS:
        try:
            subprocess.run(["pkill", "-9", "-f", pat],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    time.sleep(1)


def _rpcs3_running():
    """True when ANY RPCS3 is live (install or game). Used by the paths that
    must not touch shared state while the emulator has it open."""
    return bool(_rpcs3_pids())


# --- background install queue -------------------------------

def _worker_alive():
    try:
        with open(INSTALL_WORKER_PID) as f:
            pid = int(f.read().strip() or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False          # stale pidfile — the worker died or SHM survived


def _install_queue():
    """Queued jobs, oldest first, as (seq_filename, job dict)."""
    out = []
    try:
        names = sorted(f for f in os.listdir(INSTALL_QUEUE_DIR)
                       if f.endswith(('.job', '.running')))
    except OSError:
        return out
    for n in names:
        try:
            with open(os.path.join(INSTALL_QUEUE_DIR, n)) as f:
                parts = f.read().rstrip('\n').split('\t')
        except OSError:
            continue
        if len(parts) >= 2:
            out.append((n, {'kind': parts[0], 'path': parts[1],
                            'running': n.endswith('.running')}))
    return out


def _install_status():
    """The worker's current line for the TOOLS tab, or None when idle."""
    try:
        with open(INSTALL_STAT_FILE) as f:
            parts = f.read().rstrip('\n').split('\t')
    except OSError:
        return None
    if not parts or not parts[0]:
        return None
    while len(parts) < 4:
        parts.append('')
    return {'state': parts[0], 'name': parts[1],
            'pct': parts[2], 'msg': parts[3]}


def _install_preflight(kind, path):
    """The refusals that must be answered on-screen, BEFORE a job is queued.

    Split deliberately from the installers' own pre-flight: these are instant
    and the operator is still looking at the Pitstop, so a refusal can be
    read and acted on. The slow half (the possible multi-GB
    _ensure_rpcs3_storage_links migration, dev_flash provisioning) stays
    inside _run_install/_run_install_fw and therefore runs in the worker,
    where it belongs — it is part of the job, not a reason to reject it.

    A running GAME is NOT a refusal any more: that is the whole point of a
    queue. Returns None to proceed, or a list of result lines."""
    if not path or not os.path.isfile(path):
        return ["That file is no longer there.",
                "Re-copy it to the drop folder and try again."]
    incoherent = _storage_incoherent_msg()
    if incoherent:
        return incoherent
    return None


def _enqueue_install(kind, path, rap=None):
    """Queue an install and make sure the worker is running.

    Returns (ok, message). Deduplicates against what is already queued — ES's
    ContentInstaller does the same, and a double-press should not install a
    package twice."""
    dup = False
    try:
        os.makedirs(INSTALL_QUEUE_DIR, exist_ok=True)
        dup = any(job['path'] == path for _, job in _install_queue())
        if not dup:
            # Licences are a LIST (a title can need several, and they are keyed
            # by content id). They ride as TRAILING tab-separated fields —
            # writing the list directly would serialise its Python repr, and
            # the installer would then treat that text as one licence path,
            # failing every copy while still reporting the install complete.
            raps = [rap] if isinstance(rap, str) else list(rap or [])
            # The sequence number is the queue order; the worker takes the
            # lowest. os.link claims the name atomically, so two enqueues
            # inside the same millisecond cannot overwrite one another.
            seq = int(time.time() * 1000)
            tmp = os.path.join(INSTALL_QUEUE_DIR, f".{seq}.{os.getpid()}.tmp")
            with open(tmp, 'w') as f:
                f.write("\t".join([kind, path] + raps) + "\n")
            for bump in range(64):
                try:
                    os.link(tmp, os.path.join(INSTALL_QUEUE_DIR,
                                              f"{seq + bump}.job"))
                    break
                except FileExistsError:
                    continue
            os.remove(tmp)
    except Exception as e:
        _log(f"enqueue failed: {e}")
        return False, f"could not queue: {e.__class__.__name__}"

    # Re-arm OUTSIDE the duplicate check. A job can land in the window between
    # the worker's last look at an empty queue and its pidfile removal, which
    # would leave the queue with nothing draining it; returning early on a
    # duplicate would then make the obvious fix — pressing install again —
    # the one thing that cannot help.
    if not _worker_alive():
        try:
            # Detached on purpose: the install must outlive this Pitstop.
            subprocess.Popen(
                ["python3", f"{ETK_ROOT}/bin/etk_install_worker.py"],
                env=_tools_env(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                start_new_session=True)
        except Exception as e:
            _log(f"worker spawn failed: {e}")
            return False, f"could not start installer: {e.__class__.__name__}"
    return (False, "already queued") if dup else (True, "queued")


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


# --- golden-default config seeding (0.7.1) ------------------
# A disc/ISO title never passes through the PKG installer, so nothing ever
# deployed a per-game config for it: the first ISO on the rig (GT5P disc
# BCUS98158) booted on RPCS3's hostile defaults, and TUNING could not build
# a config from scratch (the section-aware injector refuses to append into
# a file with no sections — by design). These seeders close that gap for
# every title, whichever packaging model it arrived by.

def _games_yml_serials():
    """serial -> ROM path from RPCS3's games.yml (disc/ISO boot
    registrations; installed-PKG titles live under dev_hdd0/game and are
    not listed here). Empty dict on absence or any parse trouble."""
    out = {}
    try:
        with open(RPCS3_GAMES_YML) as f:
            for ln in f:
                if ':' not in ln or ln.lstrip().startswith('#'):
                    continue
                tid, path = ln.split(':', 1)
                tid, path = tid.strip(), path.strip().strip('"')
                if re.fullmatch(r'[A-Z]{4}[0-9]{5}', tid):
                    out[tid] = path
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"golden seed: games.yml parse failed: {e}")
    return out


def _golden_seed_config(tid, disc=False):
    """Seed custom_configs/config_<tid>.yml from the ETK golden template so
    the title never runs on RPCS3 defaults. The template IS the seed for
    BOTH packaging models now — the 0.7.1 disc-only Strict Rendering Mode
    overlay was removed for 0.7.x (a false fix: unvalidated, since refuted
    by the operator; the `disc` arg is kept for log provenance only).
    Never clobbers an existing config; fail-soft on every path (a seeding
    failure must never take Pitstop down — the title just plays untuned,
    exactly as before 0.7.1). Returns a short plain-language status."""
    if not GOLDEN_SEED_ENABLED:
        return "disabled"
    dest = os.path.join(RPCS3_CUSTOM_CONFIGS, f"config_{tid}.yml")
    if os.path.exists(dest):
        return "kept existing"
    try:
        with open(ETK_TEMPLATE_CONFIG) as f:
            lines = f.readlines()
    except Exception as e:
        _log(f"golden seed: template unreadable: {e}")
        return "template missing"
    try:
        os.makedirs(RPCS3_CUSTOM_CONFIGS, exist_ok=True)
        tmp = dest + ".etk.tmp"
        with open(tmp, 'w') as f:
            f.writelines(lines)
        os.replace(tmp, dest)
    except Exception as e:
        _log(f"golden seed: write failed for {tid}: {e}")
        return "failed"
    # Surface the seed in the CONFIG ledger so TELEMETRY shows it as a
    # config event with honest provenance (best-effort; the seed stands
    # even if the ledger append fails).
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_CHANGES_LEDGER):
            ltmp = CONFIG_CHANGES_LEDGER + ".tmp"
            with open(ltmp, 'w') as f:
                f.write(CONFIG_CHANGES_HEADER)
            os.replace(ltmp, CONFIG_CHANGES_LEDGER)
        with open(CONFIG_CHANGES_LEDGER, 'a') as f:
            f.write(f"{int(time.time())}\t{tid}\tGOLDEN SEED\trpcs3-defaults\t"
                    f"golden\n")
    except Exception as e:
        _log(f"golden seed: ledger append failed: {e}")
    _log(f"golden seed: {tid} <- template ({'disc' if disc else 'pkg'})")
    return "seeded"


def _golden_seed_sweep():
    """Seed a golden config for every playable title that has none:
    .psn launchers (installed PKGs) + games.yml serials (disc/ISO).
    Runs once at startup, and only while the emulator is idle — RPCS3
    rewrites configs on exit, so we never race a live session (skipped
    sweep = seeded on the next Pitstop open; fail-soft). Returns the
    list of seeded title IDs for the footer status line."""
    if not GOLDEN_SEED_ENABLED or _rpcs3_running():
        return []
    seeded = []
    discs = _games_yml_serials()
    tids = {g["title_id"] for g in _list_psn_games()} | set(discs)
    for tid in sorted(tids):
        if not re.fullmatch(r'[A-Z]{4}[0-9]{5}', tid or ""):
            continue
        if _golden_seed_config(tid, disc=tid in discs) == "seeded":
            seeded.append(tid)
    return seeded


# --- ISO onboarding (0.7.2) ---------------------------------
# A dropped .iso is invisible to ES (ps3 extensions: .ps3 .psn .m3u) and,
# if bracket-named, invisible to ROCKNIX per-game settings too. This sweep
# is the .iso twin of the PKG installer's onboarding writers above
# (_write_psn / _enable_mangohud / template config): rename, launcher,
# overlay, golden seed — so "copy the ISO over" is the whole install.

# (_ISO_ID_TAG_RE lives at the top of the file with PS3_ROMS_DIR — it is
# needed at import time by resolve_game_name.)


def _iso_serial_from_name(name):
    """Serial from a filename's ID tag, dash-normalized ("[BLUS-30019]" ->
    "BLUS30019"). None when the name carries no tag."""
    m = _ISO_ID_TAG_RE.search(name)
    return (m.group(1) + m.group(2)) if m else None


def _write_m3u(m3u_path, iso_name):
    """Write the ES launcher: one line, the bare .iso filename —
    start_rpcs3.sh reads the first line and prefixes /roms/ps3/ itself.
    Atomic tmp+replace; matches the proven hand-made BCUS98158 launcher."""
    tmp = m3u_path + ".etk.tmp"
    with open(tmp, 'w') as f:
        f.write(iso_name + "\n")
    os.replace(tmp, m3u_path)


def _iso_onboard_sweep():
    """Make every .iso in the ps3 roms dir a first-class ES game:
      1. normalize the filename for get_setting: bracket ID tags become
         parenthesised, dash-stripped form (get_setting regex-escapes ()&
         but not [] — bracketed names silently lose ALL per-game settings)
         and whitespace runs collapse to single spaces (get_setting's
         unquoted expansion eats consecutive spaces — the LBP case); the
         serial stays visible to disambiguate variants, operator-approved;
      2. generate/refresh the <stem>.m3u launcher ES scans for;
      3. enable the per-game MangoHud overlay key (keyed on the .m3u name,
         which is what start_rpcs3.sh passes to get_setting);
      4. golden-seed config_<serial>.yml from the filename serial — works
         before the title's first boot, no games.yml needed.
    Idle-gated and fail-soft per title: one bad filename must never stop
    the rest of the grid from onboarding. Returns plain-language notes."""
    if not ISO_ONBOARD_ENABLED or _rpcs3_running():
        return []
    try:
        entries = sorted(os.listdir(PS3_ROMS_DIR))
    except Exception as e:
        _log(f"iso onboard: roms dir unreadable: {e}")
        return []
    yml_by_base = {os.path.basename(p): s
                   for s, p in _games_yml_serials().items()}
    onboarded, renamed, seeded = [], 0, []
    for entry in entries:
        if entry.startswith('.') or not entry.lower().endswith('.iso'):
            continue
        iso_path = os.path.join(PS3_ROMS_DIR, entry)
        if not os.path.isfile(iso_path):
            continue
        try:
            iso = entry
            # 1. bracket/dash tag normalization rename
            m = _ISO_ID_TAG_RE.search(iso)
            new = iso
            if m:
                new = iso[:m.start()] + f"({m.group(1)}{m.group(2)})" + iso[m.end():]
            new = new.replace('[', '(').replace(']', ')')
            # Collapse whitespace runs: ROCKNIX get_setting expands the ROM
            # name UNQUOTED (`echo ${3}`, 001-functions), so consecutive
            # spaces collapse before the key match and every per-game
            # setting — including the MangoHud overlay — silently misses.
            # Live case: "LittleBigPlanet  (BCUS98148).iso" (root-caused
            # 2026-07-22; upstream report queued).
            new = re.sub(r'\s{2,}', ' ', new)
            if new != iso:
                new_path = os.path.join(PS3_ROMS_DIR, new)
                if os.path.exists(new_path):
                    _log(f"iso onboard: rename target exists, keeping {iso}")
                else:
                    os.rename(iso_path, new_path)
                    # Migrate a stale sidecar launcher so ES never lists a
                    # dead .m3u pointing at the pre-rename filename.
                    old_m3u = os.path.join(PS3_ROMS_DIR, iso[:-4] + ".m3u")
                    if os.path.exists(old_m3u):
                        os.replace(old_m3u,
                                   os.path.join(PS3_ROMS_DIR, new[:-4] + ".m3u"))
                    _log(f"iso onboard: renamed {iso!r} -> {new!r}")
                    iso, iso_path = new, new_path
                    renamed += 1
            stem = iso[:-4]
            # 2. .m3u launcher (create or repair a stale/mismatched one)
            m3u_name = stem + ".m3u"
            m3u_path = os.path.join(PS3_ROMS_DIR, m3u_name)
            content = None
            try:
                with open(m3u_path) as f:
                    content = f.read().strip()
            except Exception:
                pass
            if content != iso:
                _write_m3u(m3u_path, iso)
                _log(f"iso onboard: launcher {m3u_name!r} -> {iso!r}")
                onboarded.append(stem)
            # 3. per-game MangoHud overlay (idempotent upsert, self-heals
            # duplicates; keyed on the .m3u ES actually launches)
            _enable_mangohud(m3u_name)
            # 4. golden seed from the filename serial; games.yml basename
            # lookup covers untagged names that have booted at least once.
            serial = _iso_serial_from_name(iso) or yml_by_base.get(iso)
            if serial and _golden_seed_config(serial, disc=True) == "seeded":
                seeded.append(serial)
        except Exception as e:
            _log(f"iso onboard: {entry!r} failed: {e.__class__.__name__}: {e}")
    if onboarded or renamed:
        _log(f"iso onboard: {len(onboarded)} launcher(s), {renamed} rename(s), "
             f"seeded {seeded or 'none'}")
    return onboarded


# === TOOLS: HOSTLESS SELF-UPDATE (middleware layer) ===
# "Check for ETK Updates": compare APP_VERSION against the latest GitHub
# release tag, and on request update the MIDDLEWARE layer in place — the
# exact file set install.sh STEPs 3/5 deploy (bin/, scripts/, the tools/
# push-list, the config deploy-set, the pro-tuning injector, the live
# MangoHud.conf, the Tools-menu launcher master). The deep stack (Sentry
# heredoc, profile.d, systemd units, kernel/Turnip/RPCS3 binds) stays
# with install.sh / the card image — the result screen says so.
# On-rig GitHub TLS is field-proven (PADDOCK). Default-ON with the
# ETK_SELF_UPDATE=0 kill-switch (env.sh). All paths fail-soft.
ETK_SELF_UPDATE_ENABLED = os.environ.get('ETK_SELF_UPDATE', '1').strip() != '0'
_UPDATE_REPO = "mercurious/etk"


def _semver_tuple(s):
    """'v0.7.2' -> (0, 7, 2); None when unparseable (never raise)."""
    try:
        return tuple(int(p) for p in
                     s.strip().lstrip('vV').split('-')[0].split('.')[:3])
    except (ValueError, AttributeError):
        return None


def _self_update_check():
    """Worker (curses-free): -> ("newer", info) | ("result", (ok, lines))."""
    if not ETK_SELF_UPDATE_ENABLED:
        return ("result", (False, [
            "Self-update is disabled (ETK_SELF_UPDATE=0 in etk.conf)."]))
    if _rpcs3_running():
        return ("result", (False, [
            "A game session is running - update refused.",
            "Exit the game first, then check again."]))
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{_UPDATE_REPO}/releases/latest",
        headers={"User-Agent": "etk-pitstop",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            rel = json.load(r)
    except Exception as e:
        _log(f"self-update: check failed: {e}")
        return ("result", (False, [
            "Could not reach GitHub (offline? rate-limited?).",
            f"  {e.__class__.__name__}",
            "Check WiFi and try again."]))
    tag = (rel.get("tag_name") or "").strip()
    remote, local = _semver_tuple(tag), _semver_tuple(APP_VERSION)
    if not tag or remote is None or local is None:
        return ("result", (False, [
            f"Could not compare versions "
            f"(installed v{APP_VERSION}, latest {tag or 'unknown'})."]))
    if remote <= local:
        return ("result", (True, [
            "ETK is up to date.", "",
            f"  installed : v{APP_VERSION}",
            f"  latest    : {tag}"]))
    return ("newer", {"tag": tag, "name": rel.get("name") or tag})


def _self_update_apply(info):
    """Worker (curses-free): download the release tarball and sync the
    middleware file set into $ETK_ROOT. Returns (ok, lines)."""
    tag = info["tag"]
    base = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')
    tmp = os.path.join(base, ".update_tmp")
    import urllib.request
    import tarfile
    # The update used to run silently: several minutes of download + kernel
    # staging with nothing on screen but a spinner. It gets the same card
    # every other long job now.
    card = _ProgressCard("UPDATING ETK", tag[:30]).start()
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        url = (f"https://github.com/{_UPDATE_REPO}/archive/refs/tags/"
               f"{tag}.tar.gz")
        tarpath = os.path.join(tmp, "etk.tar.gz")
        req = urllib.request.Request(url, headers={"User-Agent": "etk-pitstop"})
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(tarpath, 'wb') as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tarpath) as tf:
            for m in tf.getmembers():
                if m.name.startswith('/') or '..' in m.name.split('/'):
                    raise RuntimeError(f"suspicious archive member: {m.name}")
            tf.extractall(tmp)
        roots = [d for d in os.listdir(tmp)
                 if d.startswith("etk-") and os.path.isdir(os.path.join(tmp, d))]
        if not roots:
            raise RuntimeError("archive layout: no etk-* root dir")
        src = os.path.join(tmp, roots[0])
        if not os.path.isfile(os.path.join(src, "bin", "etk_pitstop.py")):
            raise RuntimeError("archive layout: bin/etk_pitstop.py missing")
        synced = []

        def _copy_tree(rel):
            s, d = os.path.join(src, rel), os.path.join(base, rel)
            if not os.path.isdir(s):
                return
            os.makedirs(d, exist_ok=True)
            n = 0
            for name in sorted(os.listdir(s)):
                sp = os.path.join(s, name)
                if not os.path.isfile(sp):
                    continue
                dp = os.path.join(d, name)
                shutil.copy2(sp, dp)
                if name.endswith(('.sh', '.py')) or os.access(sp, os.X_OK):
                    os.chmod(dp, 0o755)
                n += 1
            synced.append(f"{rel}/ ({n})")

        _copy_tree("bin")
        _copy_tree("scripts")
        # tools/ push-list parity with install.sh STEP 3 (never wholesale).
        os.makedirs(os.path.join(base, "tools"), exist_ok=True)
        for rel in ("tools/etk_drift.py", "tools/vault_sweep.sh",
                    "tools/rocknix-bin/wl-mirror",
                    "tools/rocknix-bin/chiaki"):
            sp = os.path.join(src, rel)
            if os.path.isfile(sp):
                dp = os.path.join(base, "tools", os.path.basename(rel))
                shutil.copy2(sp, dp)
                os.chmod(dp, 0o755)
                synced.append("tools/" + os.path.basename(rel))
        # config deploy-set parity with install.sh STEP 5 (never the
        # config_<ID>.yml reference mirrors, never the operator's etk.conf).
        cfg_set = ("pitstop_fields.json", "power_profiles.json",
                   "crash_signatures.json", "etk_template.yml",
                   "MangoHud.conf", "MangoHud.default.conf",
                   "etk_pitstop.sh", "etk_pitstop.svg",
                   "etk_chiaki.sh", "etk_chiaki.svg",
                   "paddock_repos.json.example")
        os.makedirs(os.path.join(base, "config"), exist_ok=True)
        n = 0
        for name in cfg_set:
            sp = os.path.join(src, "config", name)
            if os.path.isfile(sp):
                dp = os.path.join(base, "config", name)
                shutil.copy2(sp, dp)
                if name.endswith('.sh'):
                    os.chmod(dp, 0o755)
                n += 1
        synced.append(f"config deploy-set ({n})")
        sp = os.path.join(src, "pro-tuning", "install-protune.sh")
        if os.path.isfile(sp):
            os.makedirs(os.path.join(base, "pro-tuning"), exist_ok=True)
            dp = os.path.join(base, "pro-tuning", "install-protune.sh")
            shutil.copy2(sp, dp)
            os.chmod(dp, 0o755)
        # Live overlay conf + Tools-menu launcher (Sentry tripwire would
        # re-inject the launcher anyway; doing it now avoids one boot lag).
        for s_rel, d_abs in (
                (os.path.join(src, "config", "MangoHud.conf"),
                 "/storage/.config/MangoHud/MangoHud.conf"),
                (os.path.join(base, "config", "etk_pitstop.sh"),
                 "/storage/.config/modules/etk_pitstop.sh")):
            try:
                if os.path.isfile(s_rel):
                    os.makedirs(os.path.dirname(d_abs), exist_ok=True)
                    shutil.copy2(s_rel, d_abs)
                    if d_abs.endswith('.sh'):
                        os.chmod(d_abs, 0o755)
            except Exception as e:
                _log(f"self-update: {d_abs} refresh failed: {e}")
        # --- GTK stack: kernel (0.8.3 — self-update goes full-stack) ---
        # "We never ship a GTK without its feature set": a couch update must
        # carry the kernel, not just middleware. The gtk_stack.json manifest
        # rides the release tarball (so these pins are THIS tag's), the asset
        # is fetched from the same tag's release, sha-verified, then handed
        # to bin/kernel_stage.sh which banks the osguard heal bundle and
        # harvests the live grub block. Activation stays osguard's job: the
        # staged kernel goes live only on a boot whose OS module tree matches
        # (kernel.requires_os) — staging can never make the rig unbootable.
        # Fail-soft: a kernel-step failure leaves the middleware update good.
        stack_lines = []
        try:
            man_path = os.path.join(src, "config", "gtk_stack.json")
            kern = None
            if os.path.isfile(man_path):
                with open(man_path) as f:
                    kern = (json.load(f) or {}).get("kernel")
            if kern and kern.get("asset") and kern.get("sha256"):
                cur = ""
                try:
                    with open("/storage/rocknix-gtk/heal/"
                              "KERNEL.staged.sha256") as f:
                        cur = f.read().strip()
                except OSError:
                    pass
                if cur == kern["sha256"]:
                    stack_lines.append("Kernel: already staged (up to date).")
                else:
                    kpath = os.path.join(tmp, kern["asset"])
                    kurl = (f"https://github.com/{_UPDATE_REPO}/releases/"
                            f"download/{tag}/{kern['asset']}")
                    kreq = urllib.request.Request(
                        kurl, headers={"User-Agent": "etk-pitstop"})
                    with urllib.request.urlopen(kreq, timeout=600) as r, \
                            open(kpath, 'wb') as f:
                        shutil.copyfileobj(r, f)
                    import hashlib
                    h = hashlib.sha256()
                    with open(kpath, 'rb') as f:
                        for chunk in iter(lambda: f.read(1 << 20), b''):
                            h.update(chunk)
                    if h.hexdigest() != kern["sha256"]:
                        raise RuntimeError("kernel asset failed sha256 verify")
                    rc = os.system(f"sh '{base}/bin/kernel_stage.sh' "
                                   f"'{kpath}' '{kern['sha256']}' "
                                   f">/dev/null 2>&1")
                    if rc != 0:
                        raise RuntimeError("kernel_stage.sh refused the stage")
                    synced.append("kernel")
                    if os.uname().release == kern.get("kernel_release"):
                        stack_lines.append("Kernel: GTK kernel staged and "
                                           "activated - REBOOT to load it.")
                    else:
                        stack_lines.append(
                            f"Kernel: staged for ROCKNIX "
                            f"{kern.get('requires_os', '?')} - it activates "
                            f"itself after you update the OS.")
        except Exception as e:
            _log(f"self-update: kernel stage failed: "
                 f"{e.__class__.__name__}: {e}")
            stack_lines.append("Kernel: staging FAILED (middleware is still "
                               "updated) - run install.sh from a computer.")
        # Cycle the watchdogged daemons: the Sentry respawns them from the
        # updated files within a tick or two.
        os.system("pkill -f mango_bridge.sh 2>/dev/null; "
                  "pkill -f input_d.py 2>/dev/null")
        # Provenance: CONFIG ledger row (golden-seed idiom) + marker file.
        try:
            os.makedirs(TELEMETRY_DIR, exist_ok=True)
            if not os.path.exists(CONFIG_CHANGES_LEDGER):
                ltmp = CONFIG_CHANGES_LEDGER + ".tmp"
                with open(ltmp, 'w') as f:
                    f.write(CONFIG_CHANGES_HEADER)
                os.replace(ltmp, CONFIG_CHANGES_LEDGER)
            with open(CONFIG_CHANGES_LEDGER, 'a') as f:
                f.write(f"{int(time.time())}\tSYSTEM\tSELF-UPDATE\t"
                        f"v{APP_VERSION}\t{tag}\n")
            with open(os.path.join(base, ".last_self_update"), 'w') as f:
                f.write(f"{int(time.time())} v{APP_VERSION} -> {tag}\n")
        except Exception as e:
            _log(f"self-update: provenance write failed: {e}")
        _log(f"self-update: v{APP_VERSION} -> {tag} OK ({', '.join(synced)})")
        out = [f"Updated to {tag}.", "",
               "Synced: " + ", ".join(synced), ""]
        if stack_lines:
            out += stack_lines + [""]
        out += ["Emulator / driver / Sentry still update via",
                "install.sh or a new card image.", "",
                "Restart Pitstop to load the new version."]
        card.stop()
        _Notifier().post("ETK UPDATED", f"{tag} - restart Pitstop",
                         timeout=10000)
        return (True, out)
    except Exception as e:
        _log(f"self-update: apply failed: {e.__class__.__name__}: {e}")
        card.stop()
        _Notifier().post("ETK UPDATE FAILED", e.__class__.__name__[:40],
                         timeout=12000)
        return (False, [
            f"Update failed: {e.__class__.__name__}: {e}", "",
            "Nothing outside the temp area is touched until the download",
            "verifies; if the failure was mid-copy, re-run the update",
            "(or install.sh from the host) to repair."])
    finally:
        card.stop()
        shutil.rmtree(tmp, ignore_errors=True)


# --- the blocking install / uninstall sequences -------------

def _storage_incoherent_msg():
    """Error line-list if RPCS3's games tree (/storage/roms/...) and ETK's
    (/storage/games-internal/...) are on DIFFERENT storage right now — else None.
    RPCS3 reaches its PS3 data through ROCKNIX's config-dir symlinks under
    /storage/roms/bios/rpcs3/, while ETK deploys under /storage/games-internal.
    On a coherent boot they are ONE storage (single-card: both on the boot card;
    UFS+SDGAMES: the rebind binds BOTH to the games card), so the two paths share
    a device. A FOREIGN SD card shadowing /storage/roms puts them on different
    devices (the split-brain) — RPCS3 would read/write a card that isn't your
    games tree. Compare st_dev; can't-tell -> don't block."""
    try:
        if os.stat("/storage/roms").st_dev == os.stat("/storage/games-internal").st_dev:
            return None
    except Exception:
        return None
    return ["Games storage is split across two cards:",
            "  RPCS3 uses /storage/roms, ETK uses /storage/games-internal,",
            "  and they are on different cards right now.",
            "",
            "Remove the extra SD card (or fix the SD rebind) so your games",
            "tree is the only one mounted, then retry."]


# --- RPCS3 config-dir link pre-flight (fresh-rig data-loss guard) ---
# ROCKNIX's /usr/bin/start_rpcs3.sh is what makes RPCS3's config dir point at
# the games tree, and it only runs at GAME LAUNCH:
#
#   FOLDER_LINKS=("dev_flash" "dev_hdd0" "dev_hdd1" "custom_configs")
#   rm -rf  /storage/.config/rpcs3/$F
#   ln -sf  /storage/roms/bios/rpcs3/$F  /storage/.config/rpcs3/$F
#
# Both installers invoke RPCS3_BIN DIRECTLY, so on a rig where no game has
# been launched yet those links do not exist. RPCS3 then creates its VFS tree
# as real directories under /storage/.config/rpcs3/ (VFS: Initialize
# Directories) and installs there — and the first game launch `rm -rf`s the
# lot. Observed live 2026-07-24 on a fresh rig: firmware 4.93 (193 MB) and
# GT HD Concept (706 MB) both stranded in the config tree, one launch away
# from deletion, while Pitstop watched the empty bios tree and called the
# install a failure.
#
# So: make the links exist BEFORE launching RPCS3, and rescue anything already
# stranded. Unlike start_rpcs3.sh we never `rm -rf` real content — we migrate
# it into the games tree first, and if anything cannot be migrated safely we
# leave the directory alone and report rather than risk a byte.
_RPCS3_LINK_FOLDERS = ("dev_flash", "dev_hdd0", "dev_hdd1", "custom_configs")
RPCS3_CFG_DIR = os.environ.get('RPCS3_CFG_DIR', '/storage/.config/rpcs3')
RPCS3_BIOS_DIR = os.environ.get('RPCS3_BIOS_DIR', '/storage/roms/bios/rpcs3')


def _move_entry(s, d):
    """Move `s` to `d` (which must not exist), all-or-nothing.

    os.rename is instant when it works, but it does NOT work here: ROCKNIX
    bind-mounts the games card at /storage/roms, and rename(2) returns EXDEV
    across a MOUNT boundary even when both sides are the same filesystem —
    which they are (st_dev 45826 both, so the storage-coherence check rightly
    passes). Live proof, 2026-07-24: every migration failed with "[Errno 18]
    Invalid cross-device link". So fall back to a real copy — staged under a
    temp name and renamed into place, since that final rename IS within one
    mount and therefore atomic. A copy that dies half-way leaves a .etk-part
    directory, never a partial-looking real one."""
    try:
        os.rename(s, d)
        return
    except OSError:
        pass
    part = d + ".etk-part"
    shutil.rmtree(part, ignore_errors=True)
    try:
        shutil.move(s, part)          # copy across the mount boundary
        os.rename(part, d)            # atomic: same mount
    except Exception:
        shutil.rmtree(part, ignore_errors=True)
        raise


def _merge_move(src, dst):
    """Move every entry of dir `src` into dir `dst`, recursing where both
    sides have a directory of the same name. NEVER overwrites: an entry that
    already exists on the destination side is left in `src` untouched.
    Returns the number of entries that could NOT be moved (0 = src drained)."""
    conflicts = 0
    try:
        entries = os.listdir(src)
    except Exception as e:
        _log(f"merge_move listdir {src}: {e}")
        return 1
    os.makedirs(dst, exist_ok=True)
    for name in entries:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        try:
            if not os.path.exists(d):
                _move_entry(s, d)
            elif os.path.isdir(s) and os.path.isdir(d) and \
                    not os.path.islink(s) and not os.path.islink(d):
                conflicts += _merge_move(s, d)
                # Drained subdirectory -> drop the now-empty shell.
                try:
                    os.rmdir(s)
                except OSError:
                    pass
            else:
                # Same name on both sides and not two mergeable dirs. The
                # games tree is authoritative (it is what RPCS3 will read
                # once linked); keep both copies rather than pick a winner.
                conflicts += 1
        except Exception as e:
            _log(f"merge_move {s} -> {d}: {e}")
            conflicts += 1
    return conflicts


def _ensure_rpcs3_storage_links(notify=None):
    """Make RPCS3's config dir resolve to the games tree, the way
    start_rpcs3.sh will. Returns a list of warning lines (empty = all good).
    Fail-soft by contract: a problem here is reported, never fatal — the
    install still runs, it just may land somewhere we then warn about.

    Rescuing an already-stranded tree means copying it across the games-card
    mount boundary (see _move_entry), so a rig with a game already installed
    the wrong side of the link moves real gigabytes here. Pass `notify` so
    that reads as work rather than as a hang."""
    warn = []
    announced = False
    # Only act if ROCKNIX has already seeded the config dir. Creating it
    # ourselves would make start_rpcs3.sh's `if [ ! -d ... ]` skip its
    # `cp -r /usr/config/rpcs3`, leaving the rig with no stock config.yml.
    if not os.path.isdir(RPCS3_CFG_DIR):
        return warn
    for folder in _RPCS3_LINK_FOLDERS:
        src = os.path.join(RPCS3_CFG_DIR, folder)     # what RPCS3 opens
        dst = os.path.join(RPCS3_BIOS_DIR, folder)    # the games tree
        try:
            if os.path.islink(src):
                os.makedirs(dst, exist_ok=True)       # link target must exist
                continue
            if not os.path.exists(src):
                os.makedirs(dst, exist_ok=True)
                os.symlink(dst, src)
                continue
            if not os.path.isdir(src):
                warn.append(f"{src} is a file, not a folder - left alone")
                continue
            # A real directory: rescue its contents into the games tree, then
            # replace it with the link. This is the stranded-install repair.
            if notify is not None and not announced:
                announced = True
                notify.post("TIDYING STORAGE",
                            "Moving game data onto your games card",
                            timeout=15000)
            left = _merge_move(src, dst)
            if left:
                warn.append(f"{folder}: {left} item(s) exist in both trees -")
                warn.append(f"  left in {src}")
                continue
            os.rmdir(src)          # only ever removes a now-EMPTY directory
            os.symlink(dst, src)
        except Exception as e:
            _log(f"rpcs3 link preflight {folder}: {e}")
            warn.append(f"{folder}: could not link ({e.__class__.__name__})")
    return warn


# --- RPCS3 pad binding (device-name drift repair) -----------
# ROCKNIX ships RPCS3 a stock pad config naming `InputPlumber GameController 1`
# — the Xbox-style virtual pad InputPlumber used to expose. It now targets a
# DualSense instead (uhid 054C:0CE6), so on a Flip 2 that name matches NOTHING.
# RPCS3 does not shrug this off: pad_thread.cpp:206 fails the bind and falls
# back to NullPadHandler, i.e. the controller is completely dead in game, and
# the only clue is one line in RPCS3.log. Reported from the field 2026-07-24.
#
# The name RPCS3 wants is "<SDL_GetGamepadName()> <N>" (sdl_pad_handler.cpp:412,
# N = 1-based count of same-named devices). Rather than hard-code a string —
# hard-coding a device name is what broke this in the first place — ask the
# rig's own SDL at run time and write back whatever it reports.
_SDL_NAME_PROBE = r'''
import ctypes, os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sdl = None
for cand in ("libSDL3.so.0", "/usr/lib/libSDL3.so.0", "/usr/lib/libSDL3.so"):
    try:
        sdl = ctypes.CDLL(cand); break
    except OSError:
        pass
if sdl is None:
    sys.exit(2)
sdl.SDL_Init.restype = ctypes.c_bool
sdl.SDL_GetGamepads.restype = ctypes.POINTER(ctypes.c_uint32)
sdl.SDL_GetGamepads.argtypes = [ctypes.POINTER(ctypes.c_int)]
sdl.SDL_OpenGamepad.restype = ctypes.c_void_p
sdl.SDL_OpenGamepad.argtypes = [ctypes.c_uint32]
sdl.SDL_GetGamepadName.restype = ctypes.c_char_p
sdl.SDL_GetGamepadName.argtypes = [ctypes.c_void_p]
if not sdl.SDL_Init(0x2000):          # SDL_INIT_GAMEPAD
    sys.exit(3)
n = ctypes.c_int(0)
ids = sdl.SDL_GetGamepads(ctypes.byref(n))
for i in range(n.value):
    gp = sdl.SDL_OpenGamepad(ids[i])
    nm = sdl.SDL_GetGamepadName(gp) if gp else None
    if nm:
        sys.stdout.write(nm.decode("utf-8", "replace") + "\n")
'''


def _sdl_gamepad_names():
    """Device names RPCS3's SDL pad handler will enumerate, in order — i.e.
    ["DualSense Wireless Controller 1", ...]. [] if SDL can't tell us.

    Runs in a SUBPROCESS on purpose. SDL's DS5 driver opens /dev/hidraw and
    writes feature reports to put the controller into full report mode; doing
    that inside Pitstop would disturb the pad the operator is holding (Pitstop
    reads the same device over raw evdev). A child process hands every bit of
    that state back to the kernel when it exits."""
    try:
        r = subprocess.run([sys.executable, "-c", _SDL_NAME_PROBE],
                           capture_output=True, text=True, timeout=20)
    except Exception as e:
        _log(f"sdl gamepad probe failed: {e}")
        return []
    if r.returncode != 0:
        _log(f"sdl gamepad probe rc={r.returncode}: {(r.stderr or '')[:120]}")
        return []
    seen = {}
    out = []
    for raw in r.stdout.splitlines():
        nm = raw.strip()
        if not nm:
            continue
        seen[nm] = seen.get(nm, 0) + 1          # RPCS3's same-name counter
        out.append(f"{nm} {seen[nm]}")
    return out


def _pad_player1(keys):
    """Values of `keys` from Player 1's block in RPCS3_PAD_CONFIG.
    Player 1 is the FIRST top-level section; scanning stops at the next
    column-0 header so the Null pads 2-7 are never read (the trigger-cal
    screen's FIX 5 section gate, same idiom)."""
    got = {k: None for k in keys}
    try:
        with open(RPCS3_PAD_CONFIG) as f:
            lines = f.readlines()
    except OSError:
        return got
    in_p1 = False
    for ln in lines:
        if ln and ln[0] not in " \t" and ln.rstrip().endswith(":"):
            if in_p1:
                break
            in_p1 = True
            continue
        if not in_p1:
            continue
        s = ln.strip()
        for k in keys:
            if s.startswith(k + ":"):
                got[k] = s.split(":", 1)[1].strip().strip('"')
    return got


def _pad_write_device(name):
    """Rewrite ONLY Player 1's `Device:` line, preserving its indentation and
    every other line in the file — the operator's trigger calibration, dead
    zones and button map all live here. Atomic tmp+replace (H2), verified by
    read-back. Returns True on success."""
    try:
        with open(RPCS3_PAD_CONFIG) as f:
            lines = f.readlines()
    except OSError as e:
        _log(f"pad config read failed: {e}")
        return False
    out, in_p1, done = [], False, False
    for ln in lines:
        if ln and ln[0] not in " \t" and ln.rstrip().endswith(":"):
            if in_p1:
                in_p1 = False       # past Player 1; copy the rest verbatim
            elif not done:
                in_p1 = True
            out.append(ln)
            continue
        if in_p1 and not done and ln.strip().startswith("Device:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(f"{indent}Device: {name}\n")
            done = True
            continue
        out.append(ln)
    if not done:
        _log("pad config: no Device: line in the Player 1 block")
        return False
    try:
        tmp = RPCS3_PAD_CONFIG + ".etk.tmp"
        with open(tmp, "w") as f:
            f.writelines(out)
        os.replace(tmp, RPCS3_PAD_CONFIG)
    except Exception as e:
        _log(f"pad config write failed: {e}")
        return False
    return _pad_player1(["Device"]).get("Device") == name


def _ensure_pad_binding(notify=None):
    """Point RPCS3's Player 1 at a controller that actually exists.

    Returns a list of report lines (empty = nothing to say). Fail-soft
    throughout: this must never block an install, and it never touches a
    config whose device is already valid."""
    if not PAD_BIND_ENABLED:
        return []
    cur = _pad_player1(["Handler", "Device"])
    handler, device = cur.get("Handler"), cur.get("Device")
    if not handler:
        return []                                  # no pad config yet
    if str(handler).strip().upper() != "SDL":
        # Only the SDL handler uses the "<SDL name> <N>" scheme. An operator
        # who has deliberately moved to Evdev/DualSense owns their binding.
        return []
    if _rpcs3_running():
        return []                                  # RPCS3 rewrites this on exit
    names = _sdl_gamepad_names()
    if not names:
        # Can't see the pad (none attached, or no SDL). Say nothing unless the
        # config is also obviously stale — we have no better name to offer.
        return []
    if device in names:
        return []                                  # already correct
    target = names[0]
    ok = _pad_write_device(target)
    _log(f"pad binding: {device!r} -> {target!r} ok={ok}")
    if not ok:
        return ["controller: could NOT update the RPCS3 pad config",
                f"  it still points at {device or '(unset)'}"]
    if notify is not None:
        notify.post("CONTROLLER CONFIGURED",
                    target.rsplit(' ', 1)[0][:40], timeout=10000)
    return [f"controller : {target}",
            f"  was      : {device or '(unset)'}  (no such device)"]


def _rpcs3_resolved(path, fallback):
    """Resolve one of RPCS3's config-dir paths to where it ACTUALLY lands
    (following the ROCKNIX symlink). Falls back to the games-tree constant if
    RPCS3 has no config dir at all. Works on paths that don't exist yet —
    realpath resolves as far as it can."""
    try:
        if os.path.isdir(RPCS3_CFG_DIR):
            return os.path.realpath(path)
    except Exception:
        pass
    return fallback


# --- RPCS3 log capture (shared by both installers) ----------
# RPCS3 starts a FRESH RPCS3.log on every launch: it gzips the previous one to
# RPCS3.log.gz, then rewrites the original IN PLACE — same inode, no append
# (util/logs.cpp:476, `m_fout2.open(name + ".gz", fs::rewrite)`). So the live
# log is always exactly one run's worth, and the only question is whether that
# run is OURS. That is a question about TIME, so answer it with mtime.
#
# Do NOT try to locate a byte offset into the log. 0.8.1 shipped a version that
# marked (size, inode) before launch and read an unchanged size as "RPCS3 wrote
# nothing" — but re-running the SAME package writes a byte-IDENTICAL log, so
# the guard fired on a completely successful install and reported failure
# (live, 2026-07-24: two runs of the same 722 MB package both produced exactly
# 9608 bytes, and the second was declared "RPCS3 exited without confirming").

def _rpcs3_log_mark():
    """Wall-clock stamp, taken immediately BEFORE launching RPCS3."""
    return time.time()


def _rpcs3_log_since(mark, limit=262144):
    """This run's RPCS3 log, or "" if RPCS3 never wrote one."""
    try:
        # 2 s of slack for coarse filesystem timestamp granularity.
        if os.stat(RPCS3_LOG).st_mtime < mark - 2:
            return ""
        with open(RPCS3_LOG, 'r', errors='ignore') as f:
            return f.read()[-limit:]
    except Exception:
        return ""


# RPCS3's per-package verdict lines (main_window.cpp:1201/1208/1213/1219 and
# :1041/:1320/:1349). The tuple form carries the title id, human title and
# version straight out of the package's own PARAM.SFO.
#
# It is NOT always where the package actually landed. Disc-to-PKG conversions
# keep the DISC serial in PARAM.SFO (TITLE_ID) while RPCS3 extracts to the
# package's CONTENT id — e.g. Demon's Souls installed 2026-08-05 reported
# title_id=BLUS30443 while every "Created file" line wrote to .../game/NPUB30910
# (and its .rap licence is keyed NPUB30910 too). Trusting the tuple alone left
# a complete 8 GB install with no launcher, no licence and no config. So we
# also scrape the directory RPCS3 says it wrote, and prefer it when the two
# disagree — the extracted path is ground truth for everything downstream
# (.psn, exdata, per-game config, vault keying).
_PKG_VERDICT_RE = re.compile(
    r'(?P<verb>Successfully installed|Failed to install|Partially installed'
    r'|Aborted installation of)\s+(?P<path>.+?)\s+'
    r'\(title_id=(?P<tid>[^,]*),\s*title=(?P<title>.*?),\s*'
    r'version=(?P<ver>[^)]*)\)\.')
# "PKG: Created file <dev_hdd0 root>/game/<ID>/..." — the id RPCS3 actually
# extracted under. Used only as a fallback when the verdict tuple's id has no
# game folder (see the disc-to-PKG note above).
_PKG_CREATED_ID_RE = re.compile(r'PKG: Created file .*?/game/(?P<tid>[A-Z0-9_\-]{4,16})/')
# Bare (no-tuple) forms. "Cannot install <path>." is specifically the
# app_version error: an update PKG whose base-game version doesn't match what
# is installed. In GUI mode that becomes a QMessageBox spelling out expected
# vs found; headless only gets the path, so we supply the explanation.
_PKG_VERSION_ERR_RE = re.compile(r'Cannot install (?!invalid package)(?P<path>.+?)\.\s*$',
                                 re.MULTILINE)
_PKG_INVALID_RE = re.compile(r"Cannot install invalid package: '(?P<path>[^']*)'")
# The bare failure: when extraction itself fails, main_window.cpp:1349 logs
# only this (the per-package tuple forms are written on the SUCCESS branch).
# All three are matched on exact path equality, so the tuple lines — which this
# pattern could otherwise swallow whole — never come back with a matching path.
_PKG_FAILED_RE = re.compile(r'Failed to install (?P<path>.+?)\.\s*$', re.MULTILINE)


def _pkg_install_timeout(pkg_path):
    """Wall-clock budget for one extraction, scaled to package size. The old
    fixed 600 s cap was smaller than a big title needs (GT5 is 19.4 GB) while
    still being a 10-minute stall on a small one. Measured on the reference
    rig: ~20 MB/s; budget a pessimistic 3 MB/s plus 300 s of startup slack."""
    try:
        mb = os.path.getsize(pkg_path) / (1024.0 * 1024.0)
    except Exception:
        mb = 0
    return int(min(7200, max(900, 300 + mb / 3.0)))


def _run_install(pkg_path, rap_path, notify):
    """Blocking HEADLESS install of one staged .pkg — the .pup path's twin.

    `rpcs3 --headless --installpkg <pkg>` extracts with no window and no
    prompts, then self-exits (rpcs3.cpp:1218-1223). Process exit is therefore
    the completion signal and RPCS3's own log line is the verdict; Pitstop
    keeps its spinner up throughout. Returns (ok, [result lines]). Runs on the
    spinner worker thread, so it MUST NOT touch curses.

    `rap_path` accepts a single path, None, or a list (every staged licence is
    copied — they are keyed by content id, so an extra one is harmless and a
    missing one is not)."""
    raps = ([rap_path] if isinstance(rap_path, str)
            else list(rap_path or []))

    if _rpcs3_running():
        return False, ["RPCS3 is already running.",
                       "Close the game first, then retry."]

    # Pre-flight, in order: refuse a split-brain; make RPCS3's config dir
    # resolve to the games tree (or the install lands somewhere the first game
    # launch deletes); then provision the game dir at the path RPCS3 actually
    # opens, since a freshly-flashed card has no bios/rpcs3 tree at all.
    incoherent = _storage_incoherent_msg()
    if incoherent:
        return False, incoherent
    link_warn = _ensure_rpcs3_storage_links(notify)
    game_real = _rpcs3_resolved(RPCS3_CFG_GAME_DIR, RPCS3_GAME_DIR)
    try:
        os.makedirs(game_real, exist_ok=True)
    except Exception as e:
        return False, ["Could not prepare RPCS3 game storage:",
                       f"  {game_real}",
                       f"  {e.__class__.__name__}: {str(e)[:44]}",
                       "",
                       "Is your games card inserted, writable and not full?"]

    # Install lock — the Sentry stays parked in IDLE while we drive RPCS3
    # so no phantom telemetry session fires. Clear any stale lock first
    # (we own it exclusively; installs are idle-time only).
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        open(ETK_INSTALL_LOCK, 'w').close()
    except Exception as e:
        _log(f"install lock write failed: {e}")

    name = os.path.basename(pkg_path)
    header_id = _pkg_title_id(pkg_path)          # PKG-header ID, as a fallback
    mark = _rpcs3_log_mark()

    def out_lines(lines):
        """Attach any storage-layout warning to a result. Reported on FAILURE
        as well as success — a stranded tree is exactly what a reader needs to
        know when an install has just gone wrong."""
        if not link_warn:
            return lines
        return lines + ["", "NOTE - RPCS3 storage layout:"] + \
            [f"  {ln}" for ln in link_warn]

    proc = None
    card = _ProgressCard("INSTALLING", name[:30]).start()
    try:
        proc = subprocess.Popen(
            [RPCS3_BIN, "--headless", "--installpkg", pkg_path],
            env=_tools_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        budget = _pkg_install_timeout(pkg_path)
        try:
            out = proc.communicate(timeout=budget)[0]
            out = (out or b"").decode('utf-8', 'ignore')
        except subprocess.TimeoutExpired:
            _kill_installer_rpcs3()
            return False, out_lines(
                [f"Install timed out after {budget // 60} minutes.",
                 "RPCS3 may be wedged - reboot and retry.",
                 "Staged files were kept."])

        # VERDICT FROM THE LOG, never from the filesystem. Note we do NOT
        # trust the exit code: the headless path returns 0 from main() but
        # then trips an ensure() in ~manual_typemap during static teardown and
        # dies on a signal (rc 143 observed on-rig 2026-07-24) AFTER a fully
        # successful install. The .pup path can lean on rc==0; this one can't.
        blob = out + "\n" + _rpcs3_log_since(mark)
        ours = [m for m in _PKG_VERDICT_RE.finditer(blob)
                if m.group('path') == pkg_path]
        good = [m for m in ours if m.group('verb') == 'Successfully installed']

        if not good:
            def mine(rx):
                return any(m.group('path') == pkg_path for m in rx.finditer(blob))

            if mine(_PKG_VERSION_ERR_RE):
                notify.post("INSTALL FAILED",
                            "Update needs a different game version",
                            timeout=12000)
                return False, out_lines([
                    "This package could NOT be installed.",
                    "",
                    "It is an UPDATE, and it does not match the version of",
                    "the game you have installed (or the base game is not",
                    "installed at all).",
                    "",
                    "Install the base game first, or get the update that",
                    "matches it. Staged files were kept."])
            if mine(_PKG_INVALID_RE):
                notify.post("INSTALL FAILED", "Package unreadable",
                            timeout=12000)
                return False, out_lines([
                    "RPCS3 could not read this package.",
                    "",
                    "The .pkg is corrupt or incomplete - re-copy it",
                    "to the drop folder and try again.",
                    "Staged files were kept."])
            if mine(_PKG_FAILED_RE):
                notify.post("INSTALL FAILED", "Extraction failed",
                            timeout=12000)
                return False, out_lines([
                    "RPCS3 could not finish unpacking this package.",
                    "",
                    "Usually the .pkg copied over incompletely, or the card",
                    "is full or failing. Check free space, re-copy the file",
                    "and try again. Staged files were kept."])
            bad = ours[0].group('verb').lower() if ours else None
            reason = (f"RPCS3 reported: {bad}" if bad
                      else "RPCS3 exited without confirming the install.")
            _log(f"pkg install failed rc={proc.returncode}: {reason}")
            notify.post("INSTALL FAILED", reason[:40], timeout=12000)
            return False, out_lines(["Install did NOT complete.",
                                     f"  {reason}",
                                     "",
                                     "Staged files were kept so you can retry."])

        v = good[0]
        new_id = (v.group('tid').strip() or header_id or "").strip()
        human = v.group('title').strip()
        version = v.group('ver').strip()
        if not new_id:
            # Log said success but named no title id — nothing downstream
            # (.psn, config, vault) can be keyed. Report honestly.
            return False, out_lines([
                "The package installed, but RPCS3 did not report",
                "a title ID for it, so the game launcher could not",
                "be created. Staged files were kept."])
        game_dir = os.path.join(game_real, new_id)
        if not os.path.isdir(game_dir):
            # Verdict id has no folder — fall back to the id RPCS3 logged
            # writing to (disc-to-PKG conversions report the disc serial but
            # extract under the content id).
            for m in _PKG_CREATED_ID_RE.finditer(blob):
                cand = m.group('tid')
                if cand != new_id and os.path.isdir(os.path.join(game_real, cand)):
                    _log(f"pkg install: verdict id {new_id} has no folder; "
                         f"using extracted id {cand}")
                    new_id, game_dir = cand, os.path.join(game_real, cand)
                    break
        if not human:
            human = _sfo_title(game_dir) or ""
        human = human or new_id
        if not os.path.isdir(game_dir):
            return False, out_lines([
                f"RPCS3 reported {human} installed, but its game",
                f"folder is missing:  {new_id}",
                "",
                "Staged files were kept so you can retry."])

        rap_done = "none staged"
        if raps:
            exdata = _rpcs3_resolved(RPCS3_CFG_EXDATA_DIR, RPCS3_EXDATA_DIR)
            copied = 0
            for r in raps:
                try:
                    os.makedirs(exdata, exist_ok=True)
                    shutil.copyfile(r, os.path.join(exdata, os.path.basename(r)))
                    copied += 1
                except Exception as e:
                    _log(f"rap copy failed for {r}: {e}")
            rap_done = (f"{copied} copied to exdata" if copied
                        else "COPY FAILED")

        psn_name = _write_psn(new_id, human)
        mh_ok = _enable_mangohud(psn_name)
        cfg_status = _deploy_template_config(new_id)

        # Delete staged files — success path only. A failed install above
        # returns early and leaves them untouched for a retry. Also sweep
        # macOS AppleDouble / .DS_Store cruft so the drop folder is clean
        # for the next game: the common workflow is dropping the .pkg over
        # SMB from a Mac Finder, which litters the folder with '._' files
        # (Rocknix even ships a "Remove ._ Files" Tools utility for this).
        cleanup = [pkg_path] + raps
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

        # Card down, then the verdict — ES's own order. The gamelist rescan is
        # queued here rather than asked of the operator: it lands on the first
        # ES frame after Pitstop exits, so the game is simply there.
        card.stop()
        notify.post("INSTALL COMPLETE", human[:40], timeout=10000)
        _es_reload_gamelists()
        return True, out_lines([
            f"INSTALLED:  {human}",
            f"  title id   : {new_id}" + (f"  (v{version})" if version else ""),
            f"  licence    : {rap_done}",
            f"  launcher   : {psn_name}",
            f"  mangohud   : {'enabled' if mh_ok else 'FAILED - enable manually'}",
            f"  etk config : {cfg_status}",
        ]) + ["",
              "Your library refreshes by itself when you leave the Pitstop."]
    except Exception as e:
        _log(f"install flow error: {e}\n{traceback.format_exc()}")
        return False, out_lines(["Install error:",
                                 f"  {e.__class__.__name__}: {str(e)[:48]}"])
    finally:
        card.stop()
        if proc is not None and proc.poll() is None:
            _kill_installer_rpcs3()
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

    # Cover BOTH the games tree and whatever RPCS3's config dir resolves to.
    # Once the ROCKNIX link exists these are the same path (dedup below), but a
    # rig that installed before the link was made has the game in the config
    # tree — uninstall must still find it there. See _ensure_rpcs3_storage_links.
    game_dirs = {RPCS3_GAME_DIR, _rpcs3_resolved(RPCS3_CFG_GAME_DIR, RPCS3_GAME_DIR)}
    exdata_dirs = {RPCS3_EXDATA_DIR,
                   _rpcs3_resolved(RPCS3_CFG_EXDATA_DIR, RPCS3_EXDATA_DIR)}
    dir_targets = [os.path.join(g, tid) for g in sorted(game_dirs)] + [
        os.path.join(RPCS3_RUNTIME_CACHE, tid),
        os.path.join(RPCS3_HDD1_CACHE, f"{tid}_{tid}"),
    ]
    file_targets = [game["psn"]]
    for ex in sorted(exdata_dirs):
        try:
            for entry in os.listdir(ex):
                if tid in entry and entry.lower().endswith('.rap'):
                    file_targets.append(os.path.join(ex, entry))
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
    notify.post("GAME REMOVED", f"{name[:28]} - {mb} MB freed", timeout=10000)
    _es_reload_gamelists()
    return True, [
        f"UNINSTALLED:  {name}",
        f"  title id : {tid}",
        f"  removed  : {removed} items, {mb} MB freed",
        "  kept     : your save data + ETK shader vault",
        "",
        "Your library refreshes by itself when you leave the Pitstop.",
    ]


# === TOOLS TAB — PS3 FIRMWARE INSTALLER (headless) ===
# Firmware installs through RPCS3's NATIVE headless CLI:
# `rpcs3 --headless --installfw`. In headless mode RPCS3 builds a non-GUI
# application, so main_window::InstallPup is called with a null main window and
# EVERY prompt is skipped (the confirm, the "old firmware" and "already
# installed / overwrite" questions, the progress dialog and the error popups
# are all gated on `mw != nullptr`). It installs to dev_flash, logs
# "Successfully installed PS3 firmware version X" and self-exits (return 0) —
# so no window polling and no uinput are needed here. The .pup is KEPT
# (firmware is a one-time, reusable, system-wide asset). (--no-gui is a
# DIFFERENT flag that RPCS3 explicitly refuses for installs; it must be
# --headless.)
#
# This was the model; as of 0.8.1 the PKG installer above works the same way.
# The one behavioural difference is the exit code: --installfw exits 0 cleanly,
# while --installpkg trips a teardown ensure() and dies on a signal AFTER a
# successful install — so only this path may use rc==0 as corroboration.

def _scan_firmware():
    """Return sorted absolute paths of .pup files in the firmware drop folder.
    Dotfiles are skipped (a macOS copy litters '._PS3UPDAT.PUP' AppleDouble
    siblings that would otherwise read as a second firmware file)."""
    pups = []
    try:
        for entry in sorted(os.listdir(FIRMWARE_DROP_DIR)):
            if entry.startswith('.'):
                continue
            full = os.path.join(FIRMWARE_DROP_DIR, entry)
            if os.path.isfile(full) and entry.lower().endswith('.pup'):
                pups.append(full)
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"firmware scan failed: {e}")
    return pups


def _installed_fw_version():
    """Best-effort current PS3 firmware version from dev_flash/vsh/etc/version.txt.
    Mirrors RPCS3's utils::get_firmware_version parse: the field between the
    first two ':' with leading/trailing zeros trimmed (e.g. '04.8900' -> '4.89').
    Returns the version string, or None if no firmware is installed / unreadable —
    it is only a confirm-screen hint, so any doubt yields None."""
    try:
        with open(RPCS3_FW_VERSION_FILE) as f:
            raw = f.read()
    except Exception:
        return None
    try:
        a = raw.find(':')
        if a < 0:
            return None
        b = raw.find(':', a + 1)
        if b < 0:
            return None
        ver = raw[a + 1:b].strip().lstrip('0')
        if ver.startswith('.'):
            ver = '0' + ver
        if '.' in ver:
            ver = ver.rstrip('0').rstrip('.')
        return ver or None
    except Exception:
        return None


def _run_install_fw(pup_path, notify, _progress=None):
    """Blocking headless install of one staged PS3 firmware .pup. Runs
    `rpcs3 --headless --installfw <pup>` — no GUI dialog, no uinput, no sway
    juggling (see the section header). RPCS3 self-exits when done; we confirm
    from its log tail (+ dev_flash version.txt for the reported version). On
    success the .pup is KEPT. Returns (ok, [result lines]). Runs on the spinner
    worker thread, so it MUST NOT touch curses."""
    if _rpcs3_running():
        return False, ["RPCS3 is already running.",
                       "Close the game first, then retry."]

    # Pre-flight. First refuse if storage is split across two cards (else we'd
    # provision + install onto a foreign card that isn't your games tree).
    incoherent = _storage_incoherent_msg()
    if incoherent:
        return False, incoherent
    # Then make RPCS3's config dir resolve to the games tree. That link is
    # start_rpcs3.sh's job and it only runs at GAME LAUNCH, so on a rig that
    # has never launched one, firmware installed here lands in the config tree
    # and the first launch `rm -rf`s it (see the pre-flight's header).
    link_warn = _ensure_rpcs3_storage_links(notify)
    # Firmware install is the "set this rig up for PS3" moment, so it is also
    # where the controller gets bound. ROCKNIX's stock RPCS3 pad config names a
    # virtual device that no longer exists, which leaves RPCS3 on NullPadHandler
    # and the pad dead in game with no on-screen clue (field report 2026-07-24).
    pad_note = _ensure_pad_binding(notify)
    # Then SELF-PROVISION RPCS3's dev_flash. RPCS3 resolves dev_flash through its
    # config dir (/storage/.config/rpcs3/dev_flash -> the games tree) and statfs's
    # it for free space BEFORE creating it — so on a freshly-flashed card, where
    # the whole bios/rpcs3 tree is still absent, RPCS3 fails with a cryptic
    # "Couldn't retrieve available disk space". Create the tree ourselves at
    # EXACTLY the path RPCS3 resolves (realpath follows the symlink) so firmware
    # lands where RPCS3 reads it. Fail only if the storage root is read-only/full.
    df_real = _rpcs3_resolved(RPCS3_DEV_FLASH, RPCS3_DEV_FLASH)
    try:
        os.makedirs(df_real, exist_ok=True)
    except Exception as e:
        return False, ["Could not prepare RPCS3 firmware storage:",
                       f"  {df_real}",
                       f"  {e.__class__.__name__}: {str(e)[:44]}",
                       "",
                       "Is your games card inserted, writable and not full?"]

    # Install lock — park the Sentry in IDLE so no phantom telemetry session
    # fires while we drive RPCS3 (same contract as the PKG installer).
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        open(ETK_INSTALL_LOCK, 'w').close()
    except Exception as e:
        _log(f"fw install lock write failed: {e}")

    name = os.path.basename(pup_path)
    prev_ver = _installed_fw_version()
    # Snapshot the log identity up front so we read only THIS run's output
    # (RPCS3 rotates RPCS3.log on launch — see _rpcs3_log_mark).
    mark = _rpcs3_log_mark()

    proc = None
    card = _ProgressCard("INSTALLING FIRMWARE", name[:30]).start()
    try:
        proc = subprocess.Popen(
            [RPCS3_BIN, "--headless", "--installfw", pup_path],
            env=_tools_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            out = proc.communicate(timeout=420)[0]
            out = (out or b"").decode('utf-8', 'ignore')
        except subprocess.TimeoutExpired:
            _kill_installer_rpcs3()
            return False, ["Firmware install timed out (7 min).",
                           "RPCS3 may be wedged - reboot and retry.",
                           f"The {name} file was kept."]

        blob = out + "\n" + _rpcs3_log_since(mark)

        new_ver = _installed_fw_version()
        ok_m = re.search(
            r'Successfully installed PS3 firmware version ([0-9.]+)', blob)
        err_m = re.search(r'Error while installing firmware:\s*(.+)', blob)

        # Success = RPCS3 logged it, OR a clean exit that left firmware present
        # with no error line (covers a log we couldn't read).
        if ok_m or (proc.returncode == 0 and new_ver and not err_m):
            ver = (ok_m.group(1) if ok_m else new_ver) or "?"
            card.stop()
            notify.post("FIRMWARE INSTALLED",
                        f"PS3 firmware {ver} - games can now boot",
                        timeout=10000)
            lines = [f"INSTALLED:  PS3 firmware {ver}",
                     f"  source : {name}  (kept in firmware_drop/)"]
            if prev_ver and prev_ver != ver:
                lines.append(f"  note   : replaced firmware {prev_ver}")
            if pad_note:
                lines += ["", "Also set up:"] + [f"  {ln}" for ln in pad_note]
            if link_warn:
                lines += ["", "NOTE - RPCS3 storage layout:"] + \
                         [f"  {ln}" for ln in link_warn]
            lines += ["",
                      "Firmware is system-wide - you only install it once.",
                      "Commercial PS3 games can now boot in RPCS3."]
            return True, lines

        # Failure — surface RPCS3's own reason if we captured one.
        reason = (err_m.group(1).strip()[:56] if err_m
                  else "RPCS3 exited without confirming the install.")
        _log(f"fw install failed rc={proc.returncode}: {reason}")
        notify.post("FIRMWARE INSTALL FAILED", reason[:40], timeout=12000)
        return False, ["Firmware install did NOT complete.",
                       f"  {reason}",
                       "",
                       f"The {name} file was kept so you can retry.",
                       "Re-download PS3UPDAT.PUP from Sony if this repeats."]
    except Exception as e:
        _log(f"fw install flow error: {e}\n{traceback.format_exc()}")
        return False, ["Firmware install error:",
                       f"  {e.__class__.__name__}: {str(e)[:48]}"]
    finally:
        card.stop()
        if proc is not None and proc.poll() is None:
            _kill_installer_rpcs3()
        try:
            os.remove(ETK_INSTALL_LOCK)
        except Exception:
            pass


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


def _scan_vault_hygiene(state, _progress=None):
    """Build the Manage Shaders model: per-game total/fresh/stale sizes + the
    RPCS3 runtime-cache sizes. Scope-agnostic (reclaim derived per-scope at
    draw). Slow-ish (du + the dry-run stat pass) so callers draw a busy frame
    first. Cached in state['shaders_model'] until an action invalidates it.

    `_progress` (optional, injected by _run_with_spinner): a dict whose "frac"
    this advances 0..1 as the per-game du pass runs, driving the on-screen bar."""
    def _prog(f):
        if _progress is not None:
            _progress["frac"] = f
    game_ids = _vault_game_ids()
    current = _resolve_current_vault_id(game_ids)
    _prog(0.05)
    stale_map, boundary_ok, sweep_reason = _sweep_porcelain()
    _prog(0.15)

    games = []
    n_games = len(game_ids) or 1
    for scan_idx, gid in enumerate(game_ids):
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
        _prog(0.15 + 0.80 * (scan_idx + 1) / n_games)
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
    _prog(1.0)
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
                # Floor a nonzero generation to >=1 cell so a small-but-real
                # slice is never invisible at bar resolution — e.g. 6MB fresh in
                # a 528MB vault rounds to 0 green cells and reads as "all stale"
                # when the operator does have fresh shaders to keep.
                if g["fresh_kb"] > 0 and fw == 0:
                    fw = 1
                if g["stale_kb"] > 0 and sw == 0:
                    sw = 1
                # Keep within the bar, trimming the larger segment first but
                # never erasing a nonzero generation below 1 cell.
                while fw + sw > barw and (fw > 1 or sw > 1):
                    if fw >= sw and fw > 1:
                        fw -= 1
                    elif sw > 1:
                        sw -= 1
                    else:
                        break
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
        notify.post("SHADERS SWEPT",
                    f"{files} files, {mb} MB freed", timeout=10000)
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
        notify.post("VAULT DELETED",
                    f"{mb} MB freed - rebuilds on next launch", timeout=10000)
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
        notify.post("CACHE CLEARED",
                    f"{mb} MB freed - rebuilds on next launch", timeout=10000)
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


def _tools_busy_msg(stdscr, msg, spin=None, bar=None):
    """Generic 'working' frame for a blocking TOOLS op that doesn't hand the
    screen to RPCS3 (shader scans/cleans). `spin` is a throbber glyph centered
    below the message; `bar` is an optional pre-rendered progress bar drawn under
    the throbber (the vault scan reports a real fraction — see _run_with_spinner)."""
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
        if bar:
            stdscr.addstr(h // 2 + 3, max(2, (w - len(bar)) // 2), bar[:w - 4],
                          curses.A_BOLD)
        stdscr.refresh()
    except curses.error:
        pass


def _run_with_spinner(stdscr, msg, work, *args, draw=_tools_busy_msg,
                      progress=None, indeterminate=False, **kwargs):
    """Run a blocking, curses-free `work(*args, **kwargs)` on a background
    thread while animating the ROCKNIX throbber under `msg` on the main thread.
    `draw(stdscr, msg, spin, bar)` paints each frame — defaults to the TOOLS busy
    frame; pass `draw=_paddock_busy` for the PADDOCK tab so the right tab strip
    shows. Returns work()'s value (None if it raised — logged). The worker MUST
    NOT touch stdscr: curses is single-threaded, so only this (main) thread draws.

    Progress bar (complements the throbber): pass `progress={"frac": 0.0}` and the
    worker receives it as the `_progress` kwarg; each frame renders a determinate
    bar from `progress["frac"]` (0..1). For an opaque op with no measurable
    fraction (network / git), pass `indeterminate=True` for a marquee sweep
    instead. Neither = throbber only (legacy)."""
    box = {}
    if progress is not None:
        kwargs["_progress"] = progress

    def _runner():
        try:
            box["val"] = work(*args, **kwargs)
        except Exception as e:                       # noqa: BLE001 — surface, don't crash UI
            box["err"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    i = 0
    while t.is_alive():
        bar = None
        if progress is not None and progress.get("frac") is not None:
            f = max(0.0, min(1.0, progress["frac"]))
            bar = f"{_ascii_bar(f)} {int(f * 100):3d}%"
        elif indeterminate:
            bar = _marquee(i)
        draw(stdscr, msg, _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)], bar)
        i += 1
        curses.napms(120)                            # ~8 fps; cheap, smooth enough
    t.join()
    if "err" in box:
        _log(f"spinner work failed: {box['err']}")
    return box.get("val")


def _draw_tools_busy(stdscr, kind):
    """Paint a 'working' frame before a blocking TOOLS op. (Both installers
    are headless now and run under _run_with_spinner instead — this is the
    uninstall path's frame.)"""
    try:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        _draw_title_bar(stdscr, w)
        _draw_tab_strip(stdscr, CURRENT_TAB_TOOLS, w)
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
        y += 1  # extra breathing line between the chrome rules and the title
        put(y, 2, "TOOLS", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
        # Live background-install line. The toasts are the primary surface,
        # but they expire — an operator who came back to the Pitstop after
        # queueing something needs to see it is still going without waiting
        # for the next toast.
        st = _install_status() if BG_INSTALL_ENABLED else None
        queued = len(_install_queue()) if BG_INSTALL_ENABLED else 0
        if st:
            pct = f"  {st['pct']}%" if st.get('pct') else ""
            extra = f"  (+{queued - 1} waiting)" if queued > 1 else ""
            note = st.get('msg') or ''
            put(y, 4, f"{st['state']}: {st['name'][:24]}{pct}{extra}"
                      + (f"  - {note}" if note else ""),
                curses.color_pair(1) | curses.A_BOLD)
            y += 1
        elif queued:
            put(y, 4, f"{queued} install(s) queued", curses.color_pair(1))
            y += 1
        # Single-spaced items (the uninstall-list idiom): the rig's foot
        # terminal is only ~22 rows, and double spacing pushed everything
        # below into the footer.
        for i, label in enumerate(_TOOLS_MENU):
            if i == _TOOLS_SCREENSHOT_IDX:
                label = f"{label}: {_read_screenshot_mode()}"
            sel = (i == state.get("tools_cursor", 0))
            put(y, 4, "> " if sel else "  ",
                curses.color_pair(1) if sel else curses.A_NORMAL)
            put(y, 6, f"{i + 1}. {label}",
                curses.A_REVERSE if sel else curses.A_NORMAL)
            y += 1
        # On-select help for the entries that need context (install /
        # screenshot / firmware), ANCHORED to the footer from below
        # (h-6/h-5, one blank row above the h-3 rule) — never flowed
        # downward, so it cannot spill beneath the button-label footer
        # whatever the terminal height.
        cur = state.get("tools_cursor", 0)
        ty = max(y + 1, h - 6)
        if cur == _TOOLS_INSTALL_IDX:
            put(ty, 4, "Staging drop folder (place ONE .pkg + its .rap):",
                curses.A_DIM)
            put(ty + 1, 6, PKG_STAGING_DIR, curses.A_DIM)
        elif cur == _TOOLS_SCREENSHOT_IDX:
            put(ty + 1, 4, "Screenshot on L1: always / in-game / disabled "
                           "(CONFIRM cycles)", curses.A_DIM)
        elif cur == _TOOLS_FIRMWARE_IDX:
            put(ty, 4, "Firmware drop folder (place PS3UPDAT.PUP):",
                curses.A_DIM)
            put(ty + 1, 6, FIRMWARE_DROP_DIR, curses.A_DIM)
        elif cur == _TOOLS_UPDATE_IDX:
            put(ty, 4, "Checks GitHub releases; updates the ETK middleware",
                curses.A_DIM)
            put(ty + 1, 4, "in place - no computer needed (hostless update).",
                curses.A_DIM)

    elif mode == "install_confirm":
        pkg, raps, tid = state["tools_pkg"]
        try:
            mb = os.path.getsize(pkg) // (1024 * 1024)
        except Exception:
            mb = 0
        put(y, 2, "INSTALL - CONFIRM", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        put(y, 4, f"Package : {os.path.basename(pkg)[:w - 16]}"); y += 1
        put(y, 4, f"Size    : {mb} MB"); y += 1
        put(y, 4, f"Title ID: {tid}"); y += 1
        rap_txt = (", ".join(os.path.basename(r) for r in raps)[:w - 16]
                   if raps else "none staged")
        put(y, 4, f"Licence : {rap_txt}"); y += 2
        put(y, 4, "RPCS3 installs it headless, in the background.",
            curses.A_BOLD); y += 1
        put(y, 4, "Nothing opens on screen. Big games take a while.",
            curses.A_BOLD); y += 1
        put(y, 4, "The .pkg is removed from the drop folder when it",
            curses.A_DIM); y += 1
        put(y, 4, "succeeds, and kept if it fails so you can retry.",
            curses.A_DIM); y += 2
        put(y, 4, "B: Install     A: Cancel",
            curses.color_pair(1) | curses.A_BOLD)

    elif mode == "firmware_confirm":
        pup, cur_ver = state["tools_fw"]
        try:
            mb = os.path.getsize(pup) // (1024 * 1024)
        except Exception:
            mb = 0
        put(y, 2, "INSTALL PS3 FIRMWARE - CONFIRM", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        put(y, 4, f"File     : {os.path.basename(pup)}  ({mb} MB)"); y += 1
        if cur_ver:
            put(y, 4, f"Installed: firmware {cur_ver}  "
                      "(this will reinstall / overwrite)"); y += 1
        else:
            put(y, 4, "Installed: none yet - PS3 games need this to boot"); y += 1
        y += 1
        put(y, 4, "RPCS3 installs it headless, in the background (~1 min).",
            curses.A_BOLD); y += 1
        put(y, 4, "Do not launch a game until it finishes.",
            curses.A_BOLD); y += 1
        put(y, 4, "The .pup file is KEPT for reuse.", curses.A_DIM); y += 2
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

    elif mode == "update_confirm":
        info = state.get("tools_update") or {}
        put(y, 2, "ETK UPDATE AVAILABLE", curses.A_BOLD); y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        put(y, 4, f"Installed : v{APP_VERSION}"); y += 1
        put(y, 4, f"Latest    : {info.get('tag', '?')}  "
                  f"({info.get('name', '')})"); y += 2
        put(y, 4, "Updates the ETK middleware in place (daemons, scripts,",
            curses.A_DIM); y += 1
        put(y, 4, "Pitstop, schemas). Emulator / driver / kernel update via",
            curses.A_DIM); y += 1
        put(y, 4, "install.sh or a new card image. Your etk.conf, vault,",
            curses.A_DIM); y += 1
        put(y, 4, "telemetry and game configs are not touched.",
            curses.A_DIM); y += 2
        put(y, 4, "B: Update now     A: Cancel",
            curses.color_pair(1) | curses.A_BOLD)

    elif mode == "update_done":
        ok, lines = state.get("tools_result") or (False, ["(no result)"])
        put(y, 2, "UPDATED", curses.A_BOLD | curses.color_pair(PAIR_CLEAN))
        y += 1
        put(y, 2, "-" * (w - 4), curses.A_DIM); y += 2
        for ln in lines:
            if y >= h - 4:
                break
            put(y, 4, ln)
            y += 1
        if y < h - 4:
            y += 1
            put(y, 4, "B: Restart Pitstop now     A: Later",
                curses.color_pair(1) | curses.A_BOLD)

    elif mode == "shaders":
        model = state.get("shaders_model")
        if not model:
            put(y, 4, "Scanning shader vault…", curses.A_DIM)
        else:
            _draw_shader_screen(stdscr, state, model, y, h, w)

    elif mode == "shaders_confirm":
        _draw_shader_confirm(stdscr, state, y, h, w)

    elif mode == "trigcal":
        _draw_trigcal_screen(stdscr, state, y, h, w)

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


# === TOOLS TAB — TRIGGER CALIBRATION (H7) ===
# L2/R2 analog deadzone calibration for RPCS3's Player-1 pad config.
# Why on-device: the DS5 trigger axes rest NONZERO after first actuation
# (L2 ~12/255, kernel FLAT=0), so RPCS3's 'Trigger Threshold: 0' drags the
# brake continuously — and RPCS3's own pad dialog is unusable on the 5"
# panel (the PKG-installer rationale). History: threshold 25 hand-validated
# 2026-06-16, lost to the 2026-07-02 config regeneration; this screen makes
# recovery self-service. The live gauge reads the SAME evdev pad fd Pitstop
# already owns — ABS_Z/ABS_RZ arrive there and were previously dropped; the
# main loop folds them into the model every frame (bounded drain) so the
# gauge stays live through analog event storms. SAVE writes ONLY Player 1's
# threshold lines via the H2 tmp+os.replace idiom, verifies by re-read, and
# is guarded by _rpcs3_running() at WRITE time (RPCS3 rewrites its config on
# exit — a save made while it runs silently vanishes; AI_MANIFEST law).

_TRIGCAL_ROWS = ("l2", "l2top", "r2", "r2top", "auto", "save")  # cursor order
_TRIGCAL_MARGIN = 13   # AUTO = live rest + margin (12+13=25 = the 06-16 fix)
# Top-end calibration (H7b): a trigger that physically saturates below 255
# (R2 seen max ~<255 on this unit) can never report a full pull, so the game
# never sees 100% throttle/brake. 'Trigger Max' (GTK Edition >= trigger-cal
# build; stock RPCS3 ignores it) rescales the calibrated ceiling to full
# deflection. AUTO sets top = envelope max - margin so a real full pull
# always saturates; envelope max must clear the floor to count as a pull.
_TRIGCAL_TOP_MARGIN = 4
_TRIGCAL_TOP_FLOOR = 200


def _trigcal_handler_scale(handler):
    """Config-unit scale for 'Trigger Threshold' per pad handler. The SDL
    handler stores thresholds in SDL axis units (0-32767: trigger_max =
    SDL_JOYSTICK_AXIS_MAX, confirmed in rpcs3/Input/sdl_pad_handler.cpp);
    the HID handlers (DualSense/DualShock/evdev) use the DS byte range
    0-255. The 2026-07-02 brake-drag regression was exactly this trap: a
    255-scale '25' written into an SDL-handler config = 0.08% of travel =
    no deadzone at all. The screen works in RAW pad units (0-255, what the
    live evdev gauge reads) and converts at the file boundary."""
    return 32767 if str(handler).strip().upper() == "SDL" else 255


def _trigcal_read_thresholds():
    """Parse Player 1's Handler + Left/Right Trigger Threshold + Left/Right
    Trigger Max (config units) from RPCS3_PAD_CONFIG. Returns
    (l, r, lmax, rmax, handler, err). The Max keys are the GTK-Edition
    top-end calibration and are absent on stock-written configs -> None
    (stock RPCS3 also drops them on its exit rewrite; the screen then just
    shows top-end 'off', which is honest). Player 1 is the FIRST top-level
    section in the file; scanning stops at the next col-0 header so the
    Null pads 2-7 are never read (FIX 5 section gate)."""
    try:
        with open(RPCS3_PAD_CONFIG) as f:
            lines = f.readlines()
    except OSError as e:
        return None, None, None, None, None, f"cannot read pad config: {e}"
    l = r = lmax = rmax = handler = None
    in_p1 = False
    for ln in lines:
        if ln and ln[0] not in " \t" and ln.rstrip().endswith(":"):
            if in_p1:
                break
            in_p1 = True
            continue
        if not in_p1:
            continue
        s = ln.strip()
        if s.startswith("Handler:"):
            handler = s.split(":", 1)[1].strip().strip('"')
        elif s.startswith("Left Trigger Threshold:"):
            try:
                l = int(s.split(":", 1)[1])
            except ValueError:
                pass
        elif s.startswith("Right Trigger Threshold:"):
            try:
                r = int(s.split(":", 1)[1])
            except ValueError:
                pass
        elif s.startswith("Left Trigger Max:"):
            try:
                lmax = int(s.split(":", 1)[1])
            except ValueError:
                pass
        elif s.startswith("Right Trigger Max:"):
            try:
                rmax = int(s.split(":", 1)[1])
            except ValueError:
                pass
    if l is None or r is None:
        return None, None, None, None, handler, \
            "Trigger Threshold keys not found in Player 1 block"
    return l, r, lmax, rmax, handler, None


def _trigcal_new_model():
    l, r, lmax, rmax, handler, err = _trigcal_read_thresholds()
    scale = _trigcal_handler_scale(handler)

    def to_raw(cfg):
        if cfg is None:
            return 25
        return max(0, min(255, int(round(cfg * 255.0 / scale))))

    def to_raw_top(cfg):
        # Absent key (stock-written config) or 0 = top-end cal off.
        if not cfg:
            return 0
        return max(0, min(255, int(round(cfg * 255.0 / scale))))

    return {
        "l2_thr": to_raw(l),              # RAW pad units (0-255) in the UI
        "r2_thr": to_raw(r),
        "l2_top": to_raw_top(lmax),       # top-end cal, raw units; 0 = off
        "r2_top": to_raw_top(rmax),
        "handler": handler or "?",
        "scale": scale,                   # config units per full travel
        "load_err": err,
        "l2": 0, "r2": 0,                 # live axis values
        "l2_min": None, "l2_max": None,   # observed envelope this session
        "r2_min": None, "r2_max": None,
        "dirty": False,
    }


def _trigcal_axis(state, code, val):
    """Fold a live ABS_Z/ABS_RZ event into the model (called per event from
    the main loop's bounded drain)."""
    m = state.get("trigcal_model")
    if not m:
        return
    key = "l2" if code == ABS_Z else "r2"
    m[key] = val
    mn, mx = m[key + "_min"], m[key + "_max"]
    m[key + "_min"] = val if mn is None else min(mn, val)
    m[key + "_max"] = val if mx is None else max(mx, val)


def _trigcal_adjust(state, delta):
    """DPAD left/right on a threshold row: nudge by +/-1, clamp 0-255.
    Top-end rows: same nudge, except RIGHT from 'off' (0) jumps straight to
    a useful start (envelope max - margin if a pull was seen, else 251) so
    the dial isn't 200+ presses away, and LEFT below 64 snaps back to off
    (a top-end cal that low would multiply the whole axis absurdly)."""
    m = state.get("trigcal_model")
    if not m:
        return
    row = _TRIGCAL_ROWS[state.get("tools_cursor", 0) % len(_TRIGCAL_ROWS)]
    if row in ("l2", "r2"):
        k = row + "_thr"
        m[k] = max(0, min(255, (m.get(k) or 0) + delta))
        m["dirty"] = True
    elif row in ("l2top", "r2top"):
        k = row[:2] + "_top"
        cur = m.get(k) or 0
        if cur == 0 and delta > 0:
            seen = m.get(row[:2] + "_max")
            new = (seen - _TRIGCAL_TOP_MARGIN) if (seen or 0) >= _TRIGCAL_TOP_FLOOR else 251
        else:
            new = cur + delta
        if new < 64:
            new = 0
        m[k] = min(255, new)
        m["dirty"] = True


def _trigcal_save(state):
    """Write the model's thresholds into Player 1's block only. Fresh
    game-running guard at write time, byte-copy backup, H2 tmp+os.replace,
    then verify by re-read (a silent save failure must never look like a
    success). Returns a (ok, lines) tools_result."""
    if _rpcs3_running():
        return (False, ["RPCS3 is running.",
                        "Quit the game first, then retry.",
                        "(RPCS3 rewrites its pad config on exit -",
                        "a save made now would be silently lost.)"])
    m = state.get("trigcal_model") or {}
    raw_l, raw_r = m.get("l2_thr"), m.get("r2_thr")
    if raw_l is None or raw_r is None:
        return (False, ["No thresholds to save."])
    top_l, top_r = m.get("l2_top") or 0, m.get("r2_top") or 0
    # RAW pad units (0-255, the UI/gauge domain) -> the handler's config
    # units at the file boundary (SDL = 0-32767; HID handlers = 0-255).
    scale = m.get("scale") or 255
    want_l = int(round(raw_l * scale / 255.0))
    want_r = int(round(raw_r * scale / 255.0))
    want_lt = int(round(top_l * scale / 255.0))   # 0 stays 0 = cal off
    want_rt = int(round(top_r * scale / 255.0))
    try:
        with open(RPCS3_PAD_CONFIG) as f:
            lines = f.readlines()
    except OSError as e:
        return (False, [f"cannot read pad config: {e}"])
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # The 'Trigger Max' keys (GTK-Edition top-end cal) are ABSENT from a
    # config last written by stock RPCS3 (it drops unknown keys on exit).
    # Replace them in place when present; otherwise INSERT each right after
    # its Trigger Threshold line, same indent — a deterministic anchor that
    # already gates the save (hit==2 below).
    in_p1, seen_hdr = False, False
    have_lt = have_rt = False
    for ln in lines:
        if ln and ln[0] not in " \t" and ln.rstrip().endswith(":"):
            in_p1 = not seen_hdr
            seen_hdr = True
        if in_p1:
            s = ln.lstrip()
            if s.startswith("Left Trigger Max:"):
                have_lt = True
            elif s.startswith("Right Trigger Max:"):
                have_rt = True
    out, in_p1, seen_hdr, hit, hit_top = [], False, False, 0, 0
    for ln in lines:
        if ln and ln[0] not in " \t" and ln.rstrip().endswith(":"):
            in_p1 = not seen_hdr
            seen_hdr = True
        inserted = None
        if in_p1:
            s = ln.lstrip()
            indent = ln[:len(ln) - len(s)]
            if s.startswith("Left Trigger Threshold:"):
                ln = f"{indent}Left Trigger Threshold: {want_l}\n"
                hit += 1
                if not have_lt:
                    inserted = f"{indent}Left Trigger Max: {want_lt}\n"
                    hit_top += 1
            elif s.startswith("Right Trigger Threshold:"):
                ln = f"{indent}Right Trigger Threshold: {want_r}\n"
                hit += 1
                if not have_rt:
                    inserted = f"{indent}Right Trigger Max: {want_rt}\n"
                    hit_top += 1
            elif s.startswith("Left Trigger Max:"):
                ln = f"{indent}Left Trigger Max: {want_lt}\n"
                hit_top += 1
            elif s.startswith("Right Trigger Max:"):
                ln = f"{indent}Right Trigger Max: {want_rt}\n"
                hit_top += 1
        out.append(ln)
        if inserted:
            out.append(inserted)
    if hit != 2 or hit_top != 2:
        return (False, [f"Expected 2 threshold + 2 max lines in Player 1,",
                        f"found {hit} + {hit_top}.",
                        "Pad config layout unrecognized - not writing.",
                        "(Is a pad configured in RPCS3 at all?)"])
    bak = RPCS3_PAD_CONFIG + ".etkbak-trigcal"
    try:
        with open(RPCS3_PAD_CONFIG, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
    except OSError:
        bak = None
    tmp = RPCS3_PAD_CONFIG + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.writelines(out)
        os.replace(tmp, RPCS3_PAD_CONFIG)   # atomic on POSIX
    except OSError as e:
        return (False, [f"write failed: {e}"])
    vl, vr, vlt, vrt, _vh, err = _trigcal_read_thresholds()
    if err or vl != want_l or vr != want_r \
            or (vlt or 0) != want_lt or (vrt or 0) != want_rt:
        return (False, ["VERIFY FAILED after write:",
                        err or f"read back L={vl} R={vr} Lmax={vlt} Rmax={vrt},"
                               f" wanted {want_l}/{want_r}/{want_lt}/{want_rt}"])
    m["dirty"] = False

    def _top_str(raw, cfg):
        return f"raw {raw}/255 -> {cfg}" if raw else "off"
    result = [f"Player 1 trigger calibration saved:",
              f"  L2 thr raw {raw_l}/255 -> {want_l} ({m.get('handler', '?')} scale {scale})",
              f"  R2 thr raw {raw_r}/255 -> {want_r}",
              f"  L2 top-end {_top_str(top_l, want_lt)}",
              f"  R2 top-end {_top_str(top_r, want_rt)}",
              "Verified by re-read."]
    if bak:
        result.append(f"Backup: {os.path.basename(bak)}")
    result += ["", "Takes effect on the next game launch.",
               "Top-end cal needs the GTK Edition trigger-cal",
               "build (stock RPCS3 ignores + drops the Max keys)."]
    return (True, result)


def _draw_trigcal_screen(stdscr, state, y, h, w):
    def put(row, col, text, attr=curses.A_NORMAL):
        try:
            stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)
        except curses.error:
            pass
    m = state.get("trigcal_model") or {}
    cur = state.get("tools_cursor", 0) % len(_TRIGCAL_ROWS)
    put(y, 2, "TRIGGER CALIBRATION  (RPCS3 Player 1)", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
    put(y, 4, f"pad handler: {m.get('handler', '?')}   "
              f"(config scale 0-{m.get('scale', 255)})", curses.A_DIM); y += 1
    if m.get("load_err"):
        put(y, 4, m["load_err"], curses.color_pair(PAIR_CRASH)); y += 1
    y += 1
    for key, label in (("l2", "L2 (brake)"), ("r2", "R2 (accel)")):
        live = m.get(key) or 0
        thr = m.get(key + "_thr") or 0
        top = m.get(key + "_top") or 0
        mn, mx = m.get(key + "_min"), m.get(key + "_max")
        sel = (_TRIGCAL_ROWS[cur] == key)
        bw = max(20, min(64, w - 26))
        fill = int(round(live / 255.0 * bw))
        tpos = min(bw - 1, int(round(thr / 255.0 * (bw - 1))))
        bar = "".join("#" if i < fill else "-" for i in range(bw))
        bar = bar[:tpos] + "|" + bar[tpos + 1:]
        if top:
            # ']' marks the calibrated top-end ceiling; values there = full.
            xpos = min(bw - 1, int(round(top / 255.0 * (bw - 1))))
            bar = bar[:xpos] + "]" + bar[xpos + 1:]
        put(y, 4, "> " if sel else "  ",
            curses.color_pair(1) if sel else curses.A_NORMAL)
        put(y, 6, f"{label:11s}", curses.A_REVERSE if sel else curses.A_BOLD)
        active = live > thr
        put(y, 19, f"[{bar}]",
            curses.color_pair(PAIR_CLEAN) if active else curses.A_NORMAL)
        y += 1
        env = (f"live {live:3d}   threshold {thr:3d}   seen rest/max: "
               + (f"{mn}/{mx}" if mn is not None else "-/-"))
        put(y, 19, env, curses.A_DIM)
        y += 1
        # Top-end calibration row (H7b) — its own cursor row so DPAD/A can
        # drive it; shows the saturation ceiling the fork rescales to full.
        sel = (_TRIGCAL_ROWS[cur] == key + "top")
        put(y, 4, "> " if sel else "  ",
            curses.color_pair(1) if sel else curses.A_NORMAL)
        put(y, 6, f"{key.upper()} top-end", curses.A_REVERSE if sel else curses.A_NORMAL)
        topdesc = f"{top:3d}  (full pull rescales to 255)" if top else \
            "off  (A: set from seen max after a full pull)"
        put(y, 19, topdesc, curses.A_BOLD if top else curses.A_DIM)
        y += 1
    y += 1
    sel = (_TRIGCAL_ROWS[cur] == "auto")
    put(y, 4, "> " if sel else "  ",
        curses.color_pair(1) if sel else curses.A_NORMAL)
    put(y, 6, "AUTO-SET  (pull both fully, release, then press A)",
        curses.A_REVERSE if sel else curses.A_NORMAL)
    y += 2
    sel = (_TRIGCAL_ROWS[cur] == "save")
    put(y, 4, "> " if sel else "  ",
        curses.color_pair(1) if sel else curses.A_NORMAL)
    lab = "SAVE to RPCS3 pad config" + ("  [UNSAVED]" if m.get("dirty") else "")
    put(y, 6, lab, curses.A_REVERSE if sel else
        (curses.A_BOLD if m.get("dirty") else curses.A_NORMAL))
    y += 2
    if y < h - 5:
        put(y, 4, "DPAD up/down: row    left/right: value -/+1",
            curses.A_DIM); y += 1
        put(y, 4, f"AUTO: bottom = rest+{_TRIGCAL_MARGIN} (drag fix); top-end = seen max",
            curses.A_DIM); y += 1
        put(y, 4, f"-{_TRIGCAL_TOP_MARGIN} (saturation fix; needs GTK trigger-cal build).",
            curses.A_DIM); y += 1
        put(y, 4, "B: back (discards unsaved changes)", curses.A_DIM)


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
    elif mode == "trigcal":
        state["tools_cursor"] = (state.get("tools_cursor", 0) + delta) % len(_TRIGCAL_ROWS)


def _tools_select(state):
    """A / CONFIRM action. May queue a blocking op via state['tools_action']
    for the main loop to run. Always returns 'continue' (TOOLS never
    save_exits — B from the menu is the only quit path)."""
    mode = state.get("tools_mode", "menu")

    if mode == "menu":
        if state.get("tools_cursor", 0) == _TOOLS_INSTALL_IDX:        # Install
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
                # Every staged licence goes with it: RAPs are keyed by content
                # id, so an extra one is inert and a missing one is not.
                state["tools_pkg"] = (pkg, raps,
                                      _pkg_title_id(pkg) or "unknown")
                state["tools_mode"] = "install_confirm"
        elif state.get("tools_cursor", 0) == _TOOLS_UNINSTALL_IDX:      # Uninstall
            state["tools_games"] = _list_psn_games()
            state["tools_cursor"] = 0
            state["tools_mode"] = "uninstall_list"
        elif state.get("tools_cursor", 0) == _TOOLS_SHADERS_IDX:   # Manage Shaders
            state["tools_mode"] = "shaders"
            state["tools_cursor"] = 0
            state["shaders_scope_all"] = False
            state["shaders_model"] = None
            state["shaders_scan_request"] = True     # main loop scans w/ busy frame
        elif state.get("tools_cursor", 0) == _TOOLS_TRIGCAL_IDX:   # Trigger Cal
            state["tools_mode"] = "trigcal"
            state["tools_cursor"] = 0
            state["trigcal_model"] = _trigcal_new_model()
        elif state.get("tools_cursor", 0) == _TOOLS_FIRMWARE_IDX:   # Firmware
            pups = _scan_firmware()
            if len(pups) == 0:
                state["tools_result"] = (False, [
                    "No firmware file staged.",
                    "",
                    "Download the official PS3 firmware (PS3UPDAT.PUP)",
                    "from Sony's PS3 system-software page, then drop it",
                    "into the firmware folder:",
                    "  " + FIRMWARE_DROP_DIR,
                    "and choose Install PS3 Firmware again."])
                state["tools_mode"] = "result"
            elif len(pups) > 1:
                state["tools_result"] = (False, [
                    f"{len(pups)} .pup files staged - keep only one.",
                    "",
                    "Leave a single PS3UPDAT.PUP in the folder:",
                    "  " + FIRMWARE_DROP_DIR])
                state["tools_mode"] = "result"
            else:
                state["tools_fw"] = (pups[0], _installed_fw_version())
                state["tools_mode"] = "firmware_confirm"
        elif state.get("tools_cursor", 0) == _TOOLS_UPDATE_IDX:    # Self-update
            state["tools_action"] = ("update_check",)
        else:                                        # Screenshot-on-L1 toggle
            state["status"] = f"Screenshot on L1: {_cycle_screenshot_mode()}"

    elif mode == "install_confirm":
        pkg, raps, _tid = state["tools_pkg"]
        state["tools_action"] = ("install", pkg, raps)

    elif mode == "firmware_confirm":
        pup, _ver = state["tools_fw"]
        state["tools_action"] = ("firmware", pup)

    elif mode == "update_confirm":
        state["tools_action"] = ("update_apply", state.get("tools_update") or {})

    elif mode == "update_done":
        # B on the post-update screen = restart Pitstop into the new code.
        # execv never returns; the foot terminal wrapper is preserved.
        try:
            curses.endwin()
        except Exception:
            pass
        os.execv(sys.executable,
                 [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

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

    elif mode == "trigcal":
        m = state.get("trigcal_model") or {}
        row = _TRIGCAL_ROWS[state.get("tools_cursor", 0) % len(_TRIGCAL_ROWS)]
        if row == "auto":
            # Live values ARE the rest residuals when both triggers are
            # released — the whole point of the DS5 nonzero-rest bug.
            m["l2_thr"] = max(0, min(255, (m.get("l2") or 0) + _TRIGCAL_MARGIN))
            m["r2_thr"] = max(0, min(255, (m.get("r2") or 0) + _TRIGCAL_MARGIN))
            # Top-end: only when a genuine full pull was observed this
            # session (envelope max past the floor). Max at true 255 means
            # no top deadzone -> cal off. Never clobber an existing cal
            # with 'no pull seen'.
            for k in ("l2", "r2"):
                mx = m.get(k + "_max")
                if mx is not None and mx >= _TRIGCAL_TOP_FLOOR:
                    m[k + "_top"] = 0 if mx >= 255 else max(64, mx - _TRIGCAL_TOP_MARGIN)
            m["dirty"] = True
        elif row in ("l2top", "r2top"):
            # A on a top-end row = set from this session's observed max
            # (per-trigger AUTO). No pull seen yet -> leave unchanged.
            k = row[:2]
            mx = m.get(k + "_max")
            if mx is not None and mx >= _TRIGCAL_TOP_FLOOR:
                m[k + "_top"] = 0 if mx >= 255 else max(64, mx - _TRIGCAL_TOP_MARGIN)
                m["dirty"] = True
        elif row == "save":
            state["tools_result"] = _trigcal_save(state)
            state["tools_mode"] = "result"
        # l2/r2 threshold rows adjust via DPAD left/right; A is a no-op there

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
    elif mode == "trigcal":
        state["tools_mode"] = "menu"
        state["tools_cursor"] = _TOOLS_TRIGCAL_IDX
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
    elif ch == curses.KEY_LEFT and state.get("tools_mode") == "trigcal":
        _trigcal_adjust(state, -1)
    elif ch == curses.KEY_RIGHT and state.get("tools_mode") == "trigcal":
        _trigcal_adjust(state, 1)
    elif ch == ord('\n') or ch == ord('s'):
        return _tools_select(state)
    elif ch in (curses.KEY_BACKSPACE, 8, 127):
        return _tools_back(state)
    return "continue"


def handle_tools_pad(state, etype, code, val):
    """Gamepad input for TOOLS. L1/R1 intercepted upstream for tabs.
    Trigger-axis events (ABS_Z/ABS_RZ) never reach here — the main loop's
    bounded drain folds them into the trigcal model directly. DPAD
    left/right is only consumed on the trigcal screen (threshold nudge)."""
    if etype == EV_KEY and val == 1 and code == BTN_CONFIRM:
        return _tools_select(state)
    if etype == EV_KEY and val == 1 and code == BTN_BACK:
        return _tools_back(state)
    if etype == EV_ABS and code == ABS_HAT0Y:
        if val == -1:
            _tools_move(state, -1)
        elif val == 1:
            _tools_move(state, 1)
    if (etype == EV_ABS and code == ABS_HAT0X
            and state.get("tools_mode") == "trigcal"):
        if val == -1:
            _trigcal_adjust(state, -1)
        elif val == 1:
            _trigcal_adjust(state, 1)
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
                      # Patch #7 (gtk 0.5+): widen upstream's A630/A650
                      # EARLY_Z_LATE_Z hang workaround past its D32S8 format gate.
                      # zlatez adds D24_UNORM_S8_UINT (our Z24S8 target); zlatezany
                      # drops the format gate entirely. First fork gear that is NOT
                      # a resolve mechanism — it targets the fragment stage, which
                      # is where the decode says the fault lives. UNVALIDATED:
                      # run the dimlog probe below FIRST (see DIAGNOSTIC row).
                      "zlatez", "zlatezany",
                      # Patch #3 "B" cure-candidate: cap the a6xx depth CCU cache
                      # size (FULL->HALF/QUARTER) to attack the DEPTH_CACHE=FULL
                      # boss-resolve saturation. (FALSIFIED — cache-size isn't the
                      # lever; quarter is worse. Kept for the record.)
                      "ccuhalf", "ccuquarter",
                      # Patch #3 "A" cure-candidate: dsbypass routes depth-STORING
                      # renderpasses to sysmem to bypass the GMEM depth resolve
                      # (missed the boss's alignment-driven resolve). dsany is the
                      # refinement: ANY depth-attachment pass -> sysmem (catches the
                      # boss pass). REQUIRES the gtk fork .so.
                      "dsbypass", "dsany")

# --- DIAGNOSTIC flags (always visible, NOT behind Advanced) ----------------
# These don't change how the driver renders — they only make it report. They
# sit outside the ROAD FEEL stability<->performance axis on purpose: a
# diagnostic is orthogonal to a tuning trade, and burying it in Advanced is
# what made the cheap falsification step easy to skip.
#
# dimlog (REQUIRES a gtk fork .so; inert on stock Turnip):
#   - GMEM render dims + ragged remainder at tiling setup ("[ETK dimlog]")
#   - on gtk 0.5+, the zlatez reachability probe ("[ETK zlatez] hazard state
#     reached: ... depth_format=..."), which fires with NO z-gear set. If that
#     line never appears on a session, zlatez/zlatezany are inert and the
#     hypothesis is falsified for one session instead of an N>=3 A/B.
_TU_DEBUG_DIAG = ("dimlog",)

_TU_DEBUG_KNOWN = (set(_TU_DEBUG_PRIMARY) | set(_TU_DEBUG_ADVANCED)
                   | set(_TU_DEBUG_DIAG))


# --- ROAD FEEL dial (the simple DRIVER view) -------------------------------
# Translates the cryptic TU_DEBUG gears into three plain stops on a
# stability<->performance axis. Each stop sets tu_debug to a PROVEN combo
# (etk-turnip-gtk/GEARS.md): syncdraw is the best-tested crash floor; sddepth
# is the validated FPS-recovery gear (lighter, more wedge-prone — but the
# anti-lock net now absorbs those as SURVIVED rescues); no barrier is
# stock/leanest. The falsified lighter gears (sdmem/sdme) are deliberately NOT
# offered here — they stay in Advanced for the operator's own A/B work.
_DIAL_STOPS = (
    ("Max Stability",   "syncdraw - safest; the proven crash floor, fewest saves", {"syncdraw"}),
    ("Balanced",        "sddepth - more FPS in heavy scenes; anti-lock covers the wedges", {"sddepth"}),
    ("Max Performance", "no barrier - leanest, most FPS; leans hardest on anti-lock", set()),
)


def _dial_index(model):
    """Which ROAD FEEL stop the live tu_debug matches EXACTLY, or None
    ('Custom' — a hand-mixed / experimental flag set from Advanced).

    Diagnostic flags are excluded from the match: they don't change how the
    driver renders, so a probe like dimlog must not knock the dial off its
    stop (which would also auto-open Advanced in _driver_load). 'Max Stability
    + dimlog' is still Max Stability."""
    td = model["tu_debug"] - set(_TU_DEBUG_DIAG)
    for i, (_lbl, _desc, s) in enumerate(_DIAL_STOPS):
        if td == s:
            return i
    return None


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
    # Open Advanced automatically only when the live tune ISN'T a simple ROAD
    # FEEL stop (a hand-mixed / experimental flag set the dial can't show) —
    # a plain syncdraw / sddepth / none tune stays on the simple dial view.
    if _dial_index(model) is None and model["tu_debug"]:
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
    rows = [("build", None), ("build_apply", None), ("reboot", None),
            ("dial", None)]
    # Diagnostics sit above Advanced, always reachable: the dimlog probe is the
    # cheap falsification step that comes BEFORE an N>=3 A/B, so it must not be
    # hidden behind a disclosure toggle.
    rows += [("diag", f) for f in _TU_DEBUG_DIAG]
    rows += [("advanced", None)]
    if model["show_advanced"]:
        rows += [("autotune", None)]
        rows += [("flag", f) for f in _TU_DEBUG_PRIMARY]
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

    y = 2
    put(y, 2, _chassis_string(), curses.A_DIM); y += 1
    put(y, 2, "TURNIP DRIVER", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
    put(y, 4, f"Loaded now: {driver_string()}", curses.A_BOLD); y += 1
    put(y, 4, "BUILD picks the .so (reboot to load); dials tune it next launch.",
        curses.A_DIM); y += 1

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
        elif kind == "dial":
            di = _dial_index(model)
            lbl = _DIAL_STOPS[di][0] if di is not None else "Custom (Advanced)"
            put(y, 6, f"ROAD FEEL   < {lbl} >", base | curses.A_BOLD)
        elif kind == "autotune":
            put(y, 6, f"TU_AUTOTUNE_ALGO   < {_AUTOTUNE_VALUES[model['autotune_idx']]} >", base)
        elif kind == "flag":
            on = payload in model["tu_debug"]
            tail = curses.color_pair(PAIR_CLEAN) if (on and not sel) else base
            put(y, 6, f"TU_DEBUG  {'[x]' if on else '[ ]'} {payload}", tail)
        elif kind == "diag":
            on = payload in model["tu_debug"]
            tail = curses.color_pair(PAIR_CLEAN) if (on and not sel) else base
            put(y, 6, f"DIAGNOSTIC  {'[x]' if on else '[ ]'} {payload}"
                      "   (logs only; needs fork .so)", tail)
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
    di = _dial_index(model)
    feel = (_DIAL_STOPS[di][1] if di is not None
            else "Custom - raw flags set via Advanced")
    put(y, 4, "Road feel: " + feel, curses.A_DIM); y += 1
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
    elif kind == "dial":
        _driver_set_dial(state, delta)
    elif kind == "autotune":
        model["autotune_idx"] = (model["autotune_idx"] + delta) % len(_AUTOTUNE_VALUES)
        state["driver_notice"] = None
    elif kind in ("flag", "diag"):
        _driver_toggle_flag(state, payload)


def _driver_set_dial(state, delta):
    """Cycle the ROAD FEEL dial and set tu_debug to that stop's proven combo.
    From 'Custom', a step enters the ladder at the nearest end."""
    model = state["driver_model"]
    idx = _dial_index(model)
    if idx is None:
        idx = 0 if delta >= 0 else len(_DIAL_STOPS) - 1
    else:
        idx = (idx + delta) % len(_DIAL_STOPS)
    model["tu_debug"] = set(_DIAL_STOPS[idx][2])
    state["driver_notice"] = None


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
    elif kind in ("flag", "diag"):
        _driver_toggle_flag(state, payload)
    elif kind == "dial":
        _driver_set_dial(state, 1)
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
    # Knobs the presets deliberately omit (e.g. `grid`: presets don't define it so
    # the preset identity / pwr= tag is unaffected by the rung) still need a value
    # for the UI row and the profile record — default to their first option.
    for k in schema.get("knobs", []):
        vals.setdefault(k["id"], k["options"][0]["v"] if k.get("options") else "")
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
    tag = p.lower() if p != "CUSTOM" else "custom"
    # GRID rides the tag as a suffix (race+gA) instead of a preset flip: presets
    # omit the grid knob, so the rung never mushes preset identity to CUSTOM and
    # the ledger's pwr= column attributes both dimensions in one token.
    g = model["values"].get("grid")
    if g and g != "off":
        tag += "+g" + g
    return tag


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
    # GRID knob has no sysfs path — its engine is bin/grid_apply.sh (thread
    # affinity on the live emulator). Apply the rung now (fail-silent, non-
    # blocking; a no-game state is a clean no-op) — thermal_d re-asserts it at
    # every ignition, which is what makes the knob reboot-safe.
    g = vals.get("grid")
    if g is not None:
        try:
            subprocess.Popen([os.path.join(ETK_ROOT, "bin", "grid_apply.sh"), g],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
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

    y = 2
    put(y, 2, _chassis_string(), curses.A_DIM); y += 1
    put(y, 2, "POWER PROFILE", curses.A_BOLD); y += 1
    put(y, 2, "-" * (w - 4), curses.A_DIM); y += 1
    put(y, 4, "CPU/GPU governors + clock pinning — no OC (OPP-capped). Live + reboot-safe.",
        curses.A_DIM); y += 1
    put(y, 4, "Coordinates with thermal_d: PIT cooldown overrides, then returns here.",
        curses.A_DIM); y += 1

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
                    # "Gran Turismo 5 (BCUS98114).iso" -> "Gran Turismo 5"
                    nm = _strip_serial_tag(os.path.splitext(base)[0])
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


def _paddock_busy(stdscr, msg, spin=None, bar=None):
    """PADDOCK 'working' frame. `spin` animates the ROCKNIX throbber; `bar` is an
    optional pre-rendered progress bar (indeterminate marquee for the opaque
    network / git sync) drawn between the throbber and the notifications hint."""
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
        if bar:
            stdscr.addstr(h // 2 + 3, max(2, (w - len(bar)) // 2), bar[:w - 4],
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
    put(2, 2, f"PADDOCK  ·  {_chassis_string()}", curses.A_DIM)
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

    put(4, 2, f"PRIVATE PADDOCK · {state.get('paddock_repo', '?')}", curses.A_BOLD)
    put(5, 2, f"chipset: {state.get('paddock_chipset', '')}   "
              f"{state.get('paddock_driver_note', '')}", curses.A_DIM)
    put(6, 2, "-" * (w - 4), curses.A_DIM)

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
    put(7, 4, "GAME", curses.A_DIM)
    put(7, C_LOCAL, "LOCAL", curses.A_DIM)
    put(7, C_REMOTE, "PADDOCK", curses.A_DIM)
    put(7, C_PUSH, "ACTION", curses.A_DIM)

    sel = state.get("paddock_sel", 0)
    field = state.get("paddock_field", 0)
    top = 8
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
    notifier.post(verb.upper(), name[:30], timeout=NOTIFY_PROGRESS_TTL)
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
            notifier.post(verb.upper(), f"{name[:20]}  {ln[:28]}",
                          timeout=NOTIFY_PROGRESS_TTL)
        proc.wait(timeout=1800)
        ok = proc.returncode == 0
    except Exception as e:
        return False, [f"{verb} failed: {e}"] + lines[-10:]
    notifier.close()
    _Notifier().post(f"{verb.upper()} {'COMPLETE' if ok else 'FAILED'}",
                     name[:40], timeout=10000)
    tail = lines[-12:]
    head = [f"{verb.upper()} {'OK' if ok else 'FAILED'}: {name}", ""]
    return ok, head + tail


def _ascii_bar(frac, width=18):
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    fill = int(frac * width)
    return "[" + "#" * fill + "-" * (width - fill) + "]"


def _marquee(i, width=18, block=4):
    """Indeterminate progress sweep: a `block`-wide run of # that walks (and
    wraps) across a `width` track by frame index `i`. For opaque ops (network /
    git) that can't report a real fraction — a moving bar reads as 'still
    working' where a static 0% bar would read as stalled."""
    start = i % width
    cells = ["-"] * width
    for k in range(block):
        cells[(start + k) % width] = "#"
    return "[" + "".join(cells) + "]"


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
    """Download `url` to `dest` behind ES's upper-right progress card.

    The card is re-posted every ~1.5s: that both advances the bar and holds
    the toast open, since mako expires a notification on its own timeout and
    clears the bar on any replace that omits the `value` hint. Where the
    server reports no Content-Length there is no honest fraction, so the card
    shows megabytes only and the bar stays hidden — which is what ES does for
    a job that reports no percentage. Returns curl's return code (0 = ok,
    124 = timeout).

    (Historical note: this used to draw an ASCII bar because ETK believed
    mako had no progress widget. The shipped mako is 1.10.0, which renders
    the standard `value` hint natively — see _Notifier.)"""
    total = _curl_total_bytes(url)
    notifier.post("DOWNLOADING", label, timeout=NOTIFY_PROGRESS_TTL, value=0)
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
            pct = int(100 * got / total)
            notifier.post(f"DOWNLOADING  {pct}%",
                          f"{label}  {mb}/{total >> 20} MB",
                          timeout=NOTIFY_PROGRESS_TTL, value=pct)
        else:
            notifier.post("DOWNLOADING", f"{label}  {mb} MB",
                          timeout=NOTIFY_PROGRESS_TTL)
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
            notifier.post("VERIFYING", name[:30],
                          timeout=NOTIFY_PROGRESS_TTL)
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
        # Hand off to the headless installer, which raises its own progress
        # card. Close ours first: both live on the upper-right surface and
        # mako shows one notification per surface, so a stale card left open
        # here would mask the installer's own.
        notifier.close()
        ok, ilines = _run_install(tmp_pkg, rap_path, _Notifier())
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
    _draw_footer(stdscr, h, w, state["current_tab"], state["status"],
                 state.get("tools_mode", "menu"))
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

    # ISO ONBOARD sweep (0.7.2) — FIRST, so a freshly dropped .iso gets its
    # rename + .m3u + MangoHud key + filename-serial golden seed before the
    # golden sweep and schema load run. Then the GOLDEN SEED sweep (0.7.1) —
    # BEFORE the schema load, so a title seeded right now (e.g. the freshly
    # booted disc ISO that is our TARGET_ID) opens in TUNING with the golden
    # values already on disk instead of an empty file the injector can't
    # append into. Fail-soft: empty sweeps are silent; a disabled/racing
    # sweep just defers to the next open.
    iso_ready = _iso_onboard_sweep()
    seeded_ids = _golden_seed_sweep()
    # PAD BINDING sweep. Firmware install is where this is *reported* (it is the
    # rig-setup moment), but firmware installs exactly once, and the device name
    # can drift again underneath it — a ROCKNIX update that re-targets
    # InputPlumber is precisely how the shipped config went stale. Re-checking
    # every open makes it self-healing instead of a one-shot. Same discipline as
    # the sweeps above: idle-gated, silent when correct, never fatal.
    _ensure_pad_binding()

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
        # Plain-language footer notice when the sweeps just did work — the
        # operator sees which discs became ES-ready (restart ES / reboot to
        # list them) and which titles picked up the golden tune (detail
        # lives in the TELEMETRY config-change rows and the app log).
        "status": " | ".join(
            ([f"ISO GAMES READY: {len(iso_ready)} (reboot to list in ES)"]
             if iso_ready else []) +
            ([f"GOLDEN TUNE seeded: {', '.join(seeded_ids)}"]
             if seeded_ids else [])),
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

        # Gamepad input — only if fd was successfully opened. Bounded drain:
        # analog trigger sweeps (ABS_Z/ABS_RZ) emit events far faster than
        # the 50ms frame clock, so trigger-axis events are folded into the
        # TRIGGER CAL model here (dropped elsewhere, as before) and at most
        # ONE other event per frame proceeds to normal dispatch — identical
        # semantics to the old single-read for everything but triggers,
        # without the queue backlog that would lag the live gauge.
        if fd is not None:
            data = None
            try:
                for _ in range(128):
                    chunk = os.read(fd, EVENT_SIZE)
                    if len(chunk) != EVENT_SIZE:
                        break
                    _, _, _t, _c, _v = struct.unpack(EVENT_FORMAT, chunk)
                    if _t == EV_ABS and _c in (ABS_Z, ABS_RZ):
                        if state.get("tools_mode") == "trigcal":
                            _trigcal_axis(state, _c, _v)
                        continue
                    data = chunk
                    break
            except BlockingIOError:
                pass
            except OSError as e:
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
                              _scan_vault_hygiene, state,
                              progress={"frac": 0.0})

        action = state.pop("tools_action", None)
        if action:
            notifier = _Notifier()
            kind = action[0]
            skip_result = False
            if kind in ("install", "firmware") and BG_INSTALL_ENABLED:
                # BACKGROUND (0.8.4 default). Queue it and get out of the way:
                # the worker runs out of process, so the operator can keep
                # using the Pitstop, go back to ES, or close the terminal
                # while RPCS3 unpacks. Progress and the verdict arrive on the
                # ES-style notification surfaces, exactly as a scrape does.
                # Pre-flight stays HERE and stays synchronous — a refusal has
                # to be answerable on the screen the operator is looking at.
                pre = _install_preflight("fw" if kind == "firmware" else "pkg",
                                         action[1])
                if pre:
                    ok, lines = False, pre
                else:
                    qkind = "fw" if kind == "firmware" else "pkg"
                    rap = action[2] if kind == "install" else None
                    qok, why = _enqueue_install(qkind, action[1], rap)
                    name = os.path.basename(action[1])
                    if qok:
                        busy = _game_running()
                        notifier.post(
                            "QUEUED" if busy else "INSTALLING",
                            f"{name[:28]}" + ("  after your game" if busy
                                              else "  in the background"),
                            timeout=10000)
                        ok, lines = True, [
                            f"QUEUED:  {name}",
                            "",
                            "It installs in the background - you can keep",
                            "using the Pitstop, go back to your games, or",
                            "close this app. Watch the top-right corner.",
                        ] + (["", "It starts once you finish playing."]
                             if busy else [])
                    else:
                        ok, lines = False, [f"Could not queue: {why}"]
            elif kind == "install":
                # Modal fallback (ETK_BG_INSTALL=0).
                res = _run_with_spinner(
                    stdscr, "Installing PS3 package — please wait…",
                    _run_install, action[1], action[2], notifier,
                    indeterminate=True)
                ok, lines = res if res else (
                    False, ["package install failed — see log"])
            elif kind == "firmware":
                # Modal fallback (ETK_BG_INSTALL=0).
                res = _run_with_spinner(
                    stdscr, "Installing PS3 firmware — about a minute…",
                    _run_install_fw, action[1], notifier, indeterminate=True)
                ok, lines = res if res else (
                    False, ["firmware install failed — see log"])
            elif kind == "uninstall":
                _draw_tools_busy(stdscr, "uninstall")
                ok, lines = _run_uninstall(action[1], notifier)
            elif kind == "shader":
                res = _run_with_spinner(
                    stdscr, "Cleaning shaders — please wait…",
                    _run_shader_op, action[1], action[2], action[3], notifier)
                ok, lines = res if res else (False, ["shader op failed — see log"])
                state["shaders_model"] = None     # force rescan on re-entry
            elif kind == "update_check":
                res = _run_with_spinner(
                    stdscr, "Checking GitHub for ETK updates…",
                    _self_update_check, indeterminate=True)
                if res and res[0] == "newer":
                    state["tools_update"] = res[1]
                    state["tools_mode"] = "update_confirm"
                    skip_result = True
                    ok, lines = True, []
                else:
                    ok, lines = (res[1] if res else
                                 (False, ["update check failed — see log"]))
            elif kind == "update_apply":
                res = _run_with_spinner(
                    stdscr, "Downloading + applying ETK update…",
                    _self_update_apply, action[1], indeterminate=True)
                ok, lines = res if res else (False, ["update failed — see log"])
                if ok:
                    state["tools_result"] = (ok, lines)
                    state["tools_mode"] = "update_done"
                    skip_result = True
            else:
                ok, lines = (False, [f"unknown action: {kind}"])
            if not skip_result:
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
                              _paddock_refresh, state, draw=_paddock_busy,
                              indeterminate=True)

        # PADDOCK: queued PUSH/PULL — shell out to paddock_sync.sh for the
        # selected game, streaming progress to mako. Same long-op pattern as
        # the TOOLS install above (no RPCS3 launch, so no screen handoff).
        pact = state.pop("paddock_action", None)
        if pact:
            prow = pact["row"]
            res = _run_with_spinner(
                stdscr, f"{pact['verb'].upper()}: {prow['name']} ↔ your paddock",
                _run_paddock_sync, pact["verb"], prow, _progress_notifier(),
                draw=_paddock_busy, indeterminate=True)
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
        # pkg/rap, verify, hand to the same headless installer the TOOLS tab
        # uses (nothing opens on screen). Pure-data invariant: ETK runs the
        # installer, the bytes are the operator's own.
        pkg_act = state.pop("paddock_pkg_action", None)
        if pkg_act:
            prow2, pent = pkg_act
            _paddock_busy(stdscr, f"Getting {prow2['name']}: downloading, then installing — watch notifications")
            ok, lines = _run_paddock_pkg_install(prow2, pent,
                                                _progress_notifier())
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
