#!/bin/bash
# ==========================================================
# ABL SLOT — read the ROCKNIX-ABL era KERNEL slot (host-side, read-only) + card recovery
# ==========================================================
# Since ROCKNIX 20261001 the SM8250 boots through ROCKNIX-ABL: no GRUB, no
# menu, no kernel A/B. The ABL loads exactly one file, /flash/KERNEL, an
# Android boot.img (gzip Image + the 9 device DTBs + the cmdline BAKED IN —
# the ABL appends nothing). THE KIT OWNS THAT SLOT: install.sh STEP 6.4
# (KERNELABLREMOTE) is its only writer and uninstall.sh (ABLRESTORE) puts the
# parked stock back. This tool is the instrument panel around it:
#
#   status  <target>                 what is in the slot and what is RUNNING
#   verify  <target> [<boot.img>]    after the cold boot: did the ABL boot the
#                                    slot, is the keepalive on the cmdline, modules,
#                                    panel, GPU, boot-logo order — the surface the deploy must show on
#   verify  --card <boot-mount> [--storage <stor-mount>] [<boot.img>]
#                                    the ETCHED card, in the Air, before it meets a rig:
#                                    ::/KERNEL is a boot.img whose cmdline names the
#                                    card's labels + keepalive, the relabelled stock is
#                                    parked (KERNEL.md5 names it), and -- given the
#                                    certified boot.img -- the slot IS that artifact
#                                    relabelled (relabel_bootimg.py, derived here, never
#                                    the lane's number); --storage judges the seeded
#                                    heal bundle (chain=abl, staged sha == slot)
#   restore --card <mountpoint>      the unit did not boot: card in the Air, put
#                                    KERNEL.etk-stock back into KERNEL on the mounted
#                                    boot partition (the one thing no rig-side tool
#                                    can do for a unit that is dark)
#
#   options: --car carN   refuse unless the unit reached IS that car (scripts/etk_car.sh)
#
# status/verify never write anything on the unit. Nothing lives on the unit
# (no push-list entry). install.sh banks the expected sha under
# /storage/rocknix-gtk/heal/ (KERNEL.staged.sha256, chain=abl); verify reads it.
#
# Remote shell is BusyBox POSIX (manual §Q): no long options, no bashisms.
# Harness: tools/test_abl_slot.sh (fake ssh sandbox; --against ee353f3 fails).
# Kit path harness: tools/test_kernel_abl.sh. Dossier: rocknix-gtk/UPSTREAM_20261001.md.
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_FLASH="${ABL_FLASH:-/flash}"; R_STG="${ABL_STG:-/storage/rocknix-gtk/heal}"
R_PROC="${ABL_PROC:-/proc}";    R_SYS="${ABL_SYS:-/sys}"; R_MODROOT="${ABL_MODROOT:-/usr/lib/modules}"

