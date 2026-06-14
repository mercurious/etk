#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: THERMAL GOVERNOR (v14.0.0 - AUTO-RECOVERY)
# ==========================================================
# ARCHITECTURE: Debounced thermal failsafe with automatic hysteresis recovery.
#   RACE --(sustained >=RACE_THRESHOLD)--> PIT --(sustained <=RECOVER_THRESHOLD)--> RACE
#   Fully automatic; no reboot. GPU/CPU clocks anchored on PIT, fully restored
#   on recovery.
# HARDWARE: per active device profile (scripts/profiles/<soc>.sh)
# All hardware paths / policy IDs / freq caps are profile-driven; the
# SM8250 reference values are the historical Flip 2 / RP5 calibration.
# MERGE LOG:
# - SILENT PRODUCER: Relinquished $LIVE_STAT to mango_bridge.sh
# - Writes telemetry to $SHM_DIR/thermal_stat to stop HUD flicker
# - 2026-06-13 RECALIBRATION: thresholds raised in SM8250.sh (ALARM 83->88,
#   RACE 86->92) + PIT trip DEBOUNCED (RACE_TRIP_TICKS consecutive ticks) so
#   transient spikes no longer force PIT. Profile-driven; nothing hardcoded.
# - 2026-06-13 AUTO-RECOVERY (v14): PIT now self-clears back to RACE once cooled
#   below RECOVER_THRESHOLD for RACE_TRIP_TICKS ticks. Removed the reboot
#   requirement: its true cause was an INCOMPLETE upshift — the RACE branch
#   restored only PRIME's scaling_max_freq, leaving GOLD pinned at
#   CPU_PIT_CAP_KHZ until a cold boot. Upshift now restores BOTH clusters
#   (verified on-rig: gold returns to full freq at runtime). THERMAL_PIT latch
#   ensures a MANUAL (commander/dashboard) PIT is never auto-overridden. HUD
#   gains »COOLDOWN (auto-cooling) + "RACE OK" recovery flash.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

LAST_MODE="INIT"
TICK=0
OVER_TICKS=0          # consecutive ticks at/above RACE_THRESHOLD (trip debounce)
COOL_TICKS=0          # consecutive ticks at/below RECOVER_THRESHOLD (recovery debounce)
THERMAL_PIT=0         # 1 = current PIT was tripped BY US (thermal), so auto-recoverable
RESTORE_FLASH=0       # ticks remaining to show the "RACE OK" recovery banner

# --- 1. THE ANTI-THRASHING PATCH ---
sysctl -w vm.swappiness=10 2>/dev/null
echo 1 > /proc/sys/vm/drop_caches 2>/dev/null

mkdir -p "$SHM_DIR" 2>/dev/null
chmod 777 "$SHM_DIR" 2>/dev/null

