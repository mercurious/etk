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

# EXISTING IS NOT THE SAME AS CORRECT. This lane used to check only that each
# input was present, which is how it nearly baked the wrong Vulkan driver on
# 2026-08-10: the node held a re-minted etk_turnip_rocknix_26.1.6_gtk_0.7.so
# (a83c2306, 17,136,464 B) while the release had decided to ship the PUBLISHED
# build of that same name (8a16efa6, 17,136,072 B). Identical filename,
# different bytes, and a card would have gone out with an uncertified driver
# inside it. The kernel is the same hazard with sharper teeth — -0.3.1 and
# -0.4.1 are byte-for-byte the same SIZE, so nothing but a hash separates them.
# The node is a second machine with its own copies; it drifts. Verify.
_verify() {  # <label> <path> <expected-sha or empty>
    [ -n "$3" ] || { log "WARN: no pinned sha for $1 — cannot verify $2"; return 0; }
    _got=$(sha256sum "$2" | cut -d' ' -f1)
    [ "$_got" = "$3" ] && { log "verified $1: ${_got%"${_got#????????}"}… matches the manifest"; return 0; }
    log "FATAL: $1 on this node is NOT the artifact the release pins."
    log "       node   $_got  ($(stat -c%s "$2") B)  $2"
    log "       pinned $3"
    log "       config/gtk_stack.json is the authority. Copy the pinned build up"
    log "       (scp from the staging host) and re-run — do NOT bake this."
    exit 1
}
_verify kernel "$K" "${KSHA:-}"
_verify rpcs3  "$A" "${ASHA:-}"
_verify turnip "$T" "${TSHA:-}"

# THE WHOLE CATALOG GETS BAKED, SO THE WHOLE CATALOG GETS VERIFIED.
# TCAT is "name:sha name:sha ..." for every CERTIFIED_BUILDS driver. The card's
# DRIVER tab enumerates what lands in $SEED/turnip/drivers, so shipping only the
# default left a flashed card with a one-entry chooser — no downgrade path, no
# pre-release arm. Each driver is checked here because the manifest pins only
# the primary; the rest are pinned by install.sh's driver_sha().
TCAT_PATHS=""
for _e in ${TCAT:-}; do
    _nm=${_e%%:*}; _sh=${_e##*:}
    _p="$HOME/etk/drivers/$_nm"
    [ -f "$_p" ] || { log "FATAL: catalog driver not staged on this node: $_p"; exit 1; }
    _got=$(sha256sum "$_p" | cut -d' ' -f1)
    if [ "$_got" != "$_sh" ]; then
        log "FATAL: catalog driver $_nm does not match its pin."
        log "       node   $_got"
        log "       pinned $_sh"
        exit 1
    fi
    log "verified catalog: $_nm"
    TCAT_PATHS="$TCAT_PATHS /etk/drivers/$_nm"
done
[ -n "$TCAT_PATHS" ] || TCAT_PATHS="/etk/drivers/$TNAME"

# THE MIDDLEWARE IS AN INPUT TOO, AND IT TRAVELS BY GIT.
# build_gtk_image_v2.sh rsyncs $REPO (this node's ~/etk checkout) into the
# card's $ETK_ROOT and renders the boot-identity unit from $REPO's APP_VERSION.
# So the largest, most-changed part of a release reaches the card through a
# git clone on a SECOND MACHINE — with, until 2026-08-10, no gate whatsoever.
# The three binaries were sha-pinned and verified above while the middleware
# beside them was whatever the node last pulled. It shipped a 0.8.4 card out of
# a 0.8.5 cut: right kernel, right driver, right emulator, wrong kit, and the
# operator found it by reading the boot string on a freshly etched card.
# A stale checkout is not a build failure, so nothing downstream can catch it.
NODE_VER=$(grep -m1 '^APP_VERSION' "$HOME/etk/bin/etk_pitstop.py" 2>/dev/null | cut -d'"' -f2)
NODE_HEAD=$(git -C "$HOME/etk" rev-parse --short HEAD 2>/dev/null || echo unknown)
log "node kit: APP_VERSION=${NODE_VER:-?} HEAD=$NODE_HEAD"
if [ -n "${EXPECT_VER:-}" ] && [ "$NODE_VER" != "$EXPECT_VER" ]; then
    log "FATAL: this node's ETK checkout is $NODE_VER but the release is $EXPECT_VER."
    log "       The card's ENTIRE middleware is rsynced from that checkout, so"
    log "       baking now ships the wrong kit under the right version's name."
    log "       Fix on the node, then re-run:"
    log "         git -C ~/etk fetch origin && git -C ~/etk checkout ${EXPECT_HEAD:-origin/main}"
    exit 1
fi
if [ -n "${EXPECT_HEAD:-}" ] && [ "$NODE_HEAD" != "$EXPECT_HEAD" ]; then
    log "FATAL: node ETK checkout is at $NODE_HEAD, the release cut is $EXPECT_HEAD."
    log "       Same version string can hide many commits — the middleware would"
    log "       be stale even though APP_VERSION matches. Fix on the node:"
    log "         git -C ~/etk fetch origin && git -C ~/etk checkout $EXPECT_HEAD"
    exit 1
fi

log "inputs: $KNAME / $ANAME / $TNAME / base-$BASEDATE"

docker exec etk-imgtool bash -lc "
    BASE_GZ=/work/ROCKNIX-SM8250.aarch64-$BASEDATE.img.gz \
    KERNEL=/rocknix-gtk/artifacts/$KNAME \
    APPIMAGE=/etk/emulators/$ANAME \
    TURNIP_SO=/etk/drivers/$TNAME \
    TURNIP_CATALOG=\"$(echo $TCAT_PATHS)\" \
    HOOK_SCRIPT=/work/build/mount-storage.sh \
    SEED_CONFIG=/work/build/seed_config \
    BOOT_LABEL=ROCKNIX-GTK STOR_LABEL=GTKSTOR \
    OUT_IMG=/work/$OUTIMG \
    bash /work/build/build_gtk_image_v2.sh"

# --- independent artifact-level verify (the raw spliced image survives in the
# --- persistent container's /tmp; the recipe's own §6 gates already passed) ---
log "artifact verify: labels + baked kernel, read back from the image"
# -e KSHA: the heredoc is single-quoted (correct — the recipe must not be
# expanded by the outer shell) and `docker exec` does NOT inherit the host
# environment, so the expected hash has to be handed in explicitly. Without
# this the check inside would read an empty KSHA and silently skip itself,
# which is the exact failure mode this block was just rewritten to kill.
docker exec -i -e KSHA="${KSHA:-}" etk-imgtool bash -s <<'VERIFY'
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
# READ THE BAKED KERNEL BACK AND HASH IT. The previous line here was
#   strings /tmp/kchk.img | grep 'Linux version' | head -1
# which on 2026-08-10 printed `strings: command not found` and the lane still
# declared ARTIFACT VERIFY OK. Two faults at once: `strings` is not in this
# container, and `set -e` only inspects the LAST element of a pipeline (`head`,
# which succeeded), so the failure was masked. The lane advertised "labels +
# baked kernel" while only the labels were ever checked.
# A hash is decisive where a version string was only indicative — every kernel
# in the -0.4.1.N ladder prints the SAME "Linux version 7.1.2" line, so even a
# working strings check could not have told them apart. sha256sum is present.
mcopy -i "$W@@$OFF" ::/KERNEL.gtktest /tmp/kchk.img 2>/dev/null \
    || { echo "VERIFY FAIL: could not read KERNEL.gtktest back out of the image"; exit 1; }
BAKED=$(sha256sum /tmp/kchk.img | cut -d' ' -f1)
echo "artifact kernel sha: $BAKED"
if [ -n "${KSHA:-}" ] && [ "$BAKED" != "$KSHA" ]; then
    echo "VERIFY FAIL: baked kernel is not the pinned one"
    echo "  baked  $BAKED"
    echo "  pinned $KSHA"
    exit 1
fi
[ -n "${KSHA:-}" ] || echo "VERIFY WARN: no KSHA passed — baked kernel unverified"
rm -f /tmp/kchk.img /tmp/vchk.ext4
echo "ARTIFACT VERIFY OK"
VERIFY

log "artifact: $OUTIMG.gz $(stat -c %s "$HOME/etk/os-install/$OUTIMG.gz") B"
log "sha256  : $(cut -d' ' -f1 "$HOME/etk/os-install/$OUTIMG.gz.sha256")"
log "LANE OK"
