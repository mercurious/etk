#!/bin/bash
# ==========================================================
# ETK SIMPLE TELEMETRY: SESSION POST-MORTEM
# Version: 1.0.0
# ==========================================================
# Invoked once by the Sentry on RUNNING -> IDLE transition (after the
# pkill block, before the PREV_STATE assignment). Aggregates session
# state from data sources that already exist (RPCS3.log, dmesg via
# probe.sh, thermal telemetry, SHM vault counter) and appends one TSV
# row to $SESSIONS_LEDGER. Then triggers career_aggregate.sh.
#
# Runtime budget: < 2 seconds. The operator is closing the game and
# shouldn't wait. All file reads tolerate absence with safe defaults.
# ==========================================================
# GEMINI IMMUTABLE RULE:
# - BusyBox-compliant only. No GNU find -printf, du -h, stat --format
#   without graceful fallback.
# - All paths derive from env.sh — never hardcode.
# - Tolerate missing files everywhere ('|| echo 0' / case-guarded vars).
# - The TSV header row is written exactly once via tmp+mv. Data rows are
#   direct appends — a partial last line on a crash is itself a signal.
# - Crash signature PATTERNS are duplicated inline from
#   config/crash_signatures.json because BusyBox sh cannot parse JSON
#   without jq. If the JSON patterns change, mirror them here.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

telemetry_init_dirs

# Refresh forensics so $CRASH_LOG reflects the just-ended session.
[ -x "$PROBE_SCRIPT" ] && "$PROBE_SCRIPT" >/dev/null 2>&1

# --- IDENTITY ---
GAME_ID=$(cat "$RECENT_ID_FILE" 2>/dev/null)
case "$GAME_ID" in
    ""|IDLE|UNKNOWN_ID) GAME_ID="UNKNOWN" ;;
esac

# --- TIMING ---
NOW=$(date +%s)

START_EPOCH=$(cat "$SHM_DIR/session_start.txt" 2>/dev/null)
case "$START_EPOCH" in
    ''|*[!0-9]*) START_EPOCH=0 ;;
esac

# ANCHOR_RELIABLE is 1 only when the SHM session-start seed gave a real
# epoch. The RPCS3.log mtime fallback below is a last resort: RPCS3
# writes its log continuously, so the mtime tracks session END, not
# start. A row built on that fallback carries low-confidence timing and
# fault attribution — fence_at_crash is honest-zeroed for it (Bug #1)
# and the TELEMETRY tab dims it.
ANCHOR_RELIABLE=1
[ "$START_EPOCH" -eq 0 ] && ANCHOR_RELIABLE=0

# Fallback: try RPCS3.log mtime if the SHM seed was missing.
# Rocknix RPCS3 writes to .cache/rpcs3/RPCS3.log (NOT .config, and the
# filename is capitalised). RPCS3 truncates this log on each launch, so
# at post-mortem time it holds exactly the session that just ended.
RPCS3_LOG="/storage/.cache/rpcs3/RPCS3.log"
if [ "$START_EPOCH" -eq 0 ] && [ -f "$RPCS3_LOG" ]; then
    MTIME=$(stat -c %Y "$RPCS3_LOG" 2>/dev/null)
    case "$MTIME" in
        ''|*[!0-9]*) MTIME=0 ;;
    esac
    START_EPOCH="$MTIME"
fi

if [ "$START_EPOCH" -gt 0 ]; then
    DURATION=$((NOW - START_EPOCH))
    [ "$DURATION" -lt 0 ] && DURATION=0
else
    DURATION=0
fi

# --- PANIC DETECTION ---
# Use /proc/uptime (bulletproof BusyBox) instead of GNU `date -d $(uptime -s)`.
# If boot time is later than the session start, the rig rebooted mid-session
# = kernel panic / hard hang. This catches device-down crashes before any
# signature scan, because dmesg from after a reboot won't show the prior
# session's fault patterns.
UPTIME_SEC=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)
case "$UPTIME_SEC" in
    ''|*[!0-9]*) UPTIME_SEC=0 ;;
