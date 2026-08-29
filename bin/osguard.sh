#!/bin/sh
# ============================================================
# ETK OS-UPDATE COHERENCE GUARD (osguard) — GTK 0.8.0
# ------------------------------------------------------------
# WHY (live incident 2026-08-01, ROCKNIX 20260701 -> 20260801): the ROCKNIX
# in-place updater writes the new kernel over WHATEVER /flash file the
# running boot used (BOOT_IMAGE=...), regenerates BOTH grub twins (stripping
# the ETK entries) and re-seeds grubenv (observed seeded to the WRONG device
# id, 'rp5' on a Flip 2). On a rig auto-booting the GTK kernel the update
# therefore lands the new stock kernel in KERNEL.gtktest while every
# regenerated grub entry still boots the OLD /flash/KERNEL — the next boot
# is an old-kernel/new-SYSTEM frankenboot with ZERO loadable modules
# (no WiFi, no sound, failed squashfs mounts).
#
# This oneshot runs once per boot and repairs exactly that, generalized
# for every future OS bump:
#
# PHASE A — mismatch heal (runs on the broken boot):
#   running kernel release != the SYSTEM's module tree -> find a donor
#   kernel matching the module tree among the /flash slots (the install.sh-
#   staged GTK artifact is preferred, sha-verified), promote it to
#   /flash/KERNEL (the slot every regenerated stock entry boots), refresh
#   KERNEL.etk-stock when the donor is a stock build, re-point grubenv at
#   THIS device's entry (confidence-gated), notify. Next reboot is coherent.
#
# PHASE B — GTK re-activation (runs on the coherent boot after):
#   the staged GTK kernel matches the running SYSTEM but is not live in
#   KERNEL.gtktest / grub -> replay the heal bundle rendered by install.sh
#   STEP 6.4 (kernel copy, pre-rendered grub block, grubenv seed per the
#   recorded deploy mode) PLUS the numeric default pin recomputed here — the
#   ONE piece install.sh cannot pre-render, because its index depends on the
#   healed cfg (string-id defaults don't resolve on the 20260901 generator and
#   grubenv is inert on the 4Kn internal ESP, so the numeric pin is the only
#   default mechanism that survives). Otherwise osguard replays what install.sh
#   rendered; it never re-derives the menuentries.
#
# LAWS: NEVER reboots (the operator/driver reboots). Fail-soft: every write
# is guarded and /flash is remounted ro on all exit paths; a failed heal
# leaves the rig no worse than it booted. BusyBox/POSIX only.
#
# Modes:   (none) = check + heal    --check = report only, no writes
#          --dry-run = print planned actions, no writes
# Kill-switch: ETK_OS_GUARD=0 in etk.conf.
# Test seams (tools/test_osguard.sh): OSG_FLASH, OSG_MOD_BASE, OSG_RUN_REL,
#   OSG_HEAL, OSG_DT, OSG_GRUBENV_LIST — every probe overridable so the
#   discrimination fixtures can replay the real incident end to end.
# ============================================================

# env.sh is bash; this guard must run under plain sh even when env.sh is
# unavailable (early boot). Source only etk.conf for the kill-switch knob.
ETK_ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"
[ -f "$ETK_ROOT/etk.conf" ] && . "$ETK_ROOT/etk.conf" 2>/dev/null
[ "${ETK_OS_GUARD:-1}" = "0" ] && exit 0

FLASH="${OSG_FLASH:-/flash}"
MOD_BASE="${OSG_MOD_BASE:-/usr/lib/kernel-overlays/base/lib/modules}"
RUN_REL="${OSG_RUN_REL:-$(uname -r)}"
HEAL="${OSG_HEAL:-/storage/rocknix-gtk/heal}"
DT="${OSG_DT:-/sys/firmware/devicetree/base/compatible}"
TRIP="${TRIPWIRE_LOG:-/storage/etk_tripwire.log}"
MARKER="${OSG_MARKER:-/storage/.etk-osguard-last}"
BACKUP_DIR="${OSG_BACKUP_DIR:-/storage}"

MODE="heal"
case "${1:-}" in
    --check)   MODE="check" ;;
    --dry-run) MODE="dry" ;;
esac

