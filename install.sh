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
        # survive (a ComputerName like "Operator's MacBook Air" would lose
        # words on whitespace awk).
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
# --verbose / -v: bypass the Pit Wall TUI; use raw rsync progress output.
# Useful for diagnosing failed installs (per-file rsync detail surfaces).
# --pair: run ONLY the host->rig SSH pairing wizard, then exit. Use to
# (re)establish passwordless auth without a full install (e.g. after a card
# reflash invalidates the rig's host key / authorized_keys).
RESTORE_STATE=0
DO_PAIR_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --restore-state) RESTORE_STATE=1 ;;
        --verbose|-v) ETK_VERBOSE=1 ;;
        --pair) DO_PAIR_ONLY=1 ;;
        # --sync-mode=MODE overrides etk.conf's VAULT_SYNC for this run only
        # (full|backup|off). See the Tier-A gates in Steps 2 & 4.
        --sync-mode=*) VAULT_SYNC="${arg#*=}" ;;
        --no-vault-sync) VAULT_SYNC="off" ;;
    esac
done
# Normalize + validate the Tier-A shader-sync mode (env.sh already defaulted it
# to 'full' from etk.conf; a --sync-mode flag above may have overridden it).
case "$VAULT_SYNC" in
    full|backup|off) ;;
    *) echo -e "${Y}>>> Unknown VAULT_SYNC='$VAULT_SYNC' — using 'full'.${N}"; VAULT_SYNC="full" ;;
esac

# ==========================================================
# SSH PAIRING (host -> rig)
# Must run BEFORE the TUI activates and BEFORE STEP 0's first ssh, so the
# (at most one) rig password prompt happens cleanly. scripts/etk_pair.sh is
# test-first + idempotent: an already-reachable host pays ZERO passwords; a
# fresh/reflashed rig pairs with exactly one. Without it, every ssh/scp below
# would prompt for the rig password (the bug this wizard exists to kill).
# Single source of truth: the rig-side key-install logic lives once in
# etk_pair.sh and is shared with the PowerShell port via Get-Heredoc.
# ==========================================================
if [ -f "./scripts/etk_pair.sh" ]; then
    bash ./scripts/etk_pair.sh "$RIG_SSH"
    PAIR_RC=$?
    if [ "$DO_PAIR_ONLY" = "1" ]; then
        exit $PAIR_RC
    fi
    if [ "$PAIR_RC" -ne 0 ]; then
        echo -e "${R}>>> SSH pairing failed — cannot reach the rig passwordlessly.${N}"
        echo -e "${R}    Fix the handshake (README / WINDOWS_HOST_README.md), then re-run,${N}"
        echo -e "${R}    or run './install.sh --pair' to retry pairing on its own.${N}"
        exit 1
    fi
elif [ "$DO_PAIR_ONLY" = "1" ]; then
    echo -e "${R}>>> scripts/etk_pair.sh not found — cannot --pair.${N}"
    exit 1
fi

# ==========================================================
# LIVE-SESSION GUARD (operator-directed 2026-07-05)
# NEVER deploy over an active race. STEP 0 kills the telemetry
# daemons and STEP 6 restarts the Sentry, which re-seeds SHM and
# re-fires ignition mid-game — the running session's ledger
# baseline (and the driver's race) are the casualties. Refuse to
# proceed while the emulator is running; finish or exit the game,
# then re-run. The bracketed pgrep pattern defeats the
# ssh-cmdline self-match (the AI_MANIFEST `pgrep -f` trap).
# ==========================================================
if ssh $RIG_SSH 'pgrep -f "AppRun.wrappe[d]|rpcs3-s[a]" >/dev/null 2>&1'; then
    echo -e "${R}>>> A game session is RUNNING on the rig — install refused.${N}"
    echo -e "${R}    Deploying now would kill live telemetry and could cost the${N}"
    echo -e "${R}    current race. Finish or exit the game, then re-run ./install.sh.${N}"
    exit 1
fi

# Source the install-time TUI library (host-only; never pushed to rig).
# Provides tui_step_start, tui_step_progress, tui_step_done, tui_rsync, etc.
# Falls back to no-op stubs / passthrough behaviour in verbose / non-tty mode.
source ./tools/tui.sh 2>/dev/null || true

# Tier-B host snapshot tree (local to install.sh, mirroring how ./vault
# is already a literal here). Never auto-deleted (cf. ./vault philosophy).
HOST_STATE_DIR="./state"

# --- PIPELINE CONTROLS ---
# RSYNC_CMD is the verbose-mode rsync invocation. In TUI mode, tui_rsync
# wraps rsync directly with --info=progress2 and bypasses RSYNC_CMD.
if [ "$ETK_VERBOSE" = "1" ]; then
    RSYNC_CMD="rsync -avzI --progress"
else
    # FIX: Added --modify-window=2 to account for exFAT/FAT32 2-second timestamp drift
    RSYNC_CMD="rsync -az --modify-window=2 --progress"
fi

# tui.sh defaults TUI_TOTAL_STEPS to 6 at source time; override before
# tui_init (which sizes the per-step state arrays) for steps 7 (STAGE3)
# and 8 (PADDOCK LINK).
TUI_TOTAL_STEPS=8
TOTAL_STEPS=8

# --- TUI ACTIVATION ---
# Console mode (Pit Wall TUI) is the default for terminal installs.
# Verbose flag, non-tty stdout, or terminal narrower than 76 cols all
# fall through to the historical raw output path.
if tui_should_activate; then
    tui_init
fi

if [ "$RESTORE_STATE" = "1" ]; then
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "[--restore-state] will OVERWRITE rig Tier-B state on this run"
    else
        echo -e "${R}>>> WARNING: --restore-state passed.${N}"
        echo -e "${R}    Host-side Tier-B state ($HOST_STATE_DIR) will OVERWRITE rig"
        echo -e "    telemetry ledgers, tuned RPCS3 configs, and the RPCS3 user-profile"
        echo -e "    tree (saves, trophies, .rap licenses). Intended ONLY for a"
        echo -e "    fresh/reflashed rig. NEVER run this as part of a routine update.${N}"
        echo ""
    fi
fi

# ==========================================================
# STEP 0: PROBE & QUIESCE
# Kill all ETK worker processes on the rig before any file
# operations to prevent partial-write races during repair/update.
# input_d.py is included because it holds the evdev fd open.
# mango_bridge.sh is included because it writes to SHM continuously.
# The Sentry service itself is left running — it will respawn cleanly.
# ==========================================================
tui_step_start 0
tui_log "Probing rig SSH and quiescing daemons"
ssh $RIG_SSH "pkill -f vault_d.sh; pkill -f thermal_d.sh; pkill -f mango_bridge.sh; pkill -f input_d.py" 2>/dev/null
sleep 1
tui_step_progress 0 15

# --- MESA REBUILD FINGERPRINT (addendum §G.5) ---
# Shader-sweep boundary = the Mesa VERSION the driver reports (e.g. "26.1.3"),
# NOT a byte-fingerprint of the .so. RATIONALE (2026-06-24, evidence-backed):
# Mesa keys its shader disk cache on the per-build GNU build-id, so every
# DRIVER-tab build swap changes the .so bytes yet its cache COEXISTS with the
# other builds' (Mesa reads only its own build-id's entries and ignores the
# rest — dead weight on disk, never a re-compile storm). The old first-64KB
# sha256 bumped the boundary on EVERY swap, falsely staling the very cache you
# swap BACK to (operator confirmed the "stale" shaders were live — no track
# storm). Keying on the Mesa version moves the boundary ONLY on a genuine
# version change (the nightly that bumps 26.1.x -> 26.2.x) — the one event that
# truly invalidates the whole vault. Within a version, swapping builds keeps the
# vault fresh; same-version dead weight is left to Delete Vault, by design. The
# hash file's OWN mtime is the sweep cutoff that tools/vault_sweep.sh reads.
MESA_VER_NEW=$(ssh $RIG_SSH "strings /usr/lib/libvulkan_freedreno.so 2>/dev/null | grep -oE 'Mesa [0-9]+\.[0-9]+\.[0-9]+' | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'")
if [ -n "$MESA_VER_NEW" ]; then
    MESA_HASH_FILE="$ETK_ROOT/vault/.last_mesa.hash"
    MESA_VER_OLD=$(ssh $RIG_SSH "cat $MESA_HASH_FILE 2>/dev/null")
    case "$MESA_VER_OLD" in
        "")
            # First run: seed the version silently. Nothing prior to compare.
            ssh $RIG_SSH "mkdir -p $ETK_ROOT/vault && printf '%s\n' '$MESA_VER_NEW' > $MESA_HASH_FILE"
            ;;
        *.*)
            # Already version-keyed (steady state). Bump ONLY on a real version
            # change — that genuinely orphans the prior-version cache.
            if [ "$MESA_VER_OLD" != "$MESA_VER_NEW" ]; then
                say "${Y}[INFO] MESA $MESA_VER_OLD -> $MESA_VER_NEW — run tools/vault_sweep.sh to prune the prior-version cache${N}"
                ssh $RIG_SSH "echo \"[\$(date '+%H:%M:%S.%N')] MESA VERSION CHANGE (was $MESA_VER_OLD, now $MESA_VER_NEW)\" >> $TRIPWIRE_LOG && printf '%s\n' '$MESA_VER_NEW' > $MESA_HASH_FILE"
            fi
            ;;
        *)
            # Legacy byte-hash boundary (no dot) -> migrate to version-keyed. The
            # whole current vault is the SAME Mesa version and therefore valid, so
            # BACKDATE the boundary mtime so it is retained as fresh, not falsely
            # swept. (touch -t verified on the rig's BusyBox.)
            ssh $RIG_SSH "printf '%s\n' '$MESA_VER_NEW' > $MESA_HASH_FILE && touch -t 200001010000 $MESA_HASH_FILE && echo \"[\$(date '+%H:%M:%S.%N')] BOUNDARY MIGRATED to version-keyed ($MESA_VER_NEW); vault retained as fresh\" >> $TRIPWIRE_LOG"
            say "${Y}[INFO] Shader sweep boundary upgraded to version-keyed ($MESA_VER_NEW) — DRIVER build swaps no longer stale the vault${N}"
            ;;
    esac
fi
tui_step_progress 0 30

# --- SCREENSHOT FEATURE DEPENDENCY CHECK ---
# `grim` is the Wayland screenshot tool that bin/screenshot.sh shells out
# to. It ships with Rocknix today, but a future nightly could strip it
# and the L1 chord would then silently produce zero output. Probe at
# install time so any breakage surfaces here, not at first L1 press.
if ! ssh $RIG_SSH "command -v grim >/dev/null 2>&1"; then
    say "${Y}[WARN] grim not available on rig — L1 screenshot disabled until reinstalled${N}"
fi
tui_step_progress 0 40

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
if [ "$TUI_ACTIVE" = "1" ]; then
    tui_log "Rig ID: ${TARGET_ID}  •  Vault: $VAULT_DIR"
else
    echo -e "    RIG ID   : ${Y}${TARGET_ID}${N}"
    echo -e "    VAULT DIR: ${Y}${VAULT_DIR}${N}"
fi
tui_step_progress 0 55

# ==========================================================
# STEP 1: PROVISION RIG DIRECTORIES
# Idempotent mkdir -p — safe to run on repair/update.
# Provisions the correct RPCS3 custom config path (bios tree),
# not the legacy /storage/.config/rpcs3 path.
# ==========================================================
tui_log "Provisioning rig directories ($ETK_ROOT, modules, RPCS3 dirs)"
ssh $RIG_SSH "mkdir -p \
    $VAULT_DIR \
    $ETK_ROOT/bin \
    $ETK_ROOT/scripts \
    $ETK_ROOT/tools \
    $ETK_ROOT/vault \
    $ETK_ROOT/logs \
    $ETK_ROOT/config \
    $ETK_ROOT/screenshots \
    $ETK_ROOT/pro-tuning \
    $PKG_STAGING_DIR \
    $FIRMWARE_DROP_DIR \
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
tui_step_progress 0 70

# --- STEP 1b: RPCS3 config-dir links (fresh-rig data-loss guard) ---
# ROCKNIX's start_rpcs3.sh points RPCS3's config dir at the games tree:
#   rm -rf /storage/.config/rpcs3/$F ; ln -sf /storage/roms/bios/rpcs3/$F ...
# for dev_flash/dev_hdd0/dev_hdd1/custom_configs — but it ONLY runs at GAME
# LAUNCH. Pitstop's firmware + PKG installers invoke RPCS3 directly, so on a
# rig that has never launched a game the links don't exist yet: RPCS3 creates
# its VFS tree as REAL directories under /storage/.config/rpcs3/ and installs
# there, and the first game launch rm -rf's the lot. Observed live 2026-07-24
# on a fresh rig (firmware 4.93 + a 706 MB game, both one launch from deletion).
# Making the links here means a fresh rig is correct before Pitstop is ever
# opened. Conservative by design: we never move or delete user data — a
# non-empty real directory is reported and left for Pitstop's installer
# pre-flight (_ensure_rpcs3_storage_links), which migrates it safely.
tui_log "Linking RPCS3 config dirs to the games tree"
RPCS3_LINK_REPORT=$(ssh $RIG_SSH "sh -s" <<'RPCS3LINKS'
CFG=/storage/.config/rpcs3
BIOS=/storage/roms/bios/rpcs3
# Absent config dir = ROCKNIX has not seeded it. Creating it here would make
# start_rpcs3.sh's `if [ ! -d "$CFG" ]` skip its cp of the stock config.yml.
[ -d "$CFG" ] || { echo "SKIP no rpcs3 config dir yet"; exit 0; }
for F in dev_flash dev_hdd0 dev_hdd1 custom_configs; do
    S="$CFG/$F"; T="$BIOS/$F"
    mkdir -p "$T"
    if [ -L "$S" ]; then continue; fi
    if [ ! -e "$S" ]; then ln -sf "$T" "$S"; echo "LINKED $F"; continue; fi
    if [ ! -d "$S" ]; then echo "WARN $F is a file, not a folder"; continue; fi
    # Real directory: safe to replace only when it holds nothing.
    if rmdir "$S" 2>/dev/null; then ln -sf "$T" "$S"; echo "LINKED $F"
    else echo "HOLD $F has data in $S"; fi
done
RPCS3LINKS
)
for LINE in $(echo "$RPCS3_LINK_REPORT" | grep '^HOLD' | awk '{print $2}'); do
    tui_log "  NOTE: /storage/.config/rpcs3/$LINE holds data - Pitstop's"
    tui_log "        installer will migrate it into the games tree on next use"
