#!/bin/sh
# ==========================================================
# ETK CHIAKI LAUNCHER (BUSYBOX COMPLIANT, TOAST-DRIVEN)
# Location: /storage/.config/modules/etk_chiaki.sh
# ==========================================================
# ES spawns Tools entries inside a foot terminal (foot %ROM%). The rig is
# gamepad-only, so this launcher NEVER prompts for keyboard input and
# prints nothing on the happy path — all user-facing feedback goes through
# mako toasts (bin/etk_chiaki_notify.sh), which are actually legible on
# the panel. The tiny terminal is only surfaced for the one-time pairing
# instructions, re-exec'd at a readable font size (Pitstop scale pattern).
#
# SWAY DOCTRINE (AI_MANIFEST): sway is a tiling compositor — the chiaki
# window would TILE next to this foot window. Do NOT pass the app's own
# fullscreen flag (it still tiles); instead poll for the app_id to map,
# then `swaymsg fullscreen enable` it. On exit foot closes with us and
# ES takes the screen back.
# ==========================================================

# 1. Single source of truth for all paths ($ETK_ROOT, $ETK_CHIAKI_BIN, ...)
source /storage/games-internal/roms/etk/scripts/env.sh

# 2. Wayland/SDL environment. SWAYSOCK is NOT ambient in the ES/foot
#    context — derive it (same glob the Pitstop engine uses).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export SDL_VIDEODRIVER=wayland
export SDL_VIDEO_WAYLAND_WMCLASS=chiaki
SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock 2>/dev/null | head -n 1)"
export SWAYSOCK

BIN="${ETK_CHIAKI_BIN:-$ETK_ROOT/tools/chiaki}"
CONF_DIR="${ETK_CHIAKI_CONF_DIR:-/storage/.config/chiaki}"
CONF="$CONF_DIR/chiaki.conf"
LOG="/storage/etk_chiaki.log"
NOTIFY="${ETK_CHIAKI_NOTIFY:-$ETK_ROOT/bin/etk_chiaki_notify.sh}"

# 3. First-run gate: pairing instructions are the ONE case where the
#    terminal earns its keep — re-exec in a big font so it's readable,
#    show the steps, then time out back to ES (no keyboard prompts).
if [ ! -f "$CONF" ]; then
    if [ "$ETK_SCALED" != "1" ]; then
        export ETK_SCALED=1
        exec /usr/bin/foot -F -o font="monospace:size=22" "$0" "$@"
    fi
    clear
    echo ""
    echo "  CHIAKI IS NOT PAIRED WITH YOUR PLAYSTATION YET"
    echo ""
    echo "  One-time setup, from your computer:"
    echo ""
    echo "  1. Console: Settings > System > Remote Play > Pair Device"
    echo "  2. ssh root@SM8250.local"
    echo "  3. $BIN regist \\"
    echo "       --host <console-ip> --pin <8-digit-pin> \\"
    echo "       --account-id <psn-account-id-b64>"
    echo ""
    echo "  (Account id: scripts/psn-account-id.py in the chiaki repo)"
    echo ""
    echo "  Returning to EmulationStation in 30 seconds..."
    sleep 30
    exit 0
fi

if [ ! -x "$BIN" ]; then
    "$NOTIFY" "Chiaki Missing - rerun install.sh" 2>/dev/null
    sleep 3
    exit 1
fi

# 4. Stream-active sentinel: input_d stands down the chords chiaki owns
#    in-stream (R1+L3 / L1+R3). trap so a launcher death can't leave it
#    behind; SHM is boot-volatile so a hard crash self-clears anyway.
LOCK="${ETK_CHIAKI_LOCK:-/dev/shm/etk_shm/chiaki_active}"
mkdir -p "$(dirname "$LOCK")"
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

# 5. mako toast hook: the binary invokes this with a message whenever the
#    stream state changes (connect, toggles, console sleep/busy).
export CHIAKI_NOTIFY_CMD="$NOTIFY"

# 6. Fullscreen dance: helper polls for the chiaki window, then fullscreens
#    it over this foot window. Bounded poll — never spins forever.
(
    i=0
    while [ "$i" -lt 50 ]; do
        if swaymsg -t get_tree 2>/dev/null | grep -q '"app_id": "chiaki"'; then
            swaymsg '[app_id="chiaki"] fullscreen enable' >/dev/null 2>&1
            exit 0
        fi
        usleep 200000 2>/dev/null || sleep 1
        i=$((i + 1))
    done
) &

# 7. Stream. All output to the log only (tail it over ssh when debugging);
#    quit reasons reach the user as toasts from the binary itself. No
#    terminal prompts on ANY path — ES takes the screen back on exit.
"$BIN" stream --config "$CONF" > "$LOG" 2>&1
RC=$?

# Brief grace so an error toast (fired by the binary) outlives the foot
# window teardown and is seen over ES rather than lost with the session.
[ "$RC" -ne 0 ] && sleep 2

exit 0
