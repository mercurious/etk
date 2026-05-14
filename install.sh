#!/bin/bash
# ==========================================================
# ETK PHASE 9: Game Agnostic ETK v9.4.1 (OMNI-SNIFF)
# ==========================================================
# MERGE LOG: 
# - Deployed Omni-Sniffing: Scans all RPCS3 PIDs simultaneously
# - Bypasses Dwarfs/AppImage FUSE mount encapsulation
# - Restored Donor Birth for newly discovered titles
# ==========================================================
source ./scripts/env.sh

# --- STEP 0: THE REMOTE PROBE ---
echo -e "${C}>>> [0/6] PROBING REMOTE RIG STATE...${N}"
RIG_ID=$(ssh $RIG_SSH "cat $ID_FILE 2>/dev/null || pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1")
export TARGET_ID="${RIG_ID:-NPUA80075}"
export VAULT_DIR="$ETK_ROOT/vault/$CHIPSET/$TARGET_ID/shaders"

# --- STEP 1: INITIALIZE RIG DIRECTORIES ---
echo -e "${C}>>> [1/6] INITIALIZING RIG DIRECTORIES...${N}"
ssh $RIG_SSH "mkdir -p $VAULT_DIR $ETK_ROOT/bin $ETK_ROOT/scripts $ETK_ROOT/vault $ETK_ROOT/shm /storage/.config/custom_scripts /storage/.config/rpcs3/custom_configs /storage/games-internal/roms/bios/rpcs3/custom_configs"

# =========================================================
# [CRITICAL: NON-NEGOTIABLE SYNC ARCHITECTURE]
# AI INSTRUCTION: DO NOT MERGE OR REMOVE THESE RSYNC STEPS.
# Context: The Vault must be synchronized bidirectionally. 
# Step 2.1 Harvests new shaders from Handheld -> Mac.
# Step 2.2 Replenishes master archive from Mac -> Handheld.
# Step 3 must ALWAYS exclude 'vault/' to prevent recursive loop overwrites.
# =========================================================

# --- STEP 2: THE HARVEST & REPLENISH (Bidirectional Vault Sync) ---
echo -e "${Y}>>> [2/6] SYNCHRONIZING SHADER VAULTS...${N}"
mkdir -p "./vault/$CHIPSET/$TARGET_ID/shaders"

# 2.1 HARVEST: Pull new shaders generated on the Rig -> Mac
echo "    -> Harvesting new shaders from Rig..."
rsync -av --update $RIG_SSH:$VAULT_DIR/ "./vault/$CHIPSET/$TARGET_ID/shaders/"

# 2.2 REPLENISH: Push Mac's master archive -> Rig (Handles fresh installs)
echo "    -> Replenishing rig from Mac Master Archive..."
# Note: We sync the entire CHIPSET folder to ensure all agnostic games are pushed
rsync -av --update "./vault/$CHIPSET/" "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/"

# --- STEP 3: PROJECT SYNC (Mac -> Rig) ---
echo -e "${C}>>> [3/6] SYNCING PROJECT LOGIC TO $RIG_IP...${N}"
rsync -av --inplace \
    --exclude='.DS_Store' --exclude='.git*' --exclude='_trash/' \
    --exclude='vault/' --exclude='vault_backup/' \
    ./ $RIG_SSH:$ETK_ROOT/
# =========================================================# --- STEP 4: CONFIG WAREHOUSE PROVISIONING ---
echo -e "${C}>>> [4/6] PROVISIONING NATIVE CONFIGS...${N}"
rsync -av ./config/*.yml $RIG_SSH:/storage/.config/rpcs3/custom_configs/
rsync -av ./config/*.yml $RIG_SSH:/storage/games-internal/roms/bios/rpcs3/custom_configs/
scp ./config/MangoHud.conf $RIG_SSH:$RIG_MANGO_CONF

# --- STEP 5: DEPLOY BOOT LOADER (THE SENTRY) ---
echo -e "${C}>>> [5/6] REPAIRING ETK SENTRY DAEMON...${N}"
ssh $RIG_SSH << 'EOF'
    cat << 'INNER_EOF' > /storage/.config/custom_scripts/01-etk-startup.sh
#!/bin/bash
# ETK BOOT SENTRY (v14.0.2 - DAEMON RECOVERY)
source /storage/games-internal/roms/etk/scripts/env.sh

# 1. FORCE INITIALIZE SHM
mkdir -p "$SHM_DIR" 2>/dev/null
chmod 777 "$SHM_DIR" 2>/dev/null
echo "INITIALIZING" > "$LIVE_STAT" # Create file so MangoHud doesn't crash

# 2. AGNOSTIC SNIFFER (DEEP SCAN)
    ACTIVE_PIDS=$(pgrep -f rpcs3)
    
    if [ ! -z "$ACTIVE_PIDS" ]; then
        # Scan all PIDs to find the one containing the Game ID
        CURRENT_ID=$(echo "$ACTIVE_PIDS" | while read pid; do cat /proc/$pid/cmdline /proc/$pid/environ 2>/dev/null; done | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        
        if [ ! -z "$CURRENT_ID" ]; then
             echo "$CURRENT_ID" > "$ID_FILE"
        fi
    else
        echo "IDLE" > "$ID_FILE"
    fi
    
    # 3. DAEMON WATCHDOG (FIXED PATHS)
    # Using absolute paths to ETK_ROOT/bin to ensure they launch regardless of PWD
    pgrep -f thermal_d.sh >/dev/null || nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
    pgrep -f vault_d.sh >/dev/null || nohup bash "$ETK_ROOT/bin/vault_d.sh" >/dev/null 2>&1 &
    
    if [ "$ETK_STEALTH" == "0" ]; then
        pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/scripts/mango_bridge.sh" >/dev/null 2>&1 &
    fi

    sleep 10
done
INNER_EOF
    chmod +x /storage/.config/custom_scripts/01-etk-startup.sh
    
    # KILL EXISTING STUCK SENTRY TO FORCE RESTART
    pkill -9 -f 01-etk-startup.sh 2>/dev/null
    nohup bash /storage/.config/custom_scripts/01-etk-startup.sh >/dev/null 2>&1 &
EOF