esac
BOOT_EPOCH=$((NOW - UPTIME_SEC))

STATUS="CLEAN"
CRASH_SIG=""

if [ "$START_EPOCH" -gt 0 ] && [ "$BOOT_EPOCH" -gt "$START_EPOCH" ]; then
    STATUS="PANIC"
    CRASH_SIG="PANIC_REBOOT"
fi

# --- CRASH SIGNATURE SCAN ---
# Patterns mirrored from config/crash_signatures.json. Order matters —
# first match wins, matching the JSON array order.
#
# dmesg is a kernel ring buffer that PERSISTS across sessions until
# reboot. An Adreno fault from an earlier crashed run would otherwise
# misclassify a later clean run (and bleed a stale fence value into
# fence_at_crash). Window the dmesg scan to messages timestamped at or
# after this session's start, in seconds-since-boot:
#   window_start = START_EPOCH - BOOT_EPOCH
# RPCS3.log is already session-scoped — RPCS3 truncates it per launch.
# $CRASH_LOG is deliberately NOT in the haystack: probe.sh builds it
# from unwindowed dmesg, which would re-introduce the stale-fault bleed.
#
# CRITICAL: this anchor is only valid when ANCHOR_RELIABLE=1. The
# RPCS3.log mtime fallback sets START_EPOCH to the log's LAST write,
# which tracks session END (or the instant rpcs3 froze on a GPU fault) —
# NOT session start. Using that as window_start collapses the window to
# ~now and excludes the whole session's faults, so a real RECOVERY
# misclassifies as CLEAN. When the anchor is unreliable, fall back to
# the 10-minute net regardless of whether mtime gave a nonzero epoch.
DMESG_WINDOW_START=0
if [ "$ANCHOR_RELIABLE" -eq 1 ]; then
    DMESG_WINDOW_START=$((START_EPOCH - BOOT_EPOCH))
else
    # No reliable session anchor — fall back to the last 10 minutes of
    # uptime so at least pre-session faults don't bleed in wholesale.
    DMESG_WINDOW_START=$((UPTIME_SEC - 600))
fi
[ "$DMESG_WINDOW_START" -lt 0 ] && DMESG_WINDOW_START=0

