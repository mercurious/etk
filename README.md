# The Emulation Tuning Kit
A custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, thermal and shader protection, shader cache management and advanced in-game telematics. It guards hard-earned shaders from SD card failure, OS flashing, data corruption, device failure, loss or theft. Push your device to its limits while collecting shaders and recover from crashes so the game plays well after several attempts. Specifically designed to make Gran Turismo 5 Prologue playable on a Flip2 Snapdragon, the ETK adopts the racing metaphor throughout but should work for any type of PS3 game. The long term vision is a shader swarm system where the your device automatically seeds and leeches shaders through a tight-knit device-centric P2P network during a battery charge.

# Screenshot
ToDo.

# The Kit Features
1. Hardware and driver tunings for maximum performance
1. Optimized emulator game configurations tuned to the device hardware
1. Smart thermal protection to safely overdrive the device during shader harvesting
1. Automatic shader backup from your device to computer to share with other ETK users
1. Customized in-game overlay with ETK telematics inside MangoHUD
1. Pit wall remote terminal screen to monitor and control device rig with advanced crash recovery and analytics (`scripts/commander.sh`)
1. Install, configure, repair, and uninstall the kit remotely from a computer (`install.sh` and `uninstall.sh`)
1. Multi-Installation Options: FULL installation for initial shader harvesting and tuning, LITE installation for saturated shader sets with thermal protection only, RAW for stress testing without shader and thermal protections (`ETK_BUILD_TYPE` in `scripts/env.sh`)

# ETK Project Structure
- `AI_MANIFEST.md`: System Manual and Immutable Laws of ETK Development for AI
- `README.md`: You are reading it now.
- `install.sh`: Flashes the ETK onto your handheld from a computer
- `uninstall.sh`: Removes the ETK from your handheld from a computer
- `pit_wall_sync.sh`: Runs locally with Google Drive app to enable Google Drive telemetry with Gemini
- `/bin`:
  - `input_d.py`: Handles custom gamepad controls
  - `recovery.sh`: Headless Nuclear Recovery, invoked on-device by the `R3` panic button
  - `thermal_d.sh`: Handles system conditions
  - `vault_d.sh`: Handles archival of compiled shaders
  - `etk_pitstop.py`: Handles native Rocknix Tools App for editing emulation configs
- `/config`:
  - `rsyncd.config`: Handles deployment and backup between handheld and computer
  - `MangoHud.config`: Handles custom in-game on-screen overlay
  - `pistop_fields.json`: The subset of RPCS3 config settings for on-device edits
  - `etk_pitstop.sh`: Rocknix Tools app installed at `/storage/.config/modules/`
  - `config_NPUA80075.yml`: tuned RPCS3 configuration to GranTurismo 5 Prologue
- `/scripts`:
  - `commander.sh`: Pit Wall central unit with remote terminal DDU UI
  - `env.sh`: Establishes pit and race environment
  - `mango_bridge.sh`: Manages live telemetry and overlay display
  - `probe.sh`: Provides error logs
-  `/vault`: Large archive of Vulkan precompiled shader bins 

# ETK System Requirements
- **System:** Retroid Pocket Flip 2 (SM8250)
- **OS:** ROCKNIX (Nightly Build: 20260518)
- **Driver** MESA Turnip 260.1.0
- **Target:** Gran Turismo series HD (stable/playable), Prologue (stable/playable), 5 (menus only), 6 (menus only) (RPCS3)
- **Shell:** BusyBox v1.36.1
- **Custom Overlay:** MangoHUD

# What is the ETK and How Does it Really Work?
- **To enhance how the built-in PS3 emulator handles shader caching,** the ETK intercepts the Vulkan shader cache with a simple symlink and safely stores these files into a vault folder on your SD card organized by device and game ID so they can be archived and shared. Even when you crash during a shader harvesting run, the vault has saved the shaders for the next run.
- **To enhance how the device handles high demand games during the shader compiling process and high performance gaming,** the ETK manages the system temp and performance to safely overtax the device when it needs to work the hardest while preventing a total meltdown. 
- **To enhance how you can monitor the device system stress while pushing it to its limits,** the ETK enables a custom dashboard overlay using built-in Rocknix features across a thin horizontal HUD strip designed to evoke the Driver Data Unit (DDU) found in GT and F1 racing cars. The custom HUD DDU also shows the number of shaders harvested during a game session so you realize even if you crash, it was worth it.
- **To streamline how you can tweak key emulation settings,** the ETK PITSTOP app in the Rocknix Tools menu, inspired by pit wall screens, allows you to easily adjust selected configuration settings using the gamepad controls. The subset of on-board configs can be customized in a JSON file. 
- **To streamline how you can use AI to tune games or diagnose crashes,** the ETK attempts to use Google Drive and Gemini integration to make chatting about last crash telemetry stored on the device, synchronized with Google Drive, to make settings tweaks easy, accurate, and efficient. Always use @Google Drive to connect your account to a chat session.
- **To simplify managing game shader vaults and software updates,** the ETK includes a simple command-line utility to install, repair, update, and automatically sync shader vaults as you harvest from games or trade device and game-specific shader folders with others. It also includes a comprensive uninstall utility to retire from the league. A typical game 300+ MB shader vault will involve tens of thousands of binary files so an efficient transfer mechanism to manage shader sets between a computer and the handheld devices is essential.
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
- **Phase 10:** Onboard ETK Command: `R3` as Recovery Panic Button
- Phase 11: Develop External Networking Gemini Dev Analytics Workflow Tools
- Phase 12: Developing native Rocknix ETK app for utilities (Tools or carousel UI)
- Phase 13: Develop shader sharing and shader swarming features per device/per game serial
- Phase 14: Beta Testing
- Phase 15: Release

# Easy Install Guide (FULL Kit)
1. Create a local `~/etk` for the kit's extracted code
1. Edit `scripts/env.sh` so `RIG_IP` and `RIG_SSH` match your device's IP address found in `Rocknix START button` > `Network Settings` > `IP ADDRESS`
1. Run `./install.sh` to flash your device with the ETK

## Advanced Feature: The AI Pit Wall (Telemetry Hot Drop)
The ETK includes a zero-friction diagnostic bridge designed to connect the device's live telemetry and crash logs directly to Google's Gemini AI, completely bypassing the need to manually copy-paste massive log files or open dangerous ports on your home router.

By leveraging a host computer and Google Drive, you can turn Gemini into your live pit mechanic.

**Requirements:**
- A host computer (Mac/Linux) on the same WiFi network as your handheld rig.
- Google Drive Desktop App installed and syncing on the host computer.
- Gemini Advanced with the Google Workspace extension enabled.

**Setup & Usage:**
1. Open `pit_wall_sync.sh` on your host computer and ensure the `GDRIVE_PATH` matches your local Google Drive directory (it will create an `ETK_Telemetry` folder inside it).
2. Ensure `RIG_SSH` in the script matches your device's IP address.
3. Before a heavy harvesting session or testing a new emulator config, run the script on your host computer: `./pit_wall_sync.sh`
4. Play your game. The script will quietly run in the background, mirroring your rig's RAM disk and crash logs to Google Drive every 5 seconds.
5. **If the emulator crashes:** Simply open your Gemini chat and type: `@Google Drive check my ETK_Telemetry/crash_logs/etk_crash_report.log and tell me what the error is.` The AI will read the file directly from your Drive and provide immediate diagnostic feedback.
6. Gemini will frequently argue with you that this is not possible but if you keep insisting it is possible, it will eventually relent and show you it works.