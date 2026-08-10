#!/bin/bash
# ==========================================================================
# build_gtk_image_v2.sh — ETK Tier-I image, Candidate B (resize-capable)
# --------------------------------------------------------------------------
# v2 fixes the v1 first-boot forensics (ImageLaneFirstBootForensics_20260707.md):
#   * DO NOT bake /storage/.config  → ROCKNIX fs-resize runs → STORAGE grows to
#     fill ANY card (v1 baked .config → fs-resize refused "already initialised").
#   * UNIQUE LABELS (boot=ROCKNIX-GTK, storage=GTKSTOR) → split-brain-safe on the
#     operator's UFS rig AND survive fs-resize's tune2fs/fatlabel UUID-randomize
#     (v1's UUID-pin would have broken after a resize).
#   * ETK is STAGED (framework + GTK forks in $ETK_ROOT + /storage/{rpcs3,turnip},
#     none under .config so none block resize); it ACTIVATES when the user runs
#     install.sh from host (matches the spec's "config ETK with install.sh").
#   * chmod +x the seeded scripts (v1 shipped audio_watchdog.sh non-+x → 203/EXEC).
# SYSTEM squashfs still untouched (update-friendly). Unprivileged (mtools/mke2fs).
set -eu

# DEFAULTS MUST NAME THE ARTIFACTS THE RELEASE ACTUALLY SHIPS. Bitten at the
# 0.8.4 cut: TURNIP_SO still defaulted to gtk_0.6 — the bit-collision driver that
# had been unpublished and deleted — so the lane would have died at the input
# check below, and KERNEL still named -0.3 after the forge moved to etk-cloud.
# release_sanity.sh only validated the FILENAME FORMAT of these, never that the
# file exists or is the shipping one; that check is now in the gate too.
BASE_GZ="${BASE_GZ:-/work/ROCKNIX-SM8250.aarch64-20260801.img.gz}"
KERNEL="${KERNEL:-/rocknix-gtk/artifacts/KERNEL.rocknix-gtk-20260801-0.4.1}"
APPIMAGE="${APPIMAGE:-/etk/emulators/rpcs3-etk_gtk-edition-0.8.5_v0.0.41-19638-a1deb2921_linux_aarch64.AppImage}"
TURNIP_SO="${TURNIP_SO:-/etk/drivers/etk_turnip_rocknix_26.2.0_gtk_0.7.so}"
REPO="${REPO:-/etk}"
HOOK_SCRIPT="${HOOK_SCRIPT:-}"              # optional: bake /flash/mount-storage.sh (hostless hook)
SEED_CONFIG="${SEED_CONFIG:-}"             # optional: stage $ETK_ROOT/.seed_config (hook installs it on boot 2)
# NAMING (AI_MANIFEST law #8): pass OUT_IMG with a VERSION number (…-v3.img), never a
# feature name (…-p0hook.img) — feature names are cruft the end user must clean up at release.
OUT_IMG="${OUT_IMG:-/work/ROCKNIX-GTK-SM8250.aarch64-20260801.img}"
STORAGE_MIB="${STORAGE_MIB:-256}"          # small; fs-resize grows to full card
# ⚠ THESE DEFAULTS ARE NOT WHAT SHIPS. Verified 2026-08-06 by decompressing the
# published v0.8.3 image and reading the partition table directly (GPT; p1
# 'system' FAT label, p2 'storage' ext4 superblock label):
#     p1 = ROCKNIX-GTK        p2 = GTKSTOR
# i.e. the distributable uses the UNIQUE labels, not these. The comment that
# stood here claimed the opposite ("STANDARD labels = the distributable") and was
# wrong for at least two releases — the same stale line TRACK_MANUAL §8 flags
# with "do not trust this line; recover the labels by decompressing the image".
# The shipped invocation is in FLASH_AND_TEST.md and overrides all four:
#   HOOK_SCRIPT=/work/build/mount-storage.sh SEED_CONFIG=/work/build/seed_config \
#   BOOT_LABEL=ROCKNIX-GTK STOR_LABEL=GTKSTOR OUT_IMG=...
# Defaults stay STANDARD because they are the safe choice for a test card built
# to sit beside an internal ROCKNIX — but never cut a release from them.
BOOT_LABEL="${BOOT_LABEL:-ROCKNIX}"        # <=11 chars for FAT
STOR_LABEL="${STOR_LABEL:-STORAGE}"
FLIP2_DTB="/boot/grub/sm8250-retroidpocket-flip2.dtb"
WORK="/tmp/work2.img"; SEED="/tmp/seed2"; STOR="/tmp/storage2.ext4"