done
tui_step_progress 0 80

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

# Document the PS3 firmware drop folder. Firmware is a one-time, system-wide
# prerequisite (commercial PS3 games refuse to boot without it). Unlike the
# .pkg staging folder, the .pup is KEPT after install so it can be reused.
ssh $RIG_SSH "cat > '$FIRMWARE_DROP_DIR/README.txt'" <<'FWREADME'
ETK PITSTOP - PS3 FIRMWARE INSTALL DROP FOLDER
==============================================

Commercial PS3 games need Sony's PS3 firmware installed once before
they will boot. This is a legal, free download from Sony.

1. On any computer, download the PS3 system-software update file
   (it is named  PS3UPDAT.PUP ) from Sony's official page:
     https://www.playstation.com/en-us/support/hardware/ps3/system-software/

2. Copy that PS3UPDAT.PUP into THIS folder (over the SMB share, or
   straight onto the SD card).

3. On the handheld:
     Tools > ETK Pitstop > TOOLS tab > Install PS3 Firmware

RPCS3 installs the firmware headless, in the background (about a
minute) - no on-screen dialog to confirm. You only do this once;
the firmware is shared by every PS3 game.

The PS3UPDAT.PUP file is KEPT here after installing (it is reusable
and survives an ETK reinstall). You can delete it yourself once the
firmware is in, if you want the space back.
FWREADME

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
# Rocknix's own notifications. REWRITTEN IN PLACE on every install (was
# append-once) so a restyle/reposition actually reaches already-installed rigs:
# strip any prior ETK block (header -> next [section]/EOF), then append the
# current one. anchor=top-center + a top margin drops the toast below the top
# HUD band (operator-approved 2026-07-07) so it no longer hugs the top edge.
# Output captured + summarized so the [OK]/[SKIP] echo doesn't break the TUI.
tui_log "Installing ETK mako notification style"
MAKO_OUT=$(ssh $RIG_SSH 'bash -s' 2>&1 <<'ETKMAKO'
MCFG=/storage/.config/mako/config
if [ -f "$MCFG" ]; then
awk '
  /^\[app-name="ETK Pitstop"\]/ { skip=1; next }
  skip && /^\[/ { skip=0 }
  !skip { print }
' "$MCFG" > "$MCFG.etk.tmp"
cat >> "$MCFG.etk.tmp" <<'MK'

[app-name="ETK Pitstop"]
anchor=top-center
width=1280
height=560
font=monospace 24
default-timeout=8000
padding=20
margin=240,24,24,24
border-size=3
border-color=#00b4d8
border-radius=14
text-alignment=left
background-color=#10141a
MK
mv "$MCFG.etk.tmp" "$MCFG"
XDG_RUNTIME_DIR=/var/run/0-runtime-dir makoctl reload 2>/dev/null
echo "    [OK] ETK mako notification style installed/updated."
else
echo "    [SKIP] mako config absent."
fi
ETKMAKO
)
if [ "$TUI_ACTIVE" = "1" ]; then
    if echo "$MAKO_OUT" | grep -q '\[OK\]'; then
        tui_log "ETK mako notification style installed"
    else
        tui_log "ETK mako style already present"
    fi
else
    echo "$MAKO_OUT"
fi
tui_step_done 0

# ==========================================================
# STEP 2: VAULT PULL — RIG -> HOST PC (SAFEGUARD HARVEST)
# Always pull before pushing so no session's banked shaders
# are lost if the rig is about to be reflashed or repaired.
# --ignore-existing: shader files are content-fixed (cache key
# == filename), so a banked .bin is immutable; never overwrite a
# host copy with a rig copy of the same name.
# ==========================================================
tui_step_start 1
say "${C}[INFO] Shader sync mode: ${VAULT_SYNC} (full=pull+push, backup=pull-only, off=none)${N}"
# --- INTERNAL-STORAGE DETECTION (Tier A/B) ---
# On an internal-boot rig, per-game vault dirs (Tier A) are symlinks into
# /storage/etk-internal. The vault rsyncs below use --copy-links on PULL and
# --keep-dirlinks on PUSH so the sync is symlink-safe; both are no-ops on an
# SD-only rig and so are applied unconditionally. This probe is informational.
ETK_INTERNAL_VAULT=$(ssh $RIG_SSH "find $ETK_ROOT/vault/$CHIPSET -maxdepth 1 -type l 2>/dev/null | head -1")
if [ -n "$ETK_INTERNAL_VAULT" ]; then
    say "${C}[INFO] Internal-storage vault detected — symlink-safe sync engaged${N}"
fi
# Scoped to $CHIPSET/ so anything outside the structured
# vault/$CHIPSET/<gameID>/shaders/ tree (e.g. RPCS3 ppu-* per-binary
# caches that landed at vault root from a legacy tool or a manual op)
# cannot propagate between rig and host on subsequent installs.
# --copy-links: if a per-game vault dir is a Tier A symlink into internal UFS,
# dereference it so the HOST archives the REAL shaders, not a dangling link
# pointing at a rig-only path. No-op when the dir is a plain dir (SD rig).
mkdir -p "./vault/$CHIPSET"
# Tier-A shader PULL (rig -> host harvest). Runs in full + backup modes; the
# only mode that skips it is 'off' (Private Paddock owns the shader archive).
if [ "$VAULT_SYNC" = "off" ]; then
    say "${Y}[SKIP] Tier-A shader pull — VAULT_SYNC=off (Paddock owns shader backup)${N}"
    tui_step_progress 1 70
else
    tui_rsync 1 0 70 "Pulling vault shaders (rig → host)" --copy-links --ignore-existing --exclude='.DS_Store' "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/" "./vault/$CHIPSET/"
fi

# --- TIER-B BACKUP: rig -> host (addendum §C) ---
# Mutable-precious state: telemetry ledgers, tuned RPCS3 configs, RPCS3
# user-profile tree (saves/trophies/.rap licenses). Plain mirror (no
# --ignore-existing — host must track edits; no --delete — host snapshot is
# an archive, never pruned). Read-only on the rig: zero SD writes. Skipped
# per-rule when the rig source is absent (fresh rig, first run).
tui_log "Tier-B backup: telemetry, configs, RPCS3 home, screenshots (rig → host)"
mkdir -p "$HOST_STATE_DIR/etk_telemetry" "$HOST_STATE_DIR/custom_configs" "$HOST_STATE_DIR/rpcs3_home" "$HOST_STATE_DIR/screenshots"
TIER_B_IDX=0
for pair in \
    "$TELEMETRY_DIR|$HOST_STATE_DIR/etk_telemetry|telemetry" \
    "$RPCS3_CUSTOM_CONFIGS|$HOST_STATE_DIR/custom_configs|custom_configs" \
    "$RPCS3_HOME_DIR|$HOST_STATE_DIR/rpcs3_home|rpcs3_home" \
    "$ETK_ROOT/screenshots|$HOST_STATE_DIR/screenshots|screenshots"; do
    SRC="${pair%%|*}"
    REST="${pair#*|}"
    DST="${REST%%|*}"
    NAME="${REST##*|}"
    TIER_B_IDX=$((TIER_B_IDX + 1))
    PCT_LO=$(( 70 + (TIER_B_IDX - 1) * 7 ))
    PCT_HI=$(( 70 + TIER_B_IDX * 7 ))
    if ssh $RIG_SSH "[ -d $SRC ]" 2>/dev/null; then
        # Exclude the big GPU-hang redumps (.rd, hundreds of MB each) + kernel
        # devcoredumps from the Tier-B mirror: they bloat every sync (re-pulling
        # ~900 MB redumps on each deploy) and are decoded in-container, not
        # re-read from the host backup. The tiny .faultinfo sidecars stay synced.
        tui_rsync 1 $PCT_LO $PCT_HI "Mirroring $NAME" --exclude='.DS_Store' --exclude='*.rd' --exclude='*.devcd' --exclude='crash_forensics' --exclude='forensics' "$RIG_SSH:$SRC/" "$DST/"
    else
        say "${Y}[SKIP] rig source absent: $SRC${N}"
        tui_step_progress 1 $PCT_HI
    fi
done
# RECENT_ID_FILE lives at vault root (outside the $CHIPSET/ scope Step 2
# pulls). Tiny, mutable, and folding it in saves one wrong-config flash on
# the first launch after --restore-state (seed_id falls back to NPUA80075
# otherwise — wrong game's HUD position, wrong cache pre-seed link).
if ssh $RIG_SSH "[ -f $RECENT_ID_FILE ]" 2>/dev/null; then
    tui_rsync 1 98 100 "Pulling last_played_id" --exclude='.DS_Store' "$RIG_SSH:$RECENT_ID_FILE" "./vault/last_played_id.txt"
fi

# --- FORENSICS OFFLOAD: rig -> host MOVE (operator-directed 2026-07-04) ---
# Heavy crash artifacts (hangrd redumps 600MB+, devcoredumps, teardown bts)
# have no reason to live on the rig: every deploy runs this installer, all
# analysis happens on the host, and rig storage must stay lean for card
# swaps. rsync --remove-source-files deletes ONLY files that transferred
# verified; the dirs stay (spotter/postmortem recreate content freely).
# First run moves the accumulated backlog (~13GB, one-time; subsequent runs
# are a trickle). Excluded from the Tier-B mirror above so nothing is
# double-archived. Cores are NOT archived (2-7GB each; superseded by the
# live-gdb doctrine) — pruned to newest 1 here, keep-2 per-crash in the
# postmortem, boot sweep in 02-etk-coredump.sh.
mkdir -p "$HOST_STATE_DIR/forensics/crash_forensics" "$HOST_STATE_DIR/forensics/forensics"
for FDIR in "$TELEMETRY_DIR/crash_forensics" "$TELEMETRY_DIR/forensics"; do
    FBASE=$(basename "$FDIR")
    if ssh $RIG_SSH "[ -d $FDIR ] && ls $FDIR 2>/dev/null | grep -q ." 2>/dev/null; then
        FSIZE_KB=$(ssh $RIG_SSH "du -k $FDIR 2>/dev/null | tail -1 | awk '{print \$1}'")
        case "$FSIZE_KB" in ''|*[!0-9]*) FSIZE_KB=0 ;; esac
        tui_log "Offloading $FBASE (rig → host, $((FSIZE_KB / 1024))MB, move+prune)"
        if [ "$TUI_ACTIVE" = "1" ]; then
            rsync -az --remove-source-files --exclude='.DS_Store' "$RIG_SSH:$FDIR/" "$HOST_STATE_DIR/forensics/$FBASE/" >/dev/null 2>&1
        else
            $RSYNC_CMD --remove-source-files --exclude='.DS_Store' "$RIG_SSH:$FDIR/" "$HOST_STATE_DIR/forensics/$FBASE/"
        fi
    fi
done
ssh $RIG_SSH "ls -t $ETK_ROOT/cores/*.core 2>/dev/null | tail -n +2 | xargs -r rm -f; ls -t /storage/cores/*.core 2>/dev/null | tail -n +2 | xargs -r rm -f" 2>/dev/null
tui_step_done 1

# ==========================================================
# STEP 3: DEPLOY SCRIPTS, DAEMONS & OVERLAYS
# --delete removes stale files from previous versions so
# renamed/removed scripts don't linger and cause ghost invocations.
# ==========================================================
tui_step_start 2
tui_rsync 2 0 25 "Pushing /bin daemons" --delete --exclude='*.pyc' --exclude='__pycache__' --exclude='.DS_Store' ./bin/ $RIG_SSH:$ETK_ROOT/bin/
tui_rsync 2 25 45 "Pushing /scripts (env, profiles, probes)" --delete --exclude='.DS_Store' ./scripts/ $RIG_SSH:$ETK_ROOT/scripts/
# Two tools/ entries run ON the rig, so they are explicit single-file pushes
# (the rest of tools/ is host-side — tui.sh is sourced by install.sh, the probes
# run from the host — so we do NOT push ./tools/ wholesale):
#   - etk_drift.py    : OS-drift detector (reads /sys, banks OS profiles under
#                       $ETK_ROOT/vault/os_profiles).
#   - vault_sweep.sh  : the Manage Shaders engine. etk_pitstop.py (on the rig)
#                       shells out to it for --porcelain (fresh/stale graph) and
#                       --apply (Sweep). MUST be deployed or the screen shows
#                       "no boundary" (exit 127 on a missing script). uninstall.sh
#                       does `rm -rf $ETK_ROOT/tools`, so a one-off scp does not
#                       survive a reinstall — it has to live here.
tui_rsync 2 45 52 "Pushing tools/etk_drift.py (OS-drift detector)" --exclude='.DS_Store' ./tools/etk_drift.py $RIG_SSH:$ETK_ROOT/tools/etk_drift.py
tui_rsync 2 52 56 "Pushing tools/vault_sweep.sh (Manage Shaders engine)" --exclude='.DS_Store' ./tools/vault_sweep.sh $RIG_SSH:$ETK_ROOT/tools/vault_sweep.sh
# wl-mirror: aarch64/glibc screen-mirror binary used by bin/dpmirror_d.sh to
# duplicate the internal panel onto the external DisplayPort for capture/OBS.
# Same "tools/ is rm -rf'd on uninstall" reasoning as the two tools above —
# it must be pushed here, not one-off scp'd. Rebuild via tools/rocknix-bin/build_wl_mirror.sh.
tui_rsync 2 56 58 "Pushing tools/wl-mirror (DP capture-mirror binary)" --exclude='.DS_Store' ./tools/rocknix-bin/wl-mirror $RIG_SSH:$ETK_ROOT/tools/wl-mirror

