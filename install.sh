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

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'

# ==========================================================
# FIRST-RUN: OPERATOR CONFIG WIZARD
# ==========================================================
# etk.conf is the operator's gitignored preferences file (rig SSH target,
# build tier, HUD options). On first run we generate it from
# etk.conf.example, auto-discovering the rig via mDNS — Rocknix publishes
# itself as <SOC>.local out of the box (avahi-daemon + ssh.service). On
# every subsequent run we just use the existing etk.conf.
# ==========================================================
if [ ! -f "./etk.conf" ]; then
    echo -e "${C}>>> First run — generating etk.conf...${N}"

    if [ ! -f "./etk.conf.example" ]; then
        echo -e "    ${R}[FAIL] etk.conf.example missing — repo is incomplete.${N}"
        exit 1
    fi

    # The wizard prompts the operator interactively. Bail loudly if there's
    # no controlling terminal so the install doesn't silently corrupt
    # etk.conf with an empty/garbage answer.
    if [ ! -r /dev/tty ]; then
        echo -e "    ${R}[FAIL] No interactive terminal — cannot run first-run wizard.${N}"
        echo -e "    ${R}       Run from a terminal, or copy etk.conf.example -> etk.conf${N}"
        echo -e "    ${R}       manually and re-run.${N}"
        exit 1
    fi

    # Self-name for filtering our own host out of the mDNS browse list.
    # macOS publishes the "Computer Name" from System Preferences (which
    # may contain spaces and punctuation — `hostname -s` returns the
    # different LocalHostName form, so the BSD hostname won't match).
    HOST_NAME=""
    if [ "$(uname)" = "Darwin" ] && command -v scutil >/dev/null 2>&1; then
        HOST_NAME=$(scutil --get ComputerName 2>/dev/null)
    fi
    [ -z "$HOST_NAME" ] && HOST_NAME=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo "")

    CANDIDATES=""
    if command -v dns-sd >/dev/null 2>&1; then
        # macOS native Bonjour. Background browse for 2s, kill, parse Add
        # rows. Split on "_ssh._tcp." so instance names containing spaces
        # survive ("Dave's MacBook Air" would lose words on whitespace awk).
        BROWSE=$( ( dns-sd -B _ssh._tcp local. 2>/dev/null & DPID=$!; sleep 2; kill $DPID 2>/dev/null; wait 2>/dev/null ) )
        CANDIDATES=$(printf '%s\n' "$BROWSE" \
            | awk -F'_ssh\\._tcp\\.[ \t]+' '/Add/ && NF > 1 { print $2 }' \
            | awk '{ sub(/[ \t]+$/, ""); print }' \
            | sort -u \
            | grep -vFx "$HOST_NAME" 2>/dev/null)
    elif command -v avahi-browse >/dev/null 2>&1; then
        CANDIDATES=$(avahi-browse -rtp _ssh._tcp 2>/dev/null \
            | awk -F';' '/^=/ { print $4 }' \
            | sort -u \
            | grep -vFx "$HOST_NAME" 2>/dev/null)
    else
        echo -e "    ${Y}[INFO] No mDNS browser (dns-sd / avahi-browse) found.${N}"
        echo -e "    ${Y}       Auto-discovery skipped; please enter the rig manually.${N}"
    fi

    N_CAND=$(printf '%s\n' "$CANDIDATES" | grep -c . 2>/dev/null || echo 0)
    CHOSEN=""
    case "$N_CAND" in
        0)
            echo -e "    ${Y}[INFO] No SSH hosts discovered on the LAN.${N}"
            printf "    Enter rig hostname or IP (e.g. SM8250.local, 192.168.1.53): "
            IFS= read -r ANSWER </dev/tty
            CHOSEN="$ANSWER"
            ;;
        1)
            CHOSEN=$(printf '%s\n' "$CANDIDATES" | head -n 1)
            echo -e "    ${G}[OK] Discovered rig: ${CHOSEN}.local${N}"
            CHOSEN="${CHOSEN}.local"
            ;;
        *)
            echo -e "    Multiple SSH hosts found on the LAN:"
            printf '%s\n' "$CANDIDATES" | awk '{ printf "      %d) %s.local\n", NR, $0 }'
            printf "    Pick rig number (1-%s) or 'q' to enter manually: " "$N_CAND"
            IFS= read -r PICK </dev/tty
            if [ "$PICK" = "q" ] || [ "$PICK" = "Q" ]; then
                printf "    Enter rig hostname or IP: "
                IFS= read -r ANSWER </dev/tty
                CHOSEN="$ANSWER"
            else
                MATCH=$(printf '%s\n' "$CANDIDATES" | sed -n "${PICK}p")
                [ -n "$MATCH" ] && CHOSEN="${MATCH}.local"
            fi
            ;;
    esac

    # Validate: non-empty, single line, no newlines or sed-breaking chars.
    if [ -z "$CHOSEN" ] || printf '%s' "$CHOSEN" | grep -q '[[:cntrl:]]'; then
        echo -e "    ${R}[FAIL] Invalid rig target (empty or contains control chars).${N}"
        exit 1
    fi

    # Generate etk.conf from example, substituting RIG_SSH.
    # macOS sed and GNU sed differ on `-i`; the .bak-then-delete pattern is
    # portable across both.
    cp ./etk.conf.example ./etk.conf
    sed -i.bak "s|^RIG_SSH=.*|RIG_SSH=\"root@${CHOSEN}\"|" ./etk.conf
    rm -f ./etk.conf.bak
    echo -e "    ${G}[OK] Wrote etk.conf with RIG_SSH=\"root@${CHOSEN}\"${N}"
    echo -e "    Edit ./etk.conf later to change tier, mode, or HUD options."
    echo ""
