# ETK BRIEF
A custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, thermal and shader protection, shader cache management with advanced in-game telematics and controls.

# ETK (EMULATOR TOOLKIT) MISSION MANIFEST
**TARGET HARDWARE:** Retroid Pocket Flip 2 (SM8250)
**TARGET OS:** Rocknix (Read-Only Root, BusyBox Environment) nightly build 20260610 with MESA Turnip 26.1.2 + RPCS3 0.0.41-19444 (nightly pin is deliberate — it carries the upstream GT5 memory-leak fix, RPCS3 PR #18844)
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
4. **AUTOSTART LIMITATIONS:** Do NOT use `/storage/.config/autostart/` for backend/daemon execution — but for the correct reason. Rocknix's `rocknix-autostart.service` runs each script **synchronously** at boot and is ordered `Before=` the UI service (its final act is launching the UI). A long-running or blocking script dropped there **stalls UI bring-up**. A persistent supervisor therefore belongs in its own systemd unit under `/storage/.config/system.d/` (where ETK already puts `etk.service`). NOTE: the earlier claim that autostart "causes race conditions with MangoHud" was **unsupported and has been empirically disproven** (2026-06-01) — autostart completes at boot (~12 s), while MangoHud is a game-launch overlay loaded by `runemu.sh` at an arbitrary later time; the two never share a time window. Re-run the proof with `tools/probe_autostart_race.sh`.

## ROCKNIX SD CARD BASED STORAGE PATHS
1. `/storage/roms` is the same as `/storage/games-internal/roms` on single-card devices such as the target device (Retroid Pocket Flip 2)
1. `/storage/games-external/roms` is used for storing games on the second card slot on a two slot device. Do not let this throw you off.

## ROCKNIX TOOLS ARCHITECTURE & LAYER MAPPING

### Dynamic Tools Menu Intercept
Unlike traditional emulation platforms, Rocknix compiles its native "Tools" system carousel by scraping a specialized backend configuration path rather than loose ROM folders.
- **Persistent Source Path:** `/storage/.config/modules/`
- **Target Extensions:** `.sh` (Must possess explicit `chmod +x` execution permissions)
- **Frontend Render Engine:** EmulationStation spawns items within this module using the Wayland-native terminal emulator `foot`.

### Terminal Rendering Architecture
The system invokes scripts using the absolute execution command: `/usr/bin/foot %ROM%`.
- To adjust text sizing and resolution matrices for high-DPI panels (e.g., Retroid Pocket Flip 2), downstream middleware scripts must implement a nested runtime override.
- Runtime scaling is achieved by invoking a secondary breakout layer: 
  `foot -F -o font="monospace:size=XX" <target>`
- This technique bypasses immutable system-wide font profiles and avoids breaking layout stability for neighboring OS tools.

## BUSYBOX LIMITATIONS & PITFALLS
* **DU:** Use `du -k` (KB) or `du -m` (MB). BusyBox `du` often lacks `-h` (human-readable) or behaves inconsistently with it.
* **FIND:** BusyBox `find` is extremely limited. Avoid complex `-exec` or `-regex`. Use `find | wc -l` for counts.
* **STAT:** Do not use `stat --format`. Use `readlink` for symlink resolution.
* **PROC:** Since RPCS3 runs in an encapsulated AppImage/Dwarfs mount, always use `/proc/$PID/cmdline` and `/proc/$PID/environ` for discovery.
* **AWK:** Use `awk` for floating-point math; BusyBox `sh` cannot handle decimals and `bc` may not be present in all builds.


## THE TWO PACKAGING MODELS (.pkg vs .iso) — FORMAT IS A FIRST-CLASS VARIABLE
PS3 titles arrive as two DISTINCT RUNTIME MODELS, not two file extensions. ETK tooling is ~100% .pkg-biased (measured 2026-06-12: zero iso/bdvd handling) — mostly rational economics (.pkg needs machinery: headless installer, .rap licenses, gamelist injection; .iso = copy a file), but the runtime differences are load-bearing:
1. **Spawn topology:** digital/.pkg GT titles are DRM-SPAWN (`EBOOT.BIN` launcher → spawns `EMAIN.SELF`); disc/.iso boots direct. This single axis explained the card#2 saga (disc fine, DRM-spawn crashed) and forced the flush-after-burst design in the aPS3e VkPipelineCache patch (spawned executables never reach clean teardown).
2. **Cache topology:** per-executable `ppu-<hash>` cache dirs → DRM-spawn titles carry TWO pipeline caches (launcher + game), disc titles one. Vault accounting and pro-tune bundles must know which executable's cache matters.
3. **I/O model:** .pkg = scattered reads from dev_hdd0; .iso = streaming one large file (GT5 = 19.4 GB). Treat as separate test classes in any storage-tier experiment.
4. **DRM:** .pkg needs `.rap`; .iso needs a decrypted image (or `.dkey`). Failure smells differ: `CELL_ESRCH` (DRM-spawn init) vs `CELL_ENOTMOUNTED /dev_bdvd`.
5. **Update asymmetry (UNTESTED PATH):** disc games take updates AS .pkg into `dev_hdd0/game/<ID>` — ETK's PKG installer has never been tested with an update-PKG whose base game is a disc image.

**LAW: any emulator/cache/install fix MUST be validated on BOTH models before being called done.** The cache-bug history shows why: a teardown-save pipeline cache would have *appeared correct* on .iso titles and silently failed on DRM-spawn .pkg titles — the format axis hides bugs from fixers.

**OBSERVABILITY BIAS (operator doctrine, 2026-06-12):** shader-storm pain filters which games stay in player rotation. Light-shader titles (e.g. RR7) play well immediately → high rotation → generate most community evidence. Shader-heavy titles are abandoned before saturation → the players who CAN observe deep cache/stability bugs are rare "gluttons for punishment" obsessing over one series on one device. Therefore: absence of community reports on a shader-heavy-title bug is WEAK evidence of absence; compatibility lore systematically under-represents exactly the titles ETK exists for. Weigh on-rig telemetry over community consensus accordingly.

## ROCKNIX RPCS3 PATHS
* **CUSTOM CONFIGURATION FILES** `/storage/roms/bios/rpcs3/custom_configs/config_[GAMEID].yml`
* **ETK Cache Vault Symlimk** `/storage/roms/bios/rpcs3/cache/[GAMEID]`
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

### 5. DESIGN DOSSIERS (LOCAL-PRIVATE — NOT IN GIT)
* **`dossiers/`** is the design-rationale archive: one Markdown file per major decision (e.g. `ProTuningExportDossier.md`, `Stage3CustomRigDossier.md`, certification dossiers). It lives at `$ETK_ROOT/dossiers` on the operator's machine ONLY.
* **STATUS:** pulled from the public repo and **gitignored as of 2026-06-14** — same precedent as `vault/` and `state/`. The dossiers stay local and private; they are never committed, cloned, or archived.
* **DO NOT BE CONFUSED BY THEIR ABSENCE:** code comments and docs cite them heavily (`dossiers/<Name>Dossier.md §N`, or shorthand `dossier §N`). Those citations are design provenance, not a promise the file is in the checkout. If you clone the repo, the referenced files will not be present — this is expected, not a missing-file bug. Read them locally if available; do not try to "restore" them into the repo.

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

## COPY-PASTE OPTIMIZATION: The bottom pane of the Pit Wall terminal MUST remain free of ANSI color codes or borders within the data content to facilitate seamless copying into an AI assistant for analysis.

## IMMUTABLE COMMENTS: Any script update MUST include a "GEMINI IMMUTABLE RULE" block in the header to inform subsequent models of structural constraints.

## [LOCKDOWN] HUD FORMATTING

- **HUD FORMAT STRICT LOCK**: The instrument string layout is locked to a dense, space-trimmed format that uses punctuation and short text strings to serve as the DDU (Driver Data Unit from racing cars) to preserve Flip 2 screen real estate. Future iterations MUST NOT expand spacing or add decorative characters unless custom font and unicode support is feasible, recommended, tested, and approved.
  - *Format:* `ETK:INSTALL_MODE|TARGET_ID|XX°C STAT|X.XX STAT|XX% STAT|XXMB XXX NEW:XX|`
  
### [LOCKDOWN]

- **SYNC Logic Specification**:
- PULL: New shaders always archived on computer at every run of `install.sh` so it works as a repair, update, and sync tool. All transfers must include professional `--progress` reporting indicators and clear out `.DS_Store` metadata noise.
- PUSH: sync found games on handheld with shaders found in computer vault.
- `uninstall.sh` should remove vaults (unless they can be injected into the regular Vulkan or RCPS3 cache so ETK would not be needed for their use after uninstall?)


## DEFEATING VOLATILE DIRECTORIES (THE SENTRY TRIPWIRE)

**THE ROCKNIX REBOOT BOSS:** Rocknix aggressively wipes and rebuilds user-interface directories like `/storage/.config/modules/` during the boot sequence. This wipe is highly asynchronous and often happens *after* `systemd` services tied to `multi-user.target` have already executed.

**THE IMMUTABLE RULE:** Never rely on a one-shot deployment or a static boot script to persist files in Rocknix volatile directories. 

**THE SOLUTION (ACTIVE TRIPWIRE):** To keep native bash apps (like `etk_pitstop.sh`) persistent in the Tools menu, the `etk_sentry` daemon MUST employ a self-healing active tripwire inside its primary polling loop.

1. Store the master copy of the module safely in persistent storage (e.g., `$ETK_ROOT/config/`).
2. Inside the Sentry's `while true` loop, continuously monitor the volatile target directory.
3. If the file is missing, immediately re-inject it and re-apply `chmod +x`.

**Implementation Standard:**
`bash
# Inside the Sentry while loop:
if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ]; then
    cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
    chmod +x /storage/.config/modules/etk_pitstop.sh
fi]`


## RECENT NEW DEVELOPMENTS

- **SENTRY STATE MACHINE (THE IGNITION LOCK)**: The `etk_sentry` service MUST operate as an event-driven state machine. It tracks the `rpcs3` process transitioning between `IDLE` and `RUNNING`.
  - Background daemons (`vault_d.sh`, `thermal_d.sh`) MUST NOT be executed during the OS boot sequence.
  - The Sentry must wait 4 seconds after `RUNNING` is detected before launching daemons to ensure the `TARGET_ID` string is captured cleanly from process parameters.
  - **RACE-PROOF IDENTITY SYNC**: To eliminate path-shifting race conditions, the Sentry must completely resolve and commit the definitive Game ID string to `ID_FILE` **before** spawning any downstream worker daemons.
  - **ATOMIC SESSION RESET**: The Sentry must atomically reset `vault_new.txt` to `0` at the exact moment of emulator ignition. This ensures worker daemons baseline cleanly from zero every single game launch without relying on internal automated loop resets.
  - When transitioning back to `IDLE` (graceful exit or nuclear recovery), the Sentry MUST actively `pkill` the daemons to flush stale memory.


