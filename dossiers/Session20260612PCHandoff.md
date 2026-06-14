# PC HANDOFF — Cold-Card Test of ETK 0.3.0 (Private Paddock)

**Written:** 2026-06-12 (Mac session, end of the 0.3.0 build day)
**Picked up by:** Claude Code on the operator's Windows PC
**Mission:** run the cold-card test from `dossiers/Release030PrivatePaddockDossier.md` §10/§10b
on a PC: ImageBurner flash → PowerShell install with a GitHub token → PADDOCK tab appears →
PULL restores a vault onto a cold card. Then 0.3.0 ships (tag from `main`).

> **You (PC Claude) have no memory of the Mac sessions.** This file + the dossiers in this repo
> are your context. The repo's `AI_MANIFEST.md` is law (BusyBox rig, no GNU-isms, SHM sacred).

---

## 1. CONTEXT CAPSULE (what you must know, condensed)

- **Rig:** Retroid Pocket Flip 2 (SM8250/Adreno 650), hostname `sm8250.local`, SSH `root` /
  password `rocknix` (etk-pair.ps1 sets up keys). **Single-boot, label-roulette-proofed
  (2026-06-12 evening):** ROCKNIX **nightly-20260612 on internal UFS** (`/flash`=sda24
  LABEL=ROCKNIX, `/storage`=sda25 LABEL=STORAGE — each label now UNIQUE system-wide).
  **LABEL DOCTRINE (hard-won tonight):** duplicate ROCKNIX/STORAGE labels caused per-boot
  roulette on BOTH `/flash` and `/storage` (an OS update even "healed" the SD's bootloader via
  the EFI-sync fix while /flash pointed at the SD). Fix: SD p1 stripped of ALL boot artifacts +
  relabeled `SDDATA`; SD p2 (games) relabeled `SDGAMES`; games mount via
  `etk-sd-rebind.sh` **v2** which mounts SD p2 DIRECTLY by UUID
  (`f4618c8e-13a6-49b3-a1a5-f2a4277e8532`) at `/storage/sdgames` then binds
  `games-internal(/roms)` — zero automount dependence. Verified: rebind v2 OK, 217 rom dirs,
  all services active, GRUB hidden (NOTE: GRUB reads `/boot/grub/grub.cfg` on this layout —
  ALWAYS edit BOTH grub.cfg twins). SD p1 boot backup:
  `/storage/roms/etk/backup/sd_p1_boot_backup_20260612.tar.gz` (1.5 GB).
  **Cold-card implication for §4.2:** a cold SD now boots ONLY if sda24's EFI is temporarily
  disabled (`mv /flash/EFI /flash/EFI.disabled` + restore after) — AND a freshly flashed card
  reintroduces ROCKNIX/STORAGE labels, so REMOVE the cold card after the test or the roulette
  returns. The rebind v2 UUID pin protects the games mount regardless.
- **Private Paddock (0.3.0, built+validated yesterday/today on the Mac):** each user's OWN private
  GitHub repo stores per-game bundles (vault+config+saves) as epoch-tagged release assets.
  Operator's paddock: **`mercurious/etk-paddock`** (private). Engine: `bin/paddock_sync.sh`
  (status/push/pull, curl+jq, sha256 sidecars, homologation gate). UI: Pitstop PADDOCK tab —
  **only appears if `/storage/roms/etk/config/paddock.json` exists** (written by the installer's
  PADDOCK LINK step from `PADDOCK_TOKEN`). Validated: probe 10/10; true restore (delete vault →
  pull → 47/47 back); GT5P pushed from the gamepad.
- **THE EPOCH DOCTRINE (critical to interpret test results):** Mesa shader-cache entries are keyed
  by the *driver build hash* (sha256 of first 64 KB of `/usr/lib/libvulkan_freedreno.so`). Every
  nightly rebuilds Mesa → new hash → prior vault entries + paddock bundles become dead for the new
  build. Epoch release *tags* are version-granular (`vault-SM8250-turnip26.1.2`) but the pull
  *gate* is hash-granular — a bundle pushed from a different nightly shows as pullable (`BOTH`)
  and then refuses at pull. That refusal is CORRECT behavior, not a bug.
  `tools/vault_sweep.sh --apply` prunes dead-epoch files (boundary = mtime of
  `<ETK_ROOT>/vault/.last_mesa.hash`, refreshed by install.sh when the driver hash changes).
