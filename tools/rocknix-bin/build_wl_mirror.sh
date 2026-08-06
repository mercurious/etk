#!/usr/bin/env bash
# ==========================================================
# ETK — stage the wl-mirror binary for the ROCKNIX rig
# ==========================================================
# This is the STAGER, not the build recipe. The recipe is lane_wl_mirror.sh,
# which runs on any arm64 Docker host and never touches the rig.
#
# Default lane: build on **etk-cloud** (Oracle A1 aarch64, native docker) and
# stream the artifact back — see TRACK_MANUAL §8.5. The Air stays the staging
# host and the ONLY node that ever touches the rig.
#
#   ./build_wl_mirror.sh                 # cloud build of the pinned upstream
#   ./build_wl_mirror.sh --ref <sha>     # cloud build of a different upstream
#   ./build_wl_mirror.sh --local         # fall back to the Air's colima
#
# The recipe is piped to the build host over `ssh bash -s`, so the script that
# runs there is the one in this working tree — there is no second copy on the
# cloud node to fall out of date. (chiaki differs: its recipe lives in the fork
# because it belongs with the source it builds.)
#
# Outputs into this directory (install.sh pushes ./wl-mirror to $ETK_ROOT/tools/):
#   wl-mirror  wl-mirror.ldd  wl-mirror.commit  wl-mirror.buildinfo
# ==========================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LANE="$HERE/lane_wl_mirror.sh"

BUILD_HOST="${WLM_BUILD_HOST:-etk-cloud}"
REMOTE_OUT="${WLM_REMOTE_OUT:-/tmp/etk-wl-mirror-out}"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

MODE=cloud
REF=""
while [ $# -gt 0 ]; do
    case "$1" in
        --local) MODE=local; shift ;;
        --cloud) MODE=cloud; shift ;;
        --ref)   REF="${2:?--ref needs a value}"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -x "$LANE" ] || { echo "ERROR: missing recipe $LANE" >&2; exit 1; }

# ----------------------------------------------------------------------------
# LOCAL lane — the Air's colima. Fallback for when the cloud node is down or
# its ephemeral IP has moved.
# ----------------------------------------------------------------------------
if [ "$MODE" = local ]; then
    echo "== local build (colima) =="
    OUT_DIR="$HERE" ${REF:+WLM_REF="$REF"} "$LANE"

# ----------------------------------------------------------------------------
# CLOUD lane — pipe the recipe up, build there, stream the artifact back.
# ----------------------------------------------------------------------------
else
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$BUILD_HOST" true 2>/dev/null; then
        echo "ERROR: build host '$BUILD_HOST' unreachable." >&2
        echo "       etk-cloud's public IP is EPHEMERAL — it changes on stop/start." >&2
        echo "       Re-read it from the Oracle console and update 'Host etk-cloud'" >&2
        echo "       in ~/.ssh/config, or run with --local to build on the Air." >&2
        exit 1
    fi

    echo "== cloud build on $BUILD_HOST =="
    ssh -o BatchMode=yes "$BUILD_HOST" \
        "mkdir -p '$REMOTE_OUT' && OUT_DIR='$REMOTE_OUT' ${REF:+WLM_REF='$REF'} bash -s" < "$LANE"

    echo "== streaming artifacts back to the Air =="
    ssh -o BatchMode=yes "$BUILD_HOST" \
        "cd '$REMOTE_OUT' && tar -cf - wl-mirror wl-mirror.ldd wl-mirror.commit wl-mirror.buildinfo" \
        | tar -xf - -C "$HERE"
    chmod 0755 "$HERE/wl-mirror"
fi

echo "staged wl-mirror @ $(cat "$HERE/wl-mirror.commit")"

# ----------------------------------------------------------------------------
# Rig check — Air ONLY. The rig is never reached from the cloud node.
# wl-mirror never had this gate; chiaki has had it since day one.
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
