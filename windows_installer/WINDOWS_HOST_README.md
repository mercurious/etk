# ETK on a Windows Host (PowerShell, no-vault)

A bare-bones Windows port of `install.sh` / `uninstall.sh` so a Windows PC can act as the ETK **host** — the machine that flashes, repairs, and retires the kit on your Rocknix handheld over SSH. The kit itself still runs on the ARM64 device; only the host tooling changes.

> **Status:** SSH pairing and a full no-vault install have been run end-to-end against a real rig (Rocknix SM8250, 2026-05-29). The deploy path is still young — test against an expendable rig + dev SD card before trusting it on your daily driver.

## TL;DR

Stock Rocknix handhelds answer at `SM8250.local` with the default `rocknix` password, so there's usually **nothing to configure** — just run it:

```powershell
# Install (the first run asks for the rig password ONCE, then never again):
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1

# Then reboot the handheld so the ETK Pitstop entry appears in the Tools menu.
```

That's the whole happy path. Different handheld model, a custom IP, or you changed the rig password? It's a one-line edit — see [Setup](#setup). Everything else below is detail and fallbacks.

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

## Single source of truth (why there's no second copy to maintain)

The PowerShell installer does **not** contain its own copy of the Sentry, the systemd unit, the PKG drop-folder README, or the mako style. It reads them **verbatim out of `install.sh`** at runtime (and the STOP/HW/CLEAN blocks out of `uninstall.sh`). The SSH-pairing logic that runs on the rig is likewise pulled verbatim from `scripts/etk_pair.sh` (the `ETKPAIRKEY` block), so bash and PowerShell pair the rig identically. So when you fix the Sentry in `install.sh` — or the pairing logic in `etk_pair.sh` — the Windows installer picks up the change automatically. The only duplicated thing is configuration, in `etk-env.ps1`.

## Prerequisites

- Windows 10 (1809+) or 11 with the **OpenSSH Client** (provides `ssh.exe` / `scp.exe` — installed by default on most modern Windows; if missing, Settings > Apps > Optional Features > Add > OpenSSH Client). Git for Windows' bundled SSH works too.
- *(Only if you're not on the default `SM8250.local`)* your rig's address — an mDNS hostname like `SM8550.local`, or the IP from the handheld's **START > Network Settings > IP ADDRESS**.
- The ETK repo cloned locally, **with `.gitattributes` in place** (ships alongside these scripts) so shell scripts stay LF. The installer also strips CRLF on the rig as a backstop, but the `.gitattributes` is the real fix.
- `python3` available on the rig (it is, under Rocknix) for the Tools-menu injector.

Passwordless SSH is **not** a prerequisite anymore — the installer sets it up for you on first run (see below). You'll type the rig password exactly once.

## Setup

1. Clone the repo. The PowerShell scripts live in `windows_installer/` and resolve the repo root automatically (one level up), so they find `install.sh` / `uninstall.sh` and the `bin`/`scripts`/`config`/`tools` trees on their own. `.gitattributes` lives at the **repo root** so a Windows clone keeps every shell/python script LF. Nothing to move.
2. **Usually nothing to do.** `$RigSsh` in `windows_installer\etk-env.ps1` already defaults to `root@SM8250.local`, which virtually all stock devices answer to. Edit it **only** if your handheld is a different SoC (e.g. `root@SM8550.local`) or you need a literal IP (`root@<rig-ip>`). It's the only place config is duplicated. (The rig-side paths just below it come from `scripts/env.sh`; touch them only if your `env.sh` differs.)

## First run: automatic SSH pairing

The installer makes many SSH/SCP calls, so it needs key-based (passwordless) auth. You no longer set this up by hand — the installer (and the standalone `etk-pair.ps1`) pair the rig for you the first time. It:

- generates a **dedicated, no-passphrase** key `~/.ssh/etk_rig` (it never touches your existing `id_*` keys),
- asks for the rig password **once** — the Rocknix default is `rocknix` (unless you changed it),
- installs that key on the rig (carriage-return-safe, and it never clobbers a key that's already there), and
- writes an `~/.ssh/config` block so the bare `root@<rig>` target is passwordless from then on.

After that one password, every `ssh`/`scp` is silent. Re-running the installer or the pairing script costs **zero** passwords. A machine that already has working SSH to the rig is detected and left untouched.

**Pair on its own (no install), or re-pair after a card reflash:**
```powershell
# uses $RigSsh from etk-env.ps1:
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-pair.ps1

# or target a literal IP without editing etk-env.ps1:
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-pair.ps1 -RigSshOverride root@192.168.1.53
```

> **Note on mDNS:** Windows mDNS can be flaky, and `<SOC>.local` won't always resolve. If pairing or the installer can't reach `SM8250.local`, just use the literal IP everywhere (`$RigSsh = "root@<rig-ip>"`, or `-RigSshOverride root@<rig-ip>`).

## Usage

From the repo root in PowerShell:

```powershell
# Pair / re-pair only (no install)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-pair.ps1

# Install / repair / update (auto-pairs on first run)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1

# Uninstall, preserving the rig vault (default)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-uninstall.ps1

# Uninstall AND destroy the rig vault (asks for typed confirmation)
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-uninstall.ps1 -ZapVault
```

After install, **reboot the device** so EmulationStation reads the Tools gamelist and shows the ETK Pitstop entry. Confirm the Sentry with `ssh root@<rig> "systemctl status etk.service"`.

## Troubleshooting

- **`ssh` / `scp` not found** → install the OpenSSH Client optional feature.
- **Pairing keeps asking for the password / "Permission denied"** → make sure `$RigSsh` is right and the device is on the same network; try plain `ssh <RigSsh>` once. After a card reflash the rig's host key changes — clear the stale entry with `ssh-keygen -R <rig-host>` (and `-R <rig-ip>`), then re-run `etk-pair.ps1`.
- **`scp` fails with an sftp / "subsystem request failed" error** → set `$EtkScpLegacy = "1"` in `etk-env.ps1` (forces the legacy SCP wire protocol with `-O`).
- **Scripts won't start on the rig / `\r` errors** → confirm `.gitattributes` is committed and re-clone, or just re-run the installer (it strips CRLF on the rig).
- **"Cannot reach the rig"** → verify the device is on, on the same network, and `$RigSsh` is correct; test plain `ssh <RigSsh>` first.
- **Execution policy blocks the script** → the `-ExecutionPolicy Bypass` flag above avoids changing your system policy.

### Fallback: manual SSH handshake

Auto-pairing should make this unnecessary, but if it fails you can set up passwordless auth by hand from a normal PowerShell window. Root's home on the rig is `/storage` (the persistent partition), so the key survives reboots and OS updates.

```powershell
# 1. Accept the host key (first connect, password = rocknix). Type 'yes', then 'exit':
ssh root@SM8250.local            # or ssh root@<rig-ip>

# 2. Generate a key if you don't have one (press Enter through prompts for an empty passphrase):
ssh-keygen -t ed25519 -C "etk-host"

# 3. Install the public key on the rig (you'll enter the rocknix password here):
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@SM8250.local "mkdir -p /storage/.ssh && cat >> /storage/.ssh/authorized_keys && chmod 700 /storage/.ssh && chmod 600 /storage/.ssh/authorized_keys"

# 4. Verify it returns ETK_OK with NO password prompt:
ssh root@SM8250.local "echo ETK_OK"
```

If `scp` (rather than the piped `type ... | ssh` above) misbehaves with an sftp error, set `$EtkScpLegacy = "1"` in `etk-env.ps1`.

## Known limitations / honest notes

- No host vault backup or restore (by design — see above).
- **`etk.conf` is not pushed.** The Linux/Mac `install.sh` rsyncs `etk.conf` (operator overrides for `ETK_BUILD_TYPE` / `DEFAULT_MODE` / `HUD_HEADER_HOLD_S`); this port has no equivalent, so a Windows-flashed rig runs the Sentry's baked-in defaults (unless a prior Mac/Linux install already left an `etk.conf` on the rig). Host config here lives only in `etk-env.ps1`.
- **mDNS auto-discovery is not ported.** Unlike the bash installer, the PowerShell port does not browse for the rig — you set `$RigSsh` (or pass `-RigSshOverride`). Stock Windows has no guaranteed mDNS browser, so this is intentional; a literal IP always works.
- Pairing + a full no-vault install were verified on a real SM8250 rig (2026-05-29). The rest of the deploy path is hand-ported — verify on an expendable rig + dev card first.
- `--delete` behaviour for the `bin/` and `scripts/` trees is replicated by removing the remote tree before copying; the `config/` tree is merged (not mirrored), matching `install.sh`.
- Recommended next step to fully kill the fork: refactor `install.sh` / `uninstall.sh` to read their Sentry/unit/clean blocks from standalone files in `config/`, so bash and PowerShell share literally the same files instead of the PowerShell side extracting them from the bash heredocs.
