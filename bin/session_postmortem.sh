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
# SUSPEND SKEW (found live 2026-07-06, the 14:54 mis-marked-CLEAN row):
# dmesg timestamps are CLOCK_MONOTONIC (frozen during s2idle suspend) but
# /proc/uptime is CLOCK_BOOTTIME (includes suspend). After the rig sleeps,
# epoch arithmetic overshoots the dmesg clock by the suspended time — a
# 2h27m suspend put window_start at 9071s while every dmesg line sat below
# ~1200s, so ALL FOUR of the session's keepalive rescues were excluded and
# the row wrote CLEAN. Anchor the window on the MONOTONIC clock instead:
#   window_start_mono = mono_now - (NOW - START_EPOCH)
# mono_now comes from /proc/timer_list ("now at N nsecs", CLOCK_MONOTONIC).
# If timer_list is unreadable, fall back to the old boottime math (correct
# on any boot that never suspended).
MONO_NOW=$(awk '/^now at/ {print int($3 / 1000000000); exit}' /proc/timer_list 2>/dev/null)
case "$MONO_NOW" in ''|*[!0-9]*) MONO_NOW=0 ;; esac
DMESG_WINDOW_START=0
if [ "$ANCHOR_RELIABLE" -eq 1 ]; then
    if [ "$MONO_NOW" -gt 0 ]; then
        DMESG_WINDOW_START=$((MONO_NOW - (NOW - START_EPOCH)))
    else
        DMESG_WINDOW_START=$((START_EPOCH - BOOT_EPOCH))
    fi
else
    # No reliable session anchor — fall back to the last 10 minutes of
    # the DMESG clock so at least pre-session faults don't bleed in
    # wholesale (monotonic when available, else boottime).
    if [ "$MONO_NOW" -gt 0 ]; then
        DMESG_WINDOW_START=$((MONO_NOW - 600))
    else
        DMESG_WINDOW_START=$((UPTIME_SEC - 600))
    fi
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
# Read via stdin redirect + bounded tail (NOT `strings "$RPCS3_LOG"`): the
# reader processes' argv no longer carries the rpcs3 log PATH, so the Sentry's
# emulator-detection can't mistake this parser for a running game; and we scan
# only the last 4MB instead of strings-ing a multi-hundred-MB log (RR7 hits
# ~288MB) on every postmortem. Defense-in-depth alongside the Sentry's -x fix.
RPCS3_TAIL=$(tail -c 4194304 <"$RPCS3_LOG" 2>/dev/null | strings | tail -n 200)
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

# --- SURVIVED RECLASSIFICATION (GTK full-stack, 2026-07-05) ---
# The parity kernel (msm.context_keepalive=1) can absorb an a6xx hang and
# keep the context usable — first live proof: row 1783302963 (GT5P 00E51485,
# freeze -> control regained -> race FINISHED, graceful exit, zero RPCS3.log
# errors). The signature scan alone mislabels that as RECOVERY:Adreno.
# Relabel to SURVIVED:<label> only when ALL of these hold (each guard is a
# real failure mode, not paranoia):
#   1. every windowed hangcheck recovery carries a keepalive survive line
#      (a survived hang followed by an UNsurvived one is still a crash);
#   2. no VK_DEVICE_LOST fatal in the scan (tguard fast-exit = the emulator
#      DIED of a fault, even if an earlier hang was absorbed);
#   3. the operator never pressed R3 (a survive that still needed a nuke
#      isn't a survive);
#   4. not a PANIC (kernel reboot outranks everything).
SURVIVES=$(echo "$DMESG_OUT" | grep -c 'context_keepalive: surviving hang')
HANGRECOVERS=$(echo "$DMESG_OUT" | grep -c 'hangcheck recover')
case "$SURVIVES" in ''|*[!0-9]*) SURVIVES=0 ;; esac
case "$HANGRECOVERS" in ''|*[!0-9]*) HANGRECOVERS=0 ;; esac
if [ "$SURVIVES" -gt 0 ] && [ "$SURVIVES" -ge "$HANGRECOVERS" ] \
   && [ "$R3_HONORED" -eq 0 ] && [ "$STATUS" != "PANIC" ] \
   && ! echo "$SIG_LIST" | grep -q 'VK_DEVICE_LOST'; then
    case "$STATUS" in
        RECOVERY:*) STATUS="SURVIVED:${STATUS#RECOVERY:}" ;;
        CLEAN)      STATUS="SURVIVED:Adreno" ;;
    esac
    CRASH_SIG="KEEPALIVE_SURVIVE${CRASH_SIG:+,$CRASH_SIG}"
