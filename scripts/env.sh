#!/bin/bash
# ==========================================================
# ETK PHASE 9: Game Agnostic ETK (v9.3.2 - FORENSICS RESTORED)
# ==========================================================
# MERGE LOG: 
# - Restored CMD_QUEUE for commander.sh input daemon
# - Restored PROBE_SCRIPT, CRASH_LOG, and LAST_ANALYSIS for Datalog
# - Maintained Shared-Truth ID resolution and Thermal Boundaries
# ==========================================================

# --- [ OPERATOR CONFIG ] ---
# Personal / preference settings live in etk.conf at the repo root,
# NOT in this file. install.sh's first-run wizard generates etk.conf
# (gitignored) from etk.conf.example, auto-discovering the rig via
# mDNS. Edit etk.conf to change rig target, build tier, or HUD options.
#
# This block sources etk.conf if present, then applies baked-in defaults
# via ${VAR:-default} so the kit functions before anyone runs install.sh.
# The path resolves relative to THIS file's location (BASH_SOURCE), so
# both host (install.sh) and rig (Sentry) find the same etk.conf.
ETK_CONF_FILE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)/etk.conf"
[ -f "$ETK_CONF_FILE" ] && . "$ETK_CONF_FILE"

export RIG_SSH="${RIG_SSH:-root@SM8250.local}"
export ETK_BUILD_TYPE="${ETK_BUILD_TYPE:-FULL}"
export DEFAULT_MODE="${DEFAULT_MODE:-RACE}"
export HUD_HEADER_HOLD_S="${HUD_HEADER_HOLD_S:-15}"
export ETK_VERBOSE="${ETK_VERBOSE:-1}"
# host<->rig Tier-A shader sync mode (install.sh): full | backup | off
export VAULT_SYNC="${VAULT_SYNC:-full}"
# DP-mirror daemon (bin/dpmirror_d.sh): 1 = mirror internal->external for capture
# (game native on handheld, small GPU/FPS cost); 0 = record-only (game native on
# the external/capture output, NO wl-mirror = no FPS cost, handheld idle).
export ETK_DP_MIRROR="${ETK_DP_MIRROR:-1}"
# HUD instrument set (mango_bridge.sh body): GINSTR (default since 0.7.x) =
# TEMP|JTTR|SLIP|shaders live frame-pacing gauges; BASIC = the simple set,
# TEMP|LOAD|RAM%|shaders.
export ETK_HUD_MODE="${ETK_HUD_MODE:-GINSTR}"
# Hostless self-update (Pitstop TOOLS > Check for ETK Updates): updates the
# middleware layer in place from GitHub releases. Default-ON; =0 disables.
export ETK_SELF_UPDATE="${ETK_SELF_UPDATE:-1}"
# Golden-default config seeding (0.7.1, default-ON): Pitstop seeds
# custom_configs/config_<ID>.yml from the ETK golden template for any
# playable title that has none, so a new game's first launch never runs
# RPCS3's hostile defaults (disc/ISO titles additionally get the
# Strict Rendering Mode fix). Set 0 to disable (kill-switch).
export ETK_GOLDEN_SEED="${ETK_GOLDEN_SEED:-1}"
# ISO onboarding (0.7.2, default-ON): Pitstop makes a dropped .iso a
# first-class ES game — renames [BRACKET-ID] tags to (PARENID) (ROCKNIX's
# get_setting escapes ()& but NOT [], so bracketed names silently kill
# per-game settings incl. the MangoHud overlay), generates the <name>.m3u
# launcher ES actually scans (ps3 extensions are .ps3 .psn .m3u — bare
# .iso never lists), enables the per-game MangoHud key, and golden-seeds
# the config from the filename serial. Set 0 to disable (kill-switch).
export ETK_ISO_ONBOARD="${ETK_ISO_ONBOARD:-1}"
# Controller binding repair (0.8.1, default-ON): ROCKNIX's stock RPCS3 pad
# config names "InputPlumber GameController 1" — the Xbox-style virtual pad
# InputPlumber used to expose. It now targets a DualSense, so that name
# matches nothing, RPCS3 falls back to NullPadHandler and the controller is
# DEAD in game with no on-screen clue. Pitstop asks the rig's own SDL what
# the attached pad is really called and repairs only the Device: line.
# Set 0 to disable (kill-switch).
export ETK_PAD_BIND="${ETK_PAD_BIND:-1}"
# Chiaki Remote Play (0.8.x, default-ON): deploys the ETK chiaki fork's SDL2
# client to $ETK_ROOT/tools/chiaki and registers "Chiaki Remote Play" in the
# ES Tools menu (launcher + icon + gamelist entry, tripwire-protected). The
# GT7 streaming lane. Pairing state lives in /storage/.config/chiaki/ and is
# NEVER deployed, synced or published (secrets, etk.conf precedent). Set 0 to
# disable (kill-switch: skips deploy, Tools entry and tripwire).
export ETK_CHIAKI="${ETK_CHIAKI:-1}"
# OS-update coherence guard (0.8.3, default-ON): bin/osguard.sh runs once per
# boot (etk-osguard.service) and self-heals the kernel/module-tree mismatch a
# ROCKNIX in-place update leaves behind (the 2026-08-01 frankenboot: updater
# writes the new kernel over the RUNNING boot's slot + regenerates grub twins
# and grubenv). Never reboots. Set 0 to disable (kill-switch).
export ETK_OS_GUARD="${ETK_OS_GUARD:-1}"
# Per-title CORE swap (multigame lane, default-ON): install.sh binds a launch
# wrapper over /usr/bin/rpcs3-sa that resolves serial -> core AppImage via
# core_map.tsv and execs it (launch-cadence, no reboot). Behavior-neutral
# until a title is pinned in Pitstop TUNING > CORE: an empty map always execs
# the certified default. A/B tooling — nothing ships pinned, and the catalog
# is operator-staged (host emulators/*.AppImage), never fetched. Set 0 to
# disable (kill-switch: certified AppImage binds directly, no wrapper).
export ETK_CORE_SWAP="${ETK_CORE_SWAP:-1}"
# Community patch fetch (multigame lane, default-ON): install.sh refreshes the
# rig's RPCS3 community patch file (patches/patch.yml) from the same official
# endpoint the desktop GUI uses, sha-verified, fail-soft offline. Pitstop
# TUNING > PATCH then surfaces per-title toggles. Set 0 to disable.
export ETK_PATCH_FETCH="${ETK_PATCH_FETCH:-1}"

