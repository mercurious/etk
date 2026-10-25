#!/bin/sh
# ==========================================================
# ETK ONBOARD CAP -- one lobby-cam frame (JPEG) on stdout
# ==========================================================
# The "jpeg-grim" backend of ONBOARD (etk-dossiers/Onboard_LiveMirror_20261025.md
# section 8.3). bin/etk_pitlink_usbd.py runs this ONCE PER FRAME for an open
# `onboard` channel and starts the next only after the previous frame left
# the car (natural back-pressure); with no channel open nothing runs it.
#
#   onboard_cap.sh QUALITY SCALE [OUTPUT]
#     QUALITY  JPEG quality 1..100
#     SCALE    grim output scale, e.g. 0.5 (960x540) or 1 (native 1920x1080)
#     OUTPUT   wl output name (default DSI-1: the panel; a plugged DP-1 stays
#              out of the picture)
#
# grim un-rotates the panel (DSI-1 is 1080x1920, transform 90) and delivers
# landscape. `exec` makes this process BE grim, so the daemon's kill on
# CLOSE / link loss reaches the capture itself. Exit 2 = bad arguments.
# BusyBox sh compatible; no GNU-only flags.
#
# Wayland session env as bin/screenshot.sh: the daemon is a systemd service
# with no inherited Wayland env (ROCKNIX sway defaults; keep these in sync).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

Q="$1"
S="$2"
O="${3:-DSI-1}"

case "$Q" in
    ''|*[!0-9]*) echo "onboard_cap: quality must be 1..100 (got '$Q')" >&2; exit 2 ;;
esac
if [ "$Q" -lt 1 ] || [ "$Q" -gt 100 ]; then
    echo "onboard_cap: quality must be 1..100 (got '$Q')" >&2; exit 2
fi
case "$S" in
    ''|.|*[!0-9.]*|*.*.*) echo "onboard_cap: scale must be a number like 0.5 (got '$S')" >&2; exit 2 ;;
esac
case "$O" in
    ''|-*|*[!A-Za-z0-9_-]*) echo "onboard_cap: bad output name '$O'" >&2; exit 2 ;;
esac

exec grim -o "$O" -t jpeg -q "$Q" -s "$S" -
