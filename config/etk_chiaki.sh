#!/bin/sh
# ==========================================================
# ETK CHIAKI LAUNCHER (BUSYBOX COMPLIANT, MENU-FIRST)
# Location: /storage/.config/modules/etk_chiaki.sh
# ==========================================================
# Flow: ES spawns this inside foot -> re-exec at a readable font size
# (Pitstop scale pattern) -> bin/etk_chiaki_menu.py (title screen,
# console chooser, gamepad pairing wizard) -> stream the chosen console
# -> back to the menu when the stream ends -> EXIT returns to ES.
#
# SWAY DOCTRINE (AI_MANIFEST): sway is a tiling compositor. While the
# stream runs, a WATCHDOG loop re-asserts fullscreen on the chiaki
# window every 2s ('fullscreen enable' is idempotent) — a one-shot
# assert proved racy (operator-reported split-screen, 20260730).
# All user feedback is toast-driven; no keyboard prompts anywhere.
# ==========================================================

# 1. Single source of truth for all paths
source /storage/games-internal/roms/etk/scripts/env.sh

# 2. Readable font: the menu is the UI now, not a log dump.
if [ "$ETK_SCALED" != "1" ]; then
    export ETK_SCALED=1
    exec /usr/bin/foot -F -o font="monospace:size=20" "$0" "$@"
fi

# 3. Wayland/SDL environment (SWAYSOCK not ambient in the ES/foot context)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export SDL_VIDEODRIVER=wayland
export SDL_VIDEO_WAYLAND_WMCLASS=chiaki
SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock 2>/dev/null | head -n 1)"
export SWAYSOCK

BIN="${ETK_CHIAKI_BIN:-$ETK_ROOT/tools/chiaki}"
CONF_DIR="${ETK_CHIAKI_CONF_DIR:-/storage/.config/chiaki}"
LOG="/storage/etk_chiaki.log"
NOTIFY="${ETK_CHIAKI_NOTIFY:-$ETK_ROOT/bin/etk_chiaki_notify.sh}"
CHOICE="${ETK_CHIAKI_CHOICE:-/dev/shm/etk_shm/chiaki_menu_choice}"
export ETK_CHIAKI_CHOICE="$CHOICE"

if [ ! -x "$BIN" ]; then
    "$NOTIFY" "Chiaki Missing - rerun install.sh" 2>/dev/null
    sleep 3
    exit 1
fi

# 4. Stream-active sentinel: input_d stands down the chords chiaki owns
#    in-stream (R1+L3 / L1+R3). Cleared by trap; SHM is boot-volatile.
LOCK="${ETK_CHIAKI_LOCK:-/dev/shm/etk_shm/chiaki_active}"
mkdir -p "$(dirname "$LOCK")"
trap 'rm -f "$LOCK"' EXIT INT TERM

# 5. mako toast hook for the binary
export CHIAKI_NOTIFY_CMD="$NOTIFY"

# 6. Menu -> stream -> menu loop. The menu exits 0 with a choice file
#    (stream that config) or 1 (EXIT -> back to ES).
while true; do
    rm -f "$CHOICE"
    python3 "$ETK_ROOT/bin/etk_chiaki_menu.py"
    MENU_RC=$?
    [ "$MENU_RC" -ne 0 ] && break
    CONF="$(cat "$CHOICE" 2>/dev/null)"
    [ -z "$CONF" ] || [ ! -f "$CONF" ] && break

    # keep chiaki.conf mirroring the last-used console (bare `chiaki stream`
    # over ssh and pre-menu muscle memory both keep working)
    cp -f "$CONF" "$CONF_DIR/chiaki.conf" 2>/dev/null

    # 7. Fullscreen WATCHDOG for the stream's lifetime. Idempotent assert
    #    every 2s: survives toggle-reconnects, slow maps, and whatever
    #    caused the one-shot dance to miss. Killed when the stream ends.
    touch "$LOCK"
    (
        while [ -f "$LOCK" ]; do
            swaymsg '[app_id="chiaki"] fullscreen enable' >/dev/null 2>&1
            sleep 2
        done
    ) &
    WATCHDOG=$!

    "$BIN" stream --config "$CONF" > "$LOG" 2>&1
    RC=$?

    rm -f "$LOCK"
    kill "$WATCHDOG" 2>/dev/null
    wait "$WATCHDOG" 2>/dev/null

    # errors surfaced as toasts by the binary; give the toast a beat, then
    # land back on the chooser (not ES) so retry is one press away
    [ "$RC" -ne 0 ] && sleep 2
done

exit 0
