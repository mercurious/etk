#!/bin/bash
# ==========================================================
# ETK PHASE 13.6: THE RETIREMENT PLAN (v13.3.0 - PIT WALL TUI)
# ==========================================================
# FIX: Was deleting 01-etk-startup.sh — sentry is named 01-etk-sentry.sh
# FIX: mango_bridge.sh now killed before SHM is removed (race condition)
# FIX: input_d.py now killed explicitly
# FIX: pkill "etk" scope narrowed to specific process names (was too broad)
# FIX: Vault destruction now requires explicit confirmation — shaders are
#      irreplaceable without re-harvesting. Default is to preserve the vault.
# FIX: CRASH_LOG removed as part of full cleanup
# FIX: rm -rf ETK_ROOT moved inside the heredoc for consistency
# FIX: SHM removal removed (Rocknix wipes /dev/shm on reboot anyway)
# FIX: GPU restore now also tries /sys/class/devfreq/3d00000.gpu (post
#      kgsl relocation; matches the install-time GPU_GOVERNOR_PATH fix)
# UX:  Adopts the Pit Wall console TUI shared with install.sh.
#      4-step dashboard: STAND DOWN, RESTORE STOCK, REMOVE ETK, FINAL STATUS.
#      Pass --verbose for raw output.
# ==========================================================

source ./scripts/env.sh

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'

# --- FLAG PARSE ---
# --zap-vault: also destroy the rig-side vault. Default preserves shaders
# (hours of harvest work — irreplaceable without re-harvest).
# --verbose / -v: bypass the Pit Wall TUI; show raw remote output.
ZAP_VAULT=0
for arg in "$@"; do
    case "$arg" in
        --zap-vault)  ZAP_VAULT=1 ;;
        --verbose|-v) ETK_VERBOSE=1 ;;
    esac
done

# Source the install-time TUI library (host-only). Falls back to plain
# output when stdout isn't a tty or ETK_VERBOSE=1.
source ./tools/tui.sh 2>/dev/null || true

# Configure the dashboard for the 4-step uninstall flow.
TUI_HEADER_TITLE="ETK UNINSTALL"
TUI_TOTAL_STEPS=4
TUI_STEP_LABELS=(
    "STAND DOWN SVCS"
    "RESTORE STOCK  "
    "REMOVE ETK     "
    "FINAL STATUS   "
)

if tui_should_activate; then
    tui_init
fi

# Announce the vault disposition early so the operator can abort if they
# meant to pass --zap-vault and forgot (or vice versa).
if [ "$ZAP_VAULT" = "1" ]; then
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "[--zap-vault] banked shaders will be DESTROYED on this run"
    else
        echo -e "${R}>>> WARNING: --zap-vault passed. Banked shaders will be DESTROYED.${N}"
        echo ""
    fi
else
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "Vault preserved (default). Pass --zap-vault to also destroy."
    else
        echo -e "${Y}>>> Your banked shader vault will be PRESERVED on this uninstall."
        echo -e "    To destroy the vault too, re-run with:  ./uninstall.sh --zap-vault${N}"
        echo ""
    fi
fi

# Helper: run a remote heredoc command, capture output. In verbose mode,
# replay the captured output; in TUI mode, suppress (datalog surfaces
# the user-meaningful events).
run_remote_block() {
    local outfile=$(mktemp /tmp/etk_uninstall_XXXXXX)
    ssh $RIG_SSH > "$outfile" 2>&1
    local rc=$?
    if [ "$TUI_ACTIVE" != "1" ]; then
        cat "$outfile"
    fi
    rm -f "$outfile"
    return $rc
}

# ==========================================================
# STEP 0: STAND DOWN SERVICES
# Stop the Sentry cleanly via systemctl (not pkill — let it shutdown), then
# kill any worker processes by exact script name. Narrow pkill scope so we
# never collide with EmulationStation or user files containing "etk".
# ==========================================================
tui_step_start 0
tui_log "Stopping etk.service and quiescing daemons"
ssh $RIG_SSH > /tmp/etk_uninstall_stop.log 2>&1 << 'STOP'
    systemctl stop etk.service    2>/dev/null
    systemctl disable etk.service 2>/dev/null

    pkill -9 -f "mango_bridge.sh" 2>/dev/null
    pkill -9 -f "vault_d.sh"      2>/dev/null
    pkill -9 -f "thermal_d.sh"    2>/dev/null
    pkill -9 -f "input_d.py"      2>/dev/null
    pkill -9 -f "etk_pitstop.py"  2>/dev/null

    sleep 1
    echo "    Processes quiesced."