fi

# --- CRASH FRAME (col 16, crash_shot) ---
# recovery.sh drops $SHM_DIR/crash_shot.txt (the frozen-frame basename) before
# the flush when it grabbed a frame. Bind it to THIS row so the ledger narrative
# links to the visual ("Adreno freeze at 312s" -> the actual frame of where).
# Same freshness guard + consume-after-read as the R3 sentinel; "-" when absent.
CRASH_SHOT="-"
CS_SENTINEL="$SHM_DIR/crash_shot.txt"
if [ -f "$CS_SENTINEL" ]; then
    CS_MTIME=$(stat -c %Y "$CS_SENTINEL" 2>/dev/null); case "$CS_MTIME" in ''|*[!0-9]*) CS_MTIME=0 ;; esac
    if [ "$ANCHOR_RELIABLE" -eq 0 ] || [ "$CS_MTIME" -ge "$START_EPOCH" ]; then
        CS=$(head -n1 "$CS_SENTINEL" 2>/dev/null | tr -d '\t\r')
        [ -n "$CS" ] && CRASH_SHOT="$CS"
    fi
    rm -f "$CS_SENTINEL" 2>/dev/null
fi

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

# --- GPU FAULT STATUS/FENCE (cols 24-25: gpu_fault_status, gpu_fault_fence_hex) ---
# GpuFaultTelemetryDossier.md proposed this enrichment (2026-06-18) but the col
# 17-18 slot it asked for was later claimed by the FPS gauge (cols 17-19 above);
# it was never actually wired in. Closes the gap: the a6xx_irq line is ALREADY
# in $DMESG_OUT (windowed, scanned for the Adreno signature at line ~185) — this
# just keeps the two fields the existing scan throws away. Distinct from
# fence_at_crash (col 11, decimal, sourced from the separate "rb 0: fence:"
# line) — these are hex, sourced from the a6xx_irq fault header itself, and are
# what a fresh capture's ib2 address needs to be cross-checked against (the
# cockpit spotter's .faultinfo sidecars use the same hex convention).
GPU_FAULT_LINE=$(echo "$DMESG_OUT" | grep -E 'a6xx_irq.*gpu fault' | tail -1)
GPU_FAULT_STATUS=$(printf '%s\n' "$GPU_FAULT_LINE" \
    | sed -n 's/.* status \([0-9A-Fa-f][0-9A-Fa-f]*\).*/\1/p')
GPU_FAULT_FENCE_HEX=$(printf '%s\n' "$GPU_FAULT_LINE" \
    | sed -n 's/.* fence \([0-9A-Fa-f][0-9A-Fa-f]*\).*/\1/p')
case "$GPU_FAULT_STATUS" in ''|*[!0-9A-Fa-f]*) GPU_FAULT_STATUS="-" ;; esac
case "$GPU_FAULT_FENCE_HEX" in ''|*[!0-9A-Fa-f]*) GPU_FAULT_FENCE_HEX="-" ;; esac

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

# --- ACTIVE TURNIP-DIAL TAG (col 15, tune_tag) ---
# The Pitstop DRIVER tab writes a compact dial signature to ACTIVE_TUNE_FILE
# (e.g. "autotune=prefer_gmem;tu_debug=nolrz" or "default"). Stamp it onto this
# session so genuine-play runs are attributable to the Turnip dials they ran
# under. Strip tabs/CR so it can never break TSV column alignment.
TUNE_TAG=$(head -n1 "$ACTIVE_TUNE_FILE" 2>/dev/null | tr -d '\t\r')
[ -z "$TUNE_TAG" ] && TUNE_TAG="default"

