# Game-Factor Dossier — GT5 Prologue Spec III (NPEA00050) vs Spec II (NPUA80075)

**Status:** New game factor introduced post-Fablegate. Captured live from the rig
2026-06-14. **Working hypothesis ("Spec III runs better") is NOT yet supported by
the data — and is heavily confounded.** Let the ledgers decide under controls.

---

## 0. THE TWO SUBJECTS (confirmed on-rig)

| | **Spec II** | **Spec III** |
|---|---|---|
| Title ID | `NPUA80075` (US digital) | `NPEA00050` (**EU** digital) |
| PARAM.SFO versions | 01.00 / 02.00 / **02.15** | 01.00 / 02.40 / **03.00** |
| Install size | ~1.88 GB | ~2.0 GB |
| Launcher | `Gran Turismo 5 Prologue 1.0.psn` | `GRAN TURISMO 5 Prologue 3.0 eu.psn` |
| Custom config | 278-line ETK tune | **269-line — a DIFFERENT tune** |
| Career: sessions | 154 | 35 |
| Career: clean-rate | **27%** | **29%** |
| Career: play time | 15h 37m (saturated) | 2h 08m (**un-saturated**) |
| Career: panics | 2 (+2 panic-reboot) | **0** |
| Career: best streak | 4 | 3 |
| Vault / shaders | 26,537 (172/session) | 4,722 (135/session) |

~99% the same game (operator), different SKU + **region (US↔EU)** + version
(2.15↔3.00). Polyphony may well have optimized the title in the v3 update — but the
current setup cannot prove that yet (see confounds).

---

## 1. ASSUMPTION CORRECTION (the important part)

The hunch was *"Spec III might just be on a more default config."* **It isn't** —
it's on a deliberately DIFFERENT, lower-cost tune. The 278↔269 line count hides
~20 changed values. The load-bearing deltas (Spec II → Spec III):

| Setting | Spec II (NPUA80075) | Spec III (NPEA00050) | Likely effect |
|---|---|---|---|
| **Resolution** | **1280×720** | **720×480** | ~⅓ the pixels — biggest "feels better" driver |
| Multithreaded RSX | true | **false** | perf/stability tradeoff |
| SPU Block Size | Mega | **Safe** | conservative SPU compile |
| RSX FIFO Fetch Accuracy | Ordered & Atomic | Atomic | less strict |
| Accurate RSX reservation | true | false | speed > accuracy |
| ZCULL (stats/occlusion/sync) | accurate/on | **relaxed/off** | speed > accuracy |
| Driver Wake-Up Delay | 50 | 0 | latency |
| Async Texture Streaming | true | false | |
| Audio Renderer | FAudio | Cubeb | |

**So "Spec III runs better" is almost certainly dominated by 480p + RSX/accuracy
relaxations — a CONFIG effect — not (yet) a v3.00 binary effect.** Any version
would feel smoother at 480p with multithreaded-RSX off and ZCULL relaxed.

---

## 2. THE THREE CONFOUNDS (why the 27% vs 29% means nothing yet)

1. **Config** — different resolution + accuracy/threading (§1). The dominant one.
2. **Vault saturation** — Spec II is saturated (15.6h, 26.5k shaders); Spec III is
   early (2h, 4.7k). Crash-rate is *strongly* saturation-dependent
   ([project_race_baseline_status]: race-stable was reached on a SATURATED vault,
   not reproducible fresh). Spec III is still in the noisy harvest phase.
3. **Region + binary** — EU/v3.00 vs US/v2.15. This is the ONLY variable we
   actually want to measure, and it's currently buried under #1 and #2.

Current clean-rates (27% vs 29%) are within noise AND uncontrolled → **no stability
verdict.** Faint signals only: Spec III has **0 panics** and **no VkLost** crashes
so far (vs Spec II's 2 panics + 7 VkLost) — encouraging but tiny sample +
un-saturated + different config. Crash mix differs (Spec III 50% Silent vs Spec II
21%), consistent with early-harvest, not a binary trait.

---

## 3. EXPERIMENT DESIGN — isolating the v3.00 binary

Goal: answer *"is Spec III's v3.00 binary genuinely better, or is it just the
config (and being newer/less-saturated)?"* Hold #1 and #2 constant:

**Step A — normalize the config.** Pick ONE canonical tune and apply it to BOTH
IDs at the SAME resolution. Recommended: copy the proven Spec II 278-line tune to
`config_NPEA00050.yml` (so both run 720p, Multithreaded RSX on, same accuracy).
Keep the originals saved (`.specdiff.bak`) so the 480p tune isn't lost.
- (Optional B-arm: also test both at the Spec III 480p tune — that answers the
  separate, practical question "is 480p the better daily setup regardless of spec.")

**Step B — equalize saturation.** Let Spec III's vault harvest to a comparable
level before scoring stability (it's at 4.7k vs 26.5k). Until then, compare
*shaders/session* and *load time*, not clean-rate.

**Step C — let the ledgers fill, then compare** (per-ID, same config, similar
saturation):
- `clean_rate_pct`, `best_streak`, `current_streak`
- crash composition (Adreno/Silent/VkLost/Panic)
- `peak_load`, `peak_ram_mb`, `peak_temp`, load time, `shaders_harvested`

**Decision rule:** if Spec III still wins clean-rate / streak with an *identical
config at matched saturation* → credit the **v3.00 binary** (Polyphony
optimization felt at the emulation level, the operator's thesis). If parity →
the "better feel" was the **480p/relaxed config**, and the real lever is the tune,
not the spec. Either outcome is a real finding; the per-ID ledgers already
separate them cleanly (this is exactly why ETK telemetry is per-`game_id`).

---

## 4. WHY GT5P IS THE RIGHT VEHICLE (Goldilocks — operator taxonomy)

Both Spec II and Spec III keep GT5P's Goldilocks property that makes it ETK's
prime test subject:
- **GT5P (~2 GB):** quick load, rich shader complexity, fast iteration → ideal.
- **GT5 (`BCUS98114`, disc, 19.4 GB):** quarantined — kernel panics.
- **GT6 (`NPEA00502`, 15 GB):** long load + massive SPU cache that won't optimize.

Spec III preserves the fast-iterate property (2.0 GB, quick load), so it's a valid
drop-in A/B partner — the experiment doesn't sacrifice iteration speed.

---

## 5. NOTES / RISKS
- **Region (PAL/EU vs NTSC/US)** is bundled into "binary" — can't separate without
  a US v3.00 (doesn't exist as a separate SKU here). Report findings as
  "Spec III EU v3.00" not "v3.00 in isolation."
- **Two vaults, two configs, two ledgers already exist** — no setup needed; just
  normalize the config + keep playing. The factor is "free" to study.
- Don't re-blame crashes on the binary while the config + saturation differ
  ([feedback_hardware_blame_tendency] applies to spec-blame too).
- If Spec III becomes the daily driver, its 480p tune may simply be the better
  *practical* config — log that as a tuning result distinct from the binary verdict.

## RELATED
- [project_race_baseline_status] — saturation-dependent stability (confound #2).
- [project_stage3_crash_taxonomy] — Adreno vs Silent vs VkLost crash classes.
- [project_gt5p_loadingbar_crash] — the NPUA80075 DRM-spawn crash history.
- [project_io_sensitized_tuning] — config-equivalence-≠-behavior caution.
