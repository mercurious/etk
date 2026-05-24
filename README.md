# The Emulation Tuning Kit
ETK is a custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, shader cache management, advanced in-game telematics, and simple PS3 PKG installation. It guards hard-earned shaders from SD card failure, OS flashing, data corruption, device failure, loss or theft. Push your handheld to its limits while collecting shaders with tools to recover from crashes so the game plays well after several attempts. Specifically designed to make Gran Turismo 5 Prologue playable on a Flip2 Snapdragon, the ETK adopts the racing metaphor throughout but should work for any type of PS3 game. The long term vision is a shader swarm system where the your device automatically seeds and leeches shaders and proven emulation tunings through a tight-knit device-centric P2P network during a battery charge.

# Screenshots
ToDo.
- Sample GT5P in-game screen with HUD DDU with shader harvesting.
- Sample GT5P Class-C Trophy Screen as proof ETK enables game progression.
- Sample ETK Pitstop app TELEMETRY ledger screen
- Sample ETK Pitstop app TUNING screen
- Sample ETK Pitstop app TOOLS PKG installer sequence screenshots

# The Kit Features
1. Native Rocknix ETK Pitstop App for on-device config editing, per game telemetry analysis over time, and simple PS3 game installation (drop .pkg and .rap in `roms/etk/pkg_install_drop/`)
1. Customized in-game overlay dashbard with ETK telematics inside native Rocknix MangoHUD
1. Hardware and driver tunings for maximum performance going beyond config settings
1. Optimized emulator game configurations tuned to the device hardware
1. Smart thermal protection to safely overdrive the device during shader harvesting
1. Automatic shader backup from your device to computer to shield hard earned work from loss and to share with other ETK users
1. Pit wall remote terminal screen to monitor and control device (`scripts/commander.sh`)
1. Install, configure, repair, and uninstall the kit remotely from a computer (`install.sh` and `uninstall.sh`)
1. Multi-Installation Options: FULL installation for initial shader harvesting and tuning, LITE installation for saturated shader sets with thermal protection only, RAW for stress testing without shader and thermal protections (`ETK_BUILD_TYPE` in `scripts/env.sh`)

# ETK Project Structure
- `AI_MANIFEST.md`: System Manual and Immutable Laws of ETK Development for AI
- `README.md`: You are reading it now.
- `install.sh`: Flashes the ETK onto your handheld from a computer
- `uninstall.sh`: Removes the ETK from your handheld from a computer
- `/bin`:
  - `etk_modules_inject.py`: Handles installing native Rocknix PITSTOP app persistently
  - `etk_pitstop.py`: Handles native Rocknix Tools App TELEMTRY, TUNING, TOOLS
  - `input_d.py`: Handles custom gamepad controls
  - `mango_bridge.sh`: Manages live telemetry and overlay display
  - `recovery.sh`: Headless Nuclear Recovery, invoked on-device by the `R3` panic button
  - `session_postmortem.sh`: Handles recording Simple Telemetry to PITSTOP session ledger
  - `thermal_d.sh`: Handles system conditions and emergency cooldown
  - `vault_d.sh`: Handles archival of compiled shaders
- `/config`:
  - `crash_signatures.json`: Defines crash reporting analytics
  - `etk_pitsop.sh`: Native Rocknix app launcher gets installed into device
  - `etk_pitsop.svg`: Native Rocknix app icon gets installed into device
  - `etk_template.yml`: Default emulator template for game packages installed with TOOLS
  - `MangoHud.config`: Handles custom in-game on-screen overlay
  - `pistop_fields.json`: The subset of RPCS3 config settings for on-device edits
  - `rsyncd.config`: Handles deployment and backup between handheld and computer
- `/scripts`:
  - `career_aggregate.sh`: Handles summarizing session ledger into stats for PITSTOP app
  - `commander.sh`: Pit Wall DDU UI for the terminal interface
  - `env.sh`: Establishes pit and race environment variables
  - `probe.sh`: Provides error logs