die()  { echo "ABL_SLOT_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE="${1:-}"; [ -n "$MODE" ] || usage; shift
CAR=""; CARD=""; STOR=""; TARGET=""; IMG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --car)  CAR="${2:?--car needs carN}"; shift 2 ;;
        --card) CARD="${2:?--card needs a mountpoint}"; shift 2 ;;
        --storage) STOR="${2:?--storage needs a mountpoint}"; shift 2 ;;
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
echo "expected_sha=$([ "$(cat "$STG/chain" 2>/dev/null)" = abl ] && cat "$STG/KERNEL.staged.sha256" 2>/dev/null)"
echo "expected_name=$([ "$(cat "$STG/chain" 2>/dev/null)" = abl ] && cat "$STG/mode" 2>/dev/null | sed "s/^/staged (mode=/; s/$/)/")"
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
echo "boot_root_s=$(dmesg 2>/dev/null | grep -m1 "mmcblk0p2): mounted" | sed "s/^\[ *\([0-9.]*\)\].*/\1/")"
echo "boot_dsi_s=$(dmesg 2>/dev/null | grep -m1 "bound ae94000.dsi" | sed "s/^\[ *\([0-9.]*\)\].*/\1/")"
echo "sbu_mux=$(lsmod 2>/dev/null | grep -q "^gpio_sbu_mux " && echo module || { [ -d "$SYS/bus/platform/drivers/gpio_sbu_mux" ] && echo builtin || echo absent; })"
echo "flash_free_kb=$(df -k "$FLASH" 2>/dev/null | tail -n1 | awk "{print \$4}")"
echo "ABL_REMOTE_OK"
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
    [ -n "$exp" ] && echo "install.sh: $(field "$p" expected_name) sha $(echo "$exp" | cut -c1-12)… $( [ "$exp" = "$(field "$p" slot_sha)" ] && echo "= slot" || echo "!= slot (slot was changed since)")"
    echo "running   : $(field "$p" run_version | cut -c1-110)"
    echo "  cmdline : $(field "$p" run_cmdline)"
    echo "  keepalive: cmdline $(printf '%s' "$(field "$p" run_cmdline)" | grep -q 'msm.context_keepalive=1' && echo on || echo off) · param $(field "$p" keepalive_param | sed 's/^$/absent/') · rescues $(field "$p" keepalive_rescues) · a6xx faults $(field "$p" a6xx_faults)"
    echo "  modules : $(field "$p" modules_loaded) loaded · tree $(field "$p" modtree)"
    echo "  panel   : DSI-1 $(field "$p" dsi_status) · $(field "$p" drm_mode | sed 's/^$/no DRM mode/')"
    echo "  gpu     : $(field "$p" gpu | sed 's/^$/no adreno line in dmesg/')"
    echo "  sound   : $(field "$p" sound_cards) card(s)"
    echo "  boot    : root mounted $(field "$p" boot_root_s | sed 's/^$/?/')s · msm bound DSI $(field "$p" boot_dsi_s | sed 's/^$/?/')s · gpio_sbu_mux $(field "$p" sbu_mux) · logo $(logo_verdict "$p")"
}
# The ROCKNIX splash (init's load_splash, ~2.2 s) draws into /dev/fb0 -- which exists only
# once msm has bound ae94000.dsi. Stock 20261001 binds it at ~3.9 s (gpio-sbu-mux =m defers
# the USB-C connector past switch_root); GTK >= 0.6.3 builds it in (upstream 187eb24f2e).
# Verdict from dmesg ordering: DSI bound BEFORE the root mount = the logo had a framebuffer.
logo_verdict() {   # $1 = remote status output -> drawn | MISSING | unjudged
    local rs ds; rs=$(field "$1" boot_root_s); ds=$(field "$1" boot_dsi_s)
    [ -n "$rs" ] && [ -n "$ds" ] || { echo unjudged; return; }
    awk "BEGIN{exit !($ds < $rs)}" && echo drawn || echo MISSING
}
FLASH_LABEL="$R_FLASH"