# dmesg lines are prefixed "[   <secs>.<usecs>]". Keep only lines whose
# timestamp is >= the window start. Lines without a timestamp prefix
# (rare multi-line continuations) are dropped — their timestamped parent
# line carries the signature text anyway.
DMESG_OUT=$(dmesg 2>/dev/null | awk -v t="$DMESG_WINDOW_START" '
    match($0, /\[[ ]*[0-9]+\.[0-9]+\]/) {
        ts = substr($0, RSTART + 1, RLENGTH - 2) + 0
        if (ts >= t) print
    }
')
RPCS3_TAIL=$(strings "$RPCS3_LOG" 2>/dev/null | tail -n 200)
HAYSTACK=$(printf '%s\n%s\n' "$DMESG_OUT" "$RPCS3_TAIL")

# --- SEVERITY-RANKED SIGNATURE SCAN ---
# Highest-severity hit becomes STATUS; ALL hits are concatenated into
# CRASH_SIG so a session that fires SPU + Adreno + Vulkan-device-lost is
# diagnosable from the ledger even though the STATUS column shows only
# the dominant label. Severity tiers (mirrored from
# crash_signatures.json, which is the spec): critical=4, high=3,
# medium=2, low=1.
#
# Discovery (2026-05-23 rig probe): the live RPCS3.log carried 4
# "Predecessor not found for target" SPU hits AND 1 VK_ERROR_DEVICE_LOST
# fatal AND no Adreno line in the current dmesg ring — yet 138 historical
# RECOVERY rows are ALL "RECOVERY:Adreno". First-match-wins ordering on
# Adreno was masking SPU; VK_ERROR_DEVICE_LOST wasn't a signature at all.
WINNING_SEV=0
WINNING_LABEL=""
WINNING_SIG=""
SIG_LIST=""

sig_record() {
    # $1=severity $2=label $3=sig_id — accumulates hit into composite
    # list and promotes WINNING_* when severity beats the current top.
    if [ -z "$SIG_LIST" ]; then
        SIG_LIST="$3"
    else
        SIG_LIST="$SIG_LIST,$3"
    fi
    if [ "$1" -gt "$WINNING_SEV" ]; then
        WINNING_SEV="$1"
        WINNING_LABEL="$2"
        WINNING_SIG="$3"
    fi
}

if [ "$STATUS" = "CLEAN" ]; then
    # critical (4)
    if echo "$HAYSTACK" | grep -qE 'out of memory|oom-killer|Killed process.*rpcs3'; then
        sig_record 4 "OOM" "OOM_KILL"
    fi
    # high (3) — kernel-side GPU fault
    if echo "$HAYSTACK" | grep -qE 'a6xx_irq.*gpu fault|msm_dpu.*hangcheck recover|drm:recover_worker.*offending task.*rpcs3'; then
        sig_record 3 "Adreno" "GPU_FENCE_TIMEOUT"
    fi
    # high (3) — userspace twin of an Adreno fence timeout (and also fires
    # for non-Adreno Vulkan deaths: validation crashes, driver bugs). Scope
    # to fatal lines (F prefix) so non-terminal device-lost reports don't
    # promote a still-running session.
    if echo "$RPCS3_TAIL" | grep -qE '^F .*VK_ERROR_DEVICE_LOST'; then
        sig_record 3 "VkLost" "VK_DEVICE_LOST"
    fi
    # medium (2)
    if echo "$RPCS3_TAIL" | grep -qE 'VkPresent returned unexpected error code -4'; then
        sig_record 2 "VkSwap" "VK_SWAPCHAIN_DEATH"
    fi
    if echo "$HAYSTACK" | grep -qE 'SPU.*decoder|spu_recompiler.*fail|Predecessor not found for target'; then
        sig_record 2 "SPU" "SPU_RECOMPILER_FAULT"
    fi
    # low (1) — PPU livelock indicator. A single CELL_ESRCH lwmutex failure
    # is normal at startup; only treat as signature when it spams the log
    # (>=10 hits in the windowed tail), which historically correlates with
    # a genuine PPU deadlock. Threshold tuned to RPCS3.log baseline noise.
    PPU_HITS=$(echo "$RPCS3_TAIL" | grep -cE "_sys_lwmutex_lock.*failed.*CELL_ESRCH")
    case "$PPU_HITS" in ''|*[!0-9]*) PPU_HITS=0 ;; esac
    if [ "$PPU_HITS" -ge 10 ]; then
        sig_record 1 "PPU" "PPU_LIVELOCK"
    fi

    if [ -n "$WINNING_LABEL" ]; then
        STATUS="RECOVERY:$WINNING_LABEL"
        CRASH_SIG="$SIG_LIST"
    fi
fi

