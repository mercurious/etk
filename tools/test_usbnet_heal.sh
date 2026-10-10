#!/bin/sh
# ==========================================================
# tools/test_usbnet_heal.sh — install.sh STEP 6.72 USB-net heal + uninstall coverage
# ==========================================================
# The heal (ROCKNIX 20261001: usbgadget --start dies on its udevadm settle
# timeout after binding the NCM gadget -> carrier-less USB link) was a
# disposable stage in tools/usbnet_heal.sh, cold-boot validated on car12
# 2026-10-08; on 2026-10-09 it entered the kit as install.sh STEP 6.72.
# The same audit found etk-dpmirror.service written by install.sh and never
# removed by uninstall.sh (car8 kept it enabled after an uninstall).
#
# This harness proves:
#   1. the heal script, as install.sh writes it, in a sandbox (stubs for
#      usbgadget/ip/pgrep; /sys, /storage, /run rewritten):
#        not network mode -> exit 0, nothing touched
#        healthy gadget   -> exit 0, no usbgadget call
#        half-configured  -> usbgadget stop + start cdc -> HEALED, exit 0
#        start leaves it unconfigured -> HEAL FAILED, exit 1
#   2. install.sh writes + enables the unit by absolute path, reads back a
#      verdict, and the kill-switch ETK_USBNET_HEAL=0 removes it;
#   3. UNINSTALL COVERAGE: every unit and boot script install.sh writes under
#      /storage/.config/{system.d,custom_scripts} is removed by uninstall.sh;
#   4. one source: tools/usbnet_heal.sh stages install.sh's bodies, and the
#      PowerShell port runs the USBNETHEALREMOTE body by marker;
#   5. (--rig) the rig's shell gives byte-identical heal results.
#
# DISCRIMINATION:
#   tools/test_usbnet_heal.sh                     # working tree — must PASS
#   tools/test_usbnet_heal.sh --against 0b61b62   # pre-integration — must FAIL
#   tools/test_usbnet_heal.sh --rig               # + rig leg (writes only /tmp on the rig)
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; RIGLEG=0
while [ $# -gt 0 ]; do
    case "$1" in
        --against) REV="${2:?--against needs a revision}"; shift 2 ;;
        --rig) RIGLEG=1; shift ;;
        *) echo "usage: $0 [--against <rev>] [--rig]"; exit 2 ;;
    esac
done
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_usbheal_XXXXXX)
[ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
for f in install.sh uninstall.sh tools/usbnet_heal.sh windows_installer/etk-install.ps1; do
    o="$TD/$(basename "$f")"
    if [ -n "$REV" ]; then git show "$REV:$f" > "$o" 2>/dev/null || : > "$o"; else cp "$f" "$o"; fi
done
body() { awk -v m="$1" 'index($0, "<< \x27" m "\x27") {inb=1; next} inb && $0 == m {exit} inb {print}' "$TD/$2"; }
body USBNETHEAL install.sh > "$TD/heal.sh"

# --- 1. the heal script in a sandbox (runner shared with the rig leg) ---
cat > "$TD/runner.sh" << 'RUNNER'
#!/bin/sh
D=$1; cd "$D" || exit 1
[ -s "$D/heal.sh" ] || { echo "== no heal body"; exit 0; }
mk() {  # mk <case> <USB_MODE> <udc 0|1> <addr 0|1> <start-heals 0|1>
    SB=$D/cases/$1; rm -rf "$SB"; mkdir -p "$SB/stubs" "$SB/state" "$SB/storage/.cache/usbgadget" "$SB/sys/usb_gadget/cdc/functions/ncm.usb0" "$SB/run/udev"
    echo "USB_MODE=$2" > "$SB/storage/.cache/usbgadget/usbgadget.conf"
    echo gadget > "$SB/sys/usb_gadget/cdc/functions/ncm.usb0/ifname"
    if [ "$3" = 1 ]; then echo a600000.usb > "$SB/sys/usb_gadget/cdc/UDC"; else : > "$SB/sys/usb_gadget/cdc/UDC"; fi
    [ "$4" = 1 ] && { : > "$SB/state/addr"; : > "$SB/state/udhcpd"; }
    [ "$5" = 1 ] && : > "$SB/state/start_heals"
    cat > "$SB/stubs/usbgadget" << STUB
#!/bin/sh
echo "usbgadget \$*" >> "$SB/calls"
case "\$1" in
  stop)  : > "$SB/sys/usb_gadget/cdc/UDC"; rm -f "$SB/state/addr" "$SB/state/udhcpd" ;;
  start) echo a600000.usb > "$SB/sys/usb_gadget/cdc/UDC"; [ -e "$SB/state/start_heals" ] && { : > "$SB/state/addr"; : > "$SB/state/udhcpd"; } ;;
esac
exit 0
STUB
    printf '#!/bin/sh\n[ -e "%s/state/addr" ] && printf "4: gadget: <UP>\\n    inet 169.254.170.2/16 scope global gadget\\n"\nexit 0\n' "$SB" > "$SB/stubs/ip"
    printf '#!/bin/sh\n[ "$1" = udhcpd ] && [ -e "%s/state/udhcpd" ]\n' "$SB" > "$SB/stubs/pgrep"
    chmod +x "$SB/stubs/usbgadget" "$SB/stubs/ip" "$SB/stubs/pgrep"
    sed -e "s#/usr/bin/usbgadget#$SB/stubs/usbgadget#g" -e "s#/sys/kernel/config#$SB/sys#g" -e "s#/run/udev#$SB/run/udev#g" -e "s#/storage#$SB/storage#g" "$D/heal.sh" > "$SB/heal.sh"
}
run() { SB=$D/cases/$1; PATH="$SB/stubs:$PATH" sh "$SB/heal.sh" > /dev/null 2>&1; echo "rc=$?" > "$SB/rc"; }
mk nocdc mtp 1 0 0;   run nocdc
mk healthy cdc 1 1 0; run healthy
mk half cdc 1 0 1;    run half
mk halffail cdc 1 0 0; run halffail
for c in nocdc healthy half halffail; do
    SB=$D/cases/$c
    echo "== $c $(cat "$SB/rc") calls=[$(cat "$SB/calls" 2>/dev/null | tr '\n' ';')] log=[$(sed 's/^[0-9-]* [0-9:]* \[[0-9.]*s\] //' "$SB/storage/etk_usbnet_heal.log" 2>/dev/null | cut -d: -f1 | tr '\n' ';')]"
