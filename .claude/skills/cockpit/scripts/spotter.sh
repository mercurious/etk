#!/usr/bin/env bash
# cockpit/spotter — background telemetry logger + auto dip-frame capture (READ-ONLY).
# Logs GPU/CPU/mem every INTERVAL s to CSV; grabs a frame when GPU busy >= DIP_THRESH%.
# Env: COCKPIT_PKG, ADB, OUTDIR (default /tmp/cockpit), INTERVAL(4), DIP_THRESH(95), MAXITER(2700)
set -uo pipefail
PKG="${COCKPIT_PKG:-aenu.aps3e}"
ADB="${ADB:-$(command -v adb 2>/dev/null || echo /Volumes/aps3e-build/android-sdk/platform-tools/adb)}"
OUTDIR="${OUTDIR:-/tmp/cockpit}"; INTERVAL="${INTERVAL:-4}"; DIP_THRESH="${DIP_THRESH:-95}"; MAXITER="${MAXITER:-2700}"
CSV="$OUTDIR/spotter.csv"; LOG="$OUTDIR/spotter.log"; SHOTS="$OUTDIR/dips"
mkdir -p "$SHOTS"; : > "$LOG"
echo "epoch,clock,gpu_busy,gpuclk_mhz,cpu7_mhz,pss_mb,free_mb,dip" > "$CSV"
echo "$(date '+%H:%M:%S') spotter start pkg=$PKG interval=${INTERVAL}s dip>=${DIP_THRESH}%" >> "$LOG"
gone=0; last_shot=0
for ((i=0;i<MAXITER;i++)); do
  pid="$("$ADB" shell pidof "$PKG:emu" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
  now=$(date +%s); clk=$(date '+%H:%M:%S')
  if [ -z "$pid" ]; then
    gone=$((gone+1)); [ "$gone" -ge 3 ] && { echo "$clk emu gone, exit" >> "$LOG"; break; }
    sleep "$INTERVAL"; continue
  fi
  gone=0
  read -r busy gpuhz cpu7 <<<"$("$ADB" shell 'echo $(cat /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage 2>/dev/null | tr -dc 0-9) $(cat /sys/class/kgsl/kgsl-3d0/gpuclk 2>/dev/null) $(cat /sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq 2>/dev/null)' 2>/dev/null | tr -d '\r')"
  gmhz=$(( ${gpuhz:-0} / 1000000 )); c7=$(( ${cpu7:-0} / 1000 ))
  pss="$("$ADB" shell dumpsys meminfo "$pid" 2>/dev/null | tr -d '\r' | awk '$1=="TOTAL"{print int($2/1024);exit}')"
  free="$("$ADB" shell cat /proc/meminfo 2>/dev/null | tr -d '\r' | awk '/MemAvailable/{print int($2/1024)}')"
  dip=0
  if [ "${busy:-0}" -ge "$DIP_THRESH" ] && [ $((now-last_shot)) -ge 20 ]; then
    dip=1; last_shot=$now
    "$ADB" exec-out screencap -p > "$SHOTS/dip_${clk//:/}.png" 2>/dev/null
    echo "$clk DIP gpu=${busy}% @${gmhz}MHz -> $SHOTS/dip_${clk//:/}.png" >> "$LOG"
  fi
  echo "$now,$clk,${busy:-},${gmhz},${c7},${pss:-},${free:-},$dip" >> "$CSV"
  sleep "$INTERVAL"
done
echo "$(date '+%H:%M:%S') spotter exit" >> "$LOG"
echo "spotter wrote $CSV (+ dip frames in $SHOTS)"
