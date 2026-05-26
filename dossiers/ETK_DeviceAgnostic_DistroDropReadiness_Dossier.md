# ETK DEVICE-AGNOSTIC / DISTRO-DROP READINESS DOSSIER
**Subject:** Iterating ETK from a single-device (SM8250) rig toward a profile-driven, ecosystem-portable kit, sequenced against the Rocknix stable-release cadence so a distro drop is a *graduation*, not a scramble.
**Audience:** Claude Code (deep-dev implementer)
**Parent docs:** `RocknixNightlyMigrationDossier.md`, `ADDENDUM_install_sh_tiered_backup.md`, `AI_MANIFEST.md`
**Provenance:** ETK prototyped by Gemini + operator; professionalized in Claude; deep dev in Claude Code.
**Status:** Spec + locked scope. Core architecture (§C–§F) is ready to implement now, on the nightly line, as an additive, behavior-preserving refactor. §J phases it. §K gates it. §H is the timing thesis; do not block §C–§F on it.

---

## §0. THESIS (READ FIRST)

The single biggest adoption barrier in `README.md` is the line "Requires the exact Rocknix Nightly specified above. This does not work on the official release." That barrier is **about to become removable**, not because of anything we do, but because of how Rocknix ships:

1. **The device family is already in the official tent.** The Retroid Pocket Flip 2 / SM8250 (SD865) family left beta and went *stable* roughly a year ago, and RPCS3 is a supported emulator on SD865-class hardware in the stable distribution. We are not waiting for the *hardware* to be blessed — it already is.
2. **Stable is a hardened nightly snapshot, cut on a real cadence** (historically ~every four months). The spring-2026 nightly changes ETK is currently pinned to and chasing — `inputplumber: use target ds5` (20260520), the drm/msm dealloc fix (20260522), the SM8250 audio churn, the ES bump (20260524) — are exactly the raw material the *next* stable will be snapshotted from.
3. **Therefore the work to do *now* is the part we control:** dissolve ETK's SM8250 hardcodings into a device-profile layer so that the day a stable drops containing the DS5 generation, graduation is a three-step checklist (§H), and the kit can immediately widen to the rest of the RPCS3-capable Snapdragon/Adreno set.

This refactor is also the precondition for everything downstream: you cannot sell a Pro tier (or claim "the whole ecosystem") on a kit that requires a non-official nightly and a sacrificial card. Profile-portability + stable-targeting is the unlock.

**The good news up front:** the *storage* layer is already agnostic-ready. The vault is keyed `vault/$CHIPSET/<ID>/shaders` and `install.sh` already fingerprints the Mesa rebuild. The work is concentrated in the **runtime hardware-control layer** (thermal / cpufreq / GPU) and a short list of **driver-family assumptions** (crash signatures, the Vulkan adapter pin). The break surface is narrow and enumerable — see §D.

---

## §1. SCOPE & NON-GOALS (LOCKED)

"Device-agnostic" here does **not** mean "runs on all of Rocknix." It means **profile-driven within the set of devices that can actually run the ETK mission**, plus a clean seam for future expansion. Lock this so the refactor doesn't sprawl.

