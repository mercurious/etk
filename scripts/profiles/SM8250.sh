# ==========================================================
# ETK DEVICE PROFILE — SM8250 (Tier-1 reference)
# ==========================================================
# Devices: Retroid Pocket Flip 2, Retroid Pocket 5,
#          other SD865-class RP devices
# GPU: Adreno 650 / Mesa Turnip
# Sourced ONLY by scripts/env.sh per Immutable Law #2.
# VALUES ONLY — no `export`, no logic. env.sh is the sole
# exporter of the canonical names defined below.
#
# Structurally re-certified against Rocknix nightly-20260529 on 2026-05-29
# via `etk_drift.py --check` (no structural drift) + manual headless-gate pass
# (gamepad codes unchanged, R3 survives suspend/resume, RPCS3 binds Adreno 650).
# Input-node renumbering on 20260529 is benign — find_gamepad() matches by name.
# ==========================================================

# --- Identity ---
PROFILE_SOC="SM8250"
GPU_DRIVER_FAMILY="turnip"
RPCS3_CAPABLE=1

# --- Thermal zones ---
# zone14 = cpu7-bottom-thermal (verified live, nightly-20260525).
# THERMAL_ZONE_MAP is the curated subset etk_probe.sh samples; full
# enumeration via `etk_probe.sh discover` (Phase 5).
THERMAL_ZONE_GOVERNING=14
THERMAL_ZONE_MAP="1:cpu0 5:cluster0 6:cluster1 10:cpu7_top 14:cpu7_bot 15:gpu_top 19:mem 24:gpu_bot 25:battery"

# --- CPU clusters (cpufreq policy IDs) ---
CPU_POLICY_SILVER=0
CPU_POLICY_GOLD=4
CPU_POLICY_PRIME=7
CPU_PIT_CAP_KHZ=1200000

# --- GPU control nodes ---
# Governor lives on the devfreq node directly (kgsl class node was relocated
# in the kernel/Rocknix tree). Verified live nightly-20260525: writing
# powersave drops cur_freq 800 MHz -> 305 MHz; writing performance restores.
GPU_DEVFREQ_NODE="/sys/class/devfreq/3d00000.gpu"
GPU_GOVERNOR_PATH="/sys/class/devfreq/3d00000.gpu/governor"

# --- Thermal thresholds (chassis-calibrated; Flip 2 reference) ---
# Re-validate on RP5 chassis before claiming RP5 support (different cooling).
ALARM_TEMP=83
PIT_THRESHOLD=65
RACE_THRESHOLD=86

# --- RPCS3 vault adapter pin ---
GPU_ADAPTER_STRING="Turnip Adreno (TM) 650"

# --- Display / DPI (Flip 2 panel reference) ---
FOOT_FONT_SIZE=28
MANGOHUD_FONT_SIZE=46