# --- [ SHM & STATE ] ---
export SHM_DIR="/dev/shm/etk_shm"
export ID_FILE="$SHM_DIR/active_id.txt"
export MODE_FILE="$SHM_DIR/etk_mode.txt"
export LIVE_STAT="$SHM_DIR/live_stat.txt"
export VAULT_COUNT="$SHM_DIR/vault_count"
export CMD_QUEUE="$SHM_DIR/etk_cmd_queue"

# ----[ FOR ROCKNIX NATIVE PITSTOP APP ] ---
# Persistent anchor for the last successfully resolved Game ID
export RECENT_ID_FILE="/storage/games-internal/roms/etk/vault/last_played_id.txt"

# --- [ HOISTED PATH ANCHOR ] ---
# ETK_ROOT defined here (above the immutable block) so the DEVICE PROFILE
# block below the immutable block can resolve $ETK_ROOT/scripts/profiles/.
# Per Law #2 there is a SINGLE definition; the historical second site in
# PATH RESOLUTION was removed. Do not re-introduce a duplicate.
export ETK_ROOT="/storage/games-internal/roms/etk"

# =========================================================
# [CRITICAL: NON-NEGOTIABLE GAME AGNOSTIC ETK LOGIC]
# AI INSTRUCTION: DO NOT MODIFY, OPTIMIZE, OR REFACTOR THIS BLOCK.
# Context: True Agnostic ID Resolution via PARAM.SFO extraction.
# =========================================================
if [ -f "$ID_FILE" ]; then
    export TARGET_ID=$(cat "$ID_FILE")
