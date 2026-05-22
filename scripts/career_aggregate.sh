#!/bin/bash
# ==========================================================
# ETK SIMPLE TELEMETRY: CAREER AGGREGATE
# Version: 1.0.0
# ==========================================================
# Invoked by session_postmortem.sh after appending a session row.
# Safe to invoke manually: ./career_aggregate.sh NPUA80075
#
# Reads $SESSIONS_LEDGER, filters by game_id, computes aggregates,
# atomically writes $CAREER_DIR/<game_id>.txt.
# ==========================================================
# GEMINI IMMUTABLE RULE:
# - Sessions shorter than $TELEMETRY_MIN_SESSION_S (default 60s) are
#   EXCLUDED from career stats — they're aborted launches, not real
#   attempts. The threshold is a documented policy parameter set in
#   env.sh; it now lives in a var for visibility, but changing its VALUE
#   still shifts all historical career numbers — treat the value as
#   immutable.
# - Sessions with status ABORTED are excluded by name as well, so the
#   rule is legible and doesn't rely solely on the duration coincidence.
# - BusyBox-compliant. awk is used for the aggregation pass.
# - Atomic write via tmp+mv so a concurrent Pitstop read never sees
#   a half-written career file.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

GID="$1"
if [ -z "$GID" ]; then
    echo "usage: career_aggregate.sh <game_id>" >&2
    exit 1
fi

telemetry_init_dirs

# Nothing to aggregate yet — exit cleanly so a fresh install isn't a
# noisy failure.
[ ! -f "$SESSIONS_LEDGER" ] && exit 0

# Resolve game name from optional game_names.json. Graceful fallback to
# the ID itself when the file is absent (dossier §12).
GAME_NAME="$GID"
NAMES_FILE="$ETK_ROOT/config/game_names.json"
if [ -f "$NAMES_FILE" ]; then
    CANDIDATE=$(sed -n "s/.*\"$GID\"[[:space:]]*:[[:space:]]*\"\\(.*\\)\".*/\\1/p" "$NAMES_FILE" 2>/dev/null | head -1)
    [ -n "$CANDIDATE" ] && GAME_NAME="$CANDIDATE"
fi

# Single-pass awk aggregation. Field map (1-based):
#   1=epoch 2=duration_s 3=build 4=game_id 5=status
#   6=peak_load 7=peak_ram_mb 8=peak_temp 9=avg_temp
#   10=crash_sig 11=fence_at_crash 12=shaders_harvested
#   13=drain_pct 14=thermal_overrides
#
# Ledger is append-only oldest-first, so cur_streak at END reflects
# the streak ending on the newest row — which IS current_streak.
AGG=$(awk -F'\t' -v gid="$GID" -v mindur="${TELEMETRY_MIN_SESSION_S:-60}" '
    NR == 1 { next }
    $4 != gid { next }
    $2+0 < mindur { next }
    $5 == "ABORTED" { next }

    {
        total++
        total_duration += $2+0
        total_shaders += $12+0
        if ($5 == "CLEAN") {
            clean++
            cur_streak++
            if (cur_streak > best_streak) best_streak = cur_streak
        } else {
            cur_streak = 0
            crashes++
            if ($5 == "PANIC") panic++
        }
    }
    END {
        if (total == 0) {
            print "0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0"
            exit
        }
        recov = crashes - panic
        clean_rate = int(clean * 100.0 / total + 0.5)
        avg_shaders = (total > 0) ? int(total_shaders / total + 0.5) : 0
        current = cur_streak
        printf "%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n",
            total, total_duration, clean, crashes, recov, panic,
            clean_rate, total_shaders, avg_shaders, current, best_streak
    }
' "$SESSIONS_LEDGER")

# Parse aggregate fields. Using set -- (positional split) is BusyBox-safe.
set -- $AGG
TOTAL_SESS="${1:-0}"
TOTAL_DUR="${2:-0}"
CLEAN_CNT="${3:-0}"
CRASH_CNT="${4:-0}"
RECOV_CNT="${5:-0}"
PANIC_CNT="${6:-0}"
CLEAN_RATE="${7:-0}"
TOTAL_SHD="${8:-0}"
AVG_SHD="${9:-0}"
CUR_STREAK="${10:-0}"
BEST_STREAK="${11:-0}"

# Human-readable duration (Xh Ym).
HOURS=$((TOTAL_DUR / 3600))
MINS=$(((TOTAL_DUR % 3600) / 60))
DURATION_HUMAN="${HOURS}h ${MINS}m"

# Atomic write. Pitstop reads this file on every TELEMETRY-tab refresh;
# a torn write would briefly show partial career data.
TMP="$CAREER_DIR/$GID.txt.tmp"
{
    echo "game_id=$GID"
    echo "game_name=$GAME_NAME"
    echo "total_duration_s=$TOTAL_DUR"
    echo "total_duration_human=$DURATION_HUMAN"
    echo "total_sessions=$TOTAL_SESS"
    echo "clean_rate_pct=$CLEAN_RATE"
    echo "crash_count=$CRASH_CNT"
    echo "recov_count=$RECOV_CNT"
    echo "panic_count=$PANIC_CNT"
    echo "total_shaders=$TOTAL_SHD"
    echo "avg_shaders_per_session=$AVG_SHD"
    echo "current_streak=$CUR_STREAK"
    echo "best_streak=$BEST_STREAK"
} > "$TMP"
mv "$TMP" "$CAREER_DIR/$GID.txt"

exit 0
