#!/bin/bash
# GT5 boot dialog -> B-2 card by hand-paced Pitlink taps, a frame saved at each checkpoint.
# usage: tools/autopilot/gt5_drive_to_b2.sh OUTDIR   (GT5 at its boot dialog, no other controller)
# Then attach the trial from the card: DYNO_MACRO=tools/autopilot/macros/gt5_b2_card_start.macro
# DYNO_TRIAL_ARGS="--attach --freeze-secs 25" DYNO_OBS_DELAY=4 tools/onboard/dyno_ab.sh on NAME OUTROOT.
# Check 4_b2_card.png before attaching: a "no frame" line means flips stopped (the gFKsE park).
cd "$(dirname "$0")/../.." || exit 1
D="$1"; mkdir -p "$D"; T=tools/pitlink/pitlink.py
w(){ local t0; t0=$(date +%s); until [ $(( $(date +%s) - t0 )) -ge "$1" ]; do sleep 1; done; }
p(){ python3 $T press "$1" >/dev/null; }
f(){ python3 $T frame "$D/$1.png" >/dev/null 2>&1 || echo "no frame at $1"; }
f 0_dialog; p cross; w 30
p start; w 10; f 1_menu
for i in 1 2 3; do p left; w 2; done
p cross; w 30; f 2_gt_home
p right; w 2; p down; w 2; f 3_license_tile
p cross; w 10; p cross; w 8; p right; w 3; f 4_b2_card
echo done
