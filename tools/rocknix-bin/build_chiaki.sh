#!/usr/bin/env bash
# ==========================================================
# ETK — reproducible build of chiaki (Remote Play) for the ROCKNIX rig
# ==========================================================
# Builds the ETK chiaki fork's SDL2 frontend (rocknix/) as an aarch64/glibc
# binary that runs natively on ROCKNIX. This is the GT7 lane: PS4/PS5 Remote
# Play on the rig (MissionBrief: "GT7 is Remote Play (Chiaki)").
#
# WHY A CONTAINER: ROCKNIX is read-only and ships no toolchain. We build in
# an Ubuntu 24.04 *arm64* container (glibc 2.39). glibc is backward-compatible,
# so a binary linked against 2.39 runs on the rig's newer 2.41. Noble is the
# one image whose ffmpeg (6.1 -> libavcodec.so.60) matches the rig's ffmpeg
# 6.0.1 SONAME exactly; SDL2 2.30 and OpenSSL 3 line up the same way.
#
# libopus is NOT on the rig's loader path (/usr/lib/compat is box64-only),
# so opus is linked STATICALLY from noble's libopus.a.
#
# Runtime deps (all already present on ROCKNIX, verified 2026-07-29):
#   libSDL2-2.0.so.0  libavcodec.so.60  libavutil.so.58  libcrypto.so.3
#   libm/libpthread/libc (glibc)
#
# Source: the LOCAL fork at ~/chiaki (branch rocknix), mounted read-only —
# not cloned — because the frontend lives only there until it is published.
# Output: ./chiaki (committed; install.sh pushes it to $ETK_ROOT/tools/)
#         ./chiaki.commit (fork SHA, -dirty if uncommitted)
#         ./chiaki.ldd (NEEDED manifest, verified against the rig when reachable)
# ==========================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

CHIAKI_SRC="${CHIAKI_SRC:-$HOME/chiaki}"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

if [ ! -f "$CHIAKI_SRC/rocknix/CMakeLists.txt" ]; then
    echo "ERROR: no rocknix frontend at $CHIAKI_SRC (set CHIAKI_SRC)" >&2
    exit 1
fi

# provenance stamp (host-side git; the mount below excludes nothing, but the
# container image has no git and must not need it)
COMMIT="$(git -C "$CHIAKI_SRC" rev-parse HEAD)"
if ! git -C "$CHIAKI_SRC" diff --quiet HEAD 2>/dev/null; then
    COMMIT="$COMMIT-dirty"
fi
echo "$COMMIT" > "$HERE/chiaki.commit"

docker run --rm --platform linux/arm64 \
    -v "$CHIAKI_SRC:/src:ro" -v "$HERE:/out" ubuntu:24.04 bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential cmake ninja-build pkg-config \
    protobuf-compiler python3-protobuf python3-setuptools \
    libssl-dev libopus-dev libavcodec-dev libavutil-dev libsdl2-dev >/dev/null

  OPUS_A=/usr/lib/aarch64-linux-gnu/libopus.a
  if [ ! -f "$OPUS_A" ]; then
    echo "ERROR: static libopus.a missing from libopus-dev" >&2
    exit 1
  fi

  # writable copy: the nanopb generator writes nanopb_pb2.py into its own
  # source dir, which the read-only mount (deliberately) forbids
  mkdir -p /root/srcw
  tar -C /src --exclude=.git -cf - . | tar -C /root/srcw -xf -

  cmake -S /root/srcw -B /root/build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCHIAKI_ENABLE_GUI=OFF \
    -DCHIAKI_ENABLE_TESTS=OFF \
    -DCHIAKI_ENABLE_CLI=ON \
    -DCHIAKI_ENABLE_ROCKNIX=ON \
    -DCHIAKI_ENABLE_SETSU=OFF \
    -DCHIAKI_ENABLE_PI_DECODER=OFF \
    -DCHIAKI_ENABLE_FFMPEG_DECODER=ON \
    -DOpus_INCLUDE_DIRS=/usr/include \
    -DOpus_LIBRARIES="$OPUS_A;/usr/lib/aarch64-linux-gnu/libm.so"
  cmake --build /root/build

  BIN=/root/build/rocknix/chiaki
  strip "$BIN"

  # NEEDED manifest: this is the contract with the rig. Static opus means
  # libopus must NOT appear here.
  readelf -d "$BIN" | awk "/NEEDED/ {gsub(/[\[\]]/,\"\",\$5); print \$5}" > /out/chiaki.ldd
  if grep -q opus /out/chiaki.ldd; then
    echo "ERROR: libopus leaked into NEEDED (static link failed):" >&2
    cat /out/chiaki.ldd >&2
    exit 1
  fi

  # native container: the binary itself must run (catches ABI screwups early)
  "$BIN" --help >/dev/null

  install -m 0755 "$BIN" /out/chiaki
  echo "--- NEEDED ---"; cat /out/chiaki.ldd
'

echo "built chiaki @ $(cat "$HERE/chiaki.commit")"

# verify every NEEDED soname resolves on the rig (best effort: skip if off)
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$RIG_SSH" true 2>/dev/null; then
    MISSING=0
    while read -r so; do
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