RW=0
log() { echo "[osguard] $*"; echo "[$(date '+%H:%M:%S')] osguard: $*" >> "$TRIP" 2>/dev/null; }
act() {  # act <description> <cmd...> — honors check/dry modes, logs, fail-soft
    DESC="$1"; shift
    if [ "$MODE" != "heal" ]; then log "PLAN: $DESC"; return 0; fi
    if "$@" 2>/dev/null; then log "DONE: $DESC"; return 0
    else log "FAIL: $DESC (continuing fail-soft)"; return 1; fi
}
flash_rw() {
    [ "$MODE" != "heal" ] && return 0
    [ "$RW" = "1" ] && return 0
    # Test seam: fixtures are plain dirs, not mountpoints.
    [ "${OSG_NO_REMOUNT:-0}" = "1" ] && { RW=1; return 0; }
    mount -o remount,rw "$FLASH" 2>/dev/null && RW=1
}
flash_ro() {
    [ "${OSG_NO_REMOUNT:-0}" = "1" ] && { RW=0; return 0; }
    [ "$RW" = "1" ] && { sync; mount -o remount,ro "$FLASH" 2>/dev/null; RW=0; }
}
finish() { flash_ro; exit "${1:-0}"; }

notify() {
    # Marker always (Pitstop/operator-visible); mako toast best-effort in a
    # detached retry loop — mako may not be up yet at oneshot time. Fail-silent.
    # 15s rather than the 10s house default: these are recovery instructions,
    # and they fire during a boot the operator is already puzzled by.
    printf '%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" > "$MARKER" 2>/dev/null
    [ "$MODE" != "heal" ] && return 0
    (
        i=0
        while [ $i -lt 12 ]; do
            if [ -x "$ETK_ROOT/bin/etk_notify.sh" ] && \
               XDG_RUNTIME_DIR=/var/run/0-runtime-dir \
               "$ETK_ROOT/bin/etk_notify.sh" "ETK OS GUARD" "$1" 15000 \
                >/dev/null 2>&1; then break; fi
            i=$((i + 1)); sleep 5
        done
    ) >/dev/null 2>&1 &
}

# kernel release embedded in an Image file ("Linux version 7.1.2 (...)")
kver() {
    [ -f "$1" ] || { echo ""; return; }
    strings "$1" 2>/dev/null | grep -m1 "Linux version " \
        | sed 's/.*Linux version \([^ ]*\).*/\1/'
}
# GTK build marker: our kernels carry KBUILD_BUILD_HOST=rocknix-gtk
is_gtk_build() { strings "$1" 2>/dev/null | grep -m1 "Linux version " | grep -q "rocknix-gtk"; }