- `/tools`: various utilities used during ETK development
- `/vault`: Large archive of Vulkan precompiled shader bins organized by chipset and gameID

# ETK System Requirements
- **System:** Retroid Pocket Flip 2 (SM8250)
- **OS:** ROCKNIX (Nightly Build: 20260517)
- **Driver** MESA Turnip 26.1.0
- **Target/Status:** Gran Turismo series: HD (playable/race audio), Prologue (playable/menu audio), 5 (menus only), 6 (menus only) (RPCS3)
- **Shell:** BusyBox v1.36.1
- **Custom Overlay:** MangoHUD

# What is the ETK and How Does it Really Work?
- **To enhance how the built-in PS3 emulator handles shader caching,** the ETK intercepts the Vulkan shader cache with a simple symlink and safely stores these files into a vault folder on your SD card organized by device and game ID so they can be archived and shared. Even when you crash during a shader harvesting run, the vault has saved the shaders for the next run.
- **To enhance how the device handles high demand games during the shader compiling process and high performance gaming,** the ETK manages the system temp and performance to safely overtax the device when it needs to work the hardest while preventing a total meltdown. It also modifies how the OS manages virtual memory and fine tunes the video driver.
- **To enhance how you can monitor the device system stress while pushing it to its limits,** the ETK enables a custom dashboard overlay using built-in Rocknix features across a thin horizontal HUD strip designed to evoke the Driver Data Unit (DDU) found in GT and F1 racing cars. The custom HUD DDU also shows the number of shaders harvested during a game session so you realize even if you crash, it was worth it.
- **To streamline how you can tweak key emulation settings,** the ETK PITSTOP app in the Rocknix Tools menu, inspired by pit wall screens, allows you to easily adjust selected configuration settings using the gamepad controls. The subset of on-board configs can be customized in a JSON file. 
- **To simplify managing game shader vaults and software updates,** the ETK includes a simple command-line utility to install, repair, update, and automatically sync shader vaults as you harvest from games or trade device and game-specific shader folders with others. It also includes an uninstall utility to retire from the league. A typical game 300+ MB shader vault will involve tens of thousands of binary files so an efficient transfer mechanism to manage shader sets between a computer and the handheld devices is essential.
- ETK does all of this while trying to maintain a **minimal system footprint without subjecting your SD card to abuse.**

# Warnings and Recommendations
- Requires the patience and dedication of race car drivers. You will crash. But you will also win races that could otherwise not be played. ETK doesn't magically make your device run PS3 emulation, it only gives it a fighting chance with professional grade tools and system tunings. Shader sharing spares other players the harvest.
- Requires the exact Rocknix Nightly specified above. This does not work on the official release nor has it been tested or updated for other Rocknix nightly builds.
- Do not use your main ROM library SD card for this Rocknix install. Instead, use a reasonably sized (256GB or less) high quality dev card that you don't mind wearing out or needing to reflash. Put your favorite PS3 games on this card and wait until the ETK can be upgraded for a Rocknix (official) release before using on your main card.
- Do not install on your handheld device if you intend to use the warranty coverage or otherwise would protect it from track day abuse. If you wouldn't take your daily driver to the track, do not install highly experimental software on your only retro handeld the could potentially damage or brick it.
- OS updates, ETK uninstalls and other major system events may require a PPU recompilation which do take time. Putting the device on an ice pack or in the refrigerator will reduce thermal stress on the system during these intensive operations. 
- The ETK is designed with community shader sharing in mind.

# Custom ETK Gamepad Specifications
- Reserved:
  - `START` + `SELECT` + `R1` = Native Rocknix force quit
  - `HOME` = RPSC3 menu
  - `SELECT` = GT3P camera view toggle
- Implemented:
	- `R3` = PANIC BUTTON RECOVERY COMMAND (headless on-device Nuclear Recovery, single press)
	- `L3` = SHIFT (PIT/RACE thermal mode toggle)
		  