say(){ printf '\n\033[36m== %s\033[0m\n' "$*"; }
die(){ printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }
for f in "$BASE_GZ" "$KERNEL" "$APPIMAGE" "$TURNIP_SO"; do [ -f "$f" ] || die "missing input: $f"; done

say "1. decompress + map"
rm -f "$WORK"; gunzip -c "$BASE_GZ" > "$WORK"
MAP=$(parted -m "$WORK" unit B print 2>/dev/null)
BOOT_OFF=$(echo "$MAP" | awk -F: '/^1:/{gsub("B","",$2);print $2}')
STOR_START=$(echo "$MAP" | awk -F: '/^2:/{gsub("B","",$2);print $2}')
[ -n "$BOOT_OFF" ] && [ -n "$STOR_START" ] || die "partition map parse"
MI(){ mdir -i "$WORK@@$BOOT_OFF" "$@" 2>/dev/null; }
MC(){ mcopy -i "$WORK@@$BOOT_OFF" "$@" 2>/dev/null; }
echo "   boot FAT @ ${BOOT_OFF}B  STORAGE @ ${STOR_START}B"

say "2. stage ETK seed — framework + GTK forks, NO .config (so fs-resize runs)"
rm -rf "$SEED"; mkdir -p "$SEED"
: > "$SEED/.please_resize_me"
ER="$SEED/games-internal/roms/etk"; mkdir -p "$ER/tools"
for d in bin scripts config; do
  rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' "$REPO/$d" "$ER/"
done
cp "$REPO/tools/etk_drift.py" "$ER/tools/" 2>/dev/null || true
cp "$REPO/tools/vault_sweep.sh" "$ER/tools/" 2>/dev/null || true
# chmod +x every daemon/script (v1's 203/EXEC fix — git ships some non-+x)
find "$ER/bin" "$ER/scripts" -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod 0755 {} +
# hostless activation payload: the .config the /flash hook installs on boot 2 (lives
# under $ETK_ROOT so it never trips fs-resize's /storage/.config guard)
if [ -n "$SEED_CONFIG" ]; then
  [ -d "$SEED_CONFIG" ] || die "SEED_CONFIG set but missing: $SEED_CONFIG"
  cp -a "$SEED_CONFIG" "$ER/.seed_config"
  # The seed is a RENDERED snapshot of a past install, so any unit in it that
  # install.sh normally templates is frozen at whatever the snapshot captured.
  # etk-gtk-version.service was found baked at "0.7.0-20260706" — a hostless
  # first boot would announce a version and a kernel date that are both years of
  # releases stale, because the /flash hook installs the seed directly and never
  # runs install.sh's sed. Re-render it here from the repo template using this
  # image's OWN inputs, so the flashed card and the host install agree.
  _GV=$(grep -m1 '^APP_VERSION' "$REPO/bin/etk_pitstop.py" | cut -d'"' -f2)
  _GD=$(basename "$KERNEL" | grep -oE '[0-9]{8}' | head -1)
  for _t in "$ER/.seed_config/system.d/etk-gtk-version.service"; do
    [ -f "$_t" ] || continue
    sed -e "s/@GTKVER@/${_GV:-dev}/" -e "s/@KDATE@/${_GD:-dev}/" \
        "$REPO/config/etk-gtk-version.service" > "$_t"
    echo "   re-rendered seed boot-identity: ROCKNIX-GTK ${_GV:-dev}-${_GD:-dev}"
  done
  echo "   staged .seed_config: $(find "$ER/.seed_config" -type f | wc -l | tr -d ' ') files + $(find "$ER/.seed_config" -type l | wc -l | tr -d ' ') symlinks (hook installs on boot 2)"