# chiaki: aarch64/glibc PS4/PS5 Remote Play client (SDL2 frontend built from
# the ETK chiaki fork; rebuild via tools/rocknix-bin/build_chiaki.sh, which
# also verifies the NEEDED sonames against the rig). Same "tools/ is rm -rf'd
# on uninstall" push-list reasoning as wl-mirror. Gated on ETK_CHIAKI (etk.conf).
if [ "${ETK_CHIAKI:-1}" = "1" ]; then
    tui_rsync 2 58 59 "Pushing tools/chiaki (Remote Play client)" --exclude='.DS_Store' ./tools/rocknix-bin/chiaki $RIG_SSH:$ETK_ROOT/tools/chiaki
fi
tui_rsync 2 58 70 "Pushing MangoHud.conf" --exclude='.DS_Store' ./config/MangoHud.conf $RIG_SSH:/storage/.config/MangoHud/MangoHud.conf
# Operator config: the Sentry's env.sh source on the rig reads this file
# for ETK_BUILD_TYPE, DEFAULT_MODE, HUD_HEADER_HOLD_S. Without this push
# the rig would silently use the baked-in defaults regardless of operator
# edits on the host. --checksum forces content-based comparison because
# operator edits often swap byte-identical values (HUD_HEADER_HOLD_S=15
# vs =42 are the same size) and rsync's default mtime+size check would
# silently skip such "same-size" edits when mtimes happen to match.
tui_rsync_checksum 2 70 90 "Pushing etk.conf (operator config)" --exclude='.DS_Store' ./etk.conf $RIG_SSH:$ETK_ROOT/etk.conf
tui_log "chmod +x daemons and scripts"
ssh $RIG_SSH "chmod +x $ETK_ROOT/bin/* $ETK_ROOT/scripts/* $ETK_ROOT/tools/etk_drift.py $ETK_ROOT/tools/vault_sweep.sh $ETK_ROOT/tools/wl-mirror; [ -f $ETK_ROOT/tools/chiaki ] && chmod +x $ETK_ROOT/tools/chiaki || true"
tui_step_done 2

# ==========================================================
# STEP 4: VAULT PUSH — HOST PC -> RIG (RESTORE BANKED SHADERS)
# Pushes any shaders from the host vault that the rig doesn't
# have yet. This is the repair/post-flash recovery path that
# saves re-harvesting after an OS reflash or SD card swap.
# --ignore-existing: same reasoning as Step 2 — shader files are
# content-fixed; we only add what the rig is missing, never overwrite.
# Bidirectional --ignore-existing is the property that makes the
# Tier-A vault sync safe to run on every install (addendum §B).
# --keep-dirlinks: when a per-game vault dir on the rig is a Tier A symlink
# into internal UFS, treat it as the directory it points at and sync INTO the
# internal target — never replace the symlink with a real dir (which would
# de-internalize the vault). No-op when the dir is a plain dir (SD rig).
# ==========================================================
tui_step_start 3
# Tier-A shader PUSH (host -> rig restore). ONLY in full mode. In backup/off the
# host never pushes shaders back, so a stale host vault can't re-seed the rig
# after a Mesa bump (the host-vault-staleness gap) — Private Paddock owns restore.
# Scoped to $CHIPSET/ — see step 2 rationale.
if [ "$VAULT_SYNC" != "full" ]; then
    say "${Y}[SKIP] Tier-A shader push (host → rig) — VAULT_SYNC=$VAULT_SYNC${N}"
    tui_step_progress 3 100
elif [ -d "./vault/$CHIPSET" ] && [ "$(ls -A "./vault/$CHIPSET" 2>/dev/null)" ]; then
    tui_rsync 3 0 100 "Pushing vault shaders (host → rig)" --keep-dirlinks --ignore-existing --exclude='.DS_Store' "./vault/$CHIPSET/" "$RIG_SSH:$ETK_ROOT/vault/$CHIPSET/"
else
    say "${Y}[SKIP] No local vault found — nothing to push${N}"
    tui_step_progress 3 100
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
    tui_log "[Tier-B RESTORE] Overwriting rig state with host snapshot"
    for pair in \
        "$HOST_STATE_DIR/etk_telemetry|$TELEMETRY_DIR|telemetry" \
        "$HOST_STATE_DIR/custom_configs|$RPCS3_CUSTOM_CONFIGS|custom_configs" \
        "$HOST_STATE_DIR/rpcs3_home|$RPCS3_HOME_DIR|rpcs3_home" \
        "$HOST_STATE_DIR/screenshots|$ETK_ROOT/screenshots|screenshots"; do
        SRC="${pair%%|*}"
        REST="${pair#*|}"
        DST="${REST%%|*}"
        NAME="${REST##*|}"
        if [ -d "$SRC" ] && [ "$(ls -A "$SRC" 2>/dev/null)" ]; then
            tui_log "Restoring $NAME (host → rig OVERWRITE)"
            if [ "$TUI_ACTIVE" = "1" ]; then
                rsync -az --info=progress2 --info=stats0 --exclude='.DS_Store' "$SRC/" "$RIG_SSH:$DST/" >/dev/null 2>&1
            else
                $RSYNC_CMD --exclude='.DS_Store' "$SRC/" "$RIG_SSH:$DST/"
            fi
        else
            say "${Y}[SKIP] host source empty: $SRC${N}"
        fi
    done
    if [ -f "./vault/last_played_id.txt" ]; then
        if [ "$TUI_ACTIVE" = "1" ]; then
            rsync -az --exclude='.DS_Store' "./vault/last_played_id.txt" "$RIG_SSH:$RECENT_ID_FILE" >/dev/null 2>&1
        else
            $RSYNC_CMD --exclude='.DS_Store' "./vault/last_played_id.txt" "$RIG_SSH:$RECENT_ID_FILE"
        fi
    fi
fi
tui_step_done 3

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
tui_step_start 4

# Python engine and field schema
tui_rsync 4 0 12 "Deploying Pitstop engine (etk_pitstop.py)" --exclude='.DS_Store' ./bin/etk_pitstop.py            $RIG_SSH:$ETK_ROOT/bin/
tui_rsync 4 12 24 "Deploying field schema (pitstop_fields.json)" --exclude='.DS_Store' ./config/pitstop_fields.json    $RIG_SSH:$ETK_ROOT/config/
tui_rsync 4 24 30 "Deploying POWER schema (power_profiles.json)" --exclude='.DS_Store' ./config/power_profiles.json   $RIG_SSH:$ETK_ROOT/config/
tui_rsync 4 30 36 "Deploying crash signatures" --exclude='.DS_Store' ./config/crash_signatures.json  $RIG_SSH:$ETK_ROOT/config/
# Save-folder aliases (paddock_sync.sh). Never clobber the operator's edits —
# this file is theirs to extend when a game stores saves under a foreign name.
ssh $RIG_SSH "[ -f '$ETK_ROOT/config/save_aliases.tsv' ]" 2>/dev/null \
    || tui_rsync 4 30 36 "Deploying save-folder aliases" --exclude='.DS_Store' ./config/save_aliases.tsv $RIG_SSH:$ETK_ROOT/config/
# HUD punchbox masters (bin/hud_apply.sh reads these): the pristine ETK DDU
# conf + the minimal "default" preset, both to $ETK_ROOT/config/.
tui_rsync 4 36 37 "Deploying HUD punchbox master" --exclude='.DS_Store' ./config/MangoHud.conf $RIG_SSH:$ETK_ROOT/config/
tui_rsync 4 37 38 "Deploying HUD default preset" --exclude='.DS_Store' ./config/MangoHud.default.conf $RIG_SSH:$ETK_ROOT/config/
# ETK default per-game RPCS3 config template (TOOLS-tab installer copies this
# to custom_configs/config_<ID>.yml for each newly installed game).
tui_rsync 4 36 48 "Deploying RPCS3 template (etk_template.yml)" --exclude='.DS_Store' ./config/etk_template.yml       $RIG_SSH:$ETK_ROOT/config/
# SVG icon master for the polished Tools-menu app entry (dossier addendum R1).
tui_rsync 4 48 54 "Deploying Tools-menu SVG icon" --exclude='.DS_Store' ./config/etk_pitstop.svg        $RIG_SSH:$ETK_ROOT/config/

# Chiaki Tools-menu masters (launcher + icon; the modules injector mirrors
# them into boot-volatile /storage/.config/modules every boot). CRLF-strip
# and chmod for the same Windows-checkout reason as the pitstop launcher.
if [ "${ETK_CHIAKI:-1}" = "1" ]; then
    tui_rsync 4 54 55 "Deploying Chiaki Tools-menu icon" --exclude='.DS_Store' ./config/etk_chiaki.svg $RIG_SSH:$ETK_ROOT/config/
    tui_rsync 4 55 56 "Deploying Chiaki launcher master" --exclude='.DS_Store' ./config/etk_chiaki.sh $RIG_SSH:$ETK_ROOT/config/
    ssh $RIG_SSH "sed -i 's/\r$//' $ETK_ROOT/config/etk_chiaki.sh && chmod +x $ETK_ROOT/config/etk_chiaki.sh"
fi

# PADDOCK rig-side code. The PADDOCK tab (Private Paddock, 0.3.0) shells out to
# bin/paddock_sync.sh — the push/pull sync engine — which ships in the bulk
# ./bin/ rsync above. install-protune.sh (deployed below) is the known_repo
# GET-hatch injector: it installs a MISSING game from a pkg/rap source the
# operator supplied. One trust invariant spans both — PADDOCK only ever RUNS
# these two scripts; everything else it handles is data. The host-only producer
# (export.sh) is intentionally NOT deployed to
# the rig. CRLF-strip so a Windows checkout can't ship a script the rig's
# /bin/sh chokes on.
tui_rsync 4 54 58 "Deploying PADDOCK injector (install-protune.sh)" --exclude='.DS_Store' ./pro-tuning/install-protune.sh $RIG_SSH:$ETK_ROOT/pro-tuning/
ssh $RIG_SSH "sed -i 's/\r$//' $ETK_ROOT/pro-tuning/install-protune.sh && chmod +x $ETK_ROOT/pro-tuning/install-protune.sh"

# PADDOCK known_repo TEMPLATE only. The real config/paddock_repos.json is
# operator-created (pkg/rap URLs they own), gitignored, and NEVER deployed or
# published — we ship just the .example so the schema is on the rig to copy.
tui_rsync 4 58 60 "Deploying known_repo template (paddock_repos.json.example)" --exclude='.DS_Store' ./config/paddock_repos.json.example $RIG_SSH:$ETK_ROOT/config/

# THE MASTER COPY: Push to the persistent safe zone
tui_rsync 4 60 72 "Deploying launcher master copy" --exclude='.DS_Store' ./config/etk_pitstop.sh      $RIG_SSH:$ETK_ROOT/config/
ssh $RIG_SSH "sed -i 's/\r$//' $ETK_ROOT/config/etk_pitstop.sh && chmod +x $ETK_ROOT/config/etk_pitstop.sh"

# Deploy the initial launcher directly to modules for immediate use without reboot
tui_rsync 4 72 80 "Installing launcher into /storage/.config/modules/" --exclude='.DS_Store' ./config/etk_pitstop.sh $RIG_SSH:/storage/.config/modules/etk_pitstop.sh

# THE KILL SHOT: Sanitize, Arm, and Weld to Disk
ssh $RIG_SSH "sed -i 's/\r$//' /storage/.config/modules/etk_pitstop.sh && \
              chmod +x /storage/.config/modules/etk_pitstop.sh && \
              sync"
tui_step_progress 4 85

# Verify the launcher actually landed
LAUNCHER_CHECK=$(ssh $RIG_SSH "[ -x /storage/.config/modules/etk_pitstop.sh ] && echo OK || echo MISSING")
if [ "$LAUNCHER_CHECK" = "OK" ]; then
    say "${G}[OK] Launcher verified and locked to disk${N}"
else
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_fail "Launcher missing or lacks +x permissions"
    else
        echo -e "    ${R}[FAIL] Launcher missing or lacks +x permissions — aborting.${N}"
        exit 1
    fi
fi

# Register ETK Pitstop as a polished Tools-menu app: run the modules
# injector so the launcher + SVG icon + enriched gamelist <game> entry
# land immediately. /storage/.config/modules is boot-volatile, so the
# Sentry re-asserts all three every boot — this is just the first pass.
tui_log "Registering ETK Pitstop in the Tools menu"
ssh $RIG_SSH "ETK_CHIAKI=${ETK_CHIAKI:-1} python3 $ETK_ROOT/bin/etk_modules_inject.py" >/dev/null 2>&1
GAMELIST_CHECK=$(ssh $RIG_SSH "grep -c '>ETK Pitstop<' /storage/.config/modules/gamelist.xml 2>/dev/null || echo 0")
if [ "${GAMELIST_CHECK:-0}" -ge 1 ] 2>/dev/null; then
    say "${G}[OK] ETK Pitstop registered as a Tools-menu app${N}"
else
    say "${Y}[WARN] Tools-menu entry not confirmed — Sentry will re-inject it${N}"
fi
if [ "${ETK_CHIAKI:-1}" = "1" ]; then
    CHIAKI_CHECK=$(ssh $RIG_SSH "grep -c '>Chiaki Remote Play<' /storage/.config/modules/gamelist.xml 2>/dev/null || echo 0")
    if [ "${CHIAKI_CHECK:-0}" -ge 1 ] 2>/dev/null; then
        say "${G}[OK] Chiaki Remote Play registered as a Tools-menu app${N}"
    else
        say "${Y}[WARN] Chiaki Tools-menu entry not confirmed — Sentry will re-inject it${N}"
    fi
fi
tui_step_done 4
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
tui_step_start 5
tui_log "Writing Sentry script + systemd unit, restarting etk.service"

