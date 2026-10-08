#!/bin/bash
# ==========================================================
# ABL SLOT — the ROCKNIX-ABL era KERNEL slot, as a tool (host-side)
# ==========================================================
# Since ROCKNIX 20261001 the SM8250 boots through ROCKNIX-ABL: no GRUB, no
# menu, no kernel A/B. The ABL loads exactly one file, /flash/KERNEL, an
# Android boot.img (gzip Image + the 9 device DTBs + the cmdline BAKED IN —
# the ABL appends nothing). "Stock is one grub pick away" has no mechanism
# any more; this tool is its replacement for a GTK kernel on an ABL unit:
#
#   status  <target>                 what is in the slot and what is RUNNING (read-only)
#   stage   <target> <boot.img>      OPERATOR: park stock as KERNEL.etk-stock (once,
#                                    md5-proven pristine), put the GTK boot.img in the
#                                    slot, sha-verify the read-back. Then a COLD boot.
#   verify  <target> [<boot.img>]    read-only, after the boot: did the ABL boot the
#                                    slot, is the keepalive on the cmdline, modules,
#                                    panel, GPU — the surface the change must show on
#   restore <target>                 OPERATOR: put KERNEL.etk-stock back (md5 vs KERNEL.md5)
#   restore --card <mountpoint>      the unit did not boot: card in the Air, same swap
#                                    done locally on the mounted boot partition
#
#   options: --car carN   refuse unless the unit reached IS that car (scripts/etk_car.sh)
#
# stage/restore are DEPLOY (TRACK_MANUAL §1.1 — the rig can be bricked; the
# operator runs them). status/verify never write anything on the unit.
# Nothing lives on the unit (no push-list entry); the staged copy + its sha sit
# under /storage/rocknix-gtk/abl/ so verify can judge the slot after the boot.
#
# Fallback contract: the FIRST stage on a unit copies /flash/KERNEL to
# /flash/KERNEL.etk-stock and proves it against the OS's own /flash/KERNEL.md5;
# a slot that is already non-stock with no fallback copy is REFUSED (restore
# stock by hand first). A unit that fails to boot the GTK boot.img is recovered
# with the card in the Air: `restore --card`.
#
# Remote shell is BusyBox POSIX (manual §Q): no long options, no bashisms.
# Harness: tools/test_abl_slot.sh (fake ssh/scp sandbox; --against ee353f3 fails).
# Dossier: rocknix-gtk/UPSTREAM_20261001.md (K2 execution plan, step 4).
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_FLASH="${ABL_FLASH:-/flash}"; R_STG="${ABL_STG:-/storage/rocknix-gtk/abl}"
R_PROC="${ABL_PROC:-/proc}";    R_SYS="${ABL_SYS:-/sys}"; R_MODROOT="${ABL_MODROOT:-/usr/lib/modules}"

die()  { echo "ABL_SLOT_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE="${1:-}"; [ -n "$MODE" ] || usage; shift
CAR=""; CARD=""; TARGET=""; IMG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --car)  CAR="${2:?--car needs carN}"; shift 2 ;;
        --card) CARD="${2:?--card needs a mountpoint}"; shift 2 ;;
        -h|--help) usage ;;
        -*) die "unknown option $1" ;;
        *) if [ -z "$TARGET" ]; then TARGET="$1"; elif [ -z "$IMG" ]; then IMG="$1"; else die "too many arguments"; fi; shift ;;
    esac
done

# ---- host-side readers (python3: the boot.img header, no rig needed) --------
bootimg_info() {   # $1 file -> lines: magic= cmdline= linux= dtbs=
    python3 -I - "$1" <<'PY'
import struct, sys, zlib
d = open(sys.argv[1], 'rb').read()
ok = d[:8] == b'ANDROID!'
print('magic=' + ('ok' if ok else 'bad'))
if not ok: sys.exit(0)
f = struct.unpack_from('<8s10I16s512s32s1024s', d, 0)
ksz, ps = f[1], f[8]
print('cmdline=' + f[12].rstrip(b'\0').decode(errors='replace'))
try:
    z = zlib.decompressobj(31); img = z.decompress(d[ps:ps + ksz]); tail = z.unused_data
except zlib.error:
    print('linux=?'); print('dtbs=?'); sys.exit(0)
lv = img.find(b'Linux version ')
print('linux=' + (img[lv:img.index(b'\n', lv)].decode(errors='replace') if lv >= 0 else '?'))
n, p = 0, 0
while p + 8 <= len(tail):
    m, t = struct.unpack_from('>II', tail, p)
    if m != 0xd00dfeed or t < 40 or p + t > len(tail): break
    n += 1; p += t
print(f'dtbs={n}')
PY
}
field() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1; }