- **Paddock inventory right now (all pushed from the RETIRED 20260610 build):** GT5P 34 MB trim,
  GT HD 15.5 MB, NPUA80490 56 KB, plus `paddock_names.json`. **None will pass the gate on a
  20260612 rig** until re-pushed (see §3).

## 2. PRE-FLIGHT (verify before anything)

1. **The repo on this PC must contain the 0.3.0 work.** Check `git log --oneline -3` — you should
   see commits covering: paddock_sync.sh + Pitstop tab + install.sh steps 7/8 + ps1 parity +
   vault-index removal + dossier set. If the latest commit predates 2026-06-12 or
   `bin/paddock_sync.sh` is missing, STOP — the Mac working tree wasn't pushed; ask the operator.
2. Windows prereqs (see `WINDOWS_HOST_README.md`): built-in OpenSSH client (`ssh`, `scp` on PATH),
   PowerShell 5.1+, repo cloned with LF-safe settings (the installer CRLF-strips on the rig anyway).
3. **Token:** operator pastes their GitHub PAT into `windows_installer\etk-env.ps1` →
   `$PaddockToken`. (`$PaddockRepo` empty = defaults to `mercurious/etk-paddock`.)

## 3. EPOCH ALIGNMENT (do this BEFORE the cold-card PULL test means anything)

The rig moved to 20260612 *today*; its vaults and the paddock bundles are 20260610-era (dead).
Sequence to mint the new epoch (rig-side, over SSH from the PC or by the operator playing):
1. Operator races GT5P (and optionally GT HD) on the rig — a few sessions harvests the
   20260612-epoch vault. (This is the normal life of the kit; nothing special.)
2. Refresh the sweep boundary + prune the dead epoch: re-run the installer once
   (it re-fingerprints Mesa), then `bash <ETK_ROOT>/tools/vault_sweep.sh --all-games --apply`.
3. Push the fresh vaults: PADDOCK tab → PUSH per game (or
   `bash <ETK_ROOT>/bin/paddock_sync.sh push NPUA80075 "Gran Turismo 5 Prologue"`).
Now the paddock holds 20260612-epoch bundles and the cold card (same nightly date = same build)
will pull them clean.

## 4. THE COLD-CARD TEST (PC protocol — §10b of the 0.3.0 dossier)

1. **Flash:** ROCKNIX ImageBurner (github.com/ROCKNIX/ImageBurner, single exe) → device
   "Retroid Pocket Flip2" → branch **Nightly** (catalog serves the latest; confirm it matches the
   rig's build — `releases.rocknix.org/imageburner` lists the URL. If the dates differ, either
   update the rig to match or expect gate-refusal results). ImageBurner handles the Flip2 dtb +
   grubenv post-install automatically — no U-Boot picker dance.
   **IMPORTANT:** flash a SPARE card. The main 1 TB SD is the game library — don't touch it.