STOP
[ "$TUI_ACTIVE" != "1" ] && cat /tmp/etk_uninstall_stop.log
rm -f /tmp/etk_uninstall_stop.log
tui_log "Sentry stopped, worker daemons killed"
tui_step_done 0

# ==========================================================
# STEP 1: RESTORE STOCK HARDWARE STATES
# CPU governors back to schedutil; freq caps released; GPU governor back
# to simple_ondemand. Must run while the rig is still up (before files
# are removed in Step 2).
# ==========================================================
tui_step_start 1
tui_log "Restoring CPU governors to schedutil"
ssh $RIG_SSH > /tmp/etk_uninstall_hw.log 2>&1 << 'HW'
    # CPU: restore schedutil on big & little clusters, release freq caps
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy4/scaling_governor >/dev/null 2>&1
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy7/scaling_governor >/dev/null 2>&1
    cat /sys/devices/system/cpu/cpufreq/policy4/cpuinfo_max_freq \
        > /sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq 2>/dev/null
    cat /sys/devices/system/cpu/cpufreq/policy7/cpuinfo_max_freq \
        > /sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq 2>/dev/null
    echo "    CPU governors restored to schedutil."

    # GPU: try kgsl class path first, then devfreq/kgsl-3d0, then the post-
    # relocation devfreq/3d00000.gpu (matches the install-time profile fix).
    GPU_PATH=$(find /sys/class/kgsl/ -name "kgsl-3d0" 2>/dev/null | head -n 1)
    if [ -d "$GPU_PATH" ]; then
        echo "simple_ondemand" | tee "$GPU_PATH/devfreq/governor" >/dev/null 2>&1
        echo 1 | tee "$GPU_PATH/adreno/throttling"                >/dev/null 2>&1
        echo "    GPU restored via $GPU_PATH"
    elif [ -d "/sys/class/devfreq/kgsl-3d0" ]; then
        echo "simple_ondemand" | tee /sys/class/devfreq/kgsl-3d0/governor >/dev/null 2>&1
        echo "    GPU restored via /sys/class/devfreq/kgsl-3d0"
    elif [ -d "/sys/class/devfreq/3d00000.gpu" ]; then
        echo "simple_ondemand" | tee /sys/class/devfreq/3d00000.gpu/governor >/dev/null 2>&1
        echo "    GPU restored via /sys/class/devfreq/3d00000.gpu"
    else
        echo "    GPU path not found — manual check may be needed."
    fi
HW
[ "$TUI_ACTIVE" != "1" ] && cat /tmp/etk_uninstall_hw.log
rm -f /tmp/etk_uninstall_hw.log
tui_log "Stock CPU + GPU governors restored"
tui_step_done 1

