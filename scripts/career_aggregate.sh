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
#   13=drain_pct 14=thermal_overrides 15=tune_tag 16=crash_shot
#   17=fps_med 18=fps_1low 19=ft_p99_ms 20=res_scale 21=gpu_mhz
#   22=pwr 23=ft_jitter_ms   (cols 15+ are append-only-trailing; older
#   rows lack them so $17/$23 read empty -> +0 -> 0 and are skipped)
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
        # fps_med (17) / ft_jitter_ms (23): average only over rows that carry
        # real race data (>0) so menu-only / crash-before-race / pre-instrument
        # rows do not drag the benchmark toward zero.
        if ($17+0 > 0) { sum_fps += $17+0; n_fps++ }
        if ($23+0 > 0) { sum_jit += $23+0; n_jit++ }
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
            print "0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0"
            exit
        }
        recov = crashes - panic
        clean_rate = int(clean * 100.0 / total + 0.5)
        avg_shaders = (total > 0) ? int(total_shaders / total + 0.5) : 0
        current = cur_streak
        avg_dur = int(total_duration / total + 0.5)
        avg_fps = (n_fps > 0) ? sum_fps / n_fps : 0
        avg_jit = (n_jit > 0) ? sum_jit / n_jit : 0
        printf "%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%.1f\t%.1f\n",
            total, total_duration, clean, crashes, recov, panic,
            clean_rate, total_shaders, avg_shaders, current, best_streak,
            avg_dur, avg_fps, avg_jit
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
AVG_DUR="${12:-0}"
AVG_FPS="${13:-0}"
AVG_JIT="${14:-0}"

# Human-readable duration (Xh Ym).
HOURS=$((TOTAL_DUR / 3600))
MINS=$(((TOTAL_DUR % 3600) / 60))
DURATION_HUMAN="${HOURS}h ${MINS}m"

# Human-readable AVERAGE run length (Xm Ys) — the per-run benchmark.
A_MIN=$((AVG_DUR / 60))
A_SEC=$((AVG_DUR % 60))
AVG_DUR_HUMAN="${A_MIN}m ${A_SEC}s"

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
    echo "avg_duration_s=$AVG_DUR"
    echo "avg_duration_human=$AVG_DUR_HUMAN"
    echo "avg_fps=$AVG_FPS"
    echo "avg_jitter=$AVG_JIT"
} > "$TMP"
mv "$TMP" "$CAREER_DIR/$GID.txt"

exit 0
