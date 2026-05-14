#!/bin/bash
# ==========================================================
# ETK PHASE 13: COMMANDER (v13.1.0 - TIERED AUDIT)
# ==========================================================
# MERGE LOG: 
# - UI Auto-Scaling based on ETK_BUILD_TYPE
# - Disables Vault [V] key in LITE/RAW modes
# - Preserved Nuclear Recovery and Forensic Datalog
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh
stty -echoctl
echo -ne "\033[2J" 

M_FILE="$MODE_FILE"
Q_FILE="$CMD_QUEUE"

# Start the input daemon ONLY in FULL mode
if [ "$ETK_BUILD_TYPE" == "FULL" ]; then
    pkill -f input_d.py 2>/dev/null
    python3 "$ETK_ROOT/bin/input_d.py" > /dev/null 2>&1 &
fi

run_diagnostics() {
    echo -ne "\n\033[33m[*] COMPILING DATALOG... \033[0m"
    bash "$PROBE_SCRIPT"
    echo -e "\033[32mDONE\033[0m"
    echo -e "\033[36mANALYSIS:\033[0m $(cat $LAST_ANALYSIS 2>/dev/null || echo "Nominal")"
    sleep 2
}

nuclear_recovery() {
    echo -e "\n\033[31m[!] CRASH DETECTED: CAPTURING TELEMETRY [!]\033[0m"
    run_diagnostics 
    pkill -9 -f rpcs3 2>/dev/null
    echo "schedutil" > /sys/devices/system/cpu/cpufreq/policy4/scaling_governor 2>/dev/null
    echo "schedutil" > /sys/devices/system/cpu/cpufreq/policy7/scaling_governor 2>/dev/null
    sleep 1
}

while true; do
    # 1. DATA AGGREGATION
    read -r T_RAW < /sys/class/thermal/thermal_zone14/temp 2>/dev/null || T_RAW="0"
    TEMP=$((T_RAW / 1000))
    CUR_MODE=$(cat "$M_FILE" 2>/dev/null || echo "UNK")
    V_SIZE=$(cat "$SHM_DIR/vault_size.txt" 2>/dev/null || echo "0")
    RAM_FREE=$(free -m | awk '/Mem:/ {print $4}')
    ANALYSIS_SNIP=$(cat "$LAST_ANALYSIS" 2>/dev/null | cut -c 1-30 || echo "Waiting...")

    echo -ne "\033[H"
    
    # 2. TIERED DDU RENDERING
    echo "=================================================="
    echo " ETK PROJECT v13.1.0 | MODE: $ETK_BUILD_TYPE | STATUS: $CUR_MODE"
    echo "=================================================="
    
    if [ "$ETK_BUILD_TYPE" == "RAW" ]; then
        echo " TELEMETRY : ${TEMP}°C      | RAM FREE: ${RAM_FREE}MB"
        echo "--------------------------------------------------"
        echo " [D] DATALOG | [Q] QUIT"
    elif [ "$ETK_BUILD_TYPE" == "LITE" ]; then
        echo " TELEMETRY : ${TEMP}°C      | RAM FREE: ${RAM_FREE}MB"
        echo " ANALYTICS : ${ANALYSIS_SNIP}..."
        echo "--------------------------------------------------"
        echo " [S] SHIFT | [R] RECOVERY | [D] DATALOG | [Q] QUIT"
    else
        # FULL SUITE
        echo " TELEMETRY : ${TEMP}°C      | RAM FREE: ${RAM_FREE}MB"
        echo " VAULT     : ${V_SIZE} MB    | ANALYTICS : ${ANALYSIS_SNIP}..."
        echo "--------------------------------------------------"
        echo " [S] SHIFT | [V] VAULT | [R] RECOVERY | [D] DATALOG | [Q] QUIT"
    fi
    echo "=================================================="

    # 3. COMMAND HANDLING
    if [ -s "$Q_FILE" ]; then
        while IFS= read -r QUEUED_CMD; do
            case "$QUEUED_CMD" in
                "RECOVERY") nuclear_recovery ;;
                "PIT")      echo -n "PIT" > "$M_FILE" ;;
                "RACE")     echo -n "RACE" > "$M_FILE" ;;
                "VAULT")    [ "$ETK_BUILD_TYPE" == "FULL" ] && $ETK_ROOT/bin/vault_d.sh dump & ;;
            esac
        done < "$Q_FILE"
        > "$Q_FILE" 
    fi

    read -t 1 -n 1 -s KEY
    case "$KEY" in
        [sS]) [ "$CUR_MODE" == "PIT" ] && echo -n "RACE" > "$M_FILE" || echo -n "PIT" > "$M_FILE" ;;
        [vV]) [ "$ETK_BUILD_TYPE" == "FULL" ] && $ETK_ROOT/bin/vault_d.sh dump & ;;
        [rR]) nuclear_recovery ;;
        [dD]) run_diagnostics ;;
        [qQ]) echo -e "\nExiting Commander..."; stty echo; exit 0 ;;
    esac
done