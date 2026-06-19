#!/bin/bash
# ETK Cockpit — ROCKNIX live Spotter (T1), runs ON the rig. Args: DURATION_SEC INTERVAL_SEC [STALE_TICKS]
# Telemetry over standard Linux sysfs (same SM8250). Lean on thermal + frametime, NOT GPU%
# (drm/msm has no direct busy node) — infer CPU-bound vs GPU-bound from GPU-freq + CPU-freq.
#
# CRASH-WATCH (auto-break + capture — ARM AT IDLE *BEFORE* LAUNCH, then walk away):
#   1. a6xx GPU FAULT — the dominant GT5P hang. dmesg `a6xx_irq … gpu fault`. It is DMESG-ONLY:
#      leaves NO core and NO RPCS3.log fatal, so the old core/log-only watch MISSED it entirely
#      (the whole reason a live multi-crash stint got hand-watched on 2026-06-18). This is now the
#      PRIMARY detector; the script marks dmesg at arm and breaks on any fault newer than the mark.
#   2. SILENT freeze / process death — gated on /dev/shm/etk_shm/live_stat.txt FRESHNESS, NOT pgrep.
#      The mango bridge rewrites live_stat ~1 Hz while in-game; `pgrep -x AppRun.wrapped` is
#      UNRELIABLE on this build (observed seen=0 for an entire live run), so DO NOT gate on it —
#      a frozen/stopped live_stat (unchanged for STALE_TICKS polls after the real DDU appeared) is
#      the trustworthy "no longer rendering" signal and catches the silent class the a6xx watch can't.
#   3. (kept) new /storage/cores dump OR RPCS3.log fatal — the SPU/segfault silent class (gdb-able).
# On any trigger: capture a6xx status + grim frame + proc state + ledger tail, print, break.
# The caller (run in background) is notified on exit. Classification: ADRENO when a fault is in
# dmesg, SILENT when live_stat froze with no fault.
DUR="${1:-1800}"; INT="${2:-5}"; STALE_TICKS="${3:-4}"
LOG=$(ls -t /storage/*/.cache/rpcs3/RPCS3.log /storage/.cache/rpcs3/RPCS3.log 2>/dev/null | head -1)
CORES=/storage/cores; base=$(ls "$CORES" 2>/dev/null | wc -l)
SHM=/dev/shm/etk_shm/live_stat.txt
MARK=$(awk '{print int($1)}' /proc/uptime)
zt(){ awk '{printf "%.0f",$1/1000}' /sys/class/thermal/thermal_zone"$1"/temp 2>/dev/null; }  # m°C->°C
# in-game = a real live DDU. Exclude pre-game (WAIT/IDLE/LOADING) and the startup banner (SHDRS),
# so stale-counting only begins once steady telemetry is flowing (no false trip on the banner hold).
ingame(){ case "$1" in ""|*WAIT*|*IDLE*|*LOADING*|*SHDRS*) return 1;; *) return 0;; esac; }
# --- hangrd forensic capture (gpu_watch fold-in): cat the debugfs node so the kernel emits the
#     freedreno REDUMP (.rd) of the HANGING submit the instant the GPU wedges. Arm AT IDLE, here,
#     before launch. Decode off-rig with cffdump; the .faultinfo sidecar (written on catch) carries
#     the dmesg ib1/fence/status so the decode lands on the FAULTING draw, not a 1300-draw haystack.
HANGRD_CAPTURE="${HANGRD_CAPTURE:-1}"; HANGRD_NODE=/sys/kernel/debug/dri/0/hangrd
FDIR="${FORENSIC_DIR:-/storage/games-internal/roms/etk/etk_telemetry/crash_forensics}"
RDFILE=""; HANGRD_PID=""
if [ "$HANGRD_CAPTURE" = 1 ] && [ -e "$HANGRD_NODE" ]; then
  mkdir -p "$FDIR"
  RDFILE="$FDIR/hangrd_$(date +%Y%m%d_%H%M%S).rd"
  cat "$HANGRD_NODE" > "$RDFILE" 2>/dev/null &
  HANGRD_PID=$!
  echo "$RDFILE" > "$FDIR/.armed_rd"
  echo "[spotter] hangrd ARMED -> $RDFILE (pid $HANGRD_PID); /storage free $(df -m /storage 2>/dev/null | awk 'NR==2{print $4}')MB"
elif [ "$HANGRD_CAPTURE" = 1 ]; then
  echo "[spotter] WARN: hangrd node absent ($HANGRD_NODE) — no .rd capture this run"
fi
echo "[spotter] armed @ uptime ${MARK}s — a6xx-fault + live_stat-stale(${STALE_TICKS}x) + core/log. dur=${DUR}s int=${INT}s. LAUNCH NOW."
endt=$(( MARK + DUR )); seen=0; prev=""; stale=0; crash=""
while [ "$(awk '{print int($1)}' /proc/uptime)" -lt "$endt" ]; do
  pid=$(pgrep -f rpcs3 | head -1)   # RSS display only (best-effort; do NOT use for liveness)
  rss=$(awk '/VmRSS/{printf "%d",$2/1024}' /proc/"$pid"/status 2>/dev/null)
  gpuf=$(awk '{printf "%d",$1/1000000}' /sys/class/devfreq/3d00000.gpu/cur_freq 2>/dev/null)   # MHz (305-800)
  cpf=$(awk '{printf "%d",$1/1000}' /sys/devices/system/cpu/cpufreq/policy7/scaling_cur_freq 2>/dev/null) # MHz prime
  free=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)
  etk=$(cat "$SHM" 2>/dev/null)
  # 1. a6xx GPU fault newer than arm-mark
  fl=$(dmesg | grep -E "a6xx_irq.*gpu fault ring" | tail -1)
  fts=$(echo "$fl" | sed -n 's/^\[ *\([0-9]*\).*/\1/p')
  [ -n "$fts" ] && [ "$fts" -gt "$MARK" ] && crash="ADRENO status=$(echo "$fl" | sed -n 's/.*status \([0-9A-Fa-f]*\).*/\1/p')"
  # 2. live_stat freshness (silent freeze / process death)
  if ingame "$etk"; then seen=1; [ "$etk" = "$prev" ] && stale=$((stale+1)) || stale=0
  else [ "$seen" = 1 ] && stale=$((stale+1)); fi
  prev="$etk"
  [ -z "$crash" ] && [ "$seen" = 1 ] && [ "$stale" -ge "$STALE_TICKS" ] && crash="SILENT (live_stat stale ${stale}x)"
  # 3. core dump / RPCS3.log fatal (SPU/segfault class)
  [ "$(ls "$CORES" 2>/dev/null | wc -l)" -gt "$base" ] && crash="${crash:+$crash; }NEW CORE"
  tail -8 "$LOG" 2>/dev/null | grep -qiE "Segfault|fatal error|Thread terminated" && crash="${crash:+$crash; }RPCS3 FATAL"
  printf '%s | GPU %s°C %sMHz | CPUp %s°C %sMHz | free %sMB rpcs3 %sMB | bat %s°C | etk[%s]%s\n' \
    "$(date +%H:%M:%S)" "$(zt 15)" "${gpuf:-?}" "$(zt 10)" "${cpf:-?}" "$free" "${rss:-?}" "$(zt 25)" "$etk" "${crash:+  *** $crash ***}"
  [ -n "$crash" ] && break
  sleep "$INT"
done
if [ -n "$crash" ]; then
  echo "[spotter] >>> CRASH: $crash"
  dmesg | grep -E "a6xx_irq.*gpu fault ring|hangcheck recover|rb 0: fence" | tail -3
  # --- finalize hangrd .rd + ib1 sidecar (the decode key) ---
  if [ -n "$RDFILE" ]; then
    # on an a6xx hang the cat unblocks + flushes the redump; wait up to 6s for the write to finish
    for _i in 1 2 3 4 5 6; do kill -0 "$HANGRD_PID" 2>/dev/null || break; sleep 1; done
    kill "$HANGRD_PID" 2>/dev/null   # no-op if already done (e.g. SILENT/CORE class: cat still blocking)
    rdsz=$(wc -c < "$RDFILE" 2>/dev/null || echo 0)
    { echo "$fl"
      echo "$fl" | sed -n 's/.*fence \([0-9a-fx]*\) status \([0-9A-Fa-f]*\) rb \([0-9a-f/]*\) ib1 \([0-9A-Fa-f]*\)\/\([0-9a-f]*\) ib2 \([0-9A-Fa-f]*\)\/\([0-9a-f]*\).*/fence=\1 status=\2 rb=\3 ib1=\4 ib1_size=\5 ib2=\6 ib2_size=\7/p'
    } > "$RDFILE.faultinfo"
    echo "[spotter] hangrd .rd: $RDFILE (${rdsz}B) + $(basename "$RDFILE").faultinfo"
    if [ "${rdsz:-0}" -lt 1024 ]; then echo "[spotter] WARN: .rd header-only (${rdsz}B) — hangrd didn't capture a cmdstream (dossier 6a); decode would be empty"; else echo "[spotter]   $(cat "$RDFILE.faultinfo" | tail -1)"; fi
  fi
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
  SHOT="/tmp/spotter_crash_$(date +%H%M%S).png"
  command -v grim >/dev/null && timeout 3 grim "$SHOT" 2>/dev/null && echo "[spotter] frame $SHOT ($(wc -c < "$SHOT" 2>/dev/null)B)"
  echo "[spotter] state: AppRun=$(pgrep -x AppRun.wrapped | tr '\n' ' ') input_d=$(pgrep -f input_d.py >/dev/null && echo ALIVE || echo dead) active_id=$(cat /dev/shm/etk_shm/active_id.txt 2>/dev/null)"
  echo "[spotter] tune=$(cat /storage/games-internal/roms/etk/etk_telemetry/active_tune.txt 2>/dev/null)"
  echo "[spotter] ledger: $(tail -1 /storage/games-internal/roms/etk/etk_telemetry/sessions.tsv 2>/dev/null | cut -f1,2,5,10,11,15)"
else
  # disarm hangrd: kill the blocking cat and drop the header-only stub
  if [ -n "$HANGRD_PID" ]; then
    kill "$HANGRD_PID" 2>/dev/null
    [ -n "$RDFILE" ] && [ "$(wc -c < "$RDFILE" 2>/dev/null || echo 0)" -lt 1024 ] && rm -f "$RDFILE"
    echo "[spotter] hangrd disarmed (no crash); empty .rd removed."
  fi
  echo "[spotter] window ended — NO crash caught (possible CLEAN run; check ledger)."
fi