# Capture the entire heredoc output so the TUI isn't clobbered by remote
# bash echoes (daemon-reload messages, the SENTRY_OK marker, etc.).
# Use a temp file (not $(...) command substitution) because the Sentry
# heredoc body contains literal `)` characters in ${VAR:-x} expansions
# and case-statement bodies that confuse bash's parser when nested inside
# command substitution. In verbose mode we replay the capture after for
# fidelity with the historical install.sh behaviour.
SENTRY_OUTPUT_FILE=$(mktemp /tmp/etk_sentry_XXXXXX)
ssh $RIG_SSH > "$SENTRY_OUTPUT_FILE" 2>&1 << 'REMOTE'
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

# --- DROP FOLDERS (every boot, any install path) ---
# These were created ONLY by install.sh STEP 1, so a rig built from the
# flashable card image — which never runs install.sh — came up without them.
# The documented flow is "copy your .pup/.pkg in over SMB, THEN open Pitstop",
# so the folders have to exist before the user goes looking. Found on a 0.8.1
# card; 0.8.0 shipped the same gap. Idempotent, and it self-heals a card
# flashed before this landed.
mkdir -p "$PKG_STAGING_DIR" "$FIRMWARE_DROP_DIR" 2>/dev/null

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
# BusyBox-safe: readlink -f, rsync, ln -sfn, [ -L ], [ -d ].
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
        # -n (no-dereference): belt-and-suspenders for the rm above. If that rm
        # ever fails to drop the symlink (TOCTOU / re-entry), a bare `ln -sf`
        # would dereference the still-present symlink-to-dir and drop
        # "$DESIRED/shaders -> $DESIRED" INSIDE the vault — a self-loop that
        # recurses until the 40-link kernel limit and HANGS install.sh's
        # --copy-links vault PULL (rsync "Too many levels of symbolic links").
        # -n replaces the link atomically instead of writing inside it.
        # (vault-loop regression fix, 2026-06-21.)
        ln -sfn "$DESIRED" "$RPCS3_CACHE_DIR"
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
       && ! pgrep -f "rpcs3-sa|AppRun.wrapped" >/dev/null; then

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
            printf 'epoch\tduration_s\tbuild\tgame_id\tstatus\tpeak_load\tpeak_ram_mb\tpeak_temp\tavg_temp\tcrash_sig\tfence_at_crash\tshaders_harvested\tdrain_pct\tthermal_overrides\ttune_tag\n' > "$TMP"
            mv "$TMP" "$SESSIONS_LEDGER"
        fi

        # Col 15 (tune_tag) carries the stack attribution. Orphan rows stopped at
        # col 14 historically, so every kernel-panic session in the ledger is
        # unattributable — and a panic is precisely the session you most want
        # pinned to a stack. Trailing columns stay absent, which the readers
        # already tolerate ("cols 15+ are append-only-trailing").
        ORPHAN_TUNE=$(head -n1 "$ACTIVE_TUNE_FILE" 2>/dev/null | tr -d '\t\r')
        [ -z "$ORPHAN_TUNE" ] && ORPHAN_TUNE="default"
        ORPHAN_TUNE="$(etk_attribution_tag);${ORPHAN_TUNE}"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$ROW_EPOCH" "$ORPHAN_DURATION" "$BC_BUILD" "$BC_GAME" "PANIC" \
            "0.0" "$ORPHAN_PEAK_RAM" "0" "0" \
            "PANIC_REBOOT" "0" "0" "0" "0" "$ORPHAN_TUNE" \
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
    CHIAKI_STALE=0
    if [ "${ETK_CHIAKI:-1}" = "1" ]; then
        if [ ! -f "/storage/.config/modules/etk_chiaki.sh" ] \
           || [ ! -f "/storage/.config/modules/etk_chiaki.svg" ] \
           || ! grep -q '>Chiaki Remote Play<' "$MODULES_GAMELIST" 2>/dev/null; then
            CHIAKI_STALE=1
        fi
    fi
    if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ] \
       || [ ! -f "/storage/.config/modules/etk_pitstop.svg" ] \
       || [ ! -f "$MODULES_GAMELIST" ] \
       || ! grep -q '>ETK Pitstop<' "$MODULES_GAMELIST" 2>/dev/null \
       || [ "$CHIAKI_STALE" = "1" ]; then
        echo "[$(date '+%H:%M:%S.%N')] modules wiped/stale -- re-injecting ETK Tools entries" >> "$TRIPWIRE_LOG"
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
    # Match the emulator by cmdline (-f) on a token specific enough to NOT
    # match an rpcs3 PATH. The bare "rpcs3" pattern also matched any process
    # whose argv referenced /storage/.cache/rpcs3/RPCS3.log -- notably
    # session_postmortem.sh's old `strings "$RPCS3_LOG"` -- which on a verbose
    # game (Ridge Racer 7's log hits ~288MB) outlived a 2s tick and self-
    # ignited phantom ABORTED sessions. "rpcs3-sa" is the actual
    # /usr/bin/rpcs3-sa binary: it appears in the emulator's launch argv but
    # never in the log path. (AppRun.wrapped covers older AppImage builds.)
    # DO NOT use `pgrep -x rpcs3` -- /usr/bin/rpcs3-sa is a static ELF whose
    # comm is "rpcs3-sa", so exact-comm match misses it and ignition never
    # fires (VAULT:ERROR / thermal-WAIT regression, caught 2026-05-30).
    if pgrep -f "rpcs3-sa|AppRun.wrapped" > /dev/null; then
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

        # --- PER-GAME HUD PUNCHBOX STATE ---
        # Restore this game's remembered HUD state (top/bottom/default/off) via
        # the shared applier (bin/hud_apply.sh), which writes the live conf AND
        # signals MangoHud reload — the same path R1+L3 uses, so the two never
        # drift. The 4s sleep above gives MangoHud time to open its control
        # socket as RPCS3 initialises Vulkan; if it isn't up yet the conf is
        # still correct on disk for the next launch. R1+L3 (bin/input_d.py)
        # cycles + persists the state per-game to $HUD_STATE_FILE. Detached +
        # fail-silent so it can NEVER stall ignition. Default = bottom.
        if [ -f "$HUD_STATE_FILE" ] && [ -n "$ID_STR" ]; then
            PREF_STATE=$(awk -F'\t' -v gid="$ID_STR" '$1==gid {print $2; exit}' "$HUD_STATE_FILE")
            [ -n "$PREF_STATE" ] && bash "$ETK_ROOT/bin/hud_apply.sh" "$PREF_STATE" >/dev/null 2>&1 &
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
SENTRY_OUTPUT=$(cat "$SENTRY_OUTPUT_FILE")
rm -f "$SENTRY_OUTPUT_FILE"

# Verbose mode: replay the captured remote output for fidelity with the
# historical install.sh experience. TUI mode keeps it suppressed.
if [ "$TUI_ACTIVE" != "1" ]; then
    echo "$SENTRY_OUTPUT"
fi

# Parse for SENTRY_OK / SENTRY_FAIL marker
if echo "$SENTRY_OUTPUT" | grep -q SENTRY_OK; then
    tui_step_progress 5 100
    tui_step_done 5
elif echo "$SENTRY_OUTPUT" | grep -q SENTRY_FAIL; then
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_fail "Sentry failed to start — last lines: $(echo "$SENTRY_OUTPUT" | tail -3 | tr '\n' ' ')"
    else
        echo -e "${R}>>> SENTRY FAILED TO START${N}"
        exit 1
    fi
else
    # Neither marker appeared — heredoc may have errored before the check
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_fail "Sentry deploy produced no status marker: $(echo "$SENTRY_OUTPUT" | tail -3 | tr '\n' ' ')"
    else
        echo -e "${Y}>>> WARNING: Sentry status marker missing from remote output${N}"
    fi
fi

