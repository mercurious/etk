#!/bin/bash
# ==========================================================
# tools/test_vpu_fw.sh -- bin/etk_vpu_fw.sh (install.sh STEP 6.86) on a fake car
# ==========================================================
# Kernel 7.2.0's qcom-iris wants qcom/vpu/vpu20_p4.mbn; ROCKNIX ships the blob as
# qcom/vpu-1.0/venus.mbn, so the encoder is dead at boot (dossier
# Onboard_LiveMirror_20261025.md §3 S0). The script seeds the OS's own blob (sha-gated),
# points firmware_class.path at /storage/etk-fw and rebinds qcom-iris once.
#
# Runs the real script against a temp tree: fake sysfs (platform device, qcom-iris
# driver dir with bind/unbind, firmware_class path, video4linux names), a fake stock blob
# behind a symlink like ROCKNIX's kernel overlay, and a fake iris core (VPU_PROBE: the
# node opens only once qcom-iris was bound with the blob staged and the path set). The
# pin is sed'ed per case, so the shipped placeholder never decides a result. Never
# touches the real /sys, /dev, /lib or /storage; no rig.
#
#   tools/test_vpu_fw.sh                  # working tree -- must PASS
#   tools/test_vpu_fw.sh --against main   # pre-change (no script) -- must FAIL
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; [ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
summary() { echo "test_vpu_fw: $PASS passed, $FAIL failed${REV:+ (against $REV)}"; [ "$FAIL" = 0 ]; }
TD=$(mktemp -d /tmp/etk_vpufw_XXXXXX)
[ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
if [ -n "$REV" ]; then git show "$REV:bin/etk_vpu_fw.sh" > "$TD/vpu.sh" 2>/dev/null || : > "$TD/vpu.sh"
else cp bin/etk_vpu_fw.sh "$TD/vpu.sh" 2>/dev/null || : > "$TD/vpu.sh"; fi
if [ ! -s "$TD/vpu.sh" ]; then fail "bin/etk_vpu_fw.sh does not exist"; summary; exit 1; fi
grep -q '^VPU_FW_SHA256=' "$TD/vpu.sh" && ok "the pin is one variable (VPU_FW_SHA256)" || fail "no VPU_FW_SHA256= line to pin"

DEV=aa00000.video-codec
head -c 65536 /dev/urandom > "$TD/venus.mbn"
GOOD=$(sha256sum "$TD/venus.mbn" | cut -d' ' -f1)
BAD=$(printf 'some other blob' | sha256sum | cut -d' ' -f1)
DRVR=sys/bus/platform/drivers/qcom-iris
FWP=sys/module/firmware_class/parameters/path
OVL=usr/lib/kernel-overlays/base/lib/firmware/qcom/vpu-1.0

mk() {  # mk <case>: a fresh fake car with a dead encoder
    SB=$TD/c/$1; rm -rf "$SB"
    mkdir -p "$SB/sys/bus/platform/devices/$DEV" "$SB/$DRVR/$DEV" "$SB/sys/module/firmware_class/parameters" \
             "$SB/sys/class/video4linux/video0" "$SB/sys/class/video4linux/video1" "$SB/dev" \
             "$SB/$OVL" "$SB/lib/firmware/qcom/vpu-1.0" "$SB/storage"
    echo qcom-iris-decoder > "$SB/sys/class/video4linux/video0/name"
    echo qcom-iris-encoder > "$SB/sys/class/video4linux/video1/name"
    : > "$SB/$FWP"; : > "$SB/$DRVR/bind"; : > "$SB/$DRVR/unbind"
    cp "$TD/venus.mbn" "$SB/$OVL/venus.mbn"
    ln -s "$SB/$OVL/venus.mbn" "$SB/lib/firmware/qcom/vpu-1.0/venus.mbn"   # ROCKNIX: a symlink into the overlay
    : > "$SB/env.sh"
}
# The fake iris core: the node opens once qcom-iris was bound with the blob staged under
# the firmware path (then it stays up, like the real core); else ENOMEM, like S0.
cat > "$TD/probe.sh" << 'PROBE'
#!/bin/sh
SB=$PROBE_SB
n=$(($(cat "$SB/probe.n" 2>/dev/null || echo 0) + 1)); echo "$n" > "$SB/probe.n"
b=$(cat "$SB/sys/bus/platform/drivers/qcom-iris/bind"); p=$(cat "$SB/sys/module/firmware_class/parameters/path")
echo "probe $n node=$1 bind=$b path=$p" >> "$SB/probe.log"
[ -e "$SB/up" ] && exit 0
if [ "$b" = aa00000.video-codec ] && [ -n "$p" ] && cmp -s "$p/qcom/vpu/vpu20_p4.mbn" "$SB/usr/lib/kernel-overlays/base/lib/firmware/qcom/vpu-1.0/venus.mbn"; then
    : > "$SB/up"; exit 0
fi
echo "Cannot allocate memory"; exit 1
PROBE
cat > "$TD/dead.sh" << 'DEAD'
#!/bin/sh
n=$(($(cat "$PROBE_SB/probe.n" 2>/dev/null || echo 0) + 1)); echo "$n" > "$PROBE_SB/probe.n"
echo "Cannot allocate memory"; exit 1
DEAD
chmod +x "$TD/probe.sh" "$TD/dead.sh"

run() {  # run <case> <pin> <subcmd> [VAR=val ...] -> $SB/out, $SB/rc
    SB=$TD/c/$1; local pin=$2 sub=$3; shift 3
    sed "s|^VPU_FW_SHA256=.*|VPU_FW_SHA256=\"$pin\"|" "$TD/vpu.sh" > "$SB/vpu.sh"
    env -i PATH="$PATH" HOME="$TD" VPU_SYSFS_ROOT="$SB" VPU_DEV_DIR="$SB/dev" VPU_FW_DIR="$SB/storage/etk-fw" \
        VPU_STOCK_FW="$SB/lib/firmware/qcom/vpu-1.0/venus.mbn" VPU_LOG="$SB/storage/etk_vpu_fw.log" VPU_WAIT_S=1 \
        ETK_VPU_ENV="$SB/env.sh" PROBE_SB="$SB" "$@" bash "$SB/vpu.sh" "$sub" > "$SB/out" 2>&1
    echo $? > "$SB/rc"
}
rc()   { cat "$TD/c/$1/rc"; }
lg()   { cat "$TD/c/$1/storage/etk_vpu_fw.log" 2>/dev/null; }
untouched() {  # untouched <case>: no seed, no path, no rebind
    local S=$TD/c/$1
    [ ! -e "$S/storage/etk-fw" ] && [ ! -s "$S/$FWP" ] && [ ! -s "$S/$DRVR/bind" ] && [ ! -s "$S/$DRVR/unbind" ]
}
line() { grep -n -F "$2" "$TD/c/$1/storage/etk_vpu_fw.log" 2>/dev/null | head -n1 | cut -d: -f1; }

# --- 1. kill-switch: ETK_VPU_FW=0 (environment, and etk.conf through env.sh) does nothing ---
mk ks; run ks "$GOOD" apply ETK_VPU_FW=0 VPU_PROBE="$TD/dead.sh"
[ "$(rc ks)" = 0 ] && untouched ks && [ ! -e "$TD/c/ks/storage/etk_vpu_fw.log" ] && [ ! -e "$TD/c/ks/probe.n" ] \
    && ok "ETK_VPU_FW=0 -> exit 0, nothing seeded/written/probed, no log" || fail "kill-switch: rc=$(rc ks) $(cat "$TD/c/ks/out")"
mk ks2; echo 'ETK_VPU_FW=0' > "$TD/c/ks2/env.sh"; run ks2 "$GOOD" apply VPU_PROBE="$TD/dead.sh"
[ "$(rc ks2)" = 0 ] && untouched ks2 && ok "ETK_VPU_FW=0 from etk.conf (via env.sh) is honoured too" || fail "etk.conf kill-switch ignored: $(cat "$TD/c/ks2/out")"

# --- 2. the encoder already opens (built-in open probe on a real file): no rebind ---
mk up; : > "$TD/c/up/dev/video1"; run up "$GOOD" apply
[ "$(rc up)" = 0 ] && untouched up && lg up | grep -q "already up: $TD/c/up/dev/video1 opens (no rebind)" \
    && ok "node already opens -> 'already up', no seed, no path, no rebind" || fail "already-up: rc=$(rc up) $(cat "$TD/c/up/out")"

# --- 3. sha gate: mismatch / placeholder / missing stock -> refuse + log, touch nothing ---
mk mm; run mm "$BAD" apply VPU_PROBE="$TD/dead.sh"
[ "$(rc mm)" = 0 ] && untouched mm && lg mm | grep -q "REFUSED: stock .*venus.mbn sha256 $GOOD != pinned $BAD" \
    && ok "stock sha mismatch -> REFUSED line names both hashes, nothing seeded, no rebind" || fail "mismatch: rc=$(rc mm) log=$(lg mm)"
mk ph; run ph "UNPINNED-sha256-of-stock-venus.mbn" apply VPU_PROBE="$TD/dead.sh"
[ "$(rc ph)" = 0 ] && untouched ph && lg ph | grep -q "REFUSED: VPU_FW_SHA256 is not pinned" \
    && ok "placeholder pin -> REFUSED (not pinned), nothing seeded" || fail "placeholder: rc=$(rc ph) log=$(lg ph)"
mk ns; rm -f "$TD/c/ns/$OVL/venus.mbn"; run ns "$GOOD" apply VPU_PROBE="$TD/dead.sh"
[ "$(rc ns)" = 0 ] && untouched ns && lg ns | grep -q "REFUSED: stock firmware .* missing" \
    && ok "stock blob missing (dangling symlink) -> REFUSED, nothing seeded" || fail "missing stock: log=$(lg ns)"

# --- 4. happy path: seed, path, unbind, bind in that order; the node wakes ---
mk hp; run hp "$GOOD" apply VPU_PROBE="$TD/probe.sh"
S=$TD/c/hp; B=$S/storage/etk-fw/qcom/vpu/vpu20_p4.mbn
[ "$(rc hp)" = 0 ] && ok "dead encoder + good stock -> apply exit 0" || fail "happy: rc=$(rc hp) $(cat "$S/out")"
[ -f "$B" ] && [ ! -L "$B" ] && cmp -s "$B" "$TD/venus.mbn" && ok "seeded vpu20_p4.mbn = the stock bytes (a copy, not a symlink)" || fail "blob not seeded intact"
FD="$S/storage/etk-fw"
[ "$(cat "$S/$FWP")" = "$FD" ] && [ "$(wc -c < "$S/$FWP" | tr -d ' ')" = "${#FD}" ] && ok "firmware_class.path = the firmware dir (no newline)" || fail "path='$(cat "$S/$FWP")'"
[ "$(cat "$S/$DRVR/unbind")" = $DEV ] && [ "$(cat "$S/$DRVR/bind")" = $DEV ] && ok "unbind + bind got exactly '$DEV'" || fail "unbind='$(cat "$S/$DRVR/unbind")' bind='$(cat "$S/$DRVR/bind")'"
a=$(line hp "seeded "); b=$(line hp "write /$FWP <- "); c=$(line hp "write /$DRVR/unbind <- '$DEV'"); d=$(line hp "write /$DRVR/bind <- '$DEV'"); e=$(line hp "WOKE: ")
if [ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ -n "$d" ] && [ -n "$e" ] && [ "$a" -lt "$b" ] && [ "$b" -lt "$c" ] && [ "$c" -lt "$d" ] && [ "$d" -lt "$e" ]; then
    ok "order: seed ($a) < path ($b) < unbind ($c) < bind ($d) < WOKE ($e)"
else fail "write order wrong: seed=$a path=$b unbind=$c bind=$d woke=$e"; lg hp; fi
head -n1 "$S/probe.log" | grep -q 'bind= path=$' && tail -n1 "$S/probe.log" | grep -q "bind=$DEV path=$S/storage/etk-fw" \
    && ok "the core was probed dead before any write, alive after the rebind" || fail "probe log: $(cat "$S/probe.log")"
[ "$(grep -c "write /$DRVR/bind" "$S/storage/etk_vpu_fw.log")" = 1 ] && ok "exactly one bind write (no retry loop)" || fail "bind writes: $(grep -c "write /$DRVR/bind" "$S/storage/etk_vpu_fw.log")"
lg hp | head -n1 | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} \[' && ok "log lines are timestamped" || fail "log has no timestamp: $(lg hp | head -n1)"

# --- 5. verify: one line, exit 0 when the node opens / 1 when it does not ---
run hp "$GOOD" verify VPU_PROBE="$TD/probe.sh"
[ "$(rc hp)" = 0 ] && [ "$(wc -l < "$S/out")" = 1 ] && grep -q '^VPU verify OK: .*/dev/video1 opens' "$S/out" \
    && ok "verify on a woken encoder: exit 0, one OK line" || fail "verify ok: rc=$(rc hp) $(cat "$S/out")"
run mm "$BAD" verify VPU_PROBE="$TD/dead.sh"
[ "$(rc mm)" = 1 ] && [ "$(wc -l < "$TD/c/mm/out")" = 1 ] && grep -q '^VPU verify FAIL: .*Cannot allocate memory' "$TD/c/mm/out" \
    && ok "verify on a dead encoder: exit 1, one FAIL line naming ENOMEM" || fail "verify fail: rc=$(rc mm) $(cat "$TD/c/mm/out")"
mk vb; run vb "$GOOD" verify
[ "$(rc vb)" = 1 ] && grep -q '^VPU verify FAIL: .*/dev/video1 absent' "$TD/c/vb/out" && ok "verify with the built-in open: absent node -> FAIL" || fail "verify built-in: $(cat "$TD/c/vb/out")"

# --- 6. idempotent: a second apply on a live encoder rebinds nothing ---
: > "$S/$DRVR/bind"; : > "$S/$DRVR/unbind"; run hp "$GOOD" apply VPU_PROBE="$TD/probe.sh"
[ "$(rc hp)" = 0 ] && [ ! -s "$S/$DRVR/bind" ] && [ ! -s "$S/$DRVR/unbind" ] && lg hp | tail -n1 | grep -q 'already up' \
    && ok "second apply on a live encoder -> 'already up', no rebind" || fail "second apply: $(lg hp | tail -n2)"

# --- 7. status: human lines ---
run hp "$GOOD" status VPU_PROBE="$TD/probe.sh"
[ "$(rc hp)" = 0 ] && grep -q "^pin       $GOOD" "$S/out" && grep -q '^staged    .*(match)' "$S/out" && grep -q '^stock     .* 65536 B .*(match)' "$S/out" \
    && grep -q "^fw path   firmware_class.path='$S/storage/etk-fw'" "$S/out" && grep -q "^driver    $DEV bound" "$S/out" && grep -q '^encoder   .* opens' "$S/out" \
    && ok "status: pin, stock, staged, path, driver, encoder lines" || fail "status: $(cat "$S/out")"
run ph "UNPINNED-x" status VPU_PROBE="$TD/dead.sh"
grep -q '^pin       NOT PINNED' "$TD/c/ph/out" && ok "status names an unpinned build" || fail "status unpinned: $(cat "$TD/c/ph/out")"

# --- 8. undo restores ---
run hp "$GOOD" undo
[ "$(rc hp)" = 0 ] && [ -z "$(cat "$S/$FWP")" ] && [ "$(wc -c < "$S/$FWP")" = 1 ] && [ ! -e "$S/storage/etk-fw" ] \
    && ok "undo: path cleared with a newline write (empty), /storage/etk-fw removed" || fail "undo: path='$(cat "$S/$FWP")' bytes=$(wc -c < "$S/$FWP") fw=$(ls "$S/storage")"
mk dp; printf '/foreign/fw' > "$TD/c/dp/$FWP"; run dp "$GOOD" apply VPU_PROBE="$TD/probe.sh"
lg dp | grep -q "held '/foreign/fw' (not ours): replaced" && [ "$(cat "$TD/c/dp/$FWP")" = "$TD/c/dp/storage/etk-fw" ] \
    && ok "a foreign firmware path is logged, saved and replaced" || fail "displace: $(lg dp)"
run dp "$GOOD" undo
[ "$(cat "$TD/c/dp/$FWP")" = /foreign/fw ] && [ ! -e "$TD/c/dp/storage/etk-fw" ] && ok "undo puts the displaced path back" || fail "undo displaced: '$(cat "$TD/c/dp/$FWP")'"
mk uf; printf '/foreign/fw' > "$TD/c/uf/$FWP"; mkdir -p "$TD/c/uf/storage/etk-fw"; run uf "$GOOD" undo
[ "$(cat "$TD/c/uf/$FWP")" = /foreign/fw ] && lg uf | grep -q "not ours): left alone" && ok "undo leaves a path that is not ours alone" || fail "undo foreign: $(lg uf)"

# --- 9. edges: not bound, other board, stale staged blob, wake failure is bounded ---
mk nb; rmdir "$TD/c/nb/$DRVR/$DEV"; run nb "$GOOD" apply VPU_PROBE="$TD/probe.sh"
[ "$(rc nb)" = 0 ] && [ ! -s "$TD/c/nb/$DRVR/unbind" ] && [ "$(cat "$TD/c/nb/$DRVR/bind")" = $DEV ] && lg nb | grep -q 'bind only' \
    && ok "device not bound -> bind only (no unbind write)" || fail "not bound: $(lg nb)"
mk ob; rm -rf "$TD/c/ob/sys/bus/platform/devices/$DEV"; run ob "$GOOD" apply VPU_PROBE="$TD/probe.sh"
[ "$(rc ob)" = 0 ] && untouched ob && lg ob | grep -q "no $DEV on this board" && ok "no iris device (another board) -> nothing" || fail "other board: $(lg ob)"
mk nd; rm -rf "$TD/c/nd/$DRVR"; run nd "$GOOD" apply VPU_PROBE="$TD/probe.sh"
[ "$(rc nd)" = 0 ] && untouched nd && lg nd | grep -q "qcom-iris driver not loaded" && ok "qcom-iris not loaded -> nothing" || fail "no driver: $(lg nd)"
mk st; mkdir -p "$TD/c/st/storage/etk-fw/qcom/vpu"; echo junk > "$TD/c/st/storage/etk-fw/qcom/vpu/vpu20_p4.mbn"; run st "$GOOD" apply VPU_PROBE="$TD/probe.sh"
cmp -s "$TD/c/st/storage/etk-fw/qcom/vpu/vpu20_p4.mbn" "$TD/venus.mbn" && [ "$(rc st)" = 0 ] && ok "a stale staged blob is re-seeded from stock" || fail "stale blob: $(lg st)"
mk wf; run wf "$GOOD" apply VPU_PROBE="$TD/dead.sh"
[ "$(rc wf)" = 1 ] && lg wf | grep -q 'WAKE FAILED: .*Cannot allocate memory' && [ "$(cat "$TD/c/wf/probe.n")" = 3 ] \
    && [ "$(grep -c "write /$DRVR/bind" "$TD/c/wf/storage/etk_vpu_fw.log")" = 1 ] \
    && ok "wake failure: exit 1, WAKE FAILED logged, one bind, bounded wait (3 probes at VPU_WAIT_S=1)" || fail "wake fail: rc=$(rc wf) probes=$(cat "$TD/c/wf/probe.n") $(lg wf | tail -n1)"

# --- 10. the car runs BusyBox: no GNU-only flags, no fractional sleep, one driver only ---
code=$(grep -v '^[[:space:]]*#' "$TD/vpu.sh")
gnu=$(printf '%s\n' "$code" | grep -nE '\b(cp|mv|rm|mkdir|cut|date|sha256sum|tail|head|sleep|stat|readlink|sed|grep|cat|wc|tr|printf|echo) [^|;)]*--[a-z]' || true)
[ -z "$gnu" ] && ok "no long (GNU-only) options on the userland tools" || fail "GNU-style options: $gnu"
printf '%s\n' "$code" | grep -qE 'sleep [0-9]*\.' && fail "fractional sleep (BusyBox may lack it)" || ok "integer sleeps only"
others=$(printf '%s\n' "$code" | grep -oE '/sys/bus/platform/drivers/[A-Za-z0-9_-]+' | sort -u | grep -v '/qcom-iris$' || true)
[ -z "$others" ] && ok "touches no driver but qcom-iris" || fail "other drivers: $others"
printf '%s\n' "$code" | grep -qE '\b(reboot|poweroff|systemctl)\b' && fail "the script reboots or drives systemd" || ok "no reboot, no systemctl"

summary
