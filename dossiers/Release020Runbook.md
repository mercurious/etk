# Release Runbook — ETK v0.2.0 ("Nightly Rider")

**Date prepared:** 2026-06-11 · **Scope:** Stage III T0 harness + nightly-20260610 re-pin
**Working tree state:** all v0.2.0 changes are uncommitted on `main` (see `git status`); on-rig validated same-day.

## What ships (already edited, uncommitted)

| File | Change |
|---|---|
| `install.sh` | New **STEP 7 `STAGE3 HARNESS`** (profile.d Mesa-cache cap + ulimit, coredump script, `etk-stage3.service` oneshot, `STAGE3_OK` marker, non-fatal on failure); `TUI_TOTAL_STEPS=7` |
| `tools/tui.sh` | 7th step label `STAGE3 HARNESS` |
| `uninstall.sh` | Removes all three harness artifacts + restores stock `core_pattern` (`|/bin/false`) |
| `README.md` | Re-pin to nightly `20260610` (System Requirements, Getting Started, Windows flash); GRUB tip verify-step + update-revert note |
| `CHANGELOG.md` | `[0.2.0]` entry (use as GitHub release notes) |
| `scripts/profiles/SM8250.sh` | Certification comment → nightly-20260610 (prior baseline retained) |
| `AI_MANIFEST.md` | TARGET OS → nightly-20260610 + rationale |
| `dossiers/` | Stage3CustomRigDossier, Aps3eVkPipelineCacheDossier, FablesChallenge, this runbook |

**On-rig validation already done (2026-06-11):** every step-7 artifact was deployed by hand to the live rig first and verified (`core_pattern=/storage/cores/...` after reboot via the enabled oneshot unit; `Max core file size: unlimited` inherited by ES; vault healthy at 127 MB/14k files under the 10G cap). The install step writes byte-identical content.

## Public/private split (EXECUTED 2026-06-11)

