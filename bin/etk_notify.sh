#!/bin/sh
# ==========================================================
# ETK NOTIFY — the shell side of the ETK notification surfaces
# (BUSYBOX COMPLIANT — POSIX sh only, no GNU-isms)
#
# Usage:
#   etk_notify.sh "SUMMARY" ["body"] [timeout_ms]
#   etk_notify.sh --progress "TITLE" "name" <0-100> [timeout_ms]
#   etk_notify.sh --close <id>
#
# Echoes the notification id on success so a caller can replace or close
# it later:  ID=$(etk_notify.sh "INSTALLING" "GT5P"); ...; etk_notify.sh --close "$ID"
# Pass an id in ETK_NOTIFY_ID to REPLACE that notification in place instead
# of stacking a new one.
# ==========================================================
# Why this exists: ETK had four copies of the same dbus-send incantation
# (Pitstop, osguard, bog profiler, chiaki), each with its own app-name and
# its own idea of house style. mako matches criteria on app-name with a
# byte-exact strcmp, so a single typo silently drops a toast back to the
# stock black system style — exactly the class of bug a shared sender ends.
#
# THE TWO SURFACES mirror EmulationStation's own, so ETK reads as part of
# the system rather than a style of its own invention:
#   "ETK"           -> ES's GuiInfoPopup: top-center, ~10s. VERDICTS.
#   "ETK Progress"  -> ES's scraper card: top-right, progress bar, lives as
#                      long as the job and is dismissed at the end.
# install.sh writes one mako criteria block per surface; these strings must
# match those [app-name=...] sections exactly.
#
# There is NO notify-send on ROCKNIX and mako has no image decoder on this
# build (the gdk-pixbuf loaders are stripped) — text only, via dbus-send.
#
# EXIT STATUS IS A CONTRACT: 0 only when the notification was actually
# delivered, non-zero when the bus or the notification daemon could not be
# reached. osguard depends on it — it retries this sender for a minute at
# boot because it can run before sway has started mako, and a sender that
# always succeeded would break out on the first failed attempt and lose the
# only on-screen recovery instruction the operator gets. Callers that must
# not fail on a missing toast should append `|| true`.
#
# PROGRESS: mako 1.10.0 renders the standard `value` hint natively. dbus-send
# cannot marshal the nested a{sv} that hint needs (its own man page rules out
# nested containers), so the progress form goes out through busctl. The hint
# MUST be int32: mako reads it as `v` of `i` with no type fallback, so a
# uint32 fails the WHOLE Notify call and costs the notification, not just the
# bar. Where busctl is missing we still send the toast, just without a bar.
# ==========================================================
APP_TOAST="ETK"
APP_PROGRESS="ETK Progress"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

# dbus-send prints "   uint32 7"; busctl prints "u 7". Take the trailing int.
_id_from_reply() {
    awk '/uint32|^u /{ for (i = NF; i >= 1; i--) if ($i ~ /^[0-9]+$/) { print $i; exit } }'
}

case "$1" in
--close)
    [ -n "$2" ] && [ "$2" != "0" ] || exit 0
    # CloseNotification takes a bare uint32 — no container, so dbus-send can
    # do this one. The progress card has to go the moment its job ends; a
    # card left to fade keeps reporting work that is already over.
    dbus-send --session --dest=org.freedesktop.Notifications \
        /org/freedesktop/Notifications \
        org.freedesktop.Notifications.CloseNotification \
        "uint32:$2" >/dev/null 2>&1
    exit $?
    ;;
--progress)
    SUMMARY="$2"
    BODY="$3"
    PCT="$4"
    TIMEOUT="${5:-20000}"
    case "$PCT" in
        ''|*[!0-9]*) PCT=0 ;;
    esac
    [ "$PCT" -gt 100 ] && PCT=100
    if command -v busctl >/dev/null 2>&1; then
        REPLY=$(busctl --user call org.freedesktop.Notifications \
            /org/freedesktop/Notifications \
            org.freedesktop.Notifications Notify \
            "susssasa{sv}i" \
            "$APP_PROGRESS" "${ETK_NOTIFY_ID:-0}" "" "$SUMMARY" "$BODY" \
            0 1 value i "$PCT" "$TIMEOUT" 2>/dev/null) || exit 1
    else
        # No hint transport: keep the notification, lose the bar.
        REPLY=$(dbus-send --session --print-reply \
            --dest=org.freedesktop.Notifications \
            /org/freedesktop/Notifications \
            org.freedesktop.Notifications.Notify \
            "string:$APP_PROGRESS" "uint32:${ETK_NOTIFY_ID:-0}" string: \
            "string:$SUMMARY  $PCT%" "string:$BODY" \
            array:string: dict:string:variant: "int32:$TIMEOUT" \
            2>/dev/null) || exit 1
    fi
    ID=$(printf '%s\n' "$REPLY" | _id_from_reply)
    [ -n "$ID" ] || exit 1
    printf '%s\n' "$ID"
    exit 0
    ;;
esac

SUMMARY="$1"
[ -z "$SUMMARY" ] && exit 0
BODY="${2:-}"
TIMEOUT="${3:-10000}"

REPLY=$(dbus-send --session --print-reply --dest=org.freedesktop.Notifications \
    /org/freedesktop/Notifications org.freedesktop.Notifications.Notify \
    "string:$APP_TOAST" "uint32:${ETK_NOTIFY_ID:-0}" string: \
    "string:$SUMMARY" "string:$BODY" \
    array:string: dict:string:variant: "int32:$TIMEOUT" \
    2>/dev/null) || exit 1
ID=$(printf '%s\n' "$REPLY" | _id_from_reply)
[ -n "$ID" ] || exit 1
printf '%s\n' "$ID"
exit 0