# ==========================================================
# STEP 6.4: CUSTOM KERNEL (Tier K) — boot-persistent TEST entry, config-driven
# ==========================================================
# The ROCKNIX-GTK kernel toolchain builds our own 7.0.11 Image (mainline tarball
# + ROCKNIX patch stack + rig config; see RocknixGtkKernelPipeline_20260705).
# A custom kernel is deployed HERE, through install.sh, like every other rig
# change — never a one-off scp/hand-grub-edit (that reverts on the next reinstall
# / an OS grub-twin regen). Config-driven via etk.conf:
#   KERNEL_IMAGE             path to an Image artifact (empty => don't touch the
#                            kernel at all; stock installs are unaffected)
#   KERNEL_CONTEXT_KEEPALIVE 1 => bake `msm.context_keepalive=1` into the entry's
#                            cmdline (KGSL parity; the param auto-arms at boot, no
#                            manual sysfs poke — the runtime toggle still works on
#                            top for A/B). Unknown to the stock kernel = ignored.
#   KERNEL_DEPLOY_MODE       "test" (default) => the TEST entry is pick-per-boot;
#                            the saved default stays whatever the operator last
#                            chose (stock device entry on a virgin rig).
#                            "default" => AUTO-BOOT the GTK TEST entry: the entry
#                            gains `savedefault` and grubenv `saved_entry` is
#                            seeded to it, so every unattended reboot loads the
#                            GTK kernel with a 2-s menu window. FALLBACK: the
#                            stock device entries stay in the menu (picking one
#                            re-sticks it via ROCKNIX's own savedefault), and a
#                            managed 'ROCKNIX-GTK fallback -- stock kernel' entry
#                            boots the pristine /flash/KERNEL.etk-stock snapshot
#                            (taken here on first run, before anything touches
#                            /flash/KERNEL) with the verbose forensic console.
#                            Reverting to "test" un-seeds saved_entry back to the
#                            device entry — no hand-grub-edit in either direction.
# SAFE TEST MODEL (matches the proven standalone --test): the custom kernel is
# staged as /flash/KERNEL.gtktest under its OWN grub entry; the DEFAULT boot entry
# still loads the stock /flash/KERNEL, so a bad kernel is one reboot-to-default
# away from recovery (no separate fallback needed while default==stock). The entry
# is REFRESHED every install (strip prior block + trailing blanks, re-append) so a
# cmdline change takes effect AND an OS-update grub-twin revert is re-applied —
# same drift-proofing panic=10 gets, but self-healing instead of warn-only.
# NEVER reboots the rig (operator does that on-device); NEVER edits the default
# entry. Promotion to the default KERNEL slot is a future KERNEL_DEPLOY_MODE knob.
if [ -n "${KERNEL_IMAGE:-}" ] && [ -f "${KERNEL_IMAGE:-}" ]; then
    # portable host sha256 (Linux sha256sum | macOS shasum)
    if command -v sha256sum >/dev/null 2>&1; then
        K_HOST_SHA=$(sha256sum "$KERNEL_IMAGE" | awk '{print $1}')
    else
        K_HOST_SHA=$(shasum -a 256 "$KERNEL_IMAGE" | awk '{print $1}')
    fi
    # Build the TEST entry cmdline (verbose console for forensics; + parity token).
    # Two GTK cmdlines: the QUIET primary ("ROCKNIX-GTK for Flip 2", the default
    # boot) and the VERBOSE diagnostic twin (loglevel=7 console — the proven
    # cmdline, one grub-pick away). 'quiet loglevel=3' only quiets the console —
    # NO video= change (the DSI black-screen risk is stale-grub, not quiet).
    # Validate the quiet boot on a COLD boot; verbose + stock are the fallbacks.
    K_CMDLINE="boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable rootwait console=tty0 loglevel=7 panic=30"
    K_CMDLINE_QUIET="boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable rootwait console=tty0 quiet loglevel=3 panic=30"
    if [ "${KERNEL_CONTEXT_KEEPALIVE:-0}" = "1" ]; then
        K_CMDLINE="$K_CMDLINE msm.context_keepalive=1"
        K_CMDLINE_QUIET="$K_CMDLINE_QUIET msm.context_keepalive=1"
    fi
    K_FLIP2_DTB="/boot/grub/sm8250-retroidpocket-flip2.dtb"
    K_MODE="${KERNEL_DEPLOY_MODE:-test}"
    # Flip-2 device-guard: KERNEL_DEPLOY_MODE=default AUTO-boots the GTK kernel,
    # but the GTK Image carries the Flip 2 DTB and is verified only on the Flip 2.
    # On any other SD865 device (RP5 / Mini / Thor), downgrade to TEST mode — the
    # GTK kernel stays one grub-pick away and stock remains the default boot — so
    # an unverified rig never auto-boots an untested kernel. Guard on the
    # device-tree compatible ('retroidpocket,rpflip2'); all SD865 share 'sm8250'.
    if [ "$K_MODE" = "default" ]; then
        if ! ssh $RIG_SSH "tr '\0' '\n' < /sys/firmware/devicetree/base/compatible 2>/dev/null | grep -q rpflip2" 2>/dev/null; then
            echo "    [GUARD] KERNEL_DEPLOY_MODE=default is Flip-2-only (the only verified device); this rig is not a Flip 2 -> using TEST mode (stock stays default; GTK kernel one grub-pick away)."
            K_MODE="test"
        fi
    fi
    # Skip re-staging if the rig already carries this exact Image (idempotent).
    K_RIG_SHA=$(ssh $RIG_SSH "sha256sum /flash/KERNEL.gtktest 2>/dev/null | cut -d' ' -f1" 2>/dev/null)
    scp -q "$KERNEL_IMAGE" "$RIG_SSH:/storage/rocknix-gtk.KERNEL.staging" 2>/dev/null
    K_OUT=$(ssh $RIG_SSH "HOST_SHA='$K_HOST_SHA' K_CMDLINE='$K_CMDLINE' K_CMDLINE_QUIET='$K_CMDLINE_QUIET' FLIP2_DTB='$K_FLIP2_DTB' K_MODE='$K_MODE' sh -s" 2>&1 << 'KERNELREMOTE'
set -e
S=$(sha256sum /storage/rocknix-gtk.KERNEL.staging 2>/dev/null | cut -d' ' -f1)
[ "$S" = "$HOST_SHA" ] || { echo "KERNEL_FAIL staging sha mismatch ($S)"; rm -f /storage/rocknix-gtk.KERNEL.staging; exit 1; }
mount -o remount,rw /flash
cp /storage/rocknix-gtk.KERNEL.staging /flash/KERNEL.gtktest
sync
F=$(sha256sum /flash/KERNEL.gtktest | cut -d' ' -f1)
[ "$F" = "$HOST_SHA" ] || { echo "KERNEL_FAIL flash sha mismatch ($F)"; mount -o remount,ro /flash || true; exit 1; }
rm -f /storage/rocknix-gtk.KERNEL.staging
# Pristine-stock snapshot: taken once, while install.sh has never written
# /flash/KERNEL, so on a virgin rig this IS the OS-shipped kernel. The managed
# fallback entry boots it with the verbose forensic console.
[ -f /flash/KERNEL.etk-stock ] || cp /flash/KERNEL /flash/KERNEL.etk-stock
K_STOCK_CMDLINE="boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable rootwait console=tty0 loglevel=7 panic=30"
# SD-card boot cmdlines — LOCKSTEP with os-install/build/build_gtk_image_v2.sh
# (the card's own grub entries): unique labels ROCKNIX-GTK/GTKSTOR locate the
# GTK card unambiguously next to the UFS install (the split-brain cure).
# keepalive is baked unconditionally: these lines only ever boot the GTK card
# kernel, which always wants it.
K_SD_CMDLINE="boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR grub_portable rootwait console=tty0 quiet loglevel=3 panic=30 msm.context_keepalive=1"
K_SD_CMDLINE_VERBOSE="boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR grub_portable rootwait console=tty0 loglevel=7 panic=30 msm.context_keepalive=1"
TS=$(date +%Y%m%d_%H%M%S)
for CFG in /flash/EFI/BOOT/grub.cfg /flash/boot/grub/grub.cfg; do
    [ -f "$CFG" ] || continue
    cp "$CFG" "$CFG.etkbak-$TS"
    # Strip any prior etk-managed blocks (primary + verbose + fallback +
    # sdcard entries, their comment line, and the `set fallback=` global),
    # then trailing blanks.
    awk '
        (index($0,"etk-gtk") || index($0,"etk-fallback") || index($0,"etk-sdcard")) && /menuentry/ {inblk=1; next}
        inblk && /^}/ {inblk=0; next}
        inblk {next}
        /^# etk-sdcard/ {next}
        /^set fallback=/ {next}
        {print}
    ' "$CFG" | awk 'NF{last=NR} {ln[NR]=$0} END{for(i=1;i<=last;i++)print ln[i]}' > "$CFG.stripped"
    # Build the managed entries: primary (QUIET, the default boot), verbose
    # twin, the stock-kernel fallback, then the SD-card dual-mode pair
    # (spike-validated 2026-07-22, v5 protocol; slim grub has NO echo and NO
    # configfile — only search/linux/devicetree/if/savedefault/save_env).
    {
        printf "menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {\n"
        [ "$K_MODE" = "default" ] && printf '        savedefault\n'
        printf '        search --set -f /KERNEL.gtktest\n'
        printf '        linux /KERNEL.gtktest %s\n' "$K_CMDLINE_QUIET"
        printf '        devicetree %s\n' "$FLIP2_DTB"
        printf '}\n'
        printf "menuentry 'ROCKNIX-GTK for Flip 2 (verbose)' \$menuentry_id_option 'etk-gtk-verbose' {\n"
        printf '        search --set -f /KERNEL.gtktest\n'
        printf '        linux /KERNEL.gtktest %s\n' "$K_CMDLINE"
        printf '        devicetree %s\n' "$FLIP2_DTB"
        printf '}\n'
        printf "menuentry 'ROCKNIX-GTK fallback -- stock kernel' \$menuentry_id_option 'etk-fallback-stock' {\n"
        printf '        savedefault\n'
        printf '        search --set -f /KERNEL.etk-stock\n'
        printf '        linux /KERNEL.etk-stock %s\n' "$K_STOCK_CMDLINE"
        printf '        devicetree %s\n' "$FLIP2_DTB"
        printf '}\n'
        # SD-card dual-mode pair. The if-gate is LOAD-BEARING: a failed grub
        # command does not abort a menuentry, and /KERNEL.gtktest exists on
        # the UFS ESP too — ungated, a card-out boot loads the UFS kernel
        # with the card cmdline and hangs at kernel level (spike v3 lesson).
        # savedefault only in the card branch + the else-branch save_env heal
        # give: pick sticks while the card is in; card-out costs ONE detour
        # boot that restores the internal default (spike v5, operator-passed).
        printf '# etk-sdcard entries (managed): SD boot, pick-persistent, self-healing\n'
        printf "set fallback='etk-gtk-test'\n"
        printf "menuentry 'ROCKNIX-GTK from SD card' \$menuentry_id_option 'etk-sdcard' {\n"
        printf '        if search --set=root --label ROCKNIX-GTK --no-floppy; then\n'
        printf '                savedefault\n'
        printf '                linux /KERNEL.gtktest %s\n' "$K_SD_CMDLINE"
        printf '                devicetree %s\n' "$FLIP2_DTB"
        printf '        else\n'
        printf "                set saved_entry='etk-gtk-test'\n"
        printf '                save_env saved_entry\n'
        printf '                search --set=root --label ROCKNIX --no-floppy\n'
        printf '                linux /KERNEL.gtktest %s\n' "$K_CMDLINE_QUIET"
        printf '                devicetree %s\n' "$FLIP2_DTB"
        printf '        fi\n'
        printf '}\n'
        printf "menuentry 'ROCKNIX-GTK from SD card (verbose)' \$menuentry_id_option 'etk-sdcard-verbose' {\n"
        printf '        if search --set=root --label ROCKNIX-GTK --no-floppy; then\n'
        printf '                linux /KERNEL.gtktest %s\n' "$K_SD_CMDLINE_VERBOSE"
        printf '                devicetree %s\n' "$FLIP2_DTB"
        printf '        else\n'
        printf '                search --set=root --label ROCKNIX --no-floppy\n'
        printf '                linux /KERNEL.gtktest %s\n' "$K_CMDLINE"
        printf '                devicetree %s\n' "$FLIP2_DTB"
        printf '        fi\n'
        printf '}\n'
    } > "$CFG.etkblock"
    # Insert the ETK block BEFORE the first existing menuentry so ROCKNIX-GTK
    # leads the menu (was appended at the end). Degrades safely to append if no
    # menuentry is found. grubenv still drives the DEFAULT boot (saved_entry).
    awk -v bf="$CFG.etkblock" '
        !ins && /^menuentry / { while ((getline line < bf) > 0) print line; close(bf); ins=1 }
        { print }
        END { if (!ins) { while ((getline line < bf) > 0) print line } }
    ' "$CFG.stripped" > "$CFG"
    rm -f "$CFG.stripped" "$CFG.etkblock"
done
# grubenv seeding — ROCKNIX grub auto-boots `saved_entry` with a 2-s menu.
# The env block is a fixed 1024-byte file ('#'-padded); no grub-editenv on the
# rig, so write it whole. mode=default => seed the GTK entry; mode=test =>
# un-seed it (back to the device entry) ONLY if we were the ones who set it.
seed_grubenv() {
    for GE in /flash/EFI/BOOT/grubenv /flash/boot/grub/grubenv; do
        [ -f "$GE" ] || continue
        printf '# GRUB Environment Block\nsaved_entry=%s\n' "$1" > "$GE.tmp"
        N=$(wc -c < "$GE.tmp")
        dd if=/dev/zero bs=1 count=$((1024 - N)) 2>/dev/null | tr '\0' '#' >> "$GE.tmp"
        mv "$GE.tmp" "$GE"
    done
}
if [ "$K_MODE" = "default" ]; then
    seed_grubenv etk-gtk-test
elif grep -q 'saved_entry=etk-gtk-test' /flash/EFI/BOOT/grubenv 2>/dev/null; then
    seed_grubenv rpflip2
fi
sync
mount -o remount,ro /flash || true
echo "KERNEL_OK gtktest_sha=$F keepalive=$(grep -qc 'msm.context_keepalive=1' /flash/EFI/BOOT/grub.cfg && echo on || echo off) bootdefault=$([ "$K_MODE" = "default" ] && echo gtk || echo stock)"
KERNELREMOTE
)
    if echo "$K_OUT" | grep -q KERNEL_OK; then
        K_KA=$(echo "$K_OUT" | sed -n 's/.*keepalive=\([a-z]*\).*/\1/p')
        K_BD=$(echo "$K_OUT" | sed -n 's/.*bootdefault=\([a-z]*\).*/\1/p')
        if [ "$K_BD" = "gtk" ]; then
            K_BOOTMSG="AUTO-BOOTS the GTK kernel (2-s menu; stock = one pick away)"
        else
            K_BOOTMSG="default boot stays stock; pick 'ROCKNIX-GTK TEST kernel' at the menu"
        fi
        if [ "$K_RIG_SHA" = "$K_HOST_SHA" ]; then
            say "${G}[ETK]${N} Custom kernel: $(basename "$KERNEL_IMAGE") already staged; grub entries refreshed (parity=${K_KA:-off}); $K_BOOTMSG."
        else
            say "${G}[ETK]${N} Custom kernel staged -> /flash/KERNEL.gtktest (parity=${K_KA:-off}); $K_BOOTMSG. Reboot on-device."
        fi
        # --- GTK boot-identity line (no image rebuild) ---
        # /etc/os-release (the stock boot version/date) is read-only squashfs, so
        # rebranding it needs the image lane. Instead we deploy a oneshot that
        # prints "ROCKNIX-GTK <ver>-<date>" to the boot console right after the
        # stock show-version.service — via ETK's /storage/.config/system.d/
        # vector, enabled into basic.target. It self-gates on the live cmdline
        # (silent on a stock-kernel boot). Date is derived from the Image name.
        GTK_KDATE=$(basename "$KERNEL_IMAGE" | grep -oE '[0-9]{8}' | head -1)
        tui_log "Deploying GTK boot-identity line"
        scp -q ./config/etk-gtk-version.service "$RIG_SSH:/storage/.config/system.d/etk-gtk-version.service" 2>/dev/null
        ssh $RIG_SSH "sed -i 's/@KDATE@/${GTK_KDATE:-dev}/' /storage/.config/system.d/etk-gtk-version.service 2>/dev/null; mkdir -p /storage/.config/system.d/basic.target.wants; ln -sf /storage/.config/system.d/etk-gtk-version.service /storage/.config/system.d/basic.target.wants/etk-gtk-version.service" 2>/dev/null
    else
        say "${Y}[ETK]${N} Custom kernel deploy FAILED: $(echo "$K_OUT" | grep -m1 KERNEL_FAIL || echo "$K_OUT" | tail -1)"
    fi
else
    [ -n "${KERNEL_IMAGE:-}" ] && say "${Y}[ETK]${N} KERNEL_IMAGE set but file missing ($KERNEL_IMAGE) — skipping kernel deploy."
fi

# ==========================================================
# STEP 6.5: CUSTOM TURNIP DRIVER CATALOG (Stage IV) — boot-persistent selector
# ==========================================================
# The stock Turnip lives on read-only squashfs (/usr/lib), so a forked/bumped
# .so can't replace it in place. ETK stages a CATALOG of builds under
# /storage/turnip/drivers/ (persistent). The operator picks one in Pitstop's
# DRIVER tab (writes /storage/turnip/selected = a catalog filename or 'stock');
# a oneshot systemd unit runs etk-turnip-bind.sh every boot to bind the selected
# build over the stock driver — or unbind for 'stock' — then stamps
# /storage/turnip/loaded with what it ACTUALLY bound (the honest live state the
# Pitstop header reads). Reboot-gated by design: a bind-mount can't hot-swap a
# driver a running RPCS3 has already mapped (the always-reboot gate is the real
# validation). Empty catalog / 'stock' / no selection => stock nightly Turnip,
# zero effect on stock installs. The host `drivers/*.so` set IS the catalog; the
# optional etk.conf TURNIP_SO names the DEFAULT pick on a rig that hasn't chosen.
# §G.5 note: once a fork is bound, the Mesa fingerprint tracks OUR driver, so a
# nightly's stock-Mesa change is intentionally masked (we want our driver).
ssh $RIG_SSH "mkdir -p /storage/turnip/drivers /storage/.config/system.d/" 2>/dev/null
# The host drivers/ dir IS the catalog: every .so in it is staged to the rig and
# offered in the DRIVER tab. Dropping a build into drivers/ is all it takes.
#
# CERTIFIED_BUILDS is NOT an allowlist — it is the BOOTSTRAP manifest. drivers/
# is gitignored, so a fresh clone starts empty; these get fetched from the latest
# release and sha256-verified (a download can truncate or be swapped), which is
# why a normal user install still ends up as exactly "stock + the proven fork".
# An operator with local builds simply has more in drivers/, and gets all of them.
#
# History: this was an allowlist + prune, added by the 0.5.0 productization
# commit to curate the END-USER catalog. That is a release-packaging concern, and
# enforcing it at install time also blocked the operator's own experiments —
# backwards, since a build must run on the rig before it can ever earn
# certification. Curate the shipped catalog by what goes in the release, not by
# refusing to install what the operator built.
CERTIFIED_BUILDS="etk_turnip_rocknix_26.1.3_gtk_0.4.so"
DRIVER_RELEASE_BASE="https://github.com/mercurious/etk/releases/latest/download"
# sha256 of each certified build — a fetched binary is verified against this.
driver_sha() { case "$1" in
    etk_turnip_rocknix_26.1.3_gtk_0.4.so) echo "6b9c50bf993c10d32941177e7b15868714ef64da7a3bbf28022f8f2fb745045f" ;;
    etk_turnip_rocknix_26.1.3_gtk_0.2.so) echo "245212454bb1809816f52fa7c04209db2ef63cf1b5ddc7a69533636a0a4b7d19" ;;
    *) echo "" ;; esac; }
sha256_of() {  # portable host sha256 (Linux sha256sum | macOS shasum)
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
    elif command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
    else echo ""; fi; }
