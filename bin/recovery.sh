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

# 1. Break the GPU Deadlock by killing the emulator.
#    On this build RPCS3 launches as an AppImage with TWO matchable processes:
#    the launcher (comm "rpcs3-sa", /usr/bin/rpcs3-sa) and the runtime
#    (comm "AppRun.wrapped"). BOTH must die or the Sentry's
#    `pgrep -f "rpcs3-sa|AppRun.wrapped"` keeps seeing RUNNING, the
#    RUNNING->IDLE handoff (rule 3) never fires, and the post-mortem never
#    rolls up the recovered session. killall keys on comm and (BusyBox) has
#    missed these names in testing, so the `pkill -9 -f` line below is the
#    authoritative kill — it mirrors the Sentry's exact detection pattern,
#    guaranteeing the next tick observes IDLE.
killall -9 rpcs3-sa 2>/dev/null
killall -9 AppRun.wrapped 2>/dev/null
killall -9 rpcs3 2>/dev/null
pkill -9 -f "rpcs3-sa|AppRun.wrapped" 2>/dev/null

# 2. Kill worker daemons ONLY (Leave the Sentry alive to respawn them)
pkill -9 -f "mango_bridge.sh" 2>/dev/null
pkill -9 -f "vault_d.sh" 2>/dev/null
pkill -9 -f "thermal_d.sh" 2>/dev/null

# R3 audit sentinel — written BEFORE the flush so the preserve-list keeps
# it for session_postmortem.sh on the next Sentry tick. Without this, an
# R3 panic on a session that didn't fire any kernel-side crash signature
# misclassifies as CLEAN (or ABORTED if <60s) — see ledger rows where the
# user pressed R3 but the row reads CLEAN. The mtime acts as a freshness
# check: postmortem ignores a sentinel that predates session_start.
mkdir -p "$SHM_DIR" 2>/dev/null
date +%s > "$SHM_DIR/r3_pressed.txt"

# Flush stale IPC state, but PRESERVE the post-mortem seed files.
# The Sentry observes RUNNING->IDLE on its next tick (rule 3 handoff)
# and fires session_postmortem.sh — which needs these five files to
# attribute DUR / DRAIN / SHD and R3-origin to the crash you just
# recovered from. They are overwritten cleanly on the next ignition, so
# nothing stale leaks forward. BusyBox-safe: for/case/${##} are all POSIX.
for f in "$SHM_DIR"/*; do
    case "${f##*/}" in
        session_start.txt|battery_start.txt|thermal_log_start.txt|vault_new.txt|r3_pressed.txt) ;;
        *) rm -f "$f" ;;
    esac
done
echo "IDLE" > "$ID_FILE"
echo -e "\033[32m[+] RECOVERY COMPLETE. EMULATOR TERMINATED.\033[0m"