- **In scope (the reachable universe): RPCS3-capable Snapdragon / Adreno / Mesa-Turnip devices.** This is a coherent, real family that already shares ETK's entire premise — `kgsl` GPU control, Turnip shader cache, Adreno/Qualcomm DPU crash signatures. The Flip 2 and Pocket 5 are internals-identical to the point that people swap SD cards between them; the rest of the SD865 cohort (RP5, RP Mini class) is nearly free; SM8550 (Odin 2 family) is the first genuinely-new-SoC target.
- **Out of scope (PS3 mission): Mali / non-Adreno devices** (RK3588, H700, RK3326, S922X, etc.). Even though Panfrost/Panthor are *also* Mesa drivers and the cache-symlink trick would technically transfer, **there is no RPCS3 target on these SoCs**, so the entire ETK mission collapses. The shader-vault concept is a Turnip artifact. Do not spend effort making thermal/GPU control work on Mali for this kit.
- **Non-goal: a universal abstraction over every kernel sysfs layout.** We model the *profiles we will actually ship*, with a documented "how to author a new profile" path. We do not build a generic auto-discovering hardware-abstraction layer. That is gold-plating.
- **Non-goal: changing the per-game tuning model.** Game-agnostic identity (`AI_MANIFEST` Immutable Law #4) already works and is untouched. This dossier is *device*-agnostic, orthogonal to *game*-agnostic.

### Device-support tiering (target state; values to VERIFY on-rig per §E/§G)

| SoC | Example devices | GPU / Mesa driver | RPCS3? | ETK tier | Notes |
|---|---|---|---|---|---|
| SM8250 (SD865) | RP Flip 2, RP5, RP Mini | Adreno 650 / **Turnip** | Yes | **T1 — reference** | Current rig. Becomes the canonical profile; behavior must stay byte-identical post-refactor (§K). |
| SM8250 (other) | other SD865 RP-class | Adreno 650 / Turnip | Yes | **T2 — near-free** | Same GPU family, same crash sigs, likely same cpufreq policies. Proves the profile seam with ~zero new hardware code (§J Phase 3). |
| SM8550 (SD8g2) | AYN Odin 2 / Mini / Portal | Adreno 740 / Turnip | Yes | **T3 — first real port** | New thermal map + thresholds, possibly different policy IDs, watch nightly SM8550 suspend churn. First profile that exercises §G calibration for real. |
| SM8650 | (future SD8g3 handhelds) | Adreno 750 / Turnip | Yes | **T4 — future** | Same family pattern; enable when a device is in hand. |
| RK3588 / Mali | Gameforce Ace, etc. | Mali G610 / Panfrost-Panthor | No (no RPCS3) | **Out of scope** | Mesa cache trick transfers in principle; mission does not. Document as explicitly unsupported. |

---

## §2. THE PROFILE MODEL (CORE ARCHITECTURE — LOCKED)

Introduce a **device-profile data layer** that supplies every hardware-specific *value* the runtime scripts need. The scripts stop hardcoding SM8250 facts and instead read profile variables.

### 2.1 Honoring Immutable Law #2

`AI_MANIFEST` Immutable Law #2: *"`scripts/env.sh` is the ONLY file allowed to define environment variables. All other scripts MUST source it."* The profile layer **must not** break this. The contract:

- Profiles live at `scripts/profiles/<soc>.sh` and contain **plain assignments** (`THERMAL_ZONE_GOVERNING=14`), **not** `export`s, and **no logic** beyond simple value/function definition.
- `scripts/env.sh` is the **only** file that sources a profile, and `env.sh` is the only file that `export`s the canonical variable names. The profile is *data env.sh ingests*; env.sh remains the single definer/orchestrator. State this in a comment block at the env.sh source site so a future model doesn't "promote" exports into the profile.
- Downstream scripts (`thermal_d.sh`, `commander.sh`, `etk_probe.sh`, `probe.sh`) keep sourcing **only** `env.sh`, exactly as today. They never source a profile directly.

### 2.2 The profile contract (variables a profile MUST supply)

A profile is complete iff it exports (via env.sh) all of:

| Variable | SM8250 value | Consumed by |
|---|---|---|
| `PROFILE_SOC` | `SM8250` | sets `CHIPSET`; vault keying; profile self-id |
| `GPU_DRIVER_FAMILY` | `turnip` | crash-sig family select; GPU control branch; vault validity |
| `RPCS3_CAPABLE` | `1` | install-time capability gate (§F.3) |
| `THERMAL_ZONE_GOVERNING` | `14` | `thermal_d.sh`, `commander.sh` core temp read |
| `THERMAL_ZONE_MAP` | `1:cpu0 5:cluster0 6:cluster1 10:cpu7_top 14:cpu7_bot 15:gpu_top 19:mem 24:gpu_bot 25:battery` | `etk_probe.sh` calibration sweep |
| `CPU_POLICY_PRIME` | `7` | `thermal_d.sh`, `etk_probe.sh` |
| `CPU_POLICY_GOLD` | `4` | `thermal_d.sh`, `etk_probe.sh` |
| `CPU_POLICY_SILVER` | `0` | `etk_probe.sh` |
| `CPU_PIT_CAP_KHZ` | `1200000` | `thermal_d.sh` PIT anchor cap |
| `GPU_DEVFREQ_NODE` | `/sys/class/devfreq/3d00000.gpu` | `etk_probe.sh`, `probe.sh` freq read |
| `GPU_GOVERNOR_PATH` | `/sys/class/kgsl/kgsl-3d0/devfreq/governor` | `thermal_d.sh` RACE/PIT governor swap |
| `ALARM_TEMP` | `83` | thermal failsafe (already in env.sh) |
| `RACE_THRESHOLD` | `86` | thermal failsafe |
| `PIT_THRESHOLD` | `65` | thermal intent |
| `GPU_ADAPTER_STRING` | `Turnip Adreno (TM) 650` | template `Adapter:` pin; vault-validity check (§F.2) |
| `FOOT_FONT_SIZE` | `28` | Pitstop launcher DPI (§ display) |
| `MANGOHUD_FONT_SIZE` | `46` | HUD DPI |

> The threshold trio (`ALARM_TEMP`/`RACE_THRESHOLD`/`PIT_THRESHOLD`) already lives in env.sh today — it simply **moves into the profile** so each device carries its own calibrated values. env.sh re-exports them unchanged for the active profile, so existing consumers are untouched.

---

## §3. HARDCODING INVENTORY (THE AUDIT — what becomes profile-driven)

Every SM8250 fact, where it hides, and what it becomes. This is the surface of the refactor; nothing outside this table changes hardware behavior.

| File | Hardcoded today | Becomes |
|---|---|---|
| `scripts/env.sh` | `export CHIPSET="SM8250"` | `CHIPSET="$PROFILE_SOC"` after profile source |
| `scripts/env.sh` | `ALARM_TEMP=83 / PIT_THRESHOLD=65 / RACE_THRESHOLD=86` | sourced from profile, re-exported |
| `scripts/env.sh` | recalibration comment `nightly-20260520` | bump to graduation tag; note "calibrated per profile" |
| `bin/thermal_d.sh` | `thermal_zone14` (×N reads) | `thermal_zone${THERMAL_ZONE_GOVERNING}` |
| `bin/thermal_d.sh` | `policy4`, `policy7` governor + `scaling_max_freq` | `policy${CPU_POLICY_GOLD}`, `policy${CPU_POLICY_PRIME}` |
| `bin/thermal_d.sh` | `1200000` PIT freq caps | `$CPU_PIT_CAP_KHZ` |
| `bin/thermal_d.sh` | `/sys/class/kgsl/kgsl-3d0/devfreq/governor` | `$GPU_GOVERNOR_PATH` (+ family branch, §F.1) |
| `scripts/commander.sh` | `thermal_zone14` | `thermal_zone${THERMAL_ZONE_GOVERNING}` |
| `scripts/etk_probe.sh` | `ZONES="1:cpu0 …"` | `$THERMAL_ZONE_MAP` |
| `scripts/etk_probe.sh` | `/sys/class/devfreq/3d00000.gpu/cur_freq` | `$GPU_DEVFREQ_NODE/cur_freq` |
| `scripts/etk_probe.sh` | `policy0/4/7` cur_freq | `$CPU_POLICY_SILVER/GOLD/PRIME` |
| `scripts/probe.sh` | `/sys/class/devfreq/3d00000.gpu` check | `$GPU_DEVFREQ_NODE` |
| `scripts/probe.sh` | dmesg grep `adreno\|turnip\|msm_dpu\|…` | family-keyed pattern set (§F.4) |
| `config/crash_signatures.json` | `a6xx_irq`, `msm_dpu`, `drm:recover_worker` | family file `crash_signatures.$GPU_DRIVER_FAMILY.json` (§F.4) |
| `bin/session_postmortem.sh` | inline-mirrored Adreno/DPU patterns | family-keyed (§F.4) — **Tier-3 phase**, not Tier-1/2 |
| `config/etk_template.yml` | `Adapter: Turnip Adreno (TM) 650` | installer patches line from `$GPU_ADAPTER_STRING` (§F.2) |
| `config/etk_pitstop.sh` | `font="monospace:size=28"` | `$FOOT_FONT_SIZE` (env-injected) |
| `config/MangoHud.conf` | `font_size=46` | profile-templated at deploy (§ display) |

**Already abstracted — leave alone:** the gamepad layer (`PAD_HINTS`/`PAD_EXCLUDE` in `etk_pitstop.py`/`input_d.py`) is already pad-model-agnostic and InputPlumber presents a consistent virtual target across devices; the vault path is already `$CHIPSET`-keyed; battery read already globs `/sys/class/power_supply/*/capacity`; ID resolution is game-agnostic per Law #4. Do **not** refactor these under the banner of device-agnosticism — keep the diff narrow.

---

## §4. DETECTION & PROFILE SELECTION (env.sh)

### 4.1 [VERIFY] Discover the canonical device/SoC identifier first

Do **not** guess the identifier source. Probe on the rig and on at least one other Rocknix image before wiring selection. Candidate sources, in preference order:

```sh
# 1. Device-tree compatible (most reliable; SoC compatible like "qcom,sm8250"):
tr '\0' '\n' < /sys/firmware/devicetree/base/compatible
cat /sys/firmware/devicetree/base/model 2>/dev/null

# 2. Rocknix's own device/SoC tag (CONFIRM the exact key/file on-rig):
cat /etc/os-release 2>/dev/null
#    and whatever Rocknix exposes (get_setting / system.conf / an env var) —
#    capture the literal source that yields "SM8250"/"RK3588"/etc.

# 3. Cross-check: the platform name in the running image (release/nightly
#    assets are named per SoC: SM8250, SM8550, RK3588, H700, ...).
```

Lock on whichever yields a stable, unambiguous SoC token. Map token → profile filename.

### 4.2 Selection logic (env.sh, near top, before path/threshold exports)

```sh
# --- DEVICE PROFILE (Immutable Law #2: env.sh remains the sole exporter;
# the profile supplies device VALUES only — no exports, no logic.) ---
ETK_PROFILE_DIR="$ETK_ROOT/scripts/profiles"   # NB: ETK_ROOT defined below; see ordering note
ETK_PROFILE_OVERRIDE="${ETK_PROFILE_OVERRIDE:-}"   # operator escape hatch

detect_soc() {
    # POSIX/BusyBox-safe. Returns a token like SM8250 or empty.
    soc=$(tr '\0' '\n' < /sys/firmware/devicetree/base/compatible 2>/dev/null \
          | grep -oiE 'sm8[0-9]{3}|rk3[0-9]{3}|s922x|h700' | head -n 1 \
          | tr 'a-z' 'A-Z')
    echo "$soc"
}

if [ -n "$ETK_PROFILE_OVERRIDE" ]; then
    PROFILE_FILE="$ETK_PROFILE_DIR/$ETK_PROFILE_OVERRIDE.sh"
else
    DETECTED_SOC=$(detect_soc)
    PROFILE_FILE="$ETK_PROFILE_DIR/${DETECTED_SOC:-SM8250}.sh"
fi

# Fallback chain: detected -> SM8250 reference. NEVER hard-fail env.sh on a
# missing profile (env.sh is sourced by everything; a hard exit bricks the
# whole kit). Warn to stderr, fall back to the reference profile.
if [ ! -f "$PROFILE_FILE" ]; then
    echo "ETK env: no profile for '${DETECTED_SOC:-?}', using SM8250 reference" >&2
    PROFILE_FILE="$ETK_PROFILE_DIR/SM8250.sh"
fi
. "$PROFILE_FILE"

# env.sh now EXPORTS the canonical names from the profile-supplied values:
export CHIPSET="${PROFILE_SOC:-SM8250}"
export THERMAL_ZONE_GOVERNING CPU_POLICY_PRIME CPU_POLICY_GOLD CPU_POLICY_SILVER
export CPU_PIT_CAP_KHZ GPU_DEVFREQ_NODE GPU_GOVERNOR_PATH GPU_DRIVER_FAMILY
export GPU_ADAPTER_STRING RPCS3_CAPABLE THERMAL_ZONE_MAP
export ALARM_TEMP RACE_THRESHOLD PIT_THRESHOLD FOOT_FONT_SIZE MANGOHUD_FONT_SIZE
```

> **Ordering note for the implementer:** `ETK_ROOT` is defined later in the current env.sh than where the profile must source. Either hoist the `ETK_ROOT` definition above the profile block, or compute `ETK_PROFILE_DIR` from the same literal. Do **not** scatter a second `ETK_ROOT` definition (Law #2). Resolve cleanly; this is the one structural reshuffle env.sh needs.

### 4.3 Override + safety

- `ETK_PROFILE_OVERRIDE=SM8550 ./install.sh` lets the operator force a profile (testing, or a device whose detection token is unexpected). Document in `README.md` and `env.sh`.
- The SM8250 fallback guarantees that on any unrecognized-but-similar device, ETK degrades to the reference profile rather than running with empty hardware paths (which would silently disable thermal protection — a safety regression).

---

## §5. GPU DRIVER-FAMILY ABSTRACTION

### 5.1 Governor control branch (`thermal_d.sh`)

Adreno exposes its governor under `kgsl` (`$GPU_GOVERNOR_PATH`); the value written is `performance`/`powersave`. A future non-Adreno target would use a different node and possibly different governor tokens. Keep it minimal and family-gated:

```sh
gpu_set_governor() {   # $1 = performance|powersave
    case "$GPU_DRIVER_FAMILY" in
        turnip) echo "$1" > "$GPU_GOVERNOR_PATH" 2>/dev/null ;;
        *)      echo "$1" > "$GPU_GOVERNOR_PATH" 2>/dev/null ;;  # extend per family
    esac
}
```

For Tier-1/2 (all Turnip) this is a pure indirection with no behavior change. The branch exists so adding a family later is one case arm, not a script rewrite.

### 5.2 Vault adapter pin & validity

- `config/etk_template.yml` keeps the SM8250 adapter string as its on-disk default. The TOOLS installer's `_deploy_template_config()` (in `etk_pitstop.py`) gains a profile-aware line-patch: after copying the template, rewrite the `Adapter:` line to `$GPU_ADAPTER_STRING` (read from env). Atomic tmp+replace, same idiom as `_enable_mangohud`.
- The migration dossier's §6 "confirm the adapter still matches the template pin" check generalizes: compare the live RPCS3.log adapter against `$GPU_ADAPTER_STRING`, not a literal. One sed-free `grep` against the env var.
- Vault remains `$CHIPSET`-keyed, so cross-device vaults never collide and a Turnip-650 vault is never pushed onto a Turnip-740 rig as if interchangeable. (Whether 865-vs-8g2 Turnip caches are cross-compatible is a separate empirical question — **do not assume**; keep them chipset-partitioned, which is already the behavior.)

### 5.3 RPCS3 capability gate (`install.sh`)

Add an early guard: if `RPCS3_CAPABLE` != `1` for the detected profile, `install.sh` prints a clear "this device is outside ETK's PS3 mission scope (no RPCS3 target); aborting" and exits non-zero **before** provisioning. This is the structural enforcement of §1's locked scope — it stops someone flashing ETK onto a Mali handheld and filing confused bug reports.

### 5.4 Crash-signature families [Tier-3 phase, deferred until a non-Adreno-or-new-DPU target exists]

`a6xx_irq` / `msm_dpu` / `drm:recover_worker` are Qualcomm-Adreno kernel strings. **All Tier-1/2 SD865 devices share them**, and SM8550 still uses Adreno/`a6xx`-lineage + Qualcomm DPU, so the existing `crash_signatures.json` likely needs only verification, not replacement, through Tier-3. Therefore:

- **Now:** rename conceptually to family `qcom-adreno`; the active file is selected by `$GPU_DRIVER_FAMILY` but for every shipped tier it resolves to the current file. No content change.
- **Later (only if a genuinely different DPU/GPU family is ever added):** split into `config/crash_signatures.qcom-adreno.json` + a sibling, and family-key the inline mirror in `session_postmortem.sh`. Keep the JSON/inline-mirror sync discipline the file already documents. Until then this is **DEFERRED** — do not split prematurely.

---

## §6. THERMAL RE-CALIBRATION AS A PER-PROFILE STEP

Thermal zones, cpufreq policies, and safe thresholds are device-tree-defined and **not portable**. `etk_probe.sh` already exists for exactly this — it becomes the profile-authoring instrument.

**Authoring a new profile (the documented procedure):**

1. Flash the target device; install ETK with the SM8250 fallback profile (thermal protection runs on reference thresholds — conservative, safe enough to probe).
2. Run a calibration sweep under real load:
   ```sh
   $ETK_ROOT/scripts/etk_probe.sh start <soc>_calibrate
   #   ... heavy harvest session ...
   $ETK_ROOT/scripts/etk_probe.sh stop
   $ETK_ROOT/scripts/etk_probe.sh report
   ```
3. From the peak-zone report, identify the governing core/GPU hot-spot zone → `THERMAL_ZONE_GOVERNING`; confirm the full `THERMAL_ZONE_MAP`; read cluster policy IDs from `/sys/devices/system/cpu/cpufreq/policy*`; read the GPU devfreq node name from `/sys/class/devfreq/`.
4. Set `ALARM_TEMP`/`RACE_THRESHOLD`/`PIT_THRESHOLD` from the observed idle/load curve.
5. Write `scripts/profiles/<soc>.sh`, re-flash, re-probe to confirm the failsafe trips correctly.

> **Bootstrapping caveat:** before a profile exists, `THERMAL_ZONE_MAP` and policy IDs in the fallback are SM8250's, so the *probe itself* on a new device reads wrong zone labels. Make `etk_probe.sh` accept a one-shot raw mode (`etk_probe.sh discover`) that enumerates **all** `/sys/class/thermal/thermal_zone*/type` and `cpufreq/policy*` without assuming the map — so calibration on a virgin device is self-bootstrapping. Small addition, high leverage.

---

## §7. DISPLAY / DPI

Lowest-risk band; do last. The Flip 2 panel drives `foot … size=28` and MangoHud `font_size=46`. Other panels (resolution/PPI) want different values.

- `config/etk_pitstop.sh`: replace the literal `size=28` with `${FOOT_FONT_SIZE:-28}`, injected via the launcher's environment (the launcher already sources env.sh). Preserve the GEMINI IMMUTABLE RULE about `-o font=` vs `-f` verbatim.
- `config/MangoHud.conf`: `font_size` becomes a deploy-time templated value. `install.sh` Step 3 already pushes `MangoHud.conf`; add a tiny post-push `sed -i "s/^font_size=.*/font_size=$MANGOHUD_FONT_SIZE/"` on the rig copy (BusyBox `sed -i` is fine). Keep `font_file` (LiberationMono) as-is — it's OS-image-provided and reflash-safe on all Rocknix targets.
- Respect the HUD FORMAT STRICT LOCK in `AI_MANIFEST` — font *size* is tunable per device; the dense DDU *string layout* is not. Do not widen spacing or add glyphs.

---

## §8. GRADUATION TRIGGER & RELEASE PREDICTION

### 8.1 Cadence basis (from Rocknix release history)

- Stable releases land on a roughly **four-month cadence**; the SD865/Flip-2 cohort has been **stable since ~mid-2025**, with RPCS3 included. (Source basis: Rocknix release history + Retro Handhelds reporting on the Flip 2 / Odin 2 stable wave.)
- The nightly line is where the spring-2026 changes ETK chases currently live (e.g. nightly `20260525` is published). Stable is a snapshot of a stabilized nightly.

### 8.2 The trigger is a *content* condition, not a date

Do not wait on, or invent, a date. **Graduate when the first stable image whose changelog contains the InputPlumber DS5 generation appears** — concretely, the stable that includes `inputplumber: use target ds5` / `config: reset configs for DS5 changes` (nightly 20260520 lineage). Detection without trusting marketing copy: flash the candidate stable to the dev card and run the existing `bin/gamepad_probe.py` — if the virtual target enumerates as DualSense/DS5, the generation is in. (This reuses tooling ETK already has; no new code.)

Given the ~4-month cadence and that the relevant nightly work is already landing as of late May 2026, the realistic graduation window is **the next stable cut after the DS5/drm-msm/ES-bump generation settles** — plan for it as a "when, not if" within the coming cadence cycle, and have §C–§F merged before it arrives so graduation is mechanical.

### 8.3 Branch & pin strategy

- Keep `main` as the nightly-dev line. The profile refactor lands **on `main` now** — it is additive and improves the nightly build too (it doesn't depend on stable).
- At graduation, cut `stable/<rocknix-tag>`: pin/verify the SM8250 profile values against the stable image (re-run §6 calibration — kernel/DT could shift zones, cf. the standing `sm8550/sm8650: fix thermal zone names` reminder), and flip the README requirement from "the exact nightly" to **"Rocknix stable ≥ `<tag>` *or* nightly ≥ 20260520."**
- Update version strings (README / AI_MANIFEST / env.sh recal comment) to the stable tag, per the migration dossier's standing "reconcile the version strings" item.

### 8.4 Sequencing payoff

Doing §C–§F on nightly *before* the drop means graduation day is: (1) flash stable, (2) `gamepad_probe` confirms DS5 generation, (3) re-calibrate SM8250 profile + flip README pin. The same profile machinery then lets a Pocket 5 / Odin 2 owner author a profile and join — which is precisely the "whole ecosystem" reach, scoped honestly to the RPCS3-capable Adreno set, and the footing the Pro tier needs.

---

## §9. DO-NOT-TOUCH / DECISIONS LOCKED

- **Immutable Law #2 (env.sh sole definer) is preserved**, not bent: profiles are sourced **only** by env.sh and contain values, not exports. Reaffirm in a header comment so no future model "tidies" exports into the profile.
- **Immutable Laws #1/#3/#4/#5 untouched:** SHM sanctity, symlink sanctity, agnostic identity block (do **not** modify the marked block in env.sh), BusyBox/no-GNUisms throughout the new code. `detect_soc`, the probe `discover` mode, and all profile reads must be POSIX/BusyBox-safe.
- **Keep the diff additive.** Do not refactor the Tier-A/B backup, the Sentry state machine, the gamepad layer, or the telemetry pipeline while doing device-agnosticism. Profiles are a new seam, not a rewrite.
- **Behavior on the Flip 2 must be byte-identical** post-refactor (§K). The SM8250 profile is just the current literals relocated; the reference device is the regression oracle.
- **Capability gate is mandatory**, not optional: `RPCS3_CAPABLE=0` → `install.sh` aborts. Out-of-scope devices fail fast and legibly.
- **Do not assume cross-Turnip-version vault interchangeability.** Keep vaults `$CHIPSET`-partitioned (already the case).

---

## §10. PHASED IMPLEMENTATION PLAN

Ordered so each phase is independently shippable and the reference device never regresses.

1. **Phase 1 — Profile scaffold + SM8250 extraction (behavior-identical).** Create `scripts/profiles/SM8250.sh` from the current literals. Add the env.sh source/select/export block (§E) with override + fallback. Rewire `thermal_d.sh`, `commander.sh`, `etk_probe.sh`, `probe.sh` to read profile vars. **Net hardware behavior change on Flip 2: zero.** This is the load-bearing phase; gate it hard (§K.1).
2. **Phase 2 — Detection.** Implement + VERIFY `detect_soc` (§E.1 probe first). Confirm it returns `SM8250` on the rig and a sane token on one other image. Fallback proven.
3. **Phase 3 — Second SD865 profile (prove the seam, near-free).** Author a profile for a second SD865 device (same family → likely identical thermal/policy/GPU, same crash sigs). Validates the architecture with minimal new hardware code. If it's truly identical, this phase is mostly a detection-token alias + a re-calibration confirmation.
4. **Phase 4 — GPU family indirection + adapter pin + capability gate (§F).** `gpu_set_governor` branch, installer adapter-line patch, generalized adapter-validity check, `RPCS3_CAPABLE` gate in install.sh.
5. **Phase 5 — Calibration tooling (§G).** Add `etk_probe.sh discover` (assumption-free enumeration) and document the profile-authoring procedure in `README.md` / a `dossiers/` how-to.
6. **Phase 6 — Display/DPI (§7).** `FOOT_FONT_SIZE` injection + MangoHud font_size templating.
7. **Phase 7 — SM8550 profile (first real port).** Exercise §G end-to-end on Odin-class hardware. Watch nightly SM8550 suspend churn. This is where calibration earns its keep.
8. **Graduation (§H), when triggered.** Stable pin, re-calibrate SM8250 against stable, README flip, version-string reconcile.

Crash-sig family split (§F.4) stays **DEFERRED** until a target actually needs it.

---

## §11. ACCEPTANCE CRITERIA & TEST PLAN

1. **Flip 2 behavior-identical (the prime gate).** With the SM8250 profile active, a default `install.sh` and a full game session produce thermal mode transitions, governor writes, GPU governor swaps, HUD output, `etk_probe.sh report`, and crash classification **identical** to pre-refactor. Diff a captured `thermal_d` mode-transition log and an `etk_probe report` before/after.
2. **env.sh remains sole exporter.** No `export` appears in any `scripts/profiles/*.sh`. `grep -rn 'export ' scripts/profiles/` is empty.
3. **Profile override works.** `ETK_PROFILE_OVERRIDE=SM8250` forces the profile regardless of detection; an unknown override falls back to SM8250 with a stderr warning, env.sh still sources cleanly (no hard exit).
4. **Detection correctness.** `detect_soc` returns `SM8250` on the rig; returns the correct token or empty (never garbage) on a second image.
5. **Capability gate.** A profile with `RPCS3_CAPABLE=0` causes `install.sh` to abort before provisioning, with a clear scope message and non-zero exit.
6. **No GNUisms.** New code passes a BusyBox shell; no `--long-opts`, `grep -P`, `find -printf`, `stat --format` without fallback.
7. **No new SD writes on default runs.** Profile sourcing and detection are reads only; Tier-A/B treadwear discipline intact.
8. **Adapter pin portability.** On a non-SM8250 profile (mocked), the installer writes `$GPU_ADAPTER_STRING` into the deployed template's `Adapter:` line; on SM8250 the line is unchanged.
9. **Graduation dry-run.** `gamepad_probe.py` on a DS5-generation image enumerates the DualSense virtual target; the README pin condition logic recognizes it.

### Test sequence
- **T1 (regression):** before-refactor capture of Flip 2 thermal/probe/HUD behavior → refactor → after-capture → diff = empty.
- **T2 (seam):** author a synthetic second SD865 profile, `ETK_PROFILE_OVERRIDE` it on the Flip 2, confirm only profile-sourced values change and nothing crashes.
- **T3 (fallback):** rename the SM8250 profile, confirm env.sh warns + falls back without bricking; restore.
- **T4 (gate):** synthetic `RPCS3_CAPABLE=0` profile → install aborts pre-provision.
- **T5 (calibrate):** `etk_probe.sh discover` on the Flip 2 enumerates zones/policies with no SM8250 assumptions baked in.

---

## §12. ONE-LINE SUMMARY FOR THE COMMIT MESSAGE

> Introduce device-profile layer (`scripts/profiles/<soc>.sh`, sourced only by env.sh per Law #2) and relocate all SM8250 hardware literals — thermal zone, cpufreq policies, GPU devfreq/governor, thresholds, adapter pin, DPI — into the SM8250 reference profile. Add SoC detection + override + safe SM8250 fallback, an `RPCS3_CAPABLE` install gate, GPU-family governor indirection, and `etk_probe.sh discover` for assumption-free calibration. Behavior on the Flip 2 is byte-identical (regression-gated). This is additive, lands on the nightly line now, and reduces graduation-to-stable to: flash stable, `gamepad_probe` confirms the DS5 generation, recalibrate SM8250 + flip the README pin. Crash-sig family split deferred until a non-Adreno/new-DPU target exists.