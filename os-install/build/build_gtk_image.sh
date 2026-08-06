#!/bin/bash
# ==========================================================================
# build_gtk_image.sh — ETK Tier-I image-repack lane (RocknixToolchainSpec §5)
# --------------------------------------------------------------------------
# Turns the official ROCKNIX SM8250 release .img into an update-friendly
# "ROCKNIX-GTK Edition" flashable image:
#   * SYSTEM squashfs LEFT STOCK (in-place OS updater keeps working — Q2)
#   * GTK kernel (parity + audiofix) as the DEFAULT branded boot
#   * ETK pre-installed on STORAGE (framework + RPCS3-GTK + Turnip-GTK), NO vault
#   * boot cmdline PARTITION-UUID-PINNED (test variant) so a 2nd ROCKNIX on the
#     operator's UFS rig can't LABEL-cross-wire (the split-brain guard)
# Runs UNPRIVILEGED (mtools + mke2fs -d + sfdisk); no loop-mount, no root.
# Boot partition is FAT holding KERNEL/SYSTEM/grub as FILES → mtools edits it.
# ==========================================================================
set -eu

# ---- inputs (container paths) --------------------------------------------
BASE_GZ="${BASE_GZ:-/work/ROCKNIX-SM8250.aarch64-20260701.img.gz}"
KERNEL="${KERNEL:-/rocknix-gtk/artifacts/KERNEL.rocknix-gtk-20260706-audiofix0}"
APPIMAGE="${APPIMAGE:-/work/build/rpcs3-sa.custom}"
TURNIP_SO="${TURNIP_SO:-/etk/drivers/etk_turnip_rocknix_26.1.3_gtk_0.4.so}"
SEED_PULL="${SEED_PULL:-/work/build/seed_pull}"
REPO="${REPO:-/etk}"
OUT_IMG="${OUT_IMG:-/work/ROCKNIX-GTK-SM8250.aarch64-20260707.img}"
GTK_VER="${GTK_VER:-0.7.0}"
KDATE="${KDATE:-20260706}"
STORAGE_MIB="${STORAGE_MIB:-512}"
FLIP2_DTB="/boot/grub/sm8250-retroidpocket-flip2.dtb"
WORK="/tmp/work.img"
SEED="/tmp/seed"
STOR="/tmp/storage.ext4"

