# Guide A — Claude Cockpit: Quickstart (Android)

> **The 90% path.** You have a host computer (Mac or Windows) and an Android handheld
> running the aPS3e fork. This guide gets Claude acting as your **pit engineer** — watching
> a live run, reading GPU/CPU/thermal telemetry, and tuning the emulator with you.
> **You do NOT need ROCKNIX for any of this.** (Got a ROCKNIX rig too? See Guide B — it's additive.)

## What it does
Claude Code (on your computer) connects to your handheld over USB via `adb` and runs the
**Cockpit** skill in three tiers, lightest first:

- 🟢 **Spotter** — read-only. Watches the screen + live telemetry and calls what it sees
  (FPS dips, thermal climb, where performance falls off). The safe default; never touches the device.
- 🟡 **Engineer** — telemetry-driven tuning. Reads the run, then recommends/edits emulator
  settings against what the silicon is actually doing.
- 🔴 **Driver** — *experimental.* Records/replays gamepad input. Useful as a deterministic
  repro/benchmark tool; **not** a clean autonomous driver. Off unless you explicitly ask.

## Requirements
- A computer with **Claude Code** (macOS or Windows).
- The **aPS3e fork** installed on your Android handheld (see Guide C).
- A **USB cable** (data, not charge-only).
- **adb** (Android platform-tools) — install below.

## Install (one time)

**1. The Cockpit skill** — copy the `cockpit/` skill folder into your Claude skills directory:
```
~/.claude/skills/cockpit/          # macOS / Linux host
%USERPROFILE%\.claude\skills\cockpit\   # Windows host
```
(It's standalone — you do not need to install the rest of ETK.)

**2. adb (platform-tools):**
- **macOS:** `brew install android-platform-tools`
- **Windows:** download Google's *SDK Platform-Tools for Windows*, unzip somewhere stable
  (e.g. `C:\platform-tools`), and add that folder to your **PATH**. Verify in a new terminal: `adb version`.

**3. The handheld:** Settings → **About** → tap *Build number* 7× to unlock **Developer options** →
enable **USB debugging**. Plug into the computer; **accept the "Allow USB debugging?" prompt** on the device.

## First run
In Claude Code, just say what you want — e.g. *"spot my aPS3e run"* or *"watch GT5P and tell me where the FPS drops."*
The skill runs **preflight** first (finds adb, the device, and the app). If it reports **NO DEVICE**,
it will coach you through the USB-debugging steps — fix and re-run; don't drive blind.

Targeting a different app? Set `COCKPIT_PKG=<package>` (default `aenu.aps3e`).

## Good to know
- **Vision is slow** (~0.5 fps screencaps), so Claude **spots and tunes reliably but "drives" crudely** —
  lean on Spotter/Engineer; treat Driver as experimental.
- Everything in Spotter is **read-only** — it cannot change your device.
- No ROCKNIX, no ssh, no `/storage` — this path is pure host + adb + your Android handheld.
