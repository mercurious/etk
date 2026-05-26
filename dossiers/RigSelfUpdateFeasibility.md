# RIG SELF-UPDATE — FEASIBILITY DOSSIER
**Status:** Feasibility analysis. Not yet implemented. Captured for a future focused sprint.
**Audience:** Claude Code (deep-dev implementer, future session)
**Provenance:** Operator question 2026-05-26 — "Can the rig request its own ETK software update from the GitHub repo, from within ETK Pitstop TOOLS — Check for ETK Update > Update ETK > Prompt to reboot?"
**Premise (operator):** "I'm guessing we need a way to make a release to accomplish this?" — confirmed; this dossier explains why and what shape that takes.

---

## §A. WHY (the friction this removes)

Today every ETK update requires the operator at a host computer:
1. `git pull` on host
2. `./install.sh` from host (SSH+rsync push to rig)
3. Sentry restarts, feature is live

For the operator that's fine — they have the host on the desk. But the friction surfaces in three concrete scenarios:

1. **Pre-alpha tester onboarding.** A friend gets the kit, flashes a card with Rocknix, but doesn't have a Mac/Linux box ready. Today they're blocked. The Windows install guide added WSL2 as a path, but a non-technical tester is still blocked by "install WSL2 first." A rig-side updater removes the host computer from the loop entirely after the first install.
2. **Field updates while traveling.** Operator at a coffee shop, rig on hand, host left at home, GitHub has a fresh feature → today nothing happens. With self-update, the operator hits "Check for Update" in Pitstop and pulls it over coffee-shop wifi.
3. **Shipping to non-technical users.** The "Final Mission" vision (auto-subscribing shader swarms) presupposes rigs that can update themselves. Self-update is the load-bearing precondition for everything downstream of that vision.

The proposal **does not replace** `install.sh`. It adds a second update path that is rig-initiated and rig-applied. `install.sh` remains the canonical host-side deploy + the only path for Tier-B host-side backup (which a rig-side updater fundamentally cannot replicate — see §B.3).

---

## §B. THE MODEL (the direction inversion install.sh has to share air with)

### B.1 What flips

| Phase | Today (install.sh from host) | Proposed (rig self-update) |
|---|---|---|
| Source of truth | Host's `~/etk/` working tree | GitHub release asset (tarball) |
| Transport | rsync over SSH (host → rig) | curl over HTTPS (rig pulls) |
| Trigger | `./install.sh` on host | Pitstop TOOLS menu on rig |
| Approval | Implicit (operator ran the command) | Explicit (Pitstop modal confirmation) |
| Backup | Tier-B mirror to `./state/` on host | Local pre-update snapshot on rig |
| Restart | Sentry restart via systemctl over SSH | Sentry restart via local systemctl |

### B.2 What stays host-only (the parts that fundamentally cannot move)

- **Tier-B backup TO the host (`./state/`)** — by definition the host snapshot has to be on the host. A rig-side updater cannot push to your laptop. The host install.sh remains the **only off-device backup**.
- **`--restore-state` from host** — same reason; the data lives on the host.
- **First-ever install on a fresh card** — chicken/egg: Pitstop needs ETK installed to offer the update menu. First install is always host-side (or via a pre-baked Rocknix image, see §G.2).
- **Release ceremony** — `gh release create` is operator-action on the host (or via GitHub Actions on tag push); rig is consumer, not producer.

### B.3 The non-negotiable: host install.sh stays load-bearing

The rig-side updater is **additive**, not replacement. install.sh's host-side Tier-B backup is the only off-device snapshot — a card death wipes everything on the rig including any "local pre-update snapshot." The operator (and future testers) should be coached: **run host-side install.sh periodically** as the off-device backup heartbeat, even if rig self-update is now their primary feature-pull mechanism. The rig-side updater UI should say so.

---

## §C. ARCHITECTURE

### C.1 Release flow (operator side, one-time setup + per-release ritual)

**One-time setup:**
1. Adopt SemVer-like tag convention (`v0.1.0`, `v0.2.0`, …). No tags exist today (`git tag -l` empty); first release becomes `v0.1.0`.
2. Decide release-asset shape: see §D.2.
3. Optional: `.github/workflows/release.yml` to auto-build the asset on tag push so the operator only has to `git tag vX.Y.Z && git push --tags`.

