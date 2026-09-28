#!/bin/bash
# Regression test for the cockpit spotter's crash classifier (rocknix_spotter_loop.sh).
#
# Runs the REAL spotter on the host against a fake rig: a fake RPCS3.log, a fake live_stat,
# and a fake emulator (a sleep whose argv[0] ends in AppRun.wrapped — what emu_pid() walks
# /proc for). The copy under test gets only its LOG= and SHM= lines redirected, so any
# spotter version runs unmodified otherwise — including HEAD, to prove a case discriminates:
#
#   tools/test_spotter_loop.sh                          # the working-tree spotter
#   tools/test_spotter_loop.sh <(git show HEAD:.claude/skills/cockpit/scripts/rocknix_spotter_loop.sh)
#
# Cases (2026-09-27, core 0.9.1 exit hang):
#   exit-hang  RPCS3 logs its whole shutdown, then the process stays  -> EXIT-HANG + thread capture
#              (the pre-EXIT-HANG spotter calls this SILENT: a freeze, which it is not)
#   clean-exit the process leaves right after the shutdown log         -> GRACEFUL EXIT with latency
#   mid-race   live_stat freezes, no shutdown log, process alive       -> SILENT (must survive the change)
#   exit-crash a core lands after the shutdown log, then L1+R3          -> EXIT-CRASH (signal) + R3 named
#              (what the rig actually did: abort in a static destructor, the kernel writing the core)
# Host GNU is not the rig's BusyBox; the live rig run is the other half of the proof.
set -u
SPOT="${1:-$(cd "$(dirname "$0")/.." && pwd)/.claude/skills/cockpit/scripts/rocknix_spotter_loop.sh}"
T=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; [ -n "${KEEP:-}" ] && echo "kept $T" || rm -rf "$T"' EXIT
sed -e 's|^LOG=.*|LOG="$SPOT_TEST_LOG"|' -e 's|^SHM=.*|SHM="$SPOT_TEST_SHM"|' \
    -e 's|^cp=.*|cp="$SPOT_TEST_CORES/%e.%p.%s.core"|' -e 's|^R3F=.*|R3F="$SPOT_TEST_R3"|' "$SPOT" > "$T/spotter.sh"
grep -q '^LOG="$SPOT_TEST_LOG"' "$T/spotter.sh" && grep -q '^SHM="$SPOT_TEST_SHM"' "$T/spotter.sh" \
  || { echo "FAIL: could not redirect LOG=/SHM= in $SPOT"; exit 1; }
EXIT_LINE='·! 0:06:18.554093 GUI: gui_application: Deleting old game window'
fail=0

# scenario NAME EXIT_AFTER — EXIT_AFTER: "hang" (process stays), "N" (process leaves N s after the
# shutdown line), "none" (no shutdown line; live_stat just freezes mid-race), "core" (a core lands
# after the shutdown line, then an R3 stamp and the kill)
scenario(){
  name=$1 mode=$2 d="$T/$1"; mkdir -p "$d/fx"
  export SPOT_TEST_LOG="$d/RPCS3.log" SPOT_TEST_SHM="$d/live_stat.txt" SPOT_TEST_CORES="$d/cores" SPOT_TEST_R3="$d/r3_pressed.txt"
  mkdir -p "$d/cores"; : > "$d/cores/AppRun.wrapped.1.11.core"   # an older core: must not fire
  printf '·! 0:00:00.000001 SYS: boot\n' > "$SPOT_TEST_LOG"; : > "$SPOT_TEST_SHM"
  bash -c "exec -a '$d/AppRun.wrapped' sleep 120" & emu=$!
  RESCUE_BREAK=1 HANGRD_CAPTURE=0 EXITHANG_SECS=3 EXITHANG_WAIT=6 EXITHANG_GDB=0 EXITHANG_RESAMPLE=1 \
    FORENSIC_DIR="$d/fx" ETK_ROOT="$d/etk" bash "$T/spotter.sh" 40 1 3 > "$d/out.txt" 2>&1 & spot=$!
  sleep 1.5
  for i in 1 2 3 4 5 6; do echo "62C 1.${i}ms 30fps" > "$SPOT_TEST_SHM"; echo "·! 0:00:0$i PERF: tick" >> "$SPOT_TEST_LOG"; sleep 0.5; done
  case "$mode" in
    none) ;;                                             # freeze with the emulator still alive
    hang) echo "$EXIT_LINE" >> "$SPOT_TEST_LOG" ;;
    core) echo "$EXIT_LINE" >> "$SPOT_TEST_LOG"; sleep 0.3; : > "$d/cores/AppRun.wrapped.$emu.6.core"
          sleep 3; date +%s > "$SPOT_TEST_R3"; kill "$emu" ;;
    *)    echo "$EXIT_LINE" >> "$SPOT_TEST_LOG"; sleep "$mode"; kill "$emu" ;;
  esac
  wait "$spot"; kill "$emu" 2>/dev/null; wait "$emu" 2>/dev/null
}
check(){  # check NAME PATTERN [PATTERN...] — every pattern must appear in the scenario's output
  n=$1; shift
  for p in "$@"; do
    if ! grep -qF -- "$p" "$T/$n/out.txt"; then
      echo "FAIL [$n]: expected \"$p\""; sed 's/^/    | /' "$T/$n/out.txt" | tail -8; fail=1; return
    fi
  done
  echo "ok   [$n]"
}

scenario exit-hang hang
check exit-hang ">>> CRASH: EXIT-HANG" "EXIT-HANG capture ->" "STILL HUNG"
cap=$(cat "$T/exit-hang/fx/.last_exithang" 2>/dev/null)
if [ -n "$cap" ] && [ -s "$cap/threads_a.tsv" ] && [ -s "$cap/threads_b.tsv" ]; then echo "ok   [exit-hang] two thread tables banked"
else echo "FAIL [exit-hang]: no thread tables in the capture dir"; fail=1; fi

scenario clean-exit 0.5
check clean-exit ">>> GRACEFUL EXIT: process left within"

scenario exit-crash core
check exit-crash ">>> CRASH: EXIT-CRASH (signal 6" ">>> L1+R3 ended it"

scenario mid-race none
check mid-race ">>> CRASH: SILENT"

[ "$fail" = 0 ] && echo "PASS: spotter classifier ($SPOT)" || { echo "FAILED: spotter classifier ($SPOT)"; exit 1; }