fi
# GTK forks staged where install.sh binds them (outside .config → don't block resize)
mkdir -p "$SEED/rpcs3" "$SEED/turnip/drivers"
cp "$APPIMAGE" "$SEED/rpcs3/rpcs3-sa.custom"; chmod 755 "$SEED/rpcs3/rpcs3-sa.custom"
# THE CARD MUST CARRY THE WHOLE DRIVER CATALOG, NOT JUST THE DEFAULT.
# $SEED/turnip/drivers IS what Pitstop's DRIVER tab enumerates (TURNIP_DRIVERS_DIR
# = /storage/turnip/drivers). This used to copy exactly ONE .so — TURNIP_SO — so a
# flashed card offered a chooser with a single entry: no downgrade, and the
# pre-release arm absent entirely. A host install never showed it, because
# install.sh STEP 6.5 fetches every CERTIFIED_BUILDS driver to the rig; only the
# hostless card was crippled, and only the operator flashing one would ever see it
# (found exactly that way, 2026-08-10).
# TURNIP_CATALOG = space-separated PATHS; TURNIP_SO stays the SELECTED default and
# must be one of them. Defaults to the single driver so old invocations still work.
TURNIP_CATALOG="${TURNIP_CATALOG:-$TURNIP_SO}"
TSO=$(basename "$TURNIP_SO")
_seen_default=0
for _drv in $TURNIP_CATALOG; do
  [ -f "$_drv" ] || die "TURNIP_CATALOG entry missing: $_drv"
  _b=$(basename "$_drv")
  cp "$_drv" "$SEED/turnip/drivers/$_b"; chmod 755 "$SEED/turnip/drivers/$_b"
  [ "$_b" = "$TSO" ] && _seen_default=1
done
[ "$_seen_default" = 1 ] || die "TURNIP_SO ($TSO) is not in TURNIP_CATALOG — the card would boot on a driver it does not ship"
printf '%s\n' "$TSO" > "$SEED/turnip/selected"
echo "   turnip catalog: $(ls "$SEED/turnip/drivers" | wc -l | tr -d ' ') driver(s), selected=$TSO"
echo "   seed: $(du -sh "$SEED" | cut -f1)  (NO /storage/.config)"

say "3. build ext4 STORAGE (${STORAGE_MIB} MiB, LABEL=$STOR_LABEL) — fs-resize grows it on 1st boot"
rm -f "$STOR"; BLOCKS=$(( STORAGE_MIB * 1024 / 4 ))
mke2fs -q -F -t ext4 -b 4096 -O ^orphan_file -L "$STOR_LABEL" -d "$SEED" "$STOR" "$BLOCKS"
dumpe2fs -h "$STOR" 2>/dev/null | grep -iE 'Filesystem features' | grep -q resize_inode \
  && echo "   resize_inode present (fs-resize-compatible)" || echo "   WARN: no resize_inode"
e2fsck -fn "$STOR" >/dev/null 2>&1 && echo "   e2fsck clean"

say "4. boot FAT: relabel $BOOT_LABEL + graft GTK kernel + branded LABEL-pinned grub"
mlabel -i "$WORK@@$BOOT_OFF" "::$BOOT_LABEL" 2>/dev/null || die "mlabel failed"
echo "   FAT volume: $(MI ::/ | awk '/Volume in drive/{print $NF}')"
FREE=$(MI ::/ | awk '/bytes free/{gsub(/[^0-9]/,"");print}'); KSZ=$(stat -c %s "$KERNEL")
[ "${FREE:-0}" -gt "$KSZ" ] || die "boot FAT too full for KERNEL.gtktest (free=$FREE need=$KSZ)"
MC -o "$KERNEL" ::/KERNEL.gtktest
MC ::/boot/grub/grub.cfg /tmp/base_grub2.cfg
cat > /tmp/etk_entries2.cfg <<EOF
menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {
        savedefault
        search --set=root --label $BOOT_LABEL
        linux /KERNEL.gtktest boot=LABEL=$BOOT_LABEL disk=LABEL=$STOR_LABEL grub_portable rootwait console=tty0 quiet loglevel=3 panic=30 msm.context_keepalive=1
        devicetree $FLIP2_DTB
}
menuentry 'ROCKNIX-GTK for Flip 2 (verbose)' \$menuentry_id_option 'etk-gtk-verbose' {
        search --set=root --label $BOOT_LABEL
        linux /KERNEL.gtktest boot=LABEL=$BOOT_LABEL disk=LABEL=$STOR_LABEL grub_portable rootwait console=tty0 loglevel=7 panic=30 msm.context_keepalive=1
        devicetree $FLIP2_DTB
}
menuentry 'ROCKNIX-GTK fallback -- stock kernel' \$menuentry_id_option 'etk-fallback-stock' {
        savedefault
        search --set=root --label $BOOT_LABEL
        linux /KERNEL boot=LABEL=$BOOT_LABEL disk=LABEL=$STOR_LABEL grub_portable quiet rootwait console=tty0 video=efifb:off
        devicetree $FLIP2_DTB
}
EOF
awk 'BEGIN{ins=0} /^menuentry / && !ins{while((getline l < "/tmp/etk_entries2.cfg")>0)print l; close("/tmp/etk_entries2.cfg"); ins=1} {print}' \
    /tmp/base_grub2.cfg > /tmp/grub2.cfg
