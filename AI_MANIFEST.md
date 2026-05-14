# ETK (EMULATOR TOOLKIT) MISSION MANIFEST
**TARGET HARDWARE:** Retroid Pocket Flip 2 (SM8250)
**TARGET OS:** Rocknix (Read-Only Root, BusyBox Environment) specific nightly build
**CORE PHILOSOPHY:** Defeat Rocknix's filesystem limitations using a hybrid Persistent/Volatile architecture to enable high-performance PS3 emulation.

## THE IMMUTABLE LAWS (CRITICAL)
1. **NEVER TOUCH THE SHM:** The path `/dev/shm/etk_shm` is the sacred IPC backbone. Do not attempt to "optimize" or "unify" these paths into persistent storage.
2. **SINGLE SOURCE OF TRUTH:** `scripts/env.sh` is the ONLY file allowed to define environment variables. All other scripts MUST `source` it.
3. **SYMLINK SANCTITY:** The shader cache is a dynamic symlink: `/storage/.cache/mesa_shader_cache` -> `$VAULT_DIR`. Never use `rsync` on the live cache.
4. **AGNOSTIC IDENTITY:** The `TARGET_ID` must always be dynamic. It is sniffed from the active RPCS3 process via `pgrep` and `PARAM.SFO`.
5. **NO GNU-ISMS:** Rocknix uses **BusyBox**. Assume GNU-specific flags (`--long-options`, `grep -P`, `find -printf`) will fail. Use POSIX-compliant syntax only.

## BUSYBOX LIMITATIONS & PITFALLS
* **DU:** Use `du -k` (KB) or `du -m` (MB). BusyBox `du` often lacks `-h` (human-readable) or behaves inconsistently with it.
* **FIND:** BusyBox `find` is extremely limited. Avoid complex `-exec` or `-regex`. Use `find | wc -l` for counts.
* **STAT:** Do not use `stat --format`. Use `readlink` for symlink resolution.
* **PROC:** Since RPCS3 runs in an encapsulated AppImage/Dwarfs mount, always use `/proc/$PID/cmdline` and `/proc/$PID/environ` for discovery.
* **AWK:** Use `awk` for floating-point math; BusyBox `sh` cannot handle decimals and `bc` may not be present in all builds.

## FILE REGISTRY (THE ETK ANATOMY)
### 1. CORE ENGINE
* **env.sh:** The Heart. Defines paths, thermal thresholds, and the Agnostic ID logic.
* **install.sh:** The Provisioner. Deploys the toolkit, sets up symlinks, and generates the `01-etk-startup.sh` Sentry.
* **uninstall.sh:** The Cleaner. Restores stock hardware states (GPU/CPU governors) and stops daemons.

### 2. DAEMONS (THE WORKERS)
* **01-etk-startup.sh:** The Sentry. Runs at boot. Sniffs for game launches and physically swaps the `/storage/.cache` symlink to the correct vault.
* **vault_d.sh:** The Accountant. Resides in `/bin`. Tracks "NEW" vs "BANKED" shaders in real-time.
* **thermal_d.sh:** The Governor. Resides in `/bin`. Manages CPU/GPU clocks and triggers "PIT" (Cooldown) vs "RACE" (Performance) modes.
* **input_d.py:** The Shifter. Python daemon that maps Xbox virtual controller inputs (Select+R3) to ETK commands.

### 3. INTERFACES (THE UI)
* **mango_bridge.sh:** The Dashboard. Translates SHM data into MangoHud custom text.
* **commander.sh:** The Pit Wall. Remote terminal UI for manual overrides, shader backups, and diagnostics.
* **probe.sh:** The Forensics. Captures kernel logs and RPCS3 traces for crash analysis.

### 4. UTILITIES
* **agnostify.sh:** The Migrator. Converts legacy hardcoded GT5P installs into the new Agnostic vault structure.

## SHM DATA MAP (/dev/shm/etk_shm/)
* `active_id.txt`: The current Game ID (e.g., NPUA80075).
* `etk_mode.txt`: Current thermal profile (RACE/PIT).
* `live_stat.txt`: The final string injected into MangoHud.
* `vault_count`: Total shaders in the active vault.
* `vault_new.txt`: Shaders compiled during the *current* session.
* `etk_cmd_queue`: Pipe for `commander.sh` to send instructions to daemons.

## [LOCKDOWN] PHASE 13: AGNOSTIC HUD STABILITY
- **CRITICAL**: The `TARGET_ID` must be resolved via the Deep Scan loop in the Sentry daemon to handle multi-process emulator environments.
- **HUD FORMAT**: `mango_bridge.sh` MUST use the atomic `echo > file.tmp && mv file.tmp file` pattern.
- **STRING ARCHITECTURE**: Do NOT add configuration keys like `custom_text_center=` inside the `live_stat.txt` file; keep the string raw for external parsing.
- **PATHING**: All background daemons (Bridge, Vault, Thermal) must be invoked using absolute paths via `$ETK_ROOT` to ensure reliability across reboots.