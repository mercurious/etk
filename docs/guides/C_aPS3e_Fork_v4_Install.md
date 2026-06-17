# Guide C — Installing the aPS3e Shader Fork v4 (Android device)

> The **device-side** half. This is the emulator that runs on your Android handheld; the Cockpit
> skill (Guide A) talks to it. It's the standard aPS3e plus the community **Shader Patch Edition**
> improvements and a native record/replay hook.

## What's in v4
- The upstream **aPS3e** PS3 emulator, plus the Shader Patch Edition changes (persistent
  VkPipelineCache, the Gran Turismo high-memory crash fix, restored UI icons, Shader Cache Manager).
- **Native pad-movie** — a frame-exact gamepad record/replay hook (`cellPadGetData`). It powers the
  Cockpit **Driver** tier and doubles as a **deterministic repro/benchmark harness** (replay an
  identical input stream across builds or drivers). The autonomous-driving use is
  **experimental/undocumented** — see the Cockpit skill, not this guide.

## Install
1. Get the v4 APK (`aps3e-shader-fork-v4_*.apk`).
2. Transfer it to the handheld (USB, or `adb push`), or sideload directly:
   ```
   adb install -r aps3e-shader-fork-v4_padmovie-v2.2.apk
   ```
   (`-r` replaces an existing install while keeping your data.) If installing on-device,
   enable **Install unknown apps** for your file manager first.
3. Launch it, point it at your PS3 games/firmware as usual.

## Notes
- Your saves, configs, and shader caches from a prior aPS3e install are preserved by `-r`.
- The pad-movie feature is dormant unless triggered (via cache trigger files); normal play is unaffected.
- This APK is **prebuilt** — you don't need the build toolchain (that's a developer-only setup).

## Where to go next
- **Guide A** — drive the Cockpit skill against this install (the main event).
- The native record/replay (Driver tier) is experimental; treat it as a TAS-style repro tool today.
