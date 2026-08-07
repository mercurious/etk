#!/usr/bin/env bash
# ==========================================================
# forge lane: GTK image — bake kernel+emulator+driver into the SD card image
# ==========================================================
# Runs ON THE BUILD NODE, detached by forge.sh, ALWAYS LAST. Env:
#   KNAME/ANAME/TNAME  the three baked artifact names (staged node-side by
#                      their producer lanes — or pre-existing from a prior run)
#   BASEDATE           base ROCKNIX release date (input img.gz + output name)
#   OUTIMG             output image name (…img; the recipe gzips it)
#   RUNDIR             this run's directory
#
# Encoded traps (handoff §3.4 + v0.8.4 postmortem):
#   * the recipe's DEFAULTS are not what ships — the shipped invocation
#     passes UNIQUE labels + hook + seed (FLASH_AND_TEST.md); baked in here
#   * inputs are ASSERTED, not assumed (trap #2: gitignored payloads never
#     travel via git pull — the v0.8.4 image build died on seed_config)
#   * VERIFY FROM THE ARTIFACT, not the log: labels + baked kernel re-read
#     from the built image after the recipe's own gates pass
# ==========================================================
set -eu

log() { printf '[lane_image] %s\n' "$*"; }

K="$HOME/rocknix-gtk/artifacts/$KNAME"
A="$HOME/etk/emulators/$ANAME"
T="$HOME/etk/drivers/$TNAME"
B="$HOME/etk/os-install/ROCKNIX-SM8250.aarch64-$BASEDATE.img.gz"
for f in "$K" "$A" "$T" "$B"; do
    [ -f "$f" ] || { log "FATAL: missing input $f (stage it — gitignored payloads do not git-pull)"; exit 1; }
done
log "inputs: $KNAME / $ANAME / $TNAME / base-$BASEDATE"

docker exec etk-imgtool bash -lc "
    BASE_GZ=/work/ROCKNIX-SM8250.aarch64-$BASEDATE.img.gz \
    KERNEL=/rocknix-gtk/artifacts/$KNAME \
    APPIMAGE=/etk/emulators/$ANAME \
    TURNIP_SO=/etk/drivers/$TNAME \
    HOOK_SCRIPT=/work/build/mount-storage.sh \
    SEED_CONFIG=/work/build/seed_config \
    BOOT_LABEL=ROCKNIX-GTK STOR_LABEL=GTKSTOR \
    OUT_IMG=/work/$OUTIMG \
    bash /work/build/build_gtk_image_v2.sh"

# --- independent artifact-level verify (the raw spliced image survives in the
# --- persistent container's /tmp; the recipe's own §6 gates already passed) ---
log "artifact verify: labels + baked kernel, read back from the image"
docker exec -i etk-imgtool bash -s <<'VERIFY'
set -eu
W=/tmp/work2.img
[ -f "$W" ] || { echo "VERIFY FAIL: $W gone (container restarted mid-lane?)"; exit 1; }
OFF=$(parted -m "$W" unit B print 2>/dev/null | awk -F: '/^1:/{gsub("B","",$2);print $2}')
SOFF=$(parted -m "$W" unit B print 2>/dev/null | awk -F: '/^2:/{gsub("B","",$2);print $2}')
FATLBL=$(mdir -i "$W@@$OFF" ::/ 2>/dev/null | awk '/Volume in drive/{print $NF}')
echo "artifact FAT label : $FATLBL"
[ "$FATLBL" = "ROCKNIX-GTK" ] || { echo "VERIFY FAIL: FAT label != ROCKNIX-GTK"; exit 1; }
dd if="$W" of=/tmp/vchk.ext4 bs=1M skip=$(( SOFF / 1048576 )) count=64 status=none
ELBL=$(dumpe2fs -h /tmp/vchk.ext4 2>/dev/null | awk -F: '/volume name/{gsub(/ /,"",$2);print $2}')
echo "artifact ext4 label: $ELBL"
[ "$ELBL" = "GTKSTOR" ] || { echo "VERIFY FAIL: ext4 label != GTKSTOR"; exit 1; }
mcopy -i "$W@@$OFF" ::/KERNEL.gtktest /tmp/kchk.img 2>/dev/null
strings /tmp/kchk.img | grep 'Linux version' | head -1
rm -f /tmp/kchk.img /tmp/vchk.ext4
echo "ARTIFACT VERIFY OK"
VERIFY

log "artifact: $OUTIMG.gz $(stat -c %s "$HOME/etk/os-install/$OUTIMG.gz") B"
log "sha256  : $(cut -d' ' -f1 "$HOME/etk/os-install/$OUTIMG.gz.sha256")"
log "LANE OK"
