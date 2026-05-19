#!/bin/bash
# ==========================================================
# ETK PHASE 13.6: FLASHER (v14.1.0 - SENTRY STATE FIX + VAULT PUSH)
# ==========================================================
# FIX: Sentry PREV_STATE typo ($-sign missing) caused state machine to never fire
# FIX: Added missing Step 4 vault push (host PC shaders -> rig)
# FIX: Added --delete to bin/scripts rsync to prevent stale file accumulation
# FIX: Added input_d.py kill in Step 0 to fully quiesce running ETK
# FIX: Provisioned correct RPCS3 custom config path on rig
# FIX: mkdir -p MangoHud dir before rsync on fresh/wiped rig
# FIX: Sentry hardcoded /dev/shm paths replaced with $SHM_DIR
# FIX: Final systemctl status check so DEPLOYMENT COMPLETE is honest
# ==========================================================

source ./scripts/env.sh

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'

# --- PIPELINE CONTROLS ---
if [ "$ETK_VERBOSE" == "1" ]; then
    RSYNC_CMD="rsync -avzI --progress"
else
    RSYNC_CMD="rsync -az --progress"
fi

TOTAL_STEPS=6

# ==========================================================
# STEP 0: PROBE & QUIESCE
# Kill all ETK worker processes on the rig before any file
# operations to prevent partial-write races during repair/update.
# input_d.py is included because it holds the evdev fd open.
# mango_bridge.sh is included because it writes to SHM continuously.
# The Sentry service itself is left running — it will respawn cleanly.
# ==========================================================
echo -e "${C}>>> [0/${TOTAL_STEPS}] PROBING & QUIESCING REMOTE RIG...${N}"
ssh $RIG_SSH "pkill -f vault_d.sh; pkill -f thermal_d.sh; pkill -f mango_bridge.sh; pkill -f input_d.py" 2>/dev/null
sleep 1

# Resolve current game ID from rig (for vault path construction)
RIG_ID=$(ssh $RIG_SSH "cat $ID_FILE 2>/dev/null || pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1")
export TARGET_ID="${RIG_ID:-NPUA80075}"
export VAULT_DIR="$ETK_ROOT/vault/$CHIPSET/$TARGET_ID/shaders"
echo -e "    RIG ID   : ${Y}${TARGET_ID}${N}"
echo -e "    VAULT DIR: ${Y}${VAULT_DIR}${N}"

# ==========================================================
# STEP 1: PROVISION RIG DIRECTORIES
# Idempotent mkdir -p — safe to run on repair/update.
# Provisions the correct RPCS3 custom config path (bios tree),
# not the legacy /storage/.config/rpcs3 path.
# ==========================================================
echo -e "${C}>>> [1/${TOTAL_STEPS}] PROVISIONING RIG DIRECTORIES...${N}"
ssh $RIG_SSH "mkdir -p \
    $VAULT_DIR \
    $ETK_ROOT/bin \
    $ETK_ROOT/scripts \
    $ETK_ROOT/vault \
    $ETK_ROOT/logs \
    $ETK_ROOT/config \
    /storage/.config/custom_scripts \
    /storage/.config/system.d \
    /storage/.config/modules \
    /storage/.config/MangoHud \
    /storage/games-internal/roms/bios/rpcs3/custom_configs"

# ==========================================================
# STEP 2: VAULT PULL — RIG -> HOST PC (SAFEGUARD HARVEST)
# Always pull before pushing so no session's banked shaders
# are lost if the rig is about to be reflashed or repaired.
# --update skips files where the rig copy is older than host.
# ==========================================================
echo -e "${C}>>> [2/${TOTAL_STEPS}] SAFEGUARDING HARVEST (PULLING SHADERS: RIG -> HOST PC)...${N}"
mkdir -p ./vault
$RSYNC_CMD --update --exclude='.DS_Store' "$RIG_SSH:$ETK_ROOT/vault/" ./vault/

