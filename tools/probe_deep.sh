#!/bin/bash
source /storage/games-internal/roms/etk/scripts/env.sh

# 1. TARGET ONLY THE MASTER PROCESS
PID=$(pgrep -f "rpcs3" | head -n 1)

if [ -z "$PID" ]; then
    echo "[!] RPCS3 NOT FOUND"
    exit 1
fi

echo ">>> TARGETING MASTER PID: $PID"
LOG_FILE="$ETK_ROOT/logs/forensic_fix.log"

# 2. THE DEEP DIVE (3 SHOTS)
for i in {1..3}; do
    echo "--- SNAPSHOT $i ---" >> "$LOG_FILE"
    
    # Check if the process is 'R' (Running), 'S' (Sleeping), or 'D' (Disk Sleep/Wait)
    ps -w | grep "$PID" | grep -v grep >> "$LOG_FILE"
    
    # Check Context Switches (Is it yielding or being kicked off?)
    if [ -f "/proc/$PID/status" ]; then
        grep -E "ctxt_switches|State" "/proc/$PID/status" >> "$LOG_FILE"
    fi
    
    # Check I/O Wait (Is the SD card bottlenecking the large shader cache?)
    cat /proc/loadavg >> "$LOG_FILE"
    
    sleep 2
done
echo ">>> PROBE FINISHED: $LOG_FILE"