else
    ACTIVE_PID=$(pgrep -f rpcs3 | head -n 1)
    if [ ! -z "$ACTIVE_PID" ]; then
        # 1. Try the fast in-memory grep first
        DETECTED_ID=$(cat /proc/$ACTIVE_PID/cmdline /proc/$ACTIVE_PID/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        
        # 2. If memory grep fails, extract the ROM path and rip the ID from PARAM.SFO
        if [ -z "$DETECTED_ID" ]; then
            ROM_PATH=$(cat /proc/$ACTIVE_PID/cmdline 2>/dev/null | tr '\0' '\n' | grep "\.ps3" | head -n 1)
            if [ ! -z "$ROM_PATH" ]; then
                SFO_FILE=$(find "$ROM_PATH" -name "PARAM.SFO" 2>/dev/null | head -n 1)
                [ ! -z "$SFO_FILE" ] && DETECTED_ID=$(strings "$SFO_FILE" 2>/dev/null | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
            fi
        fi
        
        export TARGET_ID="${DETECTED_ID:-UNKNOWN_ID}"
    else
        export TARGET_ID="IDLE"
    fi
fi
export CHIPSET="SM8250"
# =========================================================

# --- [ SHARED IDENTITY RESOLVER ] ---
# Single source of truth for *writing* the game ID (the immutable block
# above only resolves it for the current shell when ID_FILE is absent).
# The Sentry must commit ID_FILE/RECENT_ID_FILE before re-sourcing env.sh,
# so it needs the same resolution. The primary scan is byte-for-byte the
# proven pre-existing one-liner: it must xargs over *every* rpcs3 PID, not
# just the first — the AppImage spawns multiple processes and the game ID
# lives in a non-first worker, so a single-PID/head approach strands the
# ID at the NPUA80075 fallback (regression: stuck per-game). The only
# addition is the PARAM.SFO fallback, gated on the primary returning
# empty and derived from the live ROM path so it can never yield a
# stale or wrong-game ID.
resolve_game_id() {
    id=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
    if [ -z "$id" ]; then
        rom=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline 2>/dev/null | tr '\0' '\n' | grep '\.ps3' | head -n 1)
        if [ -n "$rom" ]; then
            sfo=$(find "$rom" -name "PARAM.SFO" 2>/dev/null | head -n 1)
            [ -n "$sfo" ] && id=$(strings "$sfo" 2>/dev/null | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        fi
    fi
    # Disc/ISO titles (the .pkg/.iso format axis — AI_MANIFEST): the live
    # cmdline is a bare .iso path. Unless the filename carries the serial,
    # BOTH scans above come up empty — there is no on-filesystem PARAM.SFO
    # to rip (it lives inside the image). Two ISO lanes, filename first:
    #  a) the IRISMAN-style ID tag in the name, dashed or not —
    #     "(BCUS98158)" / "[BLUS-30019]" — is authoritative;
    #  b) RPCS3's games.yml ("SERIAL: path") matched by BASENAME, not full
    #     path: ES boots via the /roms/ps3 symlink so games.yml records
    #     /roms/ps3/... while other contexts see /storage/roms/..., and a
    #     rename desyncs the recorded path — the basename is the stable key.
    # Fixed-string greps (names contain regex metachars); BusyBox-safe.
    if [ -z "$id" ]; then
        rom=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline 2>/dev/null | tr '\0' '\n' | grep -i '\.iso$' | head -n 1)
        if [ -n "$rom" ]; then
            rombase=$(basename "$rom")
            id=$(echo "$rombase" | grep -oE '[A-Z]{4}-*[0-9]{5}' | head -n 1 | tr -d '-')
            if [ -z "$id" ] && [ -f /storage/.config/rpcs3/games.yml ]; then
                id=$(grep -F "$rombase" /storage/.config/rpcs3/games.yml 2>/dev/null | grep -oE '^[A-Z]{4}[0-9]{5}' | head -n 1)
            fi
        fi
    fi
    # First-ever boot of a fresh ISO (games.yml row not committed yet):
    # RPCS3 truncates its log per launch and prints "Serial: <ID>" in the
    # boot header (System.cpp sys_log), so the head of the live log is this
    # session's ground truth. Bounded read — never stream a race-long log.
    # Only reached while a game is RUNNING (the Sentry's call sites), so a
    # stale log can't resurrect a dead session's ID.
    if [ -z "$id" ]; then
        id=$(head -c 262144 "${RPCS3_LOG:-/storage/.cache/rpcs3/RPCS3.log}" 2>/dev/null | grep 'Serial:' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
    fi
    echo "$id"
}

# --- [ DEVICE PROFILE ] ---
# Immutable Law #2: env.sh is the SOLE exporter of canonical env vars.
# Profiles in scripts/profiles/<soc>.sh contain VALUES ONLY (plain
# assignments, no `export`, no logic). Do NOT promote exports into
# profile files; env.sh re-exports the canonical names from profile values.
# Operator escape hatch: ETK_PROFILE_OVERRIDE=<soc> forces a profile.
#
# Profile dir resolves relative to THIS file's location (BASH_SOURCE)
# rather than $ETK_ROOT — env.sh is sourced from BOTH the host (install.sh)
# and the rig, and the rig path doesn't exist on the host. Self-anchoring
# keeps both call sites working.
ETK_PROFILE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/profiles"
ETK_PROFILE_OVERRIDE="${ETK_PROFILE_OVERRIDE:-}"

detect_soc() {
    # POSIX/BusyBox-safe. Returns a token like SM8250, or empty.
    # `cat 2>/dev/null` (instead of `< file 2>/dev/null`) lets us swallow
    # the file-not-found that bash emits BEFORE the pipeline runs — needed
    # so env.sh sources cleanly on the host (no devicetree there).
    soc=$(cat /sys/firmware/devicetree/base/compatible 2>/dev/null \
          | tr '\0' '\n' \
          | grep -oiE 'sm8[0-9]{3}|rk3[0-9]{3}|s922x|h700' | head -n 1 \
          | tr 'a-z' 'A-Z')
    echo "$soc"
}

if [ -n "$ETK_PROFILE_OVERRIDE" ]; then
    PROFILE_FILE="$ETK_PROFILE_DIR/$ETK_PROFILE_OVERRIDE.sh"
else
    DETECTED_SOC=$(detect_soc)
    PROFILE_FILE="$ETK_PROFILE_DIR/${DETECTED_SOC:-SM8250}.sh"
fi

# Never hard-fail env.sh on a missing profile (env.sh is sourced by
# everything; a hard exit bricks the kit). Warn, fall back to SM8250.
# Empty override + empty detection = host context (no devicetree) — silent
# fallback. Only warn when someone had a SoC token in hand.
if [ ! -f "$PROFILE_FILE" ]; then
    if [ -n "$ETK_PROFILE_OVERRIDE" ] || [ -n "$DETECTED_SOC" ]; then
        echo "ETK env: no profile for '${ETK_PROFILE_OVERRIDE:-$DETECTED_SOC}', using SM8250 reference" >&2
    fi
    PROFILE_FILE="$ETK_PROFILE_DIR/SM8250.sh"
fi
. "$PROFILE_FILE"

# Canonical exports from profile values. CHIPSET re-assignment here
# supersedes the literal set inside the immutable block above — purely
# additive (the marked block is not modified, per Law #2 dossier note).
export CHIPSET="${PROFILE_SOC:-SM8250}"
export PROFILE_SOC GPU_DRIVER_FAMILY RPCS3_CAPABLE
export THERMAL_ZONE_GOVERNING THERMAL_ZONE_MAP
export CPU_POLICY_SILVER CPU_POLICY_GOLD CPU_POLICY_PRIME CPU_PIT_CAP_KHZ
export GPU_DEVFREQ_NODE GPU_GOVERNOR_PATH GPU_ADAPTER_STRING
export ALARM_TEMP RACE_THRESHOLD
# RECOVER_THRESHOLD / RACE_TRIP_TICKS may be absent in older/leaner profiles;
# default here so thermal_d's auto-recovery + debounce work before a profile
# re-sync. (${VAR:-default} idiom; RECOVER_THRESHOLD replaced dead PIT_THRESHOLD.)
export RECOVER_THRESHOLD="${RECOVER_THRESHOLD:-80}"
export RACE_TRIP_TICKS="${RACE_TRIP_TICKS:-2}"
export FOOT_FONT_SIZE MANGOHUD_FONT_SIZE

# --- [ PATH RESOLUTION ] ---
export RPCS3_CACHE_DIR="/storage/.cache/mesa_shader_cache"
export VAULT_DIR="$ETK_ROOT/vault/$CHIPSET/$TARGET_ID/shaders"
export RIG_MANGO_CONF="/storage/.config/MangoHud/MangoHud.conf"

# --- [ FORENSICS & ANALYTICS ] ---
export PROBE_SCRIPT="$ETK_ROOT/scripts/probe.sh"
export CRASH_LOG="/storage/etk_crash_report.log"
export LAST_ANALYSIS="$SHM_DIR/last_analysis.txt"
# Boot tripwire: anomaly-only sink (modules re-injection, cache symlink
# events). install.sh truncates on boot; empty file = clean boot.
# Consumed by tools/vault_doctor.sh §6.
export TRIPWIRE_LOG="/storage/etk_tripwire.log"

# --- [ THERMAL BOUNDARIES ] ---
# Calibrated PER-DEVICE; values live in scripts/profiles/<soc>.sh.
# env.sh re-exports ALARM_TEMP / RACE_THRESHOLD / RECOVER_THRESHOLD /
# RACE_TRIP_TICKS via the DEVICE PROFILE block above. Bootstrap re-calibration recipe lives in
# scripts/etk_probe.sh (see dossier §G).

# --- [ RESTORED STEALTH DETECTION ] ---
if grep -q "mangohud=0" /storage/.config/rocknix/system.conf 2>/dev/null; then
    export ETK_STEALTH=1
else
    export ETK_STEALTH=0
fi

# --- [ RESTORED UI & ENGINES ] ---
# ABSOLUTE assignment, NEVER a ${PYTHONPATH}:... self-append: the Sentry and
# mango_bridge re-source this file EVERY tick (Law #2), so an append grows the
# env +42 bytes/tick until the kernel's 128KB per-variable exec limit trips at
# ~1h45m uptime — at which point every execve in the loop fails E2BIG
# ("Argument list too long"), pgrep goes blind, ignition never fires, and the
# loop spins a core. Root-caused live 2026-07-03 (the recurring "WAITING FOR
# IGNITION / reboot to fix" wedge); see memory project_sentry_env_bomb.
export PYTHONPATH="/storage/etk/lib/python3.13/site-packages"
export G='\033[0;32m'; export R='\033[0;31m'; export Y='\033[1;33m'; export C='\033[0;36m'; export N='\033[0m'
# DEFAULT_MODE is sourced via OPERATOR CONFIG block (etk.conf).

# --- [ HUD ] ---
# HUD_HEADER_HOLD_S (mango_bridge.sh banner hold) is sourced via OPERATOR
# CONFIG block above (etk.conf). Same for ETK_VERBOSE (install.sh rsync).

# --- [ SIMPLE TELEMETRY ] ---
# Post-mortem ledger + career stats layer (dossier: BuildSimpleTelemetry.md §4).
# All paths derive from $ETK_ROOT — no hardcoded paths in downstream scripts.
# The directories are NOT created at source-time; consumers call
# telemetry_init_dirs() before their first write so a read-only source
# (e.g. install.sh running in a probe-only mode) does not provision state.
export TELEMETRY_DIR="$ETK_ROOT/etk_telemetry"
export SESSIONS_LEDGER="$TELEMETRY_DIR/sessions.tsv"
export CONFIG_CHANGES_LEDGER="$TELEMETRY_DIR/config_changes.tsv"
export CAREER_DIR="$TELEMETRY_DIR/career"
export PIT_NOTE_FILE="$TELEMETRY_DIR/pit_note.txt"
# Active Turnip-dial signature (written by Pitstop DRIVER tab); session_postmortem
# stamps it onto each ledger row as tune_tag so genuine-play sessions are
# attributable to their dial set. Absent/empty => "default".
export ACTIVE_TUNE_FILE="$TELEMETRY_DIR/active_tune.txt"
# Turnip driver-build catalog (Pitstop DRIVER tab BUILD selector). `loaded` is
# ground truth stamped by etk-turnip-bind.sh at boot — WHICH .so is actually
# bound right now, as opposed to `selected` (the operator's pick, which only
# takes effect on the next reboot). session_postmortem folds `loaded` into
# tune_tag so a ledger row names the driver it ran on, not just the dials:
# with several builds in the catalog "tu_debug=sddepth" alone is ambiguous.
export TURNIP_DIR="${TURNIP_DIR:-/storage/turnip}"
export TURNIP_DRIVERS_DIR="$TURNIP_DIR/drivers"
export TURNIP_SELECTED_FILE="$TURNIP_DIR/selected"
export TURNIP_LOADED_FILE="$TURNIP_DIR/loaded"

# Per-title RPCS3 CORE catalog + map (multigame lane; see ETK_CORE_SWAP above).
# The catalog lives on the GAME CARD, not UFS /storage — cores are ~78MB each
# and the ~6.6GB SYSTEM partition has been filled to ENOSPC before (2026-07-04;
# emulator staging silently failed). NOTE: $ETK_ROOT/cores is the COREDUMP dir;
# the AppImage catalog is $ETK_ROOT/emulators, mirroring the host-side dir.
export RPCS3_CORES_DIR="$ETK_ROOT/emulators"
export RPCS3_CORE_MAP="$RPCS3_CORES_DIR/core_map.tsv"
# Launch-time core marker (active_tune.txt pattern — PERSISTENT, not SHM, so a
# PANIC row still knows which core its session ran): the launch wrapper stamps
# the resolved core token here at every launch. This is the ground truth the
# ledger attributes by — not the binary's baked branch string (the
# r0.8.0-19638 wart: the branch name didn't move for 0.8.1-dev).
export ACTIVE_CORE_FILE="$TELEMETRY_DIR/active_core.txt"

# Community patch surface (multigame lane §3 — NOT a new patch system; RPCS3
# ships the framework, ETK only surfaces it). Pitstop TUNING > PATCH writes
# RPCS3's own patch_config.yml AND the per-serial pin TSV below; the launch
# wrapper resolves this serial's pins and stamps the active set into
# ACTIVE_PATCHES_FILE so the ledger tune_tag records `patches=` per session
# (a patch IS a tune — unattributed patches pollute every future A/B).
export PATCH_PINS_FILE="$TELEMETRY_DIR/patch_pins.tsv"
export ACTIVE_PATCHES_FILE="$TELEMETRY_DIR/active_patches.txt"

# --- STACK ATTRIBUTION -----------------------------------------------------
# Every layer of the GTK stack self-identifies, and the ledger historically
# recorded none of them (col 3 "build" is ETK_BUILD_TYPE — FULL on all 1488
# rows, zero discriminating power). With ROCKNIX, the kernel, Turnip and RPCS3
# all moving independently, an A/B arm that spans a bump silently pools two
# different stacks and reads as one. That has already happened once: the
# 2026-07-30/31 zlatez arms straddled an RPCS3 base bump and the pooled N=6
# looked like a weakening effect rather than two different stacks.
#
# These helpers produce the tune_tag prefix. Used by BOTH ledger writers:
# session_postmortem.sh (normal rows) and the Sentry's orphan-PANIC synthesis
# in install.sh — panic sessions are the ones you most want attributed.
etk_turnip_build_tag() {
    _b=$(head -n1 "$TURNIP_LOADED_FILE" 2>/dev/null | tr -d '\t\r ')
    [ -z "$_b" ] && _b="stock"
    # Compact the catalog filename the same way Pitstop's _build_label does,
    # minus the constant "rocknix" (every ROCKNIX build carries it).
    _b=$(printf '%s' "$_b" | sed -e 's/\.so$//' \
                                 -e 's/^libvulkan_freedreno[-_]*//' \
                                 -e 's/^etk_turnip[-_]*//' \
                                 -e 's/^rocknix[-_]*//')
    [ -z "$_b" ] && _b="stock"
    printf 'build=%s' "$_b"
}

etk_stack_tag() {
    # ROCKNIX image: BUILD_ID is a 40-char sha; 7 pins a release unambiguously.
    _rk=$(sed -n 's/^BUILD_ID="\{0,1\}\([0-9a-f]\{7\}\).*/\1/p' /etc/os-release 2>/dev/null | head -n1)
    [ -z "$_rk" ] && _rk="?"
    # Kernel: `uname -r` reads 7.0.11 for BOTH the stock and the GTK kernel, so
    # it cannot discriminate on its own. The build counter in `uname -v` (#3)
    # is what actually changes per rebuild.
    _kr=$(uname -r 2>/dev/null)
    _kn=$(uname -v 2>/dev/null | sed -n 's/^#\([0-9][0-9]*\).*/\1/p')
    [ -n "$_kn" ] && _kr="${_kr}#${_kn}"
    [ -z "$_kr" ] && _kr="?"
    # RPCS3: the log banner's etk/<ver> field carries the fork version AND the
    # upstream build number — exactly the pair that moves on a base bump.
    _r3=$(grep -m1 -aoE 'etk/[^ |]+' "$RPCS3_LOG" 2>/dev/null | head -n1 | cut -d/ -f2)
    [ -z "$_r3" ] && _r3="?"
    printf 'stack=rk%s/k%s/r%s' "$_rk" "$_kr" "$_r3"
}

etk_rpcs3_core_tag() {
    # Which RPCS3 core the session ACTUALLY launched (wrapper-stamped marker;
    # see ACTIVE_CORE_FILE). Absent marker = pre-wrapper rig or stock opt-out
    # = the certified default. Compaction mirrors Pitstop's _core_label.
    _c=$(head -n1 "$ACTIVE_CORE_FILE" 2>/dev/null | tr -d '\t\r ')
    [ -z "$_c" ] && _c="certified"
    _c=$(printf '%s' "$_c" | sed -e 's/\.AppImage$//' \
                                 -e 's/^rpcs3[-_]*//' \
                                 -e 's/^etk[-_]*//' \
                                 -e 's/^gtk-edition[-_]*//' \
                                 -e 's/_linux_aarch64$//')
    [ -z "$_c" ] && _c="certified"
    printf 'core=%s' "$_c"
}

etk_patches_tag() {
    # Active community-patch set for the session (wrapper-stamped, comma-
    # joined slugs). EMPTY when no patches ran — and then the segment is
    # omitted entirely, so patch-free rows group identically with and
    # without this feature deployed.
    _p=$(head -n1 "$ACTIVE_PATCHES_FILE" 2>/dev/null | tr -d '\t\r ')
    [ -n "$_p" ] && printf ';patches=%s' "$_p"
}

# build=<turnip>;stack=rk<img>/k<kernel>/r<rpcs3>;core=<rpcs3-core>[;patches=..]
# — the full attribution prefix. core= is the marker-stamped ground truth for
# WHICH RPCS3 binary ran (per-title CORE swap makes the stack r-segment
# ambiguous alone); patches= appears only when the session ran with community
# patches enabled (a patch is a tune).
etk_attribution_tag() { printf '%s;%s;%s%s' "$(etk_turnip_build_tag)" "$(etk_stack_tag)" "$(etk_rpcs3_core_tag)" "$(etk_patches_tag)"; }
export SIGNATURES_FILE="$ETK_ROOT/config/crash_signatures.json"

# Persistent session breadcrumb. Written at IDLE->RUNNING ignition, removed
# by session_postmortem.sh on a clean RUNNING->IDLE transition. If it
# survives a reboot, the previous session never reached postmortem (kernel
# panic / hard hang) and the Sentry synthesizes an orphan PANIC row on
# boot. Lives in $TELEMETRY_DIR (persistent) NOT $SHM_DIR (boot-volatile).
export SESSION_ANCHOR="$TELEMETRY_DIR/session_anchor.txt"

# Per-game MangoHud HUD position memory. Two columns: game_id \t position
# (top-left | bottom-left). Written by bin/input_d.py on L3 press; read by
# the Sentry at IDLE->RUNNING to apply the remembered position to the live
# MangoHud.conf before signaling reload_cfg. Absent file = default
# (bottom-left, the dashboard convention).
export HUD_POSITIONS_FILE="$TELEMETRY_DIR/hud_positions.tsv"

# Per-game MangoHud PUNCHBOX state: one of top | bottom | default | off.
# Written by bin/input_d.py on the R1+L3 cycle; read by the Sentry at
# IDLE->RUNNING to restore this game's HUD state via bin/hud_apply.sh (the
# shared applier). Supersedes the position-only HUD_POSITIONS_FILE above for
# the 4-state punchbox. Absent / unknown => 'bottom' (dashboard default).
export HUD_STATE_FILE="$TELEMETRY_DIR/hud_state.tsv"
# Shared HUD-state applier (state -> live MangoHud.conf + reload). Absolute so
# both the Sentry and input_d resolve it identically.
export ETK_HUD_APPLY="$ETK_ROOT/bin/hud_apply.sh"

# L1-screenshot gating mode. One of: always | in-game | disabled.
#  - always   : L1 fires a screenshot whenever pressed (incl. frontend / Pitstop)
#  - in-game  : L1 fires only while a PS3 game is resolved (the default)
#  - disabled : L1 never fires a screenshot, freeing the button for the game
# Read live by bin/input_d.py on each L1 press; cycled from Pitstop's TOOLS
# tab (atomic tmp+mv). Absent / unreadable / invalid file = in-game default.
# Gates ONLY the L1 trigger -- the SELECT+DPAD-Up chord is always live.
export SCREENSHOT_MODE_FILE="$TELEMETRY_DIR/screenshot_mode.txt"

# Minimum session length (seconds) to count as a real attempt. Sessions
# shorter than this are force-quit/fat-finger aborts: career_aggregate.sh
# excludes them and session_postmortem.sh reclassifies a sub-threshold
# CLEAN as ABORTED. A documented policy parameter — tunable here, not
# hardcoded. Changing it shifts all historical career numbers.
export TELEMETRY_MIN_SESSION_S=60

# Helper: ensure telemetry tree exists; safe to call repeatedly.
# Shell-only — Python consumers in bin/etk_pitstop.py do their own mkdir.
telemetry_init_dirs() {
    mkdir -p "$TELEMETRY_DIR" "$CAREER_DIR"
}

# --- [ TOOLS TAB: HEADLESS PKG/RAP INSTALLER ] ---
# Paths for the TOOLS-tab PS3 package installer/uninstaller.
# Discovery + rationale: spike/ and dossiers/GameInstallFeatureDossier.md.
# Consumers read with fallbacks; dirs are provisioned by install.sh, never
# created at source-time (keeps a read-only source side-effect free).
export RPCS3_BIN="/usr/bin/rpcs3-sa"
export RPCS3_DEV_HDD0="/storage/games-internal/roms/bios/rpcs3/dev_hdd0"
export RPCS3_GAME_DIR="$RPCS3_DEV_HDD0/game"
export RPCS3_EXDATA_DIR="$RPCS3_DEV_HDD0/home/00000001/exdata"
# RPCS3 user-profile tree (saves, trophies, .rap licenses) — Tier-B precious,
# backed up by install.sh, restored only via --restore-state.
export RPCS3_HOME_DIR="$RPCS3_DEV_HDD0/home"
export RPCS3_CUSTOM_CONFIGS="/storage/games-internal/roms/bios/rpcs3/custom_configs"
export RPCS3_HDD1_CACHE="/storage/games-internal/roms/bios/rpcs3/dev_hdd1/caches"
export RPCS3_RUNTIME_CACHE="/storage/.cache/rpcs3/cache"
export RPCS3_LOG="/storage/.cache/rpcs3/RPCS3.log"
# dev_flash holds the installed PS3 firmware. RPCS3's config dir is
# /storage/.config/rpcs3/ and its dev_flash is symlinked out to the games tree
# (-> /storage/roms/bios/rpcs3/dev_flash). Use the CONFIG-DIR path so we track
# exactly what RPCS3 resolves at runtime, wherever the games tree is mounted.
# version.txt is RPCS3's own firmware-version marker.
export RPCS3_DEV_FLASH="/storage/.config/rpcs3/dev_flash"
# RPCS3's config-dir game path (where it ACTUALLY installs a PKG). Same file as
# RPCS3_GAME_DIR on a coherent rig; the PKG installer compares the two to catch
# a split-brain (a foreign SD card shadowing /storage/roms).
export RPCS3_CFG_GAME_DIR="/storage/.config/rpcs3/dev_hdd0/game"

# Staging drop folder — the user places ONE .pkg (+ optional .rap) here; the
# installer deletes the staged files on a SUCCESSFUL install only.
export PKG_STAGING_DIR="$ETK_ROOT/pkg_install_drop"
export PS3_LAUNCHER_DIR="/storage/games-internal/roms/ps3"

# Firmware drop folder — the user places the official Sony PS3UPDAT.PUP here.
# Unlike PKG staging, the .pup is KEPT after a successful install (firmware is a
# reusable, system-wide asset installed once, not a per-game staging file).
export FIRMWARE_DROP_DIR="$ETK_ROOT/firmware_drop"

# ETK default per-game RPCS3 config — copied to custom_configs/config_<ID>.yml
# for each newly installed game so first launch runs tuned.
export ETK_TEMPLATE_CONFIG="$ETK_ROOT/config/etk_template.yml"

# Rocknix per-game settings store — the installer upserts the MangoHud overlay
# key  ps3["<title>.psn"].rocknix.mangohud.enabled=1  here.
export ROCKNIX_SYSTEM_CFG="/storage/.config/system/configs/system.cfg"
export ROCKNIX_MAKO_CONFIG="/storage/.config/mako/config"

# Sentry sentinel: present in volatile SHM while an install runs so the Sentry
# stays parked in IDLE (no phantom RUNNING session). See 01-etk-sentry.sh, §4.
export ETK_INSTALL_LOCK="$SHM_DIR/etk_install_lock"

# --- [ TOOLS-MENU APP REGISTRATION ] ---
# /storage/.config/modules is boot-volatile: Rocknix wipes it and regenerates
# gamelist.xml every boot. The Sentry re-injects the ETK Pitstop launcher, its
# SVG icon and the enriched <game> entry via bin/etk_modules_inject.py so it
# shows as a polished Tools app, not a bare filename. Dossier addendum R1.
export MODULES_DIR="/storage/.config/modules"
export MODULES_GAMELIST="$MODULES_DIR/gamelist.xml"
export ETK_PITSTOP_SVG="$ETK_ROOT/config/etk_pitstop.svg"

# --- [ CHIAKI REMOTE PLAY ] ---
# Binary built by tools/rocknix-bin/build_chiaki.sh (ETK chiaki fork, SDL2
# frontend), pushed by install.sh to tools/. The conf dir holds the console
# pairing secrets (rp_regist_key/rp_key) — on-rig only, never synced.
export ETK_CHIAKI_BIN="$ETK_ROOT/tools/chiaki"
export ETK_CHIAKI_CONF_DIR="/storage/.config/chiaki"
export ETK_CHIAKI_SVG="$ETK_ROOT/config/etk_chiaki.svg"
# Stream-active sentinel (ETK_INSTALL_LOCK pattern): touched/removed by the
# launcher; while present, input_d stands down the chords chiaki owns
# in-stream (R1+L3 resolution toggle, L1+R3 codec toggle) plus the RPCS3
# R1+DPAD tools. Volatile SHM — a crash self-clears on reboot.
export ETK_CHIAKI_LOCK="$SHM_DIR/chiaki_active"
# mako toast helper the launcher hands to the binary as CHIAKI_NOTIFY_CMD —
# surfaces toggle reconnects ("Switching to 1080p...") while the screen is
# black. Fail-silent, replaces-id toast (see the script header).
export ETK_CHIAKI_NOTIFY="$ETK_ROOT/bin/etk_chiaki_notify.sh"