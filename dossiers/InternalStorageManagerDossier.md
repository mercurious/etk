# DOSSIER: ETK Internal-Storage Manager — Tiered UFS Migration & SD-Free Play

**Target files (primary):** new `bin/etk_internalize.sh` (or Pitstop TOOLS action) — the userspace migration manager
**Supporting:** `install.sh` (already internal-aware vault sync), generalize `/storage/etk-internal/ROLLBACK.sh`, `scripts/env.sh` path vars
**Explicitly NOT touched:** ROCKNIX `/usr/bin/installtointernal` parted/boot-repoint logic (decision below)
**Roadmap slot:** post-0.1.5 internal-storage maturation
**Author:** pre-implementation brief for Claude Code
**Status:** PROPOSAL — nothing herein is implemented. Design + on-rig ground truth (SM8250.local, official 20260601, 2026-06-02). Treat as planning.

---

## 0. ONE-PARAGRAPH SUMMARY

ROCKNIX's stock `installtointernal` resizes Android userdata and creates `ROCKNIX (2 GB boot) + STORAGE (ext4, rest)`, but it **excludes all games and saves from the copy** and then tells you to "remove your SD card" — a promise it doesn't keep for a boot-card rig. ETK's job is the **userspace layer on top**: a guided manager that physically places a chosen game's *complete* payload — game data, shader vault, disc caches, savedata, trophies, **licenses (exdata)**, the `.psn` launcher, and per-game config — onto the internal UFS partition so that game **runs with the SD removed**, while the rest of the library stays on the card. We do **not** fork the dangerous parted/boot logic; we lean on the official tool (sized large) and build the migration/rollback on top.

---

## 1. DECISIONS LOCKED (operator, 2026-06-02)

1. **Partition model = one STORAGE + tiered placement.** Do NOT fork `installtointernal`'s parted/boot-repoint logic. Use the official tool (sized large) + an ETK userspace manager. Safest; matches the existing `etk-internal` pattern; no per-SoC parted maintenance.
2. **SD-free scope = full, for chosen games.** Migrate everything a selected game needs to boot with the card out. (Other games stay on SD and require it.)
3. **Restore the revert path FIRST.** Prove fastboot works (data cable / Windows PC) before any destructive repartition — there is no "Uninstall ROCKNIX" on this U-Boot device (see [[project_installtointernal_recovery]]).

---

## 2. WHAT THE OFFICIAL TOOL DOES (GROUND TRUTH — read all 234 lines)

| Step | Behavior | Consequence for ETK |
|---|---|---|
| Sizing prompt | Shrink Android userdata to a chosen GB (floor 1 GB; forces ≥8 GB freed) | STORAGE can be big — operator just has to shrink Android more. Current rig = 6.3 GB (Android kept large). userdata still 96 GB → ~80 GB reclaimable. |
| Layout | `userdata | ROCKNIX 2 GB fat32 | STORAGE ext4 (to 100%)` | **No app-specific partitions.** STORAGE *is* `/storage`. |
| Copy prompt | Copies `/storage` **excluding `roms`, `games-internal`, `games-external`** | **Games + saves never migrate.** This is the core gap. |
| Finish | Prints "you can now reboot and remove your SD card" | **Misleading** — boot-card games/saves are still SD-only. |
| Guard | Refuses if `ROCKNIX`/`STORAGE` partitions already exist | A bigger partition = a **destructive repartition** (revert first). |

---

## 3. CURRENT RIG STATE & THE SD-BIND INCOMPATIBILITY (CRITICAL)

The rig is in the **recovered hybrid** state from 2026-06-01: booting internal (`sda25` STORAGE, 6.3 GB, **already 50 % full**), with the SD (`mmcblk0p2`, 235 GB) **bind-mounted over** `/storage/{games-internal,roms,games-external}` by `etk-sd-rebind.service`.

- `/storage/etk-internal/` (on STORAGE) holds `vault/` (119 MB), `dev_hdd0_game/` (2.5 GB), `dev_hdd1/caches`, `ROLLBACK.sh`. Live RPCS3 paths **symlink into it**.
- **Saves are NOT migrated.** `dev_hdd0/home/{savedata,trophy}` and `dev_hdd0/exdata` (licenses) + `localusername` resolve onto the **SD**. Footprint is tiny: **`home` = 25.4 MB**.
- `.psn` launchers (`/storage/roms/ps3/*.psn`, 9 bytes each, hold the title ID) are **SD-bound** too.

**The incompatibility:** in this hybrid, the canonical paths *are* the SD, and `ROLLBACK.sh` leaves **symlinks at the SD paths pointing to internal**. Pull the SD and the symlinks themselves disappear — data may be on UFS but nothing resolves to it. **SD-free cannot be reached by symlink-juggling on the bind-mounted hybrid.** It requires a clean internal install where `games-internal/roms` is *genuinely* internal and chosen games are *physically placed* there (real files), with the SD demoted to a supplementary `games-external` library.

This is why decision #3 (revert first) is on the critical path, not just a safety net: the correct architecture runs *through* a clean reinstall.

---

## 4. TARGET ARCHITECTURE