while true; do
    # 2. HARDWARE SENSING
    read -r T_RAW < /sys/class/thermal/thermal_zone${THERMAL_ZONE_GOVERNING}/temp 2>/dev/null || T_RAW="0"
    TEMP=$((T_RAW / 1000))

    # 2b. TELEMETRY SAMPLE (throttled)
    # session_postmortem.sh computes avg/peak TEMP from "SAMPLE" lines in
    # telemetry.log. thermal_d already reads TEMP every tick (2s) — this
    # only adds a log append, not a new sample. Throttled to ~30s (15
    # ticks) to keep SD-card writes minimal (~1 line / 30s). Tick 1 also
    # samples so short crash sessions still get one reading.
    #
    # The SAMPLE line carries a 4th field: /proc/loadavg 1-minute load
    # average. Total CPU% saturates to ~100 instantly on this stack and
    # carries no signal — loadavg is the unbounded, discriminating metric.
    # session_postmortem.sh parses peak loadavg from field 4.
    #
    # DEV TOGGLE: to reduce write overhead while isolating a regression
    # during development, comment out the four lines below.
    # session_postmortem.sh tolerates the absence cleanly — peak_temp/
    # avg_temp/peak_load fall back to 0 and the TELEMETRY tab renders "----".
    TICK=$((TICK + 1))
    if [ "$TICK" -eq 1 ] || [ $((TICK % 15)) -eq 0 ]; then
        LOADAVG=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
        echo "$(date +%s) SAMPLE $TEMP ${LOADAVG:-0}" >> "$ETK_ROOT/telemetry.log"
    fi

    # 3. INTENT SENSING
    read -r CURRENT_MODE < "$MODE_FILE" 2>/dev/null || CURRENT_MODE="$DEFAULT_MODE"

    # 4. THERMAL FAILSAFE (debounced trip)
    # A single 2s spike to RACE_THRESHOLD must NOT trip PIT (telemetry: most
    # ceiling touches were single-tick transients). Require RACE_TRIP_TICKS
    # consecutive over-threshold ticks (~ticks*2s sustained). The kernel's own
    # reversible passive trip (90C on SM8250) is the first responder below
    # RACE_THRESHOLD; ETK PIT is the backstop. Counter resets the instant temp
    # drops back under threshold.
    if [ "$TEMP" -ge "$RACE_THRESHOLD" ]; then
        OVER_TICKS=$((OVER_TICKS + 1))
    else
        OVER_TICKS=0
    fi

    if [ "$OVER_TICKS" -ge "${RACE_TRIP_TICKS:-2}" ] && [ "$CURRENT_MODE" == "RACE" ]; then
        echo "PIT" > "$MODE_FILE"
        CURRENT_MODE="PIT"
        THERMAL_PIT=1          # mark this PIT as ours -> eligible for auto-recovery
        COOL_TICKS=0
        echo "$(date +%s) THERMAL OVERRIDE: Switched to PIT at ${TEMP}C (sustained ${OVER_TICKS} ticks)" >> "$ETK_ROOT/telemetry.log"
    fi

    # 4b. AUTO-RECOVERY (debounced hysteresis) — the "no reboot" path.
    # A thermally-induced PIT self-clears: once TEMP holds at/under
    # RECOVER_THRESHOLD for RACE_TRIP_TICKS consecutive ticks, restore RACE.
    # THERMAL_PIT gates this so a MANUAL PIT (commander/dashboard) is never
    # auto-overridden. The RACE_THRESHOLD->RECOVER_THRESHOLD gap (92->80) is the
    # hysteresis band that keeps cycling a slow safe sawtooth, not a fast flap.
    if [ "$CURRENT_MODE" == "RACE" ]; then
        # Any RACE state (auto-recovered OR a manual override out of PIT) clears
        # the thermal latch so we never "recover" a mode the user chose.
        THERMAL_PIT=0
        COOL_TICKS=0
    elif [ "$THERMAL_PIT" -eq 1 ]; then
        if [ "$TEMP" -le "$RECOVER_THRESHOLD" ]; then
            COOL_TICKS=$((COOL_TICKS + 1))
        else
            COOL_TICKS=0
        fi
        if [ "$COOL_TICKS" -ge "${RACE_TRIP_TICKS:-2}" ]; then
            echo "RACE" > "$MODE_FILE"
            CURRENT_MODE="RACE"
            THERMAL_PIT=0
            OVER_TICKS=0
            COOL_TICKS=0
            RESTORE_FLASH=2    # ~4s "RACE OK" confirmation on the HUD
            echo "$(date +%s) THERMAL RECOVER: Restored RACE at ${TEMP}C" >> "$ETK_ROOT/telemetry.log"
        fi
    fi

    # 5. CONTINUOUS THERMAL BROADCAST (For mango_bridge.sh)
    # Replaces direct LIVE_STAT writes to prevent HUD flicker/race conditions.
    # Precedence: recovery flash > auto-cooldown > overheat > hot > normal/manual.
    if [ "$RESTORE_FLASH" -gt 0 ]; then
        # Positive confirmation that auto-recovery restored full performance.
        echo "${TEMP}°C RACE OK" > "$SHM_DIR/thermal_stat"
        RESTORE_FLASH=$((RESTORE_FLASH - 1))
    elif [ "$THERMAL_PIT" -eq 1 ]; then
        # Auto-cooldown in progress — reassures the user it is handled
        # automatically and will self-restore (no reboot). Shown across all
        # bands so the cooling intent is always legible while it falls to
        # RECOVER_THRESHOLD.
        echo "${TEMP}°C»COOLDOWN" > "$SHM_DIR/thermal_stat"
    elif [ "$TEMP" -ge "$RACE_THRESHOLD" ]; then
        echo "${TEMP}°C»»»»OVERHEAT" > "$SHM_DIR/thermal_stat"
    elif [ "$TEMP" -ge "$ALARM_TEMP" ]; then
        echo "${TEMP}°C»»HOT" > "$SHM_DIR/thermal_stat"
    elif [ "$CURRENT_MODE" == "RACE" ]; then
        echo "${TEMP}°" > "$SHM_DIR/thermal_stat"
    else
        # Manual PIT (user/boot chosen, not thermal). No reboot semantics.
        echo "${TEMP}° PIT" > "$SHM_DIR/thermal_stat"
    fi

    # 6. GOVERNOR & GPU SYNC (The "Anchor" Logic)
    if [ "$CURRENT_MODE" != "$LAST_MODE" ]; then
        if [ "$CURRENT_MODE" == "RACE" ]; then
            # UPSHIFT: Full Performance — restore BOTH clusters COMPLETELY.
            # NOTE: GOLD's scaling_max_freq restore was historically MISSING
            # here, so after a PIT the gold cluster stayed pinned at
            # CPU_PIT_CAP_KHZ until a cold boot — the real reason "overdrive
            # only returned after a reboot." Verified on-rig that restoring it
            # at runtime returns gold to full freq. Mirrors the PIT downshift,
            # which caps BOTH gold and prime.
            echo "performance" > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/scaling_governor 2>/dev/null
            echo "performance" > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/scaling_governor 2>/dev/null
            cat /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/cpuinfo_max_freq > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/scaling_max_freq 2>/dev/null
            cat /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/cpuinfo_max_freq > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/scaling_max_freq 2>/dev/null

            # GPU: Set to high-performance profile
            echo "performance" > ${GPU_GOVERNOR_PATH} 2>/dev/null

            # [LIVE_STAT output removed - Handled by Bridge]
        else
            # DOWNSHIFT: The "Heavy Pit" Anchor
            # 1. Clear RAM pressure and ring buffers
            echo 3 > /proc/sys/vm/drop_caches 2>/dev/null

            # 2. GPU ANCHOR: Force low power state to clear the command ring
            echo "powersave" > ${GPU_GOVERNOR_PATH} 2>/dev/null

            # 3. CPU ANCHOR: Hard cap at profile PIT freq
            echo ${CPU_PIT_CAP_KHZ} > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/scaling_max_freq 2>/dev/null
            echo ${CPU_PIT_CAP_KHZ} > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/scaling_max_freq 2>/dev/null
            echo "powersave" > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_GOLD}/scaling_governor 2>/dev/null
            echo "schedutil" > /sys/devices/system/cpu/cpufreq/policy${CPU_POLICY_PRIME}/scaling_governor 2>/dev/null

            # 4. VBLANK MOMENTARY STALL (Optional: clears HUD flicker)
            # echo 1 > /sys/class/graphics/fb0/blank 2>/dev/null && sleep 0.1 && echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null

            # [LIVE_STAT output removed - Handled by Bridge]
        fi
        LAST_MODE="$CURRENT_MODE"
    fi

    sleep 2
done
