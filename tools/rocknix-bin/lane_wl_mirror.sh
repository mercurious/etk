#!/usr/bin/env bash
# ==========================================================
# ETK lane recipe — wl-mirror for the ROCKNIX rig
# ==========================================================
# Builds the wl-mirror screen-mirror client as an aarch64/glibc binary that
# runs natively on ROCKNIX (bin/dpmirror_d.sh duplicates the internal panel
# onto the external DisplayPort for capture-card / OBS).
#
# This is the RECIPE. It runs on any arm64 Docker host — etk-cloud natively,
# the Air via colima — and NEVER touches the rig. The device-side NEEDED check
# belongs to the staging host (build_wl_mirror.sh), because the rig is only
# ever reached from the LAN-local Mac.
#
# Unlike chiaki, wl-mirror has no ETK fork: upstream is somebody else's repo,
# so the recipe lives here in etk and the stager pipes it to the build host
# over `ssh bash -s`. Nothing is left lying around on the cloud node to drift.
#
# WHY A CONTAINER: ROCKNIX is read-only and ships no toolchain. We build in an
# Ubuntu 24.04 *arm64* container (glibc 2.39). glibc is backward-compatible, so
# a binary linked against 2.39 runs on the rig's 2.41.
#
# PINNING — both ends, on purpose:
#   * WLM_REF pins UPSTREAM. This script used to `git clone --depth 1` master
#     and then write whatever HEAD it happened to land on into wl-mirror.commit.
#     That file documented the drift; it never prevented it. A rebuild could
#     silently ship a different upstream than the validated one.
#   * BASE_IMAGE pins the toolchain by digest. `ubuntu:24.04` is a moving tag —
#     on 2026-08-05 two ETK build hosts held different images behind it.
#
# Outputs (into $OUT_DIR, default the script's own directory):
#   wl-mirror            stripped aarch64 binary
#   wl-mirror.ldd        NEEDED manifest — the contract with the device
#   wl-mirror.commit     upstream SHA actually built
#   wl-mirror.buildinfo  provenance: base digest, toolchain + library versions
# ==========================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${OUT_DIR:-$HERE}"

WLM_URL="${WLM_URL:-https://github.com/Ferdi265/wl-mirror.git}"
# Validated upstream (in service since 2026-07-24; still upstream HEAD 2026-08-05).
WLM_REF="${WLM_REF:-428b5079fbf19c5fcbecf60177024cbc2f63190d}"
# Validated noble (etk-cloud, 2026-08-05). Pinned by digest on purpose.
BASE_IMAGE="${BASE_IMAGE:-ubuntu@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea}"

# The device contract, read off the in-service binary (2026-08-05). ROCKNIX
# ships all six. Anything else means the binary will not load on the rig.
EXPECTED_NEEDED="ld-linux-aarch64.so.1
libEGL.so.1
libGLESv2.so.2
libc.so.6
libwayland-client.so.0
libwayland-egl.so.1"

mkdir -p "$OUT_DIR"
echo "building wl-mirror @ $WLM_REF"
echo "base image: $BASE_IMAGE"

docker run --rm --platform linux/arm64 \
    -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
    -e "WLM_URL=$WLM_URL" -e "WLM_REF=$WLM_REF" \
    -v "$OUT_DIR:/out" "$BASE_IMAGE" bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential cmake pkg-config git ca-certificates scdoc \
    binutils libwayland-dev libwayland-bin wayland-protocols \
    libegl-dev libgles-dev libgbm-dev libdrm-dev >/dev/null

  # Fetch the PINNED commit, not a branch tip. GitHub serves arbitrary reachable
  # SHAs, so this stays a shallow single-commit fetch.
  mkdir -p /root/wl-mirror && cd /root/wl-mirror
  git init -q
  git remote add origin "$WLM_URL"
  git fetch -q --depth 1 origin "$WLM_REF"
  git checkout -q FETCH_HEAD
  git submodule update -q --init --recursive --depth 1
  git rev-parse HEAD > /out/wl-mirror.commit

  cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build build >/dev/null

  BIN=build/wl-mirror
  strip "$BIN"

  readelf -d "$BIN" | awk "/NEEDED/ {gsub(/[][]/,\"\",\$5); print \$5}" > /out/wl-mirror.ldd

  # Smoke test. wl-mirror is a Wayland client with no compositor in here, so a
  # non-zero exit is expected and fine; what we are ruling out is a loader
  # failure (missing/soname-mismatched library), which prints on stderr.
  SMOKE="$("$BIN" --help 2>&1 || true)"
  if echo "$SMOKE" | grep -qiE "error while loading shared libraries|cannot open shared object"; then
    echo "ERROR: dynamic loader failed inside the build container:" >&2
    echo "$SMOKE" >&2
    exit 1
  fi

  install -m 0755 "$BIN" /out/wl-mirror

  {
    echo "built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "base_os=$(grep ^VERSION= /etc/os-release | cut -d= -f2- | tr -d \")"
    echo "gcc=$(gcc -dumpfullversion 2>/dev/null || gcc -dumpversion)"
    for p in libwayland-dev wayland-protocols libegl-dev libgles-dev libdrm-dev; do
      echo "$p=$(dpkg-query -W -f=\${Version} $p)"
    done
  } > /out/wl-mirror.buildinfo

  # The container is root; hand the artifacts back to the invoking user so the
  # host-side gates can append. (No-op/ignored on colima virtiofs mounts.)
  chown "$HOST_UID:$HOST_GID" /out/wl-mirror /out/wl-mirror.ldd \
        /out/wl-mirror.commit /out/wl-mirror.buildinfo 2>/dev/null || true
'

# ---- gates (host side, so a container that lies still gets caught) ----
BUILT_REF="$(cat "$OUT_DIR/wl-mirror.commit")"
if [ "$BUILT_REF" != "$WLM_REF" ]; then
    echo "ERROR: built $BUILT_REF but asked for $WLM_REF" >&2
    exit 1
fi

ACTUAL_NEEDED="$(sort "$OUT_DIR/wl-mirror.ldd")"
if [ "$ACTUAL_NEEDED" != "$(echo "$EXPECTED_NEEDED" | sort)" ]; then
    echo "ERROR: NEEDED manifest does not match the device contract." >&2
    echo "--- expected ---" >&2; echo "$EXPECTED_NEEDED" | sort >&2
    echo "--- actual ---"   >&2; echo "$ACTUAL_NEEDED" >&2
    echo "A new upstream dependency or a base-image drift looks like this." >&2
    echo "Do NOT deploy: confirm the rig ships the new soname before re-pinning." >&2
    exit 1
fi

{
    echo "upstream_ref=$BUILT_REF"
    echo "base_image=$BASE_IMAGE"
    echo "sha256=$( (sha256sum "$OUT_DIR/wl-mirror" 2>/dev/null || shasum -a 256 "$OUT_DIR/wl-mirror") | awk '{print $1}')"
    echo "bytes=$(wc -c < "$OUT_DIR/wl-mirror" | tr -d ' ')"
} >> "$OUT_DIR/wl-mirror.buildinfo"

echo "--- NEEDED (matches device contract) ---"
cat "$OUT_DIR/wl-mirror.ldd"
echo "--- buildinfo ---"
cat "$OUT_DIR/wl-mirror.buildinfo"
echo "OK -> $OUT_DIR/wl-mirror"