fi

source ./scripts/env.sh

# --- FLAG PARSE ---
# --restore-state: GATED Tier-B restore (host -> rig). Default off; this is
# the only path that writes to rig telemetry/configs/saves and is intended
# ONLY for standing a fresh/reflashed rig back up. Mirrors uninstall.sh's
# --zap-vault gating idiom. Tolerated in any arg position.
RESTORE_STATE=0
for arg in "$@"; do
    case "$arg" in
        --restore-state) RESTORE_STATE=1 ;;
    esac
done

# Tier-B host snapshot tree (local to install.sh, mirroring how ./vault
# is already a literal here). Never auto-deleted (cf. ./vault philosophy).
HOST_STATE_DIR="./state"

if [ "$RESTORE_STATE" = "1" ]; then
    echo -e "${R}>>> WARNING: --restore-state passed.${N}"
    echo -e "${R}    Host-side Tier-B state ($HOST_STATE_DIR) will OVERWRITE rig"
    echo -e "    telemetry ledgers, tuned RPCS3 configs, and the RPCS3 user-profile"
    echo -e "    tree (saves, trophies, .rap licenses). Intended ONLY for a"
    echo -e "    fresh/reflashed rig. NEVER run this as part of a routine update.${N}"
    echo ""
fi

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

# --- MESA REBUILD FINGERPRINT (addendum §G.5) ---
# Every Rocknix nightly rebuilds Mesa from source even when upstream is
# unchanged; the new libvulkan_freedreno.so build ID invalidates the prior
# Turnip cache layer, doubling per-game vault size in one session. Fingerprint
# the first 64 KB of the shared object (BusyBox-friendly, deterministic) and
# compare against $ETK_ROOT/vault/.last_mesa.hash. On change, emit a one-line
# tripwire and prompt the operator to run tools/vault_sweep.sh. The hash file's
# OWN mtime is the rebuild-detected epoch — tools/vault_sweep.sh reads it as
# the sweep cutoff. Silent on first ever run (no prior hash to compare).
MESA_HASH_NEW=$(ssh $RIG_SSH "head -c 65536 /usr/lib/libvulkan_freedreno.so 2>/dev/null | sha256sum | cut -d' ' -f1")
if [ -n "$MESA_HASH_NEW" ]; then
    MESA_HASH_FILE="$ETK_ROOT/vault/.last_mesa.hash"
    MESA_HASH_OLD=$(ssh $RIG_SSH "cat $MESA_HASH_FILE 2>/dev/null")
    if [ -z "$MESA_HASH_OLD" ]; then
        # First run: seed the fingerprint silently. Nothing prior to compare.
        ssh $RIG_SSH "mkdir -p $ETK_ROOT/vault && printf '%s\n' '$MESA_HASH_NEW' > $MESA_HASH_FILE"
    elif [ "$MESA_HASH_OLD" != "$MESA_HASH_NEW" ]; then
        echo -e "    ${Y}[INFO] MESA REBUILT — vault layer keyed against new build ID.${N}"
        echo -e "    ${Y}       Run \`tools/vault_sweep.sh\` (on the rig) to prune orphaned shaders.${N}"
        ssh $RIG_SSH "echo \"[\$(date '+%H:%M:%S.%N')] MESA REBUILD DETECTED (was $MESA_HASH_OLD, now $MESA_HASH_NEW)\" >> $TRIPWIRE_LOG && printf '%s\n' '$MESA_HASH_NEW' > $MESA_HASH_FILE"
    fi
fi