TURNIP_KEEP="stock"   # basenames present in the on-rig catalog (stock is synthetic)
# --- bootstrap: fetch any certified build we don't already have locally -----
for cb in $CERTIFIED_BUILDS; do
    # Already present? Leave it completely alone. It is either a previous fetch
    # or the operator's OWN build of that name — and a local rebuild legitimately
    # has a different sha (MESA_GIT_SHA1 changes every build), so verifying it
    # here would delete their work from the host tree. Only ever sha-check bytes
    # we just pulled off the network.
    [ -f "./drivers/$cb" ] && continue
    say "${G}[ETK]${N} Fetching certified driver $cb from the latest ETK release..."
    mkdir -p ./drivers
    _part="./drivers/.$cb.part"
    if curl -fL --retry 2 -o "$_part" "$DRIVER_RELEASE_BASE/$cb" 2>/dev/null; then
        want=$(driver_sha "$cb"); got=$(sha256_of "$_part")
        if [ -n "$want" ] && [ -n "$got" ] && [ "$got" != "$want" ]; then
            rm -f "$_part"
            say "${Y}[ETK]${N} $cb FAILED sha256 verification — discarded (expected $want)."
        else
            mv "$_part" "./drivers/$cb"
        fi
    else
        rm -f "$_part"
        say "${Y}[ETK]${N} Could not fetch $cb (offline or asset missing) — skipping."
    fi
