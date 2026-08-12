#!/usr/bin/env bash
# ==========================================================
# ETK — stage the chiaki (Remote Play) binary for the ROCKNIX rig
# ==========================================================
# This is the STAGER, not the build recipe. The recipe lives in the fork
# (chiaki-rocknix: scripts/build_chiaki.sh) so the cloud node and the Air run
# byte-identical builds from one source of truth.
#
# Default lane: build on **etk-cloud** (Oracle A1 aarch64, native docker) and
# stream the artifact back — see TRACK_MANUAL §A.1. The Air stays the staging
# host and the ONLY node that ever touches the rig.
#
#   ./build_chiaki.sh                 # cloud build of the published rocknix head
#   ./build_chiaki.sh --ref <sha>     # cloud build of a specific published commit
#   ./build_chiaki.sh --local         # fall back to the Air's colima
#
# Outputs into this directory (install.sh pushes ./chiaki to $ETK_ROOT/tools/):
#   chiaki  chiaki.ldd  chiaki.commit  chiaki.buildinfo
#
# CONTRACT: the cloud builds PUBLISHED commits only. That keeps chiaki.commit
# resolvable on github.com/mercurious/chiaki-rocknix — a release requirement,
# and the thing that makes a cloud artifact auditable at all. Push first.
# ==========================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

BUILD_HOST="${CHIAKI_BUILD_HOST:-etk-cloud}"
LANE_DIR="${CHIAKI_LANE_DIR:-chiaki-rocknix}"
FORK_URL="${CHIAKI_FORK_URL:-https://github.com/mercurious/chiaki-rocknix.git}"
CHIAKI_SRC="${CHIAKI_SRC:-$HOME/chiaki}"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

MODE=cloud
REF=rocknix
while [ $# -gt 0 ]; do
    case "$1" in
        --local) MODE=local; shift ;;
        --cloud) MODE=cloud; shift ;;
        --ref)   REF="${2:?--ref needs a value}"; shift 2 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# ----------------------------------------------------------------------------
# LOCAL lane — the Air's colima. Kept as the fallback for when the cloud node
# is down or its ephemeral IP has moved.
# ----------------------------------------------------------------------------
if [ "$MODE" = local ]; then
    if [ ! -x "$CHIAKI_SRC/scripts/build_chiaki.sh" ]; then
        echo "ERROR: no build recipe at $CHIAKI_SRC/scripts/build_chiaki.sh" >&2
        echo "       (set CHIAKI_SRC to the chiaki-rocknix checkout)" >&2
        exit 1
    fi
    echo "== local build (colima) from $CHIAKI_SRC =="
    OUT_DIR="$HERE" "$CHIAKI_SRC/scripts/build_chiaki.sh"

# ----------------------------------------------------------------------------
# CLOUD lane — build on etk-cloud, stream the artifact back.
# ----------------------------------------------------------------------------
else
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$BUILD_HOST" true 2>/dev/null; then
        echo "ERROR: build host '$BUILD_HOST' unreachable." >&2
        echo "       etk-cloud's public IP is EPHEMERAL — it changes on stop/start." >&2
        echo "       Re-read it from the Oracle console and update 'Host etk-cloud'" >&2
        echo "       in ~/.ssh/config, or run with --local to build on the Air." >&2
        exit 1
    fi

    echo "== cloud build on $BUILD_HOST (ref: $REF) =="
    ssh -o BatchMode=yes "$BUILD_HOST" "
        set -euo pipefail
        if [ ! -d ~/$LANE_DIR ]; then
            git clone -q -b rocknix --recurse-submodules '$FORK_URL' ~/$LANE_DIR
        fi
        cd ~/$LANE_DIR
        git fetch -q --all --tags
        git checkout -q '$REF' 2>/dev/null || git checkout -q \"origin/$REF\"
        git submodule update -q --init --recursive
        ./scripts/build_chiaki.sh
    "

    echo "== streaming artifacts back to the Air =="
    ssh -o BatchMode=yes "$BUILD_HOST" \
        "cd ~/$LANE_DIR/out && tar -cf - chiaki chiaki.ldd chiaki.commit chiaki.buildinfo" \
        | tar -xf - -C "$HERE"
    chmod 0755 "$HERE/chiaki"
fi

COMMIT="$(cat "$HERE/chiaki.commit")"
echo "staged chiaki @ $COMMIT"

# Loud warning if the operator's local checkout is ahead of what was built —
# a cloud build of the published head is NOT your uncommitted work.
if [ -d "$CHIAKI_SRC/.git" ] && [ "$MODE" = cloud ]; then
    LOCAL_HEAD="$(git -C "$CHIAKI_SRC" rev-parse HEAD 2>/dev/null || echo '')"
    if [ -n "$LOCAL_HEAD" ] && [ "$LOCAL_HEAD" != "$COMMIT" ]; then
        echo "WARNING: your local $CHIAKI_SRC is at $LOCAL_HEAD" >&2
        echo "         but the cloud built  $COMMIT" >&2
        echo "         Push your branch (or pass --ref) if you meant to build local work." >&2
    fi
fi

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
    done < "$HERE/chiaki.ldd"
    if [ "$MISSING" -ne 0 ]; then
        echo "ERROR: rig is missing NEEDED libraries, do not deploy" >&2
        exit 1
    fi
    echo "rig library check: all NEEDED sonames present"
else
    echo "WARNING: rig $RIG_SSH unreachable, NEEDED-vs-rig check skipped" >&2
fi

echo "OK -> $HERE/chiaki"
