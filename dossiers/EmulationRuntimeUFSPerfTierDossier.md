# DOSSIER: Emulation Runtime + Launch Hot-Path to UFS (Performance Tier)

**Date:** 2026-06-02
**Device:** Retroid Pocket Flip 2 (SM8250), ROCKNIX `20260601` (official), kernel 7.0.2
**Goal:** Assess relocating the **emulation runtime and launch sequence** onto internal UFS to exploit its speed/durability — a *performance/reliability* play distinct from SD-free availability. Quantify the win, define what to move, and identify what blocks it.
**Status:** Feasibility + grounded launch-chain audit done (live probe). **Actionable now without repartition** — the runtime hot path fits the current 3.2 GB free. Companion to [[SDFreeGameMigration]] (this tier sits *below* SD-free and is its prerequisite).

---

## 1. WHY THIS IS A SEPARATE ASSESSMENT FROM SD-FREE

[[SDFreeGameMigration]] asks "does the game survive the SD being pulled?" — an *availability* goal, space-gated, blocked on the deferred destructive repartition ([[FastbootRevertConclusion]]). **This** dossier asks "does the emulation run *faster / more durably* from UFS?" — a *quality* goal that pays off **even with the SD inserted**, and is **achievable today** for the hot path because that path is small. Different metric, different blocker, different timeline.

## 2. THE REFRAMING FINDING

A live audit shows the **executable and control scripts are already on UFS** — the gap is the **I/O-hot data plane**, which is exactly the right thing to move for performance:

| Layer | Component | Physical location | On hot path? |
|---|---|---|---|
| **Emulator binary** | `/usr/bin/rpcs3-sa` (84 MB, static ELF) | **system image** (squashfs `/`, from `/flash`/UFS) | already UFS ✅ |
| **Launch wrappers** | `/usr/bin/start_rpcs3.sh`, `/storage/.config/modules/{Start RPCS3.sh, etk_pitstop.sh}` | system + `/storage/.config` (sda25 **UFS**) | already UFS ✅ |
| **RPCS3 config** | `/storage/.config/rpcs3/config.yml` | sda25 **UFS** | already UFS ✅ |
| **ETK control plane** | Sentry `/storage/.config/custom_scripts/01-etk-sentry.sh`, `etk.service` | sda25 **UFS** | already UFS ✅ |
| **Per-game data + shaders** | split games + vault | `/storage/etk-internal` (sda25 **UFS**) | already UFS ✅ |
| **RPCS3 data root** | `dev_flash`, `dev_hdd0` (saves/caches/skeleton), `dev_hdd1`, `custom_configs` | `/storage/roms/bios/rpcs3` = **SD (mmcblk0p2)** | **SD ❌** |
| **ETK runtime payload** | `env.sh` + all daemons (`vault_d/thermal_d/mango_bridge/input_d/recovery/session_postmortem`) + `telemetry.log` + config | `/storage/roms/etk` (= `/storage/games-internal/roms/etk`) = **SD** | **SD ❌** |
| **JIT/PPU/SPU caches** | RPCS3 recompiler caches | under `dev_hdd0` ⇒ **SD** | **SD ❌** |
| **Disc images** | `*.iso` (e.g. GT5 19.45 GB) | `/storage/roms/ps3` = **SD** | SD (too big to move) |

**Conclusion:** "move emulation + launch to UFS" is *mostly already done* for the code/control plane. The remaining SD-resident pieces are the **data plane** — `dev_flash` (read every boot), saves, JIT caches, `custom_configs`, and the **entire ETK daemon payload** — i.e. precisely the latency- and wear-sensitive I/O.

## 3. THE LAUNCH CHAIN (grounded in the actual scripts)

**RPCS3 launch (stock JELOS/ROCKNIX `start_rpcs3.sh`):** on *every* launch it runs a `FOLDER_LINKS` block that **hardcodes the SD as the target** and rebuilds the symlinks (and `rm -rf`s the source first):
```sh
FOLDER_LINKS=("dev_flash" "dev_hdd0" "dev_hdd1" "custom_configs")
TARGET_FOLDER="/storage/roms/bios/rpcs3/$FOLDER_LINK"      # <-- SD
SOURCE_FOLDER="/storage/.config/rpcs3/$FOLDER_LINK"        # UFS config dir
ln -sf "$TARGET_FOLDER" "$SOURCE_FOLDER"
```
PSN launch resolves `/storage/.config/rpcs3/dev_hdd0/game/<PSNID>/USRDIR/EBOOT.BIN` (→ SD via that symlink, or → `etk-internal` for ETK-split games). Disc launch uses `/roms/ps3/<iso>` (SD).

