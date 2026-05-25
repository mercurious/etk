#!/bin/bash
# ==========================================================
# ETK PHASE 9: Game Agnostic ETK (v9.3.2 - FORENSICS RESTORED)
# ==========================================================
# MERGE LOG: 
# - Restored CMD_QUEUE for commander.sh input daemon
# - Restored PROBE_SCRIPT, CRASH_LOG, and LAST_ANALYSIS for Datalog
# - Maintained Shared-Truth ID resolution and Thermal Boundaries
# ==========================================================

# --- [ CUSTOMIZE YOUR SETUP HERE ] ---
# FULL for thermal and shader protection
# LITE for thermal protection only
# RAW for system optimizations and custom HUD only
export ETK_BUILD_TYPE="FULL"
# City WiFi
# export RIG_IP="192.168.1.53"
# export RIG_SSH="root@192.168.1.53"
# Country WiFi
export RIG_IP="10.0.0.40"
export RIG_SSH="root@10.0.0.40"

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
    echo "$id"
}

# --- [ PATH RESOLUTION ] ---
export RPCS3_CACHE_DIR="/storage/.cache/mesa_shader_cache"
export ETK_ROOT="/storage/games-internal/roms/etk"
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

# --- [ RESTORED THERMAL BOUNDARIES ] ---
# Recalibrating to Rocknix nightly-20260520 changed thermals; re-validate
# zone14 + thresholds with scripts/etk_probe.sh after the in-place update.
export ALARM_TEMP=83
export PIT_THRESHOLD=65
export RACE_THRESHOLD=86

# --- [ RESTORED STEALTH DETECTION ] ---
if grep -q "mangohud=0" /storage/.config/rocknix/system.conf 2>/dev/null; then
    export ETK_STEALTH=1
else
    export ETK_STEALTH=0
fi

# --- [ RESTORED UI & ENGINES ] ---
export PYTHONPATH="${PYTHONPATH}:/storage/etk/lib/python3.13/site-packages"
export G='\033[0;32m'; export R='\033[0;31m'; export Y='\033[1;33m'; export C='\033[0;36m'; export N='\033[0m'
export DEFAULT_MODE="RACE"

# --- [ HUD ] ---
# Seconds the HUD shows the MODE|GAMEID launch header before collapsing to
# pure telemetry. Sourced by mango_bridge.sh.
export HUD_HEADER_HOLD_S=15

# --- [ PIPELINE CONTROLS ] ---
# Set to 1 for verbose Rsync output, 0 for clean output
export ETK_VERBOSE=1

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
export SIGNATURES_FILE="$ETK_ROOT/config/crash_signatures.json"

# Persistent session breadcrumb. Written at IDLE->RUNNING ignition, removed
# by session_postmortem.sh on a clean RUNNING->IDLE transition. If it
# survives a reboot, the previous session never reached postmortem (kernel
# panic / hard hang) and the Sentry synthesizes an orphan PANIC row on
# boot. Lives in $TELEMETRY_DIR (persistent) NOT $SHM_DIR (boot-volatile).
export SESSION_ANCHOR="$TELEMETRY_DIR/session_anchor.txt"

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
export RPCS3_CUSTOM_CONFIGS="/storage/games-internal/roms/bios/rpcs3/custom_configs"
export RPCS3_HDD1_CACHE="/storage/games-internal/roms/bios/rpcs3/dev_hdd1/caches"
export RPCS3_RUNTIME_CACHE="/storage/.cache/rpcs3/cache"
export RPCS3_LOG="/storage/.cache/rpcs3/RPCS3.log"

# Staging drop folder — the user places ONE .pkg (+ optional .rap) here; the
# installer deletes the staged files on a SUCCESSFUL install only.
export PKG_STAGING_DIR="$ETK_ROOT/pkg_install_drop"
export PS3_LAUNCHER_DIR="/storage/games-internal/roms/ps3"

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