# The dials alone do not identify a run. The DRIVER tab can bind any of several
# Turnip builds, and ROCKNIX / the kernel / RPCS3 all move independently on top
# of that, so the same dial set means different things on different stacks.
# Prefix the full stack fingerprint (see etk_attribution_tag in env.sh) and
# every etk_dyno arm becomes (stack x driver x dials) instead of dials-only.
#
# This is not hypothetical: the 2026-07-30/31 zlatez arms straddled an RPCS3
# base bump, and pooling them read as a weakening effect rather than as two
# different stacks. An arm that spans a bump is not a measurement.
TUNE_TAG="$(etk_attribution_tag);${TUNE_TAG}"

# --- FPS / FRAMETIME (cols 17-19: fps_med, fps_1low, ft_p99_ms) ---
# G-INSTR: MangoHud auto-logs a per-session CSV (config/MangoHud.conf knobs:
# autostart_log + log_interval=1000) to $SHM_DIR/mangolog. Col1=fps,
# col2=frametime(ms); 3-line preamble (2 system rows + 1 data header).
# Aggregate honestly, RACE-FRAMES ONLY (frametime >= 25ms, i.e. fps <= ~40):
#   - this single window drops BOTH the >250fps scene-transition present-spikes
#     (frametime ~1ms) AND the 60fps menu/results/vsync frames (frametime 16.6ms)
#     that otherwise contaminate the median — GT5P gameplay is 8-30fps (33-125ms),
#     so a >=25ms gate cleanly isolates real race frames from menu time. Without
#     it, a menu-heavy session medians ~60 (validated on-rig 2026-06-22: a B-6
#     run read fps_med 62.7 — pure menu artifact, not a real framerate).
#   - fps_med  = median race fps (robust headline; NEVER mean-of-fps).
#   - fps_1low = 1%-low race fps (the stutter floor).
#   - ft_p99_ms= 99th-pct race frametime ms (the hitching tail).
#   - ft_jitter_ms = mean |Δframetime| between ADJACENT race frames — the
#     road-feel / "smooth-vs-slushy" metric (even pacing = low; lumps = high).
#     This is the Phase-N chase metric: median fps lies (a higher average can
#     feel worse), p99 only catches the worst hitch — jitter is the felt
#     frame-pacing consistency the driver reads as accurate speed/time.
# Absent CSV / too few race frames (crash before race, menu-only) => 0.
FPS_MED=0; FPS_1LOW=0; FT_P99=0; FT_JITTER=0; LOCK_PCT=0; PERFECT_PCT=0
MANGOLOG_DIR="$SHM_DIR/mangolog"
FPS_CSV=$(ls -1t "$MANGOLOG_DIR"/*.csv 2>/dev/null | head -1)
if [ -n "$FPS_CSV" ] && [ -f "$FPS_CSV" ]; then
    # Upper bound 500ms: per-frame logging (log_interval=0, 2026-07-04) emits
    # pause/load sentinel rows (observed: one 6,116,220ms "frame") that the
    # old 1Hz sampling never surfaced; unbounded they poison fps_1low and
    # explode ft_jitter (ledger rows 1783195788/1783196336: jit 16-18k ms).
    VALID=$(awk -F, 'NR>3 && ($1+0)>0 && ($2+0)>=25 && ($2+0)<=500 {printf "%s %s\n", $1, $2}' "$FPS_CSV")
    NV=$(printf '%s\n' "$VALID" | grep -c .)
    case "$NV" in ''|*[!0-9]*) NV=0 ;; esac
    if [ "$NV" -ge 3 ]; then
        FPS_MED=$(printf '%s\n' "$VALID" | awk '{print $1}' | sort -n \
            | awk '{a[NR]=$1} END{m=int((NR+1)/2); if(NR%2) printf "%.1f", a[m]; else printf "%.1f", (a[m]+a[m+1])/2}')
        FPS_1LOW=$(printf '%s\n' "$VALID" | awk '{print $1}' | sort -n \
            | awk '{a[NR]=$1} END{i=int(NR*0.01); if(i<1)i=1; printf "%.1f", a[i]}')
        FT_P99=$(printf '%s\n' "$VALID" | awk '{print $2}' | sort -n \
            | awk '{a[NR]=$1} END{i=int(NR*0.99+0.999); if(i>NR)i=NR; if(i<1)i=1; printf "%.1f", a[i]}')
        # ft_jitter: mean |Δframetime| over ADJACENT race frames. Computed on the
        # RAW CSV (not the VALID list) to preserve true frame adjacency; the same
        # >=25ms race-gate is applied inline so menu/transition frames neither
        # contribute deltas nor bridge a spurious one. Validated 2026-06-24
        # (6.3ms on a real 1761-delta run). BusyBox-awk safe.
        FT_JITTER=$(awk -F, 'NR>3 { ft=$2+0; fps=$1+0; r=(ft>=25 && ft<=500 && fps>0);
            if (r && p) { d=ft-q; if(d<0)d=-d; s+=d; n++ }
            q=ft; p=r } END{ if(n>1) printf "%.1f", s/n; else printf "0" }' "$FPS_CSV")
    fi
    # --- FABLE'S CHALLENGE KPI (cols 28-29: lock_pct, perfect_pct) ---
    # Judged by PERFECT-window share, NEVER by fps averages
    # (project_fables_challenge). The race-gated cols above (>=25ms) are
    # structurally blind to locked frames (GT HD Eiger lesson), so the lock
    # lives in its own columns:
    #   lock_pct    = % of gameplay frames inside the game's locked window
    #   perfect_pct = % of 5-second windows that are PERFECT (>=95% locked
    #                 frames AND no hitch) — THE KPI number.
    # PER-TITLE TARGET (operator-set 2026-07-05): GT HD races for the full
    # console lock (60 fps / 16.7 ms); the GT5P family races for a locked 30
    # (33.3 ms) as the reasonable rung. Window scales with the target using
    # the canonical 60fps ratios (15.5/18.0 around 16.7; hitch 40ms = 2.4x):
    #   60 fps: lock [15.5, 18.0] ms, hitch > 40 ms
    #   30 fps: lock [31.0, 36.0] ms, hitch > 80 ms
    # A row's target is implied by game_id — compare KPI numbers only within
    # a title, never across targets.
    case "$GAME_ID" in
        NPEA00050|NPUA80075|NPEA00502) KPI_LO=31.0; KPI_HI=36.0; KPI_HITCH=80 ;;  # GT5P EU/US + GT6 digital: locked-30
        *)                             KPI_LO=15.5; KPI_HI=18.0; KPI_HITCH=40 ;;  # console lock (GT HD + default)
    esac
    # Gameplay frame = fps>0, 4ms <= ft <= 500ms (drops present-spikes and
    # pause sentinels, keeps menus+race alike — cross-check vs res_scale col
    # for KPI validity; bake sessions excluded at analysis time via shd).
    # Single awk pass; well under the <2s postmortem budget.
    LOCKSTATS=$(awk -F, -v lo="$KPI_LO" -v hi="$KPI_HI" -v hit="$KPI_HITCH" 'NR>3 {
        fps=$1+0; t=$2+0
        if (fps<=0 || t<4 || t>500) next
        n++; lk=(t>=lo && t<=hi)
        if (lk) nl++
        acc+=t/1000; wn++; if (lk) wl++
        if (t>hit) whit=1
        if (acc>=5) { wt++; if (wn>0 && wl/wn>=0.95 && whit==0) wp++
                      acc=0; wn=0; wl=0; whit=0 }
    } END {
        if (n>=60) printf "%.1f %.1f", nl/n*100, (wt>0 ? wp/wt*100 : 0)
        else printf "0 0"
    }' "$FPS_CSV")
    LOCK_PCT=${LOCKSTATS% *}
    PERFECT_PCT=${LOCKSTATS#* }

    # Archive the per-frame curve BEFORE pruning (audio_logs pattern, keep 12).
    # The ledger's fps cols are race-gated (>=25ms) for GT5P semantics — 60fps
    # stretches (GT HD Eiger) are invisible in them and the raw CSV was dying
    # here every session. Named by $NOW = ledger col 1 for a direct row join.
    mkdir -p "$TELEMETRY_DIR/mango_logs" 2>/dev/null
    cp "$FPS_CSV" "$TELEMETRY_DIR/mango_logs/$NOW.csv" 2>/dev/null
    ls -1t "$TELEMETRY_DIR/mango_logs"/*.csv 2>/dev/null | tail -n +13 | while read -r f; do
        rm -f "$f" 2>/dev/null
    done
    # Prune the session CSV(s) so SHM stays lean and the next session's
    # newest-CSV pick is unambiguous. MangoHud is already dead at postmortem.
    rm -f "$MANGOLOG_DIR"/*.csv 2>/dev/null
fi
case "$FPS_MED" in ''|*[!0-9.]*) FPS_MED=0 ;; esac
case "$FPS_1LOW" in ''|*[!0-9.]*) FPS_1LOW=0 ;; esac
case "$FT_P99" in ''|*[!0-9.]*) FT_P99=0 ;; esac
case "$FT_JITTER" in ''|*[!0-9.]*) FT_JITTER=0 ;; esac
case "$LOCK_PCT" in ''|*[!0-9.]*) LOCK_PCT=0 ;; esac
case "$PERFECT_PCT" in ''|*[!0-9.]*) PERFECT_PCT=0 ;; esac

# --- TUNE ATTRIBUTION (cols 20-21: res_scale, gpu_mhz) ---
# Stamp the run's RPCS3 internal resolution scale (TUNING tab) + GPU max clock
# (the OC) so the fps columns above are COMPARABLE across A/B runs — a 75%-vs-100%
# pair was otherwise indistinguishable in the ledger (both just "sddepth"). Read
# the active game's custom config; absent/non-numeric => 0 (honest "unknown").
RES_SCALE=$(grep -E '^  Resolution Scale:' "$RPCS3_CUSTOM_CONFIGS/config_${GAME_ID}.yml" 2>/dev/null \
    | head -1 | sed 's/.*: *//' | tr -dc '0-9')
case "$RES_SCALE" in ''|*[!0-9]*) RES_SCALE=0 ;; esac
GPU_HZ=$(cat /sys/class/devfreq/3d00000.gpu/max_freq 2>/dev/null)
case "$GPU_HZ" in ''|*[!0-9]*) GPU_HZ=0 ;; esac
GPU_MHZ=$((GPU_HZ / 1000000))

