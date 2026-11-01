#!/bin/bash
# ==========================================================
# BOOT SLOT — read the OpenABL boot state of a rig (host-side, read-only)
# ==========================================================
# Since ROCKNIX 20261101 the SM8250 boots through OpenABL + Boot Loader Spec entries. The
# kit's set sits beside the OS's own files (bin/bls_deploy.sh is its one writer; plan
# etk-dossiers/BuildPlan_0110_OpenABL_20261030.md §4). This is the instrument panel:
#
#   status [target]              the entries, the default, what booted, the running kernel
#   verify [target] [artifact]   after a COLD boot: is the GTK entry the one that booted, does
#                                /flash match the bundle (and the artifact, if given), and do
#                                the surfaces show it: keepalive, modules, panel, GPU, sound,
#                                mic, boot-logo order, and no drift for osguard to heal
#
#   --car <name>   refuse unless the unit is this car (default: etk.conf CAR=)
#   [target]       ssh target (default: RIG_SSH, then etk.conf RIG_SSH=)
#
# THE ENTRY PROOF: /proc/cmdline == etk-gtk.conf's `options`, whole. OpenABL appends nothing,
# so the line that booted is the entry that booted. It cannot tell etk-stock.conf from
# rocknix.conf (one options line, and no EFI variables here), and the tool says so.
#
# A ROCKNIX-ABL rig (20261001) is tools/abl_slot.sh's. The etched card (verify --card) arrives
# with the BLS image lane (plan P4). Nothing lives on the unit. It writes nothing there
# (bls_deploy.sh --check renders under the bundle and removes it).
# Remote shell is BusyBox POSIX (manual §Q). Harness: tools/test_boot_slot.sh.
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_FLASH="${BS_FLASH:-/flash}"; R_HEAL="${BS_HEAL:-/storage/rocknix-gtk/heal}"
R_PROC="${BS_PROC:-/proc}"; R_SYS="${BS_SYS:-/sys}"; R_MODROOT="${BS_MODROOT:-/usr/lib/modules}"
R_KIT="${BS_KIT:-/storage/games-internal/roms/etk}"

die()   { echo "BOOT_SLOT_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE="${1:-}"; [ -n "$MODE" ] || usage; shift
case "$MODE" in status|verify) ;; -h|--help) usage ;; *) die "unknown mode: $MODE" ;; esac
CAR=""; TARGET=""; ART=""
while [ $# -gt 0 ]; do
    case "$1" in
        --car) CAR="${2:?--car needs a name}"; shift 2 ;;
        -h|--help) usage ;;
        -*) die "unknown option: $1" ;;
        *) if [ -f "$1" ]; then ART="$1"
           elif [ -z "$TARGET" ]; then TARGET="$1"
           else die "too many arguments, or no such artifact: $1"; fi
           shift ;;
    esac
done
if [ -z "$TARGET" ]; then
    TARGET="${RIG_SSH:-}"
    [ -n "$TARGET" ] || [ ! -f "$ETK_ROOT/etk.conf" ] || TARGET=$(sed -n 's/^RIG_SSH="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
    [ -n "$TARGET" ] || die "no target: pass one, or set RIG_SSH in etk.conf"
fi
if [ -z "$CAR" ] && [ -f "$ETK_ROOT/etk.conf" ]; then
    CAR=$(sed -n 's/^CAR="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
fi
if [ -n "$CAR" ] && [ -f "$ETK_ROOT/scripts/etk_car.sh" ]; then
    . "$ETK_ROOT/scripts/etk_car.sh"
    msg=$(etk_car_verify "$TARGET" "$CAR"); rc=$?
    echo "$msg"
    [ $rc -eq 0 ] || die "the unit at $TARGET is not ${CAR}"
fi

field() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1; }
entry() { printf '%s\n' "$1" | sed -n "s/^$2:$3[[:space:]]*//p" | head -n1; }

REMOTE='
E="$FLASH/loader/entries"
echo "magic=$(head -c 8 "$FLASH/KERNEL" 2>/dev/null | tr -d "\000" | head -c 2)"
echo "os_version=$(sed -n "s/^OS_VERSION=\"\{0,1\}\([^\"]*\)\"\{0,1\}/\1/p" "$OSREL" 2>/dev/null)"
echo "layout=$([ -f "$E/rocknix.conf" ] && echo bls || echo none)"
echo "default=$(cat "$FLASH/loader/loader.conf" 2>/dev/null | tr "\n" "|")"
echo "entries=$(ls "$E" 2>/dev/null | tr "\n" " ")"
for n in rocknix etk-stock etk-gtk; do
  [ -f "$E/$n.conf" ] && sed -n "s/^\([a-z-]*\)[[:space:]]\{1,\}\(.*\)/$n:\1 \2/p" "$E/$n.conf"
