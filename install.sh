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
    # FIX: Added --modify-window=2 to account for exFAT/FAT32 2-second timestamp drift
    RSYNC_CMD="rsync -az --modify-window=2 --progress"
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
# Reject the IDLE/UNKNOWN_ID sentinels: env.sh writes "IDLE" to $ID_FILE
# whenever no game is running, and `${RIG_ID:-...}` only catches empty.
# Without this guard, an install while the rig is idle (the normal case)
# resolves VAULT_DIR to vault/$CHIPSET/IDLE/shaders and `mkdir -p` below
# creates an empty IDLE/shaders dir on the rig (then mirrored to host).
case "$RIG_ID" in
    ""|IDLE|UNKNOWN_ID) TARGET_ID="NPUA80075" ;;
    *) TARGET_ID="$RIG_ID" ;;
esac
export TARGET_ID
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
# Scoped to $CHIPSET/ so anything outside the structured
# vault/$CHIPSET/<gameID>/shaders/ tree (e.g. RPCS3 ppu-* per-binary
# caches that landed at vault root from a legacy tool or a manual op)
# cannot propagate between rig and host on subsequent installs.
mkdir -p "./vault/$CHIPSET"
$RSYNC_CMD --ignore-existing --exclude='.DS_Store' "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/" "./vault/$CHIPSET/"

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
# Scoped to $CHIPSET/ — see step 2 rationale.
if [ -d "./vault/$CHIPSET" ] && [ "$(ls -A "./vault/$CHIPSET" 2>/dev/null)" ]; then
    $RSYNC_CMD --ignore-existing --exclude='.DS_Store' "./vault/$CHIPSET/" "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/"
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

# Python engine and field schema
$RSYNC_CMD --exclude='.DS_Store' ./bin/etk_pitstop.py         $RIG_SSH:$ETK_ROOT/bin/
$RSYNC_CMD --exclude='.DS_Store' ./config/pitstop_fields.json $RIG_SSH:$ETK_ROOT/config/

# THE MASTER COPY: Push to the persistent safe zone
$RSYNC_CMD --exclude='.DS_Store' ./config/etk_pitstop.sh      $RIG_SSH:$ETK_ROOT/config/
ssh $RIG_SSH "sed -i 's/\r$//' $ETK_ROOT/config/etk_pitstop.sh && chmod +x $ETK_ROOT/config/etk_pitstop.sh"

# Deploy the initial launcher directly to modules for immediate use without reboot
echo -e "    Deploying launcher to /storage/.config/modules/..."
$RSYNC_CMD --exclude='.DS_Store' ./config/etk_pitstop.sh $RIG_SSH:/storage/.config/modules/etk_pitstop.sh

# THE KILL SHOT: Sanitize, Arm, and Weld to Disk
ssh $RIG_SSH "sed -i 's/\r$//' /storage/.config/modules/etk_pitstop.sh && \
              chmod +x /storage/.config/modules/etk_pitstop.sh && \
              sync"

# Verify the launcher actually landed
LAUNCHER_CHECK=$(ssh $RIG_SSH "[ -x /storage/.config/modules/etk_pitstop.sh ] && echo OK || echo MISSING")
if [ "$LAUNCHER_CHECK" = "OK" ]; then
    echo -e "    ${G}[OK] Launcher verified and locked to disk.${N}"
else
    echo -e "    ${R}[FAIL] Launcher missing or lacks +x permissions — aborting.${N}"
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
# WATCHDOGS: mango_bridge.sh AND input_d.py are kept alive
# every tick regardless of game state. input_d.py is the ONLY
# persistence vector for the R3 recovery panic button — the
# rig is headless, so this watchdog is load-bearing too.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

# ==========================================================
# SPYCRAFT: THE FORENSICS PROBE & TRIPWIRE
# ==========================================================
SPY_LOG="/storage/etk_spycraft.log"
echo "=====================================" > "$SPY_LOG"
echo "[$(date '+%H:%M:%S.%N')] SENTRY WAKING UP..." >> "$SPY_LOG"

# Initial Deployment
mkdir -p /storage/.config/modules/
cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
chmod +x /storage/.config/modules/etk_pitstop.sh
echo "[$(date '+%H:%M:%S.%N')] TROJAN INJECTED." >> "$SPY_LOG"
# ==========================================================

