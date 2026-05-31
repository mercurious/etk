# Rocknix Nightly Migration Certification — nightly-20260531

**Status:** ✅ **CERTIFIED 2026-05-31** — in-place 20260529→20260531 on SM8250. `--check` clean (no structural drift); `--diff` input CRITICALs were benign node renumbering (DualSense buttons `event8→event9`, `find_gamepad()` self-heals by name); Turnip driver bumped (WARN) and per-game render re-validated on GT5P (vault re-layered cleanly, +10k shaders, HUD nominal); gamepad codes unchanged; headless gate passed (ignition fires, R3 survives suspend/resume, RPCS3 binds Adreno 650); ETK Pitstop tile registers + renders post ES-engine bump (Tools-artwork bug confirmed still unfixed upstream, ETK tile insulated by thumbnail/marquee). `20260531.json` saved as the new `*PIN`. Operator observation: a 3-run CLEAN streak on GT5P (encouraging, not a certified race baseline). Certifies v0.1.3.
**Candidate nightly:** `nightly-20260531` (Pre-release, tag `a65c8da`)
**Current pin:** `20260529` (`etk_drift.py *PIN`), rig live on 20260529 / branch `next`.
**Tier-1 target:** SM8250 (Retroid Pocket Flip 2, Adreno 650 / Turnip).
**Tooling:** `tools/etk_drift.py` + `bin/gamepad_probe.py`.

> Cumulative: adopting 20260531 also lands 20260530's commits (it was 5 commits since the prior release).

---

## §1. Published changelog triage (20260531)

| Change | ETK surface | Risk | Notes |
|--------|-------------|------|-------|
| **emulationstation: bump package** (×2) | ES engine — gamelist regen, `<image>` rendering, Tools view, the modules-wipe timing the Sentry tripwire depends on | **WATCH** | Two ES engine bumps. ES is exactly the component that renders the Pitstop Tools entry + SVG and regenerates `gamelist.xml` each boot. Verify the modules-wipe/regen behavior and that the ETK Pitstop tile still registers + renders. **Does NOT fix our Tools-artwork bug — see §2.** |
| **rocknix-systems: update bios check** | none (ETK ships no BIOS) | NONE | PS3 firmware for RPCS3 is separate; not the systems BIOS check. |
| **updateabl: short hash when running** | none | NONE | Cosmetic updater output. |
| (20260530 carried) automount usb storages; aethersx2 panfrost; RK3326 fixes | none on SM8250 | NONE | Other-SoC / non-ETK subsystems. |

**Net:** the entire risk is the **two ES engine bumps**. Everything else is irrelevant to tier-1 SM8250.

## §2. Tools-menu artwork bug — repo investigation (the question asked)

**Investigated the upstream source directly. The bug is NOT fixed in 20260531.**

The shipped Tools gamelist is `projects/ROCKNIX/packages/misc/modules/sources/gamelist.xml`
(rsync'd to `/storage/.config/modules/gamelist.xml` each boot by `autostart/common/001-sync-modules`).
Fetched it at `next` HEAD and confirmed BOTH defects from `dossiers/ToolsMenuArtworkDiagnosis.md` persist:

- **Defect #1 (malformed XML):** the *Start touchHLE* `<desc>` still contains the raw
  `iOS 2 & 3` — 1 unescaped ampersand — so the file fails XML parse at line 392. Unchanged.
- **Defect #2 (field mismatch):** all 41 tool entries set only `<image>`; **zero** carry
  `<thumbnail>`/`<marquee>`, which the default `es-theme-art-book-next` theme actually reads.

So the two `emulationstation: bump package` commits are **ES *engine* version bumps, not
gamelist *data* fixes** — they don't touch either defect. **Expectation on 20260531:** stock
Tools icons still blank; **ETK's own Pitstop tile still renders** (our `etk_modules_inject.py`
emits `<thumbnail>`+`<marquee>`, independent of the upstream bug — verified still present).
The upstream Discord report (`dossiers/RocknixToolsArtworkBugReport.md`) remains unfiled/unfixed.

> Caveat: an ES *engine* bump could in principle change XML-parser strictness or the artwork
> resolution path. Low odds, but §3 step 6 re-checks the live Tools view empirically rather
> than assuming.

## §3. On-rig certification protocol

In-place updater only (memory: never clean reflash). Rig currently pinned at 20260529.

```sh
# 1. PRE: confirm pin exists (rollback reference)
python3 tools/etk_drift.py --list            # expect 20260529 *PIN

# --- flash nightly-20260531 via Rocknix updater, reboot ---

# 2. PROFILE ASSUMPTIONS
python3 tools/etk_drift.py --check
#   EXPECT clean. CRITICAL on thermal/CPU/GPU => stop.

# 3. TEMPORAL DIFF vs 20260529 pin
python3 tools/etk_drift.py --diff pin
#   EXPECT thermal/cpu/gpu clean. INPUT node-name churn = known benign noise
#   (find_gamepad matches by name). GPU_ADAPTER_STRING "unreadable" is the
#   known sysfs-gone INFO from 20260529 — confirm Adreno 650 via RPCS3.log.

# 4. BUTTON CODES (drift-tool blind spot)
python3 bin/gamepad_probe.py /dev/input/event8   # event8 = DualSense buttons on 29+
#   Confirm UNCHANGED: R3=318 L3=317 L1=310 SELECT=314 DPAD=16/17, confirm/back=304/305.

# 5. HEADLESS GATE (the load-bearing test)
#   a) cold boot: R3 fires recovery within ~2s.
#   b) suspend -> resume -> R3 fires (re-verify; ES engine bump shouldn't touch this,
#      but the gate is non-negotiable).
#   c) launch a PS3 title: confirm IGNITION (active_id != IDLE, no VAULT:ERROR),
#      RPCS3 binds "Turnip Adreno (TM) 650", thermal governs.

# 6. ES ENGINE BUMP regression (the actual 20260531 risk surface)
#   a) ETK Pitstop tile still appears in the Tools carousel (our thumbnail/marquee fix).
#   b) Sentry modules tripwire still re-injects after the boot modules-wipe
#      (confirm etk_pitstop.sh/.svg + the <game> block survive a reboot).
#   c) gamelist.xml regen unchanged enough that injection still works:
#        python3 -c 'import xml.etree.ElementTree as ET; ET.parse("/storage/.config/modules/gamelist.xml")'
#      (still expected to FAIL on the upstream touchHLE & — that's the unfixed
#      Rocknix bug, NOT an ETK regression; documents that 31 didn't fix it.)
#   d) v0.1.3 features: TELEMETRY detail view opens; L1 screenshot mode honored.

# 7. CERTIFY if 2-6 pass
python3 tools/etk_drift.py --save-baseline --pin
#   Bump nightly notes in scripts/profiles/SM8250.sh + env.sh + README.
```

**Abort triggers:** any CRITICAL in §3.2 on a non-input field; button-code drift not yet patched;
R3 fails post-resume; Pitstop tile fails to register after the ES bump.

## §4. Recommendation

**LOW-RISK to adopt, certification-gated on §3.5 (headless gate) + §3.6 (ES-bump regression).**
The published changelog is benign for SM8250 except the two ES engine bumps, and those concentrate
on exactly ETK's Tools-registration + gamelist surface — re-verify the Pitstop tile and the Sentry
re-injection tripwire empirically. The Tools-artwork bug we found is **confirmed still unfixed
upstream**, so no behavior change there is expected; ETK's own tile is insulated by our
`thumbnail`/`marquee` injection. This certification gates **v0.1.3** to 20260531.
