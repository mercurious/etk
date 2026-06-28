#!/bin/bash
# ==========================================================
# ETK DP-MIRROR DAEMON — Approach B (capture native on DP, mirror to handheld)
# ==========================================================
# PURPOSE: a capture card / OBS records the game at native quality on DP-1 while
# the operator still sees it on the handheld (DSI-1).
#
# DESIGN (Approach B — replaces the failed Approach A):
#   - Game renders NATIVELY on the external/capture output (DP-1), where ROCKNIX
#     already puts it on connect (sway `workspace 1 output DP-1 DSI-1` +
#     `for_window emulationstation move output DP-1`). We DON'T move it.
#   - wl-mirror captures DP-1 and displays fullscreen on the handheld (DSI-1).
#     captured output != displayed output  ==>  feedback loop impossible in
#     steady state (the bug that froze Approach A).
#
# THE HANDHELD-PANEL FIGHT: ROCKNIX's /usr/bin/output_monitor implements a
# "switch to TV" policy — on external connect it powers the internal panel OFF.
# Approach B NEEDS it ON (that's where we display the mirror). So while mirroring
# we STOP output_monitor (pkill — the game still reaches DP-1 via sway's own
# rules, independent of that script) and keep DSI-1 enabled. We also NEVER launch
# wl-mirror unless DSI-1 is confirmed on, and we KILL wl-mirror instantly if DSI-1
# ever drops or DP-1 disconnects — so wl-mirror can never fall back onto DP-1 and
# feedback-loop. output_monitor respawns on reboot (sway re-execs it); we re-kill
# it whenever we mirror. On unplug, sway falls ws1 back to DSI-1 on its own.
#
# TRADE-OFF: capture is native/zero-lag; the HANDHELD view carries wl-mirror's
# ~1-frame latency. That's the thing to evaluate.
#
# MODES (ETK_DP_MIRROR in etk.conf, live): 1=mirror, 0=record-only (no wl-mirror,
# leave ROCKNIX TV-mode: game on DP-1, handheld off).
# ORIENTATION: DP links only in the Type-C "normal" orientation (kernel AUX bug).
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

WLM="${ETK_ROOT:-/storage/games-internal/roms/etk}/tools/wl-mirror"
CONF="${ETK_ROOT:-/storage/games-internal/roms/etk}/etk.conf"
DP_STATUS="/sys/class/drm/card0-DP-1/status"
CAP_OUT="DP-1"     # capture source: game renders here natively
VIEW_OUT="DSI-1"   # handheld: wl-mirror displays the DP-1 mirror here
BACKEND="auto"     # = export-dmabuf on this rig (zero-copy, low latency)
LOG="${SHM_DIR:-/tmp}/dpmirror.log"
MIRROR_PID=""
LAST_START=0

_log() { echo "$(date '+%H:%M:%S') $*" >>"$LOG" 2>/dev/null; }

_swaysock() {
    [ -S "${SWAYSOCK:-/nonexistent}" ] && return 0
    SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock 2>/dev/null | head -1)"
    export SWAYSOCK
    [ -S "${SWAYSOCK:-/nonexistent}" ]
}
_sway() { _swaysock || return 1; swaymsg "$@" >/dev/null 2>&1; }

_session_up() { _swaysock; }
_dp_connected() { [ "$(cat "$DP_STATUS" 2>/dev/null)" = "connected" ]; }
_mirror_alive() { [ -n "$MIRROR_PID" ] && kill -0 "$MIRROR_PID" 2>/dev/null; }

# Is output $1 enabled/active in sway? (returns 0 = active)
_out_active() {
    _swaysock || return 1
    swaymsg -t get_outputs 2>/dev/null | python3 -c "import json,sys
name=sys.argv[1]
sys.exit(0 if any(o.get('name')==name and o.get('active') for o in json.load(sys.stdin)) else 1)" "$1" 2>/dev/null
}

# Is the capture output already at 1920x1080? (returns 0 = yes). ROCKNIX defaults
# DP-1 to 1280x720, at which MangoHUD (font sized for the 1080p internal panel)
# renders oversized and overflows off the right edge — and capture quality is
# lower. We bump it to 1080p once (conditionally, to avoid a modeset storm).
_dp_mode_ok() {
    _swaysock || return 1
    swaymsg -t get_outputs 2>/dev/null | python3 -c "import json,sys
name=sys.argv[1]
for o in json.load(sys.stdin):
    if o.get('name')==name:
        cm=o.get('current_mode') or {}
        sys.exit(0 if cm.get('width')==1920 and cm.get('height')==1080 else 1)
sys.exit(1)" "$CAP_OUT" 2>/dev/null
}

