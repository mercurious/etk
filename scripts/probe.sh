#!/bin/bash
# ETK FORENSICS PROBE v13.0 (Smart Analytics)
source /storage/games-internal/roms/etk/scripts/env.sh

{
  echo "--- ETK DATALOG: $(date) ---"
  
  # Search for system stressors or panics
  DIAG_SUMMARY=$(dmesg | grep -iE "oom|killed|panic|adreno|turnip" | tail -n 1)
  [ -z "$DIAG_SUMMARY" ] && DIAG_SUMMARY="System nominal. No driver faults detected."

  echo -e "\n[1] MEMORY STATE"
  free -m
  
  echo -e "\n[2] KERNEL TRACE"
  dmesg | tail -n 20
  
  echo -e "\n[3] RPCS3 LOG TAIL"
  RPCS3_LOG=$(find /storage/.cache/rpcs3 /storage/.config/rpcs3 -name "RPCS3.log*" 2>/dev/null | head -n 1)
  [ -n "$RPCS3_LOG" ] && tail -n 15 "$RPCS3_LOG" || echo "Log Missing"
} > "$CRASH_LOG" 2>&1

# Output for the Commander UI to scrape
echo "$DIAG_SUMMARY" > "$LAST_ANALYSIS"