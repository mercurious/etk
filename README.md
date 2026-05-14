# The Emulation Tuning Kit
- A custom Rocknix rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization and advanced telematics
- The Kit currently includes:
1. Hardware and driver tunings for maximum performance
1. Optimized emulator game configurations tuned to the device hardware
1. Sample pre-compiled device and game specific shader set 
1. Customized in-game overlay with ETK telematics with MangoHUD
1. Customized gamepad ETK commands to trigger performance and cooldown modes
1. Pit wall remote terminal screen to monitor and control device rig
1. Advanced crash recovery and analytics
1. Automatic and manual thermal throttling
1. Install, configure, repair, and uninstall the kit remotely from a computer
1. Automatically backup your shader archives from your device to computer
1. FULL installation for initial shader harvesting and tuning, LITE installation for saturated shader sets, RAW for stress testing

# ETK Project Structure
- `install.sh`: Flashes the ETK onto your handheld from a computer
- `uninstall.sh`: Removes the ETK from your handheld from a computer
- `/bin`:
  - `input_d.py`: Handles custom gamepad controls
  - `thermal_d.sh`: Handles system conditions
  - `vault_d.sh`: Handles archival of compiled shaders
- `/config`:
  - `rsyncd.config`: Handles deployment and backup between handheld and computer
  - `MangoHud.config`: Handles custom in-game on-screen overlay
  - `config_NPUA80075.yml`: tuned RCPS3 configuration to GranTurismo 5 Prologue
- `/scripts`:
  - `commander.sh`: Pit Wall central unit with remote terminal DDU UI
  - `env.sh`: Establishes pit and race environment
  - `mango_bridge.sh`: Manages live telemetry and overlay display
-  `/vault`: Large archive of Vulkan precompiled shader bins 

# ETK System Requirements
- **System:** Retroid Pocket Flip 2 (SM8250)
- **OS:** ROCKNIX (Nightly Build: 20260511)
- **Target:** Gran Turismo 5 Prologue (RPCS3)
- **Shell:** BusyBox v1.36.1
- **Custom Overlay:** MangoHUD

# ETK Concept Brief
The Emulation Tuning Kit (ETK) is a performance-optimization and telemetry suite designed to achieve the "impossible": **Native 720p PS3 Emulation on ARM Handhelds.** The core user experience is a **"Mining Meta-Game":** The driver performs "Harvesting Runs" at 50% resolution to bank shaders into a permanent Vault. A live gear-shift allows the driver to watch a custom MangoHUD overlay and pull over to downshift to PIT mode (thermal cooling) from RACE (overdrive) which prevents crashes while overtaxing the Flip 2. A similar “clutched” command triggers a shader dump. Once the Vault is saturated, the driver shifts to upscaled mode (Native 720p), utilizing the banked shaders to bypass real-time compilation stutters. Once an unknowable shader saturation is achieved, discovered only through trial and error, the thermal demand is successfully mitigated and near console quality becomes achievable upscaled, as races can be won, and the game saves and credit accruals can prove the system is gradually working. The team behind this project is also known to rest the Flip 2 on an ice pack or put it in the refrigerator during PPU compiling. Coded with Gemini, this project is about a massive cluster of GPUs tuning a lonely Adreno with open source tools running on a pocket Rocknix box to make the whole data center proud. It’s like Mazda winning Le Mans with the rotary 787B in real-life by doing thermal efficiency differently. 

# Current Features 
- Local repository (macOS) rsync, install and uninstall tools
- Private GitHub at https://github.com/mercurious/etk
- Remote DDU (Driver Data Unit) terminal window with hotkey commands including robust crash recovery tool, thermal “shift” UI, shader dump tools, probes, logs, etc.
- Large GT5P compiled shader vault (650MB+)
- Customized MangoHUD implementation for on-board DDU live telemetry and pit coaching
- Unique Shader dump UI
- Highly optimized RSCP3 GT5P config file
- Deep system enhancements and integrations

# Warnings and Recommendations
- Requires the patience and dedication of race car drivers. You will crash. But you will also win races that could otherwise not even be played. ETK doesn't magically make your device run PS3 emulation, it only gives it a fighting chance with professional grade tools and system tunings.
- Requires the exact Rocknix Nightly specified above. This does not work on the official release nor has it been tested or updated for other Rocknix nightly builds.
- Do not use your main ROM library SD card for this Rocknix install. Instead, use a reasonably sized (256GB or less) high quality dev card that you don't mind wearing out or needing to reflash. Put your favorite PS3 games on this card and wait until the ETK can be upgraded for a Rocknix (official) release before using on your main card.
- Do not install on your handheld device if you intend to use the warranty coverage or otherwise would protect it from track day abuse. If you wouldn't take your daily driver to the track, do not install highly experimental software on your only retro handeld the could potentially damage or brick it.
- Icepack or Refrigeration is recommended during PPU compiling and intensive early-stage shader harvesting or other racing when the WARNING and OVERHEAT messages display on the custom HUD DDU.
- OS updates and other major system events may require a series of successive PPU recompilations which do take time and putting the device on an ice pack or in the refrigerator will reduce thermal stress on the system during these intensive operations. Once the PPUs have been fully rebuilt, the game launch will skip this process. With each successive pass at recompiling the PPUs, the game start feeling faster, smoother, and more responsive.
- The ETK is designed with community shader sharing in mind.

