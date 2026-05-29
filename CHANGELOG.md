# Changelog

All notable changes to the ETK are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

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

### Fixed
- **`Invoke-Rig` CRLF bug (Windows port):** multi-line remote commands built from PowerShell here-strings (`.ps1` is `eol=crlf`) were shipped with `\r`, so the rig's `sh` died on `syntax error near 'do\r'` — silently, because the exit code wasn't checked. This had been breaking the on-rig CRLF normalization and the Pitstop launcher arming. `Invoke-Rig` now strips CR from every command.
- **PowerShell pairing abort:** `ssh.exe` stderr on a deliberately-failing probe became a *terminating* `NativeCommandError` under `$ErrorActionPreference='Stop'`. Pairing now scopes the error preference so probes fail gracefully and control flows off the exit code.
- The generated SSH config uses `IdentityFile ~/.ssh/etk_rig` (portable across Windows OpenSSH, Git's bundled ssh, and Mac/Linux) — an absolute MSYS path had made the bare target unusable from Windows OpenSSH.

### Changed
- `windows_installer/etk-env.ps1` `$RigSsh` now defaults to `root@SM8250.local` (matching `env.sh` / `etk.conf.example`), so most setups need **no configuration** at all.
- `windows_installer/WINDOWS_HOST_README.md` rewritten around the zero-config flow (clone → run → reboot); the old 7-step manual SSH handshake is demoted to a documented fallback.
- Main README "Windows Install Guide": the native PowerShell installer is now the primary no-WSL path; WSL2 remains the full-featured (vaulted) route.

### Known limitations
- The Windows port is **no-vault** — no host-side shader backup/restore. Use the SMB `robocopy` recipe (README) or WSL2 for that.
- mDNS auto-discovery is **not** ported to PowerShell. Set `$RigSsh` in `etk-env.ps1` (or pass `etk-pair.ps1 -RigSshOverride root@<ip>`); a literal IP always works.
- `etk.conf` operator overrides are not pushed by the Windows port (the rig runs the Sentry's baked-in defaults unless a prior Mac/Linux install left an `etk.conf`).

## [0.1.0]

Initial tagged release: bash `install.sh` / `uninstall.sh` host tooling (macOS / Linux / WSL2) with mDNS rig auto-discovery, the native Rocknix ETK Pitstop app (telemetry / tuning / PS3 `.pkg` installer), the systemd Sentry, the per-game shader vault with host-side backup, Simple Telemetry, and the OS-migration drift detector.
