#!/bin/bash
# ==========================================================
# ETK PHASE 13.3: THE RETIREMENT PLAN (v13.1.3 - SILENT GPU)
# ==========================================================
# MERGE LOG: 
# - FIXED: find error when /sys/class/kgsl/ is missing/restricted
# - Added error suppression (2>/dev/null) to hardware discovery
# ==========================================================

source ./scripts/env.sh

echo -e "${R}>>> RETIRING FROM THE CHAMPIONSHIP...${N}"

ssh $RIG_SSH << 'EOF'
    echo -e ">>> Stopping ETK Guardian Service..."
    systemctl stop etk.service 2>/dev/null
    systemctl disable etk.service 2>/dev/null

    echo -e ">>> Killing lingering ETK processes..."
    pkill -9 -f "etk" 2>/dev/null
    pkill -9 -f "thermal_d.sh" 2>/dev/null
    pkill -9 -f "vault_d.sh" 2>/dev/null
    pkill -9 -f "input_d.py" 2>/dev/null

    echo -e ">>> Restoring Stock Hardware States..."
    
    # 1. CPU Reset (Robust tee write)
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy4/scaling_governor >/dev/null 2>&1
    echo "schedutil" | tee /sys/devices/system/cpu/cpufreq/policy7/scaling_governor >/dev/null 2>&1

    # 2. GPU Reset (The "Silent Discovery" Fix)
    # We suppress the find error and check for existence before writing
    GPU_PATH=$(find /sys/class/kgsl/ -name "kgsl-3d0" 2>/dev/null | head -n 1)
    
    if [ -d "$GPU_PATH" ]; then
        echo "simple_ondemand" | tee "$GPU_PATH/devfreq/governor" >/dev/null 2>&1
        echo 1 | tee "$GPU_PATH/adreno/throttling" >/dev/null 2>&1
        echo -e ">>> GPU state restored via $GPU_PATH"
    else
        # If discovery fails, check if Rocknix uses the direct devfreq mapping
        if [ -d "/sys/class/devfreq/kgsl-3d0" ]; then
            echo "simple_ondemand" | tee /sys/class/devfreq/kgsl-3d0/governor >/dev/null 2>&1
            echo -e ">>> GPU state restored via /sys/class/devfreq/kgsl-3d0"
        fi
    fi

    echo -e ">>> Removing System Files..."
    rm -f /storage/.config/system.d/etk.service
    rm -f /storage/.config/custom_scripts/01-etk-startup.sh
    rm -f /storage/.config/MangoHud/MangoHud.conf
    # Note: Rocknix will auto-regenerate the default config on next boot
EOF

# Local directory cleanup
ssh $RIG_SSH "rm -rf $SHM_DIR $ETK_ROOT"

echo -e "${G}>>> UNINSTALL COMPLETE. SYSTEM RESTORED.${N}"