# --- POWER ATTRIBUTION (col 22: pwr) ---
# The active CPU/GPU governor + clock-pin profile (Pitstop POWER tab) so an
# unsupervised RACE-vs-BALANCED gov sweep is attributable. '# pwr=' lives in the
# POWER profile file; absent (no profile set) => 'none'.
PWR_TAG=$(sed -n 's/^# pwr=//p' /storage/etk-power/profile 2>/dev/null | head -1)
case "$PWR_TAG" in '') PWR_TAG=none ;; esac

# --- AUDIO ATTRIBUTION (col 26: aud / col 27: snd) ---
# aud: end-of-session cellAudio counters from the aud1 GTK-Edition build
# (/dev/shm/rpcs3_audio_stat — refreshed every ~2s in play + final dump at
# teardown, so even a hard crash leaves near-final values). Spaces fold to
# commas so the cell stays one token for whitespace-split readers. Absent
# (stock build / no dump) => '-'.
#
# STALE-GUARD, two independent checks (bug found 2026-08-05): the file is NOT
# reset between games, so a session that ends before the emulator writes it
# inherits the PREVIOUS game's audio wholesale. Live proof: a 14 s Demon's
# Souls session carried byte-identical audio to the 500 s SOULCALIBUR V session
# that preceded it by ~30 s (up_s=431.2, skip=2401 ...). The old mtime guard
# (>= START_EPOCH - 60) could not catch it — a 60 s grace window is wider than
# the gap between two consecutive launches.
#   1. mtime must be at or after this session's start (5 s slack for clock
#      skew only, not a grace window).
#   2. the file's own up_s= must not exceed this session's duration (+30 s
#      slack). A stale file describes a longer run than we just had.
# Either check failing => '-', because a wrong audio row is worse than none.
AUD_STAT="-"
AUD_FILE="/dev/shm/rpcs3_audio_stat"
if [ -f "$AUD_FILE" ]; then
    AUD_MT=$(stat -c %Y "$AUD_FILE" 2>/dev/null)
    case "$AUD_MT" in ''|*[!0-9]*) AUD_MT=0 ;; esac
    if [ "$START_EPOCH" -gt 0 ] && [ "$AUD_MT" -ge $((START_EPOCH - 5)) ]; then
        AUD_STAT=$(head -1 "$AUD_FILE" 2>/dev/null | tr ' \t' ',,')
        case "$AUD_STAT" in '') AUD_STAT="-" ;; esac
        # content cross-check: up_s claimed vs actual session duration
        if [ "$AUD_STAT" != "-" ]; then
            AUD_UP=$(printf '%s' "$AUD_STAT" | sed -n 's/.*up_s=\([0-9]*\).*/\1/p')
            case "$AUD_UP" in ''|*[!0-9]*) AUD_UP=0 ;; esac
            if [ "$AUD_UP" -gt $((DURATION + 30)) ]; then
                AUD_STAT="-"
            fi
        fi
    fi
