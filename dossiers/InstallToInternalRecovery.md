# INSTALL-TO-INTERNAL — RECOVERY LEARNINGS + INTERNAL-STORAGE PERFORMANCE/DURABILITY EXPERIMENT
**Status:** Part 1 (recovery) = DONE, reboot-validated 2026-06-01. Part 2 (experiment) = PLANNED, not yet executed.
**Device:** Retroid Pocket Flip 2 (SM8250), ROCKNIX nightly 20260531, U-Boot bootloader.
**Provenance:** Operator ran ROCKNIX `installtointernal` as an experiment + ETK stress-test ("what happens to ETK when an advanced user does this?"). This dossier banks the recovery learnings and plans the follow-on internal-storage performance/durability experiment.
**Related memory:** `project_installtointernal_recovery.md`. **Related dossiers:** `ShaderSwarmFeasibility.md` (vault structure), `RocknixNightlyMigrationDossier.md`.

---

## PART 1 — WHAT `installtointernal` DID, AND HOW WE RECOVERED

### 1.1 What the tool did
- Shrank Android `userdata` 104 → 96 GB; carved the freed ~8 GB into internal **`ROCKNIX` (sda24, 2 GB, `/flash`)** + **`STORAGE` (sda25, 6 GB, `/storage`)**.
- Repointed boot to internal: `boot=LABEL=ROCKNIX disk=LABEL=STORAGE`.
- The "Copy existing /storage to new STORAGE? [y/N]" prompt (answered **y**) **filled the 6 GB partition to 100%** (mostly a partial `.local/fex-emu` ArchLinux rootfs copy) before aborting on `.local/.steam/screenshots`.

### 1.2 Why the front-end broke (empty carousel) — and why "answer No" wouldn't have helped
The SD card is the intact **old `/storage`** with a *boot-card layout*: the real 127 GB of games + the ETK install live at `mmcblk0p2:/games-internal/roms/`. On internal boot, Rocknix automount mounts the card root at `/storage/games-external` and the card's **empty `/roms` skeleton** at `/storage/roms`. So ES saw no games and the shader symlink (`/storage/.cache/mesa_shader_cache → /storage/games-internal/roms/etk/vault/...`) dangled.
**Key insight:** this is caused by *internal-boot + boot-card-SD layout*, NOT by the copy. Answering "No" to the copy prompt would have produced the same empty carousel (only the disk-full was the copy's fault).

### 1.3 The label collision (root structural problem)
**Both** the internal partitions **and** the SD carry `LABEL=ROCKNIX` / `LABEL=STORAGE`. Internal (UFS/scsi) enumerates first, so it always wins `boot=LABEL=` / `disk=LABEL=` resolution. Selecting `mmc0` in U-Boot loads the SD *kernel* but `disk=LABEL=STORAGE` still resolves to internal — so you cannot escape internal storage by boot-device choice alone.

### 1.4 The recovery we shipped — `etk-sd-rebind.service`
A oneshot unit in `/storage/.config/system.d/` (script in `custom_scripts/etk-sd-rebind.sh`) that bind-mounts the SD's real data over the empty internal mountpoints:
```
mount --bind /storage/games-external/games-internal       /storage/games-internal
mount --bind /storage/games-external/games-internal/roms  /storage/roms
```
This restores games + un-dangles the vault symlink. **Ordering is load-bearing** — it MUST run after automount finishes and before the UI scans roms:
```
[Unit] Requires=rocknix-automount.service
       After=rocknix-automount.service
       Before=sway.service
```
plus the script **waits for automount's `/roms` skeleton bind to appear** before stacking on top. First attempt (only `After=`) lost the race (ran at +3.4 s, before automount at +3.8 s; automount's bind landed on top). With `Requires=` it now runs at +4.3 s after automount — reboot-validated.
Also freed space with `rm -rf /storage/.local` (redundant partial copy; original safe on SD).
`install.sh` ran clean on this config and correctly resolved `ETK_ROOT=/storage/games-internal/roms/etk` — **ETK itself was never broken.**

