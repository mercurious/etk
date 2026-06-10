# SESSION HANDOFF — 2026-06-09

**Status:** Major bug fixed + deployed (uncommitted); rig healthy on the new card; several threads parked cleanly.
**Rig:** Retroid Flip 2 (SM8250), Rocknix on a fresh **1 TB Lexar V30 A2** SD ("card #2"), replacing the retired 64 GB dev card. Old working reference card = SanDisk Ultra **A1** ("card #1").

---

## 0. ONE-PARAGRAPH SUMMARY

What looked like "card #2 is killing previously-working games" turned out to be **ETK's own PKG installer truncating its installs** — it killed RPCS3 the instant `EBOOT.BIN` appeared, mid-extraction, and the race resolved differently on the faster A2 card. Found via code read + zero-byte-file evidence, fixed (`_run_install` now waits for a real finish), and **proven on-rig** (LittleBigPlanet 391 MB → 2.0 GB, launches). The card is essentially exonerated. Along the way: returned the internal UFS partition to Android, ran a cross-platform (aPS3e) comparison, falsified an "I/O-compound tuning" hypothesis, shipped two HUD QoL tweaks, and stood up cross-device savedata sync via Syncthing.

---

## 1. THE HEADLINE FIX — PKG installer premature kill

- **Bug:** `bin/etk_pitstop.py` `_run_install` broke its wait loop the moment `EBOOT.BIN` existed (`if os.path.exists(eboot): break`) then `_kill_rpcs3()`. `EBOOT.BIN` is rarely the last file extracted → RPCS3 killed mid-extraction → **truncated installs** (missing / zero-byte late files). The "RPCS3 self-exits after install" comment was contradicted by the code.
- **Why card-specific:** the kill races RPCS3's extraction; on the faster A2 card the timing landed differently than on the slower dev card / A1 / UFS, so installs truncated on card #2 and not elsewhere.
- **Evidence:** GT6 had 7 zero-byte files, LBP 5; RR7 only 33 files (killed between files). Each game then failed *differently* at runtime (GT5P/GT6 `CELL_ESRCH`, RR7 `CELL_ENOTMOUNTED /dev_bdvd`, LBP `CELL_ENOENT bootflag.dat`) — heterogeneous symptoms of one root cause.
- **Fix (deployed to rig + working tree, UNCOMMITTED):** wait for a true finish — RPCS3 self-exit, with a game-folder-size-stable (20 s) fallback + the existing 600 s cap — then kill.
- **Proof:** LBP reinstall via the fixed automater = **391 MB/61 files/5 zero-byte → 2.0 GB/196 files/0 zero-byte**, launches. GT6 runs nicely on the A2 card after a clean install. Operator also confirms GT5P "launches much better when installed fully."
- **Watch item:** GT5P showed no zero-byte files (truncation by *missing whole files*, invisible to a zero-byte check) — its `CELL_ESRCH` was likely the incomplete install too, not a separate emulation bug.

## 2. CARD #2 — likely innocent

Same games run on card #1 (A1), internal UFS, and aPS3e; failed only on card #2 (A2). Every RPCS3-config and cache lever tried (Clocks scale, Thread Scheduler, HLE lwmutex, PPU Threads, PPU/SPU cache clear) failed — because the cause wasn't config or the card, it was the truncated install. New SanDisk A1 cards inbound + a planned different-brand A2 can still A/B the "is any A2 quirk real?" question, but it's now low-priority.

## 3. INVESTIGATION DEAD-ENDS (documented so they're not re-run)

- **Storage-I/O thread-race / "I/O-compound" tuning model** — proposed then **FALSIFIED**: forcing slow init (cache clear) did NOT fix GT5P; UFS (fastest I/O) ran it fine; only one card failed. The "tire-compound" tuning metaphor rests on at most one quirky card — do not build on it.
- **"aPS3e works because it always recompiles (slow init)"** — RETRACTED. aPS3e works because its own installer produced a *complete* install; its constant recompile is an unrelated (probable) cache bug that just costs launch time. aPS3e changelog is in the author's native language → behavior read from observation, not intent.
- Pattern lesson (logged to memory `feedback_hardware_blame_tendency`): repeatedly reached for hardware causes (bad cable, bad card) when the bug was our own code; and over-claimed a "fix" from mid-compile log reads before operator screen confirmation. Operator's big-picture intuition caught both.

## 4. UFS REPARTITION (the original task) — current state

