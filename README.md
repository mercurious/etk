# The Emulation Tuning Kit - GTK Edition
The Gran Turismo Kit (GTK) is a specialty installation for your Retroid Pocket Flip2 SM8250 (or [sibling device](https://github.com/mercurious/etk/#handheld-system-support)) built on patched forks of ROCKNIX (OS/kernel), RCPS3 (PS3 emulator) and MESA Turnip (Adreno Vulkan video driver) integrated with a custom middleware (the ETK), and all of it is **specifically tuned** for the **Gran Turismo series only**. GT HD Concept, GT 5 Prologue Spec II and Spec III are supported. GT5 and GT6 support is pending. Other game support is incidental. 
- [Download latest release](https://github.com/mercurious/etk/releases)
- Introducing [GTK Turnip for ROCKNIX](https://github.com/mercurious/etk/wiki/Using-MESA-Turnip-GTK)
- [System Requirements](https://github.com/mercurious/etk/#etk-system-requirements)
- [ETK Wiki](https://github.com/mercurious/etk/wiki) for full documentation, guides, advanced features
- [Device Support](https://github.com/mercurious/etk/#handheld-system-support)
- [Tested Games](https://github.com/mercurious/etk/wiki/Tested-Games)
- [Getting Started](https://github.com/mercurious/etk/#getting-started)

## Quick Start
### Don't have ROCKNIX?
Try the kit with a spare SD card and reader. Revert back to Android or your primary card at any time.
1. Download the ROCKNIX-GTK SD Card image from [releases](https://github.com/mercurious/etk/releases) and use a tool like Balena Etcher to flash a blank card
1. Insert your new flashed card to autoboot into ROCKNIX-GTK with your Android boot preserved
1. Add your WiFi in network settings and enable SSH, etc.
1. Add PS3 firmware and ROMS to the provided etk drop folders over SMB or SFTP
1. Use ETK Pitstop app TOOLS to install firmware and PKG files
1. Ready to play
### Already have ROCKNIX installed?
See [Getting Started](https://github.com/mercurious/etk/#getting-started) for how to install the ETK and the GTK forks into your existing ROCKNIX setup.

## Key Kit Features
| Exclusive Feature or Fix | Description |
|---|---|
| GTK Anti-Lock | Automatic live recovery of GPU wedge crashes |
| Adreno Traction Control | Automatic "limited-slip-differential" holds GPU down |
| G-INSTR Telemetry | Animated jitter gauge in HUD |
| VAULT + PADDOCK | Advanced shader protection and management |
| Thermal Guard | Automatically protect silicon from overheats |
| ETK Pitstop App | Native ROCKNIX tools app for onboard telemetry, tuning, tools, and more |
| Video Mirroring | Device screen on while USB-DisplayPort-HDMI out active | 
| Flicker-free Road Surfaces | 5 year emulator old bug solved |
| Audio card boot fixed | Sound works reliably vs official release | 


## Handheld System Support
| Make | Model | Chipset | Profile | Status |
|---|---|---|---|---|
| Retroid Pocket | Flip 2 | SM8250 Adreno 650 | `SM8250` | 🏁 Verified |
| Retroid Pocket | 5 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini V2 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| AYN | Thor Lite | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | 6 | SM8550 Adreno 740 | (needs new profile + vault) | Not yet supported |

## ETK System Requirements
The GTK fork was built from **ROCKNIX official release `20260701`** 
| Type | Detail |
|---|---|
| Host System | macOS or Linux native, Windows support via img flash, WLS, PowerShell Port  |
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
