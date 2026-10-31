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
#   FORGE_KERNEL_BASEDATE  chassis date for the 72 lane (default 20260901). THREE
#           artifact formats, one recipe (rocknix-gtk UPSTREAM_20261101.md §1c):
#             20260901         GRUB era:     the raw Image
#             20261001         qcom-abl era: a boot.img (gzip Image + DTBs, cmdline baked
#                              incl. msm.context_keepalive=1, parity-gated against the
#                              STOCK KERNEL) — /flash/KERNEL is a boot.img
#             20261101+        BLS era:      the raw PE Image again (OpenABL + Boot Loader
#                              Spec entries; cmdline and DTB come from the entry the kit
#                              renders at DEPLOY, scripts/bls_gate.sh is the parity proof).
#                              The artifact is `Image`, required, never inferred.
#   FORGE_KCC  unset (default) = the recipe builds with STOCK's compiler major, read from the
#           ground-truth config (THE TOOLCHAIN LAW as a parity law, operator 2026-10-31:
#           gcc-15 through 20261001, gcc-16 from 20261101 -- stock is 16.2.0 there). Any
#           explicit value is a DELIBERATE, cold-boot-gated override, logged loud; part of
#           the lane fingerprint. Decision record: rocknix-gtk UPSTREAM_20261101.md §5.
#
# Encoded traps (handoff §3.3):
#   * repo is mounted at /work, build tree at /kernel — not interchangeable
#   * the recipe is not executable in-container: invoke via `bash`
#   * the compiler is enforced IN the recipe (stock_gcc.sh parity check); gcc-14
#     black-screened pre-userspace after a clean verify — never override casually
#     (FORGE_KCC exists for the deliberate case, not the casual one)
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
# FORENSICS hunt kernel (2026-10-11, GT5 op): rocknix-gtk build_72.sh ETK_FORENSICS=1 adds the
# wdt sysfs + pretimeout govs, log-only lockup detectors, pseudo-NMI and a ramoops console. 0 =
# every certified mint, unchanged. Never a release kernel.
ETK_FORENSICS="${ETK_FORENSICS:-0}"
# SD-SLOT POLL (2026-10-24, car8's dead card-detect switch): ETK_SD_POLL=1 adds the splicer's
# opt-in sdpoll delta (SD host cd-gpios -> broken-cd) to the kit DTBs. 0 = every certified
# mint, unchanged; a car with a working switch keeps instant hot-plug. Needs ETK_KIT_DTB=1.
ETK_SD_POLL="${ETK_SD_POLL:-0}"
if [ "$ETK_SD_POLL" = 1 ] && [ "$ETK_KIT_DTB" != 1 ]; then
    log "FATAL: ETK_SD_POLL=1 is a kit DTB delta -- it needs ETK_KIT_DTB=1"; exit 1
fi
# this lane ships from the host every run, the recipe does not: a stale node recipe would
# drop the knob SILENTLY and mint a kernel without the delta
if [ "$ETK_SD_POLL" = 1 ] && ! grep -q 'ETK_SD_POLL' "$HOME/rocknix-gtk/scripts/build_${KLANE}.sh" 2>/dev/null; then
    log "FATAL: ETK_SD_POLL=1 but the node's rocknix-gtk build_${KLANE}.sh does not know it (pull ~/rocknix-gtk)"; exit 1
fi
# BLS lane (20261101+): the splicer is staged for the GATE's dry-run on both Flip 2 trees
# (early warning that it still fits the new DTS), not for a splice -- the kit DTB is derived
# on the rig at deploy. The recipe FAILS a BLS mint that sets any kit knob to a non-default
# value (ETK_KIT_DTB=0 / ETK_INTERNAL_MIC=0 / ETK_SD_POLL=1), with the deploy-time pointer.
if [ "$KLANE" = 72 ] && [ "$BASEDATE" -ge 20261001 ] && [ "$ETK_KIT_DTB" = 1 ]; then
    SPL="$HOME/etk/bin/etk_dtb_mic.py"
    [ -f "$SPL" ] || { log "FATAL: $SPL missing on the node (pull ~/etk) -- or ETK_KIT_DTB=0 for a pure-parity mint"; exit 1; }
    docker cp "$SPL" rocknix-gtk-kernel-sid:/kernel/staging/etk_dtb_mic.py
    log "kit DTB splicer staged: etk_dtb_mic.py sha $(sha256sum "$SPL" | cut -c1-16) from ~/etk @ $(git -C "$HOME/etk" rev-parse --short HEAD 2>/dev/null || echo '?') (mic=$ETK_INTERNAL_MIC sdpoll=$ETK_SD_POLL)"
    if [ "$ETK_SD_POLL" = 1 ] && ! grep -q -- '--sd-poll' "$SPL"; then
        log "FATAL: ETK_SD_POLL=1 but the node's splicer has no --sd-poll (pull ~/etk)"; exit 1
    fi
