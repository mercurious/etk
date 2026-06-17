#!/usr/bin/env bash
# cockpit/cockpit-read — one-shot: grab a frame + a one-line GPU/CPU/mem snapshot (READ-ONLY).
# Usage: cockpit-read.sh [out.png]   Env: COCKPIT_PKG, ADB
set -uo pipefail
PKG="${COCKPIT_PKG:-aenu.aps3e}"
ADB="${ADB:-$(command -v adb 2>/dev/null || echo /Volumes/aps3e-build/android-sdk/platform-tools/adb)}"
OUT="${1:-/tmp/cockpit_frame.png}"
"$ADB" exec-out screencap -p > "$OUT" 2>/dev/null
pid="$("$ADB" shell pidof "$PKG:emu" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
[ -z "$pid" ] && pid="$("$ADB" shell pidof "$PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
busy="$("$ADB" shell 'cat /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage' 2>/dev/null | tr -dc 0-9)"
gmhz=$(( $("$ADB" shell 'cat /sys/class/kgsl/kgsl-3d0/gpuclk' 2>/dev/null | tr -dc 0-9 || echo 0) / 1000000 ))
cpu=$(( $("$ADB" shell 'cat /sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq' 2>/dev/null | tr -dc 0-9 || echo 0) / 1000 ))
pss="$([ -n "$pid" ] && "$ADB" shell dumpsys meminfo "$pid" 2>/dev/null | tr -d '\r' | awk '$1=="TOTAL"{print int($2/1024);exit}')"
free="$("$ADB" shell cat /proc/meminfo 2>/dev/null | tr -d '\r' | awk '/MemAvailable/{print int($2/1024)}')"
echo "FRAME $OUT ($(wc -c <"$OUT" 2>/dev/null) bytes) | GPU ${busy:-?}% @${gmhz}MHz | CPU7 ${cpu}MHz | PSS ${pss:-?}MB | free ${free:-?}MB | emu ${pid:-none}"
echo "(now Read $OUT to see the screen)"
