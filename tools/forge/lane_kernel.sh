#!/usr/bin/env bash
# ==========================================================
# forge lane: kernel — build_712.sh in the rocknix-gtk-kernel-sid container
# ==========================================================
# Runs ON THE BUILD NODE, detached by forge.sh. Env:
#   KNAME   staged artifact name, KERNEL.rocknix-gtk-<8digits>-<n.n[.n]>
#           (version-only — law #8 — validated by forge.sh before launch)
#   RUNDIR  this run's directory
#
# Encoded traps (handoff §3.3):
#   * repo is mounted at /work, build tree at /kernel — not interchangeable
#   * the recipe is not executable in-container: invoke via `bash`
#   * KCC=gcc-15 is enforced IN the recipe (8eaf37a); gcc-14 black-screens
#     pre-userspace and sid's 16.x default is unvalidated — never override
#     casually
#   * the recipe does NOT package: Image is left in the build tree; naming
#     and staging is absorbed here (was a manual step during v0.8.4)
#   * the config drift diff vs rig ground truth is SURFACED, never swallowed
# The one artifact whose failure mode is a silent black screen: this lane can
# BUILD it; only the operator's cold boot can PASS it.
# ==========================================================
set -eu

log() { printf '[lane_kernel] %s\n' "$*"; }

log "build_712.sh (KCC=gcc-15, enforced in-recipe)"
docker exec rocknix-gtk-kernel-sid bash -lc 'KCC=gcc-15 bash /work/scripts/build_712.sh'

echo "=== config drift vs rig ground truth (expect INITRAMFS/FIRMWARE paths + toolchain-probe lines) ==="
docker exec rocknix-gtk-kernel-sid cat /kernel/config712.drift || true
echo "=== end drift ==="

REL=$(docker exec rocknix-gtk-kernel-sid cat /kernel/out712/include/config/kernel.release)
log "kernel.release: $REL"
MODCOUNT=$(docker exec rocknix-gtk-kernel-sid sh -c "find /kernel/out712 -name '*.ko' | wc -l")
log "modules built: $MODCOUNT"

mkdir -p "$HOME/rocknix-gtk/artifacts"
docker exec rocknix-gtk-kernel-sid cat /kernel/out712/arch/arm64/boot/Image \
    > "$HOME/rocknix-gtk/artifacts/$KNAME"
( cd "$HOME/rocknix-gtk/artifacts" && sha256sum "$KNAME" > "$KNAME.sha256" )
SZ=$(stat -c %s "$HOME/rocknix-gtk/artifacts/$KNAME")
log "artifact: $KNAME ${SZ} B (shipped -0.3.1 reference: 60,246,528 B)"
log "sha256  : $(cut -d' ' -f1 "$HOME/rocknix-gtk/artifacts/$KNAME.sha256")"
log "LANE OK — COLD-BOOT GATED: unvalidated until the operator boots it"
