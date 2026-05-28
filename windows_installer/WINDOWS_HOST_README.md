# ETK on a Windows Host (PowerShell, no-vault)

A bare-bones Windows port of `install.sh` / `uninstall.sh` so a Windows PC can act as the ETK **host** — the machine that flashes, repairs, and retires the kit on your Rocknix handheld over SSH. The kit itself still runs on the ARM64 device; only the host tooling changes.

> **Draft status:** these scripts were ported by hand from the bash originals and have not yet been run end-to-end. Test against an expendable rig + dev SD card first.

## What's different from the bash version

This Windows path is **no-vault**: it does not back your shaders up to the PC, and it does not restore them from the PC. Concretely, `install.sh` Steps 2 and 4 (the rsync vault pull/push) are skipped. Everything else — directory provisioning, daemon/script deploy, the Pitstop Tools-menu app, and the systemd Sentry — is ported faithfully. The rig still vaults shaders **locally**; you just lose the offsite copy.

Why no vault: the vault sync is the one piece that depends on rsync's `--ignore-existing` / `--update` / `--delete` semantics over tens of thousands of tiny shader files, which has no clean native-Windows equivalent. Dropping it is what keeps this port small and dependency-free. The Linux/Mac host (`install.sh`) remains the full-featured path.

### Shielding your vault on Windows (manual, via SMB)

Rocknix shares `/storage` over SMB, so you can copy the vault off the device by hand before any risky operation (reflash, OS update, uninstall `-ZapVault`):

1. In Explorer, connect to `\\<RIG_IP>\` (use the device IP from Rocknix > Network Settings).
2. Browse to the ETK vault folder under your `ETK_ROOT` (e.g. `...\etk\vault\`).
3. Copy that `vault` folder somewhere safe on the PC.
4. To restore later, copy it back to the same location before reinstalling.

The uninstaller **preserves the rig vault by default**, so a normal uninstall → reinstall keeps your shaders without any of this. The SMB copy is only insurance against reflashes and SD failure.

## Single source of truth (why there's no second Sentry to maintain)

The PowerShell installer does **not** contain its own copy of the Sentry, the systemd unit, the PKG drop-folder README, or the mako style. It reads them **verbatim out of `install.sh`** at runtime (and the STOP/HW/CLEAN blocks out of `uninstall.sh`). So when you fix the Sentry in `install.sh`, the Windows installer picks up the change automatically — there is no byte-identical fork to keep in sync. The only duplicated thing is configuration, in `etk-env.ps1`.

## Prerequisites

- Windows 10 (1809+) or 11 with the **OpenSSH Client** optional feature (provides `ssh.exe` / `scp.exe` — installed by default on most modern Windows; if missing, Settings > Apps > Optional Features > Add > OpenSSH Client).
- SSH access to the rig working from a normal terminal: `ssh <user>@<rig-ip>` should connect. A key is smoother than a password since the scripts make several SSH calls.
- The ETK repo cloned locally, **with `.gitattributes` in place** (ships alongside these scripts) so shell scripts stay LF. The installer also strips CRLF on the rig as a backstop, but the `.gitattributes` is the real fix.
- `python3` available on the rig (it is, under Rocknix) for the Tools-menu injector.

## Setup

1. The PowerShell scripts live in `windows_installer/` and resolve the repo root automatically (one level up), so they find `install.sh` / `uninstall.sh` and the `bin`/`scripts`/`config`/`tools` trees on their own. `.gitattributes` lives at the **repo root** so a Windows clone keeps every shell/python script LF. Nothing to move — just clone the repo.
2. Open `windows_installer\etk-env.ps1` and set `$RigSsh` to your rig (`root@<rig-ip>`). The rig-side paths are now resolved straight from `scripts/env.sh` (RPCS3 state lives under `bios/rpcs3`, the PKG drop + telemetry under `ETK_ROOT`, the active-id file under `/dev/shm/etk_shm`) — only touch them if your `env.sh` differs. **This file is the only place config is duplicated.**

## Usage

From the repo root in PowerShell:

```powershell
# Install / repair / update
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1

# Uninstall, preserving the rig vault (default)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-uninstall.ps1

# Uninstall AND destroy the rig vault (asks for typed confirmation)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-uninstall.ps1 -ZapVault
```

After install, **reboot the device** so EmulationStation reads the Tools gamelist and shows the ETK Pitstop entry.

## Troubleshooting

- **`ssh` / `scp` not found** → install the OpenSSH Client optional feature.
- **`scp` fails with an sftp / "subsystem request failed" error** → set `$EtkScpLegacy = "1"` in `etk-env.ps1` (forces the legacy SCP wire protocol with `-O`).
- **Scripts won't start on the rig / `\r` errors** → confirm `.gitattributes` is committed and re-clone, or just re-run the installer (it strips CRLF on the rig).
- **"Cannot reach the rig"** → verify the device is on, on the same network, and `RigSsh` is correct; test plain `ssh <RigSsh>` first.
- **Execution policy blocks the script** → the `-ExecutionPolicy Bypass` flag above avoids changing your system policy.

## Known limitations / honest notes

- No host vault backup or restore (by design — see above).
- **`etk.conf` is not pushed.** The Linux/Mac `install.sh` rsyncs `etk.conf` (operator overrides for `ETK_BUILD_TYPE` / `DEFAULT_MODE` / `HUD_HEADER_HOLD_S`); this port has no equivalent, so a Windows-flashed rig runs the Sentry's baked-in defaults (unless a prior Mac/Linux install already left an `etk.conf` on the rig). Host config here lives only in `etk-env.ps1`.
- Hand-ported and **untested end-to-end** on real hardware (this includes the 2026-05-28 sync pass: resolved `etk-env.ps1` paths, `tools/etk_drift.py` deploy, and the `tools`/`screenshots`/`rpcs3_home`/`telemetry` dir + SHOTREADME additions). Verify on an expendable rig + dev card first.
- `--delete` behaviour for the `bin/` and `scripts/` trees is replicated by removing the remote tree before copying; the `config/` tree is merged (not mirrored), matching `install.sh`.
- Recommended next step to fully kill the fork: refactor `install.sh` / `uninstall.sh` to read their Sentry/unit/clean blocks from standalone files in `config/`, so bash and PowerShell share literally the same files instead of the PowerShell side extracting them from the bash heredocs.
