# ETK BRIEF
A custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, thermal and shader protection, shader cache management with advanced in-game telematics and controls.

# ETK (EMULATOR TOOLKIT) MISSION MANIFEST
**TARGET HARDWARE:** Retroid Pocket Flip 2 (SM8250)
**TARGET OS:** Rocknix (Read-Only Root, BusyBox Environment) specific nightly build 20260513 with MESA Turnip 260.1.0
**CORE PHILOSOPHY:** Defeat Rocknix's filesystem limitations using a hybrid Persistent/Volatile architecture to enable high-performance PS3 emulation.

## THE IMMUTABLE LAWS (CRITICAL)
1. **NEVER TOUCH THE SHM:** The path `/dev/shm/etk_shm` is the sacred IPC backbone. Do not attempt to "optimize" or "unify" these paths into persistent storage.
2. **SINGLE SOURCE OF TRUTH:** `scripts/env.sh` is the ONLY file allowed to define environment variables. All other scripts MUST `source` it.
3. **SYMLINK SANCTITY:** The shader cache is a dynamic symlink: `/storage/.cache/mesa_shader_cache` -> `$VAULT_DIR`. Never use `rsync` on the live cache.
4. **AGNOSTIC IDENTITY:** The `TARGET_ID` must always be dynamic. It is sniffed from the active RPCS3 process via `pgrep` and `PARAM.SFO`.
5. **NO GNU-ISMS:** Rocknix uses **BusyBox**. Assume GNU-specific flags (`--long-options`, `grep -P`, `find -printf`) will fail. Use POSIX-compliant syntax only.

## ROCKNIX ARCHITECTURE (THE BOOT CHAIN)
1. **THE REBOOT WIPE:** Rocknix vaporizes `/dev/shm` on every reboot. Background processes must rebuild `$SHM_DIR` instantly upon execution.
2. **NO ROOT SYSTEMD:** Do NOT attempt to write to `/etc/systemd/system/`. Do NOT use `mount -o remount,rw /` to brute-force the root partition. The OS will reject it.
3. **PERSISTENCE VECTOR:** All custom systemd services MUST be written to `/storage/.config/system.d/` (Note the dot, not systemd). Rocknix natively maps this directory at boot. Use `systemctl enable /storage/.config/system.d/etk.service` to ensure absolute path resolution.4. 
4. **AUTOSTART LIMITATIONS:** Do NOT use `/storage/.config/autostart.sh` for backend/daemon execution. It is tied to the Wayland/EmulationStation UI load sequence and causes race conditions with MangoHud.

## BUSYBOX LIMITATIONS & PITFALLS
* **DU:** Use `du -k` (KB) or `du -m` (MB). BusyBox `du` often lacks `-h` (human-readable) or behaves inconsistently with it.
* **FIND:** BusyBox `find` is extremely limited. Avoid complex `-exec` or `-regex`. Use `find | wc -l` for counts.
* **STAT:** Do not use `stat --format`. Use `readlink` for symlink resolution.
* **PROC:** Since RPCS3 runs in an encapsulated AppImage/Dwarfs mount, always use `/proc/$PID/cmdline` and `/proc/$PID/environ` for discovery.
* **AWK:** Use `awk` for floating-point math; BusyBox `sh` cannot handle decimals and `bc` may not be present in all builds.
* **VIRTUAL HDD PATH PATTERNS:** Do NOT assume standard desktop path resolutions or that global mapping roots are used (e.g., `dev_hdd0/savedata` only anchors empty `vmc` volumes). Rocknix isolates actual emulator user save blocks inside localized nested structures under the individual user profile index:
  `~/roms/bios/rpcs3/dev_hdd0/home/00000001/savedata/`
  Targeted cleaning operations or resets must trace files from this exact explicit directory footprint.

## FILE REGISTRY (THE ETK ANATOMY)
### 1. CORE ENGINE
* **env.sh:** The Heart. Defines paths, thermal thresholds, and the Agnostic ID logic.
* **install.sh:** The Provisioner. Deploys the toolkit, sets up symlinks, and generates the `01-etk-startup.sh` Sentry.
* **uninstall.sh:** The Cleaner. Restores stock hardware states (GPU/CPU governors) and stops daemons.

