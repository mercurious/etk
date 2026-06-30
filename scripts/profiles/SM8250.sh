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
# Certified against Rocknix NIGHTLY 20260628 (kernel 7.0.11, Turnip Mesa 26.1.2,
# RPCS3 0.0.41-19444 w/ GT5 memory-leak fix) — re-pinned 20260610->20260628 on
# 2026-06-30 after etk_drift.py reported NO structural drift (kernel + RPCS3
# unchanged vs the prior 20260622 pin; gamepad present, no WiFi/boot regression).
# The 20260610 adoption (2026-06-11) first proved the GT5 leak fix: GT5P clean at
# 720p, RAM peaks down ~1.5 GB vs official 20260601, silent-crash class absent.
# Prior baseline: OFFICIAL 20260601 (build e7b9e9a3, kernel 7.0.2, Turnip 26.1.0),
# certified 2026-06-02 via etk_drift.py --check + headless gate; 20260601.json banked.
# Input-node renumbering across builds is benign — find_gamepad() matches by
# name, not index (DualSense buttons node drifted event8->event9 historically).
# Per-game render re-validated on GT5P + GT HD (running fully on internal UFS).
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
#
# Recalibrated 2026-06-13 for the 20260610 stack (Turnip 26.1.2 / RPCS3 19444),
# which runs the GPU hotter and pushed the normal 70-82C operating band against
# the old 86C ceiling — spurious OVERHEAT trips, worse while charging. Anchored
# to zone14 (cpu7-bottom) kernel trip points: passive 90C (gentle, reversible
# auto-throttle) / passive 95C / critical 110C. RACE_THRESHOLD now sits ABOVE
# the kernel's first passive trip so the kernel's reversible governor is the
# first responder and ETK's reboot-requiring PIT is a true backstop; still 3C
# under the kernel's hard 95C trip and 18C under critical. ALARM (HOT warning)
# precedes the kernel's 90C throttle. History: 83/86 (pre-20260613).
ALARM_TEMP=88
RACE_THRESHOLD=92

# RECOVER_THRESHOLD: auto-recovery floor. A thermally-induced PIT self-clears
# back to RACE once TEMP holds at/under this for RACE_TRIP_TICKS ticks — no
# reboot (bin/thermal_d.sh restores both CPU clusters at runtime). The 92->80
# gap below RACE_THRESHOLD is the hysteresis band that keeps thermal cycling a
# slow safe sawtooth instead of rapid flapping. (Replaces the dead, never-read
# PIT_THRESHOLD=65.)
RECOVER_THRESHOLD=80

# Debounce: consecutive over-RACE_THRESHOLD ticks (loop = 2s) required before
# tripping PIT, AND the consecutive under-RECOVER_THRESHOLD ticks required
# before auto-recovering. Telemetry showed most 86C touches were single-tick
# transients. 2 ticks ~= 4s sustained. Consumed by bin/thermal_d.sh via env.sh.
RACE_TRIP_TICKS=2

# --- RPCS3 vault adapter pin ---
GPU_ADAPTER_STRING="Turnip Adreno (TM) 650"

# --- Display / DPI (Flip 2 panel reference) ---
FOOT_FONT_SIZE=28
MANGOHUD_FONT_SIZE=46