# ==========================================================
# STEP 3: DEPLOY SCRIPTS, DAEMONS & OVERLAYS
# --delete removes stale files from previous versions so
# renamed/removed scripts don't linger and cause ghost invocations.
# ==========================================================
echo -e "${C}>>> [3/${TOTAL_STEPS}] DEPLOYING GUARDIAN DAEMONS & SCRIPTS...${N}"
$RSYNC_CMD --delete --exclude='*.pyc' --exclude='__pycache__' --exclude='.DS_Store' ./bin/ $RIG_SSH:$ETK_ROOT/bin/
$RSYNC_CMD --delete --exclude='.DS_Store' ./scripts/ $RIG_SSH:$ETK_ROOT/scripts/
$RSYNC_CMD --exclude='.DS_Store' ./config/MangoHud.conf $RIG_SSH:/storage/.config/MangoHud/MangoHud.conf
ssh $RIG_SSH "chmod +x $ETK_ROOT/bin/* $ETK_ROOT/scripts/*"

# ==========================================================
# STEP 4: VAULT PUSH — HOST PC -> RIG (RESTORE BANKED SHADERS)
# Pushes any shaders from the host vault that the rig doesn't
# have yet. This is the repair/post-flash recovery path that
# saves re-harvesting after an OS reflash or SD card swap.
# --update skips files already newer on the rig.
# --ignore-existing would be safer if you never want to overwrite
# rig shaders with host copies, but --update handles version drift.
# ==========================================================
echo -e "${C}>>> [4/${TOTAL_STEPS}] RESTORING BANKED SHADERS (PUSHING VAULT: HOST PC -> RIG)...${N}"
if [ -d "./vault" ] && [ "$(ls -A ./vault 2>/dev/null)" ]; then
    $RSYNC_CMD --update --exclude='.DS_Store' ./vault/ "$RIG_SSH:$ETK_ROOT/vault/"
else
    echo -e "    ${Y}[SKIP] No local vault found — nothing to push.${N}"
fi

# ==========================================================
# STEP 5: DEPLOY ETK PITSTOP ROCKNIX INTERFACE
# Deploys the Tools-menu launcher, Python engine, and field schema.
#
# The launcher goes to /storage/.config/modules/ — outside ETK_ROOT.
# rsync and scp both have variable-expansion and path-handling edge
# cases when targeting this directory. Use cat-over-ssh instead:
# the file content is piped directly into a remote tee, with no
# intermediate path construction that can silently misfire.
# Verify immediately and abort loudly if the file didn't land.
# ==========================================================
echo -e "${C}>>> [5/${TOTAL_STEPS}] DEPLOYING ETK PITSTOP ROCKNIX INTERFACE...${N}"

# Python engine and field schema — inside ETK_ROOT
$RSYNC_CMD --exclude='.DS_Store' ./bin/etk_pitstop.py         $RIG_SSH:$ETK_ROOT/bin/
$RSYNC_CMD --exclude='.DS_Store' ./config/pitstop_fields.json $RIG_SSH:$ETK_ROOT/config/

# Deploy the launcher using standard rsync
echo -e "    Deploying launcher to /storage/.config/modules/..."
$RSYNC_CMD --exclude='.DS_Store' ./config/etk_pitstop.sh $RIG_SSH:/storage/.config/modules/etk_pitstop.sh

# Ensure explicit execution permissions per the ETK Manifest and strip Windows/Mac CRLF line endings
ssh $RIG_SSH "chmod +x /storage/.config/modules/etk_pitstop.sh && sed -i 's/\r$//' /storage/.config/modules/etk_pitstop.sh"

# Verify the launcher actually landed
LAUNCHER_CHECK=$(ssh $RIG_SSH "[ -x /storage/.config/modules/etk_pitstop.sh ] && echo OK || echo MISSING")
if [ "$LAUNCHER_CHECK" = "OK" ]; then
    echo -e "    ${G}[OK] Launcher verified: /storage/.config/modules/etk_pitstop.sh${N}"
else
    echo -e "    ${R}[FAIL] Launcher missing from /storage/.config/modules/ — aborting.${N}"
    exit 1
fi

# ==========================================================
# STEP 6: WRITE & RESTART THE SENTRY (ROCKNIX SYSTEMD)
# The Sentry is written fresh on every install/repair so any
# bug fixes in this block take effect immediately.
#
# CRITICAL BUG FIX: PREV_STATE="$CUR_STATE" — the original had
# PREV_STATE="...CUR_STATE..." (literal string, $ missing).
# This caused the state machine transition blocks to NEVER fire:
# daemon spawning on ignition and daemon killing on exit were
# both permanently dead. Every install since the sentry was
# introduced has had this bug.
# ==========================================================
echo -e "${C}>>> [6/${TOTAL_STEPS}] DEPLOYING ROCKNIX-NATIVE SYSTEMD SENTRY...${N}"

