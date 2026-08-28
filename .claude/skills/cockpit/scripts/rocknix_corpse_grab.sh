#!/bin/bash
# ETK Cockpit — freeze-corpse forensics (ROCKNIX/ssh). Usage: rocknix_corpse_grab.sh [LABEL]
#
# For the wedge class the spotter's SILENT gate can't see: a PRE-GAME freeze
# (live_stat never went in-game — e.g. the 2026-08-27 GT5P Spec II pre-copyright
# hang). The corpse doesn't decay (a frozen emulator stays frozen until R3), so
# this is a one-shot grab to run the moment the driver reports the hang — BEFORE
# R3. Read-only apart from the capture dir on /storage; gdb attach stops/resumes
# the (already frozen) process.
#
# Captures, in one ssh round-trip, into rig crash_forensics/ + pulls to host
# manual_forensics/: LIVE core attribution (environ APPIMAGE= is ground truth —
# the ledger's core= stamp rots when the launch wrapper is unbound, proven
# 2026-08-27), banner, argv, per-thread state/wchan table, kernel stacks, gdb
# thread backtraces (if gdb present), RPCS3.log/TTY tails, dmesg, live_stat,
# flightrec tail.
set -u
RIG="${COCKPIT_RIG:-root@169.254.170.2}"
LABEL="${1:-wedge}"
HOSTDIR="${COCKPIT_FORENSICS:-$(cd "$(dirname "$0")/../../../.." 2>/dev/null && pwd)/manual_forensics}"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$HOSTDIR/corpse_${STAMP}_${LABEL}"
mkdir -p "$OUT"

ssh -o ConnectTimeout=8 "$RIG" "LABEL='$LABEL' sh -s" << 'REMOTE' | tee "$OUT/corpse_summary.txt"
E=/storage/games-internal/roms/etk
FDIR=$E/etk_telemetry/crash_forensics
CAP="$FDIR/corpse_$(date +%Y%m%d_%H%M%S)_${LABEL}"
mkdir -p "$CAP"
LOG=$(ls -t /storage/*/.cache/rpcs3/RPCS3.log /storage/.cache/rpcs3/RPCS3.log 2>/dev/null | head -1)

# --- find the emulator by argv[0] cmdline walk (pgrep is unreliable here: §Q) ---
PID=""
for c in /proc/[0-9]*/cmdline; do
  exe=$(tr '\0' '\n' < "$c" 2>/dev/null | head -1)
  case "$exe" in *AppRun.wrapped|*EBOOT.BIN|*EMAIN.SELF) PID=${c%/cmdline}; PID=${PID#/proc/};; esac
done

echo "=== CORPSE GRAB $(date) label=$LABEL pid=${PID:-NONE} ==="
echo "--- ATTRIBUTION (live > ledger) ---"
echo "bind: $(cat /storage/rpcs3/loaded 2>/dev/null)"
echo "active_core marker: $(ls -la $E/etk_telemetry/active_core.txt 2>/dev/null)"
echo "  -> $(cat $E/etk_telemetry/active_core.txt 2>/dev/null)"
echo "banner: $(head -c 400 "$LOG" 2>/dev/null | strings | head -1)"
if [ -n "$PID" ]; then
  echo "argv: $(tr '\0' ' ' < /proc/$PID/cmdline 2>/dev/null)"
  echo "APPIMAGE env: $(tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep '^APPIMAGE=' )"
  tr '\0' '\n' < /proc/$PID/environ 2>/dev/null > "$CAP/environ.txt"
fi
echo "etk_mode: $(cat /dev/shm/etk_shm/etk_mode.txt 2>/dev/null)  live_stat: $(cat /dev/shm/etk_shm/live_stat.txt 2>/dev/null)"
echo "load: $(cat /proc/loadavg)  MemAvail: $(awk '/MemAvailable/{print int($2/1024)"MB"}' /proc/meminfo)"

if [ -n "$PID" ]; then
  echo "--- THREAD TABLE (tid state wchan comm) ---"
  { for t in /proc/$PID/task/*; do
      tid=${t##*/}
      st=$(awk '{print $3}' "$t/stat" 2>/dev/null)
      wc=$(cat "$t/wchan" 2>/dev/null)
      cm=$(cat "$t/comm" 2>/dev/null)
      printf '%s\t%s\t%s\t%s\n' "$tid" "$st" "${wc:-?}" "$cm"
    done; } | tee "$CAP/threads.tsv"
  echo "--- state histogram ---"
  awk -F'\t' '{n[$2]++} END{for(s in n) printf "%s:%d ", s, n[s]; print ""}' "$CAP/threads.tsv"
  # kernel stacks: every thread, labeled (file only — noisy on the radio)
  for t in /proc/$PID/task/*; do
    echo "### ${t##*/} $(cat "$t/comm" 2>/dev/null)"
    cat "$t/stack" 2>/dev/null
  done > "$CAP/kstacks.txt"
  echo "kstacks: $(grep -c '^###' "$CAP/kstacks.txt") threads -> $CAP/kstacks.txt"
  # userspace backtraces if gdb exists (frozen corpse: attach-stop is harmless)
  if command -v gdb >/dev/null 2>&1; then
    timeout 120 gdb -p "$PID" -batch -ex 'set pagination off' -ex 'thread apply all bt' \
      > "$CAP/gdb_bt.txt" 2>&1
    echo "gdb: $(grep -c '^Thread' "$CAP/gdb_bt.txt" 2>/dev/null) threads backtraced -> $CAP/gdb_bt.txt"
  else
    echo "gdb: absent on rig — kernel stacks only"
  fi
else
  echo "NO LIVE EMULATOR — process gone (crash/exit, not a freeze); logs still captured"
fi

tail -c 30000 "$LOG" > "$CAP/rpcs3_log_tail.txt" 2>/dev/null
cat "${LOG%RPCS3.log}TTY.log" > "$CAP/tty.txt" 2>/dev/null
dmesg | tail -60 > "$CAP/dmesg_tail.txt" 2>/dev/null
ls -t $E/etk_telemetry/blackbox/flightrec-*.tsv 2>/dev/null | head -1 | xargs -r tail -20 > "$CAP/flightrec_tail.tsv" 2>/dev/null
echo "--- RPCS3.log last lines ---"
tail -c 1200 "$LOG" 2>/dev/null | strings | tail -12
echo "=== CAPTURE DIR $CAP ==="
REMOTE

# pull the capture set home (rig path printed on the last line of the summary)
CAPDIR=$(grep '^=== CAPTURE DIR' "$OUT/corpse_summary.txt" | awk '{print $4}')
if [ -n "${CAPDIR:-}" ]; then
  ssh "$RIG" "cd '$CAPDIR' && tar cf - ." | tar xf - -C "$OUT" 2>/dev/null \
    && echo "[corpse] pulled -> $OUT"
fi