MC -o /tmp/grub2.cfg ::/boot/grub/grub.cfg
mmd -i "$WORK@@$BOOT_OFF" ::/EFI 2>/dev/null || true; mmd -i "$WORK@@$BOOT_OFF" ::/EFI/BOOT 2>/dev/null || true
MC -o /tmp/grub2.cfg ::/EFI/BOOT/grub.cfg
{ printf '# GRUB Environment Block\nsaved_entry=etk-gtk-test\n'; } > /tmp/grubenv2
PAD=$(( 1024 - $(stat -c %s /tmp/grubenv2) )); head -c "$PAD" /dev/zero | tr '\0' '#' >> /tmp/grubenv2
MC -o /tmp/grubenv2 ::/boot/grub/grubenv; MC -o /tmp/grubenv2 ::/EFI/BOOT/grubenv
# --- optional: bake the hostless P0 hook into /flash (init sources /flash/mount-storage.sh) ---
if [ -n "$HOOK_SCRIPT" ]; then
  [ -f "$HOOK_SCRIPT" ] || die "HOOK_SCRIPT set but missing: $HOOK_SCRIPT"
  sh -n "$HOOK_SCRIPT" || die "hook has a syntax error (would abort init!): $HOOK_SCRIPT"
  MC -o "$HOOK_SCRIPT" ::/mount-storage.sh
  echo "   baked /flash/mount-storage.sh ($(stat -c %s "$HOOK_SCRIPT")B)"
fi

say "5. splice STORAGE + rewrite partition-table p2"
# +1 MiB tail: GPT keeps a 33-sector backup table AFTER the last partition
# (MBR needed nothing) — without the pad, p2's end sits past last-usable-lba
# and sfdisk refuses. fs-resize consumes the slack on first boot anyway.
NEW_BYTES=$(( STOR_START + STORAGE_MIB * 1024 * 1024 + 1048576 ))
truncate -s "$NEW_BYTES" "$WORK"
dd if="$STOR" of="$WORK" bs=1M seek=$(( STOR_START / 1048576 )) conv=notrunc status=none
SEC_SIZE=$(( STORAGE_MIB * 1024 * 1024 / 512 ))
sfdisk -d "$WORK" > /tmp/pt2.in 2>/dev/null
# GPT-aware (base went MBR->GPT at ROCKNIX 20260801): drop the dumped
# first-lba/last-lba bounds so sfdisk recomputes them for the RESIZED file —
# the dumped last-lba is the old end-of-disk, and keeping it rejects the new
# p2 ("last usable GPT sector is N, but M requested") while the backup header
# stays stranded mid-file. Harmless no-op on the old MBR (label: dos) bases.
awk -v n="$SEC_SIZE" '
    /^first-lba:/ || /^last-lba:/ { next }
    /img2 *:/ { sub(/size=[ ]*[0-9]+/, "size= " n) } { print }' /tmp/pt2.in > /tmp/pt2.out
