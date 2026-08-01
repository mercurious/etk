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
# nonzero = nothing staged. BusyBox/POSIX only. Fail-soft.
# Test seams: KS_HEAL, KS_CFG_LIST, KS_NO_ACTIVATE (skip the osguard poke).
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

REL=$(strings "$ART" 2>/dev/null | grep -m1 "Linux version " \
      | sed 's/.*Linux version \([^ ]*\).*/\1/')
[ -n "$REL" ] || die "cannot read a kernel release string from the artifact"

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
log "staged kernel $REL (sha ok) -> $HEAL/KERNEL.staged (mode=$(cat "$HEAL/mode"))"

# Bank the live ETK grub block (read-only on /flash; freshest render wins).
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
[ -f "$HEAL/grub.block" ] || log "NOTE: no ETK grub entries live and none banked — osguard will heal the kernel slot; entries return with the next install.sh"

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
