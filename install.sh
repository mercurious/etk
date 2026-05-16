#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: FLASHER (v13.2.6 - ATOMIC SENTRY / RACE-PROOF)
# ==========================================================
# GEMINI IMMUTABLE RULE: Tethered synchronization must maintain a two-stage 
# pipeline: PULL from rig immediately after initialization, PUSH to rig 
# post-configuration deployment. Sentry must zero volatile state file on ignition.
# To prevent path-shifting race conditions, the Sentry must completely resolve
# and write the definitive Game ID to ID_FILE BEFORE spawning worker daemons.
# All rsync transfers must include progress reporting and .DS_Store exclusion.
# ==========================================================
source ./scripts/env.sh

# --- STEP 0: THE REMOTE PROBE ---
echo -e "\033[36m>>> [0/6] PROBING REMOTE RIG STATE...\033[0m"
# Hard cleanup: Flush any lingering worker daemons from previous sessions to ensure clean deployment
ssh $RIG_SSH "pkill -f vault_d.sh; pkill -f thermal_d.sh; pkill -f mango_bridge.sh" 2>/dev/null

RIG_ID=$(ssh $RIG_SSH "cat $ID_FILE 2>/dev/null || pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1")
export TARGET_ID="${RIG_ID:-NPUA80075}"
export VAULT_DIR="$ETK_ROOT/vault/$CHIPSET/$TARGET_ID/shaders"

# --- STEP 1: INITIALIZE RIG DIRECTORIES ---
echo -e "\033[36m>>> [1/6] INITIALIZING RIG DIRECTORIES...\033[0m"
ssh $RIG_SSH "mkdir -p $VAULT_DIR $ETK_ROOT/bin $ETK_ROOT/scripts $ETK_ROOT/vault $ETK_ROOT/shm /storage/.config/custom_scripts /storage/.config/rpcs3/custom_configs /storage/games-internal/roms/etk/logs"

# --- STEP 1.5: SHADER VAULT PULL (SAFEGUARD HARVEST) ---
echo -e "\033[36m>>> [1.5/6] SAFEGUARDING HARVEST (PULLING SHADERS TO HOST PC)...\033[0m"
mkdir -p ./vault
rsync -az --progress --update --exclude='.DS_Store' "$RIG_SSH:$ETK_ROOT/vault/" ./vault/

# --- STEP 2: SYNC SCRIPTS & DAEMONS ---
echo -e "\033[36m>>> [2/6] DEPLOYING GUARDIAN DAEMONS...\033[0m"
rsync -az --progress --exclude='*.pyc' --exclude='__pycache__' --exclude='.DS_Store' ./bin/ $RIG_SSH:$ETK_ROOT/bin/
rsync -az --progress --exclude='.DS_Store' ./scripts/ $RIG_SSH:$ETK_ROOT/scripts/
ssh $RIG_SSH "chmod +x $ETK_ROOT/bin/* $ETK_ROOT/scripts/*"

# --- STEP 3: MANGOHUD INJECTION ---
echo -e "\033[36m>>> [3/6] INJECTING MANGOHUD OVERLAY...\033[0m"
rsync -az --progress --exclude='.DS_Store' ./config/MangoHud.conf $RIG_SSH:/storage/.config/MangoHud/MangoHud.conf

# --- STEP 3.5: SHADER VAULT PUSH (SEED MASTER VAULT) ---
echo -e "\033[36m>>> [3.5/6] SEEDING RIG WITH MASTER PC SHADER VAULT...\033[0m"
rsync -az --progress --exclude='.DS_Store' ./vault/ $RIG_SSH:$ETK_ROOT/vault/

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
# GEMINI IMMUTABLE RULE: Sentry handles top-level state transitions.
# It MUST atomically zero out vault_new.txt at the exact moment of
# emulator ignition to ensure downstream daemons register accurate counts.
# To eliminate race conditions, the definitive Game ID must be resolved
# and committed to ID_FILE before launching downstream worker daemons.
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
            sleep 4 # Let emulator establish game ID in PARAM.SFO and clear runway
            
            # RESOLVE IDENTITY PROFILE FIRST: Lock identity string before spawning workers
            ID_STR=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
            echo "${ID_STR:-NPUA80075}" > "$ID_FILE"
            
            # ATOMIC TELEMETRY RESET: Wipe stale session counters before spawning workers
            echo "0" > "/dev/shm/etk_shm/vault_new.txt"
            
            if [ "$ETK_BUILD_TYPE" == "FULL" ]; then
                nohup bash "$ETK_ROOT/bin/vault_d.sh" >/dev/null 2>&1 &
                nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
            elif [ "$ETK_BUILD_TYPE" == "LITE" ]; then
                nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
            fi
        fi
        
        # Continuous ID Resolution while running to maintain lock stability
        ID_STR=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        if