- **Done:** the internal `ROCKNIX`(2 GB)+`STORAGE`(6.7 GB) leftover partitions were deleted and `userdata` grown back (96 → 104.5 GiB); Android auto-resized its f2fs on boot, **zero data loss**. Split-brain permanently gone; rig boots the SD natively. (See memory `project_installtointernal_recovery`.)
- **Parked plan:** a fresh `installtointernal` sized **Balanced — shrink userdata to 40 GB → userdata 40 / ROCKNIX 2 / STORAGE ~62.5 GiB** to host the 6-game test suite + shaders on UFS for a boot-speed/shader-I/O experiment + aPS3e A/B. ON HOLD until all games are installed in both OSes for byte-accurate sizing. Real footprint so far: GT5 ISO 19.4 GB dominates; 4-GT set ≈ 23 GB. (Memory `project_ufs_experiment_plan`.) Gotcha: games must be *physically placed* on STORAGE, not SD-bind, or the test measures nothing.

## 5. 0.1.5 RELEASE PATH (PADDOCK 0.5.0 stays parked for legal review)

Confirmed clean: PADDOCK was purely additive — it never touched `_run_install`. `v0.1.4` tag has the buggy installer + the `_dir_size` helper the fix uses, all identical to HEAD.
```
git checkout -b release/0.1.5 v0.1.4
git cherry-pick <installer-fix-sha>     # applies cleanly; _run_install identical at v0.1.4
git cherry-pick <hud-qol-sha>           # mango_bridge.sh also PADDOCK-independent
git tag v0.1.5 && git push --tags       # GitHub Release; zero PADDOCK code
```
The same fix also lives on `main` for the eventual 0.5.0. **0.1.5 candidate set:** (1) installer fix, (2) vault-size GB abbreviation, (3) `VAULT:ERROR→LOADING`.

## 6. HUD QoL (deployed to rig + working tree, UNCOMMITTED) — `bin/mango_bridge.sh`

- Vault size abbreviates MB→GB ≥1 GB (`1228MB → 1.1GB`), mirroring the shader-count `k` style; locked-format header updated to `XX[MB|GB]`.
- `VAULT:ERROR → VAULT:LOADING` — the empty/0 read at emu fire-up is a startup race (Sentry/`vault_d` not ready), not a fault; a stuck LOADING is the real signal. Sync detail confirmed by reading `bin/vault_d.sh` (the Accountant publishes count/new/size + `vault_stat.txt=READY`; the HUD does its own `du` for size).

## 7. CROSS-DEVICE SAVEDATA SYNC (Syncthing) — set up this session

PS3-standard saves (`dev_hdd0/home/00000001/savedata/`) are emulator-portable. Topology = always-on **macOS hub** + Flip2-Rocknix + Flip2-Android(aPS3e) spokes (the two OSes never online together → hub relays). Mac hub holds a **keep-10 versioned** backup. **Folder ID = `x6hh6-yt9u5`** (adopted Android's auto-minted ID cluster-wide after a mismatch — Android setup left untouched). `*.protune.bak.*` ignored to keep ETK's local save-backups out of the sync. **Rocknix → hub seeded (canonical); Android pulled 100%** and is finishing a Receive-Only → Revert mirror, then flips to Send & Receive. Full detail: memory `project_save_sync_syncthing`. **Pending:** repoint Rocknix's Syncthing folder from `bf65b-9ggqv` to `x6hh6-yt9u5` over SSH next time it's booted (`syncthing cli --home /storage/.config/syncthing …`).

---

## 8. OUTSTANDING / NEXT ACTIONS

1. **Commit + push** the installer fix and HUD QoL (messages prepared; operator handling).
2. **Cut 0.1.5** from `v0.1.4` per §5 when ready.
3. **Save-sync finish:** Android Revert → Send & Receive; repoint Rocknix node to `x6hh6-yt9u5` (SSH).
4. **UFS experiment:** load all 6 games in both OSes → re-measure → run `installtointernal` (shrink to 40 GB) → physically place games on STORAGE → boot-speed/shader-I/O A/B.
5. **(Optional)** confirm GT5P with a clean full reinstall; A/B the new A1 cards vs the A2 for any genuine card-class effect.
6. **0.5.0 PADDOCK** remains gated on legal review (shader-vault distribution premise).

## 9. KEY MEMORY FILES (background)

`project_card2_multigame` (root cause + fix) · `project_gt5p_loadingbar_crash` (the long crash hunt) · `project_io_sensitized_tuning` (falsified compound model) · `project_ufs_experiment_plan` · `project_installtointernal_recovery` · `project_save_sync_syncthing` · `feedback_hardware_blame_tendency`.