fi

# aud2 timeline archive: /dev/shm/rpcs3_audio_log (wall-clock-stamped 2s
# samples) is wiped at every guest boot AND rig boot — without this copy the
# phase-resolved curve dies with the session (lost the first 5 instrumented
# sessions, 2026-07-03). Named by $NOW = ledger col 1, so row and curve join
# directly. Keep the newest 12 (~2MB max each, usually tens of KB).
if [ -f /dev/shm/rpcs3_audio_log ] && [ "$AUD_STAT" != "-" ]; then
    mkdir -p "$TELEMETRY_DIR/audio_logs" 2>/dev/null
    cp /dev/shm/rpcs3_audio_log "$TELEMETRY_DIR/audio_logs/$NOW.log" 2>/dev/null
    ls -1t "$TELEMETRY_DIR/audio_logs"/*.log 2>/dev/null | tail -n +13 | while read -r f; do
        rm -f "$f" 2>/dev/null
    done
fi

# Perf attribution (col 31, v0.7.1): /dev/shm/rpcs3_perf_stat — the RPCS3 fork's
# perf-overlay-at-Detail=High mirrors its OWN computed frame split there every
# ~overlay-interval (perfstat-v1 patch). Names the pack-bog: fps/ft_ms/cpu +
# ppu/spu/rsx %. Spaces fold to commas (one whitespace-split token). mtime-guarded
# vs session start; absent (overlay off / stock build) => '-'. The always-live
# reader len-guards, so pre-v0.7.1 rows without this column stay valid.
# Same stale-inheritance exposure as the audio block above (this file is not
# reset between games either), so the same tight mtime rule: at-or-after session
# start, 5 s clock-skew slack only. There is no self-reported uptime field here
# to cross-check against, so the mtime test carries it alone.
PERF_STAT="-"
PERF_FILE="/dev/shm/rpcs3_perf_stat"
if [ -f "$PERF_FILE" ]; then
    PERF_MT=$(stat -c %Y "$PERF_FILE" 2>/dev/null)
    case "$PERF_MT" in ''|*[!0-9]*) PERF_MT=0 ;; esac
    if [ "$START_EPOCH" -gt 0 ] && [ "$PERF_MT" -ge $((START_EPOCH - 5)) ]; then
        PERF_STAT=$(head -1 "$PERF_FILE" 2>/dev/null | tr ' \t' ',,')
        case "$PERF_STAT" in '') PERF_STAT="-" ;; esac
    fi
fi

# perf timeline archive: /dev/shm/rpcs3_perf_log (wall-clock 2s-ish samples),
# wiped every guest/rig boot — copy it out named by $NOW so the frame-split curve
# joins its ledger row directly (same convention as audio_logs). Keep newest 12.
if [ -f /dev/shm/rpcs3_perf_log ] && [ "$PERF_STAT" != "-" ]; then
    mkdir -p "$TELEMETRY_DIR/perf_logs" 2>/dev/null
    cp /dev/shm/rpcs3_perf_log "$TELEMETRY_DIR/perf_logs/$NOW.log" 2>/dev/null
    ls -1t "$TELEMETRY_DIR/perf_logs"/*.log 2>/dev/null | tail -n +13 | while read -r f; do
        rm -f "$f" 2>/dev/null
    done
fi

# RPCS3.log archive — the emulator TRUNCATES its log at every launch, so the log
# of a crashed session survives only until the next one starts. That cost a real
# investigation on 2026-08-05: the 1.6MB log of a deterministic Spec II SPU crash
# was overwritten by the two short runs that followed it, and the only surviving
# copy was a 13KB tail someone had happened to grab. Same convention as the two
# archives above (named by $NOW = ledger col 1, so row and log join directly).
# Plain cp, not gzip: this runs inside the postmortem's <2s budget. Kept to 6
# because these are ~1-2MB each rather than the tens of KB the SHM logs run.
if [ -f "${RPCS3_LOG:-/storage/.cache/rpcs3/RPCS3.log}" ]; then
    mkdir -p "$TELEMETRY_DIR/rpcs3_logs" 2>/dev/null
    cp "${RPCS3_LOG:-/storage/.cache/rpcs3/RPCS3.log}" "$TELEMETRY_DIR/rpcs3_logs/$NOW.log" 2>/dev/null
    ls -1t "$TELEMETRY_DIR/rpcs3_logs"/*.log 2>/dev/null | tail -n +7 | while read -r f; do
        rm -f "$f" 2>/dev/null
    done
fi

# snd: did this session have real audio hardware? A silent session (only the
# PipeWire dummy sink) must be excludable from audio A/B. CARD-PRESENCE-ONLY
# since 2026-07-07: the SM8250 probe race is fixed in the GTK kernel and the
# audio watchdog (which wrote the old "revived" sub-state via audio_boot.txt)
# is retired — see AI_MANIFEST "ROCKNIX AUDIO STACK". Precedence: dummy
# (session fact — RPCS3 bound auto_null; device-init lines live in the log
# HEAD, and a full grep of a 100MB+ log would blow the <2s budget, hence
# head -c) > nocard (no ALSA card at post-mortem) > ok.
SND_STAT="ok"
ls /proc/asound 2>/dev/null | grep -q "^card[0-9]" || SND_STAT="nocard"
if head -c 2000000 "$RPCS3_LOG" 2>/dev/null | grep -q 'DeviceID: "auto_null"'; then
    SND_STAT="dummy"
fi

# --- LEDGER WRITE ---
# Header written exactly once via tmp+mv (atomic on POSIX). Subsequent
# rows are direct appends; a partial last row on hard crash is a signal,
# not corruption. Columns are APPEND-ONLY-TRAILING (aud/snd are newest), so
# older narrower ledgers and any cut -f<n> reader stay valid; only fresh ledgers
# carry the labelled header.
# rescues (col 30, 2026-07-06): the keepalive survive COUNT for this session —
# the cost side of the LSD gear trade now that a wedge is a hitch, not a
# session death. Judged alongside perfect_pct: a lighter gear that buys pace
# but pays multiple freeze-rescues per race may still lose.
LEDGER_HEADER='epoch\tduration_s\tbuild\tgame_id\tstatus\tpeak_load\tpeak_ram_mb\tpeak_temp\tavg_temp\tcrash_sig\tfence_at_crash\tshaders_harvested\tdrain_pct\tthermal_overrides\ttune_tag\tcrash_shot\tfps_med\tfps_1low\tft_p99_ms\tres_scale\tgpu_mhz\tpwr\tft_jitter_ms\tgpu_fault_status\tgpu_fault_fence_hex\taud\tsnd\tlock_pct\tperfect_pct\trescues\tperf'
if [ ! -f "$SESSIONS_LEDGER" ]; then
    TMP="$SESSIONS_LEDGER.tmp"
    printf "$LEDGER_HEADER\n" > "$TMP"
    mv "$TMP" "$SESSIONS_LEDGER"
elif ! head -1 "$SESSIONS_LEDGER" | grep -q 'rescues'; then
    # Migrate a stale header (older ledgers were created with fewer columns) so
    # the ledger self-describes. Rewrite ONLY the header line; existing rows are
    # untouched — older rows simply lack the newest trailing cols (read as empty).
    TMP="$SESSIONS_LEDGER.tmp"
    { printf "$LEDGER_HEADER\n"; tail -n +2 "$SESSIONS_LEDGER"; } > "$TMP" && mv "$TMP" "$SESSIONS_LEDGER"
fi

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$NOW" "$DURATION" "$ETK_BUILD_TYPE" "$GAME_ID" "$STATUS" \
    "$PEAK_LOAD" "$PEAK_RAM" "$PEAK_TEMP" "$AVG_TEMP" \
    "$CRASH_SIG" "$FENCE_AT_CRASH" "$SHADERS" "$DRAIN_PCT" "$THERMAL_OVERRIDES" \
    "$TUNE_TAG" "$CRASH_SHOT" "$FPS_MED" "$FPS_1LOW" "$FT_P99" "$RES_SCALE" "$GPU_MHZ" "$PWR_TAG" "$FT_JITTER" \
    "$GPU_FAULT_STATUS" "$GPU_FAULT_FENCE_HEX" "$AUD_STAT" "$SND_STAT" "$LOCK_PCT" "$PERFECT_PCT" "$SURVIVES" "$PERF_STAT" \
    >> "$SESSIONS_LEDGER"

# --- FORENSICS HYGIENE: per-crash core prune ---
# Cores land 2-7GB EACH; the boot-time prune (02-etk-coredump.sh) cannot stop
# a within-boot storm — two teardown-segfault cores filled the 6.6GB UFS
# system partition on 2026-07-04 and silently broke emulator staging. This
# runs after EVERY session (each crash session ends here), keeps the newest
# 2 on the SD cores dir and clears legacy /storage strays. Fail-silent.
ls -t "$ETK_ROOT/cores"/*.core 2>/dev/null | tail -n +3 | while read -r c; do
    rm -f "$c" 2>/dev/null
done
ls -t /storage/cores/*.core 2>/dev/null | tail -n +3 | while read -r c; do
    rm -f "$c" 2>/dev/null
done

# --- CAREER ROLLUP ---
[ -x "$ETK_ROOT/scripts/career_aggregate.sh" ] && \
    "$ETK_ROOT/scripts/career_aggregate.sh" "$GAME_ID" >/dev/null 2>&1

# --- BREADCRUMB CONSUME ---
# A clean RUNNING->IDLE transition wrote a real ledger row; the
# persistent anchor's only purpose was to survive a panic, so retire it
# now. Leaving it would cause the Sentry's next-boot orphan-detect to
# synthesize a duplicate PANIC row for a session we just rolled up
# normally.
rm -f "$SESSION_ANCHOR" 2>/dev/null

exit 0