car_gate() {   # refuse unless the unit at $TARGET is $CAR (report-only without --car)
    [ -f "$ETK_ROOT/scripts/etk_car.sh" ] || { [ -z "$CAR" ] || die "--car given but scripts/etk_car.sh is missing"; return 0; }
    . "$ETK_ROOT/scripts/etk_car.sh"
    local msg; msg=$(etk_car_verify "$TARGET" "$CAR"); local rc=$?
    echo "$msg"
    [ $rc -eq 0 ] || die "the unit at $TARGET is not ${CAR}"
}

rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }
remote_env() { printf "FLASH='%s' STG='%s' PROC='%s' SYS='%s' MODROOT='%s'" "$R_FLASH" "$R_STG" "$R_PROC" "$R_SYS" "$R_MODROOT"; }

# ---- the remote bodies (BusyBox sh) -----------------------------------------
# status: what the slot holds and what is running. Read-only.
REMOTE_STATUS='
magic=$(head -c 8 "$FLASH/KERNEL" 2>/dev/null)
echo "slot_magic=$magic"
echo "slot_sha=$(sha256sum "$FLASH/KERNEL" 2>/dev/null | cut -d" " -f1)"
echo "slot_size=$(wc -c < "$FLASH/KERNEL" 2>/dev/null)"
echo "slot_cmdline=$(dd if="$FLASH/KERNEL" bs=1 skip=64 count=512 2>/dev/null | tr -d "\000")"
echo "slot_md5=$(md5sum "$FLASH/KERNEL" 2>/dev/null | cut -d" " -f1)"
echo "os_md5=$(cut -d" " -f1 "$FLASH/KERNEL.md5" 2>/dev/null)"
if [ -f "$FLASH/KERNEL.etk-stock" ]; then
  echo "stock_sha=$(sha256sum "$FLASH/KERNEL.etk-stock" | cut -d" " -f1)"
  echo "stock_md5=$(md5sum "$FLASH/KERNEL.etk-stock" | cut -d" " -f1)"
else echo "stock_sha=none"; echo "stock_md5=none"; fi
echo "grub=$([ -d "$FLASH/EFI" ] && echo present || echo absent)"
echo "expected_sha=$(cat "$STG/expected.sha256" 2>/dev/null)"
echo "expected_name=$(cat "$STG/expected.name" 2>/dev/null)"
echo "run_cmdline=$(cat "$PROC/cmdline" 2>/dev/null)"
echo "run_version=$(cat "$PROC/version" 2>/dev/null)"
echo "run_release=$(uname -r 2>/dev/null)"
echo "model=$(tr -d "\000" < "$PROC/device-tree/model" 2>/dev/null)"
echo "modules_loaded=$(lsmod 2>/dev/null | tail -n +2 | wc -l | tr -d " ")"
echo "modtree=$([ -d "$MODROOT/$(uname -r)" ] && echo present || echo MISSING)"
echo "keepalive_param=$(cat "$SYS/module/msm/parameters/context_keepalive" 2>/dev/null)"
echo "dsi_status=$(cat "$SYS"/class/drm/card*-DSI-1/status 2>/dev/null | head -n1)"
echo "drm_mode=$(grep -m1 "mode: \"[0-9]" "$SYS/kernel/debug/dri/0/state" 2>/dev/null | sed "s/^[[:space:]]*//")"
echo "gpu=$(dmesg 2>/dev/null | grep -m1 -E "bound [0-9a-f]+\.gpu|loaded qcom/a[0-9]+_sqe" | sed "s/^\[[^]]*\] //")"
echo "a6xx_faults=$(dmesg 2>/dev/null | grep -c "a6xx_irq.*gpu fault")"
echo "keepalive_rescues=$(dmesg 2>/dev/null | grep -c "context_keepalive: surviving hang")"
echo "sound_cards=$(grep -c "^ *[0-9]" "$PROC/asound/cards" 2>/dev/null)"
echo "flash_free_kb=$(df -k "$FLASH" 2>/dev/null | tail -n1 | awk "{print \$4}")"
echo "ABL_REMOTE_OK"
'

