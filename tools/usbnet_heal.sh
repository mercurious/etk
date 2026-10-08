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
# This tool is the disposable on-rig harness (TRACK_MANUAL §1.4) for one cold boot;
# integration into install.sh follows the operator's verdict.
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

HEAL_SH='#!/bin/sh
# etk-usbnet-heal (ETK, tools/usbnet_heal.sh): ROCKNIX usbgadget --start can die on its
# `udevadm wait --settle` timeout (set -e leaked from prepare_usb_network) AFTER binding the
# NCM gadget, leaving the host a carrier-less link. Re-run the OS'"'"'s own start path.
LOG=/storage/etk_usbnet_heal.log
G=/sys/kernel/config/usb_gadget/cdc
log() { echo "$(date "+%F %T") [$(cut -d" " -f1 /proc/uptime)s] $*" >> "$LOG"; echo "[etk-usbnet-heal] $*"; }
USB_MODE=""
[ -r /storage/.cache/usbgadget/usbgadget.conf ] && . /storage/.cache/usbgadget/usbgadget.conf
if [ "$USB_MODE" != "cdc" ]; then log "USB_MODE=${USB_MODE:-unset}: not network mode, nothing to heal"; exit 0; fi
UDC=$(cat $G/UDC 2>/dev/null); IF=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null)
QUEUE=$([ -e /run/udev/queue ] && echo busy || echo empty)
if [ -n "$UDC" ] && [ -n "$IF" ] && ip addr show "$IF" 2>/dev/null | grep -q "inet "; then
    log "healthy: $IF has an address, UDC=$UDC (udev queue $QUEUE)"; exit 0
fi
log "HALF-CONFIGURED: UDC=\"${UDC}\" iface=\"${IF}\" no inet, udhcpd=$(pgrep udhcpd >/dev/null && echo up || echo down), udev queue $QUEUE -> usbgadget stop; usbgadget start cdc"
/usr/bin/usbgadget stop
/usr/bin/usbgadget start cdc; RC=$?
UDC=$(cat $G/UDC 2>/dev/null); IF=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null)
ADDR=$(ip addr show "$IF" 2>/dev/null | awk "/inet /{print \$2}" | head -1)
if [ -n "$UDC" ] && [ -n "$ADDR" ] && pgrep udhcpd >/dev/null; then
    log "HEALED: start rc=$RC UDC=$UDC $IF $ADDR udhcpd up"; exit 0
fi
log "HEAL FAILED: start rc=$RC UDC=\"$UDC\" iface=\"$IF\" addr=\"$ADDR\" udhcpd=$(pgrep udhcpd >/dev/null && echo up || echo down)"; exit 1
'
HEAL_UNIT='[Unit]
Description=ETK USB-net heal (ROCKNIX usbgadget --start half-configured gadget)
# After the ES autostart that runs usbgadget --start (081). Unknown units in After=
# are ignored harmlessly on a tree that lacks them.
After=rocknix-autostart.service
ConditionPathExists=/usr/bin/usbgadget

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /storage/.config/custom_scripts/etk-usbnet-heal.sh

[Install]
WantedBy=rocknix.target
'
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
    printf '%s' "$HEAL_SH"   | rssh 'mkdir -p /storage/.config/custom_scripts /storage/.config/system.d && cat > /storage/.config/custom_scripts/etk-usbnet-heal.sh && chmod +x /storage/.config/custom_scripts/etk-usbnet-heal.sh' || die "could not write the heal script"
    printf '%s' "$HEAL_UNIT" | rssh 'cat > /storage/.config/system.d/etk-usbnet-heal.service' || die "could not write the heal unit"
    if [ "$TRACE" = 1 ]; then
        printf '%s' "$TRACE_UNIT" | rssh 'cat > /storage/.config/system.d/etk-udevtrace.service' || die "could not write the trace unit"
    fi
    rssh 'systemctl daemon-reload && systemctl enable /storage/.config/system.d/etk-usbnet-heal.service >/dev/null 2>&1; echo "heal: $(systemctl is-enabled etk-usbnet-heal.service 2>&1)"; if [ -f /storage/.config/system.d/etk-udevtrace.service ]; then systemctl enable /storage/.config/system.d/etk-udevtrace.service >/dev/null 2>&1; echo "trace: $(systemctl is-enabled etk-udevtrace.service 2>&1)"; fi; sh -n /storage/.config/custom_scripts/etk-usbnet-heal.sh && echo "heal script: syntax ok"' || die "enable failed"
    echo "STAGED on $TARGET (nothing runs until the next boot; /flash untouched)."
    echo "Next: OPERATOR cold-boots the unit, then: $0 status $TARGET  and  $0 verify <usb-target>"
    ;;
status)
    rssh '
        echo "== unit"; systemctl is-enabled etk-usbnet-heal.service 2>&1; systemctl is-active etk-usbnet-heal.service 2>&1
        echo "== gadget"; G=/sys/kernel/config/usb_gadget/cdc
        echo "UDC=$(cat $G/UDC 2>/dev/null) ifname=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null) mode=$(cat /storage/.cache/usbgadget/usbgadget.conf 2>/dev/null)"
        IF=$(cat $G/functions/ncm.usb0/ifname 2>/dev/null); [ -n "$IF" ] && ip addr show "$IF" | grep -E "^[0-9]+:|inet "
        echo "udhcpd: $(pgrep -a udhcpd || echo none)"
        echo "== heal log"; tail -5 /storage/etk_usbnet_heal.log 2>/dev/null || echo "(no log yet)"
        echo "== journal (this boot)"; journalctl -b --no-pager -o short-monotonic 2>/dev/null | grep -iE "usbnet-heal|Timed out for waiting|udevtrace" | cut -c1-200 | tail -8
        if [ -s /storage/etk_udevtrace.log ]; then
            echo "== udev trace: events per second (UDEV = post-rules), autostart window"
            grep -E "^UDEV " /storage/etk_udevtrace.log | awk "{gsub(/[\\[\\]]/,\"\",\$2); s=int(\$2); n[s]++} END{for (k in n) printf \"%4ds %3d\n\", k, n[k]}" | sort -n | awk "\$1>=6 && \$1<=18"
            echo "== udev trace: busiest subsystems 9-15 s"
            awk "/^UDEV /{gsub(/[\\[\\]]/,\"\",\$2); t=\$2+0; sub=\$4; if (t>=9 && t<=15) n[sub]++} END{for (k in n) printf \"%4d %s\n\", n[k], k}" /storage/etk_udevtrace.log | sort -rn | head -8
        fi'
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