done
RUNNER
sh "$TD/runner.sh" "$TD" > "$TD/host.out" 2>&1
hl() { grep "^== $1 " "$TD/host.out"; }
hl nocdc   | grep -q 'rc=0 calls=\[\] log=\[USB_MODE=mtp;\]' && ok "heal: not network mode -> exit 0, nothing touched" || fail "heal nocdc: '$(hl nocdc)'"
hl healthy | grep -q 'rc=0 calls=\[\] log=\[healthy;\]' && ok "heal: healthy gadget -> exit 0, no usbgadget call" || fail "heal healthy: '$(hl healthy)'"
hl half    | grep -q 'rc=0 calls=\[usbgadget stop;usbgadget start cdc;\] log=\[HALF-CONFIGURED;HEALED;\]' && ok "heal: half-configured -> stop + start cdc -> HEALED, exit 0" || fail "heal half: '$(hl half)'"
hl halffail | grep -q 'rc=1 calls=\[usbgadget stop;usbgadget start cdc;\] log=\[HALF-CONFIGURED;HEAL FAILED;\]' && ok "heal: start leaves it unconfigured -> HEAL FAILED, exit 1" || fail "heal halffail: '$(hl halffail)'"

# --- 2. install.sh deploys it the kit way ---
blk=$(awk '/STEP 6.72: USB-NET HEAL/ {i=1} i {print} i && /USB-net heal removed \(kill-switch/ {getline; print; exit}' "$TD/install.sh")
if [ -z "$blk" ]; then fail "install.sh has no STEP 6.72 USB-net heal"
else
    printf '%s\n' "$blk" | grep -q 'systemctl enable /storage/.config/system.d/etk-usbnet-heal.service' && ok "install.sh enables the heal unit by absolute path" || fail "heal unit not enabled by absolute path"
    printf '%s\n' "$blk" | grep -q 'WantedBy=rocknix.target' && ok "unit WantedBy=rocknix.target (the validated car12 stage)" || fail "unit target drifted"
    printf '%s\n' "$blk" | grep -q 'USBNETHEAL_OK' && printf '%s\n' "$blk" | grep -q 'USB-net heal did not verify' && ok "install.sh reads a verdict back and WARNs on failure" || fail "no heal verdict surface"
    printf '%s\n' "$blk" | grep -q 'ETK_USBNET_HEAL:-1' && printf '%s\n' "$blk" | grep -q 'rm -f /storage/.config/system.d/etk-usbnet-heal.service' && ok "kill-switch ETK_USBNET_HEAL=0 removes it" || fail "no kill-switch removal"
fi

# --- 3. uninstall coverage: every unit / boot script install.sh writes ---
for w in $(grep -o -E '/storage/\.config/(system\.d/[A-Za-z0-9_.@-]+\.service|custom_scripts/[A-Za-z0-9_.@-]+\.sh)' "$TD/install.sh" | sort -u); do
    grep -qF "$w" "$TD/uninstall.sh" || fail "uninstall.sh never removes $w"
done
missing=$(for w in $(grep -o -E '/storage/\.config/(system\.d/[A-Za-z0-9_.@-]+\.service|custom_scripts/[A-Za-z0-9_.@-]+\.sh)' "$TD/install.sh" | sort -u); do grep -qF "$w" "$TD/uninstall.sh" || echo "$w"; done)
[ -z "$missing" ] && ok "uninstall coverage: every unit + boot script install.sh writes is removed" || :

# --- 4. one source ---
grep -q 'HEAL_SH=$(body USBNETHEAL); HEAL_UNIT=$(body USBNETUNIT)' "$TD/usbnet_heal.sh" && ok "tools/usbnet_heal.sh stages install.sh's own bodies" || fail "usbnet_heal.sh carries its own copy (drift risk)"
grep -q 'Marker "USBNETHEALREMOTE"' "$TD/etk-install.ps1" && ok "PS port runs USBNETHEALREMOTE by marker" || fail "PS port lacks the USB-net heal"

# --- 5. rig leg ---
if [ "$RIGLEG" = 1 ]; then
    RIG="${RIG_SSH:-root@SM8250.local}"; RT="/tmp/etk_usbheal_$$"
    if tar -C "$TD" -cf - heal.sh runner.sh \
       | ssh -o BatchMode=yes "$RIG" "mkdir -p $RT && tar -C $RT -xf - && sh $RT/runner.sh $RT; R=\$?; rm -rf $RT; exit \$R" \
       > "$TD/rig.out" 2> "$TD/rig.err"; then
        cmp -s "$TD/host.out" "$TD/rig.out" && ok "rig leg: heal results byte-identical to host" \
          || { fail "rig leg: results DIFFER"; diff "$TD/host.out" "$TD/rig.out" | head -10; }
    else fail "rig leg: run errored: $(head -2 "$TD/rig.err")"; fi
else
    echo "note: rig leg skipped (--rig for the rig pass)"
fi

echo "test_usbnet_heal: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