# stage: the only writer of the slot. Every byte is sha-verified after it lands.
REMOTE_STAGE='
set -e
ro() { mount -o remount,ro "$FLASH" 2>/dev/null || true; }
fail() { echo "SLOT_FAIL $*"; ro; exit 1; }
S=$(sha256sum "$STG/KERNEL.staged" | cut -d" " -f1)
[ "$S" = "$HOST_SHA" ] || { rm -f "$STG/KERNEL.staged"; fail "staging sha mismatch ($S)"; }
[ "$(head -c 8 "$STG/KERNEL.staged")" = "ANDROID!" ] || fail "staged file is not a boot.img"
[ -d "$FLASH/EFI" ] && fail "GRUB is present on $FLASH -- this is a GRUB-era unit, use install.sh STEP 6.4"
[ "$(head -c 8 "$FLASH/KERNEL")" = "ANDROID!" ] || fail "$FLASH/KERNEL is not a boot.img -- not an ABL-era unit"
NEED=$(( $(wc -c < "$STG/KERNEL.staged") / 1024 ))
[ -f "$FLASH/KERNEL.etk-stock" ] || NEED=$(( NEED + $(wc -c < "$FLASH/KERNEL") / 1024 ))
FREE=$(df -k "$FLASH" | tail -n1 | awk "{print \$4}")
[ "$FREE" -gt $(( NEED + 2048 )) ] || fail "$FLASH has ${FREE} kB free, need ${NEED} kB + margin"
OSMD5=$(cut -d" " -f1 "$FLASH/KERNEL.md5" 2>/dev/null)
mount -o remount,rw "$FLASH" || fail "cannot remount $FLASH rw"
if [ ! -f "$FLASH/KERNEL.etk-stock" ]; then
  CUR=$(md5sum "$FLASH/KERNEL" | cut -d" " -f1)
  [ -n "$OSMD5" ] && [ "$CUR" != "$OSMD5" ] && fail "no fallback copy exists and $FLASH/KERNEL ($CUR) is not the OS kernel per KERNEL.md5 ($OSMD5) -- restore stock by hand before staging"
  cp "$FLASH/KERNEL" "$FLASH/KERNEL.etk-stock" || fail "cannot park the stock kernel"
  sync
  [ "$(md5sum "$FLASH/KERNEL.etk-stock" | cut -d" " -f1)" = "$CUR" ] || { rm -f "$FLASH/KERNEL.etk-stock"; fail "fallback copy read-back mismatch"; }
  PARKED=new
else PARKED=kept; fi
STOCKMD5=$(md5sum "$FLASH/KERNEL.etk-stock" | cut -d" " -f1)
cp "$STG/KERNEL.staged" "$FLASH/KERNEL.new" || fail "cannot write $FLASH/KERNEL.new"
sync
[ "$(sha256sum "$FLASH/KERNEL.new" | cut -d" " -f1)" = "$HOST_SHA" ] || { rm -f "$FLASH/KERNEL.new"; fail "flash write read-back mismatch"; }
mv -f "$FLASH/KERNEL.new" "$FLASH/KERNEL" || fail "cannot move the new kernel into the slot"
sync
F=$(sha256sum "$FLASH/KERNEL" | cut -d" " -f1)
[ "$F" = "$HOST_SHA" ] || fail "slot read-back mismatch ($F) -- restore NOW: $0 restore"
printf "%s\n" "$HOST_SHA" > "$STG/expected.sha256"
printf "%s\n" "$KNAME"    > "$STG/expected.name"
ro
echo "SLOT_OK slot_sha=$F stock=$PARKED stock_md5=$STOCKMD5 os_md5=${OSMD5:-none} pristine=$([ -n "$OSMD5" ] && [ "$STOCKMD5" = "$OSMD5" ] && echo yes || echo UNPROVEN)"
'