done
# --- stage the whole catalog: every .so in drivers/ ------------------------
staged=0
for f in ./drivers/*.so; do
    [ -e "$f" ] || continue          # literal glob when drivers/ is empty
    b=$(basename "$f")
    rsync -az "$f" "$RIG_SSH:/storage/turnip/drivers/$b" >/dev/null 2>&1 \
        && { staged=$((staged + 1)); TURNIP_KEEP="$TURNIP_KEEP $b"; }
done
if [ "$staged" -gt 0 ]; then
    say "${G}[ETK]${N} Staged $staged Turnip build(s) -> DRIVER-tab catalog"
else
    say "${Y}[ETK]${N} No Turnip build in drivers/ — catalog will be stock-only"
fi
# An out-of-tree TURNIP_SO is staged into the catalog too, and names the DEFAULT
# pick for a rig with no on-rig selection yet (preserves prior single-driver UX).
TURNIP_DEFAULT_SEL="stock"
if [ -n "${TURNIP_SO:-}" ] && [ -f "$TURNIP_SO" ]; then
    scp -q "$TURNIP_SO" "$RIG_SSH:/storage/turnip/drivers/$(basename "$TURNIP_SO")"
    TURNIP_DEFAULT_SEL="$(basename "$TURNIP_SO")"
    say "${G}[ETK]${N} Default driver build: $(basename "$TURNIP_SO")"
fi
# Keep the operator's out-of-tree default too, then garbage-collect: the on-rig
# catalog MIRRORS host drivers/, so a build deleted from drivers/ also leaves the
# rig. (This is now a mirror, not an allowlist — deleting a build from drivers/
# is how you retire it, rather than editing a list.)
[ "$TURNIP_DEFAULT_SEL" != "stock" ] && TURNIP_KEEP="$TURNIP_KEEP $TURNIP_DEFAULT_SEL"
# Also keep the rig's CURRENT DRIVER-tab selection: staging a new dev build
# must never prune the .so the selection still points at (bitten twice
# 2026-07-04 — prune → bind service falls back to STOCK at install time, and
# a reboot without re-selecting would silently race on the stock driver).
RIG_SELECTED=$(ssh $RIG_SSH "cat /storage/turnip/selected 2>/dev/null" | tr -d '[:space:]')
case "$RIG_SELECTED" in
    ""|stock) : ;;
    *) TURNIP_KEEP="$TURNIP_KEEP $RIG_SELECTED" ;;
esac
ssh $RIG_SSH "KEEP='$TURNIP_KEEP'; for f in /storage/turnip/drivers/*.so; do [ -e \"\$f\" ] || continue; b=\$(basename \"\$f\"); case \" \$KEEP \" in *\" \$b \"*) : ;; *) rm -f \"\$f\";; esac; done" 2>/dev/null
TURNIP_OUT_FILE=$(mktemp /tmp/etk_turnip_XXXXXX)
ssh $RIG_SSH "TURNIP_DEFAULT_SEL='$TURNIP_DEFAULT_SEL' sh -s" > "$TURNIP_OUT_FILE" 2>&1 << 'TURNIPREMOTE'
    # Boot-time resolver: read the selection, (re)bind or unbind, stamp loaded.
cat << 'BIND' > /storage/.config/etk-turnip-bind.sh
#!/bin/sh
# ETK Turnip driver selector (Stage IV). Binds the operator-selected catalog
# build over the stock /usr/lib driver, then records what was actually bound.
# Selection: /storage/turnip/selected (a drivers/*.so filename, or 'stock').
# Output:    /storage/turnip/loaded   (the live build id, or 'stock').
TDIR=/storage/turnip
DRV="$TDIR/drivers"
ACTIVE=/usr/lib/libvulkan_freedreno.so
pick=""
[ -f "$TDIR/selected" ] && pick=$(tr -d '[:space:]' < "$TDIR/selected")
# Pre-catalog fallback: legacy single-file install with no selection written.
if [ -z "$pick" ] && [ -f "$TDIR/libvulkan_freedreno.so" ]; then
    grep -q " $ACTIVE " /proc/mounts || mount --bind "$TDIR/libvulkan_freedreno.so" "$ACTIVE"
    echo custom > "$TDIR/loaded"
    exit 0
fi
# Resolve the pick to a real catalog file; anything else means stock (unbind).
target=""
if [ -n "$pick" ] && [ "$pick" != "stock" ] && [ -f "$DRV/$pick" ]; then
    target="$DRV/$pick"
fi
# Make the live bind match the target (idempotent; rebind on a changed pick).
if grep -q " $ACTIVE " /proc/mounts; then
    umount "$ACTIVE" 2>/dev/null || true
fi
if [ -n "$target" ] && mount --bind "$target" "$ACTIVE"; then
    echo "$pick" > "$TDIR/loaded"
else
    echo stock > "$TDIR/loaded"
fi
BIND
    chmod +x /storage/.config/etk-turnip-bind.sh
cat << 'SVC' > /storage/.config/system.d/etk-turnip.service
[Unit]
Description=ETK Custom Turnip Driver selector (Stage IV bind-mount over stock)
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /storage/.config/etk-turnip-bind.sh
ExecStop=/bin/sh -c 'umount /usr/lib/libvulkan_freedreno.so 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
SVC
    # Seed the default pick ONLY if the operator hasn't chosen on-rig (their
    # Pitstop selection persists across reinstall — that's the whole point).
    if [ ! -f /storage/turnip/selected ]; then
        printf '%s\n' "${TURNIP_DEFAULT_SEL:-stock}" > /storage/turnip/selected
    fi
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk-turnip.service >/dev/null 2>&1
    systemctl restart etk-turnip.service >/dev/null 2>&1
    sel=$(cat /storage/turnip/selected 2>/dev/null)
    loaded=$(cat /storage/turnip/loaded 2>/dev/null)
    active=$(strings /usr/lib/libvulkan_freedreno.so 2>/dev/null | grep -m1 -oE 'Mesa [0-9.]+')
    n=$(ls /storage/turnip/drivers/*.so 2>/dev/null | wc -l | tr -d ' ')
    echo "TURNIP_OK catalog=${n} build(s); selected=${sel:-stock}; loaded=${loaded:-stock}; /usr/lib=${active:-?}"
TURNIPREMOTE
if grep -q TURNIP_OK "$TURNIP_OUT_FILE"; then
    say "${G}[ETK]${N} $(grep -m1 TURNIP_OK "$TURNIP_OUT_FILE" | sed 's/TURNIP_OK //')"
else
    say "${Y}[ETK]${N} $(grep -m1 -E 'TURNIP|error|denied' "$TURNIP_OUT_FILE" || echo 'Turnip selector deploy: no status marker')"
fi
rm -f "$TURNIP_OUT_FILE"

# ==========================================================
# STEP 6.55: RPCS3 GTK EDITION (default) — boot-persistent bind
# ==========================================================
# rpcs3-sa also lives on read-only squashfs (/usr/bin), same story as Turnip's
# /usr/lib driver above. As of 0.6.0 GA the certified GTK Edition build is the
# DEFAULT emulator — fetched from the ETK release (sha256-verified, exactly the
# certified-driver pattern in STEP 6.5) with no configuration required. The
# manual-download + etk.conf-path flow was dev-era plumbing (one experimental
# build in flight) that had leaked into GA. etk.conf RPCS3_APPIMAGE semantics:
#   (empty/unset)  -> AUTO: fetch + stage the certified GTK Edition (default)
#   "stock"        -> explicitly run ROCKNIX's stock rpcs3-sa (opt-out)
#   /path/to/file  -> stage a local build (dev override, unchanged behavior)
# Fail-soft: if the AUTO fetch/verify fails (offline, asset missing), warn and
# fall back to stock — the kit must install cleanly without internet. Because
# the deploy is a bind-mount (never overwrites the underlying squashfs file),
# stock needs no backup: unmounting always recovers it. Reboot-gated by the
# same doctrine as Turnip: a bind-mount can't hot-swap a binary a running
# RPCS3 already has open.
CERT_RPCS3="rpcs3-etk_gtk-edition-0.7.5_v0.0.41-19544-60c9705a_linux_aarch64.AppImage"
CERT_RPCS3_SHA="c464771932da20064f0942fc61f22df9d6e67bc52893f319d5d02cde1eaae02d"
RPCS3_RELEASE_BASE="https://github.com/mercurious/etk/releases/latest/download"
ssh $RIG_SSH "mkdir -p /storage/rpcs3 /storage/.config/system.d/" 2>/dev/null
# Pre-flight: the staging target lives on /storage — the small UFS SYSTEM
# partition on internal-boot rigs, which also backs the emulator/driver
# bind-mount sources and all config writes. When it fills, the staging rsync
# below fails FAIL-SOFT ("stock stays active") and the rig silently keeps the
# OLD emulator (bitten 2026-07-04: 8.5GB of segfault cores → ENOSPC → the
# teardown-guard build never landed; caught only by sha verify). Warn loudly
# so a full partition is a headline, not a footnote.
STORAGE_FREE_KB=$(ssh $RIG_SSH "df -k /storage 2>/dev/null | tail -1 | awk '{print \$4}'")
case "$STORAGE_FREE_KB" in ''|*[!0-9]*) STORAGE_FREE_KB=999999999 ;; esac
if [ "$STORAGE_FREE_KB" -lt 204800 ]; then
    say "${R}[ETK] /storage has only $((STORAGE_FREE_KB / 1024))MB free — emulator staging WILL fail (needs ~80MB + temp).${N}"
    say "${R}[ETK] Check $ETK_ROOT/cores and /storage/cores for crash cores, then re-run install.${N}"
fi
RPCS3_STAGE_SRC=""
case "${RPCS3_APPIMAGE:-}" in
    stock)
        say "${G}[ETK]${N} RPCS3: stock ROCKNIX build selected (etk.conf opt-out)."
        ;;
    "")
        # AUTO: certified release build. emulators/ is gitignored (like drivers/),
        # so a fresh clone fetches once and reuses the cached copy thereafter.
        mkdir -p ./emulators
        if [ ! -f "./emulators/$CERT_RPCS3" ]; then
            say "${G}[ETK]${N} Fetching RPCS3 GTK Edition from the latest ETK release (~78MB, one-time)..."
            curl -fL --retry 2 -o "./emulators/$CERT_RPCS3" "$RPCS3_RELEASE_BASE/$CERT_RPCS3" 2>/dev/null \
                || { rm -f "./emulators/$CERT_RPCS3"; say "${Y}[ETK]${N} Could not fetch the GTK Edition (offline or asset missing) — stock RPCS3 stays active."; }
        fi
        if [ -f "./emulators/$CERT_RPCS3" ]; then
            got=$(sha256_of "./emulators/$CERT_RPCS3")
            if [ -n "$got" ] && [ "$got" != "$CERT_RPCS3_SHA" ]; then
                rm -f "./emulators/$CERT_RPCS3"
                say "${Y}[ETK]${N} GTK Edition FAILED sha256 verification — refusing to stage (stock stays active)."
            else
                RPCS3_STAGE_SRC="./emulators/$CERT_RPCS3"
            fi
        fi
        ;;
    *)
        if [ -f "$RPCS3_APPIMAGE" ]; then
            RPCS3_STAGE_SRC="$RPCS3_APPIMAGE"
        else
            say "${Y}[ETK]${N} RPCS3_APPIMAGE set but file not found: $RPCS3_APPIMAGE — stock stays active."
        fi
        ;;
esac
if [ -n "$RPCS3_STAGE_SRC" ]; then
    # chmod +x BOTH sides regardless of the source file's own mode — an
    # AppImage staged without the exec bit fails as a silent "quits on
    # launch" (exit 126, no RPCS3.log/dmesg trace at all), not a crash.
    chmod +x "$RPCS3_STAGE_SRC" 2>/dev/null
    rsync -az "$RPCS3_STAGE_SRC" "$RIG_SSH:/storage/rpcs3/rpcs3-sa.custom" >/dev/null 2>&1 \
        && ssh $RIG_SSH "chmod 755 /storage/rpcs3/rpcs3-sa.custom" 2>/dev/null \
        && say "${G}[ETK]${N} Staged RPCS3 build: $(basename "$RPCS3_STAGE_SRC")" \
        || say "${Y}[ETK]${N} Failed to stage RPCS3 build (rsync error) — stock stays active."
else
    ssh $RIG_SSH "rm -f /storage/rpcs3/rpcs3-sa.custom" 2>/dev/null
fi
RPCS3_OUT_FILE=$(mktemp /tmp/etk_rpcs3_XXXXXX)
ssh $RIG_SSH "sh -s" > "$RPCS3_OUT_FILE" 2>&1 << 'RPCS3REMOTE'
    # Boot-time resolver: bind the custom build over stock if present, else
    # unbind (idempotent; re-run every boot since bind-mounts don't persist).
cat << 'BIND' > /storage/.config/etk-rpcs3-bind.sh
#!/bin/sh
CUSTOM=/storage/rpcs3/rpcs3-sa.custom
ACTIVE=/usr/bin/rpcs3-sa
if grep -q " $ACTIVE " /proc/mounts; then
    umount "$ACTIVE" 2>/dev/null || true
fi
if [ -f "$CUSTOM" ] && mount --bind "$CUSTOM" "$ACTIVE"; then
    echo custom > /storage/rpcs3/loaded
else
    echo stock > /storage/rpcs3/loaded
fi
BIND
    chmod +x /storage/.config/etk-rpcs3-bind.sh
cat << 'SVC' > /storage/.config/system.d/etk-rpcs3.service
[Unit]
Description=ETK Custom RPCS3 build selector (bind-mount over stock rpcs3-sa)
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /storage/.config/etk-rpcs3-bind.sh
ExecStop=/bin/sh -c 'umount /usr/bin/rpcs3-sa 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
SVC
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk-rpcs3.service >/dev/null 2>&1
    systemctl restart etk-rpcs3.service >/dev/null 2>&1
    loaded=$(cat /storage/rpcs3/loaded 2>/dev/null)
    echo "RPCS3_OK loaded=${loaded:-stock}"
RPCS3REMOTE
if grep -q RPCS3_OK "$RPCS3_OUT_FILE"; then
    say "${G}[ETK]${N} $(grep -m1 RPCS3_OK "$RPCS3_OUT_FILE" | sed 's/RPCS3_OK //') (reboot to fully validate)"
else
    say "${Y}[ETK]${N} $(grep -m1 -E 'RPCS3|error|denied' "$RPCS3_OUT_FILE" || echo 'RPCS3 custom-build deploy: no status marker')"
fi
rm -f "$RPCS3_OUT_FILE"

# --- STEP 6.56: RPCS3 runtime env flags (profile.d vector) ---
# Runtime-gated switches for custom RPCS3 builds (e.g. the #11912 probe/fix
# vars). Same injection vector as the Turnip dials (start_rpcs3.sh sources
# /etc/profile -> profile.d at every game launch); NOT a Turnip dial, so it
# does not touch active_tune.txt / tune_tag. Empty flag => entry removed.
if [ -n "${RPCS3_ENV_FLAGS:-}" ]; then
    RPCS3_FLAGS_EXPORTS=""
    for kv in $RPCS3_ENV_FLAGS; do
        RPCS3_FLAGS_EXPORTS="${RPCS3_FLAGS_EXPORTS}export ${kv}\n"
    done
    printf "$RPCS3_FLAGS_EXPORTS" | ssh $RIG_SSH "cat > /storage/.config/profile.d/096-etk-rpcs3-flags" 2>/dev/null \
        && say "${G}[ETK]${N} RPCS3 env flags injected: ${RPCS3_ENV_FLAGS}" \
        || say "${Y}[ETK]${N} Failed to write RPCS3 env flags (096-etk-rpcs3-flags)."
else
    ssh $RIG_SSH "rm -f /storage/.config/profile.d/096-etk-rpcs3-flags" 2>/dev/null
fi

# --- STEP 6.57: RETIRE the audio watchdog (SM8250 silent-boot fixed in kernel) ---
# The SM8250 silent-boot probe race (~1 in 4 boots: q6afe/ADSP clock-vote race
# kills the va_macro probe → whole LPASS chain parks in deferred-probe → dummy
# sink → silent boot) is now fixed at the ROOT in the ROCKNIX-GTK kernel (patch
# 0002-q6afe-vote-probe-race, KERNEL.rocknix-gtk-20260706-audiofix0, shipped by
# STEP 6.4 + etk.conf KERNEL_IMAGE): the vote retries in place until the ADSP is
# ready, so no userspace revive is needed. The old etk-audio-watchdog.service +
# scripts/audio_watchdog.sh are RETIRED (2026-07-07). This step actively tears
# the unit down on rigs that still carry it from an earlier install (idempotent
# — a no-op once gone). SCOPE: this is ONLY the card-STARTUP workaround; the
# stutter/underrun telemetry (ledger aud= from the RPCS3 fork's
# /dev/shm/rpcs3_audio_stat) is a SEPARATE mechanism and is untouched.
ssh $RIG_SSH "sh -s" > /dev/null 2>&1 <<'AUDIOWDREMOTE'
    systemctl disable --now etk-audio-watchdog.service >/dev/null 2>&1
    rm -f /storage/.config/system.d/etk-audio-watchdog.service
    rm -f /storage/.config/system.d/multi-user.target.wants/etk-audio-watchdog.service
    rm -f /storage/games-internal/roms/etk/scripts/audio_watchdog.sh
    systemctl reset-failed etk-audio-watchdog.service >/dev/null 2>&1
    systemctl daemon-reload
AUDIOWDREMOTE
say "${G}[ETK]${N} Audio watchdog retired (SM8250 silent-boot fixed in the GTK kernel)"

# ==========================================================
# STEP 6.6: POWER PROFILE APPLIER (CPU/GPU gov + clock pinning)
# ==========================================================
# Pitstop's POWER tab writes /storage/etk-power/profile (path<TAB>value lines).
# sysfs power knobs reset on reboot, so a boot oneshot re-applies them — same
# self-skipping pattern as etk-turnip.service. GATED: no profile => unit skips,
# stock thermal_d governor management is unchanged (zero effect until opted in).
# The POWER schema (power_profiles.json) is pushed with the other config in STEP 4.
ssh $RIG_SSH "sh -s" > /dev/null 2>&1 <<'POWERREMOTE'
    mkdir -p /storage/.config/system.d/ /storage/etk-power
cat << 'APPLY' > /storage/.config/etk-power-apply.sh
#!/bin/sh
# ETK POWER applier — re-applies the saved gov/clock profile at boot (sysfs power
# knobs reset on reboot). Reads path<TAB>value lines; '#' lines are records/ignored.
PROFILE=/storage/etk-power/profile
[ -f "$PROFILE" ] || exit 0
TAB=$(printf '\t')
while IFS="$TAB" read -r path val; do
    case "$path" in '#'*|'') continue ;; esac
    [ -w "$path" ] && printf '%s' "$val" > "$path" 2>/dev/null
done < "$PROFILE"
EOFLINE=$(grep -c . "$PROFILE" 2>/dev/null)
echo "etk-power applied $(grep -c "$TAB" "$PROFILE" 2>/dev/null) knob writes from $PROFILE"
APPLY
    chmod +x /storage/.config/etk-power-apply.sh
cat << 'SVC' > /storage/.config/system.d/etk-power.service
[Unit]
Description=ETK POWER profile (CPU/GPU gov + clock re-apply at boot)
After=multi-user.target
ConditionPathExists=/storage/etk-power/profile

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /storage/.config/etk-power-apply.sh

[Install]
WantedBy=multi-user.target
SVC
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk-power.service >/dev/null 2>&1
POWERREMOTE
say "${G}[ETK]${N} POWER applier deployed (etk-power.service — self-skips until a profile is set)"

# ==========================================================
# STEP 6.65: PANIC BLACK BOX (pstore harvester + kmsg flight recorder)
# ==========================================================
# READ side only: bin/blackbox_d.py harvests /sys/fs/pstore at every boot
# (previous panic's ramoops dumps -> etk_telemetry/blackbox/) and then tails
# /dev/kmsg to persistent storage as the lead-up flight recorder. Harmless
# when ramoops is unarmed. The WRITE side (reserve_mem= grub-cmdline token +
# module confs) is operator-armed ONCE via scripts/arm_blackbox.sh and cold-
# boot validated — install.sh NEVER edits grub. This step only detects DRIFT:
# module confs present but the live cmdline missing the token means a ROCKNIX
# update reverted grub.cfg — warn loudly, operator re-runs the armer.
BB_OUT_FILE=$(mktemp /tmp/etk_blackbox_XXXXXX)
ssh $RIG_SSH "sh -s" > "$BB_OUT_FILE" 2>&1 << 'BLACKBOXREMOTE'
    mkdir -p /storage/.config/system.d/
cat << 'SVC' > /storage/.config/system.d/etk-blackbox.service
[Unit]
Description=ETK Panic Black Box (pstore harvest + kmsg flight recorder)
After=local-fs.target systemd-modules-load.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /storage/games-internal/roms/etk/bin/blackbox_d.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk-blackbox.service >/dev/null 2>&1
    systemctl restart etk-blackbox.service >/dev/null 2>&1
    sleep 1
    systemctl is-active etk-blackbox.service >/dev/null 2>&1 && echo "BLACKBOX_OK" || echo "BLACKBOX_FAIL"
    # Drift tripwire: the working blackbox value on THIS SoC is panic auto-reboot
    # (RAM-dump capture is FALSIFIED — DDR scrambling; see arm_blackbox.sh). ANY
    # panic=N arms auto-reboot, so gate on the PRESENCE of a panic reboot token,
    # not a specific value: the GTK kernel grub entries bake panic=30 by
    # construction (STEP 6.4 + the Tier-I image), while arm_blackbox.sh writes
    # panic=10 onto stock entries — both are "armed". Hardcoding panic=10 here
    # false-warned on every GTK-default boot. Only a boot with NO panic= (stock
    # kernel, unarmed) needs the armer. ROCKNIX updates revert grub, so re-check
    # the live cmdline each install.
    if grep -qE 'panic=[1-9][0-9]*' /proc/cmdline; then
        echo "BLACKBOX_ARMED"
    else
        echo "BLACKBOX_UNARMED"
    fi
BLACKBOXREMOTE
if grep -q BLACKBOX_OK "$BB_OUT_FILE"; then
    say "${G}[ETK]${N} Panic Black Box recorder deployed (etk-blackbox.service)"
else
    say "${Y}[ETK]${N} Panic Black Box service failed to start — check journalctl -u etk-blackbox on the rig."
fi
if grep -q BLACKBOX_UNARMED "$BB_OUT_FILE"; then
    say "${Y}[ETK]${N} No panic auto-reboot token in the live cmdline (stock-kernel boot, or a"
    say "${Y}[ETK]${N} ROCKNIX update reverted grub). The GTK kernel bakes panic=30; on a stock"
    say "${Y}[ETK]${N} boot run scripts/arm_blackbox.sh, then reboot on-device."
fi
rm -f "$BB_OUT_FILE"

# ==========================================================
# STEP 6.7: DP-MIRROR DAEMON (USB video-out / capture-card mirror)
# ==========================================================
# When the external DisplayPort (DP-over-USB-C) links, bin/dpmirror_d.sh runs
# wl-mirror to duplicate the internal panel onto it so a capture card / OBS sees
# the game while the operator keeps playing on the handheld. Cooperates with
# ROCKNIX's stock /usr/bin/output_monitor "TV mode": re-enables the internal
# panel and pins the game workspace to it, then mirrors. Idle (zero effect) until
# a DP sink links — and DP only links in the Type-C "normal" orientation (kernel
# AUX/SBU bug; reverse = no link = no mirror, see project_rocknix_usb_dp_videoout).
# Toggle with ETK_DP_MIRROR in etk.conf (default on). Long-running; Restart=always.
ssh $RIG_SSH "sh -s" > /dev/null 2>&1 <<'DPMIRRORREMOTE'
    mkdir -p /storage/.config/system.d/
cat << 'SVC' > /storage/.config/system.d/etk-dpmirror.service
[Unit]
Description=ETK DP-mirror (mirror internal panel to external DisplayPort for capture/OBS)
After=multi-user.target

[Service]
Type=simple
ExecStart=/bin/bash /storage/games-internal/roms/etk/bin/dpmirror_d.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC
    systemctl daemon-reload
    systemctl enable /storage/.config/system.d/etk-dpmirror.service >/dev/null 2>&1
    systemctl restart etk-dpmirror.service >/dev/null 2>&1
    systemctl is-active --quiet etk-dpmirror.service && echo "DPMIRROR_OK" || echo "DPMIRROR_FAIL"
DPMIRRORREMOTE
say "${G}[ETK]${N} DP-mirror daemon deployed (etk-dpmirror.service — idle until a capture display links on DP-1)"

# ==========================================================
# STEP 6.8: STAGE III STABILITY HARNESS
# Two rig-side primitives validated on-rig 2026-06-11 (see
# dossiers/Stage3CustomRigDossier.md §T0):
#   1. profile.d/098-etk-stage3 — lifts Mesa's silent 1 GB disk-cache
#      LRU cap (the vault crosses 1 GB and would evict its own oldest
#      shaders) and raises the core-size ulimit for emulator processes.
#      Inherited by RPCS3 because start_rpcs3.sh sources /etc/profile.
#   2. 02-etk-coredump.sh + etk-stage3.service — Rocknix's default
#      kernel.core_pattern is `|/bin/false` (cores are DISCARDED) and
#      the sysctl resets every boot, so a oneshot systemd unit re-arms
#      it. Silent-class crashes (no log signature, no dmesg trace)
#      are undiagnosable without a core; this is the capture path.
# ==========================================================
tui_step_start 6
tui_log "Arming Stage III harness (Mesa cache cap + coredump capture)"
# Temp-file capture (not $(...) command substitution) for the same reason as
# the Sentry step above: heredoc bodies with literal `)` characters can
# confuse bash 3.2's parser when nested inside command substitution.
STAGE3_OUTPUT_FILE=$(mktemp /tmp/etk_stage3_XXXXXX)
ssh $RIG_SSH > "$STAGE3_OUTPUT_FILE" 2>&1 << 'REMOTE'
cat << 'PROF' > /storage/.config/profile.d/098-etk-stage3
# ETK Stage III stability harness (deployed by install.sh)
# 1) Lift Mesa disk-cache 1GB default cap so the shader vault never self-evicts
export MESA_SHADER_CACHE_MAX_SIZE=10G
# 2) Let emulator processes drop core for silent-crash forensics
ulimit -c unlimited
PROF

cat << 'CORE' > /storage/.config/custom_scripts/02-etk-coredump.sh
#!/bin/bash
# ETK Stage III: core capture for silent-class crashes.
# Cores are HUGE (2-7GB sparse each). On an internal-boot rig /storage is the
# ~6.6GB UFS SYSTEM partition — it also backs the emulator/driver bind-mount
# sources and every config write. Two teardown-segfault cores filled it to
# 100% on 2026-07-04 and silently broke emulator staging (rsync ENOSPC,
# fail-soft "stock stays active"). Route cores to the big game card
# ($ETK_ROOT/cores, mount-guarded) and fall back to the legacy /storage path
# (keep 1 only) when the card is absent. Boot prune here; the PER-CRASH prune
# lives in session_postmortem.sh — boot-only pruning cannot stop a
# within-boot core storm.
CORES_SD=/storage/games-internal/roms/etk/cores
if grep -q " /storage/games-internal " /proc/mounts; then
    mkdir -p "$CORES_SD"
    echo "$CORES_SD/%e.%p.%s.core" > /proc/sys/kernel/core_pattern
    ls -t "$CORES_SD"/*.core 2>/dev/null | tail -n +3 | xargs -r rm -f
    # clear strays from the legacy UFS path — they squat on the system partition
    rm -f /storage/cores/*.core 2>/dev/null
else
    mkdir -p /storage/cores
    echo "/storage/cores/%e.%p.%s.core" > /proc/sys/kernel/core_pattern
    ls -t /storage/cores/*.core 2>/dev/null | tail -n +2 | xargs -r rm -f
fi
CORE
chmod +x /storage/.config/custom_scripts/02-etk-coredump.sh

cat << 'S3SVC' > /storage/.config/system.d/etk-stage3.service
[Unit]
Description=ETK Stage III stability harness (coredump capture)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash /storage/.config/custom_scripts/02-etk-coredump.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
S3SVC

systemctl daemon-reload
systemctl enable etk-stage3.service > /dev/null 2>&1
systemctl restart etk-stage3.service
grep -qE "^/storage/(games-internal/roms/etk/)?cores/" /proc/sys/kernel/core_pattern && echo "STAGE3_OK" || echo "STAGE3_FAIL"
REMOTE
STAGE3_OUTPUT=$(cat "$STAGE3_OUTPUT_FILE")
rm -f "$STAGE3_OUTPUT_FILE"
if [ "$TUI_ACTIVE" != "1" ]; then
    echo "$STAGE3_OUTPUT"
fi
if echo "$STAGE3_OUTPUT" | grep -q STAGE3_OK; then
    tui_step_progress 6 100
    tui_step_done 6
else
    # Non-fatal: the kit races without it; only crash forensics are degraded.
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "WARNING: Stage III harness did not verify — core capture inactive"
    else
        echo -e "${Y}>>> WARNING: Stage III harness did not verify (core capture inactive)${N}"
    fi
fi

# ==========================================================
# STEP 6.85: SD GAME-TREE REBIND (crash-card storage model)
# For an internal-boot rig whose games live on an SD card: mount the SD
# games partition and stack its tree over the internal mountpoints so ES /
# RPCS3 / ETK read the SD's roms. WHY install.sh owns this now: it was a
# hand-pushed orphan (the Manage-Shaders trap) whose v2 HARD-CODED one card's
# UUID, so it 30s-hung + exit-1'd on any OTHER card (e.g. a GTK image card),
# and being Before=sway that stalled UI bring-up ~30s every boot. v3 discovers
# the card by LABEL=SDGAMES (labels survive fs-resize/re-creation; UUIDs do
# not) and EXITS 0 when no SDGAMES card is present, so it is a near-instant
# no-op on single-card rigs. NOT restarted at install time (it bind-mounts over
# live game paths — unsafe mid-session); it re-runs on the next cold boot.
# ==========================================================
tui_log "Writing SD game-tree rebind (label-based, non-stalling)"
REBIND_OUTPUT_FILE=$(mktemp /tmp/etk_rebind_XXXXXX)
ssh $RIG_SSH > "$REBIND_OUTPUT_FILE" 2>&1 << 'REMOTE'
mkdir -p /storage/.config/custom_scripts/ /storage/.config/system.d/
cat << 'RBND' > /storage/.config/custom_scripts/etk-sd-rebind.sh
#!/bin/bash
# ETK SD rebind v3 (2026-07-07): label-based, non-fatal, non-stalling.
# Crash-card storage model = boot from internal UFS, games live on the SD.
# When a games card LABELLED "SDGAMES" is present, mount it and stack its game
# tree over the internal mountpoints. v2 hard-coded ONE card's UUID, so it
# 30s-hung + exit-1'd on any OTHER card and (being Before=sway) stalled the UI
# ~30s every boot. v3 discovers by LABEL (labels survive fs-resize/re-creation;
# UUIDs do not) and EXITS 0 when no SDGAMES card is present ("nothing to
# rebind" is normal, not a failure). Generated by install.sh STEP 6.85 — do not
# hand-edit on the rig (a reinstall regenerates it).
SD_LABEL="SDGAMES"
LINK="/dev/disk/by-label/$SD_LABEL"
LOG=/storage/etk_rebind.log
echo "$(date) rebind v3 start (label=$SD_LABEL)" >> "$LOG"
# We run After=rocknix-automount, so udev has already populated by-label; one
# short retry only guards a pathological enumeration lag. No SDGAMES card ->
# exit 0 fast (imperceptible boot cost; no failed unit, no UI stall).
[ -e "$LINK" ] || sleep 0.5
if [ ! -e "$LINK" ]; then
  echo "$(date) no SDGAMES card present - nothing to rebind (ok)" >> "$LOG"
  exit 0
fi
DEV=$(readlink -f "$LINK")
mkdir -p /storage/sdgames /storage/games-internal /storage/roms
mountpoint -q /storage/sdgames || mount "$DEV" /storage/sdgames
if [ ! -d /storage/sdgames/games-internal ]; then
  echo "$(date) SDGAMES card ($DEV) has no games-internal/ - skipping (ok)" >> "$LOG"
  exit 0
fi
mount --bind /storage/sdgames/games-internal /storage/games-internal || echo "$(date) WARN: games-internal bind failed" >> "$LOG"
mount --bind /storage/sdgames/games-internal/roms /storage/roms || echo "$(date) WARN: roms bind failed" >> "$LOG"
echo "$(date) rebind v3 OK ($DEV): $(ls /storage/roms 2>/dev/null | wc -l) entries in /storage/roms" >> "$LOG"
exit 0
RBND
chmod +x /storage/.config/custom_scripts/etk-sd-rebind.sh

cat << 'RBSVC' > /storage/.config/system.d/etk-sd-rebind.service
[Unit]
Description=ETK SD game-tree rebind (internal boot + SD games)
Requires=rocknix-automount.service
After=rocknix-automount.service
Before=sway.service

[Service]
Type=oneshot
ExecStart=/bin/bash /storage/.config/custom_scripts/etk-sd-rebind.sh
RemainAfterExit=yes
TimeoutStartSec=15

[Install]
WantedBy=multi-user.target
RBSVC

systemctl daemon-reload
systemctl enable etk-sd-rebind.service > /dev/null 2>&1
# Clear the stale 'failed' state left by the old UUID-hardcoded version so
# `systemctl --failed` is clean now; the fixed script runs on the next cold boot.
systemctl reset-failed etk-sd-rebind.service > /dev/null 2>&1
[ -x /storage/.config/custom_scripts/etk-sd-rebind.sh ] && systemctl is-enabled etk-sd-rebind.service > /dev/null 2>&1 && echo "REBIND_OK" || echo "REBIND_FAIL"
REMOTE
REBIND_OUTPUT=$(cat "$REBIND_OUTPUT_FILE")
rm -f "$REBIND_OUTPUT_FILE"
if [ "$TUI_ACTIVE" != "1" ]; then
    echo "$REBIND_OUTPUT"
fi
if echo "$REBIND_OUTPUT" | grep -q REBIND_OK; then
    tui_log "SD rebind updated (label=SDGAMES; effective next cold boot)"
else
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "WARNING: SD rebind service did not verify"
    else
        echo -e "${Y}>>> WARNING: SD rebind service did not verify${N}"
    fi
fi

# ==========================================================
# STEP 7: PADDOCK LINK (Private Paddock, 0.3.0)
# Optional and conditional: runs ONLY if PADDOCK_TOKEN is set in etk.conf
# ("to get private shader storage, add your GitHub token to etk.conf
# before installing"). ETK never shares these bytes — the repo is the
# USER'S OWN private repo; this step just wires the rig to it.
# Validated end-to-end by tools/paddock_probe.sh (10/10, 2026-06-12).
# Empty-repo lesson: a release tag needs a commit, so a freshly created
# repo MUST be seeded with a README before any push can work.
# ==========================================================
tui_step_start 7
GH_API="https://api.github.com"
if [ -z "${PADDOCK_TOKEN:-}" ]; then
    tui_log "PADDOCK: no token in etk.conf — private paddock skipped"
    tui_step_progress 7 100
    tui_step_done 7
else
    tui_log "PADDOCK: verifying GitHub token + repo"
    PADDOCK_HDR=$(mktemp /tmp/etk_paddock_hdr_XXXXXX)
    printf 'Authorization: Bearer %s\n' "$PADDOCK_TOKEN" > "$PADDOCK_HDR"
    PADDOCK_OK=0
    GH_LOGIN=$(curl -fsS -H "@$PADDOCK_HDR" "$GH_API/user" 2>/dev/null | \
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("login",""))' 2>/dev/null)
    if [ -z "$GH_LOGIN" ]; then
        tui_log "PADDOCK: token rejected by GitHub — check etk.conf, re-run install"
    else
        PR_REPO="${PADDOCK_REPO:-$GH_LOGIN/etk-paddock}"
        REPO_JSON=$(curl -fsS -H "@$PADDOCK_HDR" "$GH_API/repos/$PR_REPO" 2>/dev/null)
        if [ -z "$REPO_JSON" ]; then
            # Repo missing: try to create (classic PATs can; fine-grained cannot)
            REPO_JSON=$(curl -fsS -X POST -H "@$PADDOCK_HDR" \
                -d "{\"name\":\"${PR_REPO#*/}\",\"private\":true,\"description\":\"ETK Private Paddock - personal vault/save/config storage (not shared)\"}" \
                "$GH_API/user/repos" 2>/dev/null)
            if [ -n "$REPO_JSON" ]; then
                tui_log "PADDOCK: created private repo $PR_REPO"
            else
                tui_log "PADDOCK: repo $PR_REPO missing & token can't create it."
                tui_log "PADDOCK: create it private on github.com, then re-run install"
            fi
        fi
        if [ -n "$REPO_JSON" ]; then
            IS_PRIVATE=$(printf '%s' "$REPO_JSON" | \
                python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("private",False)).lower())' 2>/dev/null)
            if [ "$IS_PRIVATE" != "true" ]; then
                # A PUBLIC paddock would publicly distribute the vault — refuse.
                tui_log "PADDOCK: $PR_REPO is PUBLIC — refusing. Make it private, re-run"
            else
                # Seed an initial commit if empty (release tags need a commit)
                if ! curl -fsS -H "@$PADDOCK_HDR" "$GH_API/repos/$PR_REPO/contents/README.md" >/dev/null 2>&1; then
                    SEED_B64=$(printf '# ETK Private Paddock\n\nPersonal vault/save/config storage for ETK. Private, not shared, managed by the ETK PADDOCK tab.\n' | base64 | tr -d '\n')
                    curl -fsS -X PUT -H "@$PADDOCK_HDR" \
                        -d "{\"message\":\"paddock: seed\",\"content\":\"$SEED_B64\"}" \
                        "$GH_API/repos/$PR_REPO/contents/README.md" >/dev/null 2>&1 && \
                        tui_log "PADDOCK: seeded $PR_REPO (initial commit)"
                fi
                tui_step_progress 7 60
                # Wire the rig: credential file, root-only
                ssh $RIG_SSH "mkdir -p /storage/roms/etk/config && umask 077 && printf '{\"repo\":\"%s\",\"token\":\"%s\"}\n' '$PR_REPO' '$PADDOCK_TOKEN' > /storage/roms/etk/config/paddock.json && chmod 600 /storage/roms/etk/config/paddock.json && echo PADDOCK_WIRED" 2>/dev/null | grep -q PADDOCK_WIRED && PADDOCK_OK=1
                if [ "$PADDOCK_OK" = "1" ]; then
                    tui_log "PADDOCK connected: $PR_REPO — tab live on next Pitstop launch"
                else
                    tui_log "PADDOCK: rig credential write failed — re-run install"
                fi
            fi
        fi
    fi
    rm -f "$PADDOCK_HDR"
    tui_step_progress 7 100
    tui_step_done 7
fi

# Final banner. tui_finish renders the caller-supplied content in both
# console and verbose modes.
TUI_FINISH_BANNER='━━━ KIT ARMED ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
TUI_FINISH_LINE1='\033[32mSENTRY ONLINE\033[0m  •  \033[32mPITSTOP REGISTERED\033[0m  •  \033[32mVAULT MIRRORED\033[0m'
TUI_FINISH_LINE2='Reboot the rig once to surface ETK Pitstop in the Tools menu.'
TUI_FINISH_LINE3="Health:  ssh $RIG_SSH 'systemctl status etk.service'"
TUI_FINISH_VERBOSE_HEAD='DEPLOYMENT COMPLETE. REBOOT THE DEVICE TO ACTIVATE THE ETK PITSTOP APP IN ROCKNIX TOOLS.'
TUI_FINISH_VERBOSE_BODY="    (EmulationStation reads the Tools gamelist at startup, so the polished
     ETK Pitstop entry appears after a reboot — Update Gamelists does not refresh it.)
    Run \033[1;33mssh $RIG_SSH 'systemctl status etk.service'\033[0m to confirm sentry health."
tui_finish