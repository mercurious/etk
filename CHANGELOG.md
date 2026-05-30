# Changelog

All notable changes to the ETK are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.3] - Unreleased

### Fixed
- **Phantom `ABORTED` sessions polluting the ledger (crash-analytics integrity).** The Sentry detected a running emulator with `pgrep -f "rpcs3|AppRun.wrapped"`, where `-f` matches the whole command line — so it also matched any process whose argv merely referenced an **rpcs3 path**, notably `session_postmortem.sh`'s `strings /storage/.cache/rpcs3/RPCS3.log`. On a log-verbose title (Ridge Racer 7's `RPCS3.log` reaches ~288 MB) that `strings` outlived the Sentry's 2 s tick, so the Sentry mistook its own log-parser for a live game and ignited a **self-reinforcing loop** of phantom sub-threshold sessions — each <60 s, reclassified `ABORTED` — burying the real `CLEAN`/`RECOVERY` rows (RR7 read 21 ABORTED vs 4 CLEAN). Fixes: (1) the Sentry now matches the emulator by **exact process name** — `pgrep -x rpcs3 || pgrep -x AppRun.wrapped` (the same comms `recovery.sh` tears down) — at both the state-detection and orphan-PANIC-guard sites; (2) `session_postmortem.sh` reads the log via bounded stdin redirect (`tail -c 4M <"$RPCS3_LOG" | strings`) so the parser's argv no longer carries the rpcs3 path and the scan is bounded. Verified on-rig: the log-parser no longer registers as a running emulator.

## [0.1.2] - 2026-05-29

**Screenshot trigger is now operator-controlled, Tools-menu icon fixed, and certified against Rocknix nightly-20260529.** The `L1` screenshot shutter no longer fires unconditionally — it has a three-state mode so you can scope it to gameplay or free the button for the game entirely.

### Added
- **Three-state `L1` screenshot mode** — `in-game` (default) / `always` / `disabled`, cycled live from **Pitstop → TOOLS → "Screenshot on L1"**. Persisted to `etk_telemetry/screenshot_mode.txt` and read by `bin/input_d.py` on every L1 press, so a toggle takes effect with **no daemon restart**. The mode is shared via `$SCREENSHOT_MODE_FILE` (`scripts/env.sh`).

### Fixed
- **ETK Pitstop Tools-menu icon now renders on the stock theme.** The default Rocknix theme (`es-theme-art-book-next`) hides the standard `<image>` mapping and draws Tools art from `<thumbnail>`/`<marquee>` instead, so our image-only entry showed no icon. `etk_modules_inject.py` now emits all three artwork fields (→ `etk_pitstop.svg`), so the tile appears regardless of which artwork subset the theme uses — independent of the platform-wide Rocknix bug where *no* stock Tools icon renders (diagnosed on-rig, reported upstream; see `dossiers/ToolsMenuArtworkDiagnosis.md`). The SVG was never the problem — a PNG in the same field was equally blank.
- **`L1` screenshot fired in every context, with no way to disable it.** It now respects the mode above: `disabled` stops ETK shooting on L1 — genuinely freeing the button for a game that binds it (ETK never `EVIOCGRAB`s the pad, so L1 always reaches the game regardless); `in-game` suppresses accidental frontend/Pitstop captures. The deliberate `SELECT` + `D-pad Up` chord is **not** gated and always works as a manual shutter.

### Changed
- **Default screenshot behavior is now `in-game`** (was effectively `always`). Existing rigs with no mode file inherit `in-game` on first boot after upgrade. Set `always` in Pitstop → TOOLS if you capture the frontend / Pitstop UI (e.g. for README shots).