Operator decision: PADDOCK stays on the personal device but out of public reach during legal review. Actions taken:
- **Public vault pre-release `vault-SM8250-turnip26.1.0` deleted** (operator) — tag + assets gone from `mercurious/etk` (13 downloads had occurred, some operator's own).
- **`vault-index/manifest.json` neutralized on main** — `tunes: []` + withdrawal note; `vault-index/README.md` carries a status banner. Verified both consumers degrade gracefully: `install-protune.sh:60` fails with "no Pro Tuning published", Pitstop PADDOCK (`etk_pitstop.py:2736,2840`) shows "no tunes published". The PADDOCK *code* stays on public main — inert without an index, and scrubbing pushed history would break clones for zero legal benefit.
- **Private repo `mercurious/etk-garage` created**; local branch `garage` (cut from main @ `e00d8e3`, i.e. *with* the live index) pushed as its default branch; remote `garage` added alongside `origin`.
- **Vault release recreated privately**: `etk-garage` release `vault-SM8250-turnip26.1.0` with both bundles from `pro-tuning/dist/` (note: those vaults are Turnip-26.1.0-era; they no longer pass the mesa_hash gate on 26.1.2 rigs — kept for the prior epoch).

**Workflow from here:** PADDOCK development + future vault minting happens on `garage` (push to the `garage` remote only — never `origin`); public `main` carries the neutralized index; merges `garage → main` wait for legal clearance. The personal rig is unaffected: `install.sh` deploys from the local checkout. **Token note:** the operator's ETK GitHub token is only needed if/when the *rig* should pull private release assets directly (PADDOCK subscribe against `etk-garage`); deferred — personal bundles currently travel over SSH/Pitstop. When wiring it: `PROTUNE_INDEX_URL` already supports an override (`install-protune.sh:28`), and the token would go in an `etk.conf`-style gitignored local config, never in the tree.

## Step 1 — Commit on main (suggested split)

```bash
git add install.sh tools/tui.sh uninstall.sh
git commit -m "feat(stage3): stability harness install step — Mesa 10G cache cap + boot-persistent coredump capture"

git add README.md CHANGELOG.md scripts/profiles/SM8250.sh AI_MANIFEST.md
git commit -m "feat(release): re-pin to ROCKNIX nightly-20260610 (RPCS3 19444 w/ GT5 leak fix, Turnip 26.1.2)"

git add vault-index/
git commit -m "chore(vault-index): withdraw Pro Tuning distribution pending legal review (empty index, graceful consumers)"

git add dossiers/
git commit -m "docs(dossiers): Stage III custom rig dossier + aPS3e pipeline-cache sprint + v0.2.0 runbook"
```

## Step 2 — Choose the release path (operator decision: PADDOCK posture)

**Path B — zero-PADDOCK release branch (RECOMMENDED — honors the standing 0.5.0 legal gate).**
`main` has carried the PADDOCK tab, pro-tuning scaffold, vault-index, and two minted clean-room vaults since v0.1.4 (`3637aaa`, `a7df6ed`, `63094f4`). The standing decision ([[project_paddock_tab_050]], Session20260609Handoff §5) keeps all of that parked pending legal review.

```bash
git checkout -b release/0.2.0 v0.1.4
git cherry-pick 7b4e3e6     # fix(install): PKG-installer truncation fix
git cherry-pick d231bd4     # feat(hud): vault GB abbreviation + VAULT:LOADING
git cherry-pick <sha-1> <sha-2> <sha-3>   # the three commits from Step 1
```
**Conflict expectations:** `main` has drifted ~3.5k lines from v0.1.4 (mostly PADDOCK + README). Likely conflict points: `README.md` (many interim edits — resolve by keeping the cherry-picked hunk inside the v0.1.4-era surrounding text) and possibly `bin/etk_pitstop.py` context for `7b4e3e6`. The handoff (§5) verified `_run_install` is identical at v0.1.4, so that pick should be clean. If a pick conflicts beyond comfort: `git cherry-pick --abort` and fall back to hand-porting the hunk (every change in this release is small and self-contained except the README).

Sanity gate before tagging — the release branch must contain **no PADDOCK surface**:
```bash
git grep -il paddock -- bin/ config/ pro-tuning/ vault-index/ | wc -l   # expect 0 paths / pathspec errors
bash -n install.sh && bash -n uninstall.sh && bash -n tools/tui.sh
```

**Path A — tag main directly (only if the PADDOCK gate is re-judged).** Trivial mechanically (`git tag v0.2.0 main`), but it ships the PADDOCK subscribe/install tab + the committed vault index in a versioned release — a *distribution* posture change that the legal review was supposed to clear first. Not recommended without that call.

## Step 3 — Tag, push, release

```bash
git tag v0.2.0
git push origin release/0.2.0
git push origin v0.2.0      # push the ONE tag explicitly — NEVER `--tags`
                            # (a stale local copy of the withdrawn vault tag
                            # would be re-published by --tags; local copy was
                            # deleted 2026-06-11, but the rule stands)
gh release create v0.2.0 --target release/0.2.0 --title "ETK v0.2.0 — Nightly Rider" --notes-file ~/Desktop/etk_v020_release_notes.md
```

**EXECUTED 2026-06-11:** branch `release/0.2.0` built from v0.1.4 (5 cherry-picks: bb106cd installer fix · 7accfad HUD QoL · 8cbb106 GRUB tip · 72dc8d4 stage3 harness · 5e9459b nightly re-pin; two README conflicts resolved — duplicate Internal-Storage section dropped, v0.1.4 Windows wording kept). Sanity gate PASSED: no `vault-index/`, no `pro-tuning/`, zero PADDOCK code tracked (none of it existed at v0.1.4 — the release tree has no distribution surface at all). `bash -n` clean on install/uninstall/tui. Branch pushed; tag `v0.2.0` created locally; stale local vault tag deleted. Notes drafted at `~/Desktop/etk_v020_release_notes.md`. **Remaining (operator): push the tag + `gh release create`.**

## Step 4 — Post-release

1. Merge/cherry-pick the release commits back to `main` if Path B created any conflict-resolution deltas.
2. Re-run `./install.sh` once from the released tree against the rig — confirms STEP 7 renders in the Pit Wall TUI and `STAGE3_OK` fires (the only piece not yet exercised end-to-end is the TUI step itself; the payload is already validated live).
3. Bank a drift baseline for the nightly: `etk_drift.py --check` on-rig, bank `20260610.json` (matches the 0.1.4-era certification discipline).
4. Watch items for 0.2.x: thermal-failsafe frequency on long sessions (3 events on day one — if PIT latching mid-race becomes a pattern, revisit `RACE_THRESHOLD`/fan curve before touching anything else); first core in `/storage/cores/` gets symbolized against the AppImage and attached to the upstream RPCS3 SPU report.

## Out of scope for 0.2.0 (tracked in Stage3CustomRigDossier)
- GPU min-freq pin in `thermal_d.sh` RACE mode (E4 — needs its own soak; don't stack changes).
- TU_DEBUG ladder (E5), patched-JIT emulator builds (T2), custom Rocknix image (T3).
- UFS migration (sequenced after the current soak window; see dossier §5).