done
echo "gtk_sha=$(sha256sum "$FLASH/KERNEL.gtk" 2>/dev/null | cut -d" " -f1)"
DT=$(sed -n "s/^devicetree[[:space:]]*//p" "$E/etk-gtk.conf" 2>/dev/null | head -n1)
echo "gtk_dtb=$([ -n "$DT" ] && [ -f "$FLASH$DT" ] && echo present || echo MISSING)"
DT=$(sed -n "s/^devicetree[[:space:]]*//p" "$E/etk-stock.conf" 2>/dev/null | head -n1)
echo "stock_dtb=$([ -n "$DT" ] && [ -f "$FLASH$DT" ] && echo present || echo MISSING)"
echo "chain=$(cat "$HEAL/chain" 2>/dev/null)"
echo "staged_sha=$(cat "$HEAL/KERNEL.staged.sha256" 2>/dev/null)"
echo "staged_name=$(cat "$HEAL/KERNEL.staged.name" 2>/dev/null)"
echo "staged_mode=$(cat "$HEAL/mode" 2>/dev/null)"
echo "extra=$(cat "$HEAL/cmdline_extra" 2>/dev/null)"
echo "run_cmdline=$(cat "$PROC/cmdline" 2>/dev/null)"
echo "run_version=$(cat "$PROC/version" 2>/dev/null)"
echo "run_release=$(uname -r 2>/dev/null)"
echo "model=$(tr -d "\000" < "$PROC/device-tree/model" 2>/dev/null)"
echo "modules_loaded=$(lsmod 2>/dev/null | tail -n +2 | wc -l | tr -d " ")"
echo "modtree=$([ -d "$MODROOT/$(uname -r)" ] && echo present || echo MISSING)"
echo "keepalive_param=$(cat "$SYS/module/msm/parameters/context_keepalive" 2>/dev/null)"
echo "dsi_status=$(cat "$SYS"/class/drm/card*-DSI-1/status 2>/dev/null | head -n1)"
echo "gpu=$(dmesg 2>/dev/null | grep -m1 -E "bound [0-9a-f]+\.gpu|loaded qcom/a[0-9]+_sqe" | sed "s/^\[[^]]*\] //")"
echo "a6xx_faults=$(dmesg 2>/dev/null | grep -c "a6xx_irq.*gpu fault")"
echo "sound_cards=$(grep -c "^ *[0-9]" "$PROC/asound/cards" 2>/dev/null)"
echo "mic_widget=$(tr "\000" "\n" < "$PROC/device-tree/sound/widgets" 2>/dev/null | grep -c "^Internal Mic$")"
SDEV=$(awk "\$2==\"/storage\"{print \$1; exit}" "$PROC/mounts" 2>/dev/null); SDEV=${SDEV##*/}; [ -n "$SDEV" ] || SDEV=mmcblk0p2
echo "boot_root_s=$(dmesg 2>/dev/null | grep -m1 "($SDEV): mounted" | sed "s/^\[ *\([0-9.]*\)\].*/\1/")"
echo "boot_dsi_s=$(dmesg 2>/dev/null | grep -m1 "bound ae94000.dsi" | sed "s/^\[ *\([0-9.]*\)\].*/\1/")"
echo "flash_free_kb=$(df -k "$FLASH" 2>/dev/null | tail -n1 | awk "{print \$4}")"
if [ -f "$KIT/bin/bls_deploy.sh" ]; then
  echo "drift=$(BLS_FLASH="$FLASH" BLS_HEAL="$HEAL" BLS_PROC="$PROC" ETK_ROOT="$KIT" sh "$KIT/bin/bls_deploy.sh" --check 2>&1 | tail -n1)"
else echo "drift=no bls_deploy.sh on the rig"; fi
echo BOOT_REMOTE_OK'

P=$(ssh $SSH_OPTS "$TARGET" "FLASH='$R_FLASH' HEAL='$R_HEAL' PROC='$R_PROC' SYS='$R_SYS' MODROOT='$R_MODROOT' KIT='$R_KIT' OSREL='${BS_OSREL:-/etc/os-release}'; $REMOTE" 2>/dev/null) \
    || die "could not read the unit at $TARGET"
printf '%s\n' "$P" | grep -q '^BOOT_REMOTE_OK$' || die "the read did not complete on $TARGET"

if [ "$(field "$P" magic)" != MZ ] || [ "$(field "$P" layout)" != bls ]; then
    die "this rig is not on OpenABL (no PE KERNEL + rocknix.conf): tools/abl_slot.sh reads the ABL chain"
fi

CMD=$(field "$P" run_cmdline); GTK_OPT=$(entry "$P" etk-gtk options); STOCK_OPT=$(entry "$P" rocknix options)
booted() {
    if [ -n "$GTK_OPT" ] && [ "$CMD" = "$GTK_OPT" ]; then echo etk-gtk
    elif [ -n "$STOCK_OPT" ] && [ "$CMD" = "$STOCK_OPT" ]; then echo "a stock entry (etk-stock or rocknix.conf: one options line, cannot tell which)"
    else echo "UNKNOWN (no entry's options match the cmdline)"; fi
}
logo_verdict() {
    local rs ds; rs=$(field "$P" boot_root_s); ds=$(field "$P" boot_dsi_s)
    [ -n "$rs" ] && [ -n "$ds" ] || { echo unjudged; return; }
    awk "BEGIN{exit !($ds < $rs)}" && echo drawn || echo MISSING
}

if [ "$MODE" = status ]; then
    echo "unit     : $(field "$P" model) · ROCKNIX $(field "$P" os_version)"
    echo "default  : $(field "$P" default | tr '|' ' ' | sed 's/^$/(no loader.conf)/')"
    echo "entries  : $(field "$P" entries)"
    echo "booted   : $(booted)"
    echo "cmdline  : $CMD"
    echo "running  : $(field "$P" run_version | cut -c1-100)"
    echo "KERNEL.gtk: $(field "$P" gtk_sha | cut -c1-12 | sed 's/^$/absent/') · bundle $(field "$P" staged_name | sed 's/^$/none/') $(field "$P" staged_sha | cut -c1-12) (chain=$(field "$P" chain | sed 's/^$/none/'), mode=$(field "$P" staged_mode | sed 's/^$/-/'))"
    echo "modules  : $(field "$P" modules_loaded) loaded · tree $(field "$P" modtree) · keepalive param '$(field "$P" keepalive_param)' · GPU faults this boot $(field "$P" a6xx_faults)"
    echo "panel    : DSI-1 $(field "$P" dsi_status) · mic widget $(field "$P" mic_widget) · boot logo $(logo_verdict)"
    echo "drift    : $(field "$P" drift)"
    echo "/flash   : $(field "$P" flash_free_kb) kB free"
    exit 0
fi

PASS=0; FAIL=0
ok()  { echo "OK  : $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
sha8() { printf '%s' "$1" | cut -c1-12; }

WANT=$(field "$P" staged_sha); GOT=$(field "$P" gtk_sha)
[ "$(field "$P" chain)" = bls ] && ok "heal bundle chain=bls" || bad "heal bundle says chain='$(field "$P" chain)': install.sh has not deployed on this chain"
[ -n "$WANT" ] && [ "$WANT" = "$GOT" ] && ok "KERNEL.gtk == the bundle ($(sha8 "$GOT")…, $(field "$P" staged_name))" || bad "KERNEL.gtk $(sha8 "$GOT")… != bundle $(sha8 "$WANT")…"
if [ -n "$ART" ]; then
    H=$(sha256sum "$ART" | cut -d' ' -f1)
    [ "$H" = "$GOT" ] && ok "KERNEL.gtk IS the artifact $(basename "$ART")" || bad "KERNEL.gtk $(sha8 "$GOT")… is not the artifact $(sha8 "$H")…"
fi
case "$(field "$P" staged_mode)" in
    default) [ "$(field "$P" default)" = "default etk-gtk.conf|" ] && ok "loader.conf names etk-gtk.conf" || bad "mode=default, but loader.conf reads '$(field "$P" default)'" ;;
    *)       [ "$(field "$P" default)" = "default etk-stock.conf|" ] && ok "mode=test: loader.conf names etk-stock.conf" || bad "mode=test, but loader.conf reads '$(field "$P" default)'" ;;
