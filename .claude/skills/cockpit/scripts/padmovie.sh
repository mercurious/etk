#!/usr/bin/env bash
# ETK Cockpit — native pad-movie (v2.2 three-mark, rolling-start offset fix) via adb.
# Supersedes the external evdev record.sh/replay.sh: the record/replay lives INSIDE
# the aPS3e fork (cellPad.cpp cellPadGetData hook), keyed to the game's read cadence.
#
# v2.2 model: GT5P forces a rolling start whose state isn't reproducible, so the anchor
# is the LAP LINE. Mark the run by hand with the R1 + D-pad-Down chord (both unassigned
# in GT5P), THREE presses:
#   1) MARK-IN     at the rolling start (the approach/offset segment begins)
#   2) MARK-OFFSET crossing the lap line (record stores the lap-line frame index; on
#                  replay the cursor RE-SYNCS here, cancelling rolling-start drift)
#   3) MARK-OUT    at the end of the run
# Replay drives the approach toward the line, then locks onto the lap data at MARK-OFFSET.
# The movie carries a paired sidecar: <name>.bin + <name>.offset (lap-line index) — keep
# them together; replay/pull move both.
#
# Prefer the CHORD for timed runs (in-controller, no hands off the wheel). The 'mark'
# command below touches pad_movie.mark = one toggle pulse (advances IN->OFFSET->OUT across
# calls), for scripted/non-driving use only — adb timing adds the variance v2.2 removes.
#
# Usage:
#   padmovie.sh record            # arm RECORD (truncates device bin + offset)
#   padmovie.sh replay [file.bin] # arm REPLAY; if file.bin given, push it + its .offset sidecar
#   padmovie.sh mark              # fire ONE toggle pulse (IN->OFFSET->OUT) — scriptable alt to chord
#   padmovie.sh pull <name>       # pull device movie -> <name>.bin AND <name>.offset (post-record)
#   padmovie.sh off               # clear mode + mark + offset -> normal play
#   padmovie.sh status            # show on-device trigger state (incl. offset)
#
# Always-reboot rule: validate replay from a COLD boot. Nothing counts until it survives one.
set -uo pipefail
ADB="${ADB:-$(command -v adb 2>/dev/null)}"
PKG="${PKG:-aenu.aps3e}"
CACHE="${CACHE:-/sdcard/Android/data/$PKG/files/aps3e/cache}"
DIR="${HARNESS_DIR:-$HOME/etk-cockpit-harness}"
MODE_F="$CACHE/pad_movie.mode"; BIN_F="$CACHE/pad_movie.bin"
MARK_F="$CACHE/pad_movie.mark"; OFFSET_F="$CACHE/pad_movie.offset"

die() { echo "[padmovie] $*" >&2; exit 1; }
[ -n "$ADB" ] || die "adb not found"
"$ADB" get-state >/dev/null 2>&1 || die "no adb device"

cmd="${1:-status}"; shift || true
case "$cmd" in
  record)
    "$ADB" shell "rm -f '$MARK_F' '$OFFSET_F'; printf record > '$MODE_F'" \
      && echo "[padmovie] armed RECORD. R1+Down x3: MARK-IN at rolling start, MARK-OFFSET crossing the lap line, MARK-OUT at the end."
    ;;
  replay)
    if [ "${1:-}" ]; then
      f="$1"; [ -f "$f" ] || f="$DIR/$1"; [ -f "$f" ] || die "movie not found: $1"
      "$ADB" push "$f" "$BIN_F" >/dev/null || die "push failed"
      echo "[padmovie] pushed movie: $f"
      # the .offset sidecar (lap line) must travel with the .bin
      off="${f%.bin}.offset"
      if [ -f "$off" ]; then "$ADB" push "$off" "$OFFSET_F" >/dev/null && echo "[padmovie] pushed offset: $off (lap line @ $(cat "$off"))"
      else "$ADB" shell "rm -f '$OFFSET_F'"; echo "[padmovie] NOTE: no $off sidecar -> offset 0 (laps start at frame 0)"; fi
    fi
    "$ADB" shell "rm -f '$MARK_F'; printf replay > '$MODE_F'" \
      && echo "[padmovie] armed REPLAY. R1+Down: MARK-IN at rolling start (approach plays), MARK-OFFSET crossing the lap line (re-sync); laps then play."
    ;;
  mark)
    "$ADB" shell "printf '' > '$MARK_F'" && echo "[padmovie] one toggle pulse fired (advances IN -> OFFSET -> OUT across calls). Scriptable only; use the R1+Down chord for timed runs."
    ;;
  pull)
    [ "${1:-}" ] || die "usage: padmovie.sh pull <name>"
    out="$DIR/$1.bin"
    "$ADB" pull "$BIN_F" "$out" >/dev/null && echo "[padmovie] pulled movie -> $out ($(wc -c < "$out") bytes)"
    # pull the lap-line sidecar too (paired with the bin)
    "$ADB" pull "$OFFSET_F" "$DIR/$1.offset" >/dev/null 2>&1 \
      && echo "[padmovie] pulled offset -> $DIR/$1.offset (lap line @ $(cat "$DIR/$1.offset"))" \
      || echo "[padmovie] NOTE: no offset sidecar on device (MARK-OFFSET not pressed?)"
    ;;
  off|clear)
    "$ADB" shell "rm -f '$MODE_F' '$MARK_F' '$OFFSET_F'" && echo "[padmovie] cleared mode + mark + offset -> normal play."
    ;;
  status)
    echo "[padmovie] cache: $CACHE"
    echo -n "  mode  : "; "$ADB" shell "cat '$MODE_F' 2>/dev/null || echo '(none)'"
    echo -n "  mark  : "; "$ADB" shell "[ -f '$MARK_F' ] && echo present || echo '(none)'"
    echo -n "  bin   : "; "$ADB" shell "ls -l '$BIN_F' 2>/dev/null | awk '{print \$5\" bytes\"}' || echo '(none)'"
    echo -n "  offset: "; "$ADB" shell "cat '$OFFSET_F' 2>/dev/null || echo '(none)'"
    ;;
  *) die "unknown command: $cmd (record|replay|mark|pull|off|status)";;
esac
