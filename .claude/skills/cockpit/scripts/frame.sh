#!/usr/bin/env bash
# cockpit/frame — grab a single screen frame to a PNG (READ-ONLY). Usage: frame.sh [out.png]
# Env: ADB
#
# Default path (screencap) captures the composited display incl. the emulator's Vulkan
# SurfaceView on tested devices. If a frame ever comes back black (secure surface / some
# devices), use the high-fps fallbacks below instead:
#   screenrecord (built-in, ~30fps, H.264 over adb):
#     "$ADB" exec-out screenrecord --output-format=h264 --time-limit=10 - \
#        | ffmpeg -i - -vf fps=2 -f image2 /tmp/cockpit_%03d.png
#   scrcpy (separate install, lowest latency mirror):  scrcpy --record /tmp/run.mp4  then sample frames.
set -uo pipefail
ADB="${ADB:-$(command -v adb 2>/dev/null || echo /Volumes/aps3e-build/android-sdk/platform-tools/adb)}"
OUT="${1:-/tmp/cockpit_frame.png}"
"$ADB" exec-out screencap -p > "$OUT" 2>/dev/null
sz=$(wc -c <"$OUT" 2>/dev/null || echo 0)
echo "$OUT ($sz bytes)"
[ "$sz" -lt 1000 ] && echo "WARNING: tiny/empty frame — surface may be black; try the screenrecord/scrcpy fallback (see header)."
exit 0