REMOTE_RESTORE='
set -e
ro() { mount -o remount,ro "$FLASH" 2>/dev/null || true; }
fail() { echo "SLOT_FAIL $*"; ro; exit 1; }
[ -f "$FLASH/KERNEL.etk-stock" ] || fail "no $FLASH/KERNEL.etk-stock to restore from"
[ "$(head -c 8 "$FLASH/KERNEL.etk-stock")" = "ANDROID!" ] || fail "KERNEL.etk-stock is not a boot.img"
OSMD5=$(cut -d" " -f1 "$FLASH/KERNEL.md5" 2>/dev/null)
W=$(sha256sum "$FLASH/KERNEL.etk-stock" | cut -d" " -f1)
mount -o remount,rw "$FLASH" || fail "cannot remount $FLASH rw"
cp "$FLASH/KERNEL.etk-stock" "$FLASH/KERNEL.new" || fail "cannot write $FLASH/KERNEL.new"
sync
[ "$(sha256sum "$FLASH/KERNEL.new" | cut -d" " -f1)" = "$W" ] || { rm -f "$FLASH/KERNEL.new"; fail "restore write read-back mismatch"; }
mv -f "$FLASH/KERNEL.new" "$FLASH/KERNEL" || fail "cannot move stock into the slot"
sync
F=$(sha256sum "$FLASH/KERNEL" | cut -d" " -f1)
[ "$F" = "$W" ] || fail "slot read-back mismatch after restore ($F)"
M=$(md5sum "$FLASH/KERNEL" | cut -d" " -f1)
rm -f "$STG/expected.sha256" "$STG/expected.name"
ro
echo "SLOT_OK restored slot_sha=$F md5=$M os_md5=${OSMD5:-none} pristine=$([ -n "$OSMD5" ] && [ "$M" = "$OSMD5" ] && echo yes || echo UNPROVEN)"
'

# ---- modes --------------------------------------------------------------------
print_status() {   # $1 = remote status output
    local p="$1"
    printf '%s\n' "$p" | grep -q '^ABL_REMOTE_OK$' || die "could not read the unit at $TARGET"
    local magic; magic=$(field "$p" slot_magic)
    echo "unit      : $(field "$p" model) · host kernel $(field "$p" run_release) · GRUB $(field "$p" grub)"
    echo "slot      : $FLASH_LABEL/KERNEL $( [ "$magic" = "ANDROID!" ] && echo boot.img || echo "NOT a boot.img ($magic)") · $(field "$p" slot_size) B · sha $(field "$p" slot_sha | cut -c1-12)…"
    echo "  cmdline : $(field "$p" slot_cmdline)"
    local smd5 omd5; smd5=$(field "$p" slot_md5); omd5=$(field "$p" os_md5)
    echo "  is stock: $( [ -n "$omd5" ] && { [ "$smd5" = "$omd5" ] && echo "yes (md5 matches KERNEL.md5)" || echo "NO (md5 $smd5 vs KERNEL.md5 $omd5)"; } || echo "unknown (no KERNEL.md5)")"
    local stk; stk=$(field "$p" stock_sha)
    if [ "$stk" = none ]; then echo "fallback  : NONE parked (first stage will park $FLASH_LABEL/KERNEL as KERNEL.etk-stock)"
    else echo "fallback  : KERNEL.etk-stock sha $(echo "$stk" | cut -c1-12)… $( [ "$(field "$p" stock_md5)" = "$omd5" ] && echo "(pristine: md5 matches KERNEL.md5)" || echo "(md5 does NOT match KERNEL.md5)")"; fi
    local exp; exp=$(field "$p" expected_sha)
    [ -n "$exp" ] && echo "staged    : $(field "$p" expected_name) sha $(echo "$exp" | cut -c1-12)… $( [ "$exp" = "$(field "$p" slot_sha)" ] && echo "= slot" || echo "!= slot (slot was changed since)")"
    echo "running   : $(field "$p" run_version | cut -c1-110)"
    echo "  cmdline : $(field "$p" run_cmdline)"
    echo "  keepalive: cmdline $(printf '%s' "$(field "$p" run_cmdline)" | grep -q 'msm.context_keepalive=1' && echo on || echo off) · param $(field "$p" keepalive_param | sed 's/^$/absent/') · rescues $(field "$p" keepalive_rescues) · a6xx faults $(field "$p" a6xx_faults)"
    echo "  modules : $(field "$p" modules_loaded) loaded · tree $(field "$p" modtree)"
    echo "  panel   : DSI-1 $(field "$p" dsi_status) · $(field "$p" drm_mode | sed 's/^$/no DRM mode/')"
    echo "  gpu     : $(field "$p" gpu | sed 's/^$/no adreno line in dmesg/')"
    echo "  sound   : $(field "$p" sound_cards) card(s)"
}
FLASH_LABEL="$R_FLASH"

