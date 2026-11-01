#!/bin/sh
# ============================================================
# kernel_stage.sh — rig-side GTK kernel staging (GTK 0.8.0)
# ------------------------------------------------------------
# The single entry point for putting a GTK kernel artifact into the osguard
# heal bundle from ON the rig — the piece that makes Pitstop self-update
# full-stack ("we never ship a GTK without its feature set"): a couch update
# stages the kernel here, and bin/osguard.sh activates it on the first boot
# whose OS matches (kernel.requires_os gating happens naturally — Phase B
# only replays when the staged release equals the SYSTEM module tree).
#
# Usage: kernel_stage.sh <artifact_path> <sha256> [deploy_mode]
#   artifact_path  downloaded Image (caller fetched it; we verify + consume)
#   sha256         expected sha256 (from config/gtk_stack.json)
#   deploy_mode    default|test; omitted -> keep existing heal/mode -> default
#
# Also BANKS the live grub block: if the current grub.cfg carries the ETK
# entries (rendered by install.sh STEP 6.4), they are harvested into
# heal/grub.block so osguard can re-insert them after a ROCKNIX update
# strips them. Harvest-at-stage, replay-at-heal — no grub logic lives here.
#
# NEVER touches /flash and NEVER reboots: staging writes only to /storage;
# activation is osguard's job. Exit 0 = staged (activation pending/queued),
# nonzero = nothing staged (3 = card-labels: the boot.img names other partition
# labels than this unit booted from — a GTK card; reflash the next card image).
# BusyBox/POSIX only. Fail-soft.
# Test seams: KS_HEAL, KS_CFG_LIST, KS_NO_ACTIVATE (skip the osguard poke),
# KS_PROC (a fake /proc for the card-labels guard).
# ============================================================
set -u

ART="${1:-}"
WANT="${2:-}"
MODE_ARG="${3:-}"
HEAL="${KS_HEAL:-/storage/rocknix-gtk/heal}"
CFG_LIST="${KS_CFG_LIST:-/flash/EFI/BOOT/grub.cfg /flash/boot/grub/grub.cfg}"
ETK_ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"
TRIP="${TRIPWIRE_LOG:-/storage/etk_tripwire.log}"

log() { echo "[kernel_stage] $*"; echo "[$(date '+%H:%M:%S')] kernel_stage: $*" >> "$TRIP" 2>/dev/null; }
die() { log "FAIL: $*"; exit 1; }

[ -n "$ART" ] && [ -f "$ART" ] || die "no artifact at '$ART'"
[ -n "$WANT" ] || die "no expected sha256 given"

GOT=$(sha256sum "$ART" | cut -d' ' -f1)
[ "$GOT" = "$WANT" ] || die "sha256 mismatch (got $GOT) — artifact discarded, nothing staged"

# ABL era (ROCKNIX 20261001+): the artifact is an Android boot.img — the Image
# inside is gzip'd, so `strings` finds no "Linux version"; the release comes
# from the manifest (gtk_stack.json kernel.kernel_release) via KS_RELEASE, and
# the bundle is marked chain=abl so bin/osguard.sh takes its ABL path: the
# slot (/flash/KERNEL) gets the staged boot.img on the next boot whose module
# tree matches the release — never a raw Image in the ABL's slot.
#
# OpenABL era (ROCKNIX 20261101+): the artifact is the raw PE Image OpenABL boots, so
# `strings` finds the release again and the manifest cross-checks it. chain=bls: osguard
# hands the bundle to bin/bls_deploy.sh on the next boot whose module tree matches. The
# manifest's cmdline_extra and the artifact's own name go in the bundle for the entry.
CHAIN=grub
if [ "$(head -c 2 "$ART" 2>/dev/null)" = "MZ" ]; then
    CHAIN=bls
    REL=$(strings "$ART" 2>/dev/null | grep -m1 "Linux version " \
          | sed 's/.*Linux version \([^ ]*\).*/\1/')
    [ -n "$REL" ] || die "cannot read a kernel release string from the PE Image"
    [ -z "${KS_RELEASE:-}" ] || [ "$KS_RELEASE" = "$REL" ] \
        || die "the Image says release $REL, the manifest says $KS_RELEASE — nothing staged"
