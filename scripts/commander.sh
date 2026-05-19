#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: COMMANDER (v13.5.9 - SHARED RECOVERY)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. CHARACTER RESET: MUST echo -ne "\033(B" in every UI loop.
# 2. ANTI-DRIFT: Use \033[K (Erase to Line End) to prevent UI ghosting.
# 3. SPLIT-PANE: Top is Live Telemetry, Bottom is Raw Forensic Datalog.
# 4. GHOST HUNT: Probe and Analysis scrapes are DISABLED to stop binary floods.
# 5. RECOVERY: Nuclear recovery is NOT defined here. It lives in
#    $ETK_ROOT/bin/recovery.sh (single source of truth, shared
#    with input_d.py R3 panic button). Do NOT re-inline it.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

# Terminal Cleanup
stty -echoctl
echo -ne "\033[2J" 

# Shared recovery: single source of truth in bin/recovery.sh
nuclear_recovery() {
    bash "$ETK_ROOT/bin/recovery.sh"
    sleep 2
}
run_diagnostics() {
    # --- PROBE DISABLED: GHOST HUNTING MODE ---
    # To restore: Uncomment the lines below.
    # echo -ne "\n\033[33m[*] COMPILING DATALOG... \033[0m"
    # bash "$PROBE_SCRIPT"
    # echo -e "\033[32mDONE\033[0m"
    # echo -e "\033[36mANALYSIS:\033[0m $(cat $LAST_ANALYSIS 2>/dev/null | tr -cd '[:print:]' || echo 'Nominal')"
    
    echo -e "\n\033[31m[!] DIAGNOSTICS OFFLINE: Binary flood protection active.\033[0m"
    sleep 2
}

while true; do
    # 1. REFRESH TELEMETRY (BusyBox Optimized)
    T_RAW=$(cat /sys/class/thermal/thermal_zone14/temp 2>/dev/null || echo "0")
    TEMP=$((T_RAW / 1000))
    LOAD=$(cat /proc/loadavg | cut -d' ' -f1)
    V_COUNT=$(cat "$VAULT_COUNT" 2>/dev/null || echo "0")
    CUR_ID=$(cat "$ID_FILE" 2>/dev/null || echo "IDLE")

	# --- ANALYSIS SCRAPE SEVERED FOR TERMINAL STABILITY ---
	ANALYSIS_SNIP="FORENSICS ROUTED TO /storage/etk_crash_report.log"    
    
    # 2. RENDER PIT WALL (TOP PANE)
    echo -ne "\033[H"      # Reset cursor to top
    echo -ne "\033(B"      # Mandatory Character Set Reset
    
    echo -e "\033[36m==================================================\033[K"
    echo -e " \033[1mETK PIT WALL v13.5\033[0m          ID: \033[33m$CUR_ID\033[0m\033[K"
    echo -e "\033[36m==================================================\033[K"
    echo -e " CORE TEMP : ${TEMP}°C      | LOAD: ${LOAD}\033[K"
    echo -e " VAULT     : ${V_COUNT} items  | MODE: ${ETK_BUILD_TYPE}\033[K"
    echo -e " ANALYSIS  : ${ANALYSIS_SNIP}\033[K"
    echo -e "\033[36m--------------------------------------------------\033[K"
    echo -e " [S] SHIFT | [R] RECOVERY | [D] DATALOG | [Q] QUIT\033[K"
    echo -e "\033[36m==================================================\033[K"

# 3. COMMAND HANDLING
    
    # A. Process external IPC commands (Sent by input_d.py via gamepad)
    if [ -f "$CMD_QUEUE" ] && [ -s "$CMD_QUEUE" ]; then
        # Read the first command
        read -r EXT_CMD < "$CMD_QUEUE"
        # Clear the queue safely for BusyBox
        > "$CMD_QUEUE"
        
        case "$EXT_CMD" in
            "RECOVERY") 
                nuclear_recovery 
                ;;
            "VAULT") 
                # Placeholder for D-Pad right action
                ;;
        esac
    fi

    # B. Process local keyboard input (1-second timeout allows the UI to refresh)
    read -t 1 -n 1 -s KEY
    case "$KEY" in
        q|Q)
            echo -e "\n\033[32m[+] EXITING PIT WALL. TERMINAL STATE RESTORED.\033[0m"
            stty echoctl  # Restore the terminal setting altered at script start
            echo -ne "\033(B" # Final character set reset
            exit 0
            ;;
        r|R)
            nuclear_recovery
            ;;
        d|D)
            run_diagnostics
            ;;
        s|S)
            # Toggle Thermal Intent Mode
            CUR_MODE=$(cat "$MODE_FILE" 2>/dev/null || echo "PIT")
            if [ "$CUR_MODE" == "PIT" ]; then
                echo "RACE" > "$MODE_FILE"
            else
                echo "PIT" > "$MODE_FILE"
            fi
            ;;
    esac
done