**Per-release ritual (manual until automated):**
1. Land changes on `main` as today.
2. `git tag vX.Y.Z -m "release notes summary"`.
3. `git push origin vX.Y.Z`.
4. `gh release create vX.Y.Z --generate-notes --target main` (auto-generates notes from commits since previous tag).
5. Upload the curated tarball asset (`gh release upload vX.Y.Z etk-vX.Y.Z.tar.gz`) OR rely on GitHub's auto-generated source tarball.

The operator already has `gh` authenticated locally with `repo` + `workflow` scopes (audited 2026-05-26) — no auth blockers.

### C.2 On-device flow (the new path)

```
Pitstop TOOLS tab → Check for ETK Update
                     │
                     ▼
              GET api.github.com/repos/USER/etk/releases/latest
                     │
                     ▼
              Parse tag_name (jq), compare to $ETK_ROOT/VERSION
                     │
              ┌──────┴──────┐
              ▼             ▼
        Up to date     New release available
        (just notify)  Show: "Current vA.B.C, Latest vX.Y.Z"
                            │
                            ▼
                     User selects "Update ETK"
                            │
                            ▼
                     Modal: confirm
                            │
                            ▼
                     bin/update_d.sh executes (see C.3)
                            │
                            ▼
                     Success: prompt "Reboot now / later"
                     Failure: rollback + show error
```

### C.3 The on-device updater script — `bin/update_d.sh`

A new dedicated script. Skeleton:

```bash
#!/bin/bash
# bin/update_d.sh <release-tag>
#  - Downloads the release tarball from GitHub
#  - Stages in /tmp/etk_update_<tag>/
#  - Snapshots current bin/scripts/config/tools to .bak.<oldtag>
#  - Atomic swap (mv old aside, mv new in)
#  - Re-runs the install.sh equivalents that must stay rig-local:
#     - Step 1 mkdir -p (idempotent, in case release adds dirs)
#     - Step 5 etk_modules_inject.py + launcher copy
#     - Step 6 Sentry rewrite (the heredoc in install.sh:243-628)
#  - systemctl daemon-reload + restart etk.service
#  - Writes new $ETK_ROOT/VERSION
#  - On any non-zero exit: rolls back atomically from .bak

source /storage/games-internal/roms/etk/scripts/env.sh
TAG="$1"
# ... validate tag format, fetch tarball, etc.
```

### C.4 Version tracking

- `$ETK_ROOT/VERSION` — single-line plain text containing the active tag (e.g. `v0.1.2`).
- Written by **both** the rig-side updater AND install.sh on host deploy (a one-line addition to install.sh Step 5 deploy phase, sourced from `git describe --tags`).
- Absent on first install of a pre-versioning era kit → updater treats absent as "unknown / pre-release"; offers to update to current latest unconditionally.

### C.5 install.sh refactor required (the structural cost)

To prevent install.sh and update_d.sh from drifting, the **deploy-only steps** of install.sh (Steps 1, 5, 6) should be extracted into a shared script that BOTH callers invoke:

- `scripts/deploy_phase.sh` — pure-rig-local logic, no SSH, no host paths. Provisions dirs, deploys launcher, re-arms Sentry.
- install.sh calls `ssh $RIG_SSH "bash $ETK_ROOT/scripts/deploy_phase.sh"` after Step 3's rsync deposits the latest scripts/.
- update_d.sh calls `bash $ETK_ROOT/scripts/deploy_phase.sh` directly after the tarball swap.

This is non-trivial work but it's the ONLY way to keep the two paths in sync. Without it, every install.sh change has to be mirrored in update_d.sh, which won't happen reliably.

**Alternative if the refactor is too invasive:** treat update_d.sh as a minimum-viable script that just swaps files and restarts Sentry, accepting that any install.sh deploy phase changes require a coordinated update_d.sh change. Acceptable trade-off in early pre-alpha; would not scale to shipping.

---

## §D. COMPONENTS (what needs building / changing)

