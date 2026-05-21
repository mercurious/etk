#!/bin/bash
# ==========================================================
# ETK PHASE 13.5: PROBE (v13.5.6 - LEAD SHIELD MERGE)
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

{
  echo "=== [ ETK DEEP PROBE: $(date) ] ==="
  echo "ID: $TARGET_ID | MODE: $ETK_BUILD_TYPE"
  
  echo -e "\n[1] GPU ARCHITECTURE (DRM/FREEDRENO)"
  if [ -d "/sys/class/devfreq/3d00000.gpu" ]; then
    echo "Governor: $(cat /sys/class/devfreq/3d00000.gpu/governor 2>/dev/null)"
    echo "Cur Freq: $(cat /sys/class/devfreq/3d00000.gpu/cur_freq 2>/dev/null) Hz"
  fi

  echo -e "\n[2] KERNEL FAULT ANALYSIS"
  # strings + tr filter = Lead Shield
  dmesg | strings | grep -iE "adreno|turnip|fence|timeout|msm_dpu|panic|oom" | tail -n 25

  echo -e "\n[3] SYMLINK VERIFICATION"
  ls -ld /storage/.cache/mesa_shader_cache

  echo -e "\n[4] MEMORY PRESSURE"
  free -m
  
  # Patched
  echo -e "\n[5] RPCS3 INTERNAL ERROR LOG"
  # Rocknix RPCS3 writes its log to .cache (not .config) as RPCS3.log.
  strings /storage/.cache/rpcs3/RPCS3.log 2>/dev/null | tail -n 20

} | tr -cd "[:print:]\n" > "$CRASH_LOG" 2>&1

# Disabled to stop binary floods in human terminal display
# Scrape for the Commander UI summary
# DIAG_SUMMARY=$(tail -n 5 "$CRASH_LOG" | grep -iE "timeout|panic|oom" | tail -n 1)

# REVISED FORENSIC SUMMARY
# Ensure the summary itself is sanitized before it hits the disk
## echo "$DIAG_SUMMARY" | tr -cd "[:print:]" > "$LAST_ANALYSIS"