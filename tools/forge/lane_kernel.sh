#!/usr/bin/env bash
# ==========================================================
# forge lane: kernel — build_<lane>.sh in the rocknix-gtk-kernel-sid container
# ==========================================================
# Runs ON THE BUILD NODE, detached by forge.sh. Env:
#   KNAME   staged artifact name, KERNEL.rocknix-gtk-<8digits>-<n.n[.n]>
#           (version-only — law #8 — validated by forge.sh before launch)
#   RUNDIR  this run's directory
#   FORGE_KERNEL_BUILD  recipe selector: 712 (7.1.2 / 20260801, default) or
#           72 (7.2 / 20260901 rebase lane). Selects build_<sel>.sh,
#           out<sel>/ and config<sel>.drift — the recipes coexist so the
#           shipping kernel can always be reminted while the next one bakes.
#   FORGE_KERNEL_BASEDATE  chassis date for the 72 lane (default 20260901). 20261001+
#           is the qcom-abl era: the recipe builds a boot.img (gzip Image + DTBs,
#           cmdline baked incl. msm.context_keepalive=1, parity-gated against the
#           STOCK KERNEL) and THAT is the artifact — /flash/KERNEL is a boot.img now.
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

KLANE="${FORGE_KERNEL_BUILD:-712}"
BASEDATE="${FORGE_KERNEL_BASEDATE:-20260901}"
# same suffix rule as build_72.sh: the 20260901 lane keeps its historic paths
if [ "$KLANE" = 72 ] && [ "$BASEDATE" != 20260901 ]; then SFX="-$BASEDATE"; else SFX=""; fi
# KIT DTB SPLICE AT MINT (boot.img lane, 2026-10-08): the DTBs ride inside the
# boot.img, so the Flip 2 kit deltas (internal mic; USB-C VBUS where the stock DT
# lacks it) are spliced by etk's bin/etk_dtb_mic.py INSIDE the recipe. The
# splicer's canonical home is the etk repo; this lane stages the node's ~/etk
# copy into the container (never a copy kept in rocknix-gtk) and prints its
# sha + the checkout tip so a stale node kit is visible in the log.
ETK_KIT_DTB="${ETK_KIT_DTB:-1}"
ETK_INTERNAL_MIC="${ETK_INTERNAL_MIC:-1}"
# BOOT LOGO (2026-10-08): the recipe carries upstream ROCKNIX 187eb24f2e
# (CONFIG_TYPEC_MUX_GPIO_SBU m->y) as a config delta on the boot.img lane --
# stock 20261001 loses the splash because msm binds after load_splash. Default
# ON; =0 is the pure-parity A/B arm. The drift log gains exactly one line.
ETK_GPIO_SBU_BUILTIN="${ETK_GPIO_SBU_BUILTIN:-1}"
if [ "$KLANE" = 72 ] && [ "$BASEDATE" -ge 20261001 ] && [ "$ETK_KIT_DTB" = 1 ]; then
    SPL="$HOME/etk/bin/etk_dtb_mic.py"
    [ -f "$SPL" ] || { log "FATAL: $SPL missing on the node (pull ~/etk) -- or ETK_KIT_DTB=0 for a pure-parity mint"; exit 1; }
    docker cp "$SPL" rocknix-gtk-kernel-sid:/kernel/staging/etk_dtb_mic.py
    log "kit DTB splicer staged: etk_dtb_mic.py sha $(sha256sum "$SPL" | cut -c1-16) from ~/etk @ $(git -C "$HOME/etk" rev-parse --short HEAD 2>/dev/null || echo '?') (mic=$ETK_INTERNAL_MIC)"
fi
log "build_${KLANE}.sh BASEDATE=$BASEDATE (KCC=gcc-15, enforced in-recipe; kit DTB=$ETK_KIT_DTB; gpio-sbu-mux built-in=$ETK_GPIO_SBU_BUILTIN)"
docker exec rocknix-gtk-kernel-sid bash -lc "KCC=gcc-15 BASEDATE=$BASEDATE ETK_KIT_DTB=$ETK_KIT_DTB ETK_INTERNAL_MIC=$ETK_INTERNAL_MIC ETK_GPIO_SBU_BUILTIN=$ETK_GPIO_SBU_BUILTIN bash /work/scripts/build_${KLANE}.sh"

echo "=== config drift vs rig ground truth (expect INITRAMFS/FIRMWARE paths + toolchain-probe lines; + ONE TYPEC_MUX_GPIO_SBU m->y line when the boot-logo fix is on) ==="
docker exec rocknix-gtk-kernel-sid cat "/kernel/config${KLANE}${SFX}.drift" || true
echo "=== end drift ==="

REL=$(docker exec rocknix-gtk-kernel-sid cat "/kernel/out${KLANE}${SFX}/include/config/kernel.release")
log "kernel.release: $REL"
MODCOUNT=$(docker exec rocknix-gtk-kernel-sid sh -c "find /kernel/out${KLANE}${SFX} -name '*.ko' | wc -l")
log "modules built: $MODCOUNT"

mkdir -p "$HOME/rocknix-gtk/artifacts"
# qcom-abl era: ship the parity-gated boot.img (the recipe dies before here if the
# gate failed); GRUB era: the raw Image, as always.
ART="Image"
if [ "$KLANE" = 72 ] && [ "$BASEDATE" -ge 20261001 ]; then
    ART="boot.img"   # required, never inferred from a file that happens to exist
    docker exec rocknix-gtk-kernel-sid test -f "/kernel/out${KLANE}${SFX}/arch/arm64/boot/boot.img" \
        || { log "FATAL: BASEDATE=$BASEDATE is the boot.img lane but no boot.img was produced"; exit 1; }
fi
log "shipping arch/arm64/boot/$ART"
docker exec rocknix-gtk-kernel-sid cat "/kernel/out${KLANE}${SFX}/arch/arm64/boot/$ART" \
    > "$HOME/rocknix-gtk/artifacts/$KNAME"
( cd "$HOME/rocknix-gtk/artifacts" && sha256sum "$KNAME" > "$KNAME.sha256" )
SZ=$(stat -c %s "$HOME/rocknix-gtk/artifacts/$KNAME")
log "artifact: $KNAME ${SZ} B — $ART (Image ref: shipped -0.3.1 60,246,528 B; boot.img ref: stock 20261001 KERNEL 28,790,784 B)"
log "sha256  : $(cut -d' ' -f1 "$HOME/rocknix-gtk/artifacts/$KNAME.sha256")"
log "LANE OK — COLD-BOOT GATED: unvalidated until the operator boots it"