### Verified
- **Certified on Rocknix nightly-20260529** (in-place migration from 20260528, SM8250). `etk_drift.py --check` clean (no structural drift); the 10 `--diff` input CRITICALs were benign node renumbering (the DualSense buttons device moved `event9→event8`; `find_gamepad()` self-healed by name). Manual headless gate passed: gamepad codes unchanged (R3=318/L3=317/L1=310/SELECT=314/D-pad=16,17), **R3 panic survives suspend/resume** (29's fake-suspend rewrite), RPCS3 binds `Turnip Adreno (TM) 650`, and the v0.1.2 screenshot + Tools-icon features work on 29. Profile re-cal notes bumped to 20260529. See `dossiers/RocknixNightly20260529CertificationDossier.md`.

## [0.1.1] - 2026-05-29

**Windows installer port + automatic SSH pairing.** A Windows PC can now act as the ETK host without WSL, and first-run SSH setup is automatic — type the rig password once, never again.

### Added
- **Automatic host → rig SSH pairing wizard** — `scripts/etk_pair.sh` (bash) and `windows_installer/etk-pair.ps1` (PowerShell). Idempotent and test-first: a cold pair takes **at most one** password (Rocknix default `rocknix`); every later `ssh`/`scp` is silent. It:
  - generates a dedicated, no-passphrase key `~/.ssh/etk_rig` (never touches your existing `id_*` keys),
  - installs it on the rig **carriage-return-safe** and **without clobbering** an existing key (an unrelated user key in `authorized_keys` is preserved),
  - writes an `~/.ssh/config` block so the bare `root@<rig>` target the installer uses is passwordless.
  - Re-runs cost **zero** passwords; a host that already has working SSH is detected and left untouched.
- `./install.sh --pair` and `etk-pair.ps1` run pairing **standalone** (e.g. to re-pair after a card reflash). The installers also auto-pair before their first remote call.
- The rig-side key-install logic lives **once** in `etk_pair.sh` and is pulled into the PowerShell port via `Get-Heredoc` — single source of truth, exactly like the Sentry/systemd-unit blocks.
- **Verified the Windows PowerShell installer end-to-end** on a real SM8250 rig (no-vault): cold pair → full deploy → live Sentry, zero passwords after the first.
- **OS-migration drift detector** — `tools/etk_drift.py` (repurposed from the unused recon tool). Banks nightly-keyed OS profiles and diffs a live Rocknix nightly against your pinned baseline and the device profile's pinned assumptions (`--save-baseline` / `--diff` / `--check` / `--list`), so you can tell whether a nightly is safe to adopt before committing to it.

### Fixed
- **`Invoke-Rig` CRLF bug (Windows port):** multi-line remote commands built from PowerShell here-strings (`.ps1` is `eol=crlf`) were shipped with `\r`, so the rig's `sh` died on `syntax error near 'do\r'` — silently, because the exit code wasn't checked. This had been breaking the on-rig CRLF normalization and the Pitstop launcher arming. `Invoke-Rig` now strips CR from every command.
- **PowerShell pairing abort:** `ssh.exe` stderr on a deliberately-failing probe became a *terminating* `NativeCommandError` under `$ErrorActionPreference='Stop'`. Pairing now scopes the error preference so probes fail gracefully and control flows off the exit code.
- The generated SSH config uses `IdentityFile ~/.ssh/etk_rig` (portable across Windows OpenSSH, Git's bundled ssh, and Mac/Linux) — an absolute MSYS path had made the bare target unusable from Windows OpenSSH.
- **PowerShell 5.1 parser break:** em-dashes in the `.ps1` files decoded as curly quotes under Windows PowerShell's ANSI codepage (BOM-less UTF-8), desyncing the string tokenizer; the scripts are now pure ASCII.

### Changed
- `windows_installer/etk-env.ps1` `$RigSsh` now defaults to `root@SM8250.local` (matching `env.sh` / `etk.conf.example`), so most setups need **no configuration** at all.
- `windows_installer/WINDOWS_HOST_README.md` rewritten around the zero-config flow (clone → run → reboot); the old 7-step manual SSH handshake is demoted to a documented fallback.
- Main README "Windows Install Guide": the native PowerShell installer is now the primary no-WSL path; WSL2 remains the full-featured (vaulted) route.

### Known limitations
- The Windows port is **no-vault** — no host-side shader backup/restore. Use the SMB `robocopy` recipe (README) or WSL2 for that.
- mDNS auto-discovery is **not** ported to PowerShell. Set `$RigSsh` in `etk-env.ps1` (or pass `etk-pair.ps1 -RigSshOverride root@<ip>`); a literal IP always works.
- `etk.conf` operator overrides are not pushed by the Windows port (the rig runs the Sentry's baked-in defaults unless a prior Mac/Linux install left an `etk.conf`).

## [0.1.0]

Initial tagged release: bash `install.sh` / `uninstall.sh` host tooling (macOS / Linux / WSL2) with mDNS rig auto-discovery, the native Rocknix ETK Pitstop app (telemetry / tuning / PS3 `.pkg` installer), the systemd Sentry, the per-game shader vault with host-side backup, and Simple Telemetry.
