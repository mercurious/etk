# Rocknix Nightly Migration Certification — nightly-20260529

**Status:** ✅ **CERTIFIED 2026-05-29** — in-place migration 20260528→20260529 on SM8250. `--check` clean; the 10 `--diff` input CRITICALs were benign node renumbering (DualSense buttons `event9→event8`, `find_gamepad()` self-healed by name); gamepad codes unchanged; **R3 survives suspend/resume**; RPCS3 binds Adreno 650 (RPCS3.log); v0.1.2 screenshot + Tools-icon features confirmed; `20260529.json` saved as the new `*PIN`. Shipped in ETK v0.1.2.
**Candidate nightly:** `nightly-20260529` (Pre-release)
**Current pin (last verified):** SM8250 profile re-cal note = `nightly-20260525`; gamepad codes last probe-verified `2026-05-24` (DS5 std mapping). Closeout dossier covers `20260516/17 → 20260520`.
**Tier-1 target:** SM8250 (Retroid Pocket Flip 2, Adreno 650 / Turnip).
**Tooling:** `tools/etk_drift.py` (on-board baselining) + `bin/gamepad_probe.py` (button-code capture).

> **Nightlies are cumulative.** Adopting `20260529` also lands every change in `20260528` (and any 26/27 nightly we don't have changelogs for — see Gap G3). This dossier triages both published changelogs against ETK surfaces.

---

## §1. Per-change risk triage

| # | Nightly | Change | ETK surface touched | Risk | Why |
|---|---------|--------|---------------------|------|-----|
| 1 | 20260529 | **InputPlumber MelonDS mappings fix** | Gamepad button codes (304/305/310/314/316-317/318) — *maybe* | **LOW (verify)** | MelonDS is a Nintendo-DS core; the fix is almost certainly scoped to MelonDS's controller target, not the global virtual pad RPCS3 sees. But any InputPlumber mapping edit is a *button-code* risk, and the drift tool does **not** capture codes (only node→name). Must confirm with `gamepad_probe.py`. |
| 2 | 20260529 | CI: trim whitespace from PR-title validation | none | **NONE** | Build-infra only. |
| 3 | 20260529 | Revert "Disable speaker compression for Pocket ACE" | none | **NONE** | Pocket ACE is a different device; SM8250 unaffected. |
| 4 | 20260529 | **inputplumber: allow ExecStartPre to fail** | Virtual-controller node enumeration / boot timing | **LOW–WATCH** | Makes the InputPlumber unit start even if its pre-step fails → node may appear in *more* conditions, possibly with different timing. `input_d.py`'s self-heal connect loop (Gemini rule #3) already tolerates a late/absent node, so R3 is defended — but confirm the node still appears and R3 is armed within the usual window after boot. |
| 5 | 20260529 | **rocknix-fake-suspend: process freeze/unfreeze (fix slow resume / Steam)** | **Suspend/resume → R3 panic path** | **WATCH (highest of the set)** | Closeout §4 lesson 5: *"the headless gate is suspend/resume + R3."* This change rewrites how processes are frozen/unfrozen across suspend — the exact path R3-survival depends on. If the freeze cgroup catches ETK's systemd daemons (Sentry, input_d, thermal_d, vault_d), they must unfreeze cleanly and re-bind on resume. Practical exposure is low (a racing rig rarely suspends), but this is the one change that intersects a load-bearing, previously-fragile path. |
| 6 | 20260528 | gamescope: fix mouse cursor invisibility | none | **NONE** | ETK runs RPCS3 under **sway** (`WAYLAND_DISPLAY=wayland-1`), not gamescope. Irrelevant unless Rocknix moves RPCS3 launch to gamescope (it hasn't). |
| 7 | 20260528 | Revert "SM8550 Home key mapping for AYN button on Thor" | none on SM8250 | **NONE (note)** | SM8550 (Thor) is not a tier-1 ETK profile (only `SM8250.sh` exists). HOME is not an ETK-owned button. Flag for device-agnostic readiness only. |

**Net:** profile-pinned surfaces (thermal / CPU / GPU / adapter) — **untouched**. The whole risk surface is InputPlumber (#1, #4) and fake-suspend (#5), all converging on the **gamepad + headless-recovery** path.

---

## §2. What the drift tool covers — and the gap

`etk_drift.py` probes: thermal zones, CPU clusters/policies, GPU devfreq/Turnip, **input map (event node → device name)**, os-release. Its input diff is `CRITICAL` and points at `find_gamepad()` / `PAD_HINTS` / `PAD_EXCLUDE`.

- ✅ **Covers:** a device-*name* change or node-set remap (the DS5 four-sibling-node situation). Expect node-index churn here as **known noise** — memory: nodes drift (`event8→event9`), `find_gamepad()` matches by name not index, so a node renumber that keeps the name is benign.
- ❌ **Does NOT cover (the gap for this migration):**
  1. **Button *codes*.** `input_map()` reads `device/name`, never the keycodes. Changes #1/#4 are precisely code-mapping risks → **`gamepad_probe.py` is mandatory**, not optional.
  2. **Suspend/resume behavior.** No probe exercises the fake-suspend freeze/unfreeze path (#5) → **manual R3-across-suspend test required**.
  3. **RPCS3 SDL device name.** Closeout §2.3 — RPCS3's pad binds to `DualSense Wireless Controller 1` (SDL index suffix). Not an OS-surface the tool reads; verify in-game input lands.

---

## §3. On-rig certification protocol

Run on the rig after in-place update (memory: **in-place updater only, never clean reflash**). Recommend a spare card or a vault-archived state first per closeout §4.

```sh
# 0. PRE: bank the current OS as the rollback reference (if not already pinned)
python3 tools/etk_drift.py --list                  # confirm a pin exists
# (only if no current pin:) python3 tools/etk_drift.py --save-baseline --pin

# --- flash nightly-20260529 via Rocknix built-in updater, reboot ---

# 1. PROFILE ASSUMPTIONS — run the moment the nightly boots
python3 tools/etk_drift.py --check
#   EXPECT: clean. CRITICAL on any thermal/CPU/GPU field => stop, investigate.

# 2. TEMPORAL DIFF vs the pin
python3 tools/etk_drift.py --diff pin
#   EXPECT: thermal/cpu/gpu clean; INPUT diffs only as node-name noise.
#   A genuine NAME change on the pad node => find_gamepad() review.

# 3. BUTTON-CODE VERIFY  <-- the drift-tool gap; covers changes #1/#4
python3 bin/gamepad_probe.py            # auto-finds the buttons node
#   Press and confirm UNCHANGED:
#     R3=318  L3=317  L1=310  SELECT=314  DPAD ABS_HAT0X=16 / ABS_HAT0Y=17
#     confirm/back = 304/305 (DS5 standard)
#   ANY code drift => update input_d.py / etk_pitstop.py BTN_* before trusting.

# 4. HEADLESS GATE  <-- covers change #5 (fake-suspend) + #4
#   a) Cold boot: confirm R3 panic arms within ~2s (input_d.py self-heal).
#   b) Power-button SUSPEND -> RESUME, then press R3 -> recovery.sh must fire.
#      Confirm Sentry/thermal_d/vault_d unfroze and resumed (telemetry ticks).
#   c) Confirm thermal_d still governs (PIT cap holds) post-resume.

# 5. JUST-SHIPPED FEATURE regression (v0.1.2)
#   L1 screenshot three-state: in-game fires only in-game; disabled frees L1;
#   SELECT+DPAD-Up always fires. (See CHANGELOG 0.1.2.)

# 6. IN-GAME input sanity (closeout §2.3 surface)
#   Launch a PS3 title; confirm RPCS3 binds the pad (not "Adding empty device").

# 7. CERTIFY: if 1-6 clean, adopt as the new pin
python3 tools/etk_drift.py --save-baseline --pin
#   Then bump the nightly note in scripts/profiles/SM8250.sh + env.sh thermal comment.
```

**Abort/rollback triggers:** any CRITICAL in step 1-2 on a non-input field; any button-code drift in step 3 not yet patched; R3 fails to fire post-resume in step 4b.

---

## §4. Gaps / open questions

- **G1 — drift tool button-code blindness.** This migration is the case that exposes it: the only real risk is button mappings and the tool can't see them. *Enhancement candidate:* teach `etk_drift.py` to `EVIOCGNAME`/capability-probe the matched pad node (or fold a non-interactive `gamepad_probe` capability dump into the profile) so future InputPlumber nightlies are caught by `--diff` alone. Track separately; not blocking.
- **G2 — fake-suspend × ETK daemons.** No data on whether the new freeze/unfreeze cgroup includes our `etk.service` tree. Step 4b is the empirical answer; if daemons get frozen and resume dirty, that's a real finding worth its own dossier.
- **G3 — changelog coverage.** We only have `20260528` + `20260529` changelogs. If the current pin is older than `20260528`, intervening nightlies (26/27) may carry unseen changes. Verify the pin's build_id via `--list` and read any skipped changelogs before certifying.
- **G4 — RP5 chassis.** Thermal thresholds in `SM8250.sh` are Flip-2-calibrated; unrelated to this nightly but a standing caveat for any RP5 claim.

---

## §5. Recommendation

**Provisional: LOW-RISK to adopt, certification-gated on the gamepad + suspend/resume checks (steps 3-4).** Nothing in either changelog touches the pinned thermal/CPU/GPU profile, so the bulk of ETK is unaffected. The entire risk concentrates on the InputPlumber/suspend path — defended in code by `input_d.py` self-heal and probe-first discipline, but **must be empirically re-verified** because (a) it's the headless-recovery gate and (b) the drift tool can't see button codes or exercise suspend. Do **not** certify on `--check`/`--diff` green alone.