# --- R3 PANIC SENTINEL ---
# recovery.sh drops $SHM_DIR/r3_pressed.txt before the SHM flush. Honor it
# only when fresher than the session start (otherwise it's a stale
# sentinel from a prior R3 that fell through without rpcs3 actually
# dying). Composite with any signature the scan above found —
# RECOVERY:R3, RECOVERY:R3+Adreno, RECOVERY:R3+SPU, etc. — so the user
# always sees R3 attribution AND the diagnostic signal. PANIC (kernel
# reboot) outranks R3: a hard hang the user couldn't recover from is
# more useful than the attempted recovery flag.
R3_SENTINEL="$SHM_DIR/r3_pressed.txt"
R3_HONORED=0
if [ -f "$R3_SENTINEL" ] && [ "$STATUS" != "PANIC" ]; then
    R3_MTIME=$(stat -c %Y "$R3_SENTINEL" 2>/dev/null)
    case "$R3_MTIME" in ''|*[!0-9]*) R3_MTIME=0 ;; esac
    # Honor when (a) we have no reliable anchor (treat any sentinel as
    # fresh) or (b) sentinel mtime is at-or-after session start.
    if [ "$ANCHOR_RELIABLE" -eq 0 ] || [ "$R3_MTIME" -ge "$START_EPOCH" ]; then
        R3_HONORED=1
        # User feedback 2026-05-23: don't put R3 in the visible STATUS —
        # "RECOVERY" + "R3" is redundant in the row label, and the
        # limited column width should be spent on crash-signature detail
        # that drives TUNING edits. The R3 origin is recorded in
        # CRASH_SIG (R3_PANIC,<sig_id>...) so the distinction survives
        # for logic + forensics without occupying the visible column.
        if [ "$STATUS" = "CLEAN" ]; then
            # R3 fired but the scan was silent — that absence IS the
            # diagnostic ("operator had to nuke without us seeing why").
            STATUS="RECOVERY:Silent"
            [ -z "$CRASH_SIG" ] && CRASH_SIG="R3_PANIC"
        else
            # STATUS already RECOVERY:<sig_label> — keep it as-is so the
            # TUNING-relevant signature stays front-and-center.
            CRASH_SIG="R3_PANIC,$CRASH_SIG"
        fi
    fi
fi
# Always consume the sentinel — fresh or stale, leaving it would
# poison the next session's classification.
rm -f "$R3_SENTINEL" 2>/dev/null

# --- METRICS ---

# Peak RAM from RPCS3.log Performance Sensor lines. The real line shape
# on this stack (Rocknix RPCS3, verified on-rig):
#   PERF: CPU Usage: Total: 52.9%, Cores: ..., RAM Usage: 5524MB (Peak: 5569MB)
# RAM peak = max "(Peak: NNNNMB)". CPU Total% is deliberately NOT parsed:
# it pins to ~100 on this 6-core stack and carries no signal — peak
# loadavg (sampled by thermal_d, parsed in the thermal window below) is
# the discriminating metric and lands in ledger field 6 instead.
PEAK_RAM=0
if [ -f "$RPCS3_LOG" ]; then
    PEAK_RAM=$(strings "$RPCS3_LOG" 2>/dev/null \
        | grep -oE '\(Peak: [0-9]+MB\)' \
        | grep -oE '[0-9]+' \
        | awk 'BEGIN{m=0} {if ($1+0 > m) m=$1+0} END{print int(m+0)}')
    case "$PEAK_RAM" in ''|*[!0-9]*) PEAK_RAM=0 ;; esac
fi

