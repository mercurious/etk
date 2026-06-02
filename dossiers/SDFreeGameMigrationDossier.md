# DOSSIER: Running a Game Fully From Internal UFS (SD-Free Play)

**Date:** 2026-06-02
**Device:** Retroid Pocket Flip 2 (SM8250), ROCKNIX `20260601` (official, branch `next`), kernel 7.0.2
**Goal:** Define exactly what must live on internal UFS so a chosen PS3 game launches and plays **with the SD card physically removed** from the slot.
**Status:** Feasibility + manifest established from a live-rig probe. The per-game data + shader relocation is **already solved** by ETK's internal-storage feature; the remaining gaps are the **RPCS3 emulator root**, the **ES launch chain**, and — for anything beyond one small PSN title — the **UFS partition size**, which is the deferred destructive repartition (see [[FastbootRevertConclusion]]).

---

## 1. THE STORAGE REALITY (probed 2026-06-02)

The single most important fact, and the reason SD-free is non-trivial:

| Mount | Backing device | Size | Avail | What lives here |
|---|---|---|---|---|
| `/` | `/dev/loop0` (squashfs) | 1.8 G | 0 | ROCKNIX OS image (read-only) |
| `/flash` | `/dev/sda24` (UFS, **ROCKNIX**) | 2.0 G | 159 M | Boot/system, 92% full, read-only |
| `/storage` | `/dev/sda25` (UFS, **STORAGE**) | **6.3 G** | **3.2 G** | ETK internal data, configs, `.cache` |
| `/storage/roms` | **`/dev/mmcblk0p2` (SD card)** | 235 G | 98.7 G | **RPCS3 + ALL games + firmware + configs** |
| `/storage/games-internal`, `/storage/games-external` | `/dev/mmcblk0p2` (SD) | 235 G | 98.7 G | SD bind targets |

**`/storage/roms` is the SD card.** The entire emulation payload hangs off it:
- RPCS3 data root: `/storage/roms/bios/rpcs3/` — **32.0 GB** (`dev_hdd0` alone = 31.8 GB)
- PS3 ROM pointers + ISOs: `/storage/roms/ps3/` — **19.5 GB** (dominated by `Gran Turismo 5 [BCUS98114].iso` = 19.45 GB)
- `dev_flash` (PS3 firmware, required by every title): 189 MB
- Per-game configs: `/storage/roms/bios/rpcs3/custom_configs/config_<ID>.yml`
- The ES gamelist + `.psn` launch pointers: `/storage/roms/ps3/gamelist.xml`, `<Game>.psn`

**Pull the SD → `/storage/roms` unmounts → RPCS3, firmware, every game, every config, and the gamelist all vanish.** Nothing PS3-related survives on UFS today *except* what ETK has already split out (next section).

## 2. WHAT ETK ALREADY SOLVES (the head start)

ETK's internal-storage feature ("Tier A", confirmed in [[InstallToInternalRecovery]]) already relocates the heavy, write-sensitive per-game data onto UFS at **`/storage/etk-internal/`** (`/dev/sda25`), and back-references it from the SD-based RPCS3 tree via symlinks:

- `…/rpcs3/dev_hdd0/game/NPUA80075 → /storage/etk-internal/dev_hdd0_game/NPUA80075` (GT5P game data, **1.79 GB**)
- `…/rpcs3/dev_hdd0/game/NPEA90002 → /storage/etk-internal/dev_hdd0_game/NPEA90002` (GT6 game data, 0.67 GB)
- Shader vault: `/storage/.cache/mesa_shader_cache → /storage/etk-internal/vault/SM8250/<ID>/shaders` (GT5P shaders, 103 MB)
- A `.presplit` copy is left on the SD as a rollback, plus `/storage/etk-internal/ROLLBACK.sh`.

`/storage/etk-internal` currently holds **2.58 GB**. So the *game payload + shaders* for a PSN title are already UFS-resident and survive an SD pull. **What does NOT yet survive is everything in §3 that still resolves through `/storage/roms` (= SD).**

## 3. THE COMPLETE PER-GAME SD-FREE MANIFEST

Everything below must be present **on UFS** (and reachable via a path that does not pass through the SD mount) for one game to boot SD-free. Sizes are measured for **GT5P (NPUA80075)**, a PSN/HDD title (no ISO):