### D.1 New code artifacts

| Artifact | Purpose | Approx. lines |
|---|---|---|
| `bin/update_d.sh` | Rig-side updater (download, snapshot, swap, redeploy, rollback) | ~150 |
| `scripts/deploy_phase.sh` | Shared deploy logic extracted from install.sh (see §C.5) | ~80 |
| `bin/etk_pitstop.py` additions | TOOLS-tab "Check for Update" + "Update ETK" menu items + flow handlers | ~120 |
| `.github/workflows/release.yml` (optional) | Auto-build asset on tag push | ~40 |

### D.2 Release asset shape — pick one

| Option | Pros | Cons |
|---|---|---|
| **A. GitHub auto-generated source tarball** (`tarball_url` in API response) | Zero packaging work. `gh release create` produces it free. | Includes EVERYTHING in git — dossiers, docs, screenshots PNGs (~5 MB). Wasteful over coffee-shop wifi. |
| **B. Curated `etk-vX.Y.Z.tar.gz`** uploaded via `gh release upload` | Only bin/scripts/config/tools/install.sh. ~200 KB. Surgical. | Requires per-release packaging step OR an Action to build it. |

**Recommendation:** start with A for the v0.1.0 MVP (zero infra), switch to B (with automating Action) before shipping to non-operator users.

### D.3 install.sh additions (small, additive)

1. Write `$ETK_ROOT/VERSION` from `$(git describe --tags --always)` in Step 5 (Pitstop deploy phase).
2. After §C.5 refactor: replace Steps 1/5/6 inline logic with a single `ssh $RIG_SSH "bash $ETK_ROOT/scripts/deploy_phase.sh"` invocation.

### D.4 Pitstop UI additions

New menu items under existing TOOLS tab (line 188 in `bin/etk_pitstop.py`, the `TABS` registry is already plugin-friendly — author left clean seams for additions):

```
TOOLS tab
├── Install a staged PS3 Package        [existing]
├── Check for ETK Update                [new]
│    └── (network call, version compare, display result)
├── Update ETK                          [new, conditional — only shown if newer available]
│    └── (modal confirm → invoke update_d.sh → progress display → reboot prompt)
└── About / Version Info                [new — shows current $ETK_ROOT/VERSION]
```

Progress display: lean on the existing ETK mako style (1280×560 banner, configured in install.sh:155-167 via `[app-name="ETK Pitstop"]`). Spinner in the curses TUI during the long-poll, mako toast on completion. The Pitstop install-lock pattern at `01-etk-sentry.sh:491` (`ETK_INSTALL_LOCK`) is exactly the right model to extend — set a `$ETK_UPDATE_LOCK` so Sentry parks IDLE while update runs.

### D.5 env.sh additions

```bash
# --- [ SELF-UPDATE ] ---
export ETK_GITHUB_REPO="USER/etk"   # operator sets at install time
export ETK_VERSION_FILE="$ETK_ROOT/VERSION"
export ETK_UPDATE_LOCK="$SHM_DIR/etk_update_lock"
export ETK_UPDATE_STAGE_DIR="/tmp/etk_update_staging"
```

---

## §E. DEPENDENCIES & PRECONDITIONS (all audited 2026-05-26 — green)

**On the rig (Rocknix nightly-20260525 confirmed):**
- `curl` ✓ — `/usr/bin/curl`
- `jq` ✓ — `/usr/bin/jq` (for GitHub API JSON parsing)
- `python3` ✓ — `/usr/bin/python3` (already used by Pitstop)
- `systemctl` ✓ — for Sentry restart + (eventually) `reboot`
- `tar` ✓ — implied (BusyBox)
- ✗ `git` — **NOT installed.** Confirms tarball+curl is the only viable pull mechanism (no `git pull`/`git clone` paths).

**On the host:**
- `gh` CLI authenticated as `mercurious` with `repo`, `workflow` scopes ✓ — `gh release create` and `gh release upload` work today.

**Network:**
- Rocknix manages WiFi natively. Operator already configures it during onboarding.
- GitHub API anonymous rate limit: 60 req/hour. "Check for update" polling at 1 req/click is fine; if Pitstop ever polls in the background, must respect this limit (cache the last check).

