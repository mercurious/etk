#!/bin/bash
# ==========================================================
# ETK HUD APPLIER — punchbox state -> live MangoHud.conf  (v1.0.0)
# ==========================================================
# Writes the live MangoHud config for ONE of the four punchbox states and
# signals MangoHud to reload. SHARED by bin/input_d.py (the R1+L3 cycle) and
# the Sentry (per-game restore at IDLE->RUNNING) so both paths write the conf
# identically — one source of truth for the HUD conf, no drift.
#
#   top     — ETK DDU, top-left
#   bottom  — ETK DDU, bottom-left  (dashboard default)
#   default — minimal stock readout (fps + frametime), no ETK DDU
#   off     — overlay hidden (no_display=1); MangoHud stays loaded for instant
#             return on the next R1+L3 press
#
# FAIL-SILENT by construction: a bad/empty state, a missing master conf, or a
# dead MangoHud socket must NEVER disturb the caller (input_d's R3 panic path,
# the Sentry ignition). Every step is best-effort; we exit 0 regardless.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null

STATE="${1:-bottom}"
LIVE="${RIG_MANGO_CONF:-/storage/.config/MangoHud/MangoHud.conf}"
ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"
MASTER="$ROOT/config/MangoHud.conf"            # the pristine ETK DDU conf
DEFCONF="$ROOT/config/MangoHud.default.conf"   # the minimal readout
TMP="$LIVE.etk.tmp"

# No master to build from -> leave the live conf untouched (never blank the HUD).
[ -f "$MASTER" ] || exit 0

case "$STATE" in
    top)
        cp "$MASTER" "$TMP" 2>/dev/null && \
            sed -i 's|^position=.*|position=top-left|; s|^no_display=.*|no_display=0|' "$TMP" ;;
    bottom)
        cp "$MASTER" "$TMP" 2>/dev/null && \
            sed -i 's|^position=.*|position=bottom-left|; s|^no_display=.*|no_display=0|' "$TMP" ;;
    default)
        if [ -f "$DEFCONF" ]; then cp "$DEFCONF" "$TMP" 2>/dev/null
        else cp "$MASTER" "$TMP" 2>/dev/null; fi ;;
    off)
        cp "$MASTER" "$TMP" 2>/dev/null && \
            sed -i 's|^no_display=.*|no_display=1|' "$TMP" ;;
    *)
        exit 0 ;;
esac

# Atomic swap only if the tmp built cleanly.
[ -s "$TMP" ] && mv "$TMP" "$LIVE" 2>/dev/null
rm -f "$TMP" 2>/dev/null

# Best-effort live reload (BusyBox nc lacks reliable -U; use python, mirroring
# input_d.py / the Sentry). If MangoHud's socket isn't up yet, the conf is
# already correct on disk for the next launch.
python3 -c "
import socket, glob
for p in glob.glob('/tmp/MangoHud*') + glob.glob('/tmp/mangohud*'):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3); s.connect(p)
        s.send(b'reload_cfg\n'); s.close(); break
    except Exception:
        pass
" 2>/dev/null
exit 0