fi
# THE TOOLCHAIN LAW (operator 2026-10-31: parity with STOCK's compiler major, was "gcc-15"):
# with FORGE_KCC unset the recipe resolves the compiler itself from the ground-truth config's
# CONFIG_GCC_VERSION (rocknix-gtk scripts/stock_gcc.sh: gcc-15 through 20261001, gcc-16 from
# 20261101) and refuses a major mismatch before the build. gcc-14 black-screened pre-userspace
# after a clean verify (2026-07-05); the first gcc-16 kernel is unvalidated until a car
# cold-boots it. An explicit FORGE_KCC is a deliberate override: the recipe skips its parity
# check, so the loud line below is the record. Only the operator's cold boot validates either.
KCC="${FORGE_KCC:-}"
if [ -z "$KCC" ]; then
    grep -q 'stock_gcc.sh' "$HOME/rocknix-gtk/scripts/build_${KLANE}.sh" 2>/dev/null \
        || { log "FATAL: the node's rocknix-gtk build_${KLANE}.sh predates the parity law (no stock_gcc.sh) -- pull ~/rocknix-gtk, or set FORGE_KCC deliberately"; exit 1; }
    log "toolchain: FORGE_KCC unset -> the recipe builds with STOCK's compiler major (parity law, stock_gcc.sh)"
    KCC_ENV=""
else
    log "TOOLCHAIN OVERRIDE: FORGE_KCC=$KCC -- the recipe's parity check is skipped. A clean build and a clean gate prove nothing here; the failure mode is a silent pre-userspace black screen. Deliberate choice, cold-boot gated."
    KCC_ENV="KCC=$KCC "
fi
# BLS by date, or by the rehearsal knob (FORGE_KERNEL_BLS=1: a nightly staging dated before the tag,
# e.g. nightly-20261030, is BLS-format; the recipe takes the same BLS=1 override).
BLS_ENV=""
if [ "$KLANE" = 72 ] && [ "$BASEDATE" -ge 20261101 ]; then IS_BLS=1
elif [ "$KLANE" = 72 ] && [ "${FORGE_KERNEL_BLS:-}" = 1 ]; then IS_BLS=1; BLS_ENV="BLS=1 "; log "BLS REHEARSAL: FORGE_KERNEL_BLS=1 on BASEDATE=$BASEDATE (dated before the 20261101 tag) -- the recipe runs its BLS branch on this nightly staging"
else IS_BLS=0; fi
log "build_${KLANE}.sh BASEDATE=$BASEDATE (KCC=${KCC:-stock-parity}; bls=$IS_BLS; kit DTB=$ETK_KIT_DTB; gpio-sbu-mux built-in=$ETK_GPIO_SBU_BUILTIN; forensics=$ETK_FORENSICS; sd-poll=$ETK_SD_POLL)"
docker exec rocknix-gtk-kernel-sid bash -lc "${KCC_ENV}${BLS_ENV}BASEDATE=$BASEDATE ETK_KIT_DTB=$ETK_KIT_DTB ETK_INTERNAL_MIC=$ETK_INTERNAL_MIC ETK_GPIO_SBU_BUILTIN=$ETK_GPIO_SBU_BUILTIN ETK_FORENSICS=$ETK_FORENSICS ETK_SD_POLL=$ETK_SD_POLL bash /work/scripts/build_${KLANE}.sh"

if [ "$IS_BLS" = 1 ]; then
    echo "=== config drift vs ground truth (BLS lane: expect INITRAMFS/FIRMWARE paths + toolchain-probe lines ONLY -- stock ships TYPEC_MUX_GPIO_SBU=y, that line must be GONE; bls_gate.sh already failed the build if it is not) ==="