---

## §F. FAILURE MODES & RECOVERY

| Failure | Detection | Recovery |
|---|---|---|
| Network drop mid-download | `curl --fail` non-zero, partial tarball in stage dir | Delete stage dir, report "network failed" toast, leave running kit untouched |
| Tarball integrity fail (truncated, malformed) | `tar -tzf` validation before swap | Same as above — never swap from a bad tarball |
| Atomic swap interrupted (power loss between `mv old` and `mv new`) | `.bak` exists but new dir doesn't on next boot | Sentry boot-time check: if `.bak.<tag>` present and current bin/ missing or empty → auto-rollback from `.bak` |
| Sentry won't start after update | `systemctl is-active --quiet etk.service` returns false post-restart | update_d.sh auto-rolls back from `.bak`, re-restarts Sentry, reports failure to Pitstop |
| User cancels mid-update | Pitstop sends SIGTERM to update_d.sh | update_d.sh trap → cleanup stage dir, no swap performed yet → safe |
| Update breaks Pitstop (no UI to fix it) | Operator can't get back into Pitstop | **R3 still works** (load-bearing per memory) — recovery.sh path is untouched. **And** host install.sh from a laptop always works as the escape hatch. |
| GitHub API rate-limit (rare) | HTTP 403 in API call | Pitstop shows "API rate limit; try in N minutes" |

**Critical safety property:** the rig-side updater must NEVER prevent the host-side install.sh from working as escape hatch. install.sh + Sentry's `--restore-state` path are the disaster-recovery floor under everything.

---

## §G. OPEN QUESTIONS (resolve before implementation sprint)

### G.1 Reboot prompt: required, or just Sentry restart?

Most updates only need `systemctl restart etk.service`. Some (systemd unit changes, kernel-touching changes, OS-level deps) need full reboot. Three options:

- **(a)** Always reboot — conservative, slow UX
- **(b)** Never reboot — clean UX, sometimes wrong
- **(c)** Reboot if `etk.service` unit file changed, otherwise just restart — correct UX, more complex

Recommend (a) for v0.1.0 MVP; revisit at v0.2.0.

### G.2 Bootstrap problem: where does the FIRST install come from?

Pitstop self-update assumes ETK is already installed. First install paths:
- Host `install.sh` (today)
- WSL2 + `install.sh` (Windows path, today)
- Bundled in a custom Rocknix ImageBurner image (the option-2 path from the earlier Windows-support analysis — still deferred)

Self-update changes nothing about bootstrap. It only changes the *second-and-onward* update.

### G.3 Operator's own commits between releases

Pre-alpha reality: operator commits to `main` frequently, releases lag. Two sub-questions:
- Should self-update pull from `main` (any commit) OR only from tagged releases?
- If only tagged releases, what's the cadence — every meaningful commit gets tagged, or weekly batches?

Recommend tagged-only (cleaner, signals "this is intentionally release-worthy"). Tag cadence is operator preference; auto-changelog from `gh release create --generate-notes` makes per-commit tagging cheap.

### G.4 Asset signing / integrity verification

For pre-alpha single-operator: TLS to api.github.com is the trust anchor; no signing needed.
For beta+: consider release-asset SHA256 verification (publish hash in release notes, update_d.sh verifies).
For shipping: GPG-sign tarballs.

Recommend deferring all signing to beta. Documented here as future-state.

### G.5 What about `vault/` shaders during update?

Shaders MUST NOT be touched by self-update — they're Tier-A user state, regenerable but expensive. Same exclusion rule as Tier-B (`etk_telemetry/`, `custom_configs/`, `dev_hdd0/home/`, `screenshots/`).

Asset must contain ONLY the deploy artifacts (bin/scripts/config/tools/install.sh/uninstall.sh). The `.gitignore`'d directories (`vault/`, `state/`, `screenshots/`) are already excluded from any tarball built from a clean git tree — verify this when picking the asset shape (§D.2).

---

## §H. PHASED IMPLEMENTATION PLAN

