#!/bin/bash
# ==========================================================
# ETK PHASE 13: HUD BRIDGE (RECOVERY MODE)
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

mkdir -p "$SHM_DIR" 2>/dev/null

while true; do
    # 1. IDENTIFICATION (Re-source to catch TARGET_ID from env.sh)
    source /storage/games-internal/roms/etk/scripts/env.sh
    V_DIR="$VAULT_DIR"
    
    # 2. RAW SENSING & THERMAL
    T_RAW=$(cat /sys/class/thermal/thermal_zone14/temp 2>/dev/null || echo "0")
    TEMP=$((T_RAW / 1000))
    MODE=$(cat "$MODE_FILE" 2>/dev/null || echo "$DEFAULT_MODE")
    
    T_STAT="OK"
    if [ "$TEMP" -ge "$RACE_THRESHOLD" ]; then T_STAT="OVERHEAT"; elif [ "$TEMP" -ge "$ALARM_TEMP" ]; then T_STAT="WARNING"; fi

    # 3. PERFORMANCE METRICS
    LOAD=$(cat /proc/loadavg | awk '{print $1}')
    L_STAT="OK"
    if (( $(echo "$LOAD > 8.6" | bc -l) )); then L_STAT="REDLINE"; elif (( $(echo "$LOAD > 5.9" | bc -l) )); then L_STAT="PEAK"; fi
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')

    # 4. ADVANCED SHADER TELEMETRY
    BANK=$(cat "$VAULT_COUNT" 2>/dev/null || ls -1 "$V_DIR" 2>/dev/null | wc -l || echo "0")
    NEW_C=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo "0")
    V_MB=$(cat "$SHM_DIR/vault_size.txt" 2>/dev/null || du -sm "$V_DIR" 2>/dev/null | awk '{print $1}' || echo "0")

    # 5. ATOMIC WRITE (RESTORING ORIGINAL LAYOUT)
    # This removes the "custom_text_center=" prefix that caused the display error
    BAR="ETK: $MODE [$TARGET_ID] | ${TEMP}°C $T_STAT | LOAD: $LOAD $L_STAT | RAM: ${RAM_VAL}% | ${V_MB} MB BANK: ${BANK} NEW: ${NEW_C}"
    
    echo "$BAR" > "${LIVE_STAT}.tmp"
    mv "${LIVE_STAT}.tmp" "$LIVE_STAT"

    sleep 2
done