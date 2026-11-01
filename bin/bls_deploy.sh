#!/bin/sh
# ============================================================
# bls_deploy.sh — the ONE writer of the ETK set on an OpenABL rig (ETK 0.11.0)
# ------------------------------------------------------------
# ROCKNIX 20261101+ boots through OpenABL + Boot Loader Spec entries. The OS owns
# /flash/KERNEL, loader/entries/rocknix.conf and dtbs/*.dtb. The kit lives BESIDE them:
#
#   KERNEL.gtk                       the staged GTK kernel, byte-identical to the bundle
#   dtbs/etk/<model>.dtb             the OS's own DTB + the kit's deltas, derived HERE
#   loader/entries/etk-stock.conf    stock /KERNEL + the stock DTB named explicitly: the floor
#   loader/entries/etk-gtk.conf      /KERNEL.gtk + the kit DTB + stock's options + our extra
#   loader/loader.conf               one line: default etk-gtk.conf (or etk-stock.conf)
#
# Every caller goes through this script, so there is one render: install.sh STEP 6.4
# (KERNELBLSREMOTE fills the heal bundle, then calls it) and bin/osguard.sh (after an OS
# update moved the stock options or DTB; after a couch update staged a new kernel).
#
# It derives everything from the bundle and the live OS, compares with /flash, and writes
# only what differs. loader.conf goes LAST: the default never names a payload that did not
# verify. KERNEL and rocknix.conf are hashed before and after and are never written.
#
# Inputs, under $HEAL (/storage/rocknix-gtk/heal):
#   KERNEL.staged + .sha256   the kernel; refused unless the sha holds and it is a PE image
#   KERNEL.staged.name        the artifact's name, the entry's `version` line
#   cmdline_extra             appended to stock's options (absent: the 0.11.0 default)
#   mode                      default = etk-gtk boots; test = everything staged, stock boots
#
# Usage: bls_deploy.sh           render, compare, write what differs
#        bls_deploy.sh --check   compare only, writes nothing
# Last line: BLS_DEPLOY_OK wrote=<a,b|none> default=<entry> dtb=<verdict> kernel=<sha>
#            BLS_DEPLOY_DRIFT <a,b>        (--check: exit 2)
#            BLS_DEPLOY_FAIL <reason>      (exit 1, /flash left as it was or at the last
#                                           verified step; the default is written last)
#
# NEVER reboots. BusyBox/POSIX only. Kill-switches in etk.conf: ETK_INTERNAL_MIC=0 and
# ETK_SD_POLL=1 reach the splicer, as they did at mint in the boot.img era.
# Test seams (tools/test_kernel_bls.sh): BLS_FLASH BLS_PROC BLS_HEAL BLS_SPLICER BLS_NO_REMOUNT.
# ============================================================
set -u

ETK_ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"
# etk.conf is read, not sourced: under set -u on ash a stray bashism in it would end the run.
knob() { sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}.*/\1/p" "$ETK_ROOT/etk.conf" 2>/dev/null | tail -n1; }
MIC="${ETK_INTERNAL_MIC:-$(knob ETK_INTERNAL_MIC)}"
SDPOLL="${ETK_SD_POLL:-$(knob ETK_SD_POLL)}"

FLASH="${BLS_FLASH:-/flash}"
PROC="${BLS_PROC:-/proc}"
HEAL="${BLS_HEAL:-/storage/rocknix-gtk/heal}"
SPLICER="${BLS_SPLICER:-$ETK_ROOT/bin/etk_dtb_mic.py}"
NO_REMOUNT="${BLS_NO_REMOUNT:-${OSG_NO_REMOUNT:-0}}"
EXTRA_DEFAULT='msm.context_keepalive=1 panic=30'

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

E="$FLASH/loader/entries"
LC="$FLASH/loader/loader.conf"
T="$HEAL/.bls-render"
RW=0

ro_flash() {
    [ "$RW" = 1 ] || return 0
    sync
    [ "$NO_REMOUNT" = 1 ] || mount -o remount,ro "$FLASH" 2>/dev/null
    RW=0
}
cleanup() { rm -rf "$T" 2>/dev/null; rm -f "$FLASH/KERNEL.gtk.new" "$LC.new" 2>/dev/null; ro_flash; }
fail() { cleanup; echo "BLS_DEPLOY_FAIL $*"; exit 1; }
sha()  { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
same() { [ -f "$2" ] && [ "$(sha "$1")" = "$(sha "$2")" ]; }

# ---- is this rig ours to write? ------------------------------------------------------------
[ "$(head -c 2 "$FLASH/KERNEL" 2>/dev/null)" = MZ ] || fail "KERNEL is not a PE image: not an OpenABL rig"
[ -f "$E/rocknix.conf" ] || fail "no loader/entries/rocknix.conf: not an OpenABL rig"
for f in "$E"/etk-gtk+*.conf; do
    [ -f "$f" ] && fail "a boot-count trial is in place (${f##*/}): run tools/bls_trial.sh restore first"
done
[ -f "$FLASH/KERNEL.gtk.aside" ] && fail "a fallthrough trial is in place: run tools/bls_trial.sh restore first"
case "$(cat "$LC" 2>/dev/null)" in
    ""|"default etk-stock.conf"|"default etk-gtk.conf") ;;
    *) fail "loader.conf is not ours; refusing to take over that line" ;;
