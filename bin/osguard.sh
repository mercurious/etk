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
#   OSG_HEAL, OSG_DT, OSG_GRUBENV_LIST, OSG_CMDLINE, OSG_BLS_DEPLOY — every probe overridable so the
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
CMDLINE="${OSG_CMDLINE:-/proc/cmdline}"
BLS_DEPLOY="${OSG_BLS_DEPLOY:-$ETK_ROOT/bin/bls_deploy.sh}"
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
# OPENABL UNIT (ROCKNIX 20261101+) — 2026-11-01
# ==========================================================
# OpenABL boots BLS entries. The OS owns KERNEL, rocknix.conf and dtbs/*.dtb; the kit lives
# beside them (bin/bls_deploy.sh, the one writer) and an OS update removes nothing of ours
# (updater copies only), so this guard has three jobs and never touches the OS's files:
#   - RELEASE GATE: a staged kernel for another module tree must not stay the default. The
#     default goes back to etk-stock, which is the only write this guard does itself: the
#     one line it owns. Named once, by the toast at the flip.
#   - COHERENCE: an OS update moves the stock options and DTB; a couch update stages a new
#     kernel. bls_deploy.sh --check says what drifted, and bls_deploy.sh writes it.
#   - JUDGE: all in place = silent. A stock boot with everything in place is a menu pick or
#     a fallthrough, so it is logged and not toasted: a toast here is a claim the operator
#     acts on, and the ABL branch's "not active" toast was false on a live GTK boot.
# Every OpenABL layout ends here. The GRUB phases below must never run on one: Phase B would
# drop a KERNEL.gtktest nothing boots. chain=abl (a bundle from before the rig updated) goes
# on to the ABL block, which names the move. Harness: tools/test_kernel_bls.sh.
# THE BOOT IDENTITY LINE. install.sh and the image lane render it into a unit; a couch update
# runs the OLD kit's Pitstop and re-renders nothing, so the line kept the previous version and
# edition (car12, 0.10.1 -> 0.11.0, 2026-11-01). This script is replaced by that update and
# runs every boot, so it keeps the rendered text true, from the kit's own version and the
# bundle's kernel name. It takes effect on the next boot.
sync_identity() {
    ID_UNIT="${OSG_ID_UNIT:-/storage/.config/system.d/etk-gtk-version.service}"
    ID_APP="$ETK_ROOT/bin/etk_pitstop.py"
    ID_NAME=$(cat "$HEAL/KERNEL.staged.name" 2>/dev/null)
    [ -f "$ID_UNIT" ] && [ -f "$ID_APP" ] && [ -n "$ID_NAME" ] || return 0
    grep -q 'echo "ROCKNIX-GTK ' "$ID_UNIT" || return 0
    ID_VER=$(sed -n 's/^APP_VERSION = "\([^"]*\)".*/\1/p' "$ID_APP" | head -n1)
    ID_ED=$(sed -n 's/^APP_EDITION = "\([^"]*\)".*/\1/p' "$ID_APP" | head -n1)
    ID_DATE=$(printf '%s' "$ID_NAME" | grep -oE '[0-9]{8}' | head -n1)
    ID_KVER=$(printf '%s' "$ID_NAME" | sed -n 's/.*-[0-9]\{8\}-\([0-9.]*\)$/\1/p')
    [ -n "$ID_VER" ] && [ -n "$ID_DATE" ] && [ -n "$ID_KVER" ] || return 0
    ID_LINE="ROCKNIX-GTK $ID_VER-$ID_DATE${ID_ED:+ -- $ID_ED Edition} -- kernel $ID_KVER"
    grep -qF "echo \"$ID_LINE\"" "$ID_UNIT" && return 0
    if [ "$MODE" != "heal" ]; then log "PLAN: boot identity line -> $ID_LINE"; return 0; fi
    if sed "s|echo \"ROCKNIX-GTK [^\"]*\"|echo \"$ID_LINE\"|" "$ID_UNIT" > "$ID_UNIT.tmp" \
       && mv -f "$ID_UNIT.tmp" "$ID_UNIT"; then
        log "DONE: boot identity line -> $ID_LINE (from the next boot)"
    else
        rm -f "$ID_UNIT.tmp"
    fi
}
if [ -d "$FLASH/loader/entries" ] && [ "$(head -c 2 "$FLASH/KERNEL" 2>/dev/null)" = "MZ" ]; then
    case "$(cat "$HEAL/chain" 2>/dev/null)" in
    abl) ;;
    bls)
        sync_identity
        STAGED_REL=$(cat "$HEAL/KERNEL.staged.release" 2>/dev/null)
        LC="$FLASH/loader/loader.conf"
        if [ -z "$STAGED_REL" ] || [ "$STAGED_REL" != "$SYS_REL" ]; then
            log "bls chain: staged GTK kernel is ${STAGED_REL:-unknown}, this OS ships modules for $SYS_REL"
            if [ "$(cat "$LC" 2>/dev/null)" != "default etk-gtk.conf" ]; then
                log "bls chain: default is not etk-gtk; nothing to flip"
                finish 0
            fi
            if [ "$MODE" != "heal" ]; then
                log "PLAN: loader.conf -> default etk-stock.conf"
                finish 2
            fi
            flash_rw || { log "bls chain: cannot remount $FLASH rw — default left on etk-gtk"; notify "OS updated to a new kernel ($SYS_REL). Hold Volume Down at power-on and pick the stock entry, then re-run the ETK installer."; finish 0; }
            if printf 'default etk-stock.conf\n' > "$LC.new" && mv -f "$LC.new" "$LC" && sync; then
                log "DONE: default -> etk-stock.conf (a kernel with no module tree would boot to a module-less system)"
                notify "OS updated to a new kernel ($SYS_REL). The ETK kernel for it is not installed yet, so the stock kernel boots next. Re-run the ETK installer when the new build ships."
            else
                rm -f "$LC.new"
                log "FAIL: could not rewrite loader.conf"
            fi
            finish 0
        fi
        if [ ! -f "$BLS_DEPLOY" ]; then
            log "bls chain: $BLS_DEPLOY is missing; nothing judged"
            finish 0
        fi
        CHK=$(BLS_FLASH="$FLASH" BLS_HEAL="$HEAL" sh "$BLS_DEPLOY" --check 2>&1); RC=$?
        CHK=$(printf '%s\n' "$CHK" | tail -n1)
        case "$RC" in
        0)
            if grep -q 'msm.context_keepalive=1' "$CMDLINE" 2>/dev/null; then
                log "bls chain: the GTK entry is in place and this boot runs it"
            else
                log "bls chain: the GTK set is in place; this boot ran the stock kernel (menu pick, fallthrough, or mode=test)"
            fi
            finish 0 ;;
        2)
            log "bls chain: $CHK"
            if [ "$MODE" != "heal" ]; then
                log "PLAN: bin/bls_deploy.sh"
                finish 2
            fi
            OUT=$(BLS_FLASH="$FLASH" BLS_HEAL="$HEAL" sh "$BLS_DEPLOY" 2>&1 | tail -n1)
            case "$OUT" in
            BLS_DEPLOY_OK*)
                log "DONE: $OUT"
                notify "ETK kernel entry refreshed. REBOOT once to run it."
                ;;
            *)
                log "FAIL: $OUT"
                notify "The ETK kernel entry could not be refreshed. Re-run the ETK installer from your computer."
                ;;
            esac
            finish 0 ;;
        *)
            log "bls chain: $CHK"
            notify "The ETK kernel entry needs attention: ${CHK#BLS_DEPLOY_FAIL }. Re-run the ETK installer from your computer."
            [ "$MODE" = "heal" ] && finish 0 || finish 2 ;;
        esac
        ;;
    *)
        log "bls chain: no GTK kernel in the heal bundle — nothing to guard"
        finish 0 ;;
    esac