| # | Component | Current location (SD unless noted) | Size | Already on UFS? |
|---|---|---|---|---|
| 1 | **RPCS3 emulator** (binary/AppImage + launch script) | `/storage/roms/bios/rpcs3/` | ~150–250 MB | ❌ |
| 2 | **RPCS3 global config / VFS** (`config.yml`, vfs mapping) | `/storage/roms/bios/rpcs3/` | small | ❌ |
| 3 | **`dev_flash`** PS3 firmware — required by ALL titles | `/storage/roms/bios/rpcs3/dev_flash` | 189 MB | ❌ |
| 4 | **Game data** `dev_hdd0/game/<ID>` (+ updates/DLC) | symlink → `/storage/etk-internal/dev_hdd0_game/<ID>` | 1.79 GB | ✅ (ETK) |
| 5 | **License** `.rap` `dev_hdd0/home/00000001/exdata/*<ID>*.rap` | `…/rpcs3/dev_hdd0/home/00000001/exdata` | 16 B each | ❌ |
| 6 | **Save data** `dev_hdd0/home/00000001/savedata/<ID>-*` | `…/dev_hdd0/home/00000001/savedata` | small | ❌ |
| 7 | **Per-game config** `custom_configs/config_<ID>.yml` | `…/rpcs3/custom_configs` | 8 KB | ❌ |
| 8 | **Shader vault** | `/storage/.cache/mesa_shader_cache` → etk-internal | 103 MB | ✅ (ETK) |
| 9 | **ES launch pointer** `<Game>.psn` (contains just the GAMEID, e.g. `NPUA80075`) + **`gamelist.xml`** | `/storage/roms/ps3/` | tiny | ❌ |
| 10 | (Disc titles only) **`.iso`** | `/storage/roms/ps3/<Game>.iso` | up to 19.45 GB | ❌ |

**Launch dependency chain (and where it breaks when the SD is out):**
`EmulationStation` reads `ps3/gamelist.xml` + `<Game>.psn` *(SD — breaks)* → spawns the **RPCS3 AppImage** *(SD — breaks)* → RPCS3 opens its data root `/storage/roms/bios/rpcs3/` *(SD — breaks)*: `config.yml`, `dev_flash`, `dev_hdd0/game/<ID>` *(→ UFS via symlink, survives)*, `exdata`, `savedata`, `custom_configs` *(SD — break)* → shader vault *(UFS, survives)*.

So **6 of 10 components still live only on the SD.** Game data and shaders are the big ones and are already handled; the remainder are small but **load-bearing** — without `dev_flash`, the emulator, the global config, the `.rap`, and the gamelist pointer, the title will not appear or will not boot.

## 4. THE TWO ARCHITECTURES FOR SD-FREE

**Approach A — Relocate the RPCS3 root to UFS.** Move `/storage/roms/bios/rpcs3/` (emulator + `dev_flash` + `config.yml` + `dev_hdd0` skeleton + `exdata` + `savedata` + `custom_configs`) onto UFS (e.g. `/storage/etk-internal/rpcs3/`) and repoint the RPCS3 launcher + VFS at the UFS path. Game `.iso`/data and shaders stay as today (already UFS via symlink). Still needs the ES gamelist/`.psn` reachable — either also relocated to a UFS-backed `ps3/` dir, or launch RPCS3 directly bypassing ES.
*Pro:* surgical, per-game; small UFS cost for PSN titles. *Con:* RPCS3 path assumptions and the ES `/storage/roms/ps3` dependency need rework.

**Approach B — Back `/storage/roms` itself with UFS when the SD is absent.** Provide a UFS-resident `roms/` (full or a `ps3`-only subset) that mounts/overlays at `/storage/roms` when no SD is detected, so all existing paths resolve unchanged. This is the "move the whole rig off the SD" ambition from [[InternalStorageManagerDossier]].
*Pro:* zero path rework — everything Just Works. *Con:* needs the **entire** required tree duplicated on UFS and a much larger STORAGE partition.

Either way, **the data must be UFS-resident and the paths must resolve without the SD mount.** ETK already proves the symlink-relocation mechanic (Approach A is the natural extension of what `etk-internal` does today).

## 5. SPACE MATH — THE HARD CONSTRAINT

Internal STORAGE (`sda25`) = **6.3 GB total, 3.2 GB free** today.

One PSN title (GT5P), full SD-free footprint on UFS:
- Game data 1.79 GB + shaders 0.10 GB *(both already there, in the 2.58 GB etk-internal)*
- `dev_flash` 0.19 GB + RPCS3 emulator+config ~0.20 GB + `.rap`/saves/config + gamelist/pointer <0.05 GB
- **New UFS needed beyond what's already parked: ~0.45 GB.** → **GT5P alone can *just* fit SD-free today** (3.2 GB free), with almost no margin.

