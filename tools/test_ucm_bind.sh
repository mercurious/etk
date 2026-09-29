#!/bin/sh
# ============================================================
# test_ucm_bind.sh — discrimination suite for config/etk-ucm-bind.sh (STEP 6.76)
# ------------------------------------------------------------
# The Flip 2 internal-mic UCM overlay binds at boot ONLY when every gate holds
# (Flip 2 · booted DT carries "Internal Mic" · stock file == the overlay's base sha
# · not already bound); every failed gate must leave stock UCM and say why.
# Also an anti-drift check: config/ucm/sm8250-HiFi-RP.flip2.conf minus the ETK
# header and the documented delta must hash to ETK_UCM_BASE_SHA — the overlay is
# provably "stock + our delta", nothing else.
# Runs on the host and the rig's BusyBox sh (mount is a stub; writes only in tmp):
#   host: sh tools/test_ucm_bind.sh
#   rig:  scp config/etk-ucm-bind.sh config/ucm/sm8250-HiFi-RP.flip2.conf
#         tools/test_ucm_bind.sh to /tmp; ssh 'sh /tmp/test_ucm_bind.sh /tmp'
# Exit 0 = all pass; nonzero = failure count.
# ============================================================
set -u
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
BIND="$ROOT/config/etk-ucm-bind.sh"; [ -f "$BIND" ] || BIND="$ROOT/etk-ucm-bind.sh"
OVL="$ROOT/config/ucm/sm8250-HiFi-RP.flip2.conf"; [ -f "$OVL" ] || OVL="$ROOT/sm8250-HiFi-RP.flip2.conf"
[ -f "$BIND" ] && [ -f "$OVL" ] || { echo "FATAL: bind script / overlay not found under $ROOT"; exit 99; }
T="${TMPDIR:-/tmp}/ucmbind_test.$$"; mkdir -p "$T"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { D="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$D"; else bad "$D"; fi; }
if ! command -v sha256sum >/dev/null 2>&1; then
    mkdir -p "$T/shim"; printf '#!/bin/sh\nshasum -a 256 "$@"\n' > "$T/shim/sha256sum"; chmod +x "$T/shim/sha256sum"; PATH="$T/shim:$PATH"
fi
BASE=$(sed -n 's/^ETK_UCM_BASE_SHA="\(.*\)"/\1/p' "$BIND")

echo "== anti-drift: overlay = stock + documented delta"
# strip: the leading '#' header block + its blank line, the Headset ConflictingDevice
# stanza, and the appended Mic device (a blank line, then to EOF)
awk '
    !body && /^#/ { next }
    !body && /^$/ { body=1; next }
    { body=1 }
    skip==2 { skip=0; if ($0 == "") next }
    /^\tConflictingDevice \[$/ && !seen { skip=1; seen=1 }
    skip==1 { if ($0 ~ /^\t\]$/) skip=2; next }
    { print }
' "$OVL" | awk '/^SectionDevice."Mic" \{$/ { exit } { buf[++n]=$0 } END { while (n > 0 && buf[n] == "") n--; for (i = 1; i <= n; i++) print buf[i] }' > "$T/stock.recon"
check "reconstructed stock hashes to ETK_UCM_BASE_SHA" [ "$(sha256sum "$T/stock.recon" | cut -d' ' -f1)" = "$BASE" ]
check "overlay defines the Mic device on hw:,2" grep -q 'CapturePCM "hw:${CardId},2"' "$OVL"
check "Mic routes SWR_DMIC3 (the validated slot)" grep -q "TX SMIC MUX0' SWR_DMIC3" "$OVL"

# fixture <name>: every gate passing; scenarios then break one gate each
fixture() {
    F="$T/$1"; rm -rf "$F"; mkdir -p "$F"
    cp "$T/stock.recon" "$F/target.conf"
    cp "$OVL" "$F/overlay.conf"
    printf 'retroidpocket,rpflip2\0qcom,sm8250\0' > "$F/compat"
    printf 'Microphone\0Internal Mic\0' > "$F/widgets"
    printf '/dev/root / squashfs ro 0 0\n' > "$F/mounts"
    printf '#!/bin/sh\necho "$@" >> "%s/mount.calls"\nexit ${MOUNT_RC:-0}\n' "$F" > "$F/mount"; chmod +x "$F/mount"
}
run() {
    F="$T/$1"
    UCMB_TARGET="$F/target.conf" UCMB_OVERLAY="$F/overlay.conf" UCMB_COMPAT="$F/compat" \
    UCMB_WIDGETS="$F/widgets" UCMB_MOUNTS="$F/mounts" UCMB_MOUNT="$F/mount" TRIPWIRE_LOG="$F/trip.log" \
    sh "$BIND" > "$F/out" 2>&1; echo $? > "$F/rc"
}
bound()   { grep -q -- "--bind $T/$1/overlay.conf $T/$1/target.conf" "$T/$1/mount.calls" 2>/dev/null; }
unbound() { [ ! -s "$T/$1/mount.calls" ]; }

echo "== all gates hold -> bind"
fixture good; run good
check "exit 0" [ "$(cat "$T/good/rc")" = 0 ]
check "bind-mounts overlay over target" bound good
check "tripwire logs the bind" grep -q "overlay bound" "$T/good/trip.log"

echo "== each broken gate -> stock UCM, reason logged, exit 0"
fixture rp5; printf 'retroidpocket,rp5\0qcom,sm8250\0' > "$T/rp5/compat"; run rp5
check "not a Flip 2: no bind" unbound rp5
check "not a Flip 2: logged" grep -q "not a Flip 2" "$T/rp5/trip.log"
fixture nodt; printf 'Microphone\0Headset Mic\0' > "$T/nodt/widgets"; run nodt
check "booted DT lacks Internal Mic: no bind" unbound nodt
check "booted DT lacks Internal Mic: logged" grep -q "no 'Internal Mic' widget" "$T/nodt/trip.log"
fixture nowidgets; rm -f "$T/nowidgets/widgets"; run nowidgets
check "stock DTB (no widgets prop at all): no bind" unbound nowidgets
fixture stale; echo "# ROCKNIX changed this" >> "$T/stale/target.conf"; run stale
check "stock file changed: no bind" unbound stale
check "stock file changed: logged stale" grep -q "overlay stale" "$T/stale/trip.log"
fixture noovl; rm -f "$T/noovl/overlay.conf"; run noovl
check "overlay missing: no bind" unbound noovl
fixture again; printf 'x %s none rw,bind 0 0\n' "$T/again/target.conf" >> "$T/again/mounts"; run again
check "already bound: no second bind" unbound again
fixture mfail; MOUNT_RC=1 run mfail
check "mount failure: exit 0 (fail-soft)" [ "$(cat "$T/mfail/rc")" = 0 ]
check "mount failure: logged" grep -q "bind-mount FAILED" "$T/mfail/trip.log"
for s in good rp5 nodt nowidgets stale noovl again mfail; do
    cmp -s "$T/$s/target.conf" "$T/stock.recon" 2>/dev/null || [ "$s" = stale ] || bad "$s: target file modified"
done
ok "target file never written in any scenario"

rm -rf "$T"
echo; echo "$PASS passed, $FAIL failed"
exit $FAIL
