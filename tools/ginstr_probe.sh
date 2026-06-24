#!/bin/sh
# =============================================================================
# ginstr_probe.sh  --  G-INSTR FPS/frametime readback scout (DISPOSABLE HARNESS)
# -----------------------------------------------------------------------------
# WHY: MangoHud already renders fps + frame_timing (config/MangoHud.conf), but
# ETK can't read those numbers back. Before grafting an FPS column into the
# locked-down core (mango_bridge.sh / session_postmortem.sh / the ledger
# schema), prove on THIS rig's actual MangoHud build which readback mechanism
# works. "Validate before integrate" — this script touches NOTHING permanent.
#
# RUN IT WITH A GAME ACTUALLY RUNNING (MangoHud overlay visible). It:
#   1. Reports the live MangoHud version/build + whether control=mangohud.
#   2. Locates the MangoHud control socket (the `control=mangohud` listener).
#   3. Attempts logging two ways and reports which produced a parseable CSV:
#        (A) config knobs  : autostart_log + log_interval + output_folder
#        (B) control socket: start-logging / stop-logging
#   4. Tails whatever CSV appears and prints the fps + frametime columns so we
#      can see the exact header/column order this build emits.
#
# It writes ONLY under /tmp + a throwaway $OUT dir; it does NOT edit the live
# MangoHud.conf, the SHM bus, or the ledger. BusyBox/POSIX-safe.
# =============================================================================

OUT=/tmp/ginstr_probe
RIG_CONF=/storage/.config/MangoHud/MangoHud.conf
SECS="${1:-15}"          # how long to sample logging

rm -rf "$OUT"; mkdir -p "$OUT"
echo "== G-INSTR probe =="
echo "sample window: ${SECS}s   out: $OUT"
echo

# --- 0. Is a game actually up? (the readback only exists in-frame) ----------
PIDS=$(pgrep -f 'AppRun.wrapped|rpcs3' 2>/dev/null | tr '\n' ' ')
echo "-- emulator pids: ${PIDS:-<none>}"
if [ -z "$PIDS" ]; then
    echo "   WARN: no rpcs3/AppRun.wrapped detected. Launch a game first — MangoHud"
    echo "   computes fps only while presenting. Continuing (some checks are static)."
fi
echo

# --- 1. MangoHud build -------------------------------------------------------
echo "-- MangoHud build --"
( mangohud --version 2>/dev/null || cat /usr/lib*/mangohud/* 2>/dev/null | head -0 ) | head -5
for so in /usr/lib/mangohud/libMangoHud.so /usr/\$LIB/mangohud/libMangoHud.so \
          /usr/lib64/mangohud/libMangoHud.so; do
    [ -f "$so" ] && { echo "   so: $so ($(stat -c %s "$so" 2>/dev/null) bytes)"; \
        strings "$so" 2>/dev/null | grep -iE 'mangohud v?[0-9]+\.[0-9]' | head -2; }
done
echo "   config control line:"; grep -nE '^control|^fps|^frame_timing|^output_folder|^log_' "$RIG_CONF" 2>/dev/null | sed 's/^/     /'
echo

# --- 2. Control socket (the `control=mangohud` listener) ---------------------
echo "-- control socket --"
# MangoHud opens a unix socket named "mangohud" (or mangohud_<pid>) under the
# runtime dir when control=mangohud. Enumerate candidates.
RT="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
echo "   XDG_RUNTIME_DIR=$RT"
SOCKS=$(ls -1 "$RT"/mangohud* "$RT"/MangoHud/* /tmp/mangohud* 2>/dev/null)
echo "   candidate sockets:"; echo "${SOCKS:-     <none found>}" | sed 's/^/     /'
# Abstract namespace sockets won't show in ls; note them from /proc/net/unix.
echo "   abstract @mangohud (from /proc/net/unix):"
grep -i mangohud /proc/net/unix 2>/dev/null | sed 's/^/     /' || echo "     <none>"
echo

# --- 3a. Readback path A: config-driven autolog -----------------------------
# We can't safely hot-edit the live conf here, but we CAN show the operator the
# exact 3 lines to add and where MangoHud would drop the CSV, then (if it's
# already logging from a prior config) detect+parse it.
echo "-- path A: config autolog --"
echo "   to enable (next game launch): append to $RIG_CONF"
echo "     output_folder=$OUT"
echo "     log_interval=1000"
echo "     autostart_log=1"
# Detect any CSV MangoHud may already be producing anywhere obvious.
FOUND_CSV=$(ls -1t "$OUT"/*.csv /storage/.config/MangoHud/*.csv /tmp/*.csv \
             /storage/*.csv 2>/dev/null | head -1)
echo "   existing CSV: ${FOUND_CSV:-<none yet>}"
echo

# --- 3b. Readback path B: control-socket start/stop-logging ------------------
echo "-- path B: socket start/stop-logging --"
# MangoHud control protocol is line-based over the unix socket. Try the common
# verbs; harmless if the socket/verb is unsupported (we just report failure).
SOCK=$(echo "$SOCKS" | head -1)
if [ -n "$SOCK" ] && command -v socat >/dev/null 2>&1; then
    echo "   socat -> $SOCK : start-logging"
    printf 'start-logging\n' | socat - "UNIX-CONNECT:$SOCK" 2>&1 | sed 's/^/     /'
    i=0; while [ "$i" -lt "$SECS" ]; do sleep 1; i=$((i+1)); done
    printf 'stop-logging\n'  | socat - "UNIX-CONNECT:$SOCK" 2>&1 | sed 's/^/     /'
else
    echo "   skip: $( [ -z "$SOCK" ] && echo 'no socket' || echo 'no socat' )."
    echo "   (mangohud ships a control CLI on some builds: 'mangohud --control' / "
    echo "    'mangohudctl'; the harness reports whichever exists below.)"
    command -v mangohudctl mangohud-control 2>/dev/null | sed 's/^/     have: /'
fi
echo

# --- 4. Parse whatever CSV exists -------------------------------------------
echo "-- CSV parse --"
CSV=$(ls -1t "$OUT"/*.csv /storage/.config/MangoHud/*.csv /tmp/*.csv 2>/dev/null | head -1)
if [ -n "$CSV" ]; then
    echo "   csv: $CSV"
    echo "   header:"; head -2 "$CSV" | sed 's/^/     /'
    echo "   last 5 rows:"; tail -5 "$CSV" | sed 's/^/     /'
    # MangoHud CSV is typically: fps,frametime(ms),cpu_load,gpu_load,... with a
    # 2-line preamble. Show fps column stats so we know the readback is sane.
    echo "   fps column (col1) quick stats:"
    tail -n +3 "$CSV" | awk -F, 'NF>1 && $1+0>0 {n++; s+=$1; if($1>mx)mx=$1; if(mn==""||$1<mn)mn=$1}
        END{if(n)printf "     n=%d  min=%.1f  avg=%.1f  max=%.1f\n", n, mn, s/n, mx; else print "     no numeric fps rows"}'
else
    echo "   no CSV produced. => readback needs the config knobs (path A) applied"
    echo "      then a game RELAUNCH (MangoHud reads its conf at swapchain init)."
fi
echo
echo "== probe done. Verdict feeds the mango_bridge.sh / ledger graft. =="