### 2. DAEMONS (THE WORKERS)
* **01-etk-startup.sh (etk_sentry.service):** The State Machine. Runs constantly in the background. Tracks emulator ignition (IDLE vs RUNNING), resolves the Game ID, and orchestrates the live/die cycles of the thermal and vault daemons.
* **vault_d.sh:** The Accountant. Resides in `/bin`. Tracks "NEW" vs "BANKED" shaders in real-time.
* **thermal_d.sh:** The Governor. Resides in `/bin`. Manages CPU/GPU clocks and triggers "PIT" (Cooldown) vs "RACE" (Performance) modes.
* **input_d.py:** The Shifter. Python daemon that maps Xbox virtual controller inputs (Select+R3) to ETK commands.

### 3. INTERFACES (THE UI)
* **mango_bridge.sh:** The Dashboard. Translates SHM data into MangoHud custom text.
* **commander.sh:** The Pit Wall. Remote terminal UI for manual overrides, shader backups, and diagnostics.
* **probe.sh:** The Forensics. Captures kernel logs and RPCS3 traces for crash analysis.

### 4. UTILITIES
* **agnostify.sh:** The Migrator. Converts legacy hardcoded GT5P installs into the new Agnostic vault structure. (CONDSIDER FOR DEPCRATION)

## SHM DATA MAP (/dev/shm/etk_shm/)
* `active_id.txt`: The current Game ID (e.g., NPUA80075).
* `etk_mode.txt`: Current thermal profile (RACE/PIT).
* `live_stat.txt`: The final string injected into MangoHud.
* `vault_count`: Total shaders in the active vault.
* `vault_new.txt`: Shaders compiled during the *current* session.
* `etk_cmd_queue`: Pipe for `commander.sh` to send instructions to daemons.

## [LOCKDOWN] AGNOSTIC HUD STABILITY
- **CRITICAL**: The `TARGET_ID` must be resolved via the Deep Scan loop in the Sentry daemon to handle multi-process emulator environments.
- **HUD FORMAT**: `mango_bridge.sh` MUST use the atomic `echo > file.tmp && mv file.tmp file` pattern.
- **STRING ARCHITECTURE**: Do NOT add configuration keys like `custom_text_center=` inside the `live_stat.txt` file; keep the string raw for external parsing.
- **PATHING**: All background daemons (Bridge, Vault, Thermal) must be invoked using absolute paths via `$ETK_ROOT` to ensure reliability across reboots.

## [LOCKDOWN] ARCH: install.sh must remain Tier-Aware.
- VARIABLE: ETK_BUILD_TYPE (defined in env.sh) controls the execution of thermal_d.sh and mango_bridge.sh.
- CLEANUP: Moving from FULL to LITE must trigger the Sentry to kill active HUD/Thermal processes.

## [LOCKDOWN] DEV TOOLS

## UI ARCHITECTURE: commander.sh MUST maintain a split-pane layout. Top pane = Live Telemetry. Bottom pane = Raw Forensic Text. Do not consolidate into a single "Mode."

## FORENSIC INTEGRITY: probe.sh MUST use strings when reading any file from /storage or /dev/shm to prevent terminal corruption during binary floods.

## COPY-PASTE OPTIMIZATION: The bottom pane of the Pit Wall terminal MUST remain free of ANSI color codes or borders within the data content to facilitate seamless copying into Gemini chat.

## IMMUTABLE COMMENTS: Any script update MUST include a "GEMINI IMMUTABLE RULE" block in the header to inform subsequent models of structural constraints.

## [LOCKDOWN] HUD FORMATTING

