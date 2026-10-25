#!/bin/bash
# ==========================================================
# ETK VPU FIRMWARE -- wake the iris H.264/HEVC encoder (install.sh STEP 6.86)
# ==========================================================
# Kernel 7.2.0's qcom-iris driver (platform device aa00000.video-codec) asks for
# qcom/vpu/vpu20_p4.mbn. ROCKNIX ships the same SM8250 VPU 2.0 firmware only under the
# venus driver's name (/lib/firmware/qcom/vpu-1.0/venus.mbn, 1,980,084 bytes), so core
# init fails at boot and the encoder node (qcom-iris-encoder, /dev/video1 on car8)
# answers every open with ENOMEM. Proven live on car8 2026-10-25 (dossier
# Onboard_LiveMirror_20261025.md §3 S0 RESULT): copy the OS's own blob to
# /storage/etk-fw/qcom/vpu/vpu20_p4.mbn, point firmware_class.path at /storage/etk-fw,
# unbind + bind qcom-iris -> the encoder opens and encodes (H.264 + HEVC).
# firmware_class.path is volatile (empty after every boot) and the root is read-only,
# so etk-vpu-fw.service runs `apply` at every boot. Nothing is downloaded: the blob is
# the one the OS already ships, and it is seeded ONLY when its sha256 is the pinned one
# (the STEP 6.76 UCM pattern: act only when the stock file is the one we know).
#
#   apply    what the unit runs. ETK_VPU_FW=0 -> nothing. Encoder already opens ->
#            "already up", no rebind. Else: seed the blob (sha-gated), set the path,
#            unbind/bind qcom-iris ONCE, wait <= 3 s, re-check. Never loops, never
#            reboots, never touches another driver. Log: /storage/etk_vpu_fw.log
#   verify   one line; exit 0 when the encoder node opens.
#   status   human lines (knob, pin, blobs, path, driver, node).
#   undo     clear firmware_class.path if it is ours (or restore the value apply
#            displaced), remove /storage/etk-fw. The encoder stays up until the next
#            boot (no rebind); it is dark from then on.
#
# firmware_class.path is ONE directory, not a list. If it already holds a value that
# is not ours, apply logs it, saves it to $FW_DIR/.displaced_path and sets ours anyway
# (the encoder is the feature this unit exists for; the stock /lib/firmware search
# still follows the custom path, so only a second custom directory loses); undo puts
# the displaced value back.
#
# Test seams (tools/test_vpu_fw.sh): VPU_SYSFS_ROOT (prefix for /sys), VPU_DEV_DIR,
# VPU_FW_DIR, VPU_STOCK_FW, VPU_LOG, VPU_NODE (pin the node), VPU_PROBE (a command
# run as `$VPU_PROBE <node>` instead of the built-in open), VPU_WAIT_S, ETK_VPU_ENV
# (the env.sh to source; default ../scripts/env.sh beside this script).
# BusyBox userland: no GNU-only flags.
# ==========================================================

# PM: pin before validation. sha256 of the stock /lib/firmware/qcom/vpu-1.0/venus.mbn
# (1,980,084 bytes on ROCKNIX 20261001, the blob S0 proved). On the car:
#   sha256sum /lib/firmware/qcom/vpu-1.0/venus.mbn
# Until this is 64 hex characters, apply refuses to seed (and says so in the log).
VPU_FW_SHA256="UNPINNED-sha256-of-stock-venus.mbn"

ENV_SH="${ETK_VPU_ENV:-$(dirname "$0")/../scripts/env.sh}"
# shellcheck disable=SC1090
[ -r "$ENV_SH" ] && . "$ENV_SH" >/dev/null 2>&1
ETK_VPU_FW="${ETK_VPU_FW:-1}"

ROOT="${VPU_SYSFS_ROOT:-}"
DEVICE="aa00000.video-codec"
DRV="$ROOT/sys/bus/platform/drivers/qcom-iris"
PLATDEV="$ROOT/sys/bus/platform/devices/$DEVICE"
FWPARAM="$ROOT/sys/module/firmware_class/parameters/path"
V4L="$ROOT/sys/class/video4linux"
DEVDIR="${VPU_DEV_DIR:-/dev}"
FW_DIR="${VPU_FW_DIR:-/storage/etk-fw}"
FW_BLOB="$FW_DIR/qcom/vpu/vpu20_p4.mbn"
DISPLACED="$FW_DIR/.displaced_path"
STOCK="${VPU_STOCK_FW:-/lib/firmware/qcom/vpu-1.0/venus.mbn}"
LOG="${VPU_LOG:-/storage/etk_vpu_fw.log}"
WAIT_S="${VPU_WAIT_S:-3}"
OPEN_ERR=""