# Project History
- Phase 1: MVP proof-of-concept: now deprecated monolithic mvp/commander.sh achieved initial shader cache accumulation downscaled with no audio, essential commands and Excitebike UX proofed
- Phase 2: Modular professional grade deployable ETK: shader cache successfully upscaled
- Phase 3: Enabled on-board MangoHUD DDU: integrated live instrumentation
- Phase 4: Enabled Gamepad ETK pit controls: full un-tethered racing and shader harvesting
- Phase 5: Solved treadwear problem on SD card with RAM disk support
- Phase 6: Rocknix OS migrated to May 11 Nightly, dependency updated, preserved into repo, 
- Phase 7: Enabled robust crash reporting, diagnosis, advisory, ETK install tiers
- Phase 8: Attempted experimental incremental audio support
- Phase 9: Enabled game agnostic ETK
- Phase 10: Onboard ETK Command: `R3` as Recovery Panic Button
- Phase 11: Develop External Networking Gemini Dev Analytics Workflow Tools
- ***Phase 12:** Developing native Rocknix ETK app for utilities (Tools or carousel UI)
- Phase 13: Develop shader sharing and shader swarming features per device/per game serial
- Phase 14: Beta Testing
- Phase 15: Release

# Easy Install Guide (FULL Kit)
1. Create a local `~/etk` for the kit's extracted code
1. Edit `scripts/env.sh` so `RIG_IP` and `RIG_SSH` match your device's IP address found in `Rocknix START button` > `Network Settings` > `IP ADDRESS`
1. Run `./install.sh` to flash your device with the ETK

# How to Install PS3 Games with the ETK
The ETK solves the problem of installing PS3 Packages on Rocknix which is otherwise a ridiculous process.
1. Place a single PS3 `.pkg` and `.rap` into `/storage/roms/etk/pkg_install_drop/`
1. In Rocknick Tools > ETK Pistop > TOOLS > Install a stage PS3 package
1. Wait for the automated process where ETK will handle RPCS3 installation for you and follow the on screen overlay instructions
1. Quit ETK Pitstop after installation and Update Gamelists in Rocknix

# How to Use Simple Telemetry
The ETK Pitstop Rocknix Tools app records your sessions for each game. You must launch a game and quit to switch which app the ETK Pitstop app will display. It records your tuning changes in a session ledger with summary statistics to help you determine which settings have resulted in better play results.

## Advanced Feature: Google Gemini Pit Wall (Telemetry Hot Drop)
The ETK includes a zero-friction diagnostic bridge designed to connect the device's live telemetry and crash logs directly to Google's Gemini AI, completely bypassing the need to manually copy-paste massive log files or open dangerous ports on your home router. By leveraging a host computer and Google Drive, you can turn Gemini into your live pit mechanic.

**Requirements:**
- A host computer (Mac/Linux) on the same WiFi network as your handheld rig.
- Google Drive Desktop App installed and syncing on the host computer.
- Gemini Advanced with the Google Workspace extension enabled.

**Setup & Usage:**
1. Open `etk/tools/pit_wall_sync.sh` on your host computer and ensure the `GDRIVE_PATH` matches your local Google Drive directory (it will create an `ETK_Telemetry` folder inside it).
2. Ensure `RIG_SSH` in the script matches your device's IP address.
3. Before a heavy harvesting session or testing a new emulator config, run the script on your host computer: `./pit_wall_sync.sh`
4. Play your game. The script will quietly run in the background, mirroring your rig's RAM disk and crash logs to Google Drive every 5 seconds.
5. **If the emulator crashes:** Simply open your Gemini chat and type: `@Google Drive check my ETK_Telemetry/crash_logs/etk_crash_report.log and tell me what the error is.` The AI will read the file directly from your Drive and provide immediate diagnostic feedback.
6. Gemini will frequently argue with you that this is not possible but if you keep insisting it is possible, it will eventually relent and show you it works.