# ==========================================================
# STEP 2: REMOVE ETK FILES FROM RIG
# Specific removal order: systemd unit, sentry script, pitstop launcher,
# MangoHud config, crash log, then ETK_ROOT (with or without vault).
# Heredoc is UNQUOTED (CLEAN, not 'CLEAN') so $ZAP_VAULT and $ETK_ROOT
# expand on the host side before reaching the remote shell.
# ==========================================================
tui_step_start 2
tui_log "Removing ETK files from rig"
ssh $RIG_SSH > /tmp/etk_uninstall_clean.log 2>&1 << CLEAN
    # Systemd unit
    rm -f /storage/.config/system.d/etk.service
    echo "    Removed: etk.service"

    # Sentry script — correct filename (legacy uninstall referenced 01-etk-startup.sh)
    rm -f /storage/.config/custom_scripts/01-etk-sentry.sh
    echo "    Removed: 01-etk-sentry.sh"

    # Legacy carcasses from earlier ETK phases (Phase 12 + pre-rename Phase 13).
    # The pre-rename install left a separate etk-guardian.service unit pointing
    # at the old 01-etk-startup.sh — when the script was renamed to
    # 01-etk-sentry.sh and the unit to etk.service, neither old half was cleaned
    # up. The dead unit sits in a status=127 restart loop forever, spamming the
    # systemd journal.
    systemctl disable --now etk-guardian.service 2>/dev/null
    rm -f /storage/.config/system.d/etk-guardian.service
    rm -f /storage/.config/custom_scripts/etk_sentry.sh
    rm -f /storage/.config/01-etk-startup.sh
    systemctl daemon-reload 2>/dev/null
    echo "    Removed: legacy etk-guardian.service / etk_sentry.sh / 01-etk-startup.sh"

    # Tools menu launcher (current and any legacy uppercase-space name)
    rm -f "/storage/.config/modules/etk_pitstop.sh"
    rm -f "/storage/.config/modules/ETK PITSTOP.sh"
    echo "    Removed: pitstop launcher from modules"

    # ETK MangoHud overlay
    rm -f /storage/.config/MangoHud/MangoHud.conf
    echo "    Removed: MangoHud overlay config"

    # Crash log
    rm -f /storage/etk_crash_report.log
    echo "    Removed: crash log"

    # ETK_ROOT — vault handling
    if [ "$ZAP_VAULT" = "1" ]; then
        rm -rf $ETK_ROOT
        echo "    Removed: ETK_ROOT + vault (ZAP)"
    else
        # Preserve vault, remove everything else under ETK_ROOT.
        rm -rf $ETK_ROOT/bin
        rm -rf $ETK_ROOT/scripts
        rm -rf $ETK_ROOT/config
        rm -rf $ETK_ROOT/logs
        rm -f  $ETK_ROOT/telemetry.log
        rm -f  $ETK_ROOT/etk.conf
        echo "    Preserved: $ETK_ROOT/vault (shader bank intact)"
        echo "    Removed:   ETK_ROOT scripts, bin, config, logs, etk.conf"
    fi
CLEAN
[ "$TUI_ACTIVE" != "1" ] && cat /tmp/etk_uninstall_clean.log
rm -f /tmp/etk_uninstall_clean.log
if [ "$ZAP_VAULT" = "1" ]; then
    tui_log "ETK files + rig vault destroyed"
else
    tui_log "ETK files removed; rig vault preserved"
fi
tui_step_done 2

# ==========================================================
# STEP 3: FINAL STATUS
# No destructive ops — confirms what's left on the host.
# Host-side ./vault is the offsite backup; never auto-deleted.
# ==========================================================
tui_step_start 3
if [ -d "./vault" ] && [ "$(ls -A ./vault 2>/dev/null)" ]; then
    tui_log "Host vault preserved at ./vault — your shader backup is safe"
    HOST_VAULT_STATUS="preserved"
else
    tui_log "No local vault found on host"
    HOST_VAULT_STATUS="absent"
fi
tui_step_done 3

# ==========================================================
# FINISH BANNER
# ==========================================================
TUI_FINISH_BANNER='━━━ KIT DECOMMISSIONED ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
if [ "$ZAP_VAULT" = "1" ]; then
    TUI_FINISH_LINE1='\033[32mSENTRY DISABLED\033[0m  •  \033[32mDAEMONS STOPPED\033[0m  •  \033[33mVAULT ZAPPED\033[0m'
else
    TUI_FINISH_LINE1='\033[32mSENTRY DISABLED\033[0m  •  \033[32mDAEMONS STOPPED\033[0m  •  \033[32mVAULT PRESERVED\033[0m'
fi
if [ "$HOST_VAULT_STATUS" = "preserved" ]; then
    TUI_FINISH_LINE2='Host vault backed up at ./vault — reinstall will push it back.'
else
    TUI_FINISH_LINE2='The rig is clean. No host-side vault to back up.'
fi
TUI_FINISH_LINE3='Reinstall anytime with:  ./install.sh'
TUI_FINISH_VERBOSE_HEAD='RETIREMENT COMPLETE. RIG RESTORED.'
if [ "$ZAP_VAULT" != "1" ]; then
    TUI_FINISH_VERBOSE_BODY="    Reinstall anytime with \033[1;33m./install.sh\033[0m — vault will be pushed back to rig."
else
    TUI_FINISH_VERBOSE_BODY="    Reinstall anytime with \033[1;33m./install.sh\033[0m."
fi
tui_finish