elif [ "$(head -c 8 "$ART" 2>/dev/null)" = "ANDROID!" ]; then
    CHAIN=abl
    REL="${KS_RELEASE:-}"
    if [ -z "$REL" ]; then
        # 0.10.0: a 0.9.0 Pitstop's couch update calls this script WITHOUT
        # KS_RELEASE (the knob arrived with the ABL era), so every v0.9.0 rig
        # updating from the couch would have failed its kernel stage. The
        # manifest that named the asset sits beside this script in the update
        # tree (config/gtk_stack.json), and the installed kit keeps a copy —
        # read kernel_release from the first one found. Still never guess.
        for _mf in "$(dirname "$0")/../config/gtk_stack.json" "$ETK_ROOT/config/gtk_stack.json"; do
            [ -f "$_mf" ] || continue
            REL=$(sed -n 's/.*"kernel_release": *"\([^"]*\)".*/\1/p' "$_mf" | head -1)
            [ -n "$REL" ] && { log "KS_RELEASE unset; kernel_release $REL read from $_mf"; break; }
        done
    fi
    [ -n "$REL" ] || die "boot.img artifact needs KS_RELEASE (the manifest's kernel_release) and no gtk_stack.json was found beside the script — nothing staged"
    # CARD-LABELS GUARD (0.11.0; the v0.10.0 Known-behaviour caveat). Under ABL the
    # boot.img's cmdline is baked in, and the GTK CARD IMAGE boots a kernel RELABELLED
    # to the card's own partitions (boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR) while the
    # release asset names ROCKNIX/STORAGE. Staging that asset on a card would bank it in
    # the heal bundle, and osguard's re-stage would put a kernel into the card's slot that
    # mounts an internal ROCKNIX's partitions (split-brain) or no root at all. The live
    # cmdline IS the slot's (the ABL appends nothing), so compare the artifact's baked
    # disk label with the one this unit booted from: they must agree. An artifact with
    # no disk= token is accepted only on a STORAGE unit. Exit 3 = card-labels, so Pitstop
    # can say "reflash the next card image" instead of "staging failed". Same signature
    # as install.sh STEP 6.4's card guard; the relabelled-on-rig path (relabel_bootimg.py
    # in bin/) is the follow-up that would let a card take a couch-update kernel.
    PROC="${KS_PROC:-/proc}"
    LIVE_DISK=$(tr ' ' '\n' < "$PROC/cmdline" 2>/dev/null | sed -n 's/^disk=LABEL=//p' | head -n1)
    ART_DISK=$(head -c 576 "$ART" 2>/dev/null | tail -c 512 | tr -d '\0' | tr ' ' '\n' | sed -n 's/^disk=LABEL=//p' | head -n1)
    if [ -n "$LIVE_DISK" ] && [ "$ART_DISK" != "$LIVE_DISK" ] && { [ "$LIVE_DISK" != "STORAGE" ] || [ -n "$ART_DISK" ]; }; then
        log "FAIL: reason=card-labels — this unit boots disk=LABEL=$LIVE_DISK, the boot.img names disk=LABEL=${ART_DISK:-none}; staging it would hand osguard a kernel for another unit's partitions. Nothing staged$([ "$LIVE_DISK" != STORAGE ] && echo ' -- a GTK card takes its kernel from the next card image (reflash)')."
        exit 3
    fi
else
    REL=$(strings "$ART" 2>/dev/null | grep -m1 "Linux version " \
          | sed 's/.*Linux version \([^ ]*\).*/\1/')
    [ -n "$REL" ] || die "cannot read a kernel release string from the artifact"
fi