log() {
    echo "$(date '+%F %T') [$(cut -d' ' -f1 /proc/uptime 2>/dev/null)s] $*" >> "$LOG" 2>/dev/null
    echo "[etk-vpu-fw] $*"
}
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
pin_ok() {
    [ "${#VPU_FW_SHA256}" -eq 64 ] || return 1
    case "$VPU_FW_SHA256" in *[!0-9a-f]*) return 1 ;; esac
    return 0
}

# One sysfs write, logged in the order it happens. No trailing newline (S0's `echo -n`):
# an older firmware_class keeps a newline as part of the path.
sysw() {
    if printf '%s' "$2" > "$1" 2>/dev/null; then log "write ${1#"$ROOT"} <- '$2'"; return 0; fi
    log "write ${1#"$ROOT"} <- '$2' FAILED"; return 1
}
# Clearing the path needs a byte: an empty write never reaches the kernel. firmware_class
# strips the newline, leaving the path empty (= stock search only).
clear_path() {
    if echo > "$FWPARAM" 2>/dev/null; then log "write ${FWPARAM#"$ROOT"} <- '' (cleared)"; return 0; fi
    log "write ${FWPARAM#"$ROOT"} <- '' FAILED"; return 1
}

# The encoder node, found by its v4l2 name (numbering can move across a rebind).
find_node() {
    if [ -n "${VPU_NODE:-}" ]; then echo "$VPU_NODE"; return; fi
    for n in "$V4L"/video*/name; do
        [ -r "$n" ] || continue
        case "$(cat "$n" 2>/dev/null)" in
            *iris*enc*|*Iris*Enc*) d=${n%/name}; echo "$DEVDIR/${d##*/}"; return ;;
        esac
    done
}