case "$MODE" in
status)
    [ -n "$TARGET" ] || usage
    car_gate
    print_status "$(rssh "$(remote_env) sh -s" <<< "$REMOTE_STATUS" 2>/dev/null)"
    ;;

stage)
    [ -n "$TARGET" ] && [ -n "$IMG" ] || usage
    [ -f "$IMG" ] || die "no such file: $IMG"
    info=$(bootimg_info "$IMG")
    [ "$(field "$info" magic)" = ok ] || die "$IMG is not an Android boot.img (the ABL boots nothing else)"
    cmd=$(field "$info" cmdline)
    printf '%s' "$cmd" | grep -q 'msm.context_keepalive=1' || die "$IMG's baked cmdline lacks msm.context_keepalive=1 -- the ABL appends nothing; this is not a GTK boot.img: $cmd"
    [ "$(field "$info" dtbs)" = 9 ] || die "$IMG carries $(field "$info" dtbs) DTBs, the SM8250 stock set is 9"
    HOST_SHA=$(sha256sum "$IMG" | cut -d' ' -f1); KNAME=$(basename "$IMG")
    echo "image     : $KNAME · $(wc -c < "$IMG") B · sha $HOST_SHA"
    echo "  kernel  : $(field "$info" linux | cut -c1-100)"
    echo "  cmdline : $cmd"
    car_gate
    echo "staging   : -> $TARGET:$R_STG/KERNEL.staged"
    rssh "mkdir -p '$R_STG'" || die "cannot create $R_STG on $TARGET"
    scp -q $SSH_OPTS "$IMG" "$TARGET:$R_STG/KERNEL.staged" || die "scp failed"
    out=$(rssh "HOST_SHA='$HOST_SHA' KNAME='$KNAME' $(remote_env) sh -s" <<< "$REMOTE_STAGE" 2>&1)
    printf '%s\n' "$out"
    printf '%s\n' "$out" | grep -q '^SLOT_OK' || die "stage did not complete -- read the lines above; the slot is either untouched or restorable"
    echo
    echo "NEXT: COLD boot the unit on-device (power off, power on; Vol- for the SD if that is how it boots)."
    echo "      Then: tools/abl_slot.sh verify $TARGET $IMG${CAR:+ --car $CAR}"
    echo "      Did not boot: card in the Air -> tools/abl_slot.sh restore --card /run/media/\$USER/ROCKNIX"
    ;;