# ---- the etched card, in the Air (no rig involved) ----------------------------
# The image lane verified the raw image inside the build container; this judges the
# COPY that dd made, on the card that will boot. Same derivation as the lane: the
# expected slot is the certified boot.img relabelled ROCKNIX->ROCKNIX-GTK,
# STORAGE->GTKSTOR by relabel_bootimg.py (only the 512-byte cmdline field moves).
CARD_BOOT_LABEL="${ABL_CARD_BOOT_LABEL:-ROCKNIX-GTK}"; CARD_STOR_LABEL="${ABL_CARD_STOR_LABEL:-GTKSTOR}"
RELABEL="$ETK_ROOT/os-install/build/relabel_bootimg.py"
card_verify() {
    # no rig in card mode: the one positional is the boot.img, not a target
    [ -n "$IMG" ] || { IMG="$TARGET"; TARGET=""; }
    [ -d "$CARD" ] || die "$CARD is not a directory (mount the card's boot partition first)"
    [ -z "$STOR" ] || [ -d "$STOR" ] || die "$STOR is not a directory (mount the card's storage partition first)"
    PASS=0; FAIL=0
    ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
    bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
    # the label is judged only when $CARD is itself a mountpoint (a subdirectory of some
    # other filesystem -- the harness sandbox -- would report that filesystem's label)
    local lbl=""; if [ "$(findmnt -n -o TARGET --target "$CARD" 2>/dev/null)" = "$(realpath "$CARD")" ]; then
        lbl=$(lsblk -no LABEL "$(findmnt -n -o SOURCE --target "$CARD" 2>/dev/null)" 2>/dev/null | head -n1); fi
    echo "card      : $CARD$( [ -n "$lbl" ] && echo " (label $lbl)")$( [ -n "$STOR" ] && echo " · storage $STOR")"
    [ -f "$CARD/KERNEL" ] || { bad "no $CARD/KERNEL -- not the boot partition, or the dd did not land"; echo "ABL_SLOT_CARD FAIL ($FAIL failed, $PASS passed)"; exit 1; }
    [ -z "$lbl" ] || { [ "$lbl" = "$CARD_BOOT_LABEL" ] && ok "boot partition label $lbl" || bad "boot partition label '$lbl' != $CARD_BOOT_LABEL (a stock-labelled card collides with an internal ROCKNIX)"; }
    local info; info=$(bootimg_info "$CARD/KERNEL")
    [ "$(field "$info" magic)" = ok ] && ok "::/KERNEL is a boot.img ($(field "$info" dtbs) DTBs, $(wc -c < "$CARD/KERNEL" | tr -d ' ') B)" || bad "::/KERNEL is not a boot.img"
    local cmd slot; cmd=$(field "$info" cmdline); slot=$(sha256sum "$CARD/KERNEL" | cut -d' ' -f1)
    echo "  cmdline : $cmd"
    printf '%s' "$cmd" | grep -q "boot=LABEL=$CARD_BOOT_LABEL disk=LABEL=$CARD_STOR_LABEL" && ok "slot cmdline names the card's labels ($CARD_BOOT_LABEL/$CARD_STOR_LABEL)" || bad "slot cmdline does NOT name the card's labels -- this kernel would mount an internal ROCKNIX (split-brain): '$cmd'"
    printf '%s' "$cmd" | grep -q 'msm.context_keepalive=1' && ok "msm.context_keepalive=1 baked in the slot cmdline" || bad "keepalive NOT in the slot cmdline"
    if [ -f "$CARD/KERNEL.etk-stock" ]; then
        [ "$(head -c 8 "$CARD/KERNEL.etk-stock")" = "ANDROID!" ] && ok "KERNEL.etk-stock parked (a boot.img)" || bad "KERNEL.etk-stock is not a boot.img"
        local m o; m=$(md5sum "$CARD/KERNEL.etk-stock" | cut -d' ' -f1); o=$(cut -d' ' -f1 "$CARD/KERNEL.md5" 2>/dev/null)
        [ -n "$o" ] && { [ "$m" = "$o" ] && ok "KERNEL.md5 names the parked stock (uninstall/osguard can prove it pristine)" || bad "KERNEL.md5 ($o) != parked stock md5 ($m)"; }
        local scmd; scmd=$(field "$(bootimg_info "$CARD/KERNEL.etk-stock")" cmdline)
        printf '%s' "$scmd" | grep -q "boot=LABEL=$CARD_BOOT_LABEL disk=LABEL=$CARD_STOR_LABEL" && ok "parked stock is relabelled too (a fallback that boots THIS card)" || bad "parked stock still names the stock labels -- a fallback that would mount an internal ROCKNIX: '$scmd'"
    else bad "no KERNEL.etk-stock parked -- the slot has no fallback"; fi
    if [ -n "$IMG" ]; then
        [ -f "$IMG" ] || die "no such file: $IMG"
        [ -f "$RELABEL" ] || die "relabel tool missing: $RELABEL"
        local tmp; tmp=$(mktemp); 
        if python3 -I "$RELABEL" "$IMG" "$tmp" ROCKNIX STORAGE "$CARD_BOOT_LABEL" "$CARD_STOR_LABEL" >/dev/null 2>&1; then
            local want; want=$(sha256sum "$tmp" | cut -d' ' -f1)
            [ "$slot" = "$want" ] && ok "slot == $(basename "$IMG") relabelled to the card (sha $(echo "$want" | cut -c1-12)…)" || bad "slot sha $slot != $(basename "$IMG") relabelled ($want) -- wrong kernel on the card, or a bad dd"
        else bad "could not relabel $(basename "$IMG") for comparison (not the stock-labelled certified artifact?)"; fi
        rm -f "$tmp"
    else echo "NOTE: no boot.img given -- the slot is unjudged against the certified artifact (pass ~/rocknix-gtk/artifacts/<KNAME>)"; fi
    if [ -n "$STOR" ]; then
        local h="$STOR/rocknix-gtk/heal"
        if [ -d "$h" ]; then
            [ "$(cat "$h/chain" 2>/dev/null)" = abl ] && ok "heal bundle seeded: chain=abl" || bad "heal bundle chain is '$(cat "$h/chain" 2>/dev/null)', not abl"
            local st; st=$(cat "$h/KERNEL.staged.sha256" 2>/dev/null | cut -d' ' -f1)
            [ "$st" = "$slot" ] && ok "heal bundle's staged sha == the slot (osguard can re-stage after an OS update)" || bad "heal bundle staged sha ($st) != slot ($slot)"
            [ -n "$(cat "$h/KERNEL.staged.release" 2>/dev/null)" ] && ok "heal bundle names the module tree ($(cat "$h/KERNEL.staged.release"))" || bad "heal bundle has no KERNEL.staged.release (osguard's re-stage gate would refuse)"
        else bad "no heal bundle at $h (a card-born install could not self-heal an OS-update revert)"; fi
    fi
    echo
    if [ "$FAIL" = 0 ]; then echo "ABL_SLOT_CARD PASS ($PASS checks) -- the card is ready for the rig"; exit 0
    else echo "ABL_SLOT_CARD FAIL ($FAIL failed, $PASS passed)"; exit 1; fi
}

