#!/usr/bin/env bash
# ==========================================================
# ETK — stage the wl-mirror binary for the ROCKNIX rig
# ==========================================================
# This is the STAGER, not the build recipe. The recipe lives in the fork
# (wl-mirror-rocknix: scripts/build_wl_mirror.sh) so the cloud node and the
# Air run byte-identical builds from one source of truth — the chiaki model,
# adopted 2026-08-07 when the fork was created (before that the recipe was
# piped from this tree; see git history for lane_wl_mirror.sh).
#
# The fork is an UNPATCHED build mirror of Ferdi265/wl-mirror: its recipe
# fetches the pinned WLM_REF into the container, so the artifact is
# byte-identical to a pristine-upstream build of that ref. The fork exists
# for source surfacing/GPL self-evidence/upstream-rewrite insurance, and as
# the landing zone if a patch is ever needed.
#
# Default lane: build on **etk-cloud** (Oracle A1 aarch64, native docker) and
# stream the artifact back — see TRACK_MANUAL §A.1. The Air stays the staging
# host and the ONLY node that ever touches the rig.
#
#   ./build_wl_mirror.sh                 # cloud build of the pinned ref
#   ./build_wl_mirror.sh --ref <sha>     # cloud build of a different source ref
#   ./build_wl_mirror.sh --local         # fall back to the Air's colima
#
# Outputs into this directory (install.sh pushes ./wl-mirror to $ETK_ROOT/tools/):
#   wl-mirror  wl-mirror.ldd  wl-mirror.commit  wl-mirror.buildinfo
#
# CONTRACT: the cloud builds PUBLISHED refs only — that keeps wl-mirror.commit
# resolvable on github.com/mercurious/wl-mirror-rocknix, which is what makes a
# cloud artifact auditable at all. Push first.
# ==========================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

BUILD_HOST="${WLM_BUILD_HOST:-etk-cloud}"
LANE_DIR="${WLM_LANE_DIR:-wl-mirror-rocknix}"
FORK_URL="${WLM_FORK_URL:-https://github.com/mercurious/wl-mirror-rocknix.git}"
WLM_SRC="${WLM_SRC:-$HOME/wl-mirror-rocknix}"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

MODE=cloud
REF=""
while [ $# -gt 0 ]; do
    case "$1" in
        --local) MODE=local; shift ;;
        --cloud) MODE=cloud; shift ;;
        --ref)   REF="${2:?--ref needs a value}"; shift 2 ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# ----------------------------------------------------------------------------
# LOCAL lane — the Air's colima. Fallback for when the cloud node is down or
# its ephemeral IP has moved. Needs a local clone of the fork.
# ----------------------------------------------------------------------------
if [ "$MODE" = local ]; then
    if [ ! -x "$WLM_SRC/scripts/build_wl_mirror.sh" ]; then
        echo "ERROR: no build recipe at $WLM_SRC/scripts/build_wl_mirror.sh" >&2
        echo "       (git clone -b rocknix $FORK_URL, or set WLM_SRC)" >&2
        exit 1
    fi
    echo "== local build (colima) from $WLM_SRC =="
    OUT_DIR="$HERE" ${REF:+WLM_REF="$REF"} "$WLM_SRC/scripts/build_wl_mirror.sh"

# ----------------------------------------------------------------------------
# CLOUD lane — refresh the fork clone on the node, run its in-tree recipe,
# stream the artifacts back.
# ----------------------------------------------------------------------------
else
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$BUILD_HOST" true 2>/dev/null; then
        echo "ERROR: build host '$BUILD_HOST' unreachable." >&2
        echo "       etk-cloud's public IP is EPHEMERAL — it changes on stop/start." >&2
        echo "       Re-read it from the Oracle console and update 'Host etk-cloud'" >&2
        echo "       in ~/.ssh/config, or run with --local to build on the Air." >&2
        exit 1
    fi

    echo "== cloud build on $BUILD_HOST (recipe: fork rocknix head${REF:+, source ref $REF}) =="
    ssh -o BatchMode=yes "$BUILD_HOST" "
        set -euo pipefail
        if [ ! -d ~/$LANE_DIR ]; then
            git clone -q -b rocknix '$FORK_URL' ~/$LANE_DIR
        fi
        cd ~/$LANE_DIR
        git fetch -q origin
        git checkout -q rocknix && git reset -q --hard origin/rocknix
        OUT_DIR=~/$LANE_DIR/out ${REF:+WLM_REF='$REF'} ./scripts/build_wl_mirror.sh
    "

    echo "== streaming artifacts back to the Air =="
    ssh -o BatchMode=yes "$BUILD_HOST" \
        "cd ~/$LANE_DIR/out && tar -cf - wl-mirror wl-mirror.ldd wl-mirror.commit wl-mirror.buildinfo" \
        | tar -xf - -C "$HERE"
    chmod 0755 "$HERE/wl-mirror"
fi

echo "staged wl-mirror @ $(cat "$HERE/wl-mirror.commit")"

# ----------------------------------------------------------------------------
# Rig check — Air ONLY. The rig is never reached from the cloud node.
# ----------------------------------------------------------------------------
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$RIG_SSH" true 2>/dev/null; then
    MISSING=0
    while read -r so; do
        [ -n "$so" ] || continue
        if ! ssh -o BatchMode=yes "$RIG_SSH" "[ -e /usr/lib/$so ] || [ -e /lib/$so ]" 2>/dev/null; then
            echo "MISSING ON RIG: $so" >&2
            MISSING=1
        fi
    done < "$HERE/wl-mirror.ldd"
    if [ "$MISSING" -ne 0 ]; then
        echo "ERROR: rig is missing NEEDED libraries, do not deploy" >&2
        exit 1
    fi
    echo "rig library check: all NEEDED sonames present"
else
    echo "WARNING: rig $RIG_SSH unreachable, NEEDED-vs-rig check skipped" >&2
fi

echo "OK -> $HERE/wl-mirror"
