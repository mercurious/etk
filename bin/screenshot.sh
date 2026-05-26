#!/bin/bash
# ==========================================================
# ETK SCREENSHOT — silent grim capture with HUD intact
# ==========================================================
# Fired by bin/input_d.py on SELECT + D-pad-Up. The whole point is
# to capture the ETK HUD overlay (MangoHUD Vulkan layer) alongside
# the game frame — RPCS3's internal Save Screenshot strips it.
#
# Files land at $ETK_ROOT/screenshots/, which is:
#  - inside $ETK_ROOT so install.sh Tier-B backs it up every run
#  - under the SMB [games-internal] share so shots appear instantly at
#    \\<rig-ip>\games-internal\roms\etk\screenshots\ (Win) /
#    smb://<rig-ip>/games-internal/roms/etk/screenshots/  (mac)
#
# NO toast / notification — by design. A mako popup would land on screen
# AFTER grim but BEFORE any follow-up shot, contaminating the next frame
# and breaking the "bragging shot" use case. Operator verifies via SMB.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

# Wayland session env — REQUIRED when this script is invoked from
# input_d.py (which the Sentry runs as a systemd service with no
# inherited Wayland env). Without these, grim exits with "compositor
# is at WAYLAND_DISPLAY= (the empty string)" and writes nothing. The
# values match Rocknix sway defaults (verified 2026-05-26 on
# nightly-20260525). The install.sh mako reload uses the same
# XDG_RUNTIME_DIR path — keep these in sync.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

SCREENSHOTS_DIR="$ETK_ROOT/screenshots"
mkdir -p "$SCREENSHOTS_DIR"

# Game ID scoping for the filename. Reject IDLE/UNKNOWN sentinels and
# substitute ROCKNIX — tells a future Pitstop gallery view "this shot
# was taken in the system UI / Pitstop TUI, not in a game session."
GID=$(cat "$ID_FILE" 2>/dev/null)
case "$GID" in
    ""|IDLE|UNKNOWN_ID) GID="ROCKNIX" ;;
esac

FNAME="etk_${GID}_$(date +%Y%m%d_%H%M%S).png"
# Stderr → .last_error sidecar so future silent failures are
# diagnosable without re-instrumenting (last attempt wins; no rotation).
grim "$SCREENSHOTS_DIR/$FNAME" 2> "$SCREENSHOTS_DIR/.last_error"
