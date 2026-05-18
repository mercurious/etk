# ETK BRIEF
A custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, thermal and shader protection, shader cache management with advanced in-game telematics and controls.

# ETK (EMULATOR TOOLKIT) MISSION MANIFEST
**TARGET HARDWARE:** Retroid Pocket Flip 2 (SM8250)
**TARGET OS:** Rocknix (Read-Only Root, BusyBox Environment) specific nightly build 20260517 with MESA Turnip 260.1.0
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
3. **PERSISTENCE VECTOR:** All custom systemd services MUST be written to `/storage/.config/system.d/` (Note the dot, not systemd). Rocknix natively maps this directory at boot. Use `systemctl enable /storage/.config/system.d/etk.service` to ensure absolute path resolution.
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
* **install.sh:** The Provisioner. Deploys the toolkit, sets up symlinks, and generates the `01-etk-sentry.sh` Sentry.
* **uninstall.sh:** The Cleaner. Restores stock hardware states (GPU/CPU governors) and stops daemons.

### 2. DAEMONS (THE WORKERS)
* **01-etk-sentry.sh (etk_sentry.service):** The State Machine. Runs constantly in the background. Tracks emulator ignition (IDLE vs RUNNING), resolves the Game ID, and orchestrates the live/die cycles of the thermal and vault daemons.
* **vault_d.sh:** The Accountant. Resides in `/bin`. Tracks "NEW" vs "BANKED" shaders in real-time.
* **thermal_d.sh:** The Governor. Resides in `/bin`. Manages CPU/GPU clocks and triggers "PIT" (Cooldown) vs "RACE" (Performance) modes.
* **input_d.py:** The Shifter. Python daemon that maps Xbox virtual controller inputs (Select+R3) to ETK commands.

### 3. INTERFACES (THE UI)
* **mango_bridge.sh:** The Dashboard. Translates SHM data into MangoHud custom text.
* **commander.sh:** The Pit Wall. Remote terminal UI for manual overrides, shader backups, and diagnostics.
* **probe.sh:** The Forensics. Captures kernel logs and RPCS3 traces for crash analysis.

### 4. UTILITIES
* **agnostify.sh:** The Migrator. Converts legacy hardcoded GT5P installs into the new Agnostic vault structure. (CONSIDER FOR DEPRECATION)

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
  
## [LOCKDOWN] DEV TOOLS: THE PIT WALL SYNC (AI TELEMETRY BRIDGE)
- **ARCHITECTURE:** To bypass the lack of GitHub Connected Apps, the ETK utilizes a "Hot Drop" telemetry bridge via Google Drive. A host machine script (`pit_wall_sync.sh`) continuously polls the rig via SSH and mirrors `/storage/etk_crash_report.log` and `/dev/shm/etk_shm/` to a local Google Drive folder (`ETK_Telemetry`).
- **AI INSTRUCTION (CRITICAL):** Do NOT ask the user to manually copy-paste crash logs or terminal outputs. 
- **FORENSIC PROTOCOL:** When a user reports a crash or requests tuning, instruct the user to ensure `pit_wall_sync.sh` is running. Then, use your Google Workspace integration to directly read the user's Google Drive. 
  - Look in `ETK_Telemetry/crash_logs/etk_crash_report.log` for kernel panics and Turnip driver traces.
  - Look in `ETK_Telemetry/live_shm/` for real-time rig state (e.g., `live_stat.txt`, `etk_mode.txt`, `vault_new.txt`).
- This creates a zero-friction, automated AI-human dev loop without requiring open router ports or exposed webhooks.

### [LOCKDOWN]

- **SYNC Logic Specification**:
- PULL: New shaders always archived on computer at every run of `install.sh` so it works as a repair, update, and sync tool. All transfers must include professional `--progress` reporting indicators and clear out `.DS_Store` metadata noise.
- PUSH: sync found games on handheld with shaders found in computer vault.
- `uninstall.sh` should remove vaults (unless they can be injected into the regular Vulkan or RCPS3 cache so ETK would not be needed for their use after uninstall?)


## RECENT NEW DEVELOPMENTS

- **SENTRY STATE MACHINE (THE IGNITION LOCK)**: The `etk_sentry` service MUST operate as an event-driven state machine. It tracks the `rpcs3` process transitioning between `IDLE` and `RUNNING`.
  - Background daemons (`vault_d.sh`, `thermal_d.sh`) MUST NOT be executed during the OS boot sequence.
  - The Sentry must wait 4 seconds after `RUNNING` is detected before launching daemons to ensure the `TARGET_ID` string is captured cleanly from process parameters.
  - **RACE-PROOF IDENTITY SYNC**: To eliminate path-shifting race conditions, the Sentry must completely resolve and commit the definitive Game ID string to `ID_FILE` **before** spawning any downstream worker daemons.
  - **ATOMIC SESSION RESET**: The Sentry must atomically reset `vault_new.txt` to `0` at the exact moment of emulator ignition. This ensures worker daemons baseline cleanly from zero every single game launch without relying on internal automated loop resets.
  - When transitioning back to `IDLE` (graceful exit or nuclear recovery), the Sentry MUST actively `pkill` the daemons to flush stale memory.


