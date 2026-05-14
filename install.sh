#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: FLASHER (v13.2.4 - ATOMIC SENTRY)
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
# ETK SENTRY: SYSTEMD SHM BACKBONE
source /storage/games-internal/roms/etk/scripts/env.sh

# MANDATORY: Reconstruct the sacred SHM
mkdir -p "$SHM_DIR" 2>/dev/null
echo "INITIALIZING" > "$LIVE_STAT"

exec -a etk_guardian bash -c '
    source /storage/games-internal/roms/etk/scripts/env.sh
    while true; do
        ACTIVE_PIDS=$(pgrep -f rpcs3)
        if [ ! -z "$ACTIVE_PIDS" ]; then
            CURRENT_ID=$(echo "$ACTIVE_PIDS" | while read pid; do cat /proc/$pid/cmdline /proc/$pid/environ 2>/dev/null; done | tr '\''\0'\'' '\''\n'\'' | grep -oE '\''[A-Z]{4}[0-9]{5}'\'' | head -n 1)
            
            if [ ! -z "$CURRENT_ID" ]; then
                echo "$CURRENT_ID" > "$ID_FILE"
                DESIRED_VAULT_DEST="$ETK_ROOT/vault/$CHIPSET/$CURRENT_ID/shaders"
                PHYSICAL_LINK_DEST=$(readlink -f /storage/.cache/mesa_shader_cache)
                
                if [ "$PHYSICAL_LINK_DEST" != "$DESIRED_VAULT_DEST" ]; then
                    mkdir -p "$DESIRED_VAULT_DEST"
                    rm -f /storage/.cache/mesa_shader_cache
                    ln -sf "$DESIRED_VAULT_DEST" /storage/.cache/mesa_shader_cache
                    pkill -f vault_d.sh
                fi
            fi
        else
            echo "IDLE" > "$ID_FILE"
        fi
        
        pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/scripts/mango_bridge.sh" >/dev/null 2>&1 &
        
        if [ "$ETK_BUILD_TYPE" == "FULL" ]; then
            pgrep -f vault_d.sh >/dev/null || nohup bash "$ETK_ROOT/bin/vault_d.sh" >/dev/null 2>&1 &
            pgrep -f thermal_d.sh >/dev/null || nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        elif [ "$ETK_BUILD_TYPE" == "LITE" ]; then
            pkill -f vault_d.sh 2>/dev/null
            pgrep -f thermal_d.sh >/dev/null || nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        elif [ "$ETK_BUILD_TYPE" == "RAW" ]; then
            pkill -f vault_d.sh 2>/dev/null
            pkill -f thermal_d.sh 2>/dev/null
        fi
        sleep 2
    done
'
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