fi

# A couch update staged a PE kernel on a rig that has not taken the ROCKNIX update yet. It
# cannot boot here and it does not have to: the block above deploys it on the first boot
# after the update. The boot.img in the slot keeps running until then. Quiet on purpose.
if [ "$(cat "$HEAL/chain" 2>/dev/null)" = "bls" ]; then
    log "bls chain: a GTK kernel for OpenABL is staged; it deploys after the ROCKNIX update"
    finish 0
fi

# ==========================================================
# ABL-ERA UNIT (ROCKNIX 20261001+, ROCKNIX-ABL on SM8250) — 2026-10-08
# ==========================================================
# No GRUB: the ABL boots the ONE file $FLASH/KERNEL (a boot.img, cmdline baked).
# Every phase below edits grub twins / grubenv / KERNEL.gtktest — none of which
# exist here; left to run, Phase B would drop a stray KERNEL.gtktest on /flash
# and announce "GTK kernel restored" every boot (UPSTREAM_20261001.md §1b).
# The slot is install.sh STEP 6.4's (KERNELABLREMOTE). This guard JUDGES it
# against the heal bundle and, since the slot path was cold-boot validated
# (car12, 0.6.1/0.6.2), RE-STAGES the GTK boot.img after the revert an OS
# update causes (the updater writes its boot.img over /flash/KERNEL — stock is
# back, silently) — under ONE gate that has no exception:
#   the staged kernel's release == the NEW SYSTEM's module tree.
# There is no grub pick on this chain: a kernel the ABL boots to a module-less
# system is a DARK unit with a card-in-a-PC recovery. So a release mismatch is
# named, not healed, and the stock the updater just wrote is left in the slot.
# When the gate holds: the updater's boot.img is parked as KERNEL.etk-stock
# (it IS the pristine stock for this OS; the old parked copy is for an OS that
# no longer exists), ours goes back sha-verified, and the operator is told to
# reboot once. Kill-switch ETK_OSGUARD_ABL_RESTAGE=0 (etk.conf): name only.
# Harness: tools/test_kernel_abl.sh (osguard section).
if [ "$(cat "$HEAL/chain" 2>/dev/null)" = "abl" ] || \
   { [ ! -f "$FLASH/boot/grub/grub.cfg" ] && [ ! -d "$FLASH/EFI" ] && [ "$(head -c 8 "$FLASH/KERNEL" 2>/dev/null)" = "ANDROID!" ]; }; then
    WANT=$(cat "$HEAL/KERNEL.staged.sha256" 2>/dev/null)
    if [ -z "$WANT" ] || [ "$(cat "$HEAL/mode" 2>/dev/null)" != "default" ]; then
        log "abl chain: no GTK boot.img deployed to the slot (bundle absent or mode=test) — nothing to guard"
        finish 0
    fi
    SLOT=$(sha256sum "$FLASH/KERNEL" 2>/dev/null | cut -d' ' -f1)
    if [ "$SLOT" = "$WANT" ]; then
        if grep -q 'msm.context_keepalive=1' /proc/cmdline 2>/dev/null; then
            log "abl chain: slot holds the staged GTK boot.img (sha ok) and this boot runs it"
        else
            log "abl chain: slot holds the staged GTK boot.img (sha ok) but this boot's cmdline has no keepalive — not yet cold-booted, or the ABL booted another target"
        fi
        finish 0
    fi
    STAGED_REL=$(cat "$HEAL/KERNEL.staged.release" 2>/dev/null)
    log "abl chain: slot sha ${SLOT:-none} != staged $WANT — an OS update (or a hand swap) put another boot.img in $FLASH/KERNEL; the GTK kernel is NOT live (SYSTEM modules $SYS_REL, staged kernel ${STAGED_REL:-?})"
    if [ "${ETK_OSGUARD_ABL_RESTAGE:-1}" = "0" ]; then
        notify "OS update replaced the GTK kernel. Re-run the ETK installer from your computer to restore it."
        [ "$MODE" = "heal" ] && finish 0 || finish 2
    fi
    # THE GATE. Release mismatch = the staged boot.img was built for another OS:
    # its modules would not load and nothing on this chain can fall back.
    if [ -z "$STAGED_REL" ] || [ "$STAGED_REL" != "$SYS_REL" ]; then
        log "abl chain: NOT re-staging — staged GTK kernel is ${STAGED_REL:-unknown}, this OS ships modules for $SYS_REL (a kernel that boots dark has no grub pick to recover from). Stock stays in the slot until a GTK kernel for $SYS_REL is installed."
        notify "OS updated to a new kernel ($SYS_REL). The ETK kernel for it is not installed yet - stock kernel in use. Re-run the ETK installer when the new build ships."
        [ "$MODE" = "heal" ] && finish 0 || finish 2
    fi
    GOT_STAGED=$(sha256sum "$HEAL/KERNEL.staged" 2>/dev/null | cut -d' ' -f1)
    if [ "$GOT_STAGED" != "$WANT" ] || [ "$(head -c 8 "$HEAL/KERNEL.staged" 2>/dev/null)" != "ANDROID!" ]; then
        log "abl chain: NOT re-staging — heal bundle KERNEL.staged is missing, corrupt (sha) or not a boot.img"
        notify "OS update replaced the GTK kernel and the staged copy is unusable. Re-run the ETK installer from your computer."
        [ "$MODE" = "heal" ] && finish 0 || finish 2
    fi
    if [ "$(head -c 8 "$FLASH/KERNEL" 2>/dev/null)" != "ANDROID!" ]; then
        # 2026-10-30 (P0 for ROCKNIX 20261101): the OS moved SM8250 to OpenABL + Boot Loader
        # Spec entries -- /flash/KERNEL is a raw PE Image ("MZ"), /flash/loader/entries/ exists,
        # and a boot.img cannot boot there at all. The release gate above does not catch it
        # (the kernel stays 7.2.0), so this branch is the ONLY place the operator can be told.
        # It used to exit silently: an ETK rig that took the update ran stock with no toast.
        # Never re-stage here -- the staged boot.img has no EFI stub.
        if [ -d "$FLASH/loader/entries" ] || [ "$(head -c 2 "$FLASH/KERNEL" 2>/dev/null)" = "MZ" ]; then
            log "abl chain: NOT re-staging — $FLASH/KERNEL is a PE/BLS kernel (ROCKNIX 20261101+, OpenABL); the staged boot.img cannot boot on this chain. The GTK kernel is NOT live; stock runs until an ETK release for this boot chain is installed. Slot left alone."
            notify "ROCKNIX moved to a new bootloader. The ETK kernel is not active - stock kernel in use. Re-run the ETK installer from your computer."
        else
            log "abl chain: NOT re-staging — $FLASH/KERNEL is not a boot.img (unknown format); leaving it alone"
            notify "OS update left an unknown kernel in the boot slot; the ETK kernel is not active. Re-run the ETK installer from your computer."
        fi
        [ "$MODE" = "heal" ] && finish 0 || finish 2
    fi
    # CARD-LABELS GATE (0.11.0). The staged boot.img's baked disk label must be the one
    # this unit boots from: a GTK card (disk=LABEL=GTKSTOR) whose bundle somehow holds the
    # ROCKNIX/STORAGE release asset (a pre-0.11.0 couch update staged it; a hand copy)
    # would otherwise get a kernel restored into its slot that mounts an internal
    # ROCKNIX's partitions or no root at all. kernel_stage.sh refuses such a stage now
    # (exit 3); this is the second lock on the same door. A staged image with no disk=
    # token is accepted only on a STORAGE unit. Seam: OSG_PROC.
    LIVE_DISK=$(tr ' ' '\n' < "${OSG_PROC:-/proc}/cmdline" 2>/dev/null | sed -n 's/^disk=LABEL=//p' | head -n1)
    STG_DISK=$(head -c 576 "$HEAL/KERNEL.staged" 2>/dev/null | tail -c 512 | tr -d '\0' | tr ' ' '\n' | sed -n 's/^disk=LABEL=//p' | head -n1)
    if [ -n "$LIVE_DISK" ] && [ "$STG_DISK" != "$LIVE_DISK" ] && { [ "$LIVE_DISK" != "STORAGE" ] || [ -n "$STG_DISK" ]; }; then
        log "abl chain: NOT re-staging — reason=card-labels: the staged GTK boot.img names disk=LABEL=${STG_DISK:-none}, this unit boots disk=LABEL=$LIVE_DISK (a kernel for another unit's partitions; it would boot into the wrong storage or none). Stock stays in the slot."
        notify "OS update replaced the GTK kernel, and the staged copy is built for other partition labels. Reflash the next ETK card image to update the kernel."
        [ "$MODE" = "heal" ] && finish 0 || finish 2
    fi
    log "abl chain: RE-STAGE — release $STAGED_REL matches this OS; parking the updater's boot.img as KERNEL.etk-stock and restoring the GTK boot.img"
    if [ "$MODE" != "heal" ]; then
        log "PLAN: park $FLASH/KERNEL -> KERNEL.etk-stock; cp $HEAL/KERNEL.staged -> $FLASH/KERNEL (sha-verified)"
        finish 2
    fi
    flash_rw || { log "abl chain: cannot remount $FLASH rw — not re-staged"; finish 0; }
    NEWMD5=$(md5sum "$FLASH/KERNEL" | cut -d' ' -f1)
    if cp "$FLASH/KERNEL" "$FLASH/KERNEL.etk-stock.new" && sync \
       && [ "$(md5sum "$FLASH/KERNEL.etk-stock.new" | cut -d' ' -f1)" = "$NEWMD5" ] \
       && mv -f "$FLASH/KERNEL.etk-stock.new" "$FLASH/KERNEL.etk-stock" && sync; then
        OSMD5=$(cut -d' ' -f1 "$FLASH/KERNEL.md5" 2>/dev/null)
        log "DONE: parked the updater's boot.img as KERNEL.etk-stock (md5 $NEWMD5, KERNEL.md5 ${OSMD5:-absent}, pristine=$([ -n "$OSMD5" ] && [ "$NEWMD5" = "$OSMD5" ] && echo yes || echo UNPROVEN))"
    else
        rm -f "$FLASH/KERNEL.etk-stock.new"
        log "FAIL: could not park the updater's boot.img — slot left as the updater wrote it"
        finish 0
    fi
    if cp "$HEAL/KERNEL.staged" "$FLASH/KERNEL.new" && sync \
       && [ "$(sha256sum "$FLASH/KERNEL.new" | cut -d' ' -f1)" = "$WANT" ] \
       && mv -f "$FLASH/KERNEL.new" "$FLASH/KERNEL" && sync \
       && [ "$(sha256sum "$FLASH/KERNEL" | cut -d' ' -f1)" = "$WANT" ]; then
        log "DONE: GTK boot.img restored to $FLASH/KERNEL (sha $WANT) — REBOOT once to run it"
        notify "OS update repaired: the ETK kernel is back in the boot slot. REBOOT once to finish."
        finish 0
    fi
    rm -f "$FLASH/KERNEL.new"
    log "FAIL: GTK boot.img write did not verify — slot left as the updater wrote it (stock boots)"
    notify "OS update replaced the GTK kernel and the restore did not verify. Re-run the ETK installer from your computer."
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
# FLIP 2 KIT DTB SLOT (install.sh STEP 6.4 parity: mic per ETK_INTERNAL_MIC,
# USB-C VBUS always). The banked grub block — and the live cfg — may boot
# /boot/grub/etk-flip2.dtb, derived from the OS's stock Flip 2 DTB. An OS update can replace that stock DTB (base sha
# changes) or leave the slot missing, and a grub `devicetree` that fails does
# NOT abort the entry (DT-less boot). So BEFORE the block goes back in:
#   bank coherent with this OS -> restore the banked DTB if the slot drifted
#   base changed / bank damaged -> re-derive from the new stock DTB (same
#     deltas install.sh chose: --no-mic when ETK_INTERNAL_MIC=0); if every
#     delta stands down, bank + slot hold the STOCK DTB byte-for-byte (boot
#     safe — the UCM overlay self-gates on the booted DT). Either
#     way the bank then matches the base, so the next boot is a no-op.
# Last line of defence: if the slot is still absent, the block is replayed
# with the stock DTB path (never an entry naming a missing devicetree).
DTB_TOOL="${OSG_DTB_TOOL:-$ETK_ROOT/bin/etk_dtb_mic.py}"
STOCK_DTB="$FLASH/boot/grub/sm8250-retroidpocket-flip2.dtb"
SLOT="$FLASH/boot/grub/etk-flip2.dtb"
BLOCK="$HEAL/grub.block"
if grep -qs 'etk-flip2.dtb' "$HEAL/grub.block" "$FLASH/EFI/BOOT/grub.cfg" "$FLASH/boot/grub/grub.cfg" \
        && [ -f "$STOCK_DTB" ]; then
    BASE_NOW=$(sha256sum "$STOCK_DTB" | cut -d' ' -f1)
    BASE_WAS=$(cat "$HEAL/DTB.base.sha256" 2>/dev/null)
    D_WANT=$(cat "$HEAL/DTB.staged.sha256" 2>/dev/null)
    D_GOT=$(sha256sum "$HEAL/DTB.staged" 2>/dev/null | cut -d' ' -f1)
    if [ -n "$D_WANT" ] && [ "$D_GOT" = "$D_WANT" ] && [ "$BASE_NOW" = "$BASE_WAS" ]; then
        if [ "$(sha256sum "$SLOT" 2>/dev/null | cut -d' ' -f1)" != "$D_WANT" ]; then
            flash_rw && act "restore Flip 2 kit DTB -> $SLOT" cp "$HEAL/DTB.staged" "$SLOT" && CHANGED=1
        fi
    elif [ "$MODE" != "heal" ]; then
        log "PLAN: re-derive the Flip 2 kit DTB (stock DTB base changed or bank damaged)"; CHANGED=1
    else
        D_FLAGS=""; [ "${ETK_INTERNAL_MIC:-1}" = "1" ] || D_FLAGS="--no-mic"
        D_OUT=$(python3 "$DTB_TOOL" derive $D_FLAGS "$STOCK_DTB" "$HEAL/DTB.staged" 2>&1) || true
        case "$D_OUT" in
            DTB_MIC_OK*) D_NOTE="re-derived for this OS ($(printf '%s' "$D_OUT" | sed -n 's/.* \(mic=[^ ]* vbus=[^ ]*\).*/\1/p'))" ;;
            *) cp "$STOCK_DTB" "$HEAL/DTB.staged" 2>/dev/null
               D_NOTE="patcher stood down ($(printf '%s' "$D_OUT" | awk '{print $2}')) - STOCK DTB in the slot, kit deltas off" ;;
        esac
        sha256sum "$HEAL/DTB.staged" 2>/dev/null | cut -d' ' -f1 > "$HEAL/DTB.staged.sha256"
        printf '%s\n' "$BASE_NOW" > "$HEAL/DTB.base.sha256"
        flash_rw && act "Flip 2 kit DTB slot: $D_NOTE -> $SLOT" cp "$HEAL/DTB.staged" "$SLOT" && CHANGED=1
    fi
    if [ "$MODE" = "heal" ] && [ ! -f "$SLOT" ] && grep -q 'etk-flip2.dtb' "$HEAL/grub.block" 2>/dev/null; then
        sed 's#/boot/grub/etk-flip2.dtb#/boot/grub/sm8250-retroidpocket-flip2.dtb#g' "$HEAL/grub.block" > "$HEAL/grub.block.stockdtb" \
            && BLOCK="$HEAL/grub.block.stockdtb" && log "kit DTB slot unavailable - replaying the ETK entries with the stock DTB"
    fi
fi
if [ -f "$BLOCK" ]; then
    for CFG in "$FLASH/EFI/BOOT/grub.cfg" "$FLASH/boot/grub/grub.cfg"; do
        [ -f "$CFG" ] || continue
        grep -q "etk-gtk-test" "$CFG" && continue
        flash_rw || continue
        if [ "$MODE" = "heal" ]; then
            awk -v bf="$BLOCK" '
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