# --- SCREENSHOT FEATURE DEPENDENCY CHECK ---
# `grim` is the Wayland screenshot tool that bin/screenshot.sh shells out
# to. It ships with Rocknix today, but a future nightly could strip it
# and the L1 chord would then silently produce zero output. Probe at
# install time so any breakage surfaces here, not at first L1 press.
if ! ssh $RIG_SSH "command -v grim >/dev/null 2>&1"; then
    echo -e "    ${Y}[WARN] \`grim\` not available on rig — ETK screenshot feature (L1) will not work${N}"
    echo -e "    ${Y}       until grim is reinstalled. Check Rocknix package state.${N}"
fi

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
    $ETK_ROOT/screenshots \
    $PKG_STAGING_DIR \
    /storage/.config/custom_scripts \
    /storage/.config/system.d \
    /storage/.config/modules \
    /storage/.config/MangoHud \
    $RPCS3_CUSTOM_CONFIGS \
    $RPCS3_GAME_DIR \
    $RPCS3_EXDATA_DIR \
    $RPCS3_HOME_DIR \
    $PS3_LAUNCHER_DIR \
    $TELEMETRY_DIR"

# Document the PKG install drop folder so a user browsing the SD card
# understands what it is and that staged files are consumed on success.
ssh $RIG_SSH "cat > '$PKG_STAGING_DIR/README.txt'" <<'PKGREADME'
ETK PITSTOP - PS3 PACKAGE INSTALL DROP FOLDER
=============================================

Drop ONE .pkg file here (plus its .rap licence file, if the
game needs one) to install a PS3 game.

Then on the handheld:
  Tools > ETK Pitstop > TOOLS tab > Install a staged PS3 Package

On a SUCCESSFUL install the .pkg and .rap are deleted from this
folder automatically. A failed install leaves them here so you
can retry. One game at a time.
PKGREADME

# Document the ETK screenshots folder so a user browsing the SD card
# (or the mapped \\<rig-ip>\games-internal SMB share) understands what
# it is, how to trigger captures, and the per-game L1 onboarding step.
ssh $RIG_SSH "cat > '$ETK_ROOT/screenshots/README.txt'" <<'SHOTREADME'
ETK SCREENSHOTS - DRIVER DATA UNIT CAPTURES
===========================================

Silent `grim` captures of the full Wayland surface, MangoHUD overlay
included. RPCS3's own Save Screenshot strips the HUD; this folder is
how you get the ETK DDU into the camera shot.

Triggers (handheld):
  L1                  = recommended in-race screenshot (single button)
  SELECT + D-pad Up   = UI / menu / Pitstop / pause-menu fallback

Per-game onboarding (one time, before in-race L1 works):
  Open each PS3 game's controller settings and UNBIND L1. In GT5P/GT6
  L1 defaults to Rear View camera toggle, which doesn't render on
  Turnip anyway — a free trade. Without this step, L1 in-race fires
  BOTH the screenshot and the game's default L1 action.

Filename: etk_<gameID>_<YYYYMMDD>_<HHMMSS>.png
  gameID = ROCKNIX when captured outside a game session.

Host access via SMB:
  Windows:  \\<rig-ip>\games-internal\roms\etk\screenshots\
  macOS:    smb://<rig-ip>/games-internal/roms/etk/screenshots/

Backup: every `./install.sh` Tier-B mirror copies this folder to the
host at ./state/screenshots/. To restore on a fresh/reflashed rig run
`./install.sh --restore-state`.

Diagnostics: if a press produces no file, check `.last_error` in this
folder — `bin/screenshot.sh` writes grim's stderr there on failure.
SHOTREADME

# ETK mako notification style: a criteria section scoped to app-name
# "ETK Pitstop" so ETK toasts are wide and readable WITHOUT altering
# Rocknix's own notifications. Idempotent -- appended only once.
ssh $RIG_SSH 'bash -s' <<'ETKMAKO'
MCFG=/storage/.config/mako/config
if [ -f "$MCFG" ] && ! grep -q 'app-name="ETK Pitstop"' "$MCFG"; then
cat >> "$MCFG" <<'MK'

[app-name="ETK Pitstop"]
width=1280
height=560
font=monospace 24
default-timeout=8000
padding=20
margin=24
border-size=3
border-color=#00b4d8
border-radius=14
text-alignment=left
background-color=#10141a
MK
XDG_RUNTIME_DIR=/var/run/0-runtime-dir makoctl reload 2>/dev/null
echo "    [OK] ETK mako notification style installed."
else
echo "    [SKIP] ETK mako style already present (or mako config absent)."
fi
ETKMAKO