Clean internal install (big STORAGE) → `games-internal/roms` lives natively on UFS → ETK manager **copies** a chosen game's full payload from SD into the internal tree and registers it. No symlinks-on-SD. The SD holds only non-migrated (`games-external`) titles.

### 4.1 Per-game SD-free migration manifest
Everything that must be internal for game `<ID>` to boot with the card out:

| Piece | Source (SD) | Size | Why needed for SD-free |
|---|---|---|---|
| Game install data | `dev_hdd0/game/<ID>/` | GBs (dominant) | the game itself |
| Shader vault (Mesa cache) | `vault/<chip>/<ID>/` → `mesa_shader_cache` | 100s MB | the saturated-cache perf win |
| Disc/PPU caches | `dev_hdd1/caches/<ID>*` | MBs | avoids recompile/stutter |
| Savedata | `dev_hdd0/home/00000001/savedata/<ID>*` | tiny (≪25 MB) | **goal #3** — progress |
| Trophies | `dev_hdd0/home/00000001/trophy/<ID>*` | tiny | progress |
| **Licenses** | `dev_hdd0/exdata/*<ID>*.rap` | tiny | **PSN titles won't run without these** |
| User account | `dev_hdd0/home/00000001/localusername` | 4 B (shared, once) | RPCS3 user identity |
| ES launcher | `roms/ps3/<Game>.psn` (+ gamelist entry) | 9 B | so it appears/launches in the carousel |
| Per-game config | `custom_configs/config_<ID>.yml` | 8 KB | already on internal `.config` post-install |

The save/license/account/launcher set is **trivially small (<26 MB total)** — the only heavy item is game data. So "full SD-free" is cheap on top of the game data already being moved.

### 4.2 The manager (`etk_internalize.sh`)
- **Tier select** (gamepad/CLI): `SHADERS` (vault only) · `SHADERS+GAME` · `SD-FREE` (full manifest above).
- **Pre-flight**: compute required bytes for the chosen tier vs. STORAGE free; refuse with a clear "you need an N-GB STORAGE; reinstall larger" if it won't fit (the official tool's sizing is the lever).
- **Place**: copy (not symlink) the manifest pieces into the internal `games-internal/roms` tree; verify checksums.
- **Register**: ensure ES launcher + RPCS3 see the internal copy.
- **Rollback**: generalize the existing `ROLLBACK.sh` (today it only de-symlinks vault + restores `dev_hdd0/game/<ID>.presplit` for 2 hardcoded IDs) into a manifest-driven, per-game, reversible move with a manifest log.
- **SD-free readiness check**: a dry "with the SD out, does `<ID>` have every manifest piece on internal?" verifier — the honest gate before telling the operator they can pull the card.

---

## 5. PHASED WORKFLOW

- **Phase 0 — Restore revert path (PREREQ, BLOCKING).** Prove `fastboot` enumerates and `fastboot erase ROCKNIX/STORAGE` works (data cable / Windows PC). Blocked 2026-06-01 by suspected charge-only cable. *No destructive repartition until this is green.*
- **Phase 1 — Clean revert.** Fastboot-erase internal partitions → boot SD natively → pristine baseline.
- **Phase 2 — Reinstall large.** Re-run stock `installtointernal`, shrinking Android enough to give STORAGE room for the target games' full SD-free footprint (game data + vault dominate).
- **Phase 3 — ETK manager.** Build/run `etk_internalize.sh` per §4.2. Validate incrementally (one small game first — GT HD Concept, 11 MB shader set — then GT5P).
- **Throughout**: after any `/storage/.config` divergence, diff internal vs SD and restore (the MangoHUD/gamepad regression from last time — see [[project_installtointernal_recovery]]).

---

## 6. RISKS

1. **No clean revert until Phase 0 succeeds** — physical-cable blocker, off-rig. Hard gate.
2. **Label collision persists** (internal `ROCKNIX`/`STORAGE` win boot over SD). A clean reinstall keeps this; acceptable since SD-free is the goal (we *want* internal to win), but the SD can no longer be booted standalone without fastboot.
3. **`.config` divergence** between internal and SD copies — re-bit us last time (lost MangoHUD overlay + gamepad). Manager should snapshot/restore the known-good `.config` pieces.
4. **Destructive repartition** loses current `etk-internal` contents — but they're re-syncable from the host (`install.sh`) and host `.bak`.
5. **Crash-stability is unaffected** — internal storage is a smoothness/responsiveness win (operator-confirmed, [[project_internal_storage_tierb]]) but does NOT fix track panic / R3 (see [[project_race_baseline_status]]). Don't conflate.

---

## 7. OPEN ITEMS / NEXT ACTIONS

- **[Phase 0]** Source a confirmed USB-C **data** cable or a Windows PC; verify fastboot. Everything downstream waits on this.
- Decide manager surface: standalone `bin/etk_internalize.sh` vs. a Pitstop **TOOLS** tab action (reuses gamepad UI; cf. [[project_pitstop_on_rig_repair]]).
- Confirm exdata `.rap` naming → title-ID mapping so the manager can select the right licenses per game (probe returned exdata dir present; contents not yet enumerated).
- Decide STORAGE sizing target: how many games does the operator want SD-free at once? (drives the Android shrink in Phase 2).
- Consider whether the manager should also write the ES gamelist entry or rely on ROCKNIX rescan.
