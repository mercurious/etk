# HOST -> RIG SSH PAIRING WIZARD — DOSSIER

**Status:** Plan. Not yet implemented. Target: **0.1.1** (alongside the Windows installer port).
**Audience:** Claude Code (implementer, future session) + operator.
**Provenance:** 2026-05-28, first real Windows-host test. After a manual key copy, `ssh -o BatchMode=yes -o PreferredAuthentications=publickey root@SM8250.local` returned `Permission denied (publickey)` — the rig rejected the key, and every `ssh $RIG_SSH` call fell back to a password prompt, barraging the user.
**Premise:** A non-technical tester may never have done an SSH handshake. First run must pair host<->rig with **at most one** password entry, then stay silent forever. This belongs to **both** `install.sh` and the PowerShell port.

---

## §A. WHY
- `install.sh` already auto-discovers the rig (mDNS `_ssh._tcp` via `dns-sd`/`avahi-browse` -> `RIG_SSH="root@<SOC>.local"` in `etk.conf`, ~L20–119) but does **nothing** about authentication. With no key, each of the installer's ~dozen `ssh`/`scp` calls prompts for a password.
- The PowerShell port has **no discovery** (operator hand-edits `$RigSsh`) and the same no-key problem — plus Windows-only traps (CRLF, no `ssh-copy-id`, flaky mDNS).
- Pairing is the load-bearing first-run UX, and a precondition for the host side of `RigSelfUpdateFeasibility.md` (0.2.0).

## §B. FAILURE CATALOG (what the test exposed — design against all of these)
1. **CRLF in the key** — a Windows `.pub` appended with a trailing `\r`; sshd won't match a `...comment\r` line. **Top suspect for the observed `Permission denied`** (unconfirmed — see §E.1). Same class of bug as the em-dash encoding break.
2. **Passphrase on the key** — every call prompts for the passphrase (no agent caching).
3. **Perms / StrictModes** — `.ssh` must be `700`, `authorized_keys` `600`, root-owned, or sshd silently ignores the key.
4. **Wrong target dir** — the key must land where sshd actually looks (root home `.ssh`; assume `/storage/.ssh`, verify).
5. **Host-key prompt** — the first connect asks to accept the fingerprint (interactive friction).
6. **No `ssh-copy-id` on Windows**, and PowerShell pipe encoding can corrupt the key in transit.
7. **mDNS `<SOC>.local` is flaky on Windows** -> need a literal-IP fallback.
8. **ssh offering the wrong key** among many -> need `IdentitiesOnly`.

## §C. DESIGN — `etk-pair` (idempotent, test-first, <=1 password)
Runs once the rig target is known, before the first real ssh call. Identical behavior on both hosts.
1. **Resolve target** — reuse install.sh discovery (mDNS -> pick/confirm -> IP fallback); add equivalent (or prompt + IP) to the PS port.
2. **Test first** — `ssh -o BatchMode=yes -o ConnectTimeout=5 $RIG "echo ETK_OK"`. Pass -> already paired, **skip with zero passwords**.
3. **Pair:**
   a. Ensure a **dedicated, no-passphrase** key: `ssh-keygen -t ed25519 -N "" -f ~/.ssh/etk_rig -C "etk-host"`. Empty passphrase = no agent and no future prompts; dedicated = never clobbers the user's other keys.
   b. **One `ssh` call** with `-o StrictHostKeyChecking=accept-new` (no fingerprint prompt) that prompts for the rig password **exactly once** and, rig-side: `mkdir -p <sshdir>; chmod 700`; append the key only if absent (`grep -qxF`); **strip CR** (`tr -d '\r'` / clean `printf`); `chmod 600`. One password, CRLF-proof, dedup-safe.
   c. Write a `~/.ssh/config` block (`Host etk-rig` / `User root` / `IdentityFile ~/.ssh/etk_rig` / `IdentitiesOnly yes`) so the right key is always offered and bare targets resolve it.
