# Guide B — Claude Cockpit on ROCKNIX (add-on)

> **Only if you own a ROCKNIX handheld.** This is **additive** to Guide A — the same Cockpit
> skill, pointed at a ROCKNIX rig instead of (or in addition to) an Android device. If you're
> Android-only, ignore this guide entirely; nothing here is required.

## What changes vs. Android
The Cockpit skill is **target-agnostic**. It detects the transport at preflight:

| Target | Transport | Telemetry source |
|---|---|---|
| Android (aPS3e fork) | `adb` over USB | Guide A |
| **ROCKNIX rig** | **`ssh`** (USB-net gadget **or** WiFi LAN) | this guide |

With no ROCKNIX rig reachable, the ROCKNIX path simply **stays dormant** — no errors, no setup.
Add a rig and it lights up automatically.

## Requirements
- A **ROCKNIX** handheld (e.g. on an SM8250 device).
- **ssh** from your host (macOS/Linux built-in; Windows 10/11 has OpenSSH built in).
- The **ETK toolkit flashed onto the rig** (below).

## Set up the rig
1. **Flash ETK to the rig** with the ETK Flasher: `./install.sh` (macOS/Linux host). It
   auto-discovers the rig via mDNS (`<soc>.local`) and deploys the toolkit over ssh.
   *(A Windows PowerShell port of the flasher is forthcoming; until then flash from a mac/Linux host,
   or use WSL.)*
2. **Connection:** the most stable link is the **USB-net gadget** (the rig appears as a network
   interface; reach it at its link-local address). WiFi works too but can be flaky on ROCKNIX (see caveats).
3. Run the Cockpit skill as in Guide A — it finds the ssh rig and uses the ROCKNIX telemetry path
   (`rocknix_spotter`): full thermal map, GPU devfreq, CPU-prime freq, memory, the ETK live-stat bridge,
   and a crash-watch.

## ROCKNIX-specific caveats (read these)
- **WiFi can drop** (an `iwd` Wi-Fi-handshake issue on stock ROCKNIX). For sustained sessions,
  prefer the **USB-net gadget** — it's rock-solid and low-latency. *(Note: USB-net uses the USB-C
  port, so it's mutually exclusive with USB-C video capture.)*
- **Higher FPS, lower stability.** Bare-metal ROCKNIX runs the emulator faster than Android but
  is more crash-prone — chiefly **GPU-driver lockups** under load. If the game freezes, use your
  **panic-recovery** to recover. (ROCKNIX gives real core dumps + on-device `gdb`, so it's actually
  the *better* environment for hunting these — let it freeze and capture *before* recovering.)
- ROCKNIX support is **early** relative to the Android path; expect rough edges.