2. First boot of the cold card **with the main SD removed and... NOTE:** the rig's internal UFS
   install boots first when present. For a true cold-card test EITHER temporarily disable the
   internal boot (mirror of today's trick: rename `EFI`→`EFI.disabled` on sda24 — restore after)
   OR run the test on a second SM8250 device if available. Discuss with operator at test time;
   simplest honest variant: test against the internal install itself being "cold" is NOT possible,
   so the cold card boots only when UFS EFI is disabled. Plan ~10 min for the toggle.
3. Cold card first-time setup: WiFi join only.
4. **Install from the PC:** `cd windows_installer`, fill `etk-env.ps1` ($PaddockToken!), then
   `powershell -ExecutionPolicy Bypass -File .\etk-install.ps1`.
   Expect 8 steps; STEP 7 = STAGE3 (verify it reports core_pattern → /storage/cores), STEP 8 =
   `PADDOCK connected: mercurious/etk-paddock`. **Maiden flight note:** steps 7/8 of the ps1 were
   written on the Mac 2026-06-12 and have NEVER run on real Windows — pattern-faithful but treat
   failures as port bugs first (TLS, quoting, Get-Heredoc markers PROF/CORE/S3SVC), not design bugs.
5. **PASS criteria (in order):**
   a. Installer completes 8 steps, paddock connected.
   b. Pitstop on the rig shows 4 tabs; PADDOCK rows show the library **by title** (names come from
      `paddock_names.json` — cold cards have no local name sources; this was built for exactly
      this screen).
   c. PULL Gran Turismo 5 Prologue → vault restores (thousands of shaders) → launch GT5P → HUD
      vault count nonzero on first boot. *The whole feature in one gesture.*
   d. `.\etk-uninstall.ps1` removes the credential → 3 tabs.
6. Negative paths: empty `$PaddockToken` → step 8 skips, 3 tabs; garbage token → step reports
   rejection, install completes.

## 5. KNOWN STATE / WATCH ITEMS (inherited from the Mac sessions)

- **v0.2.0 is released** (tag from `release/0.2.0`); **0.3.0 ships from `main`** after this test —
  no cherry-picks needed anymore (the public distribution surface was deleted; see CHANGELOG).
- **Stage III soak continues in parallel:** crash taxonomy = Silent (emulator, compile-coupled —
  fixed largely by the GT5 leak-fix nightly) vs Adreno fence-timeouts (driver-layer; TU_DEBUG=nolrz
  is the next planned experiment, 10+ sessions, AFTER current variables settle). Kernel panics
  (GT5, RR7) leave no trace until ramoops lands in a custom image (T3). Core trap is armed —
  any core in `/storage/cores` is gold: it confirms the SPU ARM64 wild-branch (see
  `dossiers/Stage3CustomRigDossier.md`) on Linux.
- **Upstream:** aPS3e PR #122 open (the VkPipelineCache fix; fork release `shader-patch-1` serves
  users meanwhile). RPCS3 SPU-JIT report queued on the first Linux core dump.
- **BusyBox law, twice-proven this week:** `cp -rn src/. dest/` is a silent no-op; jq on an empty
  file exits 0 with no output. Test on-rig before trusting any host idiom.
- The operator prefers literal spec adherence, validate-before-integrate (disposable harness
  first), and artifacts-not-announcements for anything public.

## 6. IF THINGS GO SIDEWAYS

- Rig unreachable: it may have booted to Android (default when no Rocknix boot found — shouldn't
  happen now). Vol-Down at boot = Qualcomm ABL/fastboot; Vol-Up = U-Boot menu.
- Cold card boots but carousel empty: that's the missing rebind (cold cards don't have it; it's
  only needed for the games-on-other-SD layout — a cold card with no game library shows an empty
  but healthy ES. PKG-install a small game via TOOLS, or PULL only exercises the vault path).
- PowerShell step 7/8 failures: each is non-fatal by design; the bash reference implementation is
  `install.sh` steps 7-8 — diff behavior against it.
- Paddock 401s: token typo or expired; re-run installer after fixing `etk-env.ps1`.

*Mac-side memory will be updated to mirror this handoff. Good luck — flash, hold nothing, race.*

---

# ADDENDUM — SHADER CONDITIONING (next session's build; operator-directed 2026-06-12)

