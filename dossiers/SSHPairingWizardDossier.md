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

## §E-R. PHASE 0 RESULTS — on-rig recon (2026-05-29, rig `SM8250.local`)
Captured from a **fresh Windows host** (never paired) against the `OS_VERSION=20260528` nightly (kernel 7.0.2 aarch64). Two passworded recon round-trips; all §E questions now resolved.

- **E.1 root home / key path:** `root` home = **`/storage`** (shell `/bin/sh`); `AuthorizedKeysFile .ssh/authorized_keys` (relative) → keys live at **`/storage/.ssh/authorized_keys`**. README assumption confirmed.
- **E.3 sshd vs dropbear:** **OpenSSH `sshd`** — `/etc/ssh/sshd_config` present, `/etc/dropbear` absent. Both would read `~/.ssh/authorized_keys` anyway; only the one standard path is in play.
- **B.3 StrictModes:** **`StrictModes no`** — sshd will **not** silently reject a key over loose perms/ownership. Perms are not a hard failure mode on this build. (Current perms are already correct anyway: `/storage/.ssh` = `700`, `authorized_keys` = `600`, both root:root.) `PubkeyAuthentication` defaulted on.
- **E.1 / B.1 CRLF — CONFIRMED:** `authorized_keys` holds 2 keys. The Mac key (`dave@Daves-MacBook-Air.local`, ssh-rsa) is **clean LF**; the **`etk-host` ed25519 key (appended by the old Windows host) ends in `\r\n` (CRLF)**. The Windows path *did* introduce a CR — §B.1 reproduced. Caveat: the `\r` sits in the *comment* field, which OpenSSH (esp. with StrictModes off) **often tolerates**, so CRLF may be contributory rather than the sole cause of the original `Permission denied`; keep §B.8 (wrong identity offered / no `IdentitiesOnly`, or absent matching privkey) as a co-suspect. Wizard must do **both** CR-strip and `IdentitiesOnly`, not rely on either.
- **E.2 default password:** `rocknix` worked. (Wizard should still accept a custom one.)

**Design consequences for Phase 1 (beyond the dossier as written):**
1. **Dedup must compare CR-stripped.** A real, paired-from-Mac user key is present and must be preserved (§G.4). The existing `etk-host` line is `\r`-terminated, so a naive `grep -qxF "<clean key>"` will miss it and append a clean **duplicate**. Strip CR on *both* sides of the idempotency check; ideally repair stray `\r` on existing lines (`sed -i 's/\r$//'`) as part of the append.
2. **Host resolves to IPv6 + IPv4** (`SM8250.local` → `2600:4041:...` and `192.168.1.53`); ssh prefers IPv6 and it works. The placeholder `192.168.1.50` in `etk-env.ps1` was never the real rig — discovery/confirm step should surface the actual address.
3. **Mixed ssh/scp providers on the host:** `ssh` = Windows OpenSSH 10.3p1, but `scp` = Git MSYS2 `scp.exe`. Relevant to the `$EtkScpLegacy` decision; the wizard should avoid `scp` entirely and append via piped `ssh` to dodge it.