mkdir -p "$HEAL" || die "cannot create $HEAL"
# Consume the artifact into the bundle (rename when same fs, copy across).
if ! mv "$ART" "$HEAL/KERNEL.staged" 2>/dev/null; then
    cp "$ART" "$HEAL/KERNEL.staged" || die "cannot place KERNEL.staged"
    rm -f "$ART" 2>/dev/null
fi
printf '%s\n' "$WANT" > "$HEAL/KERNEL.staged.sha256"
printf '%s\n' "$REL"  > "$HEAL/KERNEL.staged.release"
if [ -n "$MODE_ARG" ]; then
    printf '%s\n' "$MODE_ARG" > "$HEAL/mode"
elif [ ! -f "$HEAL/mode" ]; then
    printf 'default\n' > "$HEAL/mode"
fi
printf '%s\n' "$CHAIN" > "$HEAL/chain"
if [ "$CHAIN" = bls ]; then
    printf '%s\n' "${KS_NAME:-$(basename "$ART")}" > "$HEAL/KERNEL.staged.name"
    for _mf in "$(dirname "$0")/../config/gtk_stack.json" "$ETK_ROOT/config/gtk_stack.json"; do
        [ -f "$_mf" ] || continue
        _extra=$(sed -n 's/.*"cmdline_extra": *"\([^"]*\)".*/\1/p' "$_mf" | head -1)
        [ -n "$_extra" ] && { printf '%s\n' "$_extra" > "$HEAL/cmdline_extra"; break; }
    done
    rm -f "$HEAL/grub.block" "$HEAL/grub.block.stockdtb" "$HEAL/DTB.staged" "$HEAL/DTB.staged.sha256" "$HEAL/DTB.base.sha256"
fi
log "staged kernel $REL (sha ok, chain=$CHAIN) -> $HEAL/KERNEL.staged (mode=$(cat "$HEAL/mode"))"

# Bank the live ETK grub block (read-only on /flash; freshest render wins).
# No grub on an ABL or OpenABL unit: the bundle alone drives osguard there.
[ "$CHAIN" = grub ] || CFG_LIST=""
for CFG in $CFG_LIST; do
    [ -f "$CFG" ] || continue
    grep -q "etk-gtk-test" "$CFG" || continue
    awk '
        /menuentry / && (index($0,"etk-gtk") || index($0,"etk-fallback") || index($0,"etk-sdcard")) { inblk=1 }
        /^# etk-sdcard/ || /^set fallback=/ { print; next }
        inblk { print; if ($0 ~ /^}/) inblk=0 }
    ' "$CFG" > "$HEAL/grub.block.tmp"
    if [ -s "$HEAL/grub.block.tmp" ]; then
        mv "$HEAL/grub.block.tmp" "$HEAL/grub.block"
        log "banked live ETK grub block from $CFG ($(grep -c menuentry "$HEAL/grub.block") entries)"
    else
        rm -f "$HEAL/grub.block.tmp"
    fi
    break
done
[ "$CHAIN" != grub ] || [ -f "$HEAL/grub.block" ] || log "NOTE: no ETK grub entries live and none banked — osguard will heal the kernel slot; entries return with the next install.sh"

# Poke osguard: if the running OS already matches this kernel, activation
# happens right now (Phase B) and the user just reboots; if not, the stage
# sits ready and activates on the first coherent boot after the OS update.
if [ "${KS_NO_ACTIVATE:-0}" != "1" ] && [ -x "$ETK_ROOT/bin/osguard.sh" ]; then
    sh "$ETK_ROOT/bin/osguard.sh" >/dev/null 2>&1
    if [ "$(uname -r)" = "$REL" ]; then
        log "OS matches ($REL): osguard activation attempted — REBOOT to load the GTK kernel"
    else
        log "staged for OS matching kernel $REL; current boot is $(uname -r) — activates automatically after the ROCKNIX update"
    fi
fi
exit 0