### 1.5 The config-divergence trap (bit us after recovery)
On internal boot, `/storage/.config` is the **internal partition's copy** and **permanently diverges from the SD's `.config`**. `installtointernal`'s partial copy + a settings reset left the internal `.config` degraded, breaking things `install.sh` does **not** manage (it syncs ETK files, not Rocknix/RPCS3 config):
- **MangoHud gone:** `runemu.sh` checks `rocknix.mangohud.enabled` in `system.cfg`; the per-game `ps3["…"].rocknix.mangohud.enabled=1` lines were missing → fell back to global `=0`.
- **Gamepad didn't reach the game:** RPCS3 `input_configs/global/Default.yml` had stale `Device: InputPlumber GameController 1`, but the current InputPlumber virtual pad is `DualSense Wireless Controller 1` (PS-pad nightly migration). R3 still worked because `input_d.py` matches by name.
- **Racing profiles lost:** per-game `cooling.profile=aggressive` / `cpugovernor=performance` / `gpuperf=performance` (GT6, GT3, Forza, Halo) and the global `cooling.profile=aggressive` were wiped.
- **Wi-Fi:** Rocknix configures Wi-Fi from `system.cfg`'s `wifi.key` at boot (NetworkManager has no stored `.nmconnection`); a wholesale config copy risked reverting the key — **always confirm the live Wi-Fi password before copying `system.cfg`** (operator's correct key: kept, not the wrong internal one).
**Fix:** restore the affected files from the SD original (`/storage/games-external/.config/...`), which held the correct values; `system.cfg` was restored wholesale to match the SD original (with `wifi.key` verified against the operator).

### 1.6 Revert paths — what works on THIS device
- **ROCKNIX ABL "Uninstall ROCKNIX" does NOT exist here** — the Flip 2 uses **U-Boot** (menu: Boot/Reset/…/`mmc0`/`scsi0-5`/Enable fastboot/Drop to shell), not the ROCKNIX ABL. That uninstall option is only on ABL-bootloader devices.
- **`tune2fs -L` relabel of the live `/storage` does NOT persist** — the kernel rewrites the old label at unmount.
- **`grub disk=UUID` pin** is possible (GRUB shows a 2 s menu) but risky — boot-hang if the initramfs lacks UUID support, and recovery is hard with fastboot down.
- **Clean FULL revert = fastboot** (boot U-Boot → Enable fastboot → `fastboot erase ROCKNIX`/`STORAGE` → reboot → boots SD natively, reclaims 8 GB). **Blocked 2026-06-01:** fastboot wouldn't enumerate on the Mac across two USB-C cables — **suspected charge-only cables**; retry with a confirmed DATA cable or a Windows PC.
- **Chosen outcome:** "functional" config — internal boot + `etk-sd-rebind`. Reliable; full revert deferred to a fastboot session.

### 1.7 Gotcha table
| Symptom | Root cause | Fix |
|---|---|---|
| Empty carousel | boot-card SD layout → automount binds empty `/roms` skeleton | `etk-sd-rebind` binds real `games-internal/roms` on top |
| `/storage` 100% full | partial `.local` copy from `installtointernal` | `rm -rf /storage/.local` (original on SD) |
| Rebind lost on reboot | service ran before automount (race) | `Requires=` + `After=rocknix-automount`, `Before=sway`, script waits for automount |
| MangoHud absent | per-game enable lines missing in internal `system.cfg` | restore `system.cfg` from SD |
| Gamepad dead in-game (R3 ok) | RPCS3 pad device name stale vs DualSense migration | restore RPCS3 `Default.yml` from SD |
| Lost aggressive cooling/perf | racing profiles wiped in reset | restore `system.cfg` from SD |
| `mmc0` boot still uses internal storage | `LABEL=STORAGE` collision (internal wins) | only fastboot-erase or label-break fixes it |

---

## PART 2 — INTERNAL-STORAGE PERFORMANCE/DURABILITY EXPERIMENT (PLAN)

### 2.1 Hypothesis
Moving the **hot, random-I/O, frequently-written** working set from the SD card to the internal **UFS** will (a) reduce shader-compile stutter and load times (UFS ≫ SD on random I/O), and (b) improve **durability** (SD cards wear and corrupt under heavy writes; the vault is rewritten every session). As-configured, internal boot gives **zero** emulation speedup because games + vault still live on the SD via the rebind — this experiment is the only lever that could change that.

### 2.2 Feasibility — what fits in the 6 GB partition (measured 2026-06-01)
Internal `/storage` free: **~5750 MB**. PSN games are 1 MB stubs; real data is in `dev_hdd0`.
| Title | dev_hdd0 install | vault | full-internal fits? |
|---|---|---|---|
| GT HD Concept (NPEA90002) | 690 MB | 44 MB | **YES — 734 MB** |
| GT5 Prologue (NPUA80075) | 1836 MB | 445 MB | **YES — ~2.3 GB** |
| LittleBigPlanet (NPUA80472) | 2039 MB | 19 MB | YES — ~2.1 GB |
| GT6 (NPEA00502) | 14,734 MB | 1159 MB | NO (16 GB) |
| GT5 (.iso BCUS98114) | 19,917 MB | 73 MB | NO (20 GB) |
| **All vaults combined** | — | **~1.7 GB** | trivially |
| RPCS3 `dev_hdd1` caches | 23 MB | — | trivially |

