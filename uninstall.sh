#!/bin/bash
# ==========================================================
# ETK PHASE 13.6: THE RETIREMENT PLAN (v13.2.0 - CLEAN ZAP)
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
# ==========================================================

source ./scripts/env.sh

echo -e "${R}"
echo "=========================================================="
echo "  ETK RETIREMENT — REMOTE CLEAN & ZAP"
echo "=========================================================="
echo -e "${N}"

# ==========================================================
# VAULT PRESERVATION PROMPT
# Shaders are hours of harvest work. Default is KEEP.
# Pass --zap-vault as argument to skip this prompt and destroy.
# ==========================================================
ZAP_VAULT=0
if [ "$1" = "--zap-vault" ]; then
    ZAP_VAULT=1
    echo -e "${R}>>> WARNING: --zap-vault passed. Banked shaders will be DESTROYED.${N}"
else
    echo -e "${Y}>>> Your banked shader vault will be PRESERVED on this uninstall."
    echo -e "    To destroy the vault too, re-run with:  ./uninstall.sh --zap-vault${N}"
fi
echo ""

# ==========================================================
# STEP 1: QUIESCE ALL ETK PROCESSES
# Kill workers and bridge BEFORE removing any files to avoid
# mango_bridge writing to a deleted SHM, or vault_d reading
# a symlink that's about to be destroyed.
# Sentry is stopped via systemctl, not pkill, for clean shutdown.
# ==========================================================
echo -e "${C}>>> [1/4] STOPPING ETK SERVICES & PROCESSES...${N}"
ssh $RIG_SSH << 'STOP'
    systemctl stop etk.service    2>/dev/null
    systemctl disable etk.service 2>/dev/null

    # Kill workers by exact script name — not broad "etk" which could
    # match EmulationStation paths or user files
    pkill -9 -f "mango_bridge.sh" 2>/dev/null
    pkill -9 -f "vault_d.sh"      2>/dev/null
    pkill -9 -f "thermal_d.sh"    2>/dev/null
    pkill -9 -f "input_d.py"      2>/dev/null
    pkill -9 -f "etk_pitstop.py"  2>/dev/null

    sleep 1
    echo "    Processes quiesced."
STOP

# ==========================================================
# STEP 2: RESTORE STOCK HARDWARE STATES
# CPU and GPU governors back to Rocknix defaults.
# Must happen while the rig is still running (before ETK_ROOT removal).
# ==========================================================
echo -e "${C}>>> [2/4] RESTORING STOCK HARDWARE STATES...${N}"
ssh $RIG_SSH << 'HW'
    # CPU: restore schedutil on both big and little core clusters
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy4/scaling_governor >/dev/null 2>&1
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy7/scaling_governor >/dev/null 2>&1
    # Also restore max freq in case thermal_d capped it
    cat /sys/devices/system/cpu/cpufreq/policy4/cpuinfo_max_freq \
        > /sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq 2>/dev/null
    cat /sys/devices/system/cpu/cpufreq/policy7/cpuinfo_max_freq \
        > /sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq 2>/dev/null
    echo "    CPU governors restored to schedutil."

    # GPU: silent discovery — try kgsl class path first, devfreq fallback
    GPU_PATH=$(find /sys/class/kgsl/ -name "kgsl-3d0" 2>/dev/null | head -n 1)
    if [ -d "$GPU_PATH" ]; then
        echo "simple_ondemand" | tee "$GPU_PATH/devfreq/governor" >/dev/null 2>&1
        echo 1 | tee "$GPU_PATH/adreno/throttling"                >/dev/null 2>&1
        echo "    GPU restored via $GPU_PATH"
    elif [ -d "/sys/class/devfreq/kgsl-3d0" ]; then
        echo "simple_ondemand" | tee /sys/class/devfreq/kgsl-3d0/governor >/dev/null 2>&1
        echo "    GPU restored via /sys/class/devfreq/kgsl-3d0"
    else
        echo "    GPU path not found — manual check may be needed."
    fi
HW

# ==========================================================
# STEP 3: REMOVE ETK FILES
# Removes all ETK-owned files from the rig in a specific order:
#   a) Systemd service unit
#   b) Sentry script (correct filename: 01-etk-sentry.sh)
#   c) Pitstop Tools-menu launcher
#   d) MangoHud config (ETK's overlay — restores system default)
#   e) Crash log
#   f) ETK_ROOT tree (conditionally preserves or destroys vault)
# ==========================================================
echo -e "${C}>>> [3/4] REMOVING ETK FILES FROM RIG...${N}"

ssh $RIG_SSH << CLEAN
    # Systemd unit
    rm -f /storage/.config/system.d/etk.service
    echo "    Removed: etk.service"

    # Sentry script — correct filename (was 01-etk-startup.sh in old uninstall, which never matched)
    rm -f /storage/.config/custom_scripts/01-etk-sentry.sh
    echo "    Removed: 01-etk-sentry.sh"

    # Legacy carcasses from earlier ETK phases. The pre-rename install
    # left a separate `etk-guardian.service` unit pointing at the old
    # `01-etk-startup.sh` script — when the script was renamed to
    # `01-etk-sentry.sh` and the unit to `etk.service`, neither old
    # half was cleaned up. The dead unit then sits in a status=127
    # restart loop forever, spamming the systemd journal. A stale
    # `etk_sentry.sh` (Phase 12, pre-`01-` prefix) likewise lingers in
    # custom_scripts/ and confuses anyone grepping for sentry logic.
    systemctl disable --now etk-guardian.service 2>/dev/null
    rm -f /storage/.config/system.d/etk-guardian.service
    rm -f /storage/.config/custom_scripts/etk_sentry.sh
    rm -f /storage/.config/01-etk-startup.sh
    systemctl daemon-reload 2>/dev/null
    echo "    Removed: legacy etk-guardian.service / etk_sentry.sh / 01-etk-startup.sh"

    # Tools menu launcher (target both current and any legacy uppercase-space name)
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
        # Preserve vault, remove everything else under ETK_ROOT
        # Scripts, bin, config, logs, SHM mirror — gone.
        # vault/ subtree — kept intact for future reinstall.
        rm -rf $ETK_ROOT/bin
        rm -rf $ETK_ROOT/scripts
        rm -rf $ETK_ROOT/config
        rm -rf $ETK_ROOT/logs
        rm -f  $ETK_ROOT/telemetry.log
        echo "    Preserved: $ETK_ROOT/vault (shader bank intact)"
        echo "    Removed:   ETK_ROOT scripts, bin, config, logs"
    fi
CLEAN

# ==========================================================
# STEP 4: LOCAL HOST CLEANUP
# Nothing destructive — just confirms what's left on the host.
# The host-side ./vault is the offsite backup; never auto-deleted.
# ==========================================================
echo -e "${C}>>> [4/4] HOST-SIDE STATUS...${N}"
if [ -d "./vault" ] && [ "$(ls -A ./vault 2>/dev/null)" ]; then
    echo -e "    ${Y}Host vault preserved at ./vault — your shader backup is safe.${N}"
else
    echo -e "    No local vault found."
fi

echo ""
echo -e "${G}=========================================================="
echo    "  RETIREMENT COMPLETE. RIG RESTORED."
echo -e "==========================================================${N}"
if [ "$ZAP_VAULT" != "1" ]; then
    echo -e "    Reinstall anytime with ${Y}./install.sh${N} — vault will be pushed back to rig."
fi