## §F. PHASED PLAN
- **Phase 0 — on-rig recon (~15 min):** the §E commands; lock the rig-side path/perms/auth facts before writing logic. **DONE 2026-05-29 — see §E-R.**
- **Phase 1 — standalone `scripts/etk_pair.sh` (~2 hr):** the full flow; prove on a real rig that a cold pair is single-password and a re-run is a silent no-op (no duplicate keys). Validate-before-integrate. **DONE 2026-05-29 — see §F-R1.**
- **Phase 2 — wire into install.sh (~1 hr):** call after discovery / before STEP 0; add `--pair`. **DONE 2026-05-29 — see §F-R2.**
- **Phase 3 — PS mirror + discovery (~2–3 hr):** `Invoke-EtkPair`, `Get-Heredoc` the shared rig-side snippet, gate `Assert-RigConnection`; standalone `etk-pair.ps1`. **DONE 2026-05-29 — see §F-R3.**
- **Phase 4 — docs (~30 min):** fold into `WINDOWS_HOST_README.md` + main README; the existing manual handshake steps become the documented fallback. **WINDOWS_HOST_README DONE 2026-05-29** (zero-config TL;DR — clone, run installer for one password, reboot; "First run: automatic SSH pairing" section; 7-step manual handshake demoted to a Troubleshooting fallback; status/limitations updated for the verified install + un-ported mDNS). **`etk-env.ps1` default decided:** `$RigSsh` ships as `root@SM8250.local` (matches `env.sh`/`etk.conf.example`), reframed as edit-only-if-you're-the-1% (different SoC / custom IP / changed password) — no `192.168.1.50` placeholder. **Main README DONE 2026-05-29** — "Windows Install Guide" rewritten (native PowerShell installer is now the primary no-WSL path with auto-pairing; WSL2 = full-featured/vaulted route); `CHANGELOG.md` created with the 0.1.1 release notes. Uninstaller reviewed: `etk-uninstall.ps1` faithfully mirrors `uninstall.sh` (STOP/HW/CLEAN via `Get-Heredoc`, vault gating, typed `-ZapVault` confirm). Open question for operator: install/uninstall pairing asymmetry — installers auto-pair, uninstallers don't (matches `uninstall.sh`); add `Invoke-EtkPair` to both uninstallers for symmetry if desired.

