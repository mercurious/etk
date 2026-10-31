# The Emulation Tuning Kit - GTK Edition

> [!IMPORTANT]
> **ETK v0.10.1 is out, built on ROCKNIX 20261001.** A point release: Gran Turismo 6's rear-view mirror renders (core 0.10.1), and the kit recognises the bootloader that ROCKNIX **20261101** brings. **Hold that November ROCKNIX update until ETK 0.11.0** — on 20261101 the ETK kernel is not active (the rig runs stock, with a message at boot), and if the screen stays dark after that update, hold **Volume Down** at power-on and set *Device model* to your Flip 2. The October ROCKNIX (20261001) moved the Retroid Pocket Flip 2 to a new bootloader (ROCKNIX-ABL: no more boot menu). The kit now lives inside it: our kernel sits in the one slot the bootloader loads, the stock kernel is parked beside it, and the ETK OS Guard puts ours back after every future ROCKNIX update. Safe to update again. The 12 GB Flip 2 (Visionox panel) is now a verified rig, at a measured 120 Hz.
>
> - **New rig?** Flash the [card image](https://github.com/mercurious/etk/releases/latest) (the v0.10.0 build; after boot 2, Pitstop TOOLS → Check for ETK Updates brings it to 0.10.1). Windows users: this is your path — the Windows installer can't deliver the kernel.
> - **On ETK v0.9.0 / ROCKNIX 20260901?** Three steps, in this order, with the charger on, ≥ 50 % battery and 2 GB free on `/storage`:
>   1. Remove the old kit with the **current** `uninstall.sh` (`curl -fsSL https://raw.githubusercontent.com/mercurious/etk/main/uninstall.sh -o ~/etk/uninstall.sh && bash ~/etk/uninstall.sh`) and check the rig boots on its own. (Ran an older `uninstall.sh` and it doesn't? Pick *Retroid Pocket Flip2* in the boot menu, then run this one.)
>   2. In ROCKNIX: `START` → `UPDATES & DOWNLOADS` → stable → take 20261001 (it flashes the new bootloader — don't interrupt it). The first boot stops at `DEVICE MODEL NOT SET`: hold **Vol−**, *Set device model* → **Retroid Pocket Flip2** (*Flip2 Visionox* only on 12 GB units).
>   3. Run the one-liner below. Reboot once when the Guard asks. Your shaders, saves and tunes come back.
> - **Already took 20261001 on a GTK rig and it won't boot?** Card in a PC: in the `ROCKNIX-GTK` partition delete `KERNEL`, rename `KERNEL.gtktest` → `KERNEL`, boot, then do step 3. Internal install: [open an issue](https://github.com/mercurious/etk/issues) — we'll walk it with you.
>
> Full guide: [Migrating to ROCKNIX 20261001](https://github.com/mercurious/etk/wiki/Migrating-to-ROCKNIX-20261001).

The **Gran Turismo Kit** (GTK) is a specialty installation for your **Retroid Pocket Flip2 SM8250** (or [sibling device](https://github.com/mercurious/etk/#handheld-system-support)) built on patched forks of [ROCKNIX](https://github.com/mercurious/rocknix-gtk) (OS/kernel), [RPCS3](https://github.com/mercurious/etk-rpcs3-gtk) (PS3 emulator) and [MESA Turnip](https://github.com/mercurious/etk-turnip-gtk) (Adreno Vulkan video driver), integrated with a custom middleware (the **ETK**), originally tuned for the **Gran Turismo series**. GT HD Concept, GT5 Prologue Spec II and Spec III, and GT6 are supported; GT5 reaches the track but still freezes the chip mid-race (tracked). Other game support is *emergent* and shows what the chassis can do.

<img src="https://raw.githubusercontent.com/mercurious/etk/main/docs/screenshots/etk_NPUA80075_20260526_132550.png" width="640"
     alt="GT5P Suzuka chase cam, blue Nissan Skyline GT-R approaching a sweeping corner, mini-map visible, position 12 of 12 lap 2 of 3." />

- To flash an SD card, [download the latest card image](https://github.com/mercurious/etk/releases)
- Or use the install commands below to [Quick Start](https://github.com/mercurious/etk/#quick-start) an existing ROCKNIX boot (re-run the same line anytime to update, repair or sync)

macOS / Linux / WSL:
```sh
curl -fsSL https://raw.githubusercontent.com/mercurious/etk/main/get-etk.sh | bash
```
Windows (any PowerShell — kit only; the kernel comes from the card image, see [Windows Host](https://github.com/mercurious/etk/wiki/Windows-Host)):
```powershell
irm https://raw.githubusercontent.com/mercurious/etk/main/windows_installer/get-etk.ps1 | iex
```

- [ETK Wiki](https://github.com/mercurious/etk/wiki) for full documentation, guides, advanced features
- [Migrating from 20260901](https://github.com/mercurious/etk/wiki/Migrating-to-ROCKNIX-20261001) · [Tested Games](https://github.com/mercurious/etk/wiki/Tested-Games) · [Platforms](https://github.com/mercurious/etk/wiki/Platforms) (Android aPS3e/RPCSX forks, macOS, Windows, Asahi bench build)
- [System Requirements](https://github.com/mercurious/etk/#etk-system-requirements) · [Device Support](https://github.com/mercurious/etk/#handheld-system-support) · [Kill-Switch Index](https://github.com/mercurious/etk/wiki/Kill-Switch-Index)

## Why Install ROCKNIX-GTK and the ETK?
*Perhaps you've tried getting a PS3 Gran Turismo game working on a Retroid Pocket Flip2 and concluded the device supports GT for PS2 and PSP only.*
1. **HIGH PERFORMANCE RIG:** faster and more playable than anything we've run on the Android boot, including our own [aPS3e fork](https://github.com/mercurious/aps3e/releases).
2. **CRASH PREVENTION:** the whole stack is forked and patched as one "chassis", so the GTK **anti-lock** catches a GPU wedge and keeps the race going — the HUD shows the save `|·«!»·|` and keeps a counter `|·×03·|` for the boot, so you know when it's time to refresh the rig with a reboot.
3. **BUGS FIXED:** owning the chassis let us fix the stubborn GT5P "road flicker" [bug](https://github.com/RPCS3/rpcs3/issues/11912), the glitchy audio-card boot in ROCKNIX, the Flip 2's dead built-in microphone, USB-C video out, and more.
4. **FEATURES ADDED:** see [Key Kit Features](#key-kit-features) — from PSRemotePlay streaming to on-device tuning down to the overclock and the Turnip dials, and now an Engineer's link for Claude Code.

## Dyno-Proven
Every claim above is scored against the ETK telemetry ledger (1,400+ instrumented sessions on the reference rig, ledger through 2026-08-30) — not vibes.

<img src="https://raw.githubusercontent.com/mercurious/etk/main/docs/charts/anti-lock.png" width="720" alt="Stacked weekly bars of Adreno GPU wedges: before v0.7.0 every wedge required a human recovery button press; after anti-lock went default-on, 69% are absorbed automatically and total wedges fall week over week." />

*The GTK Anti-Lock flagship: before v0.7.0, every GPU wedge ended with a human pressing the panic chord. Since: 69 % absorbed automatically — and total wedges are falling as the next-generation nets land.*

<img src="https://raw.githubusercontent.com/mercurious/etk/main/docs/charts/session-survival.png" width="720" alt="Median racing-session duration per 5-day bucket rising from about 3 minutes to over 11 minutes across the release timeline, with the 90th-percentile ceiling rising from 7 to 22 minutes." />

*Sessions keep getting longer: median race session up ~3× across the campaign, ceiling up ~3× with it.* The KERS cycle-reclaim and library-growth charts moved to the wiki: [Using MESA Turnip GTK](https://github.com/mercurious/etk/wiki/Using-MESA-Turnip-GTK#dyno).

## Quick Start
### Don't have ROCKNIX?
It's easy to try the kit with a spare SD card and a USB card reader, and to go back to Android or standard ROCKNIX anytime.
1. Download the ROCKNIX-GTK SD card image from [releases](https://github.com/mercurious/etk/releases) and flash a blank card with a tool like [Balena Etcher](https://etcher.balena.io/#download-etcher).
2. Insert the card and power on. *(On a unit that has only ever run Android, the first boot from the card still goes through the stock Retroid loader — hold `Volume-Up` before the Retroid logo and release at the U-Boot logo — and installs the ROCKNIX-ABL bootloader from the card. This is the one step nobody in the garage has executed on a fresh unit; if it goes differently for you, tell us.)* If the screen says `DEVICE MODEL NOT SET`, hold **Vol−** for the boot menu, choose *Set device model* → **Retroid Pocket Flip2** (12 GB units: **Flip2 Visionox** — the wrong pick is a black panel until you pick again). The first boot resizes the card and reboots itself; the second boot is the kit. **Then run the one-liner once** from your computer (turn on **SSH** — and **USB gadget network mode** if you connect by cable — in ROCKNIX's settings first): the card carries the GTK kernel, the core and the driver catalog, and the one-liner adds the kit's boot-time services — the Internal Microphone, the USB-net heal, the battery guard, the sleep fixes — and pairs your computer with the rig.
3. In the same **Vol−** boot menu you can make ROCKNIX (the card) or Android the default — no logo racing at every boot. *(exact menu wording; see [Using ROCKNIX](https://github.com/mercurious/etk/wiki/Using-ROCKNIX-Guide))*
4. Add your WiFi and enable SSH in the ROCKNIX EmulationStation settings. Drop the PS3 firmware `.pup` into `/storage/roms/etk/firmware_drop`, a game `.pkg` into `/storage/roms/etk/pkg_install_drop`, or copy an `.iso` into `roms/ps3` over SMB or SFTP; the [ETK Pitstop TOOLS tab](https://github.com/mercurious/etk/wiki/ETK-Pitstop-App#tools) installs them in the background while you keep playing.
5. **Ready to play** — but beware of shader storms early on (the spinner in the lower-left corner). They are one-time compiles banked in your shader vault; let camera pans and demo replays run, visit the dealership, and performance arrives as the vault saturates. The HUD counts every shader you bank, so even a crashed run was productive.

### Already have ROCKNIX?
Set the update channel to stable (`START` → `UPDATES & DOWNLOADS`) and make sure you are on **20261001** (not 20261101 — hold that one until ETK 0.11.0), then run the one-liner above. On 20260901 or older, follow the migration box at the top first. Prefer a manual checkout? `git clone https://github.com/mercurious/etk` then `./install.sh` (Windows: `powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1`) from the repo root.

## Key Kit Features
| Exclusive Feature or Fix | What it does for you | Where you see it |
|---|---|---|
| GTK Anti-Lock | Catches a GPU wedge and keeps the race going; counts the saves this boot | overlay `·«!»·` `·×03·` |
| GTK Turnip Traction Control | Limited-slip for the Adreno: the GTK driver's dials (Max Stability = `zlatez`) keep the GPU gripping longer | DRIVER tab · overlay `++···` `=====` |
| GTK KERS | Reclaims the ≈38 % of CPU cycles stock burns in spins, polls and fault storms | dyno-proven (ledger + in-race profiler) |
| **120 Hz on the 12 GB Flip 2** *(new)* | Both Flip 2 device trees ride in the kernel with the kit's mic and USB-C fixes applied to both; the Visionox panel's 120 Hz is measured, not assumed | pick *Flip2 Visionox* once at the first boot |
| **OS Guard for ROCKNIX-ABL** *(new)* | A ROCKNIX update silently puts the stock kernel back; the Guard parks it, restores the GTK kernel and asks for one reboot — never a brick, nothing by hand | boot toast "reboot once" · `ETK_OSGUARD_ABL_RESTAGE=0` |
| **USB-C charging** *(new, kernel 0.6.4)* | The handheld now draws up to ~3 A (charging at ~1.9 A) from a USB-C PD source instead of 500 mA, so it charges on the same cable that carries the Engineer's link | direct C-to-C cable |
| **Pitlink — Engineer link** *(new, core ≥ 0.10.0)* | Lets Claude Code on your computer see the game's frames and press the pad *inside* the emulator over the USB cable; off by default, cable-only | Pitstop TOOLS → Pitlink on/off · [wiki](https://github.com/mercurious/etk/wiki/Pitlink) |
| **Engineer Autonomy** *(new)* | Kill switch at the car for an overnight crash hunt you sign for on your computer; off and inert by default | Pitstop TOOLS → Autonomy on/off · `./hunt start\|status\|stop` · [wiki](https://github.com/mercurious/etk/wiki/Engineer-Autonomy) |
| **Sleep that holds** *(new)* | Lid close and power key sleep and stay asleep — cable in or fan still running | just works · `ETK_USB_NOWAKE=0` · [wiki](https://github.com/mercurious/etk/wiki/Sleep-and-Battery) |
| **Battery Guard** *(new)* | Powers the rig off cleanly at 10 % before the cell goes into recovery mode; warns first if awake; the next boot says why | boot toast · `ETK_BATTERY_GUARD=0`, `ETK_BATTERY_FLOOR` |
| **POWER: governor + GPU ceiling** *(new)* | The CPU governor you pick now reaches the game; cap the GPU below the 925 MHz overclock; UFS clock pin (the GT5 freeze mitigation) | Pitstop POWER tab (footer `+ games: <gov>`) · `ETK_UFS_PIN=0` · [wiki](https://github.com/mercurious/etk/wiki/POWER-Tab) |
| **Internal microphone** *(new)* | The Flip 2's built-in mic works on ROCKNIX; the headset mic records speech, not noise | PipeWire "Internal Microphone" · `ETK_INTERNAL_MIC=0` · [wiki](https://github.com/mercurious/etk/wiki/Flip-2-Mic-and-USB-C-Video) |
| **Treadwear** *(new)* | SD-card wear gauge from your computer: HEALTHY / DEGRADED / FAIL with evidence, trend per card | `tools/card_doctor.py` · [wiki](https://github.com/mercurious/etk/wiki/Treadwear) |
| ETK Self-Update | Update the kit from the couch — no computer | TOOLS → Check for ETK Updates |
| G-INSTR Telemetry | Jitter gauge in the HUD | overlay `··2»»` |
| VAULT + PADDOCK | Shader vault survives crashes and OS updates; optional private GitHub backup | overlay `2+ 34.5k 167MB` · PADDOCK tab |
| Thermal Guard | PIT-mode cooldown when hot, back to racing when cool, no reboot | overlay `89°HOT»»»` |
| ETK Pitstop App | On-device telemetry, tuning, driver, power, tools, paddock | ROCKNIX Tools carousel · [wiki](https://github.com/mercurious/etk/wiki/ETK-Pitstop-App) |
| DDU Overlay | MangoHUD with ETK gauges and a battery icon; G-INSTR / BASIC / LEAN / COMPACT dashes | TOOLS → HUD dash · hold `R1`+`L3` to move/hide |
| Crash Recovery | Back to the ES frontend from a freeze | `L1`+`R3` |
| ETK Screenshot | Shutter with the overlay included | `L1`+`L2`; modes in TOOLS |
| On-Device Installs | Firmware and PKG install in the background from drop folders | `etk/firmware_drop`, `etk/pkg_install_drop` → TOOLS |
| Golden Tune + ISO Onboarding | New titles (PKG or ISO) start on the shipped per-title tune or the golden default | automatic · `ETK_GOLDEN_SEED=0`, `ETK_ISO_ONBOARD=0` |
| USB-C video out + mirroring *(fixed)* | HDMI/DP adapters power up again on the custom kernel; the screen stays on; the pad keeps focus on the TV | plug in · `ETK_DP_ESFOCUS=0` |
| Flicker-free road surfaces | The 5-year RPCS3 road-flicker bug, fixed in the fork | just renders |
| Audio boot fixed | Sound every boot (fixed in the kernel) | just works |
| **Boot logo + USB networking on 20261001** *(fixed)* | Stock 20261001 loses the boot logo and half-configures the USB link; the GTK kernel and the kit heal both | logo on the glass · `ETK_USBNET_HEAL=0` |
| PSRemotePlay Streaming | chiaki-rocknix pairs with your PS4/PS5 | Chiaki app in Tools |

## Handheld System Support
| Make | Model | Chipset | Profile | Status |
|---|---|---|---|---|
| Retroid Pocket | Flip 2 (8 GB) | SM8250 Adreno 650 | `SM8250` | 🏁 Verified |
| Retroid Pocket | Flip 2 **12 GB (Visionox panel)** | SM8250 Adreno 650 | `SM8250` | 🏁 Verified (120 Hz) |
| Retroid Pocket | 5 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini V2 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| AYN | Thor Lite | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| MANGMI | Pocket Max | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | 6 | SM8550 Adreno 740 | (needs new profile + vault) | Not yet supported |

## ETK System Requirements
The GTK fork is built from **ROCKNIX official release `20261001`** (the **GTK 0.10.1** stack, ROCKNIX-ABL boot chain).
| Type | Detail |
|---|---|
| Host System | macOS or Linux native, WSL; Windows via the card image or the [PowerShell port](https://github.com/mercurious/etk/wiki/Windows-Host) (kit only — no kernel lane, no Pitlink) |
| OS | ROCKNIX-GTK on ROCKNIX 20261001 |
| Kernel | rocknix-gtk 7.2.0 `0.6.4` (`boot.img`: anti-lock + audio-fix + Flip 2 mic and USB-C power device trees + boot logo + USB-C charging) |
| Emulator | RPCS3 **GTK Edition** 0.10.1 (ARMSX3 1.0.4 base) |
| Driver | MESA Turnip 26.2.2 `gtk_0.7` (catalog of 9 in the DRIVER tab) |

## Notes on ROCKNIX OS Updates
ROCKNIX ships its kernel as one file the bootloader loads (`/flash/KERNEL`), and the kit puts its own kernel in that slot with the stock one parked beside it. The ROCKNIX updater doesn't know we exist: every OS update silently puts the stock kernel back. That is a quiet downgrade, never a brick — the rig boots stock, with WiFi and sound. On that boot the **ETK OS Guard** notices, parks the updater's kernel as the new stock fallback, puts the GTK kernel back and shows one message: **reboot once**. If the OS update moved to a kernel our build doesn't match yet, the Guard says so and leaves stock in place until the next ETK release — nothing to fix by hand.

- **Recommended order:** ETK first (Pitstop `TOOLS` → Check for ETK Updates), then ROCKNIX, then the one reboot. The updater needs 2 GB free on `/storage` and ≥ 50 % battery on the charger (it flashes the bootloader).
- **Turning it off:** `ETK_OSGUARD_ABL_RESTAGE=0` in `etk.conf` makes the Guard report only.
- **On ETK v0.9.0 or older?** Follow the migration box at the top first — those kits predate the new bootloader.

## Removing ETK
- `uninstall.sh` removes the kit and puts the parked stock ROCKNIX kernel back in the boot slot (Windows: `windows_installer\etk-uninstall.ps1` runs the same block). Your vault is kept unless you ask otherwise.

# Credits
Support the underlying projects: **[ROCKNIX](https://rocknix.org)** (OS and kernel) · **[RPCS3](https://rpcs3.net)** (emulator) · **[MESA Project](https://mesa3d.org)** (freedreno video driver). Every component of the kit is a public fork — companion repos and build notes are on the wiki: [Kit Map and Building](https://github.com/mercurious/etk/wiki/Kit-Map-and-Building).

# Legal Notice
This project is intended for expert enthusiasts who maintain fair use/legal digital archives of their own games, not copyright infringement. No association with Sony or any of its affiliates is implied.

# AI Disclosure
ETK was originally prototyped with Google Gemini and developed/maintained with Anthropic Claude Code.

# License
ETK is released under the [GNU General Public License v2.0](LICENSE), matching the licensing of [ROCKNIX](https://github.com/ROCKNIX/distribution) which it extends.