grep -q "size= *$SEC_SIZE" /tmp/pt2.out || die "MBR p2 size rewrite failed"
sfdisk --no-reread -q "$WORK" < /tmp/pt2.out >/dev/null 2>&1 || die "sfdisk failed"

say "6. verify"
parted -m "$WORK" unit B print 2>/dev/null | sed 's/^/   /'
echo "   -- labels --"; blkid_fake=$(MI ::/ | awk '/Volume in drive/{print}'); echo "   FAT: $blkid_fake"
dd if="$WORK" of=/tmp/vs2.ext4 bs=1M skip=$(( STOR_START / 1048576 )) count="$STORAGE_MIB" status=none
echo "   ext4 label: $(dumpe2fs -h /tmp/vs2.ext4 2>/dev/null | awk -F: '/volume name/{gsub(/ /,"",$2);print $2}')"
echo "   -- boot FAT (KERNEL + KERNEL.gtktest + SYSTEM) --"; MI ::/ | grep -iE 'KERNEL|SYSTEM' | sed 's/^/   /'
if [ -n "$HOOK_SCRIPT" ]; then
  echo "   -- /flash/mount-storage.sh (hostless P0 hook) --"; MI ::/ | grep -i 'mount' | sed 's/^/   /'
  MC ::/mount-storage.sh /tmp/vh.sh 2>/dev/null && { grep -q 'mount_part "\$disk"' /tmp/vh.sh && echo "   hook mounts \$disk FIRST ✅" || die "hook missing the mount line"; sh -n /tmp/vh.sh && echo "   hook syntax OK ✅"; }
fi
echo "   -- grub (label-pinned, ETK first) --"; MC ::/boot/grub/grub.cfg /tmp/vg2.cfg; grep -nE "ROCKNIX-GTK for Flip 2'|disk=LABEL|search --set=root --label" /tmp/vg2.cfg | head -4 | sed 's/^/   /'
echo "   -- STORAGE (framework + forks + marker, NO .config) --"
debugfs -R "ls /" /tmp/vs2.ext4 2>/dev/null | tr -s ' ' | fold -sw100 | sed 's/^/   /'
echo "   etk/bin daemons: $(debugfs -R 'ls /games-internal/roms/etk/bin' /tmp/vs2.ext4 2>/dev/null | grep -c '\.')"
echo "   resize marker:"; debugfs -R "stat /.please_resize_me" /tmp/vs2.ext4 2>/dev/null | grep -i Type | sed 's/^/   /'
echo "   audio_watchdog.sh mode (must be 755, the 203/EXEC fix):"; debugfs -R "stat /games-internal/roms/etk/scripts/audio_watchdog.sh" /tmp/vs2.ext4 2>/dev/null | grep -i Mode | sed 's/^/   /'
if debugfs -R "ls /" /tmp/vs2.ext4 2>/dev/null | grep -q '\.config'; then die ".config present — would block fs-resize!"; else echo "   .config absent ✅ (fs-resize will run)"; fi
# SYSTEM untouched
MC ::/SYSTEM /tmp/nS2; gunzip -c "$BASE_GZ" > /tmp/bref2.img
BR=$(parted -m /tmp/bref2.img unit B print 2>/dev/null | awk -F: '/^1:/{gsub("B","",$2);print $2}')
mcopy -i "/tmp/bref2.img@@$BR" ::/SYSTEM /tmp/bS2 2>/dev/null
[ "$(sha256sum /tmp/nS2|cut -d' ' -f1)" = "$(sha256sum /tmp/bS2|cut -d' ' -f1)" ] && echo "   SYSTEM identical ✅" || die "SYSTEM changed"
rm -f /tmp/bref2.img /tmp/bS2 /tmp/nS2 /tmp/vs2.ext4

say "7. compress + sha"
rm -f "$OUT_IMG.gz"; gzip -c "$WORK" > "$OUT_IMG.gz"
sha256sum "$OUT_IMG.gz" | tee "$OUT_IMG.gz.sha256"
echo "   raw=${NEW_BYTES}B gz=$(stat -c %s "$OUT_IMG.gz")B -> $OUT_IMG.gz"
say "DONE (labels: boot=$BOOT_LABEL storage=$STOR_LABEL; ETK staged; fs-resize will grow STORAGE)"