## §F-R1. PHASE 1 RESULTS — `scripts/etk_pair.sh` validated (2026-05-29, rig `SM8250.local`)
Written and proven on the real rig from a fresh Git-Bash host. All §G criteria met:
- Cold pair = **one** `rocknix` password, then `Pairing complete`. Re-run = **zero** passwords (`Already paired`).
- `authorized_keys` ends at 3 lines (Mac user key + old orphan `etk-host` + this host's `etk-host`); re-run adds none. **0 CR bytes** (old `\r\n` line repaired); perms 600.
- Bare `ssh etk-rig "echo ..."` resolves via the appended `~/.ssh/config` block.

**REGRESSION CAUGHT & FIXED during Phase 1 validation (the load-bearing lesson):**
install.sh calls `ssh $RIG_SSH` — the **bare target** (`root@SM8250.local`), NOT the `etk-rig` alias. The dedicated key `etk_rig` is **not a default identity name**, so a `~/.ssh/config` block of `Host etk-rig` alone leaves the bare target with no usable key → pairing "succeeds" yet `ssh root@SM8250.local` still returns `Permission denied` and install.sh gets barraged anyway. The v1 script verified only the alias / explicit `-i`, so it hid this. **Fixes:** (a) the config block is `Host <RIG_HOST> etk-rig` (multi-pattern) so the bare target offers the key; (b) all *test-first* and *verify* probes use the **bare target with no `-i`** — mirroring exactly what install.sh runs — so this failure mode can never hide again. Verified: `ssh root@SM8250.local "echo"` is now passwordless.

**Design decisions locked in the implementation (read before Phase 3 mirror):**
1. **Dedup by full key line, never by comment.** Every ETK host keys with `-C "etk-host"`, so the comment is NOT unique — two paired ETK hosts legitimately share it. `grep -qxF` on the whole line is the only safe test. Corollary: the wizard must **never** "clean up stale `etk-host` keys" by comment (it would nuke a second real host). The orphan dead key is left in place by design.
2. **Rig-side body is the `ETKPAIRKEY` heredoc**, run as `ssh "$TARGET" "bash -s -- \"$PUBKEY\""` ($1 = the host pubkey). Confirmed extractable by `Get-Heredoc` (open `<<'ETKPAIRKEY'`, close column-0 `ETKPAIRKEY`) and valid bash standalone — Phase 3 pulls it verbatim, no re-port.
3. **Busybox-safe rig body:** `tr`/`grep -qxF`/`printf`/`mkdir`/`chmod`/`touch` only (no `sed -i`, no `mktemp`). CR repair is a whole-file `tr -d '\r'` rewrite guarded on `[ -s ]`.
4. **Probe strategy (3 tiers).** `probe_bare` = `ssh $PROBE_OPTS "$TARGET"` with NO `-i`/`IdentitiesOnly` (the real install.sh path) — used for test-first (§C.2) and verify. `probe_etkrig` = adds `-o IdentitiesOnly=yes -i ~/.ssh/etk_rig` — used only to detect "key already on rig but bare target unrouted," so that case re-routes config **without a password**. `IdentitiesOnly` lives in the *config block* (§B.8), not the bare probe. All probes use `BatchMode=yes` (never prompt) + `StrictHostKeyChecking=accept-new` (§B.5).
5. **Config block is marker-guarded + append-only** (`# ETK pairing (etk_pair.sh) ...`), honoring §G.4 "never clobber." Idempotent: steady state re-runs short-circuit at test-first and never touch the file. (Limitation: one ETK rig per host — a second rig's block would be suppressed by the marker; out of scope, dossier assumes one rig.)
6. Target precedence: arg > `$RIG_SSH` env > `etk.conf` (read directly, **not** via sourcing `env.sh`, to stay side-effect-free). Persists `RIG_SSH` back to `etk.conf` only if that file already exists.
7. Host-side avoids `scp` entirely (host has mixed OpenSSH-ssh / Git-MSYS2-scp providers, §E-R.3) — the key goes over a piped `ssh ... bash -s`.

## §F-R2. PHASE 2 RESULTS — install.sh integration (2026-05-29)
- New flag `--pair` (init `DO_PAIR_ONLY=0`; `--pair) DO_PAIR_ONLY=1`). Pairing block sits **after** `source ./scripts/env.sh` + flag parse and **before** `source ./tools/tui.sh` / `tui_init` / STEP 0 — so the one password prompt is clean (no TUI redraw) and precedes the first `ssh`.
- Block calls `bash ./scripts/etk_pair.sh "$RIG_SSH"`. With `--pair`: `exit $PAIR_RC` immediately. Without: on non-zero, fail fast with an actionable message (vs. the old silent password barrage); on zero, fall through to the install. Missing `etk_pair.sh` + non-`--pair` = silent fall-through (legacy manual-key behavior preserved); missing + `--pair` = clear error.
- Verified `bash install.sh --pair`: skips wizard (etk.conf present), pair = no-op `Already reachable`, **exits 0 without reaching STEP 0**. Full install path not run (it deploys); the same pairing block is exercised by `--pair`.
- Note: `pgrep: command not found` from env.sh L57 appears only when sourcing env.sh under Git Bash (no rig procs); harmless (exit 0), pre-existing, and absent from both real paths (Mac/Linux has pgrep; Windows uses the PS port). Out of scope for pairing.
- Side note: created `etk.conf` from `etk.conf.example` (gitignored; `RIG_SSH="root@SM8250.local"`) so the wizard is skipped on this test host — the normal first-run artifact.

## §F-R3. PHASE 3 RESULTS — PowerShell mirror (2026-05-29)
- `Invoke-EtkPair` + `Write-EtkSshConfigBlock` added to `etk-common.ps1`; gated in `etk-install.ps1` (called **before** `Assert-RigConnection`); standalone `windows_installer/etk-pair.ps1` mirrors `./install.sh --pair` (`-RigSshOverride <user@host|host>` for a literal IP).
- **Single source of truth proven across hosts:** the rig-side body was changed from positional `$1` to the `$ETK_PUBKEY` env var so the *same* `ETKPAIRKEY` heredoc feeds both hosts. PS pulls it via `Get-Heredoc -Marker ETKPAIRKEY` against `scripts/etk_pair.sh`. Verified live: base64-shipped the extracted body to the rig and it executed (`ETK_PAIR_OK already-present`).
- **Transport = ONE ssh, base64-wrapped:** `ssh $RigSsh "echo <b64> | base64 -d | bash"` where `<b64>` encodes `ETK_PUBKEY='...'\n<body>`. This dodges every PowerShell newline/quoting/CRLF trap and keeps the cold pair at **<=1 password**. Deliberately NOT `Invoke-RigBash` (it `scp`s a temp script + 2 more ssh calls = 3 password prompts pre-key). Confirmed `base64` exists on the rig.
- **`IdentityFile ~/.ssh/etk_rig`** (not an absolute path): portable across Windows OpenSSH, Git's MSYS `ssh.exe`, and Mac/Linux. Caught + fixed a real bug where the bash run under Git Bash wrote `/c/Users/.../etk_rig`, which Windows `ssh.exe` (reading the same `~/.ssh/config`) cannot resolve. Bash side updated to emit the `~` form too.
- **`ssh-keygen -t ed25519 -N '""'`** verified to produce a *true* no-passphrase key on Windows (the `-N ''` empty-arg-drop gotcha avoided by the literal `'""'`).
- Validated: parse-check (3 files), keygen, full base64 transport e2e against the live rig, standalone no-op.
- **§H RELEASE GATE MET — true cold PS pair done on a fresh Windows box (2026-05-29).** Reset to cold (stripped both `etk-host` keys from the rig, keeping the Mac key; removed local `etk_rig` + config block), then ran `etk-pair.ps1 -RigSshOverride root@SM8250.local`: STEP 1 failed silently (the `$ErrorActionPreference='Stop'` + native-stderr abort had to be fixed first — see below), generated a fresh `etk_rig`, took **one** `rocknix` password, base64-installed the key, wrote the config block, verified. Re-run = `Already reachable` (zero passwords). Post-pair verification: bare target passwordless, `authorized_keys` = Mac key + one fresh `etk-host` (2 lines, **0 CR**), key is no-passphrase, alias works.
- **PS gotcha fixed:** Git's `ssh.exe` writes "Permission denied" to stderr on the deliberately-failing probe; under the script's `$ErrorActionPreference='Stop'` that became a *terminating* `NativeCommandError` even with `2>$null`, aborting at the first probe. Fix: `$ErrorActionPreference='Continue'` scoped inside `Invoke-EtkPair` (control flow is driven by `$LASTEXITCODE` + explicit `throw`, which still terminates). Phase 3 mirror must keep this.
- **Discovery scope (honest):** mDNS auto-discovery was **not** ported to PS. Per §E.4, stock Windows has no guaranteed mDNS browser (`dns-sd` ships with Bonjour, not the OS). The PS "prompt + IP" fallback is realized as: operator sets `$RigSsh` in `etk-env.ps1`, or passes `etk-pair.ps1 -RigSshOverride root@<ip>`. `Invoke-EtkPair` throws a clear message if the target is empty. True mDNS discovery deferred past 0.1.1.
- Env note: on the test box, PS `ssh`/`scp` resolve to Git's MSYS binaries (PATH order); both work, and the `~` IdentityFile is what keeps the shared config valid for whichever `ssh.exe` wins.

## §F-R3b. INSTALL-TEST ADDENDUM — full `etk-install.ps1` run (2026-05-29)
After the cold PS pair, ran the **full Windows installer** end-to-end against the live rig. Pairing integration is flawless: `Invoke-EtkPair` -> `Already reachable` -> `Rig reachable`, **zero passwords**, all 6 steps ran, Sentry ended `active`. (Set `$RigSsh` in `etk-env.ps1` from the placeholder to `root@SM8250.local` first — the operator step; the installer has no `-RigSshOverride`.)
- **Separate installer bug found & fixed (CRLF, same family):** STEP 3's `$crlfFix` and STEP 5's `$fixMaster` are PowerShell here-strings. Because `.gitattributes` forces `*.ps1 eol=crlf`, their bodies always carry `\r`; `Invoke-Rig` shipped them verbatim and the rig's `sh` died on `syntax error near 'do\r'` (and a trailing `\r` turned a `sed` path into `file.sh\r` -> "No such file or directory"). Both failed **silently** (`Invoke-Rig` ignores the exit code; `[OK]` printed anyway). **Fix:** `Invoke-Rig` now strips `\r` from every command (`$Command -replace "\`r",""`) — single source, covers `Invoke-RigTemplate` and any future multi-line remote command. Re-run is clean; verified on-rig: all deployed `bin/scripts/tools` scripts CR=0 + executable, master launcher armed, Sentry active. NOT a pairing bug, but in-scope for "0.1.1 = working Windows installer."

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
