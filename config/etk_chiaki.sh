#!/bin/sh
# ==========================================================
# ETK CHIAKI LAUNCHER (BUSYBOX COMPLIANT)
# Location: /storage/.config/modules/etk_chiaki.sh
# ==========================================================
# ES spawns Tools entries inside a foot terminal (foot %ROM%), so this
# script's stdout is visible on the panel. It launches the chiaki SDL
# window and does the sway fullscreen dance from the OUTSIDE:
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

if [ ! -x "$BIN" ]; then
    echo "chiaki binary missing at $BIN"
    echo "Re-run install.sh from your computer, then try again."
    echo "Press Enter to exit."
    read _dummy
    exit 1
fi

# 3. First-run gate: streaming needs a one-time pairing with the console.
if [ ! -f "$CONF" ]; then
    echo "Chiaki is not paired with your PlayStation yet."
    echo ""
    echo "One-time setup, from your computer:"
    echo "  1. On the console: Settings > System > Remote Play > Link Device"
    echo "  2. ssh root@SM8250.local"
    echo "  3. $BIN regist \\"
    echo "       --host <console-ip> --pin <8-digit-pin> \\"
    echo "       --account-id <your-psn-account-id-b64>"
    echo ""
    echo "(Account id: scripts/psn-account-id.py in the chiaki repo.)"
    echo "Press Enter to exit."
    read _dummy
    exit 0
fi

# 4. Fullscreen dance: helper polls for the chiaki window, then fullscreens
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

# 5. Stream. Log goes to /storage (tail it over ssh when debugging). No
#    pipeline here: the binary's exit status gates the on-error hold below,
#    and BusyBox ash has no PIPESTATUS.
#
#    The SHM sentinel tells input_d to stand down the chords chiaki owns
#    in-stream (R1+L3 / L1+R3). trap so a launcher death can't leave it
#    behind; SHM is boot-volatile so a hard crash self-clears anyway.
LOCK="${ETK_CHIAKI_LOCK:-/dev/shm/etk_shm/chiaki_active}"
mkdir -p "$(dirname "$LOCK")"
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

# mako toast hook: the binary invokes this with a message whenever the
# stream state changes (toggle reconnects, connected, console-busy).
export CHIAKI_NOTIFY_CMD="${ETK_CHIAKI_NOTIFY:-$ETK_ROOT/bin/etk_chiaki_notify.sh}"
echo "Starting Remote Play..."
echo "  quit:              hold Select+Start (or the Home button)"
echo "  toggle resolution: hold R1+L3   (1080p <-> 720p)"
echo "  toggle codec:      hold L1+R3   (h265 <-> h264)"
"$BIN" stream --config "$CONF" > "$LOG" 2>&1
RC=$?
rm -f "$LOCK"

# 6. On failure, keep this foot window open so the reason (e.g. "Remote Play
#    on Console is already in use") is readable instead of flashing past.
if [ "$RC" -ne 0 ]; then
    echo ""
    echo "Remote Play ended with an error:"
    tail -n 8 "$LOG"
    echo ""
    echo "Full log: $LOG   Press Enter to exit."
    read _dummy
fi

exit 0