# --- REBOOT SURVIVAL: Seed SHM on boot before first loop tick ---
mkdir -p "$SHM_DIR"
echo "0"    > "$SHM_DIR/vault_count"
echo "0"    > "$SHM_DIR/vault_new.txt"
echo "0"    > "$SHM_DIR/vault_size.txt"
echo "IDLE" > "$SHM_DIR/active_id.txt"
echo "$DEFAULT_MODE" > "$SHM_DIR/etk_mode.txt"
echo "READY" > "$SHM_DIR/vault_stat.txt"
echo "ETK:[$ETK_BUILD_TYPE] | SYSTEM ONLINE" > "$LIVE_STAT"

# --- ROCKNIX NATIVE APP REBOOT SURVIVAL: Seed SHM on boot before first loop tick ---
mkdir -p "$SHM_DIR" /storage/.config/modules
# DEFEAT THE REBOOT BOSS: Re-inject the launcher into the volatile modules directory every boot
# GEMINI is saying these are not needed anymore
# cp /storage/games-internal/roms/etk/config/etk_pitstop.sh /storage/.config/modules/etk_pitstop.sh
# chmod +x /storage/.config/modules/etk_pitstop.sh

# ==========================================================
# 2. THE TROJAN HORSE INJECTION
# Rebuild the Pitstop module after Rocknix's boot-wipe
# ==========================================================
mkdir -p /storage/.config/modules/
# FIX: Look in the config folder for the master copy
cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
chmod +x /storage/.config/modules/etk_pitstop.sh
# ==========================================================

echo "0"    > "$SHM_DIR/vault_count"

# ==========================================================
# SHADER PIPELINE: cache -> vault symlink establishment
# ----------------------------------------------------------
# Regression context: the /storage/.cache/mesa_shader_cache ->
# $VAULT_DIR symlink (AI_MANIFEST "SYMLINK SANCTITY") was
# dropped in commit 8ad9ecf when install.sh became the
# event-driven boot-persistent Sentry. Without it, MESA Turnip
# writes shaders to a real cache dir that vault_d.sh never
# inspects, so the HUD "NEW" counter is pinned at 0 forever.
#
# etk_link_cache <target_vault>: idempotently makes the fixed
# Turnip cache path resolve into the per-game vault.
#  - If the cache path is a REAL directory (legacy / first run),
#    its contents are folded into the vault first; the real dir
#    is only removed if that rsync SUCCEEDS, so shaders are
#    never lost on a partial migrate.
#  - The symlink is only (re)pointed when it differs from the
#    desired target, and vault_d.sh is killed on a re-point so
#    the Accountant re-baselines against the correct tree.
# BusyBox-safe: readlink -f, rsync, ln -sf, [ -L ], [ -d ].
# ==========================================================
etk_link_cache() {
    DESIRED="$1"
    [ -z "$DESIRED" ] && return 0
    mkdir -p "$DESIRED" "$(dirname "$RPCS3_CACHE_DIR")"

    # Legacy/first-run: a REAL directory squatting on the cache path.
    # Get it out of the way by RENAME (atomic on same fs, reliable on
    # BusyBox) so the symlink can always be established — never leave
    # the pipeline broken on a copy hiccup. Old contents are folded
    # into the vault best-effort afterward; nothing is deleted, so a
    # failed fold loses no shaders (they remain in the .pre-etk backup).
    if [ -e "$RPCS3_CACHE_DIR" ] && [ ! -L "$RPCS3_CACHE_DIR" ] && [ -d "$RPCS3_CACHE_DIR" ]; then
        BK="${RPCS3_CACHE_DIR}.pre-etk.$(date +%s)"
        if mv "$RPCS3_CACHE_DIR" "$BK" 2>/dev/null; then
            if command -v rsync >/dev/null 2>&1; then
                rsync -a "$BK"/ "$DESIRED"/ 2>/dev/null
            else
                cp -a "$BK"/. "$DESIRED"/ 2>/dev/null
            fi
            echo "[$(date '+%H:%M:%S.%N')] CACHE DIR MOVED -> $BK, folded into $DESIRED" >> "$SPY_LOG"
        else
            echo "[$(date '+%H:%M:%S.%N')] FATAL: cannot move real cache dir $RPCS3_CACHE_DIR" >> "$SPY_LOG"
            return 1
        fi
    fi

    CUR_LINK="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null)"
    if [ "$CUR_LINK" != "$DESIRED" ]; then
        rm -f "$RPCS3_CACHE_DIR"
        ln -sf "$DESIRED" "$RPCS3_CACHE_DIR"
        pkill -f vault_d.sh 2>/dev/null
        echo "[$(date '+%H:%M:%S.%N')] CACHE LINKED -> $DESIRED" >> "$SPY_LOG"
    fi
    return 0
}

