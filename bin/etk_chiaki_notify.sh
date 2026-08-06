#!/bin/sh
# ==========================================================
# ETK CHIAKI NOTIFIER — mako toast helper (BUSYBOX COMPLIANT)
# Usage: etk_chiaki_notify.sh "message"
# ==========================================================
# Invoked by the chiaki binary via CHIAKI_NOTIFY_CMD (exported by
# config/etk_chiaki.sh) whenever the stream state changes — toggle
# reconnects, connected confirmations, console-busy retries. The toast
# is what the user sees while the screen sits black during a profile
# reconnect.
#
# Sends through bin/etk_notify.sh so Chiaki shares the ETK toast surface.
# It used to send app_name="Chiaki", which matched no mako criteria and so
# silently fell through to the stock system style — right look, by accident.
# Now it is on purpose, and a restyle reaches it with everything else.
#
# replaces_id: the last notification id is kept in volatile SHM and passed
# back so rapid updates REPLACE the toast in place instead of stacking a
# column of stale ones. Fail-silent — a toast must never break a stream.
# ==========================================================
MSG="$1"
[ -z "$MSG" ] && exit 0

ETK_ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"
SENDER="$ETK_ROOT/bin/etk_notify.sh"
[ -x "$SENDER" ] || exit 0

IDDIR="/dev/shm/etk_shm"
IDFILE="$IDDIR/chiaki_notify_id"
ID=$(cat "$IDFILE" 2>/dev/null)
case "$ID" in
    ''|*[!0-9]*) ID=0 ;;
esac

# The MESSAGE rides in the SUMMARY slot: mako renders the summary in the
# large title font (the only reliably legible text on the 1080p panel at
# handheld distance); the body field stays empty by design.
NEW=$(ETK_NOTIFY_ID="$ID" "$SENDER" "$MSG" "" 6000 2>/dev/null)
case "$NEW" in
    ''|*[!0-9]*) ;;
    *)
        mkdir -p "$IDDIR" 2>/dev/null
        echo "$NEW" > "$IDFILE" 2>/dev/null
        ;;
esac
exit 0