esac
[ "$(field "$P" gtk_dtb)" = present ] && ok "etk-gtk.conf's devicetree exists: $(entry "$P" etk-gtk devicetree)" || bad "etk-gtk.conf names a DTB that is not there"
[ "$(field "$P" stock_dtb)" = present ] && ok "etk-stock.conf's devicetree exists (the floor)" || bad "etk-stock.conf is missing or names a DTB that is not there"
WANT_OPT="$STOCK_OPT $(field "$P" extra)"
[ "$GTK_OPT" = "$WANT_OPT" ] && ok "etk-gtk.conf options = stock's verbatim + the bundle's extra" || bad "etk-gtk.conf options '$GTK_OPT' != '$WANT_OPT'"
case "$(field "$P" drift)" in
    "BLS_DEPLOY_OK wrote=none"*) ok "no drift: osguard has nothing to heal" ;;
    *) bad "drift: $(field "$P" drift)" ;;
esac
if [ "$(field "$P" staged_mode)" = default ]; then
    [ "$CMD" = "$GTK_OPT" ] && ok "booted etk-gtk: /proc/cmdline is its options line, whole" || bad "this boot is not etk-gtk: /proc/cmdline '$CMD'"
    printf '%s' "$CMD" | grep -q 'msm.context_keepalive=1' && ok "keepalive on the cmdline" || bad "no keepalive on the cmdline"
    case "$(field "$P" keepalive_param)" in 1|Y|y) ok "msm.context_keepalive param reads $(field "$P" keepalive_param)" ;; *) bad "msm.context_keepalive param reads '$(field "$P" keepalive_param)'" ;; esac
    field "$P" run_version | grep -q rocknix-gtk && ok "the running kernel is a GTK build" || bad "the running kernel is not a GTK build: $(field "$P" run_version | cut -c1-70)"
    [ "$(field "$P" mic_widget)" -gt 0 ] && ok "Internal Mic in the booted DT" || echo "SKIP: no Internal Mic in the booted DT (ETK_INTERNAL_MIC=0, or the splicer stood down: install's Flip 2 kit DTB line names which)"
