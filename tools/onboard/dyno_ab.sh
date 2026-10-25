#!/bin/bash
# ==========================================================
# tools/onboard/dyno_ab.sh -- ONBOARD observer dyno A/B: one arm run on car8
# ==========================================================
# Runs ONE GT6 Apricot Hill session through gt5_trial.py (the same macro every
# time) either alone (off) or with the ONBOARD window attached as a GAME
# observer (on), so the ledger gets same-shaped rows for tools/etk_dyno.py.
# The observer is started ~25 s after the launch (after the trial's HELLO
# holds the wheel, so the window lands as an observer, never the controller)
# and stopped when the trial ends. The window records a ring beside the run.
#
#   tools/onboard/dyno_ab.sh off|on NAME [OUTROOT]     (default OUTROOT state/hunt/ab-onboard-<date>)
#   DYNO_GAME=BCUS98114 DYNO_MACRO=tools/autopilot/macros/gt5_b2_start.macro tools/onboard/dyno_ab.sh on ...
#                                         (another title/route; default GT6 Apricot Hill)
#
# Alternate arms (off on off on ...), N>=3 each, then:
#   awk the two arms' epochs out of the rig ledger into two files and run
#   tools/etk_dyno.py --game BCUS98296 --ledger <file> on each (the dyno keys arms on
#   tune_tag, which both arms share -- the split by epoch is the arm).
# ==========================================================
set -u
ARM="${1:?off|on}"; NAME="${2:?run name}"; ROOT="${3:-state/hunt/ab-onboard-$(date +%Y%m%d)}"
cd "$(dirname "$0")/../.." || exit 1
OUT="$ROOT/$NAME"; mkdir -p "$OUT"
GAME="${DYNO_GAME:-BCUS98296}"
MACRO="${DYNO_MACRO:-tools/autopilot/macros/gt6_apricot_ab.macro}"
# DYNO_TRIAL_ARGS: extra gt5_trial flags, e.g. "--freeze-secs 25" to thread-dump a stall early
# shellcheck disable=SC2086
python3 tools/autopilot/gt5_trial.py --game "$GAME" --host etk-rig-usb --macro "$MACRO" \
    --out "$OUT" --secs 900 --label "$NAME" ${DYNO_TRIAL_ARGS:-} > "$OUT.log" 2>&1 &
TRIAL=$!
WIN=""
if [ "$ARM" = "on" ]; then
    sleep "${DYNO_OBS_DELAY:-25}"    # after the trial's HELLO holds the wheel; --attach runs need less
    ./onboard --game --ring "$OUT/onboard" > "$OUT/onboard.log" 2>&1 &
    WIN=$!
    echo "$(date +%T) observer window pid $WIN attached" >> "$OUT.log"
fi
wait $TRIAL; RC=$?
[ -n "$WIN" ] && { kill "$WIN" 2>/dev/null; wait "$WIN" 2>/dev/null; echo "$(date +%T) observer window stopped" >> "$OUT.log"; }
echo "$(date +%T) trial exit $RC" >> "$OUT.log"
# FREEZE: the trial dumped the threads once; a second dump ~10 s on says whether anything moved
if [ "$RC" = 5 ]; then
    sleep 10
    python3 tools/pitlink/pitlink.py garage --out "$OUT/dump2.txt" dump_threads timeout=8 > /dev/null 2>&1
    echo "$(date +%T) second thread dump: $(wc -c < "$OUT/dump2.txt" 2>/dev/null || echo 0) bytes" >> "$OUT.log"
fi
# the trial keeps only the log's last 400 lines; keep the whole session (a live car's log is the session's)
ssh -o ConnectTimeout=5 etk-rig-usb cat /storage/.cache/rpcs3/RPCS3.log > "$OUT/RPCS3_full.log" 2>/dev/null
# CRASH (3) / FREEZE (5) leave the car up with RPCS3 still holding the game (an anti-lock stop does
# not exit the process): R3 it, or the next arm refuses with NO_GAME
if [ "$RC" = 3 ] || [ "$RC" = 5 ]; then
    echo "$(date +%T) recover: $(python3 tools/autopilot/gtpilot.py --host etk-rig-usb recover 2>&1 | tr '\n' ' ')" >> "$OUT.log"
fi
exit $RC