# ==========================================================
# STEP 2: VAULT PULL — RIG -> HOST PC (SAFEGUARD HARVEST)
# Always pull before pushing so no session's banked shaders
# are lost if the rig is about to be reflashed or repaired.
# --ignore-existing: shader files are content-fixed (cache key
# == filename), so a banked .bin is immutable; never overwrite a
# host copy with a rig copy of the same name.
# ==========================================================
echo -e "${C}>>> [2/${TOTAL_STEPS}] SAFEGUARDING HARVEST (PULLING SHADERS: RIG -> HOST PC)...${N}"
# Scoped to $CHIPSET/ so anything outside the structured
# vault/$CHIPSET/<gameID>/shaders/ tree (e.g. RPCS3 ppu-* per-binary
# caches that landed at vault root from a legacy tool or a manual op)
# cannot propagate between rig and host on subsequent installs.
mkdir -p "./vault/$CHIPSET"
$RSYNC_CMD --ignore-existing --exclude='.DS_Store' "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/" "./vault/$CHIPSET/"

# --- TIER-B BACKUP: rig -> host (addendum §C) ---
# Mutable-precious state: telemetry ledgers, tuned RPCS3 configs, RPCS3
# user-profile tree (saves/trophies/.rap licenses). Plain mirror (no
# --ignore-existing — host must track edits; no --delete — host snapshot is
# an archive, never pruned). Read-only on the rig: zero SD writes. Skipped
# per-rule when the rig source is absent (fresh rig, first run).
echo -e "${C}    [Tier-B] Backing up telemetry / configs / RPCS3 home / screenshots (RIG -> HOST PC)...${N}"
mkdir -p "$HOST_STATE_DIR/etk_telemetry" "$HOST_STATE_DIR/custom_configs" "$HOST_STATE_DIR/rpcs3_home" "$HOST_STATE_DIR/screenshots"
for pair in \
    "$TELEMETRY_DIR|$HOST_STATE_DIR/etk_telemetry" \
    "$RPCS3_CUSTOM_CONFIGS|$HOST_STATE_DIR/custom_configs" \
    "$RPCS3_HOME_DIR|$HOST_STATE_DIR/rpcs3_home" \
    "$ETK_ROOT/screenshots|$HOST_STATE_DIR/screenshots"; do
    SRC="${pair%%|*}"
    DST="${pair##*|}"
    if ssh $RIG_SSH "[ -d $SRC ]" 2>/dev/null; then
        $RSYNC_CMD --exclude='.DS_Store' "$RIG_SSH:$SRC/" "$DST/"
    else
        echo -e "    ${Y}[SKIP] rig source absent: $SRC${N}"
    fi
done
# RECENT_ID_FILE lives at vault root (outside the $CHIPSET/ scope Step 2
# pulls). Tiny, mutable, and folding it in saves one wrong-config flash on
# the first launch after --restore-state (seed_id falls back to NPUA80075
# otherwise — wrong game's HUD position, wrong cache pre-seed link).
if ssh $RIG_SSH "[ -f $RECENT_ID_FILE ]" 2>/dev/null; then
    $RSYNC_CMD --exclude='.DS_Store' "$RIG_SSH:$RECENT_ID_FILE" "./vault/last_played_id.txt"
fi

# ==========================================================
# STEP 3: DEPLOY SCRIPTS, DAEMONS & OVERLAYS
# --delete removes stale files from previous versions so
# renamed/removed scripts don't linger and cause ghost invocations.
# ==========================================================
echo -e "${C}>>> [3/${TOTAL_STEPS}] DEPLOYING GUARDIAN DAEMONS & SCRIPTS...${N}"
$RSYNC_CMD --delete --exclude='*.pyc' --exclude='__pycache__' --exclude='.DS_Store' ./bin/ $RIG_SSH:$ETK_ROOT/bin/
$RSYNC_CMD --delete --exclude='.DS_Store' ./scripts/ $RIG_SSH:$ETK_ROOT/scripts/
$RSYNC_CMD --exclude='.DS_Store' ./config/MangoHud.conf $RIG_SSH:/storage/.config/MangoHud/MangoHud.conf
# Operator config: the Sentry's env.sh source on the rig reads this file
# for ETK_BUILD_TYPE, DEFAULT_MODE, HUD_HEADER_HOLD_S. Without this push
# the rig would silently use the baked-in defaults regardless of operator
# edits on the host. --checksum forces content-based comparison because
# operator edits often swap byte-identical values (HUD_HEADER_HOLD_S=15
# vs =42 are the same size) and rsync's default mtime+size check would
# silently skip such "same-size" edits when mtimes happen to match.
$RSYNC_CMD --checksum --exclude='.DS_Store' ./etk.conf $RIG_SSH:$ETK_ROOT/etk.conf
ssh $RIG_SSH "chmod +x $ETK_ROOT/bin/* $ETK_ROOT/scripts/*"

