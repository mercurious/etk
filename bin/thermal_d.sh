#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: THERMAL GOVERNOR (v13.1.0 - SILENT PRODUCER)
# ==========================================================
# ARCHITECTURE: Proactive GPU Clearing & Hard CPU Anchoring
# HARDWARE: SM8250 (Adreno 650 / Policies 4 & 7)
# MERGE LOG: 
# - SILENT PRODUCER: Relinquished $LIVE_STAT to mango_bridge.sh
# - Writes telemetry to $SHM_DIR/thermal_stat to stop HUD flicker
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

LAST_MODE="INIT"
TICK=0

# --- 1. THE ANTI-THRASHING PATCH ---
sysctl -w vm.swappiness=10 2>/dev/null
echo 1 > /proc/sys/vm/drop_caches 2>/dev/null

mkdir -p "$SHM_DIR" 2>/dev/null
chmod 777 "$SHM_DIR" 2>/dev/null

while true; do
    # 2. HARDWARE SENSING
    read -r T_RAW < /sys/class/thermal/thermal_zone14/temp 2>/dev/null || T_RAW="0"
    TEMP=$((T_RAW / 1000))
    
    # 3. INTENT SENSING
    read -r CURRENT_MODE < "$MODE_FILE" 2>/dev/null || CURRENT_MODE="$DEFAULT_MODE"

    # 4. THERMAL FAILSAFE
    if [ "$TEMP" -ge "$RACE_THRESHOLD" ]; then
        if [ "$CURRENT_MODE" == "RACE" ]; then
            echo "PIT" > "$MODE_FILE"
            CURRENT_MODE="PIT"
            echo "$(date) - THERMAL OVERRIDE: Switched to PIT at ${TEMP}C" >> "$ETK_ROOT/telemetry.log"
        fi
    fi

    # 5. CONTINUOUS THERMAL BROADCAST (For mango_bridge.sh)
    # Replaces direct LIVE_STAT writes to prevent HUD flicker/race conditions
    if [ "$TEMP" -ge "$RACE_THRESHOLD" ]; then
        echo "OVERHEAT" > "$SHM_DIR/thermal_stat"
    elif [ "$TEMP" -ge "$ALARM_TEMP" ]; then
        echo "HOT" > "$SHM_DIR/thermal_stat"
    else
        if [ "$CURRENT_MODE" == "RACE" ]; then
            echo "NOMINAL" > "$SHM_DIR/thermal_stat"
        else
            echo "ANCHOR" > "$SHM_DIR/thermal_stat"
        fi
    fi

    # 6. GOVERNOR & GPU SYNC (The "Anchor" Logic)
    if [ "$CURRENT_MODE" != "$LAST_MODE" ]; then
        if [ "$CURRENT_MODE" == "RACE" ]; then
            # UPSHIFT: Full Performance
            # CPU: Unleash Gold & Silver Cores
            echo "performance" > /sys/devices/system/cpu/cpufreq/policy4/scaling_governor 2>/dev/null
            echo "performance" > /sys/devices/system/cpu/cpufreq/policy7/scaling_governor 2>/dev/null
            cat /sys/devices/system/cpu/cpufreq/policy7/cpuinfo_max_freq > /sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq 2>/dev/null
            
            # GPU: Set to high-performance profile
            echo "performance" > /sys/class/kgsl/kgsl-3d0/devfreq/governor 2>/dev/null
            
            # [LIVE_STAT output removed - Handled by Bridge]
        else
            # DOWNSHIFT: The "Heavy Pit" Anchor
            # 1. Clear RAM pressure and ring buffers
            echo 3 > /proc/sys/vm/drop_caches 2>/dev/null 
            
            # 2. GPU ANCHOR: Force low power state to clear the command ring
            echo "powersave" > /sys/class/kgsl/kgsl-3d0/devfreq/governor 2>/dev/null
            
            # 3. CPU ANCHOR: Hard cap at 800MHz-1.2GHz
            echo 1200000 > /sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq 2>/dev/null
            echo 1200000 > /sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq 2>/dev/null
            echo "powersave" > /sys/devices/system/cpu/cpufreq/policy4/scaling_governor 2>/dev/null
            echo "schedutil" > /sys/devices/system/cpu/cpufreq/policy7/scaling_governor 2>/dev/null
            
            # 4. VBLANK MOMENTARY STALL (Optional: clears HUD flicker)
            # echo 1 > /sys/class/graphics/fb0/blank 2>/dev/null && sleep 0.1 && echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null
            
            # [LIVE_STAT output removed - Handled by Bridge]
        fi
        LAST_MODE="$CURRENT_MODE"
    fi

    sleep 2
done