But this does **not** generalize:
- **ISO/disc titles are impossible** — GT5's ISO is 19.45 GB; the whole STORAGE partition is 6.3 GB.
- **Multiple games** blow the budget fast (RPCS3 `dev_hdd0` across the current library = 31.8 GB).
- No room for shader growth, saves, or new installs.

**Therefore: a robust, multi-game (and any-ISO) SD-free rig requires growing STORAGE to ~40–60 GB**, carved from the Android `userdata` partition (`sda23`). That repartition is the blocked operation: it needs either `fastboot` (proven dead — [[FastbootRevertConclusion]]) or a destructive reflash/repartition rebuild, **which the operator has deferred.** A single small PSN title is the only SD-free play possible *without* repartitioning.

## 6. PROPOSED IMPLEMENTATION — the "SD-FREE" tier of the Internal Storage Manager

Extends the existing tier model (`SHADERS` → `SHADERS+GAME` → `SD-FREE`). For a chosen PSN game, with the SD still inserted (migration is done while both disks are present):

1. **Relocate the RPCS3 root** to `/storage/etk-internal/rpcs3/`: copy `dev_flash`, `config.yml`/VFS, the `dev_hdd0` skeleton (`home/00000001/{exdata,savedata}`), and `custom_configs/config_<ID>.yml`. Symlink back from the SD tree (mirrors the existing `dev_hdd0_game` pattern) so SD-present operation is unchanged.
2. **Stage the RPCS3 emulator binary** on UFS and point the Tools/launch entry at the UFS copy.
3. **Make the ES launch path SD-independent** for the chosen title: a UFS-backed `ps3/` holding `<Game>.psn` (just the GAMEID) + a minimal `gamelist.xml` entry, surfaced at `/storage/roms/ps3` when the SD is absent (bind/overlay), **or** a direct-launch shim that starts RPCS3 with `<ID>` bypassing ES.
4. **Pre-flight space gate:** refuse if `dev_flash + emulator + game + shaders + overhead` exceeds STORAGE free; report the shortfall and point at the repartition precondition. Disc/ISO titles: hard-block with "needs larger STORAGE."
5. **Extend `ROLLBACK.sh`** to cover the new RPCS3-root relocation (it already handles game-data splits).
6. **Idempotent + repair:** like `install.sh`, re-running re-verifies symlinks and re-injects anything the boot wipe or a resync disturbed.

## 7. VERIFICATION PLAN

1. Migrate GT5P (NPUA80075) via the SD-FREE tier with the SD inserted; confirm it still launches normally (SD present).
2. **Power off, physically remove the SD card, boot.** Confirm: rig boots to ROCKNIX (internal already wins boot — confirmed), the ETK Tools entry is present, and GT5P appears/launches.
3. Confirm in-game: shaders compile and **credit** to the UFS vault, saves write, ETK HUD/telemetry resolve `TARGET_ID` correctly with no SD.
4. Re-insert SD; confirm no split-brain or double-counting (the label-collision caveat from [[InstallToInternalRecovery]]).

## 8. RISKS / OPEN QUESTIONS

- **RPCS3 path assumptions:** does the build resolve its data root purely from the launch CWD/arg, or are there absolute `/storage/roms/bios/rpcs3` references baked into `config.yml`/VFS? Audit before relocating (§4 probe found no explicit VFS overrides in `config.yml`, so it likely uses the emulator-dir default — verify).
- **ES with no `/storage/roms`:** does EmulationStation tolerate the SD's `roms` tree being absent/empty, or does it need the bind/overlay in place *before* it scans? Determines whether Approach B's overlay must mount pre-ES.
- **`/flash` is 92% full and read-only** — not a target; all relocation goes to `/storage` (sda25).
- **Boot already comes from internal** (cmdline `boot=LABEL=ROCKNIX`), so SD removal does not threaten boot itself — only the emulation payload. Good: SD-free boot is already proven; this is purely a data-locality problem.
- **The partition-size ceiling is the real gate** for anything beyond one small PSN title — and that returns to the deferred destructive repartition.

## 9. CROSS-REFERENCES

- [[InternalStorageManagerDossier]] — the tiered manager this extends (SD-FREE tier); Phase 0 was gated on fastboot.
- [[FastbootRevertConclusion]] / `dossiers/FastbootRevertChecklist.md` — why growing STORAGE needs a destructive repartition (fastboot is dead).
- [[InstallToInternalRecovery]] — internal-install internals, the label collision, and the existing UFS relocation (`etk-internal`, `.presplit`, `ROLLBACK.sh`).