### Phase 0 — Versioning ceremony (no code changes; ~30 min)
- Pick the SemVer convention
- Tag the current HEAD as `v0.1.0` (current state of kit is a sensible baseline)
- `gh release create v0.1.0 --generate-notes`
- Manually verify the auto-generated tarball downloads + extracts cleanly

### Phase 1 — Version-awareness on rig (~1 hr)
- Add `$ETK_ROOT/VERSION` write to install.sh Step 5
- Re-deploy via `./install.sh` so the rig now has a known version
- Add a tiny "About" item to Pitstop TOOLS tab that just reads the file — proves the read path works

### Phase 2 — "Check for Update" (read-only; ~2 hr)
- Pitstop TOOLS menu: "Check for ETK Update"
- curl to GitHub API, parse with jq, compare to local VERSION
- Display result (no action button yet — pure information)
- Proves: network call works, version compare works, no destructive surface

### Phase 3 — `bin/update_d.sh` (the meat; ~4 hr)
- Download + verify + stage tarball
- Atomic swap with .bak
- Re-run rig-local deploy steps
- Rollback on failure
- Test extensively with throwaway versions before exposing in UI

### Phase 4 — Wire "Update ETK" into Pitstop (~2 hr)
- Conditional menu item (only shown if newer available)
- Modal confirm → invoke update_d.sh → progress mako toast → reboot prompt

### Phase 5 — install.sh refactor for deploy_phase.sh (~3 hr)
- Extract Steps 1/5/6 from install.sh into `scripts/deploy_phase.sh`
- install.sh and update_d.sh both call it
- Eliminates drift risk

### Phase 6 — Optional polish
- GitHub Action for automated release-asset build on tag push
- Custom tarball (option D.2-B) instead of auto-source
- Asset SHA256 verification

**Total estimate:** ~12 hr deep-dev for Phases 1-5, deployable to alpha-tester rigs. Phase 6 anytime later.

---

## §I. ACCEPTANCE CRITERIA (for the implementation session)

1. **install.sh unchanged in behavior.** All §H acceptance criteria from the Tier-B addendum still pass.
2. **`$ETK_ROOT/VERSION` is the single source of truth.** Both install.sh and update_d.sh write it; Pitstop reads it.
3. **"Check for Update" is purely read-only.** No state change on rig from a check.
4. **"Update ETK" requires explicit modal confirmation.** No silent updates.
5. **Update is atomic.** A power loss mid-update leaves the rig either fully on old version (with valid Sentry) or fully on new version (with valid Sentry); never in-between.
6. **Failure auto-rolls-back.** If post-update `etk.service` won't start, the rig reverts to the previous version automatically.
7. **R3 panic button works through and after an update.** Load-bearing — verify input_d.py keeps running or respawns within the Sentry tick.
8. **Host install.sh remains the escape hatch.** A bricked rig-side updater never prevents `./install.sh` from a host laptop from fully restoring the rig.
9. **No SD writes during a "Check for Update" that returns up-to-date.** Treadwear discipline.
10. **Operator can disable self-update entirely** by setting `ETK_GITHUB_REPO=""` in env.sh — fallback for paranoid mode.

---

## §J. SUMMARY FOR THE FUTURE-SPRINT TL;DR

- **Feasible: yes.** All rig-side deps present (curl, jq, python3, systemd, tar); no git on Rocknix means tarball-based pull, not git pull.
- **Requires a release ceremony.** First-time tag (`v0.1.0`), then per-release `git tag + gh release create`. `gh` CLI already authenticated locally with required scopes.
- **Architecture is additive, not replacement.** install.sh stays canonical for host-deploys and is the only off-device Tier-B backup path. update_d.sh is a new second path for rig-initiated updates.
- **The structural cost is real:** extract install.sh's Steps 1/5/6 into a shared `scripts/deploy_phase.sh` so install.sh + update_d.sh don't drift. Skippable in v0.1.0 MVP, mandatory before shipping.
- **~12 hours deep-dev for an alpha-tester-shippable rig self-update**, across 5 phases that each ship independently.
- **The killer onboarding win:** non-technical Windows testers can flash a card, get ETK once (via WSL2 or pre-baked image), then never touch a host computer again to keep current.
