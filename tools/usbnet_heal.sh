#!/bin/bash
# ==========================================================
# USB-NET HEAL — the NCM gadget comes up bound but unconfigured on ROCKNIX 20261001
# ==========================================================
# Found 2026-10-08 on car12 (GTK 0.6.x on the 20261001 chassis): the host enumerates
# the rig's CDC-NCM gadget ("Retroid Pocket Flip2 Visionox") but the link never gets a
# carrier — the rig's `gadget` interface is DOWN with no address and udhcpd is not
# running. Mechanism (rig journal, same boot): ROCKNIX's `usbgadget --start` (ES
# autostart 081) binds the function to the UDC FIRST, then `configure_iface` runs
# `udevadm wait --timeout=5 --settle /sys/class/net/gadget` before `ip link set up`.
# `prepare_usb_network()` had left `set -e` on for the whole script, so when the udev
# queue was still busy 5 s later ("udevadm: Timed out for waiting devices being
# initialized", 14.6 s = 9.3 s + 5) the script died with the gadget half-configured.
# ROCKNIX's `start cdc` path has no `set -e`, so `usbgadget stop; usbgadget start cdc`
# completes the configuration — proven live on car12 2026-10-08 16:10 (host: carrier,
# lease 169.254.170.5, ssh over the gadget). The OS script is byte-identical between
# 20260901 and 20261001, and the same unit linked fine on stock 20261001 at 00:14,
# so WHAT keeps udev busy on a kit/GTK boot is still open: `--trace` records it.
#
# INTEGRATED 2026-10-09 as install.sh STEP 6.72 (etk-usbnet-heal.service, kill-switch
# ETK_USBNET_HEAL=0, removed by uninstall.sh). This tool keeps the instruments
# (status, verify) and a disposable `stage` that writes install.sh's own bodies.
#
#   stage  <target> [--car carN] [--trace]
#          write etk-usbnet-heal.service (+ script) under /storage/.config: after
#          rocknix-autostart.service it checks the cdc gadget; healthy (bound + an
#          inet address) = log + exit 0; half-configured = `usbgadget stop; usbgadget
#          start cdc`, logged to /storage/etk_usbnet_heal.log. --trace adds
#          etk-udevtrace.service: `udevadm monitor -u -k -p` for the first 60 s of
#          boot into /storage/etk_udevtrace.log (what is in the queue at 9-15 s).
#          Nothing under /flash is touched. THE OPERATOR COLD-BOOTS.
#   status <target>   read-only: gadget UDC/ifname/address/udhcpd, the heal log, the
#          journal's udevadm timeout line, and a trace summary (events per second
#          over the autostart window) when a trace exists.
#   verify <usb-target>   the surface: ssh over the gadget link itself (e.g.
#          flip2-12g-usb) — the only proof that counts.
#   remove <target>   disable + delete both units and their logs (keeps nothing).
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
die() { echo "USBNET_HEAL_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
MODE="${1:-}"; TARGET="${2:-}"; shift 2 2>/dev/null || usage
[ -n "$MODE" ] && [ -n "$TARGET" ] || usage
CAR=""; TRACE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --car)   CAR="${2:?--car needs carN}"; shift 2 ;;
        --trace) TRACE=1; shift ;;
        *) die "unknown option $1" ;;
    esac
done
rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }
car_gate() {
    [ -n "$CAR" ] || return 0
    [ -f "$ETK_ROOT/scripts/etk_car.sh" ] || die "--car given but scripts/etk_car.sh is missing"
    "$ETK_ROOT/scripts/etk_car.sh" verify "$TARGET" "$CAR" || die "car check refused: $TARGET is not $CAR"
}