say(){ printf '\n\033[36m== %s\033[0m\n' "$*"; }
die(){ printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

for f in "$BASE_GZ" "$KERNEL" "$APPIMAGE" "$TURNIP_SO"; do [ -f "$f" ] || die "missing input: $f"; done
[ -d "$SEED_PULL/.config" ] || die "missing seed pull: $SEED_PULL/.config"

# ---- 1. decompress + map -------------------------------------------------
say "1. decompress base image"
rm -f "$WORK"
gunzip -c "$BASE_GZ" > "$WORK"
BASE_BYTES=$(stat -c %s "$WORK")
echo "   base raw: $BASE_BYTES bytes"
# machine-readable partition map (unit B): line N = "num:startB:endB:sizeB:fs:..."
MAP=$(parted -m "$WORK" unit B print 2>/dev/null)
BOOT_OFF=$(echo "$MAP" | awk -F: '/^1:/{gsub("B","",$2);print $2}')
STOR_START=$(echo "$MAP" | awk -F: '/^2:/{gsub("B","",$2);print $2}')
[ -n "$BOOT_OFF" ] && [ -n "$STOR_START" ] || die "could not parse partition map"
echo "   boot FAT @ ${BOOT_OFF}B  |  STORAGE @ ${STOR_START}B"
MI(){ mdir -i "$WORK@@$BOOT_OFF" "$@" 2>/dev/null; }
MC(){ mcopy -i "$WORK@@$BOOT_OFF" "$@" 2>/dev/null; }

# ---- 2. build STORAGE seed (first, to capture its fs-UUID) ----------------
say "2. stage ETK seed (framework + forks + services, NO vault)"
rm -rf "$SEED"; mkdir -p "$SEED"
: > "$SEED/.please_resize_me"                                   # first-boot grow trigger
# 2a. framework  (bin/scripts/config + tools subset) — clean, from the repo
ER="$SEED/games-internal/roms/etk"; mkdir -p "$ER/tools"
for d in bin scripts config; do
  rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' "$REPO/$d" "$ER/"
done
cp "$REPO/tools/etk_drift.py" "$ER/tools/" 2>/dev/null || true
cp "$REPO/tools/vault_sweep.sh" "$ER/tools/" 2>/dev/null || true
# 2b. forks (certified v0.7.0 assets)
mkdir -p "$SEED/rpcs3" "$SEED/turnip/drivers"
cp "$APPIMAGE" "$SEED/rpcs3/rpcs3-sa.custom"; chmod 755 "$SEED/rpcs3/rpcs3-sa.custom"
TSO=$(basename "$TURNIP_SO"); cp "$TURNIP_SO" "$SEED/turnip/drivers/$TSO"; chmod 755 "$SEED/turnip/drivers/$TSO"
printf '%s\n' "$TSO" > "$SEED/turnip/selected"                 # certified default pick (NOT the rig's stale 0.3)
# 2c. persistent /storage/.config (allowlisted standard bits from the rig pull)
mkdir -p "$SEED/.config/system.d/multi-user.target.wants" "$SEED/.config/system.d/basic.target.wants"
mkdir -p "$SEED/.config/custom_scripts" "$SEED/.config/profile.d"
SVCS="etk etk-rpcs3 etk-turnip etk-power etk-blackbox etk-audio-watchdog etk-dpmirror etk-stage3"
for s in $SVCS etk-gtk-version; do
  cp "$SEED_PULL/.config/system.d/$s.service" "$SEED/.config/system.d/$s.service"
done
for s in $SVCS; do                                             # enable at multi-user (NOT sshd/sd-rebind)
  ln -sf "/storage/.config/system.d/$s.service" "$SEED/.config/system.d/multi-user.target.wants/$s.service"
done
ln -sf "/storage/.config/system.d/etk-gtk-version.service" "$SEED/.config/system.d/basic.target.wants/etk-gtk-version.service"
cp "$SEED_PULL/.config/custom_scripts/01-etk-sentry.sh" "$SEED/.config/custom_scripts/"; chmod 755 "$SEED/.config/custom_scripts/01-etk-sentry.sh"
for h in etk-rpcs3-bind.sh etk-turnip-bind.sh etk-power-apply.sh; do
  cp "$SEED_PULL/.config/$h" "$SEED/.config/$h"; chmod 755 "$SEED/.config/$h"
done
cp "$SEED_PULL/.config/profile.d/098-etk-stage3" "$SEED/.config/profile.d/098-etk-stage3"
echo "   seed size: $(du -sh "$SEED" | cut -f1)"

say "3. build enlarged ext4 STORAGE (${STORAGE_MIB} MiB) via mke2fs -d"
rm -f "$STOR"
BLOCKS=$(( STORAGE_MIB * 1024 / 4 ))                            # 4KiB blocks
mke2fs -q -F -t ext4 -b 4096 -L STORAGE -d "$SEED" "$STOR" "$BLOCKS"
STOR_UUID=$(dumpe2fs -h "$STOR" 2>/dev/null | awk -F: '/Filesystem UUID/{gsub(/ /,"",$2);print $2}')
[ -n "$STOR_UUID" ] || die "no STORAGE uuid"
echo "   STORAGE uuid = $STOR_UUID"
e2fsck -fn "$STOR" >/dev/null 2>&1 && echo "   e2fsck: clean" || echo "   e2fsck: (nonzero — see log)"

# ---- 4. boot-partition edits (mtools; SYSTEM untouched) -------------------
say "4. boot FAT: graft GTK kernel + branded, UUID-pinned grub"
# FAT volume serial -> grub fs-uuid form XXXX-XXXX (uppercase, blkid style)
SER=$(minfo -i "$WORK@@$BOOT_OFF" :: 2>/dev/null | awk '/serial/{print $NF}' | tr -d '[:space:]' | tr 'a-f' 'A-F')
SER=$(printf '%08s' "$SER" | tr ' ' '0')
BOOT_UUID="${SER:0:4}-${SER:4:4}"
echo "   boot FAT uuid = $BOOT_UUID"
FREE=$(MI ::/ | awk '/bytes free/{gsub(/[^0-9]/,"");print}')
KSZ=$(stat -c %s "$KERNEL")
echo "   FAT free=${FREE}  kernel=${KSZ}"
[ "${FREE:-0}" -gt "$KSZ" ] || die "not enough free space in boot FAT for KERNEL.gtktest"
MC -o "$KERNEL" ::/KERNEL.gtktest
# craft branded grub.cfg: prepend 3 ETK entries before the first stock menuentry
MC ::/boot/grub/grub.cfg /tmp/base_grub.cfg
cat > /tmp/etk_entries.cfg <<EOF
menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {
        savedefault
        search --set=root --fs-uuid $BOOT_UUID
        linux /KERNEL.gtktest boot=UUID=$BOOT_UUID disk=UUID=$STOR_UUID grub_portable rootwait console=tty0 quiet loglevel=3 panic=30 msm.context_keepalive=1
        devicetree $FLIP2_DTB
}
menuentry 'ROCKNIX-GTK for Flip 2 (verbose)' \$menuentry_id_option 'etk-gtk-verbose' {
        search --set=root --fs-uuid $BOOT_UUID
        linux /KERNEL.gtktest boot=UUID=$BOOT_UUID disk=UUID=$STOR_UUID grub_portable rootwait console=tty0 loglevel=7 panic=30 msm.context_keepalive=1
        devicetree $FLIP2_DTB
}
menuentry 'ROCKNIX-GTK fallback -- stock kernel' \$menuentry_id_option 'etk-fallback-stock' {
        savedefault
        search --set=root --fs-uuid $BOOT_UUID
        linux /KERNEL boot=UUID=$BOOT_UUID disk=UUID=$STOR_UUID grub_portable quiet rootwait console=tty0 video=efifb:off
        devicetree $FLIP2_DTB
}
EOF
awk 'BEGIN{ins=0} /^menuentry / && !ins{while((getline l < "/tmp/etk_entries.cfg")>0)print l; close("/tmp/etk_entries.cfg"); ins=1} {print}' \
    /tmp/base_grub.cfg > /tmp/grub.cfg
MC -o /tmp/grub.cfg ::/boot/grub/grub.cfg
mmd -i "$WORK@@$BOOT_OFF" ::/EFI 2>/dev/null || true
mmd -i "$WORK@@$BOOT_OFF" ::/EFI/BOOT 2>/dev/null || true
MC -o /tmp/grub.cfg ::/EFI/BOOT/grub.cfg
# grubenv (fixed 1024 bytes, '#'-padded) → saved_entry = GTK default
{ printf '# GRUB Environment Block\nsaved_entry=etk-gtk-test\n'; } > /tmp/grubenv
PAD=$(( 1024 - $(stat -c %s /tmp/grubenv) )); head -c "$PAD" /dev/zero | tr '\0' '#' >> /tmp/grubenv
MC -o /tmp/grubenv ::/boot/grub/grubenv
MC -o /tmp/grubenv ::/EFI/BOOT/grubenv

# ---- 5. splice STORAGE into image + fix MBR -------------------------------
say "5. splice STORAGE + rewrite MBR partition 2"
NEW_BYTES=$(( STOR_START + STORAGE_MIB * 1024 * 1024 ))
truncate -s "$NEW_BYTES" "$WORK"
dd if="$STOR" of="$WORK" bs=1M seek=$(( STOR_START / 1048576 )) conv=notrunc status=none
SEC_SIZE=$(( STORAGE_MIB * 1024 * 1024 / 512 ))                # p2 start unchanged; only size grows
sfdisk -d "$WORK" > /tmp/pt.in 2>/dev/null
awk -v n="$SEC_SIZE" '/img2 *:/{sub(/size=[ ]*[0-9]+/,"size= " n)} {print}' /tmp/pt.in > /tmp/pt.out
grep -q "size= *$SEC_SIZE" /tmp/pt.out || die "MBR p2 size rewrite failed"
sfdisk --no-reread -q "$WORK" < /tmp/pt.out >/dev/null 2>&1 || die "sfdisk repartition failed"

# ---- 6. verify -----------------------------------------------------------
say "6. verify"
parted -m "$WORK" unit B print 2>/dev/null | sed 's/^/   /'
echo "   -- boot FAT root --"; MI ::/ | grep -iE 'KERNEL|SYSTEM' | sed 's/^/   /'
echo "   -- grub ETK entries --"; MC ::/boot/grub/grub.cfg /tmp/vg.cfg; grep -n "ROCKNIX-GTK\|etk-gtk-test\|disk=UUID" /tmp/vg.cfg | sed 's/^/   /'
echo "   -- STORAGE contents --"
dd if="$WORK" of=/tmp/vs.ext4 bs=1M skip=$(( STOR_START / 1048576 )) count="$STORAGE_MIB" status=none
debugfs -R "ls -l /games-internal/roms/etk" /tmp/vs.ext4 2>/dev/null | sed 's/^/   /' | head
echo "   resize marker:"; debugfs -R "stat /.please_resize_me" /tmp/vs.ext4 2>/dev/null | grep -i inode | head -1 | sed 's/^/   /'
V_UUID=$(dumpe2fs -h /tmp/vs.ext4 2>/dev/null | awk -F: '/Filesystem UUID/{gsub(/ /,"",$2);print $2}')
[ "$V_UUID" = "$STOR_UUID" ] && echo "   STORAGE uuid matches grub: $V_UUID" || die "STORAGE uuid drift"
# SYSTEM untouched proof (update-friendly, Q2): extract from new img + from base, sha-compare
say "6b. prove SYSTEM squashfs is byte-identical to stock (update-friendly)"
MC ::/SYSTEM /tmp/new_SYSTEM
gunzip -c "$BASE_GZ" > /tmp/base_ref.img
BREF_OFF=$(parted -m /tmp/base_ref.img unit B print 2>/dev/null | awk -F: '/^1:/{gsub("B","",$2);print $2}')
mcopy -i "/tmp/base_ref.img@@$BREF_OFF" ::/SYSTEM /tmp/base_SYSTEM 2>/dev/null
NS=$(sha256sum /tmp/new_SYSTEM | cut -d' ' -f1); BS=$(sha256sum /tmp/base_SYSTEM | cut -d' ' -f1)
[ "$NS" = "$BS" ] && echo "   SYSTEM identical ✅ ($NS)" || die "SYSTEM changed — NOT update-friendly"
rm -f /tmp/base_ref.img /tmp/base_SYSTEM /tmp/new_SYSTEM /tmp/vs.ext4

# ---- 7. compress + stamp -------------------------------------------------
say "7. compress + sha"
rm -f "$OUT_IMG" "$OUT_IMG.gz"
gzip -c "$WORK" > "$OUT_IMG.gz"
sha256sum "$OUT_IMG.gz" | tee "$OUT_IMG.gz.sha256"
echo "   raw=${NEW_BYTES}B  gz=$(stat -c %s "$OUT_IMG.gz")B  -> $OUT_IMG.gz"
say "DONE  (boot_uuid=$BOOT_UUID  storage_uuid=$STOR_UUID)"
