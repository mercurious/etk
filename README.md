# The Emulation Tuning Kit - GTK Edition
The Gran Turismo Kit (GTK) is a specialty installation for your Retroid Pocket Flip2 SM8250 (or [sibling device](https://github.com/mercurious/etk/#handheld-system-support)) built on patched forks of [ROCKNIX](https://github.com/mercurious/rocknix-gtk) (OS/kernel), [RCPS3](https://github.com/mercurious/etk-rpcs3-gtk) (PS3 emulator) and [MESA Turnip](https://github.com/mercurious/etk-turnip-gtk) (Adreno Vulkan video driver) integrated with a custom middleware (the ETK), and all of it is **specifically tuned** for the **Gran Turismo series only**. GT HD Concept, GT 5 Prologue Spec II and Spec III are supported. GT5 and GT6 support is *pending*. Other game support is *incidental*.
- [Download latest release](https://github.com/mercurious/etk/releases)
- [Quick Start](https://github.com/mercurious/etk/#quick-start)
- [ETK Wiki](https://github.com/mercurious/etk/wiki) for full documentation, guides, advanced features
- [System Requirements](https://github.com/mercurious/etk/#etk-system-requirements)
- [Device Support](https://github.com/mercurious/etk/#handheld-system-support)
- [Tested Games](https://github.com/mercurious/etk/wiki/Tested-Games)

## Why Install ROCKNIX-GTK and the ETK?
1. **CRASH PREVENTION:** By forking and patching the entire stack on the device, we were able to cross-integrate the OS to the emulator with the video driver as a single “chassis” and wire it to the native MangoHUD overlay so you can see in real-time an alert when you’ve just been rescued from a GPU wedge crash `|·«!»·|`. The same GTK ANTI-LOCK gauge keeps a live counter `|·×03·|` for the current boot so you know when it’s time to refresh the rig with a reboot. This “anti-lock” system emulates how Android provides a similar level of stability, enabling the Adreno Kernel Graphics Support Layer (KGSL) but running on the bare metal of linux opens up tremendous performance headroom by comparison.
2. **BUGS FIXED:** Owning the chassis allowed us to fix the stubborn “road flicker” [bug](https://github.com/RPCS3/rpcs3/issues/11912) that affects GT5P on all platforms, solved a glitchy audio card boot sequence in ROCKNIX, and more.
3. **FEATURES ADDED:** We’ve added new core capabilities to the device such as video mirroring, easy firmware & package install, advanced shader management, on-device tuning down to the overclock and Turnip dials, advanced trigger calibration for the top-end, and an advanced telemetry UI/UX to inform your tuning choices.

## Quick Start
### Don’t have ROCKNIX?
It’s easy to try out the kit with a spare SD card and USB card reader, revert back to Android, or standard ROCKNIX anytime.
1. Download the ROCKNIX-GTK SD Card image from [releases](https://github.com/mercurious/etk/releases) and then use a tool like Balena Etcher to flash a blank card with ROCKNIX-GTK. Safe to ignore Balena validation error.
1. Insert your new flashed card and hold down `Volume-Up` before the Retroid Pocket logo and release as soon as you see the sideways U-Boot logo. The GRUB menu automatically boots into ROCKNIX-GTK. (In GRUB, you can use `Volume` and `Power` buttons to boot into the standard ROCKNIX kernel.) (By holding Volume-down at boot, you can also set the rig to [auto-boot in Rocknix](https://github.com/mercurious/etk/wiki/Using-ROCKNIX-Guide#to-always-boot-into-rocknix-as-the-default-os) instead of Android so you don't have to play race the logos every boot.)
1. Add your WiFi in network settings and enable SSH, etc. once in the ROCKNIX EmulationStation frontend.
1. Add PS3 firmware and ROMS to the provided etk drop folders over SMB or SFTP: `etk/firmware_drop`, `etk/pkg_drop`
1. Use the [ETK Pitstop app TOOLS](https://github.com/mercurious/etk/wiki/ETK-Pitstop-App#how-to-install-ps3-games-with-the-etk) to install your staged firmware and PKG files and games will automatically appear in the ES carousel and much more.
1. Ready to play
### Already have ROCKNIX installed?
See [Getting Started](https://github.com/mercurious/etk/#getting-started) to install the ETK and GTK forks into your existing ROCKNIX setup.

## Key Kit Features
| Exclusive Feature or Fix | Description | Interface |
|---|---|---|
| GTK Anti-Lock | Automatic live recovery of GPU wedge crashes | overlay gauge `·«!»·` `·×03·` |
| GTK Turnip Traction Control | Automatic "limited-slip-differential" holds the Adrendo GPU down | overlay gauge `++···` `=====` |
| G-INSTR Telemetry | Animated jitter gauge in HUD | overlay gauge `··2»»` |
| VAULT + PADDOCK | Advanced shader protection and management | overlay gauge `2+ 34.5k 167MB` |
| Thermal Guard | Automatically protect silicon from overheats | overlay gauge `89°HOT»»»`  |
| ETK Pitstop App | Native ROCKNIX tools app for onboard telemetry, tuning, tools, and more | native app in ROCKNIX ES Tools carousel |
| DDU Overlay | Native MangoHUD customized with ETK gauges | `R1` + `L3` to toggle between top, bottom, default, off |
| Crash Recovery | Safely exit from crash or freeze | `L1` + `R3` to safely recover to ES frontend |
| ETK Screenshot | One-finger shutter screenshots with overlay | `L1` configurable in ETK Pitstop |
| On Device Installations | One-tap, on-device firmware and game installs | Drop `.pup` in `etk/firmware_drop`, `.pkg` in `etk/pkg_drop`, open ETK Pitstop `TOOLS` tab |
| Video Mirroring | Device screen on while USB-DisplayPort-HDMI out active | turn USB plug upside-down to solve video-out issue |
| Flicker-free Road Surfaces | 5 year emulator old [bug](https://github.com/RPCS3/rpcs3/issues/11912) solved | perfectly rendered road surfaces |
| Audio card boot fixed | Sound works reliably vs official release | sound just works now, finally; solving race stutter is a different problem |


## Handheld System Support
| Make | Model | Chipset | Profile | Status |
|---|---|---|---|---|
| Retroid Pocket | Flip 2 | SM8250 Adreno 650 | `SM8250` | 🏁 Verified |
| Retroid Pocket | 5 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini V2 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| AYN | Thor Lite | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| MANGMI | Pocket Max | SM250 Adreno 650 | `SM8250` | Expected (untested |
| Retroid Pocket | 6 | SM8550 Adreno 740 | (needs new profile + vault) | Not yet supported |

## ETK System Requirements
The GTK fork was built from **ROCKNIX official release `20260701`** 
| Type | Detail |
|---|---|
| Host System | macOS or Linux native, Windows support via img flash, WLS, [PowerShell Port](https://github.com/mercurious/etk/tree/main/windows_installer) |
| OS | ROCKNIX-GTK |
| Emulator | RPCS3 **GTK Edition** |
| Driver | MESA Turnip 26.1.3 **GTK** |

## Getting Started
1. If you've already installed ROCKNIX, set the update channel to release/stable (`START` → `UPDATES & DOWNLOADS`), and update if necessary. Nightly builds may not be compatible.
2. Clone this repo to your computer.
```sh
git clone https://github.com/mercurious/etk
```
- (You can also download the code as a `.zip` and extract as `~/etk/`).
3. Install the ETK onto your handheld rig

**For macOS, WLS2, Linux:** from the repo root
to make it executable
```sh
chmod +x install.sh
```
to install the ETK on your handheld rig
```sh
./install.sh
```
whenever you want to update, repair, or sync your rig, `cd ~/etk` or wherever you keep it
```sh
./install.sh
```
3. Drop your PS3 firmware into the approprite drop folder and use ETK Pitstop TOOLS to easily install it. PKG files can be easily installed similarly.

## Removing ETK
- Use the provided `uninstall.sh` to remove the ETK from your system.

## Android-only Support
No ETK or GTK features, just a house tuned Android aPS3e fork. Offers stable but low-framerates vs. ROCKNIX+GTK rig.
- Use [aPS3e Shader Patch Edition](https://github.com/mercurious/aps3e/releases) for Android (until main release is updated with cache fix)
- Use the latest [ETK MESA Turnip drivers](https://github.com/mercurious/aps3e/releases/tag/etk-turnip-26.1.3) for Android during the aPS3e setup wizard or configuration.
- Use an ETK config tuning from [Tested Games](https://github.com/mercurious/etk/wiki/Tested-Games).
- Try the ETK [Claude Code Cockpit skill](https://github.com/mercurious/etk/wiki/Claude-Cockpit-Skill) for real-time pit-engineering advice, crash forensics, tuning suggestions, track photography analysis and more. Works over USB with any Android device and USB & `ssh` on ROCKNIX.

# Legal Notice
This project is intended for expert enthusiasts who maintain fair use/legal digital archives of their own games, not copyright infrigement.

# AI Disclosure
ETK was originally prototyped with Google Gemini and developed/maintained with Anthropic Claude Code.

# License
ETK is released under the [GNU General Public License v2.0](LICENSE), matching the licensing of [ROCKNIX](https://github.com/ROCKNIX/distribution) which it extends.
