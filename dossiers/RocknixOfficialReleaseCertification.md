# Rocknix Certification — OFFICIAL RELEASE 20260601

**Status:** ✅ **CERTIFIED 2026-06-02** — official ROCKNIX release `20260601` on SM8250 (Retroid Pocket Flip 2). `etk_drift.py --check` clean (no structural drift); drift baseline `20260601.json` banked + pinned and its `build_id` matches `/etc/os-release`; headless gate passed (gamepad codes unchanged, R3 survives suspend/resume, RPCS3 binds `Turnip Adreno (TM) 650` on Mesa 26.1.0); per-game render re-validated on GT5P + GT HD Concept, both running **fully on internal UFS** (Tier B). **Certifies v0.1.4** — the first non-prerelease tag and ETK's graduation from nightly-pinning to an official build.
**Provenance:** This dossier writes up an on-rig certification that had already been performed (the drift baseline was banked live before the write-up). Verified against `root@sm8250.local` on 2026-06-02.
**Related:** `dossiers/InstallToInternalRecovery.md` (internal-storage layout + recovery), `dossiers/RocknixNightly20260531CertificationDossier.md` (prior pin), memories `project_race_baseline_status`, `project_internal_storage_tierb`.

---

## §1. The build (verified on-rig)

`/etc/os-release`:
```
OS_NAME="ROCKNIX"
OS_VERSION="20260601"
OS_BUILD="official"
BUILD_ID="e7b9e9a30440bf6a7eb41dc229a43f4f4a6d4371"
BUILD_BRANCH="next"
BUILD_DATE="Mon Jun  1 09:13:48 UTC 2026"
HW_DEVICE="SM8250"  HW_CPU="Snapdragon 865"  HW_ARCH="aarch64"
```
`uname`: `Linux SM8250 7.0.2 #1 SMP PREEMPT Mon Jun  1 05:30:11 UTC 2026 aarch64`.

> The official release was cut from the `next` branch (`BUILD_BRANCH=next`), so the OS update path for users is the standard official-release channel, not the NIGHTLY branch toggle the prior README described.

## §2. Driver — unchanged from the certified nightly

- `strings /usr/lib/libvulkan_freedreno.so` → `Mesa 26.1.0`, `turnip`.
- `RPCS3.log`: `RSX: Found Vulkan-compatible GPU: 'Turnip Adreno (TM) 650' running on driver 26.1.0` → `Renderer initialized on device 'Turnip Adreno (TM) 650'`. (The `Unsupported device` warning is RPCS3's standard non-fatal note on Turnip.)

So the README driver line (`MESA Turnip 26.1.0`) needs **no change** — the official release ships the same Turnip the 20260531 nightly carried.

## §3. Drift + structural gate

- `etk_drift.py --check` — **clean** (no CRITICAL on thermal/CPU/GPU device-profile assumptions).
- Baseline store `vault/os_profiles/` holds `20260528 / 20260529 / 20260531 / 20260601 / pin.json`. **`pin.json` `build_id` = `e7b9e9a3…` = the live `os-release` BUILD_ID** → the pin is the official release, not a stale nightly. The drift step of certification is therefore complete and banked.
- Input-node renumbering across builds remains benign — `find_gamepad()` matches by name, not index.

## §4. Headless gate

- Cold-boot R3 fires recovery; R3 survives suspend/resume (the non-negotiable test).
- Ignition fires on a real PS3 title (active_id != IDLE, no VAULT:ERROR); thermal governs.
- RPCS3 binds Adreno 650 / Turnip 26.1.0 (§2).

## §5. Per-game render + internal-storage (Tier B) — live

Both certified titles run **fully on internal UFS** (game data + vault + `dev_hdd1` caches), symlink-wired with on-SD `.presplit` safety copies and a working `ROLLBACK.sh`:

| Title | dev_hdd0 game | vault | wiring |
|---|---|---|---|
| GT5 Prologue (NPUA80075) | `→ /storage/etk-internal/dev_hdd0_game/NPUA80075` | `→ /storage/etk-internal/vault/SM8250/NPUA80075` (~10,972 files) | symlinks + `.presplit` |
| GT HD Concept (NPEA90002) | `→ /storage/etk-internal/dev_hdd0_game/NPEA90002` | `→ /storage/etk-internal/vault/SM8250/NPEA90002` (~1,508 files) | symlinks + `.presplit` |

Internal `/storage` (`/dev/sda25`): 6.3 G, ~50% used, **3.2 G free** — above the ≥1.5 G headroom floor. `install.sh` resolves `ETK_ROOT=/storage/games-internal/roms/etk` and engages symlink-safe sync ("Internal-storage vault detected").

> The SD's real games/roms reach internal-boot via `etk-sd-rebind.service` (see InstallToInternalRecovery.md). ETK itself was never broken by `installtointernal`.

## §6. Race-stability status (corrected — was stale in the docs)

The README previously said "no version yet certified as race stable." **That is false and now corrected.** GT5P's live telemetry career stat reports **`best_streak=16`** crash-free sessions; an independent count of strictly-consecutive `CLEAN` finishes is **8**. Dated streaks from the ledger:

| Streak | When | OS |
|---|---|---|
| 8 CLEAN (best consecutive-clean) | 2026-05-22 | early nightly (~20260520–21) |
| 5 CLEAN | 2026-05-22 | early nightly |
| 6 CLEAN | 2026-05-31 19:02–20:10 | nightly-20260531 |
| 6 CLEAN | 2026-05-31 21:29 → 2026-06-01 09:42 | straddles into official `20260601` |

**Calibration (don't overstate):** the streaks were earned on a **saturated** vault (~49,730 files in May). The official-`20260601` migration **wiped the vault** (`ROLLBACK.sh`: "no `.presplit` since the official-release wipe"; internal vault now ~10,972 files), so June runs re-harvest from scratch and crash until re-saturation — the predicted "fresh cache → in-race compiles → fence timeouts" regime. GT5P lifetime clean-rate is ~28%, but that **averages two regimes** (May saturated + June fresh) and must not be quoted as current durability. Race-stable is proven **reachable on a saturated vault**, not guaranteed every session. A May-saturated vs June-fresh A/B over June is the planned next dataset.

## §7. Recommendation

**CERTIFIED for v0.1.4 (full release).** The official `20260601` build is structurally and behaviorally equivalent to the last certified nightly (same Turnip, clean drift, headless gate passes), with internal Tier-B confirmed live. Ship as the first non-prerelease tag. Hold any "consistently race-stable" claim until the June fresh-harvest re-saturates and the streak reproduces from a clean install.
