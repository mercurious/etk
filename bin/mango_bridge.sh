#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: HUD BRIDGE (v13.2.0 - INSTRUMENT LOCKDOWN)
# ==========================================================
# AI IMMUTABLE RULE:
# 1. HUD STRING FORMAT IS LOCKED (three-stage time-gated header):
#    Stage 1 — launch window show ETK MODE, GAME ID, VAULT  (0 <= AGE < HUD_HEADER_HOLD_S):
#      MODE|ID|XX°CSTAT|SHADERS XXMB X.Xk XX+
#    Stage 2 — instrument labels  (HUD_HEADER_HOLD_S <= AGE < 2*HUD_HEADER_HOLD_S):
#      TEMP: XX°CSTAT|CORES: X.XXSTAT|MEM: XX%STAT|SHDRS: XXMB X.Xk XX+
#    Stage 3 — pure telemetry  (AGE >= 2*HUD_HEADER_HOLD_S):
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
    
    L_STAT="»--"
    if [ "$LOAD_INT" -gt 860 ]; then 
        L_STAT="»»»"
    elif [ "$LOAD_INT" -gt 590 ]; then 
        L_STAT="»»-"
    fi
    
    # RAM Calculation and Thresholds
    RAM_VAL=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')
    R_STAT="»--"
    if [ "$RAM_VAL" -ge 90 ]; then
        R_STAT="»»»"
    elif [ "$RAM_VAL" -ge 75 ]; then
        R_STAT="»»-"
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
        VAULT_STR="${NEW_SHADERS}+ ${BANK_STR} ${V_SIZE}MB"
    fi

    # --- TIME-GATED LAUNCH HEADER (three stages) ---
    # Stage 1 (0..HOLD):     MODE|GAMEID| + telemetry body
    # Stage 2 (HOLD..2HOLD): labeled telemetry body (TEMP:/CORES:/MEM:/SHDRS:)
    # Stage 3 (2HOLD..):     pure telemetry body
    # Clock is the ignition-seeded session_start.txt (install.sh) — no second
    # timer. A missing/non-numeric clock collapses to stage 3.
    STAGE=3
    S_START=$(cat "$SHM_DIR/session_start.txt" 2>/dev/null)
    case "$S_START" in ''|*[!0-9]*) S_START=0 ;; esac
    if [ "$S_START" -gt 0 ] && [ "$TARGET_ID" != "IDLE" ]; then
        AGE=$(( $(date +%s) - S_START ))
        HOLD="${HUD_HEADER_HOLD_S:-60}"
        if [ "$AGE" -ge 0 ] && [ "$AGE" -lt "$HOLD" ]; then
            STAGE=1
        elif [ "$AGE" -ge "$HOLD" ] && [ "$AGE" -lt $(( HOLD * 2 )) ]; then
            STAGE=2
        fi
    fi

    # 5. ATOMIC HUD INJECTION [MANIFEST RULE: HUD FORMAT]
    case "$STAGE" in
        1) FINAL_STRING="${ETK_BUILD_TYPE}|${TARGET_ID}|SHDRS ${VAULT_STR}" ;;
        2) FINAL_STRING="TEMP ${T_STAT}|LOAD ${LOAD_RAW}${L_STAT}|RAM ${RAM_VAL}%${R_STAT}|" ;;
        *) FINAL_STRING="${T_STAT}|${LOAD_RAW}${L_STAT}|${RAM_VAL}%${R_STAT}|${VAULT_STR}" ;;
    esac
    
    # Write to temp file then move to prevent MangoHud from reading an incomplete file
    echo "$FINAL_STRING" > "${LIVE_STAT}.tmp"
    mv "${LIVE_STAT}.tmp" "$LIVE_STAT"

    sleep 1
done