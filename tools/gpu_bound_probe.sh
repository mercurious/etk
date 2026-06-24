#!/bin/sh
# =============================================================================
# gpu_bound_probe.sh  --  GPU-bound vs CPU-bound characterizer (DISPOSABLE)
# -----------------------------------------------------------------------------
# WHY: GT5P FPS is flat across resolution AND barrier gear (G-INSTR ledger), so
# the ~20 fps ceiling sits upstream of both. This decides WHICH: if the GPU is
# saturated we chase it (OC / bin-tune); if it has headroom the wall is RPCS3.
#
# METHOD (valid because the GPU governor is simple_ondemand = LOAD-true):
#   - Sample GPU cur_freq every INT s and histogram it => residency. Direct and
#     robust (trans_stat only flushes on a transition, so it lies at idle).
#     % of samples at 800 MHz (max) is the headline.
#   - Per-thread RPCS3 CPU (utime+stime jiffies, start->end diff) => the hottest
#     emulator thread (rsx::thread / PPU / SPU) and total cores busy.
#   - Live fps/frametime off the ETK live_stat bridge for context.
# Reads sysfs + /proc only; touches nothing permanent. BusyBox-clean (no join).
# Run backgrounded at race start: `ssh RIG "sh -s [DUR] [INT]" < this`.
# =============================================================================
DUR="${1:-90}"; INT="${2:-2}"
GPU=/sys/class/devfreq/3d00000.gpu
PRIME=/sys/devices/system/cpu/cpufreq/policy7/scaling_cur_freq
SHM=/dev/shm/etk_shm/live_stat.txt
CLK=$(getconf CLK_TCK 2>/dev/null || echo 100)
TMP=/tmp/.gpu_bound.$$; mkdir -p "$TMP"; SAMP="$TMP/samp"; : > "$SAMP"

snap_threads() {   # tid comm jiffies(utime+stime) for every RPCS3 thread
  [ -n "$1" ] || return
  for t in /proc/"$1"/task/*; do
    awk '{c=$2; gsub(/[()]/,"",c); print "'"${t##*/}"'", c, $14+$15}' "$t/stat" 2>/dev/null
  done
}

pid=""; i=0
while [ "$i" -lt 30 ]; do
  pid=$(pgrep -f 'AppRun.wrapped' | head -1); [ -n "$pid" ] && break
  sleep 2; i=$((i+1))
done
echo "[bound] rpcs3 pid=${pid:-NONE-(launch the game)}  dur=${DUR}s int=${INT}s  -- DRIVE A STEADY LAP"

snap_threads "$pid" > "$TMP/thr_start"
t0=$(awk '{print int($1)}' /proc/uptime); end=$(( t0 + DUR ))
while [ "$(awk '{print int($1)}' /proc/uptime)" -lt "$end" ]; do
  gf=$(awk '{printf "%d",$1/1000000}' "$GPU/cur_freq" 2>/dev/null)
  pf=$(awk '{printf "%d",$1/1000}' "$PRIME" 2>/dev/null)
  echo "${gf:-0} ${pf:-0}" >> "$SAMP"
  printf '%s  GPU %sMHz  CPUp %sMHz | etk[%s]\n' "$(date +%H:%M:%S)" "${gf:-?}" "${pf:-?}" "$(cat $SHM 2>/dev/null)"
  sleep "$INT"
done
[ -z "$pid" ] && pid=$(pgrep -f 'AppRun.wrapped' | head -1)
snap_threads "$pid" > "$TMP/thr_end"
elapsed=$(( $(awk '{print int($1)}' /proc/uptime) - t0 ))

echo ""; echo "===== GPU RESIDENCY (cur_freq histogram, ${elapsed}s, simple_ondemand=load-true) ====="
awk '
  { g[$1]++; tg++; if($1==800)max++; gsum+=$1; if($2>0){p[$2]++; psum+=$2; tp++} }
  END{ if(tg==0){print "  (no samples)"; exit}
       for(f in g) printf "  GPU %4d MHz : %5.1f%%  (%d)\n", f, 100*g[f]/tg, g[f]
       printf "  ---- GPU avg %.0f MHz; %.0f%% of samples at MAX (800) ----\n", gsum/tg, 100*max/tg
       printf "  ---- CPU-prime avg %.0f MHz (max 2841) ----\n", (tp?psum/tp:0) }
' "$SAMP" | sort

echo ""; echo "===== TOP RPCS3 THREADS (CPU core-fraction over window) ====="
awk -v el="$elapsed" -v clk="$CLK" '
  FNR==NR { s[$1]=$3; next }
  { d=$3-s[$1]; if(d<0)d=0; pct=100*(d/clk)/el; if(pct>3) print pct, $2 }
' "$TMP/thr_start" "$TMP/thr_end" | sort -rn | awk '
  { printf "  %-18s %5.0f%% of a core\n", $2, $1; tot+=$1 }
  END{ printf "  ---- ~%.1f cores busy across RPCS3 ----\n", tot/100 }'

echo ""
echo "[bound] READ: GPU ~100% at 800 + no single thread ~100% => GPU-bound (OC / bin-tune is the lever)."
echo "[bound]       GPU avg well under 800 while one thread pegs a core => CPU-bound (wall is RPCS3, move emulator-side)."
rm -rf "$TMP"