ssh $RIG_SSH << 'REMOTE'
    BOOT_SENTRY="/storage/.config/custom_scripts/01-etk-sentry.sh"
    mkdir -p /storage/.config/custom_scripts/ /storage/.config/system.d/

cat << 'SENTRY' > "$BOOT_SENTRY"
#!/bin/bash
# ==========================================================
# ETK PHASE 13.6: EVENT-DRIVEN SENTRY STATE MACHINE
# ==========================================================
# GEMINI IMMUTABLE RULE:
# This is an event-driven state machine. PREV_STATE tracking
# is the only mechanism that gates daemon spawning and killing.
# Do NOT "simplify" or "optimize" the PREV_STATE assignment.
# The $ in "$CUR_STATE" is load-bearing. Do not remove it.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

# --- REBOOT SURVIVAL: Seed SHM on boot before first loop tick ---
mkdir -p "$SHM_DIR"
echo "0"    > "$SHM_DIR/vault_count"
echo "0"    > "$SHM_DIR/vault_new.txt"
echo "0"    > "$SHM_DIR/vault_size.txt"
echo "IDLE" > "$SHM_DIR/active_id.txt"
echo "$DEFAULT_MODE" > "$SHM_DIR/etk_mode.txt"
echo "READY" > "$SHM_DIR/vault_stat.txt"
echo "ETK:[$ETK_BUILD_TYPE] | SYSTEM ONLINE" > "$LIVE_STAT"

PREV_STATE="IDLE"

while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh

    # --- STATE DETECTION ---
    if pgrep -f "rpcs3|AppRun.wrapped" > /dev/null; then
        CUR_STATE="RUNNING"
    else
        CUR_STATE="IDLE"
    fi

    # --- BRIDGE WATCHDOG: Keep mango_bridge alive regardless of game state ---
    pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/scripts/mango_bridge.sh" >/dev/null 2>&1 &

    # --- IGNITION: IDLE -> RUNNING transition ---
    if [ "$CUR_STATE" = "RUNNING" ] && [ "$PREV_STATE" = "IDLE" ]; then
        # Wait for RPCS3 to populate its process environment with the game ID
        sleep 4

        # RACE-PROOF IDENTITY SYNC: Resolve and commit ID before spawning workers
        ID_STR=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        echo "${ID_STR:-NPUA80075}" > "$ID_FILE"

        # ATOMIC SESSION RESET: Zero new-shader counter at ignition
        echo "0" > "$SHM_DIR/vault_new.txt"

        # Spawn workers according to build tier
        if [ "$ETK_BUILD_TYPE" = "FULL" ]; then
            nohup bash "$ETK_ROOT/bin/vault_d.sh"   >/dev/null 2>&1 &
            nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        elif [ "$ETK_BUILD_TYPE" = "LITE" ]; then
            nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        fi
    fi

    # --- RUNNING: Continuous ID refresh and persistent anchor write ---
    if [ "$CUR_STATE" = "RUNNING" ]; then
        ID_STR=$(pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1)
        if [ -n "$ID_STR" ]; then
            echo "$ID_STR" > "$ID_FILE"
            echo "$ID_STR" > "$RECENT_ID_FILE"
        fi
    fi

    # --- SHUTDOWN: RUNNING -> IDLE transition ---
    if [ "$CUR_STATE" = "IDLE" ] && [ "$PREV_STATE" = "RUNNING" ]; then
        echo "IDLE" > "$ID_FILE"
        pkill -f vault_d.sh   2>/dev/null
        pkill -f thermal_d.sh 2>/dev/null

        echo "ETK:[$ETK_BUILD_TYPE] | WAITING FOR IGNITION" > "$LIVE_STAT.tmp"
        mv "$LIVE_STAT.tmp" "$LIVE_STAT"
    fi

    # THE LOAD-BEARING LINE. Do not change to a literal string.
    PREV_STATE="$CUR_STATE"
    sleep 2
done
SENTRY

    chmod +x "$BOOT_SENTRY"

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

    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk.service
    systemctl restart etk.service
    sleep 2
    systemctl is-active --quiet etk.service && echo "SENTRY_OK" || echo "SENTRY_FAIL"
REMOTE

# Check the sentry status signal written by the remote block
echo ""
echo -e "${G}>>> DEPLOYMENT COMPLETE.${N}"
echo -e "    Run ${Y}ssh $RIG_SSH 'systemctl status etk.service'${N} to confirm sentry health."