verify)
    [ -n "$TARGET" ] || usage
    car_gate
    p=$(rssh "$(remote_env) sh -s" <<< "$REMOTE_STATUS" 2>/dev/null)
    print_status "$p"
    echo
    PASS=0; FAIL=0
    ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
    bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
    exp=$(field "$p" expected_sha); slot=$(field "$p" slot_sha)
    if [ -n "$IMG" ]; then
        [ -f "$IMG" ] || die "no such file: $IMG"
        want=$(sha256sum "$IMG" | cut -d' ' -f1)
        [ "$slot" = "$want" ] && ok "slot holds $(basename "$IMG")" || bad "slot sha $slot != $(basename "$IMG") $want"
        winfo=$(bootimg_info "$IMG"); wcmd=$(field "$winfo" cmdline); wlinux=$(field "$winfo" linux)
    else
        [ -n "$exp" ] && { [ "$slot" = "$exp" ] && ok "slot holds the staged $(field "$p" expected_name)" || bad "slot sha != staged sha ($exp)"; }
        wcmd=$(field "$p" slot_cmdline); wlinux=""
    fi
    rcmd=$(field "$p" run_cmdline)
    [ "$rcmd" = "$wcmd" ] && ok "ABL booted the slot: /proc/cmdline == the boot.img's baked cmdline (nothing appended)" \
                          || bad "running cmdline != slot cmdline (booted something else, or the ABL rewrote it): '$rcmd'"
    printf '%s' "$rcmd" | grep -q 'msm.context_keepalive=1' && ok "msm.context_keepalive=1 on the live cmdline" || bad "keepalive NOT on the live cmdline -- anti-lock net #2 is off"
    kp=$(field "$p" keepalive_param); [ -z "$kp" ] || { [ "$kp" = 1 ] && ok "msm.context_keepalive param reads 1" || bad "msm.context_keepalive param reads '$kp'"; }
    if [ -n "$wlinux" ]; then
        rv=$(field "$p" run_version | cut -d'#' -f1); wv=$(printf '%s' "$wlinux" | cut -d'#' -f1)
        [ "$rv" = "$wv" ] && ok "running kernel is the image's build ($(printf '%s' "$wv" | cut -c1-70)…)" || bad "running '$rv' != image '$wv'"
    fi
    [ "$(field "$p" modtree)" = present ] && ok "module tree matches $(field "$p" run_release)" || bad "NO module tree for $(field "$p" run_release) (frankenboot class)"
    [ "$(field "$p" modules_loaded)" -gt 0 ] && ok "$(field "$p" modules_loaded) modules loaded" || bad "zero modules loaded"
    [ "$(field "$p" dsi_status)" = connected ] && ok "panel DSI-1 connected · $(field "$p" drm_mode | cut -c1-40)" || bad "panel DSI-1 not connected"
    [ -n "$(field "$p" gpu)" ] && ok "GPU: $(field "$p" gpu | cut -c1-80)" || bad "no adreno probe line in dmesg"
    [ "$(field "$p" sound_cards)" -gt 0 ] && ok "audio: $(field "$p" sound_cards) sound card(s)" || bad "no sound card (q6afe probe race?)"
    echo
    if [ "$FAIL" = 0 ]; then echo "ABL_SLOT_VERIFY PASS ($PASS checks) -- the GTK boot.img is LIVE on $(field "$p" model)"; exit 0
    else echo "ABL_SLOT_VERIFY FAIL ($FAIL failed, $PASS passed)"; exit 1; fi
    ;;

restore)
    if [ -n "$CARD" ]; then
        [ -d "$CARD" ] || die "$CARD is not a directory (mount the card's boot partition first)"
        [ -f "$CARD/KERNEL.etk-stock" ] || die "no $CARD/KERNEL.etk-stock -- nothing was staged on this card, or it is not the boot partition"
        [ "$(head -c 8 "$CARD/KERNEL.etk-stock")" = "ANDROID!" ] || die "$CARD/KERNEL.etk-stock is not a boot.img"
        cp "$CARD/KERNEL.etk-stock" "$CARD/KERNEL.new" || die "cannot write to $CARD (mounted read-only?)"
        sync
        [ "$(sha256sum "$CARD/KERNEL.new" | cut -d' ' -f1)" = "$(sha256sum "$CARD/KERNEL.etk-stock" | cut -d' ' -f1)" ] || { rm -f "$CARD/KERNEL.new"; die "write read-back mismatch"; }
        mv -f "$CARD/KERNEL.new" "$CARD/KERNEL" && sync
        M=$(md5sum "$CARD/KERNEL" | cut -d' ' -f1); O=$(cut -d' ' -f1 "$CARD/KERNEL.md5" 2>/dev/null)
        echo "SLOT_OK restored on card: $CARD/KERNEL md5 $M · KERNEL.md5 ${O:-absent} · pristine=$( [ -n "$O" ] && [ "$M" = "$O" ] && echo yes || echo UNPROVEN)"
        echo "Unmount the card (sync; umount) and boot the unit."
        exit 0
    fi
    [ -n "$TARGET" ] || usage
    car_gate
    out=$(rssh "$(remote_env) sh -s" <<< "$REMOTE_RESTORE" 2>&1)
    printf '%s\n' "$out"
    printf '%s\n' "$out" | grep -q '^SLOT_OK' || die "restore did not complete"
    echo "NEXT: COLD boot the unit; then tools/abl_slot.sh status $TARGET"
    ;;
*) usage ;;
esac