**Premise (operator):** off the official-release pin, every nightly is a potential epoch flip —
harvest/prune is the steady state, not an event. **0.3.0 should not ship until shader
conditioning is more automated than what's committed today.** Stress-testing harvest+prune IS the
feature. `vault_sweep.sh` graduates from hand tool to load-bearing subsystem. Today's evidence of
the manual gap: a forgotten sweep left a 1.2 GB / 174,954-file corpse (97.6% of GT6's vault), the
HUD lied about saturation for a month, and a dead-epoch bundle got banked under a live tag.

## A. Build order (proposal — next Claude refines)

**A1. `vault_sweep.sh --report` (machine-readable, the keystone).** Emit per-game TSV:
`GAME_ID  live_n  live_kb  stale_n  stale_kb` (boundary = `.last_mesa.hash` mtime, same logic as
today's dry-run). Everything below consumes this one interface — UI, paddock, HUD, automation.
Also store `<mesa_version> <mesa_hash>` as the fingerprint file's CONTENT (today install.sh writes
the hash; add version) so tools can name the *old* epoch, not just detect drift.

**A2. Epoch-change detection at boot (flag-gated automation).** Extend `etk-stage3.service`'s
oneshot (or a sibling 03 script): compare live driver hash vs fingerprint; on drift →
mako announce "EPOCH CHANGE: <n> stale shaders across <g> games" + behavior per etk.conf knob:
`SWEEP_ON_EPOCH="announce" | "auto" | "off"` (default **announce** — auto-delete at boot must be
opt-in). `auto` runs `--all-games --apply` AFTER the announce, never while RPCS3 runs (guard
exists). install.sh keeps doing the fingerprint refresh on its own runs.

**A3. TOOLS-tab "SHADER CONDITIONER" UI (Pitstop).** Row per game from `--report`:
`GAME | LIVE n·size | STALE n·size | [SWEEP]`, plus a SWEEP-ALL action and a header line with the
current epoch (`turnip 26.1.2 @ cde62f68`). Reuse the PADDOCK tab's exact patterns: busy frame,
result card, `_Notifier` toasts, dpad+CONFIRM only. Implementation seam already exists:
`_run_paddock_sync`-style subprocess wrapper around the sweep. (TOOLS tab dispatch:
`bin/etk_pitstop.py` — follow the PKG-installer action pattern.)

**A4. Stop the HUD lying.** `vault_d.sh` counts raw files; after an epoch flip it reports corpse
as treasure. Cheapest honest fix: at session ignition, compute the live-epoch count once
(`find -newer .last_mesa.hash`), publish to SHM as the baseline the HUD displays; per-tick deltas
already measure only new files. Career telemetry could append `live_vault` at postmortem —
saturation curves per epoch become measurable (ties conditioning to the Stage III soak science:
the race-stable bar is an *epoch-relative* property).

**A5. Paddock integration (conditioning the cloud side).**
- `push` HARD-refuses stale>0 (today: warns) unless `--with-stale`; sweep prompt in the tab.
- PADDOCK rows show the local split (`4,634 live + 11k stale`) from the same `--report`.
- **Epoch retention:** old-epoch release tags accumulate in the paddock repo; conditioner offers
  "retire epochs older than the previous one" (keep current + 1 as rollback insurance — pairs with
  pinning back a nightly, which is the one case old bundles resurrect).
- **(Stretch) `sweep --bank-first`:** before pruning, push the dying epoch's bundle under its OLD
  tag (needs A1's version-in-fingerprint) — makes the paddock a true epoch museum and the sweep
  fully regret-free.

**A6. Stress harness (validate-before-integrate, as always).** Disposable
`tools/conditioner_probe.sh`: simulate an epoch flip (backdate/touch the fingerprint), verify
report/sweep/gates/HUD-baseline respond; fault-inject: sweep refused while RPCS3 runs, re-run
idempotency (sweep twice = second is no-op), push-refusal on stale, pull-refusal cross-epoch,
empty-vault push guard (refuse pushing <10 files — a swept-bare vault is not a backup).

## B. Safety rails (the cost of load-bearing)

Dry-run default stays; `--apply` stays explicit; automation defaults to announce-only; never
follow symlinks; refuse when fingerprint mtime is < 5 min old (mid-install churn) or in the
future; the RPCS3-running guard is sacred. Deletion is provably harmless ONLY for stale-epoch
files (current driver cannot read them) — that invariant is why automation is acceptable at all;
nothing may ever auto-delete live-epoch files.

## C. Release gate restated

0.3.0 tags from `main` only after: **A1 + A2 + A5's push-refusal** are built and stress-tested
(A6), the cold-card test passes, and the PADDOCK tab shows the live/stale split. A3 (TOOLS UI)
and A4 (HUD honesty) may ride 0.3.0 or follow as 0.3.x at build-time discretion. Rationale:
nightly-chasers (the kit's own operator first among them) will hit an epoch flip within days of
install — the kit must condition shaders without requiring them to remember a shell command.
