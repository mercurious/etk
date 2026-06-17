#!/bin/bash
# ETK Cockpit — ROCKNIX live Spotter (T1), runs ON the rig. Args: DURATION_SEC INTERVAL_SEC
# Telemetry over standard Linux sysfs (same SM8250). Lean on thermal + frametime, NOT GPU%
# (drm/msm has no direct busy node) — infer CPU-bound vs GPU-bound from GPU-freq + CPU-freq.
# Crash-watch: new /storage/cores dump OR RPCS3.log fatal -> flag + break for gdb forensics.
DUR="${1:-600}"; INT="${2:-2}"
LOG=$(ls -t /storage/*/.cache/rpcs3/RPCS3.log /storage/.cache/rpcs3/RPCS3.log 2>/dev/null | head -1)
CORES=/storage/cores; base=$(ls "$CORES" 2>/dev/null | wc -l)
zt(){ awk '{printf "%.0f",$1/1000}' /sys/class/thermal/thermal_zone"$1"/temp 2>/dev/null; }  # m°C->°C
echo "[spotter] log=$LOG  cores=$CORES base=$base  dur=${DUR}s int=${INT}s"
endt=$(( $(awk '{print int($1)}' /proc/uptime) + DUR ))
while [ "$(awk '{print int($1)}' /proc/uptime)" -lt "$endt" ]; do
  pid=$(pgrep -f rpcs3 | head -1)
  rss=$(awk '/VmRSS/{printf "%d",$2/1024}' /proc/"$pid"/status 2>/dev/null)
  gpuf=$(awk '{printf "%d",$1/1000000}' /sys/class/devfreq/3d00000.gpu/cur_freq 2>/dev/null)   # MHz (305-800)
  cpf=$(awk '{printf "%d",$1/1000}' /sys/devices/system/cpu/cpufreq/policy7/scaling_cur_freq 2>/dev/null) # MHz prime
  free=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)
  etk=$(cat /dev/shm/etk_shm/live_stat.txt 2>/dev/null)
  crash=""
  [ "$(ls "$CORES" 2>/dev/null | wc -l)" -gt "$base" ] && crash=" *** NEW CORE DUMP ***"
  tail -8 "$LOG" 2>/dev/null | grep -qiE "Segfault|fatal error|Thread terminated" && crash="$crash *** RPCS3 FATAL ***"
  printf '%s | GPU %s°C %sMHz | CPUprime %s°C %sMHz | free %sMB / rpcs3 %sMB | bat %s°C | etk[%s]%s\n' \
    "$(date +%H:%M:%S)" "$(zt 15)" "${gpuf:-?}" "$(zt 10)" "${cpf:-?}" "$free" "${rss:-?}" "$(zt 25)" "$etk" "$crash"
  [ -n "$crash" ] && { echo "[spotter] >>> CRASH — newest core: $(ls -t "$CORES" 2>/dev/null | head -1)"; break; }
  sleep "$INT"
done
echo "[spotter] window ended"
