#!/usr/bin/env bash
# cockpit/preflight — verify adb, device, target app, and gamepad node before a session.
# Env: COCKPIT_PKG (default aenu.aps3e), ADB (optional explicit adb path).
set -uo pipefail
PKG="${COCKPIT_PKG:-aenu.aps3e}"

# --- locate adb: explicit env, then PATH, then ETK toolchain fallback ---
ADB="${ADB:-}"
if [ -z "$ADB" ]; then
  if command -v adb >/dev/null 2>&1; then ADB="$(command -v adb)"
  elif [ -x "/Volumes/aps3e-build/android-sdk/platform-tools/adb" ]; then
    ADB="/Volumes/aps3e-build/android-sdk/platform-tools/adb"
  else
    echo "ADB: NOT FOUND. Install Android platform-tools, or run with ADB=/path/to/adb."
    exit 2
  fi
fi
echo "ADB: $ADB"
"$ADB" start-server >/dev/null 2>&1

# --- device present? ---
devs="$("$ADB" devices | sed '1d;/^$/d')"
if [ -z "$devs" ]; then
  cat <<'EOF'
NO DEVICE on adb. Coach the driver through USB debugging:
  1. Handheld: Settings -> About phone -> tap "Build number" 7 times.
  2. Back -> System -> Developer options -> turn ON "USB debugging".
  3. Connect the device to this computer by USB cable.
  4. On the device, accept "Allow USB debugging?" (tick "Always allow from this computer").
  5. Re-run preflight.
If a device shows as "unauthorized": accept the RSA prompt on the device screen.
(Wireless option: enable "Wireless debugging" and `adb connect <ip>:<port>`.)
EOF
  exit 3
fi
echo "DEVICE:"; echo "$devs" | sed 's/^/  /'

# --- target app ---
if "$ADB" shell pm path "$PKG" >/dev/null 2>&1; then echo "APP: $PKG (installed)"
else echo "APP: $PKG NOT installed (set COCKPIT_PKG to the right package)"; fi
emu="$("$ADB" shell pidof "$PKG:emu" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
if [ -n "$emu" ]; then echo "EMU: in-game (pid $emu)"
else echo "EMU: not in a title yet (launch one for live eyes/telemetry)"; fi

# --- gamepad node: the input device exposing BTN_GAMEPAD ---
gp="$("$ADB" shell 'for d in /dev/input/event*; do getevent -lp "$d" 2>/dev/null | grep -q BTN_GAMEPAD && echo "$d" && break; done' 2>/dev/null | tr -d '\r' | head -1)"
if [ -n "$gp" ]; then
  echo "GAMEPAD: $gp"
  perm="$("$ADB" shell "ls -l $gp" 2>/dev/null | tr -d '\r')"
  if echo "$perm" | grep -q '^crw-rw-rw-'; then echo "  HANDS: sendevent OK (node world-writable, no root needed)"
  else echo "  HANDS: node not world-writable -> driver mode likely needs root ($perm)"; fi
else
  echo "GAMEPAD: none with BTN_GAMEPAD found (driver mode unavailable; spotter/engineer still fine)"
fi

echo "PREFLIGHT OK | pkg=$PKG | gamepad=${gp:-none}"
echo "export these for the other scripts:  ADB='$ADB'  GP='${gp:-}'  COCKPIT_PKG='$PKG'"
