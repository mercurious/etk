#!/bin/bash
# ==========================================================
# ETK PHASE 13.9: PULSE MONITOR (FIXED MATH)
# ==========================================================
PID=$(pgrep -f "rpcs3" | head -n 1)
if [ -z "$PID" ]; then echo "Launch GT5P first!"; exit 1; fi

LOG="/storage/games-internal/roms/etk/logs/pulse.log"
echo "TIMESTAMP | STATE | SW_DELTA | LOAD" > "$LOG"

# Initial Reading (Ensuring a single integer)
LAST_SW=$(grep "voluntary_ctxt_switches" "/proc/$PID/status" | head -n 1 | awk '{print $2}')

echo "[*] PULSE MONITOR ACTIVE. TAIL $LOG TO WATCH."

while true; do
    # Pull current state and context switches
    DATA=$(grep -E "State|voluntary_ctxt_switches" "/proc/$PID/status")
    STATE=$(echo "$DATA" | grep "State" | head -n 1 | awk '{print $2}')
    CUR_SW=$(echo "$DATA" | grep "voluntary_ctxt_switches" | head -n 1 | awk '{print $2}')
    
    # Calculate progress (Delta)
    DELTA=$((CUR_SW - LAST_SW))
    LOAD=$(cat /proc/loadavg | cut -d' ' -f1)
    
    # Log with precision
    echo "$(date +%H:%M:%S.%N) | $STATE | $DELTA | $LOAD" >> "$LOG"
    
    LAST_SW=$CUR_SW
    sleep 0.2
done