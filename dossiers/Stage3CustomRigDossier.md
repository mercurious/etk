# Stage III Custom Rig Dossier — GT-Specialized SM8250

**Date:** 2026-06-11 · **Status:** Mechanisms identified · experiments armed · build plan ready
**Mission (Fable's Challenge):** advance GT5P/GT-suite race stability on the Flip2 toward the edge of console quality, building a rig specialized for Gran Turismo on SM8250 — other games' compatibility expendable. Crash prevention outranks FPS and audio.

---

## 0. MEMO (TL;DR)

The crash data resolves into **two distinct mechanisms**, each with a named, attackable cause:

1. **Silent freezes (dominant, ~50% of crashes)** are *emulator-side and compile-coupled* — 80% of them strike while new shaders/SPU blocks are compiling (vs ~39% baseline). Two concrete defects were identified in RPCS3's ARM64 SPU JIT (a GHC-convention x30 clobber and zero icache maintenance on JIT publication — 9 identical Android tombstones prove the wild branch), plus a now-fixed upstream GT5 memory leak. **Attack: leak-fixed builds (deployed), fork-level JIT fixes, upstream report, vault saturation.**
2. **Adreno fence timeouts (~30%)** are *driver/kernel-layer and compile-independent* — heavy track sections blow drm/msm's 500 ms hangcheck budget; recovery is a full GPU reset and RPCS3 treats the resulting `VK_ERROR_DEVICE_LOST` as unconditionally fatal (no recovery path exists in any RPCS3 lineage). **Attack: GPU clock pinning, TU_DEBUG isolation ladder, a one-line hangcheck kernel patch in a custom Rocknix image.**

**OS verdict: Rocknix is the Stage III platform; Android/aPS3e is the control rig and GPU-resilience benchmark.** Both now run the same emulator generation and the same Turnip 26.1.2, so the comparison is finally clean.

Thermals are exonerated (identical temps across outcomes). The 8 GB RAM ceiling is real but manageable post-leak-fix. No unmovable class limit has been hit: every observed crash class traces to fixable software, not silicon.

---

## 1. EVIDENCE BASE (what was measured, 2026-06-10/11)

### 1.1 Telemetry corpus
- **SD #1 ledger: 1,047 sessions** (May 21 → Jun 10, nightlies), GT5P n=640 non-aborted.
- **SD #2 ledger: 78 sessions** (Jun 8–10, official 20260601).
- **Android dropbox: 36 native crash records** (Jun 10), 9 of them one identical signature.

### 1.2 The two-mechanism split (GT5P, n=640)

| | CLEAN (162) | ADRENO (311) | SILENT (146) |
|---|---|---|---|
| Sessions with new-shader harvest | 40% | 38% | **80%** |
| Median new shaders at end | 0 | 0 | **54** |
| Median peak RAM (MB) | 7362 | 7246 | 5846 |
| Median duration (s) | 234 | 169 | 116 |
| Median peak temp (°C) | 76 | 76 | 74 |

Readings:
- **SILENT is compile-coupled** (80% vs ~39% baseline) — it strikes when fresh content is being compiled (new track section ⇒ new shaders *and* new SPU blocks, same moments). Explains the operator lore "saturated vault races better."
- **ADRENO is not compile-coupled** (38% = baseline). It is a steady-state GPU hazard at specific heavy draw patterns. Fence-at-crash values 6k–87k = mid-race.
- **Thermals exonerated**: identical temps across outcomes, all under the 83 °C alarm.
- **Not simple OOM**: silent crashes die at *lower* median RAM than clean sessions — but everything brushes 7.5 GB on an 8 GB device (see leak, §2.2).
- Kernel panics exist but are rare (RR7 ×2, GT5 ×1 on Jun 10) and currently leave **zero trace** (no pstore/ramoops on this dtb).

---

## 2. MECHANISM VERDICTS

### 2.1 Silent class → ARM64 SPU JIT wild branch (NOVEL, reportable upstream)

Nine identical Android tombstones (fork 1.19a, GT titles, Jun 10): thread `SPU[0x0000200]`, SIGBUS BUS_ADRALN, **pc = `0x0011121314151617`**, frame #01 = `0x1011121314151617`. Those two values are the adjacent 64-bit lanes of RPCS3's **SPU shuffle-control identity constant** (CBD/CHD/CWD/CDD masks — `SPUInterpreter.cpp:780`, `SPULLVMRecompiler.cpp:6654`). The JIT consumed a 16-byte vector spill slot holding a shuffle mask as a `{return address, link register}` pair.

Code audit (base = upstream bcd9663, Apr 2026) found **two real defects, neither fixed upstream as of Jun 10**:

1. **PRIME — GHC calling-convention x30/stack discipline (AArch64).** GHC CC has *no* callee-saved registers including x30; SPU chunks run "stackless" over a shared scratchpad (`use_stack_frames=false`, `SPULLVMRecompiler.cpp:1594`). The repair pass (`Emu/CPU/Backends/AArch64/AArch64JIT.cpp`) is self-admittedly heuristic — `clobbers_x30 = instruction_count > 32` (lines 99–103), "WARNING: This can corrupt the call" (392–395) — and its inline-asm x30 reload before `ret` is not allocator-proof. Under GT's SHUFB-heavy SPURS register pressure, a q-register spill slot can alias an `{x29,x30}` save slot → `ldp`/`ret` through mask data. Fits all 9/9 identical signatures and the first-drive-of-section trigger.
2. **CONFIRMED LATENT — zero icache maintenance on JIT publication.** `finalizeMemory` is no-op'd (`JITLLVM.cpp:321,383`); ubertrampolines are memcpy'd raw (`SPUCommonRecompiler.cpp:1363–1444`); runtime patch sites use only `ISB; DSB ISH` (`:2057, :2197`) which does **not** clean D→I. No `__builtin___clear_cache` anywhere in the tree. Fails precisely when a thread executes brand-new code right after compile.

Ruled out by audit: dispatch-table tearing (entries are CAS'd; u128 patches use LDAXP/STLXP release; reader is single-copy-atomic `ldr`+`br`).

**Same code runs on Rocknix** → same wild branch = traceless process death = `RECOVERY:Silent`. The Linux core-dump capture (deployed Jun 11, §5.1) will confirm.

Supporting upstream context: #18852 (compile-burst starvation freeze, ~85%→0% warm-cache), #18828 (open: RSX/SPURS non-deterministic freeze on *exactly* our stack — Rocknix/aarch64/Adreno/Turnip), #16388 (closed unexplained ARM64 SPU memory violation).

### 2.2 Silent class, second contributor → GT5 memory leak (FIXED upstream)

Upstream #18819: ~300 MB leaked per car model viewed, regression at build 18055, *"emulator closes due to lack of available RAM, **without reporting an error**"* — verbatim our silent signature. **Fixed by PR #18844 ("vk: Autogrow descriptor pools based on demand"), merged 2026-06-05.**
- Official 20260601 (RPCS3 19291) — leak present.
- **Nightly 20260610 (RPCS3 0.0.41-19444-62d32ab4) — leak fixed.** Both cards now run it.
- aPS3e main4 base (Apr 13) — leak present → **cherry-pick #18844 or rebase** is a fork work order.
- First leak-fixed GT5P session set (Jun 10, n≈13): **0 silent crashes** (4 clean / 3 Adreno during cold-vault resaturation). Small n; the soak decides.

### 2.3 Adreno class → drm/msm hangcheck vs heavy submissions

Verified mechanics (mainline `msm_gpu.h`/`msm_gpu.c`, checked 2026-06-11):
- Watchdog: `DRM_MSM_HANGCHECK_DEFAULT_PERIOD 500` ms; a6xx gets progress detection (`a6xx_progress`: CP rptr/IB sampling) with `DRM_MSM_HANGCHECK_PROGRESS_RETRIES 3` → a slow-but-alive submission gets ≈2 s max, then **full GPU reset** (`a6xx_recover`: GBIF halt, GMU power cycle — no partial reset on a6xx).
- Guilt is attributed per-submitqueue; innocent clients' submits replay, but the guilty queue is poisoned → Turnip returns `VK_ERROR_DEVICE_LOST` to RPCS3.
- **RPCS3 treats device-loss as unconditionally fatal** (`vkutils/shared.cpp die_with_error` → throw; no recovery path, no setting, no fork that survives it — verified against upstream code and PR history). Avoidance is the only strategy.
- **No runtime hangcheck tunable exists** — raising it is a one-line kernel patch (`msm_gpu.h`) in a custom image.

### 2.4 The OS asymmetry (measured on the rig)

| GPU layer | Android (KGSL) | Rocknix (drm/msm mainline) |
|---|---|---|
| Preemption | **ON** (250 events counted) | OFF on a650 (single ring) |
| Fault tolerance | `ft_policy=0xC2` — per-context IB skip/**replay** | Full-GPU reset, guilty queue poisoned |
| Long-submission grace | `ft_long_ib_detect=1` | 500 ms × (1+3) progress retries |
| GPU clocks | Pinned 587–670 MHz | devfreq `simple_ondemand` 305–800 MHz (ETK pins `performance` during sessions) |
| Crash forensics | dropbox tombstones w/ backtraces (cracked the SPU case) | nothing (no cores, no ramoops) — **fixed 2026-06-11 for cores** |

KGSL absorbs GPU hiccups that are fatal on Linux. The Stage III custom image's job is to close that gap from the Linux side (§4, T3).

---

## 3. OS / RUNTIME SELECTION

**Criteria:** (a) crash-mechanism attackability, (b) instrumentation depth, (c) build/iteration loop, (d) I/O + driver parity, (e) ecosystem leverage (ETK).

**Verdict — Rocknix primary, Android control:**
- Every identified mechanism is *more attackable* on Rocknix: kernel patchable (hangcheck/ramoops/dtb), Mesa swappable (TU_DEBUG today, custom Turnip tomorrow), emulator replaceable (AppImage), middleware already ours (ETK sentry/telemetry/vault). Android can change exactly one layer (the APK; Turnip via adrenotools) — kernel and OS are sealed.
- Android remains: (1) the GPU-resilience benchmark (KGSL absorbs what drm/msm fatals — any Linux-side hang fix can be sanity-checked against Android's behavior on the same section), (2) the SPU-bug tombstone factory (its dropbox gives backtraces for free), (3) UFS-I/O reference until the Rocknix UFS experiment runs ([[project_ufs_experiment_plan]]).
- Parity achieved 2026-06-10/11: both OSes on RPCS3 0.0.4x + Turnip 26.1.2. Differences are now attributable to kernel driver + OS, not emulator/driver versions.

---

## 4. THE STAGE III BUILD PLAN (tiered)

### T0 — Config/env experiments (DEPLOYED 2026-06-11, zero build)
1. ✅ **Shader Mode A/B**: GT5P + GT HD switched `Async Recompiler with Shader Interpreter` → `Async Recompiler (multi-threaded)` (ledger rows written; backups `.stage3ab.bak`). Hypothesis: interpreter über-shader is an Adreno-class amplifier during harvest. Watch `RECOVERY:Adreno` rate in harvest-heavy sessions.
2. ✅ **`MESA_SHADER_CACHE_MAX_SIZE=10G`** (`profile.d/098-etk-stage3`): Mesa's default disk cache cap is **1 GB with LRU eviction** — the vault crossed ~1.1–1.2 GB and was plausibly evicting its own shaders (would explain stability erosion between sessions). New 26.1.2 vault will never self-evict.
3. ✅ **Core-dump capture**: `ulimit -c unlimited` + `core_pattern → /storage/cores/` (keep 2). First silent-crash core on Linux confirms/refutes §2.1 on this OS.
4. ⏭ **GPU clock pin**: `echo 800000000 > /sys/class/devfreq/3d00000.gpu/min_freq` during sessions (ETK thermal_d already sets governor `performance`; pin min as well — eliminates GMU DCVS transitions *and* the "heavy section arrives at low clock → 500 ms budget blown" path). Candidate for `thermal_d.sh` RACE mode.
5. ⏭ **TU_DEBUG isolation ladder** (per-session, profile.d pattern proven by `tools/boost_test.sh`): `nolrz` → `noubwc` → `sysmem` → (diagnostic hammers) `syncdraw`/`flushall`. Verified current spellings; also available: `nolrzfc`, `nobin`, `nocb`, `noconcurrentresolves`. If `sysmem` kills the fence timeouts, the bug lives in GMEM/binning/CCU and can be narrowed.

### T1 — ETK instrumentation (middleware, no OS rebuild)
- **Ledger `os_build` column** (sessions.tsv currently can't distinguish nightlies — today's era-segmentation had to be done by epoch).
- **Fix the garbled `/storage/etk_crash_report.log`** capture filter.
- **Core-dump postmortem hook**: on new file in `/storage/cores`, record name+size in the session row; pull-to-Mac tool for symbolization against the AppImage.
- **Crash-signature additions**: `SIGBUS/wild-branch` (from journal if the kernel logs it), devcoredump presence.
- **Per-session devfreq transition counter** (from `/sys/class/devfreq/3d00000.gpu/trans_stat`) to correlate clock transitions with Adreno crashes — decides T0-4 permanence.

### T2 — Custom emulator builds (toolchains already proven)
- **aPS3e fork** (build env live on SSD): cherry-pick upstream #18844 (leak fix) + `dff29a78` (native ARM shuffles — reduces the exact register pressure behind §2.1); apply the two fork fixes below; keep `vk-pipeline-cache` branch going for the PR.
- **SPU JIT fork fixes** (both cheap, both OSes):
  1. `__builtin___clear_cache` at all five JIT publication sites (`JITASM.cpp:249`, `SPUCommonRecompiler.cpp:1852/1994/2057/2197`) + real `finalizeMemory`.
  2. Fuse x30-reload + `ret` into one inline-asm block in `AArch64JIT.cpp` so the allocator can't schedule between them.
- **Rocknix RPCS3 override**: ship our patched AppImage via ETK (no distro fork needed — `rpcs3-sa` is a downloaded AppImage; ETK can swap the binary).
- **Upstream report**: tombstones + constant identification + `AArch64JIT.cpp:99` comment + audit. Candidate venue: new issue cross-linked to #18828/#16388. This is the highest-leverage single artifact — if upstream fixes GHC/x30, every ARM64 platform benefits.

### T3 — Custom Rocknix image (the "custom rig"; build system is upstream, one repo)
- **Kernel one-liners**: `DRM_MSM_HANGCHECK_DEFAULT_PERIOD 500→2000`, `PROGRESS_RETRIES 3→8` (verified against mainline source). Converts watchdog false-positives into stutters. GT-rig trade: worst case is seconds-longer freeze on a real wedge.
- **ramoops dtb node**: kernel panics finally leave traces (currently zero — no pstore on this dtb).
- **Mesa tracking**: pin `projects/ROCKNIX/packages/graphics/mesa` to Mesa main or cherry-pick `src/freedreno/` fixes (a6xx hang fixes land on main first).
- **Restore MangoHUD telemetry** (stripped in Rocknix) → frame-pacing data for the "feels smoother" axis.
- **Devcoredump enabled** → GPU crashstate capture for Turnip bug reports.
- Build = Rocknix `make SM8250` with our overlay; in-place updatable per [[project_inplace_updates_only]] discipline (custom builds install via the same updater path).

### T4 — Custom Turnip (only if T0-5 isolates a driver bug)
If the TU_DEBUG ladder pins the hang to a specific path (LRZ/GMEM/CCU), file Mesa GitLab issue with devcoredump + maintain the narrow workaround flag as default in the T3 image. A full Turnip fork is **not** justified by current evidence — the ladder decides.

---

## 5. EXPERIMENT LADDER (decision tree, in order)

| # | Experiment | Decides | Status |
|---|---|---|---|
| E1 | GT5P/GT5 soak on leak-fixed nightly, warm vault | How much of Silent was the leak | **ARMED** (both cards nightly) |
| E2 | Shader-mode A/B during resaturation | Interpreter = Adreno amplifier? | **RUNNING** (configs live) |
| E3 | First Linux core dump from a silent crash | SPU x30 wild-branch on Rocknix? | **ARMED** (capture live) |
| E4 | GPU min-freq pin, A/B vs E1 baseline | DCVS transitions → fence timeouts? | ready (one sysfs write) |
| E5 | `TU_DEBUG=nolrz` soak → ladder | Which GPU path wedges | ready (profile.d) |
| E6 | SPU compile threads=1 + SPU-cache-off replay | icache race vs codegen bug (§2.1 discriminator) | ready (config) |
| E7 | Patched-JIT emulator build (T2) soak | Confirms fork fixes; feeds upstream PR | needs T2 build |
| E8 | Hangcheck-raised kernel (T3) on the E5-winner config | Fence-timeout conversion to stutter | needs T3 image |
| E9 | Same-section Android-vs-Rocknix on identical Turnip | Quantifies KGSL absorption; UFS-vs-A2 I/O | ready (parity achieved) |

**Measurement discipline:** one change per soak; ≥10 non-aborted GT5P sessions per arm (career clean-rate CI is meaningless below that); compare cold-vault arms to cold-vault arms; `config_changes.tsv` is the changelog of record. Success bar unchanged from [[project_race_baseline_status]]: consecutive-CLEAN streaks, with the new target = **clean rate >70% warm-vault** before declaring Stage III complete.

---

## 6. CLASS-LIMITS ASSESSMENT (what is actually immovable)

Honest ceilings on SM8250 — none currently binding on the *stability* mission:
- **8 GB RAM**: GT5 brushes 7.5 GB. Post-leak-fix this is workable; it caps resolution-scale ambitions and forbids x86-RPCS3-under-FEX/Box64 (JIT-on-JIT memory blowup; also no community evidence of it racing). A real wall for *future* RPCS3 memory growth — watched, not hit.
- **Cortex-A77 ISA**: no SVE2, no I8MM — locked out of the newest upstream SPU fast paths (they're A715+/X-series). Costs FPS, not stability. FPS<30 is acceptable per mission.
- **Adreno 650 / 1 MB GMEM**: forced-sysmem fallback costs bandwidth; no MSAA features; no async-compute queue. Constrains quality, not crash-freedom.
- **No audio**: pre-existing acceptance; SPURS audio jobs still execute (they must, for game logic), only output is muted.
- **Conclusion: the objective is not blocked by silicon.** Every crash class maps to fixable software. Stage III proceeds.

## 7. CONTRIBUTION PIPELINE (the flywheel)

Rocknix `update.sh` fix (shipped, PR #2874) → aPS3e VkPipelineCache (PR pending) → **RPCS3 ARM64 SPU JIT report + fix (this dossier, queued)** → possible Mesa/Turnip issue (post-E5). The custom rig is also a bug-discovery instrument; everything it finds flows upstream, and every upstream landing shrinks our fork surface.

---

*Related memory: [[project-spu-arm64-wildbranch]] · [[project-stage3-crash-taxonomy]] · [[project_aps3e_shader_cache]] · [[project_race_baseline_status]] · [[project_ufs_experiment_plan]]*
*Verification notes: kernel constants + RPCS3 device-loss handling + Mesa TU_DEBUG vocabulary re-verified against live sources 2026-06-11. The KGSL ft_policy semantics and "innocent submit replay" detail are from driver-source knowledge, not re-verified line-by-line — treat as high-confidence but check before citing externally.*