_mirror_enabled() {
    local v
    v=$(sed -n 's/^[[:space:]]*ETK_DP_MIRROR=//p' "$CONF" 2>/dev/null \
          | tail -1 | tr -d '"'"'"' \t')
    [ "${v:-${ETK_DP_MIRROR:-1}}" = "1" ]
}

# SIGKILL: instant, so wl-mirror can never linger and fall back onto DP-1.
_stop_mirror() {
    [ -n "$MIRROR_PID" ] && kill -9 "$MIRROR_PID" 2>/dev/null
    pkill -9 -f "$WLM" 2>/dev/null
    [ -n "$MIRROR_PID" ] && _log "mirror killed (pid=$MIRROR_PID)"
    MIRROR_PID=""
}

_start_mirror() {
    _mirror_alive && return
    [ -x "$WLM" ] || { _log "wl-mirror missing at $WLM"; return; }
    _dp_connected || return
    # HARD PRECONDITION: never launch unless the handheld panel is on, else
    # wl-mirror has no valid display target and could fall back onto DP-1.
    _out_active "$VIEW_OUT" || { _log "defer start: $VIEW_OUT not active yet"; return; }
    local now; now=$(date +%s 2>/dev/null || echo 0)
    [ "$LAST_START" -ne 0 ] && [ $((now - LAST_START)) -lt 8 ] && return   # crash-loop backoff
    LAST_START=$now
    setsid "$WLM" -b "$BACKEND" --no-show-cursor -s fit \
        --fullscreen-output "$VIEW_OUT" "$CAP_OUT" >>"$LOG" 2>&1 &
    MIRROR_PID=$!
    _log "mirror started pid=$MIRROR_PID backend=$BACKEND (capture $CAP_OUT -> view $VIEW_OUT)"
}

_reconcile() {
    _mirror_alive || MIRROR_PID=""
    if _dp_connected && _session_up; then
        if _mirror_enabled; then
            # CONDITIONAL ONLY — issuing `output ...` commands unconditionally here
            # generates output events that re-trigger this reconcile = a ~14/s
            # command storm that resets ES's menu and eats gamepad input. In
            # steady state (output_monitor already dead, DSI-1 already on, mirror
            # already running) this branch must issue ZERO sway commands.
            pgrep -f /usr/bin/output_monitor >/dev/null 2>&1 && pkill -f /usr/bin/output_monitor
            _out_active "$VIEW_OUT" || _sway output "$VIEW_OUT" enable
            # Bump capture output to 1080p once (correct HUD scale + quality).
            _dp_mode_ok || _sway output "$CAP_OUT" mode 1920x1080@60Hz
            # SAFETY: mirror running but its display panel vanished -> kill it now
            # so it can't relocate onto DP-1 and feedback.
            if _mirror_alive && ! _out_active "$VIEW_OUT"; then
                _log "guard: $VIEW_OUT lost while mirroring -> kill"; _stop_mirror
            fi
            _start_mirror
        else
            _stop_mirror   # record-only: leave ROCKNIX TV-mode (game on DP-1, handheld off)
        fi
    else
        # DP gone / no session: kill immediately. sway falls ws1 back to DSI-1.
        _stop_mirror
    fi
}

trap '_stop_mirror; exit 0' TERM INT

# SHM_DIR is seeded by the Sentry at boot; ensure the log dir exists in case we
# start first, so diagnostics aren't silently lost.
mkdir -p "$(dirname "$LOG")" 2>/dev/null
: > "$LOG" 2>/dev/null
_log "dpmirror_d (Approach B) starting (event-driven)"
_reconcile

while :; do
    if _swaysock; then
        exec 3< <(swaymsg -t subscribe -m '["output"]' 2>/dev/null)
        while :; do
            read -t 3 -u 3 _ev; rc=$?
            _reconcile
            [ "$rc" -eq 1 ] && break          # EOF: subscription died -> rebuild
        done
        exec 3<&- 2>/dev/null
    else
        _reconcile
        sleep 3
    fi
done
