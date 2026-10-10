#!/bin/bash
# usb_harness.sh -- disposable on-rig trial of Pitlink over raw USB (PLUSB). No install:
# the daemon runs from the rig's /tmp, configfs is volatile, a reboot restores stock.
#
#   tools/pitlink/usb_harness.sh attach   copy + start etk_pitlink_usbd (adds the "ETK Pitlink"
#                                         USB function next to NCM; USB-net blips ~3 s), report
#   tools/pitlink/usb_harness.sh detach   stop it (it removes its function; NCM alone again)
#   tools/pitlink/usb_harness.sh status   gadget + daemon state, last log lines
#
# The daemon rolls itself back to NCM-only if the rebind fails or NCM loses its address.
# Then, from the M1:  python3 tools/pitlink/usb_broker.py probe     (link RTT)
#                     python3 tools/pitlink/pitlink.py status       (PLNK over USB; default addr)
set -u
cd "$(dirname "$0")/../.." || exit 1
RIG="${RIG:-root@169.254.170.2}"
D=/tmp/pitlink-usb
SSH="ssh -o BatchMode=yes -o ConnectTimeout=5"

wait_rig() {  # USB-net comes back after the rebind (host re-enumerates, NM re-leases)
    for _ in $(seq 1 40); do $SSH "$RIG" true 2>/dev/null && return 0; sleep 1; done
    echo "rig not reachable over $RIG after 40 s (WiFi ssh still works; a reboot restores stock)"; return 1
}

status() {
    $SSH "$RIG" "python3 $D/etk_pitlink_usbd.py status 2>/dev/null || echo 'daemon not copied yet';
        p=\$(cat $D/pid 2>/dev/null); [ -n \"\$p\" ] && kill -0 \$p 2>/dev/null && echo \"daemon pid \$p running\" || echo 'daemon not running';
        tail -n 8 $D/daemon.log 2>/dev/null"
    echo "--- M1 side:"
    lsusb -v -d 1d6b:0104 2>/dev/null | grep -E "iInterface|bInterfaceClass" | paste - - | sed 's/  */ /g' | sed 's/^/  /'
}

case "${1:-status}" in
attach)
    $SSH "$RIG" "mkdir -p $D" && scp -q bin/etk_pitlink_usbd.py tools/pitlink/plusb.py "$RIG:$D/" || exit 1
    HOST_SUM=$(md5sum bin/etk_pitlink_usbd.py tools/pitlink/plusb.py | awk '{print $1}' | tr '\n' ' ')
    RIG_SUM=$($SSH "$RIG" "cd $D && md5sum etk_pitlink_usbd.py plusb.py" | awk '{print $1}' | tr '\n' ' ')
    echo "pushed daemon+plusb md5: $HOST_SUM"
    [ "$HOST_SUM" = "$RIG_SUM" ] || { echo "rig copy differs ($RIG_SUM) -- not starting"; exit 1; }
    $SSH "$RIG" "cd $D && p=\$(cat pid 2>/dev/null); [ -n \"\$p\" ] && kill \$p 2>/dev/null && sleep 2;
        setsid nohup python3 $D/etk_pitlink_usbd.py serve > $D/daemon.log 2>&1 < /dev/null & echo \$! > $D/pid; echo started pid \$!"
    sleep 3
    wait_rig && status
    ;;
detach)
    $SSH "$RIG" "p=\$(cat $D/pid 2>/dev/null); [ -n \"\$p\" ] && kill \$p 2>/dev/null; sleep 2; python3 $D/etk_pitlink_usbd.py detach; rm -f $D/pid"
    sleep 2
    wait_rig && status
    ;;
status) status ;;
*) sed -n 2,14p "$0"; exit 2 ;;
esac