**ETK control plane (`01-etk-sentry.sh`):** `source`s **hardcoded** `/storage/games-internal/roms/etk/scripts/env.sh` (SD) in 3 places, then spawns every daemon from `$ETK_ROOT/bin/` where `$ETK_ROOT` resolves to the SD. So each session reads ETK code + writes telemetry on the SD.

**`etk-sd-rebind.service`** (oneshot, `After=rocknix-automount`, `Before=sway`) repoints `/storage/roms` + `games-internal` at the SD's real games — the "functional config" glue that makes SD-on-internal-boot work, and the reason `ETK_ROOT` currently lives on SD.

## 4. RELOCATION LINCHPINS & THE OVERRIDE PROBLEM

Two hardcoded SD targets must be defeated, and **both self-heal back to SD if naively edited:**

1. **The `FOLDER_LINKS` block in `start_rpcs3.sh` / `Start RPCS3.sh`** rewrites the dev_flash/dev_hdd0/dev_hdd1/custom_configs symlinks to SD on *every* launch. These are **system files in `/usr`** (lost on every ROCKNIX update). Relocation therefore cannot just edit them — ETK must **own the launch entry** (it already ships `etk_pitstop.sh` in the Tools menu) or insert a post-link tripwire that re-points the four symlinks to a UFS root after the stock block runs, the same active-tripwire pattern the Sentry already uses for the modules dir.
2. **The Sentry's hardcoded `…/games-internal/roms/etk/scripts/env.sh`** must become a UFS path (and ideally a single variable, not three literals). Moving `ETK_ROOT` to UFS also requires `env.sh` to define paths relative to the new root.

This is the crux risk: a relocation that isn't enforced by an ETK-owned tripwire will be silently reverted to SD by the stock launcher or a ROCKNIX update.

## 5. PERFORMANCE / DURABILITY THESIS (to be measured)

UFS (sda25) offers far better random I/O and write endurance than the SD. Hypothesized wins from moving the data plane:
- **Faster game load:** `dev_flash` firmware + RPCS3 **PPU/SPU JIT cache** reads happen at title boot; UFS random read should cut boot-to-playable time.
- **Smoother streaming:** for non-split games, asset reads from `dev_hdd0` off UFS vs SD reduce hitching (frame-time variance).
- **Lower save latency** and **less stall** on autosave/replay writes.
- **Durability/wear:** moves write-heavy paths off the wear-prone SD (the same rationale that already justified moving the shader vault — [[InstallToInternalRecovery]] §Tier A).
- **Crash-resistance:** removes SD-corruption as a fault source on the control + save path.

**This closes a standing open question:** [[InstallToInternalRecovery]] says Tier A is a keeper on integrity/durability grounds but the **performance claim is "promising but not yet measured — pending the smoothness instrument."** This tier is what that instrument would validate.

**Measurement plan (A/B, same game, UFS vs SD data plane):** boot-to-playable time; first-lap vs warm-cache lap load; frame-time variance / 1%-low (MangoHud frametime graph already in the HUD); save-write latency; I/O wait during streaming. Hold shaders constant (already UFS). Capture via the existing telemetry/probe tooling.

## 6. WHAT TO MOVE vs LEAVE — and the space budget

Move to a UFS RPCS3 root (e.g. `/storage/etk-internal/rpcs3/`):
- `dev_flash` (189 MB) — whole, shared.
- `dev_hdd0/home/00000001/{savedata,exdata}` (saves + `.rap`, small) and `dev_hdd0/cache` (JIT/PPU/SPU caches).
- `custom_configs/` (per-game ymls, ~80 KB) and `dev_hdd1`.
- `ETK_ROOT` payload (`bin/`, `scripts/`, `config/`, telemetry) — tens of MB.

Leave on SD:
- **`dev_hdd0/game/<ID>` per-entry** — keep the existing selective split (big/active games → `etk-internal`; others stay SD). *Design nuance:* today the whole `dev_hdd0` is the SD symlink and ETK splits individual `game/<ID>` out to UFS; the perf tier **inverts** this — relocate the `dev_hdd0` *skeleton* to UFS and symlink *unsplit* games' `game/<ID>` back to the SD copy.
- **Disc `*.iso`** — too large (GT5 = 19.45 GB).

