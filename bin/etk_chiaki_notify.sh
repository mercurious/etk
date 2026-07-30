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
# dbus env derivation per the manifest (cf. bin/bog_profile.sh): there
# is NO notify-send on ROCKNIX, mako listens on the session bus at
# $XDG_RUNTIME_DIR/bus. Fail-silent — a toast must never break a stream.
#
# replaces_id (cf. etk_pitstop.py _Notifier): the last notification id is
# kept in volatile SHM and passed back so rapid updates REPLACE the toast
# in place instead of stacking a column of stale ones.
# ==========================================================
MSG="$1"
[ -z "$MSG" ] && exit 0

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

IDDIR="/dev/shm/etk_shm"
IDFILE="$IDDIR/chiaki_notify_id"
ID=$(cat "$IDFILE" 2>/dev/null)
case "$ID" in
    ''|*[!0-9]*) ID=0 ;;
esac

REPLY=$(dbus-send --session --print-reply --dest=org.freedesktop.Notifications \
    /org/freedesktop/Notifications org.freedesktop.Notifications.Notify \
    "string:Chiaki" "uint32:$ID" string: \
    "string:Remote Play" "string:$MSG" \
    array:string: dict:string:variant: int32:6000 2>/dev/null)

NEW=$(echo "$REPLY" | grep uint32 | head -n 1 | awk '{print $2}')
case "$NEW" in
    ''|*[!0-9]*) ;;
    *)
        mkdir -p "$IDDIR" 2>/dev/null
        echo "$NEW" > "$IDFILE" 2>/dev/null
        ;;
esac
exit 0