- **HUD FORMAT STRICT LOCK**: The instrument string layout is locked to a dense, space-trimmed format that uses punctuation and short text strings to serve as the DDU (Driver Data Unit from racing cars) to preserve Flip 2 screen real estate. Future iterations MUST NOT expand spacing or add decorative characters unless custom font and unicode support is feasible, recommended, tested, and approved.
  - *Format:* `ETK:MODE|TARGET_ID|XX°C STAT|X.XX STAT|XX% STAT|XXMB XXX NEW: XX`
  - *HUDMode* SPEEDO (vs PRESET SELECTOR see below)

## RECENT NEW DEVELOPMENTS

- **SENTRY STATE MACHINE (THE IGNITION LOCK)**: The `etk_sentry` service MUST operate as an event-driven state machine. It tracks the `rpcs3` process transitioning between `IDLE` and `RUNNING`.
  - Background daemons (`vault_d.sh`, `thermal_d.sh`) MUST NOT be executed during the OS boot sequence.
  - The Sentry must wait 3 seconds after `RUNNING` is detected before launching daemons to ensure `TARGET_ID` is established from `PARAM.SFO`. This prevents the `BANK: 0` race condition.
  - When transitioning back to `IDLE` (graceful exit or nuclear recovery), the Sentry MUST actively `pkill` the daemons to flush stale memory.

## PROPOSALS TO DO

### DEVELOP ALT GITHUB CONNECTED ACCESS REPLACEMENT TOOLS
- If we cannot get Github Connected Apps provisioned because of license limitations, what tool can integrate into the ETK to get closer to a GitHub enabled workflow despite not having full access, accepting certain limitations, but being clever about working around them?

### FIX NEW VAULT COUNT BUG
- Use new event state model to reset the NEW count every game launch and increment with every new shader added during that game launch session.

### RESTORE AND LOCK AUTO RSYNC SHADER PROTECTION AND AUTO MANAGEMENT AT `install.sh`
- Gemini designed beautiful tethered shader saver system and then a later Gemini erased it.
- **SYNC Logic Specification**: 
- PULL: New shaders always archived on computer at every run of `install.sh` so it works as a repair, update, and sync tool.
- PUSH: sync found games on handheld with shaders found in computer vault.
- `uninstall.sh` should remove vaults (unless they can be injected into the regular Vulkan or RCPS3 cache so ETK would not be needed for their use after uninstall?)

### PLAN FOR A REFACTOR FOR FULLY ONBOARD ETK
- Go headless and move away from a tethered, commander.sh dependent rig
- Preserve and enrich commander.sh as dev tool rather than used during harvesting runs, more for crash analytics and to continue to support the overhaul of on-board systems
- Correct the location of `scripts/mango_bridge.sh` into `bin/` where daemons are expected to live as the ETK goes headless and increasingly event-based.
- Trap R3 as a PANIC BUTTON that calls Recovery command

### PLAN FOR NEW ONBOARD 'NEXT PRESET' CONFIG SELECTION UI
- Trap L3 as a CLUTCH to toggle HUD modes from SPEEDO to PRESET SELECT modes
- with R analog left and right that 'swipes' between an array of preset emulator config settings displayed in the HUD
- Releasing L3 CLUTCH confirms selection and returns HUD to SPEEDO display
- Selected custom config preset is preloaded to be injected into emulator at next game launch. Not intended for live config injection into the emulator; dangerous.

### PRESET CONFIGS: Proposed initial on-board preset tiers; The config set should be designed to be easily modified in dev crash test cycles so they can be tuned to the device and potentially also tuned to individual games and easily expandable by advanced ETK users
- BASE: Performance Target for saturated shader sets
- PACE: Shader Harvesting optimizations
- CORE: Survival Blueprint for extreme resource isolation

### SAMPLE TROUBLESHOOTING WORKFLOW
[RACE LOADS ON BASE] ---> (Turnip Driver crash)
									|
									|
						[Triger R3 Panic Recovery]
									|
									|
[Reboot and/or Relaunch Game] -> [Trigger L3 (Engage HUD Clutch)]  -> [Flick R3 Right to PACE]
									|
									|
						[Exit Emulator to Frontend]
									|
									|
	[Ignition Cycle] ---> Sentry Injects 'PACE' Template -> [Launch Game Safetly]