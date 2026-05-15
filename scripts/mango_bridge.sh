#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: HUD BRIDGE (v13.2.0 - INSTRUMENT LOCKDOWN)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. HUD STRING FORMAT IS LOCKED: 
#    ETK:[MODE][ID] | TEMP: XX°C STAT | LOAD: X.XX STAT | RAM: XX% STAT | VAULT: XX MB BANK: XXX NEW: XX
# 2. ATOMIC SWAP ONLY: MUST use `echo > tmp && mv tmp $LIVE_STAT`
# 3. RAW TEXT ONLY: No MangoHud configuration keys (e.g., custom_text=) in the output.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

# 0. SHM REBOOT SEEDING
mkdir -p "$SHM_DIR" 2>/dev/null
echo "INITIALIZING" > "$LIVE_STAT"

while true; do
    # 1. IDENTIFICATION
    source /storage/games-internal/roms/etk/scripts/env.sh
    V_DIR="$VAULT_DIR"
    CUR_MODE=$(cat "$MODE_FILE" 2>/dev/null || echo "$ETK_BUILD_TYPE")
    
    # 2. CONSUME THERMAL STATE (Produced by thermal_d.sh)
    T_STAT=$(cat "$SHM_DIR/thermal_stat" 2>/dev/null || echo "WAIT")

    # 3. PERFORMANCE METRICS (BusyBox Safe)
    LOAD_RAW=$(cat /proc/loadavg | awk '{print $1}')
    LOAD_INT=$(echo "$LOAD_RAW" | awk '{print int($1 * 100)}')
    
    L_STAT="OK"
    if [ "$LOAD_INT" -gt 860 ]; then 
        L_STAT="REDLINE"
    elif [ "$LOAD_INT" -gt 590 ]; then 
        L_STAT="PEAK"
    fi
    
    # RAM Calculation and Thresholds
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')
    R_STAT="OK"
    if [ "$RAM_VAL" -ge 90 ]; then
        R_STAT="CRITICAL"
    elif [ "$RAM_VAL" -ge 75 ]; then
        R_STAT="PEAK"
    fi

    # 4. ADVANCED SHADER TELEMETRY
    BANK=$(cat "$VAULT_COUNT" 2>/dev/null || echo "0")
    NEW_SHADERS=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo "0")
    # Calculate physical Vault size in MB (BusyBox Safe per Manifest)
	V_SIZE=$(du -sm "$V_DIR" 2>/dev/null | awk '{print $1}')
    
    if [ -z "$V_SIZE" ]; then
        VAULT_STR="VAULT: ERROR"
    else
        VAULT_STR="VAULT: ${V_SIZE} MB BANK: ${BANK} NEW: ${NEW_SHADERS}"
    fi

    # 5. ATOMIC HUD INJECTION [MANIFEST RULE: HUD FORMAT]
    # Build the exact string requested in AI_MANIFEST.md
    FINAL_STRING="ETK:[${CUR_MODE}][${TARGET_ID}] | TEMP: ${T_STAT} | LOAD: ${LOAD_RAW} ${L_STAT} | RAM: ${RAM_VAL}% ${R_STAT} | ${VAULT_STR}"
    
    # Write to temp file then move to prevent MangoHud from reading an incomplete file
    echo "$FINAL_STRING" > "${LIVE_STAT}.tmp"
    mv "${LIVE_STAT}.tmp" "$LIVE_STAT"

    sleep 1
done