4. **Verify** — re-run the BatchMode test. On failure, print the specific cause (perms/path) + the manual fallback.
5. **Persist** — store the resolved target in `etk.conf` (`RIG_SSH`) / `etk-env.ps1` (`$RigSsh`).

Net UX: run installer -> type the rig password **once** -> never again. Already-paired hosts pay zero passwords.

## §D. INTEGRATION
- **install.sh:** new `scripts/etk_pair.sh`. Invoke right after `RIG_SSH` is resolved (~L119) and before STEP 0's first ssh (~L192); auto-runs when the BatchMode test fails. Expose `./install.sh --pair` for explicit re-pairing.
- **PowerShell:** mirror as `Invoke-EtkPair` in `etk-common.ps1`, called from `Assert-RigConnection` before deploy; this also gives the PS port the discovery it currently lacks. Standalone `windows_installer/etk-pair.ps1` for re-pairing.
- **Single source of truth:** put the rig-side append/perms/CR-strip snippet as one heredoc in `install.sh` and have the PS side pull it via `Get-Heredoc` (the same pattern as `SENTRY`/`SVC`), so the pairing logic cannot drift between the two hosts. (Aligns with the `deploy_phase.sh` de-dup idea in `RigSelfUpdateFeasibility.md`.)

## §E. OPEN QUESTIONS (resolve in Phase 0 — on-rig recon)
1. **Confirm the exact rejection cause.** Capture rig-side (one password): `getent passwd root | cut -d: -f6`, `ls -ld ~/.ssh`, `cat -A authorized_keys` (look for `^M`), `grep -i authorizedkeysfile /etc/ssh/sshd_config*`. Not captured this session (tester left the PC).
2. **Default password** — assume Rocknix `rocknix`; confirm, and let the wizard accept a custom one.
3. **sshd vs dropbear** on the current nightly — both read `~/.ssh/authorized_keys`, but confirm path + whether StrictModes is enforced.
4. **PS discovery** — is a usable mDNS browser present on stock Windows? (`dns-sd` ships with Bonjour, not guaranteed.) If not, PS path = prompt + IP, hostname optional.

## §F. PHASED PLAN
- **Phase 0 — on-rig recon (~15 min):** the §E commands; lock the rig-side path/perms/auth facts before writing logic.
- **Phase 1 — standalone `scripts/etk_pair.sh` (~2 hr):** the full flow; prove on a real rig that a cold pair is single-password and a re-run is a silent no-op (no duplicate keys). Validate-before-integrate.
- **Phase 2 — wire into install.sh (~1 hr):** call after discovery / before STEP 0; add `--pair`.
- **Phase 3 — PS mirror + discovery (~2–3 hr):** `Invoke-EtkPair`, `Get-Heredoc` the shared rig-side snippet, gate `Assert-RigConnection`; standalone `etk-pair.ps1`.
- **Phase 4 — docs (~30 min):** fold into `WINDOWS_HOST_README.md` + main README; the existing manual handshake steps become the documented fallback.

## §G. ACCEPTANCE CRITERIA
1. Cold first run pairs with **<=1 password entry**; thereafter every ssh/scp is silent.
2. Re-running pair is a no-op — idempotent, no duplicate `authorized_keys` lines.
3. CRLF/encoding cannot corrupt the key (CR-stripped rig-side).
4. Never clobbers the user's existing keys or `~/.ssh/config` (dedicated key, appended config block).
5. Key survives reboot + in-place OS update (persistent `/storage`).
6. `install.sh` behavior is unchanged when already paired.
7. On failure: one clear, actionable message + the manual fallback.

## §H. 0.1.1 SCOPE
**0.1.1 = working Windows installer port + auto-discovery + auto-key-pairing wizard.** Bundles the already-done pieces (encoding/em-dash fix; install.sh-sync of the PS port) with this pairing wizard. Tag/release `v0.1.1` only after a clean cold-pair test on a real Windows box.
