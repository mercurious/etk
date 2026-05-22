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
export RIG_IP="192.168.1.53"
export RIG_SSH="root@192.168.1.53"


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

# --- [ RESTORED THERMAL BOUNDARIES ] ---
# Recalibrating to Rocknix nightly-20260516 changed thermals
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