# --- what does the SYSTEM expect? (single module dir ships per image) ----
SYS_REL=""
for d in "$MOD_BASE"/*; do
    [ -d "$d" ] && { SYS_REL=$(basename "$d"); break; }
done
if [ -z "$SYS_REL" ]; then
    log "cannot read SYSTEM module tree ($MOD_BASE) — nothing to validate"
    finish 0
fi

# ==========================================================
# PHASE A — running kernel does not match the SYSTEM
# ==========================================================
if [ "$RUN_REL" != "$SYS_REL" ]; then
    log "MISMATCH: running $RUN_REL, SYSTEM ships modules for $SYS_REL (OS update trap)"

    # Donor hunt: staged GTK artifact first (sha-verified), then /flash slots.
    DONOR=""
    if [ -f "$HEAL/KERNEL.staged" ] && [ -f "$HEAL/KERNEL.staged.release" ]; then
        if [ "$(cat "$HEAL/KERNEL.staged.release" 2>/dev/null)" = "$SYS_REL" ]; then
            WANT=$(cat "$HEAL/KERNEL.staged.sha256" 2>/dev/null)
            GOT=$(sha256sum "$HEAL/KERNEL.staged" 2>/dev/null | cut -d' ' -f1)
            if [ -n "$WANT" ] && [ "$WANT" = "$GOT" ]; then
                DONOR="$HEAL/KERNEL.staged"
                log "donor: staged GTK kernel ($SYS_REL, sha verified)"
            else
                log "staged GTK kernel FAILED sha check — ignoring it"
            fi
        fi
    fi
    if [ -z "$DONOR" ]; then
        for K in "$FLASH/KERNEL.gtktest" "$FLASH/KERNEL.etk-stock" "$FLASH/KERNEL"; do
            V=$(kver "$K")
            [ "$V" = "$SYS_REL" ] && { DONOR="$K"; log "donor: $K ($V)"; break; }
        done
    fi
    if [ -z "$DONOR" ]; then
        log "NO donor kernel for $SYS_REL on this rig — cannot heal (reflash or install.sh needed)"
        notify "OS updated but no matching kernel found on /flash. Re-run the ETK installer from your computer."
        finish 1
    fi

    flash_rw || { log "cannot remount $FLASH rw — heal aborted"; notify "OS update mismatch detected but /flash is locked. Re-run the ETK installer."; finish 1; }

    # Preserve whatever /flash/KERNEL holds before we overwrite it (it may be
    # the only copy of an old GTK build; keep exactly one backup), then
    # promote the donor. Promote also when KERNEL is missing entirely.
    if [ "$DONOR" != "$FLASH/KERNEL" ]; then
        [ -f "$FLASH/KERNEL" ] && act "back up displaced /flash/KERNEL -> $BACKUP_DIR/KERNEL.osguard-displaced" \
            cp "$FLASH/KERNEL" "$BACKUP_DIR/KERNEL.osguard-displaced"
        act "promote donor $DONOR -> $FLASH/KERNEL" cp "$DONOR" "$FLASH/KERNEL"
    fi

    # A stock donor is also the correct new pristine-stock snapshot (the old
    # snapshot is the PREVIOUS OS's kernel — stale the moment the OS moved).
    if ! is_gtk_build "$DONOR"; then
        act "refresh $FLASH/KERNEL.etk-stock from stock donor" cp "$DONOR" "$FLASH/KERNEL.etk-stock"
    fi

    # grubenv: the updater may have seeded a WRONG device id. Re-point at this
    # device, confidence-gated: only when exactly one menuentry id from the
    # regenerated cfg is a substring of the live devicetree compatible.
    COMPAT=$(tr '\0' ' ' < "$DT" 2>/dev/null)
    CFG_IDS=""
    for CFG in "$FLASH/EFI/BOOT/grub.cfg" "$FLASH/boot/grub/grub.cfg"; do
        [ -f "$CFG" ] || continue
        CFG_IDS=$(grep "menuentry " "$CFG" | grep -o "'[a-z0-9]*'" | tr -d "'" | sort -u)
        break
    done
    DEV_ID=""; N_MATCH=0
    for ID in $CFG_IDS; do
        case "$ID" in *recovery*) continue ;; esac
        case "$COMPAT" in *"$ID"*) DEV_ID="$ID"; N_MATCH=$((N_MATCH + 1)) ;; esac
    done
    if [ "$N_MATCH" = "1" ] && [ -n "$DEV_ID" ]; then
        for GE in ${OSG_GRUBENV_LIST:-$FLASH/EFI/BOOT/grubenv $FLASH/boot/grub/grubenv}; do
            [ -f "$GE" ] || continue
            if [ "$MODE" = "heal" ]; then
                printf '# GRUB Environment Block\nsaved_entry=%s\n' "$DEV_ID" > "$GE.tmp"
                N=$(wc -c < "$GE.tmp")
                dd if=/dev/zero bs=1 count=$((1024 - N)) 2>/dev/null | tr '\0' '#' >> "$GE.tmp"
                mv "$GE.tmp" "$GE" && log "DONE: grubenv $GE -> saved_entry=$DEV_ID"
            else
                log "PLAN: grubenv $GE -> saved_entry=$DEV_ID"
            fi
        done
    else
        log "device id not confidently resolvable (matches=$N_MATCH) — leaving grubenv alone (grub falls back to first entry, kernel is now correct)"
    fi

    notify "OS update repaired: kernel $SYS_REL restored to the boot slot. REBOOT once to finish."
    log "PHASE A complete — reboot required (guard never reboots)"
    # heal mode: 0 (repaired). check/dry: 2 = findings present, nothing written.
    [ "$MODE" = "heal" ] && finish 0 || finish 2
fi

# ==========================================================
# PHASE B — coherent boot; is the staged GTK kernel live?
# ==========================================================
[ -d "$HEAL" ] || finish 0
[ "$(cat "$HEAL/KERNEL.staged.release" 2>/dev/null)" = "$SYS_REL" ] || finish 0
WANT=$(cat "$HEAL/KERNEL.staged.sha256" 2>/dev/null)
[ -n "$WANT" ] || finish 0
GOT_STAGED=$(sha256sum "$HEAL/KERNEL.staged" 2>/dev/null | cut -d' ' -f1)
[ "$GOT_STAGED" = "$WANT" ] || { log "staged GTK kernel sha mismatch — not replaying"; finish 0; }

CHANGED=0
GT_SHA=$(sha256sum "$FLASH/KERNEL.gtktest" 2>/dev/null | cut -d' ' -f1)
if [ "$GT_SHA" != "$WANT" ]; then
    flash_rw || { log "cannot remount $FLASH rw for Phase B"; finish 0; }
    act "restore GTK kernel -> $FLASH/KERNEL.gtktest" cp "$HEAL/KERNEL.staged" "$FLASH/KERNEL.gtktest" && CHANGED=1
fi
if [ -f "$HEAL/grub.block" ]; then
    for CFG in "$FLASH/EFI/BOOT/grub.cfg" "$FLASH/boot/grub/grub.cfg"; do
        [ -f "$CFG" ] || continue
        grep -q "etk-gtk-test" "$CFG" && continue
        flash_rw || continue
        if [ "$MODE" = "heal" ]; then
            awk -v bf="$HEAL/grub.block" '
                !ins && /^menuentry / { while ((getline line < bf) > 0) print line; close(bf); ins=1 }
                { print }
                END { if (!ins) { while ((getline line < bf) > 0) print line } }
            ' "$CFG" > "$CFG.osguard" && mv "$CFG.osguard" "$CFG" && log "DONE: ETK grub entries re-inserted into $CFG" && CHANGED=1
        else
            log "PLAN: re-insert ETK grub entries into $CFG"; CHANGED=1
        fi
    done
fi
# NUMERIC DEFAULT PIN (STEP 6.4 parity — the one piece of grub logic that MUST
# live here too). The banked grub.block carries only menuentries; STEP 6.4's
# decisive default is a NUMERIC `set default=<idx>` injected AFTER the abl block
# (string-id defaults do NOT resolve on the 20260901 generator, and grubenv is
# INERT on the internal 4Kn ESP), and it is NOT part of the banked block. Without
# re-applying it, a heal leaves the default riding the FRAGILE entry-0 alignment.
# The index is cfg-dependent so it cannot be pre-rendered — recompute it here and
# pin the deploy mode's target (etk-gtk-test in default, the device entry in test),
# idempotently (skip when the tail default already resolves to it). Same transform
# as install.sh STEP 6.4 (harness tools/test_grub_default.sh covers both).
DMODE=$(cat "$HEAL/mode" 2>/dev/null)
PIN_TGT="rpflip2"; [ "$DMODE" = "default" ] && PIN_TGT="etk-gtk-test"
for CFG in "$FLASH/EFI/BOOT/grub.cfg" "$FLASH/boot/grub/grub.cfg"; do
    [ -f "$CFG" ] || continue
    grep -q "'$PIN_TGT' {" "$CFG" 2>/dev/null || continue
    PIN_IDX=$(awk -v q="'$PIN_TGT' {" '/^menuentry /{ if (index($0,q)) { print n+0; exit } n++ }' "$CFG")
    printf '%s' "$PIN_IDX" | grep -Eq '^[0-9]+$' || continue
    [ "$(grep '^set default=' "$CFG" 2>/dev/null | tail -1 | sed 's/^set default=//')" = "$PIN_IDX" ] && continue
    if [ "$MODE" = "heal" ]; then
        flash_rw || continue
        awk -v idx="$PIN_IDX" '
            /feature_menuentry_id/ && !ins {
                print "# ETK: pin default NUMERICALLY (osguard heal; string-ids do not";
                print "# resolve on this grub -- the abl block would fall to entry 0).";
                print "set default=" idx;
                print "set timeout=2";
                print "";
                ins=1
            }
            { print }
            END { if (!ins) { print "set default=" idx; print "set timeout=2" } }
        ' "$CFG" > "$CFG.osgpin" && mv "$CFG.osgpin" "$CFG" \
            && log "DONE: numeric default pin -> $PIN_IDX ($PIN_TGT) in $CFG" && CHANGED=1
    else
        log "PLAN: numeric default pin -> $PIN_IDX ($PIN_TGT) in $CFG"; CHANGED=1
    fi
done
if [ "$(cat "$HEAL/mode" 2>/dev/null)" = "default" ] && [ "$CHANGED" = "1" ]; then
    for GE in ${OSG_GRUBENV_LIST:-$FLASH/EFI/BOOT/grubenv $FLASH/boot/grub/grubenv}; do
        [ -f "$GE" ] || continue
        grep -q "saved_entry=etk-gtk-test" "$GE" 2>/dev/null && continue
        if [ "$MODE" = "heal" ]; then
            flash_rw || continue
            printf '# GRUB Environment Block\nsaved_entry=etk-gtk-test\n' > "$GE.tmp"
            N=$(wc -c < "$GE.tmp")
            dd if=/dev/zero bs=1 count=$((1024 - N)) 2>/dev/null | tr '\0' '#' >> "$GE.tmp"
            mv "$GE.tmp" "$GE" && log "DONE: grubenv $GE -> saved_entry=etk-gtk-test (deploy mode: default)"
        else
            log "PLAN: grubenv $GE -> saved_entry=etk-gtk-test"
        fi
    done
fi

if [ "$CHANGED" = "1" ]; then
    notify "GTK kernel restored for the updated OS. REBOOT once to re-enable ANTI-LOCK."
    log "PHASE B complete — reboot required (guard never reboots)"
    [ "$MODE" = "heal" ] && finish 0 || finish 2
else
    log "coherent: running $RUN_REL == SYSTEM $SYS_REL, GTK kernel live/staged correctly"
fi
finish 0
