#!/usr/bin/env bash
# ==========================================================
# ETK — reproducible build of wl-mirror for the ROCKNIX rig
# ==========================================================
# Builds the wl-mirror screen-mirror client as an aarch64 / glibc binary
# that runs natively on ROCKNIX (used by bin/dpmirror_d.sh to duplicate the
# internal panel onto the external DisplayPort for capture-card / OBS).
#
# WHY A CONTAINER: ROCKNIX is read-only and ships no toolchain. We build in
# an Ubuntu 24.04 *arm64* container (glibc 2.39). glibc is backward-compatible,
# so a binary linked against 2.39 runs on the rig's newer 2.41. On Apple
# Silicon the arm64 container is native (fast); on x86 hosts Docker uses qemu.
#
# Runtime deps (all already present on ROCKNIX, verified 2026-06-27):
#   libwayland-egl.so.1  libEGL.so.1  libGLESv2.so.2  libwayland-client.so.0  libc.so.6
#
# Pinned upstream: github.com/Ferdi265/wl-mirror  (see wl-mirror.commit)
# Output: ./wl-mirror  (committed; install.sh pushes it to $ETK_ROOT/tools/)
# ==========================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

docker run --rm --platform linux/arm64 -v "$HERE:/out" ubuntu:24.04 bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential cmake pkg-config git ca-certificates scdoc \
    libwayland-dev libwayland-bin wayland-protocols libegl-dev libgles-dev libgbm-dev libdrm-dev >/dev/null
  cd /root
  git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/Ferdi265/wl-mirror.git
  cd wl-mirror
  git rev-parse HEAD > /out/wl-mirror.commit
  cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build build >/dev/null
  install -m 0755 build/wl-mirror /out/wl-mirror
  aarch64-linux-gnu-strip /out/wl-mirror 2>/dev/null || strip /out/wl-mirror || true
  echo "built wl-mirror @ $(cat /out/wl-mirror.commit)"
'
echo "OK -> $HERE/wl-mirror"
