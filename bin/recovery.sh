#!/bin/bash
# ==========================================================
# ETK PHASE 10: NUCLEAR RECOVERY (v10.0.0 - HEADLESS PANIC)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. SINGLE SOURCE OF TRUTH: This is the ONLY definition of
#    nuclear recovery. commander.sh ([R]/RECOVERY) and
#    input_d.py (R3 panic button) MUST call this script.
#    Do NOT re-inline this logic anywhere else.
# 2. HEADLESS: Runs untethered with no terminal attached.
#    The echo lines are harmless when detached; do not add
#    interactive prompts or guards.
# 3. SENTRY HANDOFF: Killing rpcs3 here makes the Sentry
#    observe RUNNING->IDLE on its next tick and tear down
#    workers + reseed LIVE_STAT. The pkills below are for
#    IMMEDIATE deadlock break, not a replacement for it.
# 4. BUSYBOX: POSIX only. killall/pkill/rm as used below.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

echo -e "\n\033[31m[!] INITIATING NUCLEAR RECOVERY...\033[0m"

# 1. Break the GPU Deadlock by killing the emulator
killall -9 rpcs3 2>/dev/null
killall -9 AppRun.wrapped 2>/dev/null

# 2. Kill worker daemons ONLY (Leave the Sentry alive to respawn them)
pkill -9 -f "mango_bridge.sh" 2>/dev/null
pkill -9 -f "vault_d.sh" 2>/dev/null
pkill -9 -f "thermal_d.sh" 2>/dev/null

rm -f "$SHM_DIR"/*
echo "IDLE" > "$ID_FILE"
echo -e "\033[32m[+] RECOVERY COMPLETE. EMULATOR TERMINATED.\033[0m"