esac

WANT=$(cat "$HEAL/KERNEL.staged.sha256" 2>/dev/null)
[ -n "$WANT" ] || fail "no staged kernel in the heal bundle"
[ "$(sha "$HEAL/KERNEL.staged")" = "$WANT" ] || fail "staged kernel fails its sha256"
[ "$(head -c 2 "$HEAL/KERNEL.staged" 2>/dev/null)" = MZ ] || fail "staged kernel is not a PE image (a boot.img cannot boot here)"

# The running model names the OS's DTB file. Never guess: a wrong tree is a dark panel.
MODEL=$(tr -d '\000' < "$PROC/device-tree/model" 2>/dev/null)
case "$MODEL" in
    "Retroid Pocket Flip2 Visionox") DTB=sm8250-retroidpocket-flip2-visionox.dtb ;;
    "Retroid Pocket Flip2")          DTB=sm8250-retroidpocket-flip2.dtb ;;
    *) fail "unknown model '$MODEL': no Flip 2 DTB to name" ;;
esac
[ -f "$FLASH/dtbs/$DTB" ] || fail "the OS ships no dtbs/$DTB"

OPTIONS=$(sed -n 's/^options[[:space:]]*//p' "$E/rocknix.conf" | head -n1)
[ -n "$OPTIONS" ] || fail "rocknix.conf has no options line"
VERSION=$(sed -n 's/^version[[:space:]]*//p' "$E/rocknix.conf" | head -n1)
EXTRA=$(cat "$HEAL/cmdline_extra" 2>/dev/null); [ -n "$EXTRA" ] || EXTRA="$EXTRA_DEFAULT"
NAME=$(cat "$HEAL/KERNEL.staged.name" 2>/dev/null); [ -n "$NAME" ] || NAME=etk-gtk
MODE=$(cat "$HEAL/mode" 2>/dev/null); [ -n "$MODE" ] || MODE=default
case "$MODE" in default) DEFAULT=etk-gtk.conf ;; *) DEFAULT=etk-stock.conf ;; esac

# ---- render into the bundle, never onto /flash ----------------------------------------------
rm -rf "$T"; mkdir -p "$T" || fail "cannot create $T"
printf 'title      ROCKNIX (stock kernel, ETK DTB line)\nversion    %s\nlinux      /KERNEL\ndevicetree /dtbs/%s\noptions    %s\nsort-key   etk-stock\n' \
    "${VERSION:-etk}" "$DTB" "$OPTIONS" > "$T/etk-stock.conf"
printf 'title      ROCKNIX-GTK\nversion    %s\nlinux      /KERNEL.gtk\ndevicetree /dtbs/etk/%s\noptions    %s %s\n' \
    "$NAME" "$DTB" "$OPTIONS" "$EXTRA" > "$T/etk-gtk.conf"
printf 'default %s\n' "$DEFAULT" > "$T/loader.conf"

# The kit DTB tracks the OS's own tree. If the splicer stands down or is not sure, the GTK
# entry gets the stock tree: the mic stays off and the reason is named, never a half-state.
FLAGS=""
[ "${MIC:-1}" = 1 ] || FLAGS="--no-mic"
[ "${SDPOLL:-0}" = 1 ] && FLAGS="$FLAGS --sd-poll"
DOUT=""
[ -f "$SPLICER" ] && command -v python3 >/dev/null 2>&1 && \
    DOUT=$(python3 "$SPLICER" derive $FLAGS "$FLASH/dtbs/$DTB" "$T/kit.dtb" 2>&1)
case "$DOUT" in
    DTB_MIC_OK*)
        DV=$(printf '%s' "$DOUT" | sed -n 's/.* mic=\([^ ]*\).*/\1/p')
        [ "$DV" = applied ] && DV=mic || DV="kit(mic=${DV:-?})" ;;
    DTB_MIC_SKIP*) cp "$FLASH/dtbs/$DTB" "$T/kit.dtb" || fail "cannot copy the stock DTB"
                   DV="stock($(printf '%s' "$DOUT" | awk '{print $2}'))" ;;
    *)             cp "$FLASH/dtbs/$DTB" "$T/kit.dtb" || fail "cannot copy the stock DTB"
                   DV="stock(derive-failed)" ;;