**Budget:** dev_flash 0.19 GB + caches + saves + ETK payload ≈ well under 0.5 GB *new* on UFS (game data + shaders already counted in the 2.58 GB `etk-internal`). **Fits the current 3.2 GB free with margin — no repartition needed.**

## 7. THE TIER LADDER (where this sits)

| Tier | On UFS | Win | Blocker |
|---|---|---|---|
| **Tier 0 (today)** | shaders + split game data | durability of vault | — |
| **Tier P (this dossier)** | + RPCS3 data plane (dev_flash, dev_hdd0 skeleton, JIT caches, custom_configs, saves) + ETK_ROOT | **perf + durability + crash-resistance**, SD still inserted | none — fits now |
| **Tier SD-FREE** | + full per-game data / ISO | runs with card removed | larger STORAGE → **repartition** |

Tier P is the natural, low-risk next step **and** the foundation SD-free builds on.

## 8. IMPLEMENTATION SKETCH

1. **Define a UFS RPCS3 root** under `etk-internal`; migrate dev_flash / dev_hdd0-skeleton / custom_configs / dev_hdd1 there (with the §6 per-game `game/<ID>` selectivity).
2. **ETK-owned launch enforcement:** make `etk_pitstop.sh`/the ETK launch path (or a Sentry tripwire) re-point the four `FOLDER_LINKS` symlinks to the UFS root *after* the stock `start_rpcs3.sh` block, every launch — so stock behavior and ROCKNIX updates can't revert it.
3. **Relocate `ETK_ROOT` to UFS:** move the ETK payload to `/storage/etk-internal/etk/`, update `env.sh` to one root variable, and replace the Sentry's three hardcoded `games-internal/...` literals.
4. **Pre-flight + idempotent repair** (mirror `install.sh`): re-verify/re-inject symlinks on each run and after the boot wipe.
5. **Extend `ROLLBACK.sh`** to cover the data-plane + ETK_ROOT relocation.
6. **Coordinate with `etk-sd-rebind.service`** so rebind no longer assumes ETK_ROOT on SD.

## 9. VERIFICATION PLAN

1. Apply Tier P with SD inserted; confirm a split game (GT5P) and a non-split/disc game both still launch and tune normally.
2. **A/B measure** §5 metrics: same game, data plane on SD vs UFS; record boot-to-playable, frame-time variance, save latency.
3. Confirm shaders still credit to the UFS vault, saves persist, HUD/`TARGET_ID` resolve, and a reboot re-injects all symlinks (volatile-dir survival).
4. Simulate a ROCKNIX `/usr` update (touch/restore stock `start_rpcs3.sh`) and confirm the ETK tripwire re-points to UFS on next launch.

## 10. RISKS / OPEN QUESTIONS

- **Stock launcher reverts the symlinks every run** (and `rm -rf`s the source) — the central risk; demands ETK-owned enforcement, not a one-shot edit (§4).
- **`/usr` scripts are wiped by ROCKNIX updates** — never patch them in place; own the entry or tripwire.
- **`dev_hdd0` selectivity** (skeleton-to-UFS, inverting the current split) needs careful migration to avoid orphaning unsplit games' data.
- **`etk-sd-rebind` interaction** — ensure rebind and the new UFS ETK_ROOT don't fight.
- **Not SD-free:** Tier P still references the SD for unsplit/ISO game data — pulling the card still breaks those titles. That's [[SDFreeGameMigration]] + repartition.
- **Don't conflate with crash-stability:** per [[InternalStorageManagerDossier]], internal storage is a *smoothness/durability* win and does **not** fix track panic / R3 — this tier must be sold as perf/durability, with the panic work tracked separately.

## 11. CROSS-REFERENCES

- [[SDFreeGameMigration]] — the availability tier above this; Tier P is its prerequisite.
- [[InternalStorageManagerDossier]] — the tier model + the "smoothness, not crash-fix" caveat.
- [[InstallToInternalRecovery]] — existing UFS relocation (`etk-internal`, `.presplit`, `ROLLBACK.sh`) and the unmeasured-performance note this tier resolves.
- [[FastbootRevertConclusion]] — why the SD-free step beyond Tier P needs a destructive repartition.