# Custom ETK Gamepad Specifications
- Reserved:
  - `START` + `SELECT` + `R1` = Native Rocknix force quit
  - `HOME` = RPSC3 menu
  - `SELECT` = GT3P camera view toggle
- Implemented and tested
	- R3 (press down right analog stick) to toggle thermal mode PIT/RACE  
- Implemented but untested
  - `SELECT` + `L2` + `R2` = ETK DDU [R]ecovery key command
  - `SELECT` + `D-pad Right` = dump [V]ault shaders
  - `SELECT` + `D-pad Left` = toggle between available MangoHUD configurations (or just on/off)
- Temporariily disabled     
	- `SELECT` + `D-pad Up` = download into PIT MODE (thermal cooldown)
	- `SELECT` + `D-pad Down` = upshift into RACE MODE (overdrive)

  
# Project History
- Phase 1: MVP proof-of-concept: now deprecated monolithic mvp/commander.sh achieved initial shader cache accumulation downscaled with no audio, essential commands and Excitebike UX proofed
- Phase 2: Modular professional grade deployable ETK: shader cache successfully upscaled
- Phase 3: Enabled on-board MangoHUD DDU: integrated live instrumentation
- Phase 4: Enabled Gamepad ETK pit controls: full un-tethered racing and shader harvesting
- Phase 5: Solved treadwear problem on SD card with RAM disk support
- Phase 6: Rocknix OS migrated to May 11 Nightly, dependency updated, preserved into repo, 
- Phase 7: Enabled robust crash reporting, diagnosis, advisory, ETK install tiers
- Phase 8: Attempted experimental incremental audio support
- Phase 9: Enable game agnostic ETK
- Phase 10: Work with pre-alpha testers to test other games
- Phase 11: Prepare for public GitHub distribution 
- Phase 12: Develop native Rocknix ETK app for utilities (Tools or carousel UI)
- Phase 13: Develop shader sharing and shader swarming features per device/per game serial

# External Assets
- [Download](https://drive.google.com/drive/folders/1u-Q92-v0PLur2GsAvfe-_afgYUINreTq) Archival Dependent Rocknix Build (nightly-20260511)
- [Download](https://drive.google.com/drive/folders/1d_efusVz_TBBnxW6urAgDicwSQbUWVkk?usp=drive_link) a Starter Set of Shaders for GT5P (NPUA87005) for Retroid Pocket Flip2 (SM8250) only

# Quick Install Instructions for Pros
1. Setup the correct Rocknix nightly build boot
1. Download ETK and setup `config/rsyncd.config` with correct local paths and `scripts/env.sh` with correct device and computer IP addresses
1. Extract shaders into correct vault folder.
1. Run ./install.sh

# Full Installation Instructions for Brave Newbies (work-in-progress)
1. Flash a new SD card and install the correct nightly build (instructions)
1. Launch Rocknix and press `Start` -> `Network` -> Connect to your WiFi and note your IP address
1. Use SMB in macOS Finder or Windows File Explorer or an SFTP client to access your `/storage/roms/` folder on your SD card through your handheld to install games
1. Copy your .pkg/.rap files to `/storage/roms/temp` and create text files with .psn extension containing only game serial number and put in /storage/roms/ps3
1. On your new Rocknix boot go to `Tools` -> `Start RPSC3`
1. Copy your .iso files to `/storage/roms/ps3`
1. In RPSC3 (plugging in a mouse helps a lot) choose `File` and `Install package` and select the .pkg file. Repeat for the .rap file. No need to check boxes to install desktop shortcuts. You can go to `File` and `Exit` out of RPSC3. You should only need a mouse to install a .pkg file using this method from here on out.
1. Setup your local deployment tools (local folder and terminal window)
1. Download the ETK and extract into local working folder
1. Edit `config/rsyncd.config` with your local dev paths in a text editor
1. Edit `scripts/env.sh` with your device IP addresses in a text editor 
1. Extract starter shaders into `vault/SM8250/NPUA80075/`
1. Set up secure handshake between computer and device
1. Run this command to enable installer `chmod +x TK`
1. Type `./install.sh` to set up your device and you will only need that command again to update or repair your ETK from here
1. Launch GT5P and you will notice that the PPU modules will recompile several times when you boot the game and each time it will get better and better and will eventually be skipped when the game is fully tuned to your device. By pre-installing pre-compiled shaders you are skipping the in-game processing freeing up substantial overhead to render the game.

# ETK Install Modes
Feature,FULL (Tuning),LITE (Competition),RAW (Stress)
Thermal Governor,Active (Dynamic),Active (Dynamic),Off (Hard Max)
Vault Harvester,Enabled,Disabled,Disabled
Cache Injection,Active at boot,Active at boot,Manual only
MangoHUD Bridge,Full Details,Compact Layout,Disabled
Stealth Support,Yes (Auto-hide),Yes (Auto-hide),N/A
