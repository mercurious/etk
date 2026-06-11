# aPS3e VkPipelineCache Dossier — RPCS3-on-Android Shader-Cache Fix

**Sprint date:** 2026-06-10 · **Status:** ✅ Fix written + validated on-device; PR pending maintainer response
**Subject:** aPS3e (RPCS3 fork for Android) GitHub issue [#121](https://github.com/aenu1/aps3e/issues/121)
**Rig:** Retroid Pocket Flip2 · Snapdragon SM8250 · Adreno 650 · Turnip (Mesa) 26.1.2 · Android 13

---

## MEMO (TL;DR)

Issue #121 ("shader cache not loaded, recompiles every launch") was a **misdiagnosis**. RPCS3 rebuilds `VkPipeline`s from cached ucode every launch *by design*. The real defect: **aPS3e never used a `VkPipelineCache`**, so the Turnip driver recompiled SPIR-V→native ISA cold on every boot. Fix = persist a `VkPipelineCache` per title. The non-obvious key: **save it right after the startup compile burst, not on clean teardown** — DRM-spawn titles crash before teardown, so a teardown-only save never persists the real cache. **Validated on-device:** GT5P cold launch writes a **93.6 MB** driver cache that survives the crash; warm relaunch reloads it and the compile phase zips (operator-confirmed). Clean 5-file patch ready.

---

## THE BUG

- `Emu/RSX/VK/VKPipelineCompiler.cpp` (and on the dev branch, `VKProgramPipeline.cpp`): both `vkCreateGraphicsPipelines` / `vkCreateComputePipelines` passed a **null** pipeline cache. No `VkPipelineCache` existed anywhere in the tree.
- Consequence: the on-disk shader cache loads + rebuilds pipelines each launch (expected), but with **zero driver-side reuse** — every pipeline recompiles cold. On mobile (Adreno/Turnip) that driver compile is the dominant boot cost.
- Why #121 read as "cache not loaded": the ~3000-pipeline "Compiling shaders" pass *is* the cache loading; it just paid full price every time.

## THE FIX (branch `vk-pipeline-cache`, 5 files)

1. `VKPFNTable.h` — register `vkCreate/Get/DestroyPipelineCache` (dlsym wrangler).
2. `VKPipelineCompiler.cpp` — global `g_pipeline_cache`; `load_pipeline_cache()` on init; `flush_pipeline_cache()` (save **without** destroy) + `save_pipeline_cache()` (flush + destroy) on teardown; per-title path `get_ppu_cache()+"vk_pipeline_cache.bin"`; header sanity check.
3. `VKPipelineCompiler.h` — declare `flush_pipeline_cache`.
4. `VKProgramPipeline.cpp` — `extern g_pipeline_cache`; pass handle into the two create calls.
5. `VKGSRender.cpp` — **`vk::flush_pipeline_cache(*m_device)` right after `shaders_cache::load()`** in `on_init_thread`. ← the insight that made it work.

Pipeline caches are internally synchronized for `vkCreate*Pipelines`, so the compiler workers share the handle lock-free.

## VALIDATION (on-device, GT5P / NPUA80075, DRM-spawn)

- Driver confirmed on the **renderer** path: `Found Vulkan-compatible GPU 'Turnip Adreno (TM) 650' running on driver 26.1.2`.
- Cold launch → flush writes `…/EMAIN.SELF/vk_pipeline_cache.bin` = **93,611,565 bytes** (full 3272-pipeline ISA cache); survives the race crash.
- Warm relaunch → `Loaded Vulkan pipeline cache (93611565 bytes)`; pipeline-compile phase **zips** (matches Rocknix).
- Second vector: changing shader-quality low→high correctly invalidates only the changed pipelines and re-warms next boot (proves the cache is content-addressed, not stale-serving).
- **Out of scope (honest):** the GT5P/GT5 race crash is a *separate* emulation-stability bug that also hits Rocknix; we fixed the boot/compile-time cost, which is the fix's job.

## THE ON-RIG SAGA (how we got to a base that runs the games)

The debugging arc, in order — each wall eliminated by evidence (forced RPCS3 file-log to the adb-readable external dir):
1. **Build env:** macOS host, 8 GB RAM. Built on an **APFS sparse image on the exFAT SSD** (exFAT can't host the build). Host CLT was broken (libc++ headers stub) → symlinked to SDK copy. Throttled `-j4` + `LLVM_PARALLEL_LINK_JOBS=1`.
2. **First builds crashed games** → chased driver, data, our fix; all exonerated by log. Concluded "older base" — **WRONG**.
3. **Driver:** operator's instinct was right — there are two load paths (probe `vk_lib_info` vs renderer `VulkanAPI`); confirmed Turnip 26.1.2 loaded correctly on both. Not the cause.
4. **Data:** complete (all PDIPFS/EDAT open with correct sizes). Not the cause. (On-device `mv` relocate dance protects the 28 GB games across uninstalls; FUSE re-attributes ownership.)
5. **ISO permission:** GT5 `.iso` opened via SAF content-URI (`detach_open_uri`), not a raw path — `MANAGE_EXTERNAL_STORAGE` can't mint it; only the in-app picker can. Live-injection has limits.
6. **The pivot (operator's question):** the `2.39` git tag == `main2`; there are also `main3`/`main4`. **`main4`** (post-2.39 dev) has `iso`/`font`/`VKPresent` reworks + a newer RPCS3 merge — the fixes for every wall. Built from `main4` + re-implemented the fix there.
7. **main4 build breaks (newer base):** `3rdparty/abseil-cpp` empty → cloned `20250512.1` (protobuf 6.33.4 dep); curl built its broken test suite → `-DBUILD_TESTING=OFF`.
8. **main4 runs GT5 to menu + GT5P with correct loading graphics** → but cache showed "no improvement" → log revealed the teardown-save-too-late problem → **flush-after-burst refinement** → validated.

## CONTRIBUTIONS THIS SPRINT

1. **ROCKNIX (shipped):** SM8250 `update.sh` boot fix — landed in nightly-20260610 as "sm8250: update update.sh (by Philippe Simons)" via loki666b PR #2874. (See [[project_dsi_coldboot_blackscreen]].) Fixes cold-boot black screen on all 5 SM8250 products.
2. **aPS3e (delivered, PR pending):** this VkPipelineCache fix for issue #121.

## HANDOFF / ARTIFACTS

**Repo:** `/Users/dave/aps3e` — clean fix committed on branch **`vk-pipeline-cache`** (`4b4e12f5`), based on `main4`. Remote is upstream `aenu1/aps3e` (no fork yet). Test-only edits left uncommitted: `aps3e_emu.cpp` forced-logging; untracked `3rdparty/abseil-cpp/`, restored `app/build.gradle` (ndk pin + signing + `-DBUILD_TESTING=OFF` + LLVM throttle).

**Submission artifacts** (`/Users/dave/aps3e/issue121_attachments/`):
- `vk_pipeline_cache_fix.patch.txt` — the patch (GitHub rejects `.patch`; `git apply` ignores the extension)
- `validation_evidence.txt` — driver line + 93 MB load/save + cache sizes
- `proposed_comment.md` — the issue-#121 comment body
- `vk_pipeline_cache_fix_bundle.zip` — all three
Also: `/Users/dave/aps3e/0001-persist-vk-pipeline-cache-main4.patch`, `issue121_comment.md`.

**Built APKs (SSD):** `/Volumes/Extreme SSD/aps3e-main4-flush.apk` (final, validated), plus earlier iterations.

**Build environment (reusable):** APFS sparse image `/Volumes/Extreme SSD/aps3e-build.sparseimage` → mounts at `/Volumes/aps3e-build` (contains `android-sdk` w/ NDK 27.0.12077973 + CMake 3.22.1, `gradle-home`, and a project copy). JDK = `brew openjdk@17`. To rebuild: mount the image, `cd /Volumes/aps3e-build/aps3e`, export `JAVA_HOME`/`ANDROID_HOME`/`GRADLE_USER_HOME`/`SDKROOT`, `caffeinate -ims ./gradlew :app:assembleRelease`.

## NEXT STEPS

- ✅ **DONE 2026-06-11:** forked `mercurious/aps3e`, pushed `vk-pipeline-cache` (4b4e12f5), and published the **unofficial interim build** as fork release [`shader-patch-1`](https://github.com/mercurious/aps3e/releases/tag/shader-patch-1) (APK `aps3e-shader-patch-edition-unofficial.apk`, SHA256 `8d6aaf1d…c96926`, GPL-compliance notes + deprecation promise in the release body). Decision rationale: GPLv2 propagates from the embedded RPCS3 (upstream repo itself has NO license file — aenu1's glue code carries only an implied grant, hence interim/temporary posture); hosted on the **fork**, not ETK, to keep ETK's mad-science framing and audience separation. `issue121_attachments/proposed_comment.md` extended with the interim-build paragraph (field data: per-title cache grown to ~295 MB across days of sessions).
- **REMAINING (operator fires):** post the #121 comment + attachments, open the PR against `main4`. The fork release retires when upstream ships.

Related memory: [[project_aps3e_shader_cache]] (full technical trail), [[feedback_hardware_blame_tendency]] (the "rule out own-creation first" lesson, which cut both ways here — the operator's external-cause instincts on driver/branch were *right*).
