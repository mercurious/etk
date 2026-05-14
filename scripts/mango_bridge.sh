#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: HUD BRIDGE (v13.1.1 - REBOOT SURVIVOR)
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

# 0. SHM REBOOT SEEDING
# Immediately recreate the volatile file so MangoHud doesn't crash on boot race conditions
mkdir -p "$SHM_DIR" 2>/dev/null
echo "INITIALIZING" > "$LIVE_STAT"

while true; do
    # 1. IDENTIFICATION (Re-source to catch TARGET_ID shifts)
    source /storage/games-internal/roms/etk/scripts/env.sh
    V_DIR="$VAULT_DIR"
    
    # 2. CONSUME THERMAL STATE (Produced by thermal_d.sh)
    # Fallback to 'WAIT' if the Governor hasn't posted yet
    T_STAT=$(cat "$SHM_DIR/thermal_stat" 2>/dev/null || echo "WAIT")

    # 3. PERFORMANCE METRICS (BusyBox Safe)
    LOAD_RAW=$(cat /proc/loadavg | awk '{print $1}')
    # Convert 8.65 to 865 for integer comparison
    LOAD_INT=$(echo "$LOAD_RAW" | awk '{print int($1 * 100)}')
    
    L_STAT="OK"
    if [ "$LOAD_INT" -gt 860 ]; then 
        L_STAT="REDLINE"
    elif [ "$LOAD_INT" -gt 590 ]; then 
        L_STAT="PEAK"
    fi
    
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')

# 4. ADVANCED SHADER TELEMETRY
    BANK=$(cat "$VAULT_COUNT" 2>/dev/null || echo "0")
    NEW_SHADERS=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo "0")
    
    # Calculate physical Vault size in MB (BusyBox Safe per Manifest)
    V_SIZE=$(du -m "$V_DIR" 2>/dev/null | awk '{print $1}')
    V_SIZE="${V_SIZE:-0}MB"

    # 5. ATOMIC HUD INJECTION [MANIFEST RULE: HUD FORMAT]
    # Injects the V_SIZE parameter to match: V: XXMB BANK (+NEW)
    printf "ID:%s | V:%s %s (+%s) | L:%s | T:%s | R:%s%%\n" \
        "$TARGET_ID" "$V_SIZE" "$BANK" "$NEW_SHADERS" "$L_STAT" "$T_STAT" "$RAM_VAL" > "$LIVE_STAT.tmp"
    
    mv -f "$LIVE_STAT.tmp" "$LIVE_STAT"

    sleep 1
done