fi
[ "$(field "$P" modtree)" = present ] && ok "module tree matches $(field "$P" run_release)" || bad "NO module tree for $(field "$P" run_release)"
[ "$(field "$P" modules_loaded)" -gt 0 ] && ok "$(field "$P" modules_loaded) modules loaded" || bad "zero modules loaded"
[ "$(field "$P" dsi_status)" = connected ] && ok "panel DSI-1 connected" || bad "panel DSI-1 not connected"
[ -n "$(field "$P" gpu)" ] && ok "GPU: $(field "$P" gpu | cut -c1-70)" || bad "no adreno probe line in dmesg"
[ "$(field "$P" sound_cards)" -gt 0 ] && ok "audio: $(field "$P" sound_cards) sound card(s)" || bad "no sound card"
RS=$(field "$P" boot_root_s); DS=$(field "$P" boot_dsi_s)
case "$(logo_verdict)" in
    drawn)   ok "boot logo: msm bound DSI at ${DS}s BEFORE the root mount at ${RS}s" ;;
    MISSING) bad "boot logo MISSING: msm bound DSI at ${DS}s AFTER the root mount at ${RS}s" ;;
    *)       echo "SKIP: boot logo unjudged: dmesg no longer holds the boot lines; judge at a fresh boot" ;;
esac
echo
if [ "$FAIL" = 0 ]; then echo "BOOT_SLOT_VERIFY PASS ($PASS checks) -- $(field "$P" model), booted $(booted)"; exit 0
else echo "BOOT_SLOT_VERIFY FAIL ($FAIL failed, $PASS passed)"; exit 1; fi
