# Rocknix Nightly Migration Pre-Check — nightly-20260614

**Status:** 🟢 **LOW-RISK to adopt — provisional clearance, certification-gated on §5.**
The published changelog reads "SM8250-heavy," but the entire SM8250 burst is **Mangmi
Pocket Max device bring-up**. Every functional change lands in *Mangmi-specific* files;
**no commit in the delta touches `sm8250-retroidpocket-flip2.dts` or
`sm8250-retroidpocket-common.dtsi`.** The big-ticket items the changelog advertises
(Charger driver, enable rumble, merge pm driver, panel unify regulators) are **already
running on the rig's 20260612 build** and have been stable since Jun 12. The true new
delta for the Flip 2 is small and benign.

**Candidate:** `nightly-20260614` (Pre-release).
**Rig live:** `nightly-20260612`, build `a3388da2bde9` (`next`, kernel 7.0.11, Turnip Mesa 26.1.2).
**Pin reference:** 20260610 (certified 2026-06-11; see `SM8250.sh`). ⚠️ No baseline JSON is
currently banked — `os_profiles/` was swept in the 20260612 epoch reset. Re-bank on certify (§5.7).
**Tier-1 target:** SM8250 / Retroid Pocket Flip 2 (Adreno 650 / Turnip).
**Tooling:** `tools/etk_drift.py` (probed live), `bin/gamepad_probe.py`.
**Gates:** ETK v0.3.0 (Private Paddock).

---

## §1. The "since last nightly" trap — compute the TRUE delta

The pasted changelog compares 20260614 against an *older* published nightly, so it
**double-counts commits already on the rig.** The rig was built from merge `a3388da2`
(2026-06-11T21:43Z); only commits *after* that boundary are genuinely new. Reconstructed
from `next` (via GitHub API):

| Commit | Date (UTC) | On rig (20260612)? | Touches Flip 2? |
|--------|-----------|--------------------|-----------------|
| `c3643b9` gamescope: bump (wlroots submodule) | 06-13 | ❌ NEW | compositor (shared) |
| `8fcba31` rocknix-fake-suspend: unfreeze before shutdown | 06-13 | ❌ NEW | suspend path (shared) |
| `d43279a` **sm8250: gamepad fixes** | 06-13 | ❌ NEW | **No — Mangmi only** |
| `b59f018` sm8750: panel init in gamescope | 06-12 | ❌ NEW | No (sm8750) |
| `d3193af` **Mangmi Pocket Max** (device add) | 06-12 | ❌ NEW | **No — new device** |
| `fea700c` sm8550/ayaneo: DP audio | 06-12 | ❌ NEW | No (sm8550) |
| `6419643` abl: bump to 1.1.1 | 06-12 | ❌ NEW | bootloader pkg (all qcom) |
| `5418491` **sm8250: fixed regulator rumble** | 06-12 | ❌ NEW | **No — Mangmi only** |
| `4417483` rocknixinfo: prevent SN hang | 06-12 | ❌ NEW | info tool (shared) |
| — — — rig build boundary `a3388da2` — — — | 06-11 | | |
| `9ffcf10` **sm8250: Charger driver** | 06-10 | ✅ already | **No — Mangmi HL7139** |
| `7601e36` **sm8250: enable rumble** | 06-10 | ✅ already | (Flip 2 rumble already live) |
| `32c12dc` **panel: unify regulators** | 06-09 | ✅ already | **No — Mangmi Chipone panel** |
| `5868d2c` **sm8250: merge pm driver** | 06-09 | ✅ already | **No — Mangmi panel/pm** |
| `1c02c75` networkmanager: ControlPortOverNL80211→true | 06-09 | ✅ already | shared (WiFi — see §4) |

**Net:** four of the five scary-sounding SM8250 headlines are already on the rig and field-stable.

## §2. The "sm8250:" prefix is the platform tree, not our device

The SM8250 family now hosts seven device trees:
`flip2`, `rp5`, `rpmini`, `rpminiv2`, `ayn-thorlite`, **`mangmi-pocket-max`**, plus the shared
`-common.dtsi`. `sunshineinabox` is bringing up the **Mangmi Pocket Max**, and every
functional change in this burst lands in that device's files:

- `d43279a` gamepad fixes → `sm8250-mangmi-pocket-max.dts`, `0060_Mangmi-Pocket-Max-SPI-joypad.patch`, `pocket_max_mcu.yaml`
- `5418491` regulator rumble → `sm8250-mangmi-pocket-max.dts` **only**
- `9ffcf10` charger → `0063_Mangmi-Pocket-Max-HL7139-charge-pump.patch` (+412)
- `32c12dc` / `5868d2c` panel/pm → the **Chipone ICNA35XX / ICAN3512** panel patches (Mangmi's panel)

**Verified:** grepping all three sm8250-touching delta commits for `flip2`/`common.dtsi` →
`NONE`. This is the *inverse* of the usual collateral-damage pattern in
[[project_rocknix_nightly_regressions]] — the changes are nominally "for our SoC" but are
cleanly isolated to a sibling device's files.

**The only shared SM8250 files touched anywhere in the burst:**
1. `linux/linux.aarch64.conf` (shared kernel defconfig) — charger `+4/-2` (enables HL7139), merge-pm `-1`. **Already on rig; charging works.** Additive driver symbols for an IC the Flip 2 doesn't have.
2. `gamecontrollerdb.txt` `+1` (one additive GUID — Mangmi's pad) and `gamepadcalibration/package.mk` (`+5/-2` tool bump) — in the NEW `d43279a`. Additive/benign.

## §3. Live rig state vs. the surfaces this changelog names

Probed `root@sm8250.local` on 20260612. `etk_drift.py --check` → **no structural drift, safe to adopt.**

| ETK memory / surface | Live state on 20260612 | Verdict for 20260614 |
|----------------------|------------------------|----------------------|
| **Battery gauge desync** ([[project_battery_gauge_android_recal]]) | `battery` node = voltage/capacity only (4.07 V, 73%); **no `charge_full`/`cycle_count`/`health`**. `pm8150b-charger` present (Flip 2's charger). | **Unaffected.** The new charger driver is Mangmi's **HL7139 charge-pump**, not the Flip 2's pm8150b. Gauge fields neither added nor removed. Android-recal workaround still stands. |
| **Rumble** | `Retroid Pocket Gamepad` and `spmi_haptics` both expose `FF=107030000`. | **Already enabled & working.** "enable rumble"/"fixed regulator rumble" are Mangmi-scoped; Flip 2 rumble pre-dates them. New ETK opportunity, not a risk (§6). |
| **Gamepad codes / node drift** ([[project_gamepad_button_swap]]) | DualSense at `js1/event9` (matches the historic event8→event9 drift); `find_gamepad()` matches by name. | **Unaffected.** `d43279a` touches the Pocket-Max capability map, not the DualSense/Flip 2 mapping. `gamecontrollerdb` +1 is additive. |
| **DSI cold-boot black screen** ([[project_dsi_coldboot_blackscreen]]) | `card0-DSI-1` = **connected**. | **Unaffected.** "panel: unify regulators" edits the **Chipone (Mangmi) panel patch**, not the Flip 2 DSI panel. EFI grub.cfg fix is orthogonal (re-verify after any update per [[project_rig_boot_sequence]]). |
| **Thermal zone14 anchoring** ([[project_thermal_recalibration]]) | zone map intact, `--check` clean. | **Unaffected.** "merge pm driver" is Mangmi panel/pm consolidation, not SM8250 platform thermal/cpufreq. |
| **Fastboot revert / ABL** ([[project_installtointernal_recovery]]) | ABL is the revert path (Qualcomm firmware menu). | **abl→1.1.1** is a package version+hash bump (`+1/-1`). Re-confirm `fastboot` still enumerates before relying on it for a revert. Low risk. |

## §4. WiFi-jank suspect status (no change)

`ControlPortOverNL80211→true` (`1c02c75`, the prime WiFi-jank suspect flagged in
[[project_rocknix_nightly_regressions]]) is **already on the rig** (Jun 9, below the build
boundary). 20260614 **does not revert it** and adds nothing new to the WiFi path. If WiFi
jank is real it's already present on 20260612 — adopting 20260614 neither helps nor hurts.
Not a new abort trigger; track separately.

## §5. On-rig certification protocol

In-place updater only (never clean reflash). Rig on 20260612; no banked baseline.

```sh
# 0. PRE: bank the CURRENT good state first (none exists — epoch-swept)
python3 tools/etk_drift.py --save-baseline --pin     # banks 20260612 as rollback ref
python3 bin/gamepad_probe.py /dev/input/event9       # DualSense buttons (now event9)
#    Record: confirm/back=304/305, R3=318, L3=317, L1=310, SELECT=314, DPAD=16/17.

# --- flash nightly-20260614 via Rocknix in-place updater, reboot ---

# 1. STRUCTURAL: profile assumptions
python3 tools/etk_drift.py --check
#    EXPECT clean. The GPU_ADAPTER_STRING "gpu model unreadable" INFO is KNOWN
#    (kgsl sysfs not exposed on 7.0.11) — confirm Adreno 650 via RPCS3.log, not sysfs.

# 2. TEMPORAL DIFF vs the 20260612 baseline just banked
python3 tools/etk_drift.py --diff pin
#    EXPECT thermal/cpu/gpu clean. Input node-name churn = benign (name-matched).

# 3. GAMEPAD (drift-tool blind spot — the one NEW Flip-2-shared input change)
python3 bin/gamepad_probe.py /dev/input/event9
#    Confirm codes UNCHANGED vs step 0. d43279a should not move them, but it bumped
#    gamecontrollerdb + gamepadcalibration — verify empirically.

# 4. HEADLESS GATE (load-bearing)
#    a) cold boot: panel lights (DSI-1 connected), R3 fires recovery ~2s.
#    b) suspend -> resume -> R3 fires.  (fake-suspend "unfreeze before shutdown"
#       changed the suspend/shutdown path — this gate is non-negotiable.)
#    c) launch a PS3 title: IGNITION (active_id != IDLE, no VAULT:ERROR),
#       RPCS3 binds "Turnip Adreno (TM) 650", thermal governs.

# 5. RUMBLE sanity (now that FF is live)
cat /sys/class/input/event3/device/capabilities/ff   # Retroid Pocket Gamepad still FF-capable
#    EXPECT 107030000 unchanged.

# 6. FASTBOOT path (revert insurance, post abl-1.1.1)
#    Confirm VolDown firmware menu still reaches fastboot and Mac enumerates the device.

# 7. CERTIFY if 1-6 pass
python3 tools/etk_drift.py --save-baseline --pin     # re-pin to 20260614
#    Bump nightly notes in scripts/profiles/SM8250.sh + env.sh + README.
```

**Abort triggers:** any CRITICAL in §5.1 on a non-input field; gamepad button-code drift
(step 3); R3 fails post-resume (step 4b — the fake-suspend change is the one that could
plausibly regress this); panel dark on cold boot; fastboot unreachable post-abl-bump.

## §6. Opportunity flagged: rumble is now first-class on the Flip 2

The Flip 2's `Retroid Pocket Gamepad` (and `spmi_haptics`) expose force feedback
(`FF=107030000`) on the current stack. RPCS3 supports DualSense/evdev rumble; ETK currently
ships no rumble configuration. This is a **net-new capability** unlocked by the SM8250
rumble work, worth a future Pitstop toggle (out of scope here — the experimental conditions
in memory are ON HOLD). Logged, not actioned.

## §7. Recommendation

**Adopt-clear, certification-gated on §5.** The 20260614 changelog looks like a major SM8250
push but is overwhelmingly **Mangmi Pocket Max bring-up** isolated to that device's files;
the Flip 2's DTS, panel, charger, thermal, and gamepad surfaces are untouched by the new
delta, and the advertised "big" SM8250 items already ride on the rig's stable 20260612.

True new risk to the Flip 2 narrows to three shared-surface items, all low:
1. **`rocknix-fake-suspend: unfreeze before shutdown`** — re-run the R3-post-resume gate (§5.4b).
2. **`abl: bump to 1.1.1`** — re-confirm the fastboot revert path (§5.6).
3. **`gamescope` bump + `gamecontrollerdb`/`gamepadcalibration`** — covered by §5.3–5.4.

Bank a baseline first (none exists post-epoch-sweep), then proceed. This pre-check gates ETK
**v0.3.0**.

---

### Appendix — pre-flash gamepad baseline (20260612, event9 DualSense)

Captured `bin/gamepad_probe.py /dev/input/event9` on 2026-06-14 (the drift tool's blind
spot). All codes match the certified mapping — **zero drift on 20260612**; this is the
reference for the post-flash §5.3 comparison after `d43279a`:

```
confirm ✕ = 304   back ○ = 305   △ = 307   □ = 308
L1 = 310   R1 = 311   SELECT = 314   START = 315   PS = 316
L3 = 317   R3 = 318                    (R3 = recovery panic button — confirmed)
D-pad = ABS 16/17 (HAT0X/Y)
L-stick = ABS 0/1   R-stick = ABS 3/4   L2 = ABS 2 (Z)   R2 = ABS 5 (RZ)
```

Full map verified across two probe sessions — **zero drift on 20260612**, R3=318
([[project_headless_refactor]]) confirmed. Complete reference for the post-flash §5.3 compare.

### Appendix — provenance

- Rig probe: `ssh root@sm8250.local` — `etk_drift.py --check`, `/sys/class/power_supply`, `/proc/bus/input/devices`, `/sys/class/input/event*/device/capabilities/{ev,ff}`, `/sys/class/drm/*/status`.
- Commit/delta reconstruction: `gh api repos/ROCKNIX/distribution/commits?sha=next` + per-commit file lists. Build boundary `a3388da2` confirmed against `etk_drift.py --check` build id and `os-release` BUILD_DATE.
- Nightlies are rolling CI artifacts (not git tags/releases), so the delta is computed against the rig's actual build commit, not a tag.