# ==========================================================
# STEP 4: VAULT PUSH — HOST PC -> RIG (RESTORE BANKED SHADERS)
# Pushes any shaders from the host vault that the rig doesn't
# have yet. This is the repair/post-flash recovery path that
# saves re-harvesting after an OS reflash or SD card swap.
# --ignore-existing: same reasoning as Step 2 — shader files are
# content-fixed; we only add what the rig is missing, never overwrite.
# Bidirectional --ignore-existing is the property that makes the
# Tier-A vault sync safe to run on every install (addendum §B).
# ==========================================================
echo -e "${C}>>> [4/${TOTAL_STEPS}] RESTORING BANKED SHADERS (PUSHING VAULT: HOST PC -> RIG)...${N}"
# Scoped to $CHIPSET/ — see step 2 rationale.
if [ -d "./vault/$CHIPSET" ] && [ "$(ls -A "./vault/$CHIPSET" 2>/dev/null)" ]; then
    $RSYNC_CMD --ignore-existing --exclude='.DS_Store' "./vault/$CHIPSET/" "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/"
else
    echo -e "    ${Y}[SKIP] No local vault found — nothing to push.${N}"
fi

# --- TIER-B RESTORE: host -> rig (addendum §C, gated) ---
# OVERWRITE semantics — NOT --update. A just-reinstalled game drops a
# *template* config_<ID>.yml whose mtime is newer than the tuned backup, so
# --update would silently skip the restore and leave the game on template
# tuning (the spec's "newer-mtime trap"). Plain $RSYNC_CMD overwrites the
# template with the tuned backup, which is the entire point of --restore-state.
# No --delete: a fresh rig has nothing extra; --delete is pure downside.
# Inert on default runs (no flag = no host->rig state write).
if [ "$RESTORE_STATE" = "1" ]; then
    echo -e "${C}    [Tier-B] Restoring telemetry / configs / RPCS3 home / screenshots (HOST PC -> RIG, OVERWRITE)...${N}"
    for pair in \
        "$HOST_STATE_DIR/etk_telemetry|$TELEMETRY_DIR" \
        "$HOST_STATE_DIR/custom_configs|$RPCS3_CUSTOM_CONFIGS" \
        "$HOST_STATE_DIR/rpcs3_home|$RPCS3_HOME_DIR" \
        "$HOST_STATE_DIR/screenshots|$ETK_ROOT/screenshots"; do
        SRC="${pair%%|*}"
        DST="${pair##*|}"
        if [ -d "$SRC" ] && [ "$(ls -A "$SRC" 2>/dev/null)" ]; then
            $RSYNC_CMD --exclude='.DS_Store' "$SRC/" "$RIG_SSH:$DST/"
        else
            echo -e "    ${Y}[SKIP] host source empty: $SRC${N}"
        fi
    done
    if [ -f "./vault/last_played_id.txt" ]; then
        $RSYNC_CMD --exclude='.DS_Store' "./vault/last_played_id.txt" "$RIG_SSH:$RECENT_ID_FILE"
    fi
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
$RSYNC_CMD --exclude='.DS_Store' ./bin/etk_pitstop.py            $RIG_SSH:$ETK_ROOT/bin/
$RSYNC_CMD --exclude='.DS_Store' ./config/pitstop_fields.json    $RIG_SSH:$ETK_ROOT/config/
$RSYNC_CMD --exclude='.DS_Store' ./config/crash_signatures.json  $RIG_SSH:$ETK_ROOT/config/
# ETK default per-game RPCS3 config template (TOOLS-tab installer copies this
# to custom_configs/config_<ID>.yml for each newly installed game).
$RSYNC_CMD --exclude='.DS_Store' ./config/etk_template.yml       $RIG_SSH:$ETK_ROOT/config/
# SVG icon master for the polished Tools-menu app entry (dossier addendum R1).
$RSYNC_CMD --exclude='.DS_Store' ./config/etk_pitstop.svg        $RIG_SSH:$ETK_ROOT/config/

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

# Register ETK Pitstop as a polished Tools-menu app: run the modules
# injector so the launcher + SVG icon + enriched gamelist <game> entry
# land immediately. /storage/.config/modules is boot-volatile, so the
# Sentry re-asserts all three every boot — this is just the first pass.
echo -e "    Registering ETK Pitstop in the Tools menu..."
ssh $RIG_SSH "python3 $ETK_ROOT/bin/etk_modules_inject.py"
GAMELIST_CHECK=$(ssh $RIG_SSH "grep -c '>ETK Pitstop<' /storage/.config/modules/gamelist.xml 2>/dev/null || echo 0")
if [ "${GAMELIST_CHECK:-0}" -ge 1 ] 2>/dev/null; then
    echo -e "    ${G}[OK] ETK Pitstop registered as a Tools-menu app.${N}"
