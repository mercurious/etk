#!/bin/sh
# ==========================================================
# ETK PITSTOP PRODUCTION LAUNCHER (BUSYBOX COMPLIANT + HIGH-DPI SCALE size 28)
# Location: /storage/.config/modules/etk_pitstop.sh
# Version: 12.1.2 - FOOT FLAG FIX
# ==========================================================
# GEMINI IMMUTABLE RULE:
# foot's font override flag is -o font="monospace:size=XX"
# NOT -f. The -f flag does not exist in foot and causes an
# immediate non-zero exit (Error 230) before the script body
# ever executes. The manifest specifies the correct form:
#   foot -F -o font="monospace:size=XX" <target>
# Do NOT change -o font= to -f or any other short flag.
# ==========================================================

# 1. Relaunch Check: If not already scaled, replace this process with a
#    scaled foot instance. Uses -o font= per foot's actual flag spec.
if [ "$ETK_SCALED" != "1" ]; then
    export ETK_SCALED=1
    exec /usr/bin/foot -F -o font="monospace:size=28" "$0" "$@"
fi

# 2. Source the single source of truth for all paths.
#    Gives us $ID_FILE, $RECENT_ID_FILE, and $ETK_ROOT without hardcoding.
source /storage/games-internal/roms/etk/scripts/env.sh

# 3. Resolve active game identity.
#    Priority: live SHM (game running) -> persistent anchor (last played).
#    NO silent hardcoded fallback: editing a default config when no game is
#    resolved is exactly how tuning silently lands in the wrong file and
#    looks "not retained" on the next launch. Fail loudly instead.
RAW_ID=""
if [ -f "$ID_FILE" ]; then
    FILE_ID=$(cat "$ID_FILE" 2>/dev/null | tr -d '[:space:]')
    case "$FILE_ID" in
        IDLE|UNKNOWN_ID|"") ;;
        *) RAW_ID="$FILE_ID" ;;
    esac
fi
if [ -z "$RAW_ID" ]; then
    RAW_ID=$(cat "$RECENT_ID_FILE" 2>/dev/null | tr -d '[:space:]')
fi

CLEAN_ID=$(echo "$RAW_ID" | tr -d '[:space:]')

# Title IDs are exactly 4 uppercase letters + 5 digits. Anything else
# (empty, IDLE, partial) means we could not resolve a target.
#
# DEGRADE, DON'T EXIT (dossier §3): a fresh rig with no installed PS3
# game must still be able to open Pitstop -- the TOOLS tab is the front
# door for installing that first game. So instead of exiting, launch the
# engine with ETK_NO_TARGET=1. The engine forces the TOOLS tab and renders
# TUNING / TELEMETRY as inert panels, so no default config or ledger is
# ever touched in the unresolved state.
if echo "$CLEAN_ID" | grep -qE '^[A-Z]{4}[0-9]{5}$'; then
    export TARGET_ID="$CLEAN_ID"
    export ETK_NO_TARGET=0
else
    export TARGET_ID=""
    export ETK_NO_TARGET=1
fi

# 4. Sanitize terminal space
clear

# 5. Hand off to the Python curses engine via $ETK_ROOT (single source of truth)
exec python3 "$ETK_ROOT/bin/etk_pitstop.py"