case "$MODE" in
status)
    [ -n "$TARGET" ] || usage
    car_gate
    print_status "$(rssh "$(remote_env) sh -s" <<< "$REMOTE_STATUS" 2>/dev/null)"
    ;;

verify)
    [ -n "$CARD" ] && card_verify
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
        [ -n "$exp" ] && { [ "$slot" = "$exp" ] && ok "slot holds what install.sh staged ($(field "$p" expected_name))" || bad "slot sha != install.sh's staged sha ($exp) -- an OS update or hand swap replaced it"; }
        wcmd=$(field "$p" slot_cmdline); wlinux=""
    fi
    rcmd=$(field "$p" run_cmdline)
    [ "$rcmd" = "$wcmd" ] && ok "ABL booted the slot: /proc/cmdline == the boot.img's baked cmdline (nothing appended)" \
                          || bad "running cmdline != slot cmdline (booted something else, or the ABL rewrote it): '$rcmd'"
    printf '%s' "$rcmd" | grep -q 'msm.context_keepalive=1' && ok "msm.context_keepalive=1 on the live cmdline" || bad "keepalive NOT on the live cmdline -- anti-lock net #2 is off"
    # bool module params print Y/N via sysfs (seen live on car12 2026-10-08), ints print 1/0
    kp=$(field "$p" keepalive_param); [ -z "$kp" ] || { case "$kp" in 1|Y|y) ok "msm.context_keepalive param reads $kp" ;; *) bad "msm.context_keepalive param reads '$kp'" ;; esac; }
    if [ -n "$wlinux" ]; then
        rv=$(field "$p" run_version | cut -d'#' -f1); wv=$(printf '%s' "$wlinux" | cut -d'#' -f1)
        [ "$rv" = "$wv" ] && ok "running kernel is the image's build ($(printf '%s' "$wv" | cut -c1-70)…)" || bad "running '$rv' != image '$wv'"
    fi
    [ "$(field "$p" modtree)" = present ] && ok "module tree matches $(field "$p" run_release)" || bad "NO module tree for $(field "$p" run_release) (frankenboot class)"
    [ "$(field "$p" modules_loaded)" -gt 0 ] && ok "$(field "$p" modules_loaded) modules loaded" || bad "zero modules loaded"
    [ "$(field "$p" dsi_status)" = connected ] && ok "panel DSI-1 connected · $(field "$p" drm_mode | cut -c1-40)" || bad "panel DSI-1 not connected"
    [ -n "$(field "$p" gpu)" ] && ok "GPU: $(field "$p" gpu | cut -c1-80)" || bad "no adreno probe line in dmesg"
    [ "$(field "$p" sound_cards)" -gt 0 ] && ok "audio: $(field "$p" sound_cards) sound card(s)" || bad "no sound card (q6afe probe race?)"
    rs=$(field "$p" boot_root_s); ds=$(field "$p" boot_dsi_s); sbu=$(field "$p" sbu_mux)
    case "$(logo_verdict "$p")" in
        drawn)   ok "boot logo: msm bound ae94000.dsi at ${ds}s BEFORE the root mount at ${rs}s (load_splash had a /dev/fb0; gpio_sbu_mux $sbu)" ;;
        MISSING) bad "boot logo MISSING: msm bound ae94000.dsi at ${ds}s AFTER the root mount at ${rs}s -- load_splash drew into a /dev/fb0 that did not exist yet (gpio_sbu_mux $sbu; needs CONFIG_TYPEC_MUX_GPIO_SBU=y, GTK >= 0.6.3)" ;;
        *)       echo "SKIP: boot logo unjudged -- dmesg no longer holds the boot lines (ring rolled); judge at a fresh boot" ;;
    esac
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
    die "restore over ssh is the kit's job: ./uninstall.sh puts the parked stock back (ABLRESTORE). This tool restores only a dark unit's card (--card <mountpoint>)."
    ;;
*) usage ;;
esac