else
    echo -e "    ${Y}[WARN] Tools-menu entry not confirmed — the Sentry will re-inject it.${N}"
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

# --- BOOT TRIPWIRE LOG ---
# Anomaly-only sink: truncated on boot; empty file == clean boot.
# Writers below append on module re-injection and cache symlink events.
: > "$TRIPWIRE_LOG"

# Initial Deployment
mkdir -p /storage/.config/modules/
cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
chmod +x /storage/.config/modules/etk_pitstop.sh

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
            echo "[$(date '+%H:%M:%S.%N')] CACHE DIR MOVED -> $BK, folded into $DESIRED" >> "$TRIPWIRE_LOG"
        else
            echo "[$(date '+%H:%M:%S.%N')] FATAL: cannot move real cache dir $RPCS3_CACHE_DIR" >> "$TRIPWIRE_LOG"
            return 1
        fi
    fi

    CUR_LINK="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null)"
    if [ "$CUR_LINK" != "$DESIRED" ]; then
        rm -f "$RPCS3_CACHE_DIR"
        ln -sf "$DESIRED" "$RPCS3_CACHE_DIR"
        pkill -f vault_d.sh 2>/dev/null
        echo "[$(date '+%H:%M:%S.%N')] CACHE LINKED -> $DESIRED" >> "$TRIPWIRE_LOG"
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

# ==========================================================
# ORPHAN PANIC RECOVERY
# ----------------------------------------------------------
# session_postmortem.sh only runs on a RUNNING->IDLE edge. A kernel panic
# / hard hang takes the rig down BEFORE that edge fires, so the
# panic-ending session never gets a ledger row -- the existing
# in-postmortem PANIC detector (BOOT_EPOCH > START_EPOCH) is unreachable
# in that path because nothing schedules a postmortem after a reboot.
#
# The persistent $SESSION_ANCHOR breadcrumb closes the gap: ignition
# writes it, postmortem deletes it. If we find it at Sentry boot AND the
# rig has rebooted since it was written AND no rpcs3 is running now,
# that's an orphan -- the previous session paniced. Synthesize one PANIC
# row from what survived (breadcrumb epoch, RPCS3.log mtime as
# last-alive proxy, RAM peak still parseable until RPCS3 next launches),
# then consume the breadcrumb so the next clean session starts fresh.
# ==========================================================
if [ -f "$SESSION_ANCHOR" ]; then
    BC_LINE=$(cat "$SESSION_ANCHOR" 2>/dev/null)
    BC_EPOCH=$(printf '%s\n' "$BC_LINE" | cut -f1)
    BC_GAME=$(printf '%s\n' "$BC_LINE" | cut -f2)
    BC_BUILD=$(printf '%s\n' "$BC_LINE" | cut -f3)
    case "$BC_EPOCH" in ''|*[!0-9]*) BC_EPOCH=0 ;; esac
    [ -z "$BC_GAME" ]  && BC_GAME="UNKNOWN"
    [ -z "$BC_BUILD" ] && BC_BUILD="$ETK_BUILD_TYPE"

    NOW=$(date +%s)
    UPTIME_SEC=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)
    case "$UPTIME_SEC" in ''|*[!0-9]*) UPTIME_SEC=0 ;; esac
    BOOT_EPOCH=$((NOW - UPTIME_SEC))

    # Synthesize ONLY when: breadcrumb has a real epoch, rig has rebooted
    # since it was written, and no rpcs3 is currently running (a Sentry
    # restart with rpcs3 still alive is not a panic -- let the normal
    # state machine resume).
    if [ "$BC_EPOCH" -gt 0 ] \
       && [ "$BOOT_EPOCH" -gt "$BC_EPOCH" ] \
       && ! pgrep -f "rpcs3|AppRun.wrapped" >/dev/null; then

        # Last-alive proxy: RPCS3 writes its log continuously, so its
        # mtime tracks the last moment the process was responsive. RPCS3
        # truncates this log on next launch, so until then the file is
        # still the panicked session's. If mtime is unavailable or
        # absurd, fall back to 0 duration -- an honest unknown.
        LAST_ALIVE=0
        if [ -f "$RPCS3_LOG" ]; then
            LAST_ALIVE=$(stat -c %Y "$RPCS3_LOG" 2>/dev/null)
            case "$LAST_ALIVE" in ''|*[!0-9]*) LAST_ALIVE=0 ;; esac
        fi
        if [ "$LAST_ALIVE" -gt "$BC_EPOCH" ] && [ "$LAST_ALIVE" -lt "$BOOT_EPOCH" ]; then
            ORPHAN_DURATION=$((LAST_ALIVE - BC_EPOCH))
            ROW_EPOCH=$LAST_ALIVE
        else
            ORPHAN_DURATION=0
            ROW_EPOCH=$NOW
        fi

        # Peak RAM is the only metric still parseable post-reboot --
        # RPCS3.log persists until next launch, telemetry/SHM seeds are
        # gone. Mirror the postmortem's PERF parser exactly.
        ORPHAN_PEAK_RAM=0
        if [ -f "$RPCS3_LOG" ]; then
            ORPHAN_PEAK_RAM=$(strings "$RPCS3_LOG" 2>/dev/null \
                | grep -oE '\(Peak: [0-9]+MB\)' \
                | grep -oE '[0-9]+' \
                | awk 'BEGIN{m=0} {if ($1+0 > m) m=$1+0} END{print int(m+0)}')
            case "$ORPHAN_PEAK_RAM" in ''|*[!0-9]*) ORPHAN_PEAK_RAM=0 ;; esac
        fi

        # Header write is idempotent -- mirrors postmortem's tmp+mv idiom
        # so a first-ever orphan synthesis still gets the schema right.
        mkdir -p "$TELEMETRY_DIR"
        if [ ! -f "$SESSIONS_LEDGER" ]; then
            TMP="$SESSIONS_LEDGER.tmp"
            printf 'epoch\tduration_s\tbuild\tgame_id\tstatus\tpeak_load\tpeak_ram_mb\tpeak_temp\tavg_temp\tcrash_sig\tfence_at_crash\tshaders_harvested\tdrain_pct\tthermal_overrides\n' > "$TMP"
            mv "$TMP" "$SESSIONS_LEDGER"
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$ROW_EPOCH" "$ORPHAN_DURATION" "$BC_BUILD" "$BC_GAME" "PANIC" \
            "0.0" "$ORPHAN_PEAK_RAM" "0" "0" \
            "PANIC_REBOOT" "0" "0" "0" "0" \
            >> "$SESSIONS_LEDGER"

        [ -x "$ETK_ROOT/scripts/career_aggregate.sh" ] && \
            "$ETK_ROOT/scripts/career_aggregate.sh" "$BC_GAME" >/dev/null 2>&1
    fi

    # Always consume -- a stale breadcrumb from a Sentry restart without
    # reboot, or a breadcrumb whose synthesis was skipped, must not
    # poison the next legitimate session.
    rm -f "$SESSION_ANCHOR"