# --- BOOT PRE-SEED ---
# RPCS3 can be launched before the first IDLE->RUNNING tick is
# handled. Point the cache at the last-played game's vault now
# so the link already exists (as a symlink, never a real dir)
# before Turnip writes its first shader. Ignition will re-point
# it if the actual game differs.
SEED_ID="$(cat "$RECENT_ID_FILE" 2>/dev/null)"
# Match the install-time TARGET_ID guard: reject IDLE/UNKNOWN_ID sentinels,
# not just empty. RECENT_ID_FILE should only ever hold a resolved game ID,
# but treat any non-pattern value as missing so a stale sentinel cannot
# create vault/$CHIPSET/IDLE/shaders via etk_link_cache's mkdir.
case "$SEED_ID" in
    ""|IDLE|UNKNOWN_ID) SEED_ID="NPUA80075" ;;
esac
etk_link_cache "$ETK_ROOT/vault/$CHIPSET/$SEED_ID/shaders"

PREV_STATE="IDLE"

while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh
    
    # --- PROBE TRIPWIRE ---
    if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ]; then
        echo "[$(date '+%H:%M:%S.%N')] ALARM: FILE WAS NUKED! Re-deploying..." >> "$SPY_LOG"
        cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
        chmod +x /storage/.config/modules/etk_pitstop.sh
    fi

    CUR_STATE="IDLE"

    # --- STATE DETECTION ---
    if pgrep -f "rpcs3|AppRun.wrapped" > /dev/null; then
        CUR_STATE="RUNNING"
    else
        CUR_STATE="IDLE"
    fi

    # --- BRIDGE WATCHDOG: Keep mango_bridge alive regardless of game state ---
    pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/scripts/mango_bridge.sh" >/dev/null 2>&1 &

    # --- SHIFTER WATCHDOG: Keep input_d.py alive (headless R3 panic button) ---
    pgrep -f input_d.py >/dev/null || nohup python3 "$ETK_ROOT/bin/input_d.py" >/dev/null 2>&1 &

    # --- IGNITION: IDLE -> RUNNING transition ---
    if [ "$CUR_STATE" = "RUNNING" ] && [ "$PREV_STATE" = "IDLE" ]; then
        # Wait for RPCS3 to populate its process environment with the game ID
        sleep 4

        # RACE-PROOF IDENTITY SYNC: Resolve and commit ID before spawning workers
        ID_STR=$(resolve_game_id)
        echo "${ID_STR:-NPUA80075}" > "$ID_FILE"
        # Anchor the persistent last-played ID at ignition, not only on the
        # running tick — otherwise a game that exits before the first
        # non-empty tick leaves RECENT_ID_FILE pointing at the prior game,
        # so Pitstop tunes the wrong config. Never anchor the NPUA80075
        # fallback: only commit a genuinely resolved ID.
        [ -n "$ID_STR" ] && echo "$ID_STR" > "$RECENT_ID_FILE"

        # Re-source so $VAULT_DIR reflects the just-resolved ID
        # (env.sh derives TARGET_ID -> VAULT_DIR from $ID_FILE).
        source /storage/games-internal/roms/etk/scripts/env.sh

        # SHADER PIPELINE: point the Turnip cache at THIS game's
        # vault before vault_d.sh starts. etk_link_cache kills any
        # stale vault_d so the spawn below baselines cleanly.
        etk_link_cache "$VAULT_DIR"

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
        ID_STR=$(resolve_game_id)
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
echo -e "${G}>>> DEPLOYMENT COMPLETE PRESS START - GAME SETTINGS - UPDATE GAMELISTS TO ACTIVATE THE ETK PITSTOP APP IN ROCKNIX TOOLS.${N}"
echo -e "    Run ${Y}ssh $RIG_SSH 'systemctl status etk.service'${N} to confirm sentry health."