**Conclusion:** A full library on internal is impossible in 6 GB (dev_hdd0 = 32 GB, GT5 = 20 GB ISO, GT6 install = 14.7 GB). But **GT HD + GT5P can both run fully internal (~3 GB)**, and **every vault + cache fits with room to spare** — leave ≥1.5 GB headroom (vault grows; the partition is also the system `/storage` — filling it breaks ES, as seen during recovery).

### 2.3 Three test tiers (escalating, all symlink-based = reversible)
Scratch location: a non-bind-mounted internal dir, e.g. `/storage/etk-internal/`.

- **Tier A — hot data only (safest, biggest durability win, do first).**
  Move the active game's **vault** + RPCS3 **`dev_hdd1` caches** to internal; repoint the `mesa_shader_cache` symlink + cache paths. Game data stays on SD (sequential reads, less SD-penalty). Isolates the shader random-I/O variable — the most likely bottleneck — and moves the most-written data off the card (durability). Footprint: ≤1.2 GB for GT6's vault, ≤0.5 GB for GT5P.

- **Tier B — small game fully internal ("entirely internal" proof the operator asked for).**
  Move **GT HD Concept (734 MB)** and/or **GT5P (~2.3 GB)**: copy `dev_hdd0/game/<TITLEID>` + the vault to internal, replace the on-SD paths with symlinks, repoint dev_hdd1. Run the game with *all* its data + shaders on UFS. GT HD is the ideal first subject (smallest, fits with 5 GB to spare). GT6/GT5 are excluded (don't fit).

- **Tier C — full library internal: NOT FEASIBLE** in 6 GB. Would require a larger internal partition (re-do `installtointernal` with a smaller Android userdata, or repartition) — out of scope for this test.

### 2.4 Mechanics (reversible)
1. `mkdir -p /storage/etk-internal/{vault,dev_hdd0_game,dev_hdd1}`.
2. Copy the target's vault / `dev_hdd0/game/<ID>` / `dev_hdd1` into it.
3. Replace the SD-side path with a symlink → internal copy (or bind-mount). The `mesa_shader_cache` symlink already centralizes shaders — just retarget it to the internal vault.
4. Keep an `ETK_INTERNAL_TEST=1` marker so it's obvious and scriptable to undo.
**Rollback:** delete the symlinks, restore the original SD paths, retarget the shader symlink back. No data loss (originals copied, not moved, until proven).

### 2.5 Measurement protocol (validate before integrate)
Same runs, A/B, on the SD baseline vs internal:
- **Stressor:** GT5P time-trial + GT HD (and GT6 Deep Forest for the Tier-A vault-only test, since GT6 can't go fully internal but its vault can).
- **Metrics:** shader-compile stutter / frame hitches (ETK telemetry + visual), level/lap load times (stopwatch or log timestamps), in-race FPS consistency, peak/sustained thermal. Capture 3 runs each side.
- **Decision rule:** integrate only if internal shows a *reproducible, meaningful* improvement (e.g. measurably fewer first-lap shader hitches or shorter load). If null, keep the SD layout and bank the negative result (avoids cargo-culting an unproven optimization).

### 2.6 Durability angle (independent of perf)
Even if perf is a wash, moving the **vault (written every session), saves, and caches** to internal UFS reduces SD write-wear and corruption exposure — the SD is the single point of failure for the whole rig. This alone may justify Tier A.

### 2.7 Risks
- **6 GB is also the system partition.** Filling it breaks ES/boot (observed during recovery). Leave ≥1.5 GB headroom; the vault grows as new shaders bank.
- **Mesa rebuilds** rotate the vault partition key — the internal vault copy would go stale on the next Rocknix Mesa bump (re-sync needed).
- **Config divergence** (Part 1.5) still applies — internal `/storage` won't reflect SD-side changes.
- A clean **fastboot revert** wipes all of this; treat the internal copies as disposable test artifacts, not canonical.

### 2.8 Recommendation
Start with **Tier A** (vault + `dev_hdd1` → internal, GT5P first) — safest, isolates the hot variable, and is the biggest durability win. Measure. Then **Tier B** with **GT HD Concept** as the "entirely internal" proof. Skip GT6/GT5 (don't fit). Decide on permanence only from the measured A/B result.