# Opening the node is the test (S0: a dead core answers ENOMEM). OPEN_ERR names why not.
node_opens() {
    OPEN_ERR=""
    [ -n "$1" ] || { OPEN_ERR="no qcom-iris-encoder node"; return 1; }
    if [ -n "${VPU_PROBE:-}" ]; then
        OPEN_ERR=$($VPU_PROBE "$1" 2>&1); return
    fi
    [ -e "$1" ] || { OPEN_ERR="$1 absent"; return 1; }
    if command -v python3 >/dev/null 2>&1; then
        OPEN_ERR=$(python3 -c 'import os, sys
try:
    os.close(os.open(sys.argv[1], os.O_RDWR))
except OSError as e:
    print(e.strerror)
    sys.exit(1)' "$1" 2>&1)
    elif command -v v4l2-ctl >/dev/null 2>&1; then
        out=$(v4l2-ctl -d "$1" --info 2>&1) && return 0
        OPEN_ERR=$(printf '%s\n' "$out" | tail -n1); return 1
    else
        OPEN_ERR="neither python3 nor v4l2-ctl to open it"; return 1
    fi
}

do_apply() {
    if [ "$ETK_VPU_FW" != 1 ]; then
        echo "[etk-vpu-fw] ETK_VPU_FW=$ETK_VPU_FW: kill-switch, nothing done"; return 0
    fi
    [ -e "$PLATDEV" ] || { log "no $DEVICE on this board: nothing to do"; return 0; }
    [ -d "$DRV" ] || { log "qcom-iris driver not loaded: nothing to do"; return 0; }
    node=$(find_node)
    if node_opens "$node"; then log "already up: $node opens (no rebind)"; return 0; fi
    log "encoder ${node:-node} does not open (${OPEN_ERR:-?}): waking it"

    # Gate: seed only the blob we know.
    if ! pin_ok; then
        log "REFUSED: VPU_FW_SHA256 is not pinned in $0 ('$VPU_FW_SHA256') - nothing seeded, no rebind"; return 0
    fi
    [ -f "$STOCK" ] || { log "REFUSED: stock firmware $STOCK missing - nothing seeded, no rebind"; return 0; }
    s=$(sha "$STOCK")
    if [ "$s" != "$VPU_FW_SHA256" ]; then
        log "REFUSED: stock $STOCK sha256 $s != pinned $VPU_FW_SHA256 (OS update?) - nothing seeded, no rebind; check the new blob, re-pin, reinstall"
        return 0
    fi
    if [ "$(sha "$FW_BLOB")" = "$VPU_FW_SHA256" ]; then
        log "staged blob current: $FW_BLOB"
    else
        mkdir -p "${FW_BLOB%/*}" && cp "$STOCK" "$FW_BLOB.tmp" && mv "$FW_BLOB.tmp" "$FW_BLOB"
        if [ "$(sha "$FW_BLOB")" != "$VPU_FW_SHA256" ]; then
            rm -f "$FW_BLOB" "$FW_BLOB.tmp"; log "SEED FAILED: $FW_BLOB did not copy intact (storage full?)"; return 1
        fi
        log "seeded $FW_BLOB from $STOCK (sha256 pinned, match)"
    fi

    cur=$(cat "$FWPARAM" 2>/dev/null)
    if [ "$cur" != "$FW_DIR" ]; then
        if [ -n "$cur" ]; then
            printf '%s\n' "$cur" > "$DISPLACED"
            log "firmware_class.path held '$cur' (not ours): replaced (ONE directory only), saved for undo"
        fi
        sysw "$FWPARAM" "$FW_DIR" || return 1
    fi

    # One rebind of this one device on this one driver.
    if [ -e "$DRV/$DEVICE" ]; then sysw "$DRV/unbind" "$DEVICE"
    else log "$DEVICE not bound to qcom-iris: bind only"; fi
    sysw "$DRV/bind" "$DEVICE"
    i=0
    while :; do
        node=$(find_node)
        if node_opens "$node"; then log "WOKE: $node opens after the rebind (${i}s)"; return 0; fi
        [ "$i" -ge "$WAIT_S" ] && break
        sleep 1; i=$((i + 1))
    done
    k=""
    [ -z "$ROOT" ] && k=$(dmesg 2>/dev/null | grep -i iris | tail -n1 | cut -c1-120)
    log "WAKE FAILED: ${node:-no encoder node} still does not open after the rebind (${OPEN_ERR:-?})${k:+; kmsg: $k}"
    return 1
}

do_verify() {
    node=$(find_node)
    if node_opens "$node"; then echo "VPU verify OK: $node opens (iris encoder up)"; return 0; fi
    echo "VPU verify FAIL: ${node:-no encoder node} does not open (${OPEN_ERR:-?}); last log: $(tail -n1 "$LOG" 2>/dev/null | cut -c1-120)"
    return 1
}

do_status() {
    echo "knob      ETK_VPU_FW=$ETK_VPU_FW"
    if pin_ok; then echo "pin       $VPU_FW_SHA256"; else echo "pin       NOT PINNED ('$VPU_FW_SHA256') - apply refuses to seed"; fi
    if [ -f "$STOCK" ]; then
        s=$(sha "$STOCK"); [ "$s" = "$VPU_FW_SHA256" ] && m=match || m=MISMATCH
        echo "stock     $STOCK $(wc -c < "$STOCK" | tr -d ' ') B sha256 $s ($m)"
    else echo "stock     $STOCK missing"; fi
    if [ -f "$FW_BLOB" ]; then
        s=$(sha "$FW_BLOB"); [ "$s" = "$VPU_FW_SHA256" ] && m=match || m=MISMATCH
        echo "staged    $FW_BLOB sha256 $s ($m)"
    else echo "staged    $FW_BLOB absent"; fi
    echo "fw path   firmware_class.path='$(cat "$FWPARAM" 2>/dev/null)'$([ -s "$DISPLACED" ] && echo " (displaced: $(cat "$DISPLACED"))")"
    if [ -e "$DRV/$DEVICE" ]; then echo "driver    $DEVICE bound to qcom-iris"
    elif [ -d "$DRV" ]; then echo "driver    qcom-iris loaded, $DEVICE NOT bound"
    else echo "driver    qcom-iris not loaded"; fi
    node=$(find_node)
    if node_opens "$node"; then echo "encoder   $node opens"; else echo "encoder   ${node:-no node}: does not open (${OPEN_ERR:-?})"; fi
    echo "last log  $(tail -n1 "$LOG" 2>/dev/null)"
    return 0
}

do_undo() {
    case "$FW_DIR" in ""|/) echo "[etk-vpu-fw] refusing undo: FW_DIR='$FW_DIR'"; return 1 ;; esac
    cur=$(cat "$FWPARAM" 2>/dev/null)
    if [ "$cur" = "$FW_DIR" ]; then
        prev=$(cat "$DISPLACED" 2>/dev/null)
        if [ -n "$prev" ]; then sysw "$FWPARAM" "$prev"; else clear_path; fi
    elif [ -n "$cur" ]; then
        log "undo: firmware_class.path is '$cur' (not ours): left alone"
    fi
    rm -rf "$FW_DIR"
    log "undo: removed $FW_DIR (the encoder stays up until the next boot; dark after it)"
    return 0
}

case "${1:-}" in
    apply)  do_apply ;;
    verify) do_verify ;;
    status) do_status ;;
    undo)   do_undo ;;
    *) echo "usage: $0 apply|verify|status|undo"; exit 2 ;;
esac
