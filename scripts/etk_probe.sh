#!/bin/bash
# ==========================================================
# ETK THERMAL PROBE (v1.0)
# Location: $ETK_ROOT/scripts/etk_probe.sh
# Writes to: $ETK_ROOT/etk_probe/
# ==========================================================
# Three-mode thermal/freq probe for empirical thermal calibration.
# Captures all relevant zones + CPU/GPU frequencies at 1Hz.
#
# Usage:
#   etk_probe.sh start <label>   # Begin session, label it (e.g., "raw_run3")
#   etk_probe.sh stop            # End current session
#   etk_probe.sh report          # Output markdown block for the last session
#   etk_probe.sh peek            # Show current peak values without stopping
# ==========================================================

# Source ETK environment for path consistency
source /storage/games-internal/roms/etk/scripts/env.sh

PROBE_DIR="$ETK_ROOT/etk_probe"
PID_FILE="$PROBE_DIR/probe.pid"
CURRENT_FILE="$PROBE_DIR/current_label.txt"
INTERVAL=1

mkdir -p "$PROBE_DIR"

# Zones we care about: idx:label format
# Sourced from the active device profile (scripts/profiles/<soc>.sh).
# Curated subset for sampling; full enumeration is `etk_probe.sh discover`
# (Phase 5).
ZONES="$THERMAL_ZONE_MAP"

probe_loop() {
    local outfile="$1"
    
    # CSV header
    local header="epoch,uptime"
    for z in $ZONES; do
        header="$header,$(echo $z | cut -d: -f2)"
    done
    header="$header,gpu_mhz,silver_mhz,gold_mhz,prime_mhz"
    echo "$header" > "$outfile"
    
    while true; do
        local epoch=$(date +%s)
        local uptime_s=$(awk '{print int($1)}' /proc/uptime)
        local row="$epoch,$uptime_s"
        
        # Thermal zones (millidegrees -> degrees)
        for z in $ZONES; do
            local idx=$(echo $z | cut -d: -f1)
            local t=$(cat /sys/class/thermal/thermal_zone${idx}/temp 2>/dev/null || echo 0)
            row="$row,$((t / 1000))"
        done
        
        # GPU frequency (Hz -> MHz)
        local gpu=$(cat ${GPU_DEVFREQ_NODE}/cur_freq 2>/dev/null || echo 0)
        # CPU frequencies (kHz -> MHz) for each cluster
        local s=$(cat /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_SILVER}/scaling_cur_freq 2>/dev/null || echo 0)
        local g=$(cat /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/scaling_cur_freq 2>/dev/null || echo 0)
        local p=$(cat /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/scaling_cur_freq 2>/dev/null || echo 0)
        row="$row,$((gpu / 1000000)),$((s / 1000)),$((g / 1000)),$((p / 1000))"
        
        echo "$row" >> "$outfile"
        sleep $INTERVAL
    done
}

case "$1" in
    start)
        LABEL="${2:-unlabeled}"
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        OUTFILE="$PROBE_DIR/${TIMESTAMP}_${LABEL}.csv"
        
        # Kill any existing probe
        if [ -f "$PID_FILE" ]; then
            OLD_PID=$(cat "$PID_FILE")
            kill "$OLD_PID" 2>/dev/null
            rm -f "$PID_FILE"
        fi
        
        # Start new probe in background
        probe_loop "$OUTFILE" &
        echo $! > "$PID_FILE"
        echo "$OUTFILE" > "$CURRENT_FILE"
        
        echo "PROBE STARTED: $LABEL"
        echo "Output: $OUTFILE"
        echo "PID:    $(cat $PID_FILE)"
        ;;
        
    stop)
        if [ -f "$PID_FILE" ]; then
            kill "$(cat $PID_FILE)" 2>/dev/null
            rm -f "$PID_FILE"
            echo "PROBE STOPPED."
            if [ -f "$CURRENT_FILE" ]; then
                OUTFILE=$(cat "$CURRENT_FILE")
                ROWS=$(wc -l < "$OUTFILE")
                echo "Captured $((ROWS - 1)) samples in $OUTFILE"
            fi
        else
            echo "No probe running."
        fi
        ;;
        
    peek)
        if [ -f "$CURRENT_FILE" ] && [ -f "$PID_FILE" ]; then
            OUTFILE=$(cat "$CURRENT_FILE")
            echo "Current peaks (probe running):"
            awk -F, 'NR==1 { for(i=3;i<=NF;i++) names[i]=$i; next }
                     { for(i=3;i<=NF;i++) if($i+0 > peak[i]) peak[i]=$i+0 }
                     END { for(i=3;i<=NF;i++) printf "  %-12s %d\n", names[i]":", peak[i] }' "$OUTFILE"
        else
            echo "No probe running."
        fi
        ;;
        
    report)
        if [ ! -f "$CURRENT_FILE" ]; then
            echo "No probe data found."
            exit 1
        fi
        
        OUTFILE=$(cat "$CURRENT_FILE")
        LABEL=$(basename "$OUTFILE" .csv | sed 's/^[0-9_]*//')
        SAMPLES=$(($(wc -l < "$OUTFILE") - 1))
        DURATION=$(awk -F, 'NR==2{first=$1} END{print $1-first}' "$OUTFILE")
        
        echo "## Probe Report: $LABEL"
        echo ""
        echo "**File:** \`$OUTFILE\`"
        echo "**Samples:** $SAMPLES"
        echo "**Duration:** ${DURATION}s"
        echo ""
        echo "### Peak Values"
        echo ""
        echo '```'
        awk -F, '
        NR==1 { for(i=3;i<=NF;i++) names[i]=$i; next }
        { for(i=3;i<=NF;i++) if($i+0 > peak[i]) peak[i]=$i+0 }
        END { for(i=3;i<=NF;i++) printf "%-12s %d\n", names[i]":", peak[i] }
        ' "$OUTFILE"
        echo '```'
        echo ""
        echo "### Recent dmesg (Adreno/fence/timeout)"
        echo ""
        echo '```'
        dmesg | strings | grep -iE "adreno|fence|timeout|hangcheck" | tail -n 10
        echo '```'
        echo ""
        echo "### Full CSV"
        echo ""
        echo '```csv'
        cat "$OUTFILE"
        echo '```'
        ;;
        
    *)
        echo "Usage: etk_probe.sh {start <label>|stop|peek|report}"
        exit 1
        ;;
esac