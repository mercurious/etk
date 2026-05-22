#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: HUD BRIDGE (v13.2.0 - INSTRUMENT LOCKDOWN)
# ==========================================================
# AI IMMUTABLE RULE:
# 1. HUD STRING FORMAT IS LOCKED (time-gated launch header):
#    First HUD_HEADER_HOLD_S of a session (launch window):
#      MODE|ID|XX°CSTAT|X.XXSTAT|XX%STAT|XXMB X.Xk XX+
#    After the launch window it collapses to pure telemetry:
#      XX°CSTAT|X.XXSTAT|XX%STAT|XXMB X.Xk XX+
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
    
    # 2. CONSUME THERMAL STATE (Produced by thermal_d.sh)
    T_STAT=$(cat "$SHM_DIR/thermal_stat" 2>/dev/null || echo "WAIT")

    # 3. PERFORMANCE METRICS (BusyBox Safe)
    LOAD_RAW=$(cat /proc/loadavg | awk '{print $1}')
    LOAD_INT=$(echo "$LOAD_RAW" | awk '{print int($1 * 100)}')
    
    L_STAT="»"
    if [ "$LOAD_INT" -gt 860 ]; then 
        L_STAT="»»»»PEAK"
    elif [ "$LOAD_INT" -gt 590 ]; then 
        L_STAT="»»»"
    fi
    
    # RAM Calculation and Thresholds
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')
    R_STAT="»"
    if [ "$RAM_VAL" -ge 90 ]; then
        R_STAT="»»»»MAX"
    elif [ "$RAM_VAL" -ge 75 ]; then
        R_STAT="»»»"
    fi

    # 4. ADVANCED SHADER TELEMETRY
    BANK=$(cat "$VAULT_COUNT" 2>/dev/null || echo "0")
    NEW_SHADERS=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo "0")

    # Shader-count abbreviation to save HUD width (integer math, BusyBox-safe):
    #   < 1000          -> plain count          (e.g. 50)
    #   1000 .. 99999   -> X.Xk, truncated      (e.g. 2549 -> 2.5k)
    #   >= 100000       -> XXXk, truncated      (e.g. 399812 -> 399k)
    if [ "$BANK" -ge 100000 ] 2>/dev/null; then
        BANK_STR="$((BANK / 1000))k"
    elif [ "$BANK" -ge 1000 ] 2>/dev/null; then
        BANK_STR="$((BANK / 1000)).$(( (BANK % 1000) / 100 ))k"
    else
        BANK_STR="$BANK"
    fi
# ==================================================================
# PATCHED VIA PIT WALL: BUSYBOX-SAFE SYMLINK FOOTPRINT CALCULATION
# ==================================================================
# 1. Use -k (Kilobytes) which is universally supported by BusyBox
# 2. Use -L to explicitly FORCE du to follow the vault symlinks
	V_SIZE_KB=$(du -skL "$V_DIR" 2>/dev/null | awk '{print $1}')    
    if [ -z "$V_SIZE_KB" ] || [ "$V_SIZE_KB" -eq 0 ]; then
        VAULT_STR="VAULT:ERROR"
    else
    	# Convert Kilobytes to Megabytes safely using integer arithmetic
    	V_SIZE=$((V_SIZE_KB / 1024))
        VAULT_STR="${V_SIZE}MB ${BANK_STR} ${NEW_SHADERS}+"
    fi

    # --- TIME-GATED LAUNCH HEADER ---
    # MODE|GAMEID| only for the first $HUD_HEADER_HOLD_S of a session, then "".
    # Clock is the ignition-seeded session_start.txt (install.sh) — no second
    # timer. A missing/non-numeric clock collapses to the compact strip.
    HEADER=""
    S_START=$(cat "$SHM_DIR/session_start.txt" 2>/dev/null)
    case "$S_START" in ''|*[!0-9]*) S_START=0 ;; esac
    if [ "$S_START" -gt 0 ]; then
        AGE=$(( $(date +%s) - S_START ))
        if [ "$AGE" -ge 0 ] && [ "$AGE" -lt "${HUD_HEADER_HOLD_S:-60}" ] \
           && [ "$TARGET_ID" != "IDLE" ]; then
            HEADER="${ETK_BUILD_TYPE}|${TARGET_ID}|"
        fi
    fi

    # 5. ATOMIC HUD INJECTION [MANIFEST RULE: HUD FORMAT]
    # Time-gated header (above) + telemetry body.
    FINAL_STRING="${HEADER}${T_STAT}|${LOAD_RAW}${L_STAT}|${RAM_VAL}%${R_STAT}|${VAULT_STR}"
    
    # Write to temp file then move to prevent MangoHud from reading an incomplete file
    echo "$FINAL_STRING" > "${LIVE_STAT}.tmp"
    mv "${LIVE_STAT}.tmp" "$LIVE_STAT"

    sleep 1
done