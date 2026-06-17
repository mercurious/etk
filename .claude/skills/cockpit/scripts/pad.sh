#!/usr/bin/env bash
# cockpit/pad — DRIVER MODE: inject gamepad input via sendevent (buttons + analog).
# EXPLICIT OPT-IN ONLY — this moves the car and can wreck a run. Always finish with release-all.
# Usage: pad.sh <action> [value]
#   unpause                 START button (resume/pause)
#   throttle on|off         hold/release X/cross (BTN_SOUTH) — common accel mapping
#   gas on|off              analog accelerator (ABS_GAS full/zero)
#   brake on|off            analog brake (ABS_BRAKE full/zero)
#   steer <-32767..32767>   left .. right (0 = center)
#   tap <keycode>           press+release an arbitrary evdev button code
#   release-all             zero everything (DO THIS WHEN YOU STOP)
# Env: ADB, GP (gamepad node, auto-detected via BTN_GAMEPAD if unset)
set -uo pipefail
ADB="${ADB:-$(command -v adb 2>/dev/null || echo /Volumes/aps3e-build/android-sdk/platform-tools/adb)}"
GP="${GP:-$("$ADB" shell 'for d in /dev/input/event*; do getevent -lp "$d" 2>/dev/null | grep -q BTN_GAMEPAD && echo "$d" && break; done' 2>/dev/null | tr -d '\r' | head -1)}"
[ -z "$GP" ] && { echo "no gamepad node found; set GP=/dev/input/eventN"; exit 2; }

EV_SYN=0; EV_KEY=1; EV_ABS=3
BTN_SOUTH=304; BTN_EAST=305; BTN_WEST=308; BTN_TR2=313; BTN_START=315   # evdev key codes
ABS_X=0; ABS_GAS=9; ABS_BRAKE=10                          # evdev abs codes
# Per-game control mapping — CONFIRM with a getevent capture before driving (see SKILL.md
# "confirm the mapping"): the button a game uses for accel/brake is NOT guaranteed.
# Defaults = Retroid Flip2 / GT5P-on-aPS3e, captured 2026-06-16: accel=BTN_EAST(305),
# brake=BTN_WEST(308), steer=ABS_X. (NOT BTN_SOUTH — that face button is unmapped in GT5P here,
# which is exactly why an earlier "throttle" did nothing and coasted the car into the wall.)
ACCEL_BTN="${PAD_ACCEL_BTN:-$BTN_EAST}"; BRAKE_BTN="${PAD_BRAKE_BTN:-$BTN_WEST}"

_key(){ "$ADB" shell "sendevent $GP $EV_KEY $1 $2; sendevent $GP $EV_SYN 0 0"; }
_abs(){ "$ADB" shell "sendevent $GP $EV_ABS $1 $2; sendevent $GP $EV_SYN 0 0"; }

case "${1:-}" in
  unpause|start)      _key $BTN_START 1; _key $BTN_START 0 ;;
  throttle|accel)    [ "${2:-on}" = off ] && _key $ACCEL_BTN 0 || _key $ACCEL_BTN 1 ;;
  brake)             [ "${2:-on}" = off ] && _key $BRAKE_BTN 0 || _key $BRAKE_BTN 1 ;;
  gas)               [ "${2:-on}" = off ] && _abs $ABS_GAS 0  || _abs $ABS_GAS 32767 ;;   # analog-trigger games
  abrake)            [ "${2:-on}" = off ] && _abs $ABS_BRAKE 0|| _abs $ABS_BRAKE 32767 ;;  # analog-trigger games
  steer)             _abs $ABS_X "${2:-0}" ;;
  tap)               _key "${2:?need keycode}" 1; _key "${2}" 0 ;;
  release-all)       _key $ACCEL_BTN 0; _key $BRAKE_BTN 0; _key $BTN_SOUTH 0; _key $BTN_START 0; _key $BTN_TR2 0; _abs $ABS_X 0; _abs $ABS_GAS 0; _abs $ABS_BRAKE 0; echo "all inputs released" ;;
  *) echo "actions: unpause | throttle|accel on|off | brake on|off | gas on|off (analog) | abrake on|off (analog) | steer <-32767..32767> | tap <code> | release-all"; exit 1 ;;
esac