# --- Thermal window ---
# Parse telemetry.log lines appended during THIS session, scoped via the
# line-count snapshot taken at IDLE->RUNNING. thermal_d.sh writes two
# line kinds:
#   "<epoch> SAMPLE <temp>"           throttled per-tick temperature
#   "... Switched to PIT at <T>C"     thermal override events
THERMAL_LOG="$ETK_ROOT/telemetry.log"
PEAK_TEMP=0
AVG_TEMP=0
PEAK_LOAD=0.0
THERMAL_OVERRIDES=0
START_LINE=$(cat "$SHM_DIR/thermal_log_start.txt" 2>/dev/null)
case "$START_LINE" in ''|*[!0-9]*) START_LINE=0 ;; esac
if [ -f "$THERMAL_LOG" ]; then
    TOTAL_LINE=$(wc -l < "$THERMAL_LOG" 2>/dev/null)
    case "$TOTAL_LINE" in ''|*[!0-9]*) TOTAL_LINE=0 ;; esac
    NEW_LINES=$((TOTAL_LINE - START_LINE))
    [ "$NEW_LINES" -lt 0 ] && NEW_LINES=0
    if [ "$NEW_LINES" -gt 0 ]; then
        WINDOW=$(tail -n "$NEW_LINES" "$THERMAL_LOG" 2>/dev/null)
        # Match the actual override idiom, not a bare "PIT" substring —
        # the latter is only accidentally correct today (Fix #6).
        THERMAL_OVERRIDES=$(echo "$WINDOW" | grep -c "THERMAL OVERRIDE")
        case "$THERMAL_OVERRIDES" in ''|*[!0-9]*) THERMAL_OVERRIDES=0 ;; esac
        # SAMPLE line shape: "<epoch> SAMPLE <temp> <loadavg>".
        # Field 3 = temp, field 4 = 1-minute loadavg (Bug #3). SAMPLE
        # lines from a pre-loadavg thermal_d have no field 4 — those
        # degrade to peak_load 0.0, which is honest historical data.
        SAMPLE_LINES=$(echo "$WINDOW" | grep ' SAMPLE ')
        SAMPLES=$(echo "$SAMPLE_LINES" | awk '{print $3}')
        if [ -n "$SAMPLES" ]; then
            PEAK_TEMP=$(echo "$SAMPLES" | awk 'BEGIN{m=0} {if ($1+0 > m) m=$1+0} END{print int(m+0)}')
            AVG_TEMP=$(echo "$SAMPLES" | awk 'BEGIN{s=0;n=0} {s+=$1+0;n++} END{if (n>0) print int(s/n+0.5); else print 0}')
        fi
        LOADS=$(echo "$SAMPLE_LINES" | awk '{print $4}')
        if [ -n "$LOADS" ]; then
            PEAK_LOAD=$(echo "$LOADS" | awk 'BEGIN{m=0} {if ($1+0 > m) m=$1+0} END{printf "%.1f", m+0}')
        fi
    fi
fi
case "$PEAK_TEMP" in ''|*[!0-9]*) PEAK_TEMP=0 ;; esac
case "$AVG_TEMP" in ''|*[!0-9]*) AVG_TEMP=0 ;; esac
case "$PEAK_LOAD" in ''|*[!0-9.]*) PEAK_LOAD=0.0 ;; esac

# Fence at crash — value AFTER "rb 0: fence:" in the last matching dmesg
# line. Note: the line is prefixed with a "[<secs>.<usecs>]" timestamp,
# so a naive "first integer" grab returns the timestamp, not the fence.
# sed-extract the digits immediately following "fence:".
FENCE_AT_CRASH=$(echo "$DMESG_OUT" | grep "rb 0: fence:" | tail -1 \
    | sed -n 's/.*fence:[[:space:]]*\([0-9][0-9]*\).*/\1/p')
case "$FENCE_AT_CRASH" in ''|*[!0-9]*) FENCE_AT_CRASH=0 ;; esac

# --- FENCE HONEST-ZERO + STALE-FENCE DEDUP (Bug #1) ---
# A zero-duration session with no reliable start anchor has no
# trustworthy fault attribution: the dmesg window fell back to the
# 10-minute net and re-admitted stale faults. An honest 0 beats a
# stale fence value.
if [ "$ANCHOR_RELIABLE" -eq 0 ] && [ "$DURATION" -eq 0 ]; then
    FENCE_AT_CRASH=0
fi
# Dedup guard: a GPU fence counter is monotonic within a boot, so two
# genuine consecutive crashes cannot share a fence value. An identical
# fence on the prior crash row means the windowed dmesg scan re-admitted
# a stale fault that survived from an earlier session (the live ledger
# showed fence 123 bleeding across five rows, three with real
# durations — the honest-zero guard alone misses those). Zero it.
if [ "$FENCE_AT_CRASH" -gt 0 ] && [ -f "$SESSIONS_LEDGER" ]; then
    PREV_ROW=$(tail -n 1 "$SESSIONS_LEDGER" 2>/dev/null)
    PREV_STATUS=$(printf '%s\n' "$PREV_ROW" | cut -f5)
    PREV_FENCE=$(printf '%s\n' "$PREV_ROW" | cut -f11)
    case "$PREV_FENCE" in ''|*[!0-9]*) PREV_FENCE=0 ;; esac
    case "$PREV_STATUS" in
        RECOVERY:*|PANIC)
            [ "$FENCE_AT_CRASH" -eq "$PREV_FENCE" ] && FENCE_AT_CRASH=0
            ;;
    esac
