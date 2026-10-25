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
MACRO=tools/autopilot/macros/gt6_apricot_ab.macro
python3 tools/autopilot/gt5_trial.py --game BCUS98296 --host etk-rig-usb --macro "$MACRO" \
    --out "$OUT" --secs 900 --label "$NAME" > "$OUT.log" 2>&1 &
TRIAL=$!
WIN=""
if [ "$ARM" = "on" ]; then
    sleep 25
    ./onboard --game --ring "$OUT/onboard" > "$OUT/onboard.log" 2>&1 &
    WIN=$!
    echo "$(date +%T) observer window pid $WIN attached" >> "$OUT.log"
fi
wait $TRIAL; RC=$?
[ -n "$WIN" ] && { kill "$WIN" 2>/dev/null; wait "$WIN" 2>/dev/null; echo "$(date +%T) observer window stopped" >> "$OUT.log"; }
echo "$(date +%T) trial exit $RC" >> "$OUT.log"
exit $RC