esac

# ---- compare: write only what differs ------------------------------------------------------
DRIFT=""
add() { DRIFT="${DRIFT:+$DRIFT,}$1"; }
[ "$(sha "$FLASH/KERNEL.gtk")" = "$WANT" ]        || add KERNEL.gtk
same "$T/kit.dtb" "$FLASH/dtbs/etk/$DTB"          || add dtb
same "$T/etk-stock.conf" "$E/etk-stock.conf"      || add etk-stock.conf
same "$T/etk-gtk.conf" "$E/etk-gtk.conf"          || add etk-gtk.conf
same "$T/loader.conf" "$LC"                       || add loader.conf
LEGACY=no
[ "$(head -c 8 "$FLASH/KERNEL.etk-stock" 2>/dev/null)" = "ANDROID!" ] && LEGACY=yes

if [ -z "$DRIFT" ] && [ "$LEGACY" = no ]; then
    rm -rf "$T"
    echo "BLS_DEPLOY_OK wrote=none default=$DEFAULT dtb=$DV kernel=$WANT"
    exit 0
fi
if [ "$CHECK" = 1 ]; then
    rm -rf "$T"
    echo "BLS_DEPLOY_DRIFT ${DRIFT:-legacy-KERNEL.etk-stock}"
    exit 2
fi

# ---- write: payload, then entries, then the default ----------------------------------------
BEFORE="$(sha "$FLASH/KERNEL") $(sha "$E/rocknix.conf")"
if [ "$NO_REMOUNT" != 1 ]; then
    mount -o remount,rw "$FLASH" || fail "cannot remount $FLASH rw"
fi
RW=1

# The ABL-era parked boot.img cannot boot on this chain; it only holds /flash space.
[ "$LEGACY" = yes ] && rm -f "$FLASH/KERNEL.etk-stock"

case ",$DRIFT," in *,KERNEL.gtk,*)
    NEED=$(( $(wc -c < "$HEAL/KERNEL.staged") / 1024 + 2048 ))
    FREE=$(df -k "$FLASH" | tail -n1 | awk '{print $4}')
    [ "${FREE:-0}" -gt "$NEED" ] || fail "$FLASH has ${FREE:-0} kB free, need $NEED kB"
    cp "$HEAL/KERNEL.staged" "$FLASH/KERNEL.gtk.new" || fail "cannot write KERNEL.gtk.new"
    sync
    [ "$(sha "$FLASH/KERNEL.gtk.new")" = "$WANT" ] || fail "KERNEL.gtk read-back mismatch"
    mv -f "$FLASH/KERNEL.gtk.new" "$FLASH/KERNEL.gtk" || fail "cannot move KERNEL.gtk into place"
    sync
    [ "$(sha "$FLASH/KERNEL.gtk")" = "$WANT" ] || fail "KERNEL.gtk read-back mismatch after the move" ;;
esac
case ",$DRIFT," in *,dtb,*)
    mkdir -p "$FLASH/dtbs/etk" || fail "cannot create dtbs/etk"
    cp "$T/kit.dtb" "$FLASH/dtbs/etk/$DTB" || fail "cannot write the kit DTB"
    sync
    same "$T/kit.dtb" "$FLASH/dtbs/etk/$DTB" || fail "kit DTB read-back mismatch" ;;
esac
for c in etk-stock.conf etk-gtk.conf; do
    case ",$DRIFT," in *,$c,*)
        cp "$T/$c" "$E/$c" || fail "cannot write $c"
        sync
        same "$T/$c" "$E/$c" || fail "$c read-back mismatch" ;;
    esac
done
case ",$DRIFT," in *,loader.conf,*)
    cp "$T/loader.conf" "$LC.new" && mv -f "$LC.new" "$LC" || fail "cannot write loader.conf"
    sync
    same "$T/loader.conf" "$LC" || fail "loader.conf read-back mismatch" ;;
esac

AFTER="$(sha "$FLASH/KERNEL") $(sha "$E/rocknix.conf")"
[ "$BEFORE" = "$AFTER" ] || fail "KERNEL or rocknix.conf CHANGED during the deploy"
rm -rf "$T"
ro_flash
echo "BLS_DEPLOY_OK wrote=${DRIFT:-legacy} default=$DEFAULT dtb=$DV kernel=$WANT"
exit 0