# status body: BusyBox sh + awk (manual §Q). Fed on stdin so the awk programs keep
# single quotes; awk compares ARRAY KEYS as strings, hence the +0 everywhere (the
# first cut of this table printed nothing: "9s">="6" is a string compare).
REMOTE_STATUS='
echo "== unit"; systemctl is-enabled etk-usbnet-heal.service 2>&1; systemctl is-active etk-usbnet-heal.service 2>&1
echo "== gadget"; G=/sys/kernel/config/usb_gadget/cdc
echo "UDC=$(cat $G/UDC 2>/dev/null) ifname=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null) mode=$(cat /storage/.cache/usbgadget/usbgadget.conf 2>/dev/null)"
IF=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null); [ -n "$IF" ] && ip addr show "$IF" | grep -E "^[0-9]+:|inet "
echo "udhcpd: $(pgrep -a udhcpd || echo none)"
echo "== heal log"; tail -5 /storage/etk_usbnet_heal.log 2>/dev/null || echo "(no log yet)"
echo "== journal (this boot)"
J=$(journalctl -b --no-pager -o short-monotonic 2>/dev/null | grep -iE "usbnet-heal|Timed out for waiting|udevtrace" | cut -c1-200)
printf "%s\n" "$J" | tail -8
L=/storage/etk_udevtrace.log
if [ -s "$L" ]; then
    TO=$(printf "%s\n" "$J" | grep -m1 "Timed out for waiting" | sed "s/^\[ *\([0-9.]*\)\].*/\1/")
    echo "== udev trace: UDEV (post-rules) events per second, 6-22 s (settle timeout at ${TO:-?} s)"
    awk '"'"'/^UDEV  \[/{t=$2; gsub(/[][]/,"",t); s=int(t+0); n[s]++} END{for (k in n) if (k+0>=6 && k+0<=22) printf "%4d s %4d\n", k, n[k]}'"'"' "$L" | sort -n
    echo "== udev trace: busiest subsystems 9-15 s"
    awk '"'"'/^UDEV  \[/{t=$2; gsub(/[][]/,"",t); if (t+0>=9 && t+0<=15) n[$NF]++} END{for (k in n) printf "%4d %s\n", n[k], k}'"'"' "$L" | sort -rn | head -6
    echo "== udev trace: slowest rule processing, KERNEL uevent -> UDEV done (the queue is busy while ANY of these runs)"
    awk '"'"'
    /^KERNEL\[/ { t=$1; sub(/^KERNEL\[/,"",t); sub(/\]$/,"",t); k=$2 SUBSEP $3; q[k, ++qn[k]]=t+0; next }
    /^UDEV  \[/ { t=$2; gsub(/[][]/,"",t); k=$3 SUBSEP $4; i=++qh[k]; if ((k,i) in q) { s=q[k,i]; printf "%8.3f -> %8.3f  %7.3f s  %s %s\n", s, t+0, t-s, $3, $4 } }
    '"'"' "$L" | sort -k4 -rn | head -8
    if [ -n "$TO" ]; then
        echo "== udev trace: events IN FLIGHT at the settle timeout ($TO s) -- the culprit list"
        awk -v to="$TO" '"'"'
        /^KERNEL\[/ { t=$1; sub(/^KERNEL\[/,"",t); sub(/\]$/,"",t); k=$2 SUBSEP $3; q[k, ++qn[k]]=t+0; next }
        /^UDEV  \[/ { t=$2; gsub(/[][]/,"",t); k=$3 SUBSEP $4; i=++qh[k]; if ((k,i) in q) { s=q[k,i]; if (s<to+0 && t+0>to+0) printf "%8.3f -> %8.3f  %s %s\n", s, t+0, $3, $4 } }
        '"'"' "$L" | head -10
    fi
fi
'

# The heal script + unit are install.sh STEP 6.72's (USBNETHEAL / USBNETUNIT): stage writes
# exactly those bodies, so the disposable stage and the kit can never drift apart.
body() { awk -v m="$1" 'index($0, "<< \x27" m "\x27") {inb=1; next} inb && $0 == m {exit} inb {print}' "$ETK_ROOT/install.sh"; }
HEAL_SH=$(body USBNETHEAL); HEAL_UNIT=$(body USBNETUNIT)
[ -n "$HEAL_SH" ] && [ -n "$HEAL_UNIT" ] || { echo "USBNET_HEAL_FAIL: install.sh carries no USBNETHEAL/USBNETUNIT body" >&2; exit 1; }
TRACE_UNIT='[Unit]
Description=ETK udev trace (DIAGNOSTIC, removable): what is in the udev queue during boot
DefaultDependencies=no
After=systemd-udevd.service
Before=rocknix-autostart.service

[Service]
Type=simple
ExecStart=/bin/sh -c "exec /usr/bin/timeout 60 /usr/bin/udevadm monitor -u -k -p > /storage/etk_udevtrace.log 2>&1"
SuccessExitStatus=124

[Install]
WantedBy=sysinit.target
'

case "$MODE" in
stage)
    car_gate
    rssh 'test -x /usr/bin/usbgadget && test -d /sys/kernel/config/usb_gadget' || die "$TARGET has no ROCKNIX usbgadget / configfs gadget — nothing to heal"
    printf '%s\n' "$HEAL_SH"   | rssh 'mkdir -p /storage/.config/custom_scripts /storage/.config/system.d && cat > /storage/.config/custom_scripts/etk-usbnet-heal.sh && chmod +x /storage/.config/custom_scripts/etk-usbnet-heal.sh' || die "could not write the heal script"
    printf '%s\n' "$HEAL_UNIT" | rssh 'cat > /storage/.config/system.d/etk-usbnet-heal.service' || die "could not write the heal unit"
    if [ "$TRACE" = 1 ]; then
        printf '%s' "$TRACE_UNIT" | rssh 'cat > /storage/.config/system.d/etk-udevtrace.service' || die "could not write the trace unit"
    fi
    rssh 'systemctl daemon-reload && systemctl enable /storage/.config/system.d/etk-usbnet-heal.service >/dev/null 2>&1; echo "heal: $(systemctl is-enabled etk-usbnet-heal.service 2>&1)"; if [ -f /storage/.config/system.d/etk-udevtrace.service ]; then systemctl enable /storage/.config/system.d/etk-udevtrace.service >/dev/null 2>&1; echo "trace: $(systemctl is-enabled etk-udevtrace.service 2>&1)"; fi; sh -n /storage/.config/custom_scripts/etk-usbnet-heal.sh && echo "heal script: syntax ok"' || die "enable failed"
    echo "STAGED on $TARGET (nothing runs until the next boot; /flash untouched)."
    echo "Next: OPERATOR cold-boots the unit, then: $0 status $TARGET  and  $0 verify <usb-target>"
    ;;
status)
    rssh 'sh -s' <<< "$REMOTE_STATUS"
    ;;
verify)
    OUT=$(ssh $SSH_OPTS "$TARGET" 'echo "USBNET_OK host=$(hostname) car=$(cat /storage/.etk/car 2>/dev/null) up=$(cut -d" " -f1 /proc/uptime)s"' 2>&1) || die "no ssh over $TARGET: ${OUT}"
    echo "$OUT"; echo "$OUT" | grep -q USBNET_OK || die "unexpected reply"
    ;;
remove)
    car_gate
    rssh 'systemctl disable etk-usbnet-heal.service etk-udevtrace.service >/dev/null 2>&1; rm -f /storage/.config/system.d/etk-usbnet-heal.service /storage/.config/system.d/etk-udevtrace.service /storage/.config/custom_scripts/etk-usbnet-heal.sh /storage/etk_usbnet_heal.log /storage/etk_udevtrace.log; systemctl daemon-reload; echo removed'
    ;;
*) usage ;;
esac
