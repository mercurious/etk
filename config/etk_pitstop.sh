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
#    Priority: live SHM (game running) -> persistent anchor (last played) -> hardcoded default
if [ -f "$ID_FILE" ] && [ "$(cat $ID_FILE 2>/dev/null)" != "IDLE" ]; then
    RAW_ID=$(cat "$ID_FILE")
else
    RAW_ID=$(cat "$RECENT_ID_FILE" 2>/dev/null)
fi

CLEAN_ID=$(echo "$RAW_ID" | tr -d '[:space:]')
if [ -z "$CLEAN_ID" ]; then
    CLEAN_ID="NPUA80075"
fi
export TARGET_ID="$CLEAN_ID"

# 4. Sanitize terminal space
clear

# 5. Hand off to the Python curses engine via $ETK_ROOT (single source of truth)
exec python3 "$ETK_ROOT/bin/etk_pitstop.py"