else
    echo "=== config drift vs rig ground truth (expect INITRAMFS/FIRMWARE paths + toolchain-probe lines; + ONE TYPEC_MUX_GPIO_SBU m->y line when the boot-logo fix is on) ==="
fi
docker exec rocknix-gtk-kernel-sid cat "/kernel/config${KLANE}${SFX}.drift" || true
echo "=== end drift ==="

REL=$(docker exec rocknix-gtk-kernel-sid cat "/kernel/out${KLANE}${SFX}/include/config/kernel.release")
log "kernel.release: $REL"
# modules.order = what ships; a find for *.ko also counts stale objects from an
# incremental rebuild (the 0.6.3 remint, 2026-10-08).
MODCOUNT=$(docker exec rocknix-gtk-kernel-sid sh -c "wc -l < /kernel/out${KLANE}${SFX}/modules.order")
log "modules built: $MODCOUNT (modules.order)"

mkdir -p "$HOME/rocknix-gtk/artifacts"
# The artifact is REQUIRED by lane, never inferred from a file that happens to exist:
#   GRUB era (20260901):          the raw Image
#   qcom-abl era (20261001 only): the parity-gated boot.img (the recipe dies before here
#                                 if bootimg_parity.py failed)
#   BLS era (20261101+):          the raw PE Image again, after scripts/bls_gate.sh (the
#                                 recipe dies before here if the gate failed; the gate's
#                                 cmdline_extra record is the proof it ran). A boot.img in
#                                 this slot would be a dark boot under OpenABL.
ART="Image"
if [ "$IS_BLS" = 1 ]; then
    docker exec rocknix-gtk-kernel-sid test -f "/kernel/cmdline_extra${SFX}.txt" \
        || { log "FATAL: BASEDATE=$BASEDATE is the BLS lane but the recipe left no cmdline_extra${SFX}.txt -- bls_gate.sh did not run (node's rocknix-gtk predates the BLS branch? pull it)"; exit 1; }
    docker exec rocknix-gtk-kernel-sid test -f "/kernel/out${KLANE}${SFX}/arch/arm64/boot/Image" \
        || { log "FATAL: BASEDATE=$BASEDATE is the BLS lane but no Image was produced"; exit 1; }
    MAGIC=$(docker exec rocknix-gtk-kernel-sid head -c 2 "/kernel/out${KLANE}${SFX}/arch/arm64/boot/Image")
    [ "$MAGIC" = MZ ] || { log "FATAL: arch/arm64/boot/Image does not start with MZ (not a PE Image; got '$MAGIC')"; exit 1; }
    log "BLS lane: cmdline_extra (rendered into the entry at DEPLOY, not baked): $(docker exec rocknix-gtk-kernel-sid cat "/kernel/cmdline_extra${SFX}.txt")"
elif [ "$KLANE" = 72 ] && [ "$BASEDATE" -ge 20261001 ]; then
    ART="boot.img"
    docker exec rocknix-gtk-kernel-sid test -f "/kernel/out${KLANE}${SFX}/arch/arm64/boot/boot.img" \
        || { log "FATAL: BASEDATE=$BASEDATE is the boot.img lane but no boot.img was produced"; exit 1; }
fi
log "shipping arch/arm64/boot/$ART"
docker exec rocknix-gtk-kernel-sid cat "/kernel/out${KLANE}${SFX}/arch/arm64/boot/$ART" \
    > "$HOME/rocknix-gtk/artifacts/$KNAME"
( cd "$HOME/rocknix-gtk/artifacts" && sha256sum "$KNAME" > "$KNAME.sha256" )
SZ=$(stat -c %s "$HOME/rocknix-gtk/artifacts/$KNAME")
log "artifact: $KNAME ${SZ} B — $ART (Image refs: shipped -0.3.1 60,246,528 B, stock 20261101 KERNEL 59,986,432 B; boot.img ref: stock 20261001 KERNEL 28,790,784 B)"
log "sha256  : $(cut -d' ' -f1 "$HOME/rocknix-gtk/artifacts/$KNAME.sha256")"
log "LANE OK — COLD-BOOT GATED: unvalidated until the operator boots it"