fi

PREV_STATE="IDLE"

while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh
    
    # --- PROBE TRIPWIRE ---
    # /storage/.config/modules is boot-volatile: Rocknix wipes it and
    # regenerates gamelist.xml every boot. Re-assert the full ETK Pitstop
    # Tools-menu presence -- launcher .sh, .svg icon, and the enriched
    # <game> entry -- whenever any of the three is missing. The injector is
    # idempotent and touches only the ETK entry; other tools are untouched.
    if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ] \
       || [ ! -f "/storage/.config/modules/etk_pitstop.svg" ] \
       || [ ! -f "$MODULES_GAMELIST" ] \
       || ! grep -q '>ETK Pitstop<' "$MODULES_GAMELIST" 2>/dev/null; then
        echo "[$(date '+%H:%M:%S.%N')] modules wiped/stale -- re-injecting ETK Pitstop" >> "$TRIPWIRE_LOG"
        python3 "$ETK_ROOT/bin/etk_modules_inject.py" >> "$TRIPWIRE_LOG" 2>&1
    fi

    # --- INSTALL LOCK (dossier §4) ---
    # While the TOOLS-tab installer runs, RPCS3 IS the installer process,
    # not a game. Stay parked in IDLE: no RUNNING transition, no worker
    # spawn, no post-mortem, no phantom telemetry row. The lock lives in
    # volatile SHM, so a crash mid-install self-clears on the next reboot.
    if [ -f "$ETK_INSTALL_LOCK" ]; then
        PREV_STATE="IDLE"
        sleep 2
        continue
    fi

    CUR_STATE="IDLE"

    # --- STATE DETECTION ---
    if pgrep -f "rpcs3|AppRun.wrapped" > /dev/null; then
        CUR_STATE="RUNNING"
    else
        CUR_STATE="IDLE"
    fi

    # --- BRIDGE WATCHDOG: Keep mango_bridge alive regardless of game state ---
    pgrep -f mango_bridge.sh >/dev/null || nohup bash "$ETK_ROOT/bin/mango_bridge.sh" >/dev/null 2>&1 &

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

        # --- PER-GAME HUD POSITION ---
        # Apply the remembered MangoHud position for this game (if any) and
        # signal MangoHud to reload its config so the swap takes effect mid-
        # launch (the 4s sleep above gives MangoHud time to open its control
        # socket as RPCS3 initialises Vulkan). Default = bottom-left
        # (dashboard convention); a per-game override at $HUD_POSITIONS_FILE
        # wins. L3 (bin/input_d.py) is the user-side toggle that writes that
        # file. The reload signal is best-effort — if the socket isn't there
        # yet, the conf is still correct for any subsequent launches.
        if [ -f "$HUD_POSITIONS_FILE" ] && [ -f "$RIG_MANGO_CONF" ] && [ -n "$ID_STR" ]; then
            PREF_POS=$(awk -F'\t' -v gid="$ID_STR" '$1==gid {print $2; exit}' "$HUD_POSITIONS_FILE")
            if [ -n "$PREF_POS" ]; then
                # Atomic in-place sed; MangoHud.conf has exactly one
                # position= line by convention.
                sed -i "s|^position=.*|position=$PREF_POS|" "$RIG_MANGO_CONF"
                # Inline Python — BusyBox nc lacks reliable -U Unix socket
                # support. Mirrors the helper in bin/input_d.py so the two
                # paths stay behaviour-compatible without sharing state.
                python3 -c "