fi

# Shaders harvested — vault_d.sh writes this on every tick.
SHADERS=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null)
case "$SHADERS" in ''|*[!0-9]*) SHADERS=0 ;; esac

# Battery drain — capacity delta from session start.
BATT_START=$(cat "$SHM_DIR/battery_start.txt" 2>/dev/null)
case "$BATT_START" in ''|*[!0-9]*) BATT_START=0 ;; esac
BATT_NOW=$(cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1)
case "$BATT_NOW" in ''|*[!0-9]*) BATT_NOW=0 ;; esac
if [ "$BATT_START" -gt 0 ] && [ "$BATT_NOW" -gt 0 ]; then
    DRAIN_PCT=$((BATT_NOW - BATT_START))
else
    DRAIN_PCT=0
fi

# --- THERMAL_INFERRED fallback ---
# Dossier §9: signatures evaluate in order; THERMAL_INFERRED is the
# catch-all when nothing else matched AND peak_temp crossed ALARM_TEMP.
# With thermal_d still events-only, PEAK_TEMP is 0 today and this never
# fires — that's correct (no false attribution).
if [ "$STATUS" != "CLEAN" ] && [ "$STATUS" != "PANIC" ] && [ -z "$CRASH_SIG" ]; then
    if [ "$PEAK_TEMP" -gt "$ALARM_TEMP" ]; then
        CRASH_SIG="THERMAL_INFERRED"
    fi
fi

# --- ABORTED RECLASSIFICATION (Feature #7) ---
# A sub-threshold session that matched no crash signature is a
# force-quit or fat-finger abort, not a real attempt. Mark it ABORTED so
# the ledger doesn't present a 16s force-quit as a green CLEAN row.
# A genuine crash keeps its RECOVERY:* / PANIC even when short.
if [ "$STATUS" = "CLEAN" ] && [ "$DURATION" -lt "${TELEMETRY_MIN_SESSION_S:-60}" ]; then
    STATUS="ABORTED"
fi

# --- LEDGER WRITE ---
# Header written exactly once via tmp+mv (atomic on POSIX). Subsequent
# rows are direct appends; a partial last row on hard crash is a signal,
# not corruption.
if [ ! -f "$SESSIONS_LEDGER" ]; then
    TMP="$SESSIONS_LEDGER.tmp"
    printf 'epoch\tduration_s\tbuild\tgame_id\tstatus\tpeak_load\tpeak_ram_mb\tpeak_temp\tavg_temp\tcrash_sig\tfence_at_crash\tshaders_harvested\tdrain_pct\tthermal_overrides\n' > "$TMP"
    mv "$TMP" "$SESSIONS_LEDGER"
fi

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$NOW" "$DURATION" "$ETK_BUILD_TYPE" "$GAME_ID" "$STATUS" \
    "$PEAK_LOAD" "$PEAK_RAM" "$PEAK_TEMP" "$AVG_TEMP" \
    "$CRASH_SIG" "$FENCE_AT_CRASH" "$SHADERS" "$DRAIN_PCT" "$THERMAL_OVERRIDES" \
    >> "$SESSIONS_LEDGER"

# --- CAREER ROLLUP ---
[ -x "$ETK_ROOT/scripts/career_aggregate.sh" ] && \
    "$ETK_ROOT/scripts/career_aggregate.sh" "$GAME_ID" >/dev/null 2>&1

exit 0
