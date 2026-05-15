#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: FLASHER (v13.2.5 - ATOMIC SENTRY)
# ==========================================================
# MERGE LOG: 
# - MANIFEST COMPLIANCE: Atomic Symlink Swap added to Sentry.
# - MANIFEST COMPLIANCE: Tier-Aware Watchdog perfectly aligned.
# - FIXED: vault_d.sh correctly suppressed in LITE/RAW modes.
# ==========================================================
source ./scripts/env.sh

# --- STEP 0: THE REMOTE PROBE ---
echo -e "\033[36m>>> [0/6] PROBING REMOTE RIG STATE...\033[0m"
RIG_ID=$(ssh $RIG_SSH "cat $ID_FILE 2>/dev/null || pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1")
export TARGET_ID="${RIG_ID:-NPUA80075}"
export VAULT_DIR="$ETK_ROOT/vault/$CHIPSET/$TARGET_ID/shaders"

# --- STEP 1: INITIALIZE RIG DIRECTORIES ---
echo -e "\033[36m>>> [1/6] INITIALIZING RIG DIRECTORIES...\033[0m"
ssh $RIG_SSH "mkdir -p $VAULT_DIR $ETK_ROOT/bin $ETK_ROOT/scripts $ETK_ROOT/vault $ETK_ROOT/shm /storage/.config/custom_scripts /storage/.config/rpcs3/custom_configs /storage/games-internal/roms/etk/logs"

# --- STEP 2: SYNC SCRIPTS & DAEMONS ---
echo -e "\033[36m>>> [2/6] DEPLOYING GUARDIAN DAEMONS...\033[0m"
rsync -avz --exclude='*.pyc' --exclude='__pycache__' ./bin/ $RIG_SSH:$ETK_ROOT/bin/
rsync -avz ./scripts/ $RIG_SSH:$ETK_ROOT/scripts/
ssh $RIG_SSH "chmod +x $ETK_ROOT/bin/* $ETK_ROOT/scripts/*"

# --- STEP 3: MANGOHUD INJECTION ---
echo -e "\033[36m>>> [3/6] INJECTING MANGOHUD OVERLAY...\033[0m"
rsync -avz ./config/MangoHud.conf $RIG_SSH:/storage/.config/MangoHud/MangoHud.conf

# --- STEP 4: WAKING THE SENTRY (ROCKNIX SYSTEMD) ---
echo -e "\033[36m>>> [4/6] DEPLOYING ROCKNIX-NATIVE SYSTEMD SENTRY...\033[0m"

ssh $RIG_SSH << 'EOF'
    BOOT_SENTRY="/storage/.config/custom_scripts/01-etk-sentry.sh"
    mkdir -p /storage/.config/custom_scripts/

    # 1. Write the Sentry logic
cat << 'SENTRY' > "$BOOT_SENTRY"
#!/bin/bash
# ==========================================================
# ETK PHASE 13.6: EVENT-DRIVEN SENTRY
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh
PREV_STATE="IDLE"

while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh
    
    # 1. THE TRAP (Is RPCS3 Running?)
    if pgrep -f "rpcs3|AppRun.wrapped" > /dev/null; then
        CUR_STATE="RUNNING"
    else
        CUR_STATE="IDLE"
    fi

    # 2. THE BRIDGE (Always keep MangoHud bridge alive)
    pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/scripts/mango_bridge.sh" >/dev/null 2>&1 &

    # 3. STATE TRANSITIONS
    if [ "$CUR_STATE" == "RUNNING" ]; then
        # If we just transitioned from IDLE to RUNNING...
        if [ "$PREV_STATE" == "IDLE" ]; then
            sleep 3 # Let emulator establish game ID in PARAM.SFO
            if [ "$ETK_BUILD_TYPE" == "FULL" ]; then
                nohup bash "$ETK_ROOT/bin/vault_d.sh" >/dev/null 2>&1 &
                nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
            elif [ "$ETK_BUILD_TYPE" == "LITE" ]; then
                nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
            fi
        fi
        
        # Continuous ID Resolution while running
        ID_STR=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        if [ ! -z "$ID_STR" ]; then
            echo "$ID_STR" > "$ID_FILE"
        fi

    elif [ "$CUR_STATE" == "IDLE" ]; then
        # If we just transitioned from RUNNING to IDLE (User quit or Game crashed)...
        if [ "$PREV_STATE" == "RUNNING" ]; then
            echo "IDLE" > "$ID_FILE"
            pkill -f vault_d.sh 2>/dev/null
            pkill -f thermal_d.sh 2>/dev/null
            
            # Reset the Bridge stats so HUD doesn't freeze on last known values
            echo "ETK:[IDLE] | WAITING FOR IGNITION" > "$LIVE_STAT".tmp
            mv "$LIVE_STAT".tmp "$LIVE_STAT"
        fi
    fi

    PREV_STATE="$CUR_STATE"
    sleep 2
done
SENTRY

    chmod +x "$BOOT_SENTRY"

# 2. Deploy to the ROCKNIX Persistent Systemd path (Note the dot in system.d)
    mkdir -p /storage/.config/system.d/
    
    cat << 'SVC' > /storage/.config/system.d/etk.service
[Unit]
Description=ETK Guardian Sentry
After=multi-user.target

[Service]
Type=simple
ExecStart=/bin/bash /storage/.config/custom_scripts/01-etk-sentry.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SVC

    # 3. Reload, Enable, and Start using the absolute path
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk.service
    systemctl restart etk.service
EOF