import socket, glob
for p in glob.glob('/tmp/MangoHud*') + glob.glob('/tmp/mangohud*'):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3); s.connect(p)
        s.send(b'reload_cfg\n'); s.close(); break
    except Exception: pass
" 2>/dev/null
            fi
        fi

        # Spawn workers according to build tier
        if [ "$ETK_BUILD_TYPE" = "FULL" ]; then
            nohup bash "$ETK_ROOT/bin/vault_d.sh"   >/dev/null 2>&1 &
            nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        elif [ "$ETK_BUILD_TYPE" = "LITE" ]; then
            nohup bash "$ETK_ROOT/bin/thermal_d.sh" >/dev/null 2>&1 &
        fi

        # --- SIMPLE TELEMETRY: capture session start state for post-mortem ---
        # session_postmortem.sh consumes these on the RUNNING->IDLE edge.
        # All three are ephemeral SHM keys, scoped to this single session.
        date +%s > "$SHM_DIR/session_start.txt"
        cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1 > "$SHM_DIR/battery_start.txt"
        wc -l < "$ETK_ROOT/telemetry.log" 2>/dev/null > "$SHM_DIR/thermal_log_start.txt" || echo 0 > "$SHM_DIR/thermal_log_start.txt"

        # --- PERSISTENT BREADCRUMB ---
        # Twin of session_start.txt that lives in $TELEMETRY_DIR (persistent),
        # not SHM (boot-volatile). If a kernel panic / hard hang takes the rig
        # down before postmortem fires, this file survives the reboot and the
        # Sentry's boot-time orphan-detect (above the main loop) reconstructs
        # the lost session as a PANIC row. Cleaned up by session_postmortem.sh
        # at the end of a normal RUNNING->IDLE rollup. `sync` is one fsync per
        # ignition — cheap insurance that the breadcrumb is on disk before the
        # session starts burning silicon.
        mkdir -p "$TELEMETRY_DIR"
        printf '%s\t%s\t%s\n' "$(date +%s)" "${ID_STR:-UNKNOWN}" "$ETK_BUILD_TYPE" > "$SESSION_ANCHOR"
        sync
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

        # --- SIMPLE TELEMETRY: post-mortem rollup ---
        # Runs in background so this transition stays sub-second. The
        # post-mortem reads SHM seeds captured at IDLE->RUNNING above
        # (still present — SHM survives until next reboot), aggregates
        # the session, appends one row to $SESSIONS_LEDGER, and
        # triggers career_aggregate.sh.
        nohup bash "$ETK_ROOT/bin/session_postmortem.sh" >/dev/null 2>&1 &
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
echo -e "${G}>>> DEPLOYMENT COMPLETE. REBOOT THE DEVICE TO ACTIVATE THE ETK PITSTOP APP IN ROCKNIX TOOLS.${N}"
echo -e "    (EmulationStation reads the Tools gamelist at startup, so the polished"
echo -e "     ETK Pitstop entry appears after a reboot — Update Gamelists does not refresh it.)"
echo -e "    Run ${Y}ssh $RIG_SSH 'systemctl status etk.service'${N} to confirm sentry health."