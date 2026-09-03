# ETK Big Changelog — v0.8.0 → 0.9.0

Every change across the kit between v0.8.0 (2026-07-22) and the pending 0.9.0,
grouped by ETK release tag. **0.9.0 (2026-09-03) is the cut this document
closes on; 0.8.7 shipped 2026-08-31.** Sibling-repo work is filed under the ETK release window it
landed in; `CHANGELOG.md` remains the canonical per-release record.

Releases: v0.8.1 (07-27) · v0.8.2 (07-30) · v0.8.3 (08-01) · v0.8.4 (08-07) ·
v0.8.5 (08-10) · v0.8.6 (08-28) · v0.8.7 (08-31) · v0.9.0 (09-03)

---

## 🐧 Kernel & Boot

**GTK changes** (`rocknix-gtk`)
- v0.8.3 — build_712: 7.1.2 kernel lane on ROCKNIX 20260801 base; ships as `20260801-0.3`
- v0.8.4 — kernel `20260801-0.3.1`: same source, re-minted on etk-cloud
- v0.8.5 — gcc-15 compiler law enforced by the build, not just documented
- v0.8.5 — DP hotplug repro harness: state probe, edge trace, plug protocol
- v0.8.5 — patches #3–#7: Type-C debounce/mux/redriver + DP enable-lock + dpu encoder; phantom-cable and DP-hotplug wedges closed
- v0.8.5 — 0.4 series built and validated (boot A/B, in-game DP hotplug both ways); `-0.4.1` promoted daily driver, ships as `20260801-0.4.1`
- v0.8.6 — 20260901 upstream survey + re-survey: it's a 7.2 rebase; `patches-7.2/` staged (6 carry; DP enable-lock retired upstream; q6asm dropped)
- v0.8.6 — build_72 + stage_72: 7.2/20260901 kernel lane with loud staging gate; ships as `20260827-0.5`, validated live
- 0.8.7 — stage_72/build_72: stderr logging + recursive firmware count; staging-gate false-WARN/false-fail fixes
- v0.9.0 — kernel `20260901-0.5`: 7.2.0 rebuilt on the OFFICIAL ROCKNIX 20260901 tag (delta vs the surveyed nightly touched nothing in kernel/boot/dts); cold-boot certified; card base moves to 20260901

**ROCKNIX team changes** (upstream, surveyed for the 7.2 rebase — not merged)
- sm8250: bumped to mainline kernel 7.2 (PR #3209)
- sm8250: gpu improvements — 305→925 MHz OPP ladder, ACD, GPU→DDR bandwidth voting
- kernel cmdline: `gpt` added (RP Mini V2 recovery fallout)
- 7.2 conf: LSUI enabled; READ_ONLY_THP_FOR_FS dropped; NETFILTER_NETLINK m→y
- firmware: regulatory.db + .p7s into EXTRA_FIRMWARE (wireless-regdb 2026.05.30)
- grub: generator rework — `abl_dev` block overrides `saved_entry` post-`load_env`
- rocknix-abl: 1.1.6 → 1.1.8 (reaches rigs only by manual reflash)

## 🎮 Emulators & Frontend

**GTK changes** (`etk-rpcs3-gtk`; core versioning is the fork's own, not ETK's)
- v0.8.3 — core 0.8.0-dev: base bump 19544 → 19638 (`a1deb2921`); PPU obj-cache v7→v8
- v0.8.3 — core 0.8.1 **certified**: reverts upstream SPU reduced-loop skip (GT5P Spec II boot fatal)
- v0.8.4 — core 0.8.2-dev: LLVM 22.1.8 toolchain rebuild, identical source
- v0.8.4 — core 0.8.3-dev: `optnone` on `spu_thread::stop_and_signal` (Spec II clang-22 miscompile mask)
- v0.8.4 — core 0.8.4-dev: GTK_PROBE_11912 TIU probe restored; `verify-markers.sh` 13-marker gate
- v0.8.4 — core 0.8.5 **certified**: `noinline` barrier replaces optnone; full LLVM-22 gains
- v0.8.6 — core 0.8.5-dev: TEXCACHE-UNLOCK net (SSX texture-cache race)
- v0.8.6 — leap-frog: base RPCS3 `a1deb2921` → ARMSX3 `f707458b0`; 0.9.0-mint1 baseline + leapfrog-v1 cumulative (32 files, +1344/−87)
- v0.8.6 — Linux build fixes 0004–0006: Oboe gated Android-only; `fcntl.h`; X11 `CWX` vs SPU opcode
- v0.8.6 — CI LLVM image bakes the ARMSX3 AArch64 GHC emergency-spill patch
- v0.8.6 — core 0.9.0 GA cut; certified 08-20 (N=3 GT5P gate, 13/13 markers), rolled back to 0.8.5 on 08-27
- v0.8.6 — core 0.9.0.1-dev: leap-frog set rebased onto ARMSX3 `a74a0f3e0`; fixes 0008–0010 (LSFG gated, config members, capture stub)
- v0.8.6 — EXP-maskoff probe: 0.9.0.1 minus the optnone stop_and_signal mask
- 0.8.7 — core 0.9.0.2: optnone mask retired, EXP-maskoff promoted (~23 vs 20 fps)
- 0.8.7 — patches: overlay-coalesce-notice — dedupe fence rescue-notice overlay (Asahi-surfaced)
- 0.8.7 — Asahi/M1 (Honeykrisp) validation lane: 0.8.4+overlayfix stack on RPCS3 19895

**ARMSX3 changes** (upstream base)
- `f707458b0`: 484 commits over old base; mobile-profile gates (ETK widens via `ETK_CONSTRAINED_HOST`); eight new tunables surfaced
- `a74a0f3e0`: LSFG FrameGen port + SGSR upscaler; driver pipeline cache default-ON (ETK ships it OFF)

**RPCS3 changes** (upstream, arrived via base bumps)
- 19544 → 19638: rsxfp/rsxvp interpreter fix series; LIT re-implemented; vk/gl MSAA + D24S8 flattening; LLVM 22.1.8
- via leap-frog: unpkg OOB fixes; 3D-texture mipmap fixes; PPU ARM64 saturation fix; SPU SELB/SHUFB fixes; ROP_OUTPUT_REMAP
- via `a74a0f3e0`: tolerant ISO short-read (supersedes GTK patch 0001); reservation notify-after-store (root-fixes the bug optnone masked)

## 🖥️ Graphics

**Mesa Turnip stable** (`etk-turnip-gtk`)
- v0.8.3 — stable rebase 26.1.3 → 26.1.6; 26.1 backports (D32S8 EARLY_Z_LATE_Z hang wa, tile-division fix)
- v0.8.3 — zlatez: A650 workaround widened past D32S8 to Z24S8; QUERY_SURVIVE default ON; `ETK-GTK` driverInfo stamp
- v0.8.3 — certified pin `26.1.3_gtk_0.4` → `26.1.6_gtk_0.6` (26.1.3 kept as fallback)
- v0.8.4 — gear registration decoupled into `tu_etk_gears.h`; shipped bit collision fixed
- v0.8.4 — gtk_0.7 re-mints on etk-cloud replace gtk_0.6; certified `26.1.6_gtk_0.7`
- v0.8.5 — stable rebase 26.1.6 → 26.2.0 (26.1 backports now native); certified `26.2.0_gtk_0.7`
- v0.8.5 — catalog policy set: Turnip is CUMULATIVE — every listed build stays fetchable
- v0.8.6 — stable bump 26.2.0 → 26.2.1 (repo-side; `26.2.0_gtk_0.7` remains the shipping pin); 26.1 series EOL, 26.1.6 frozen fallback
- v0.8.7 — 26.2.1 catalogued (7 entries); certified pin still `26.2.0_gtk_0.7`
- v0.9.0 — 26.2.2 minted + catalogued (9 entries); **certified default advances to `26.2.2_gtk_0.7`** (operator verdict; the rig's daily driver since 09-02)

**Mesa Turnip dev**
- v0.8.3 — pre-release track added: 26.2.0-rc3; enters the DRIVER catalog as `rc3_gtk_0.6` (unvalidated, operator-directed)
- v0.8.4 — devel pinnable to any 40-hex sha ("main is a position, not a version"); rc3 track retired for sha-pinned 26.3.0-devel
- v0.8.5 — `26.3.0-devel-e40d93a_gtk_0.7` enters the catalog as the pre-release slot
- v0.8.5 — ANDROID (bionic/kgsl) lane: `build_android.sh` + container provisioning; stock-Mesa adpkg reconstruction, sha-pin gates
- v0.8.6 — devel re-pinned main@`d2e56df`; 26.3.0-rc1 lane pre-wired (upstream due 2026-10-14)
- v0.8.7 — devel `20260821-d2e56df` catalogued under the dated naming scheme
- v0.9.0 — devel re-pinned main@`c0682c5` (dated `20260902`, 5/8 series — no dsbypass/dsany); forge gate accepts dated devel names

## 🔊 Audio

**GTK changes**
- v0.8.5 — kernel patch #8 q6asm 24-bit word-size: WITHDRAWN — deterministic DP AFE −110 on `-0.4.2`
- v0.8.5 — reverse-orientation ladder: seven builds, software stack exonerated; vendor redriver bus stays dark (Android confirms hardware path)
- v0.8.5 — DP capture sink pinned S16_LE (was S24_LE, ~25 dB loss); `ETK_DP_AUDIO_S16=0`

**ROCKNIX changes**
- q6afe silent-boot race independently root-caused upstream via audio-as-modules
- wireplumber: device-config existence guard (cosmetic)

## 🔧 System

**GTK changes**
- v0.8.6 — wdt-bark branch: watchdog pretimeout governors, bark→panic, dark until armed (held off main pending cold-boot validation)
- v0.8.6 — 7.2 migration route: the built-in updater; staged-tar path retired

**ROCKNIX changes**
- LibreELEC master merge: glibc 2.44, Python 3.13.5 → 3.14.7
- ffmpeg pinned 7.1.1 (libavcodec soname stays 61); Mesa project override → 26.1.6
- rpcs3-sa: natively source-built; `set_kill "-9 rpcs3-sa"`
- MangoHud pinned v0.8.4; fake-suspend: DPMS off by default + suspend-action fix

## 🔩 Other

**GTK changes**
- v0.8.4/v0.8.5 — build containers provisioned from repo (kernel + Turnip lanes); CLAUDE.md bootstraps point at `~/etk`
- v0.8.6 — `UPSTREAM_20260901.md`: full upstream survey + re-survey for the 7.2 chassis

**ROCKNIX changes**
- EmulationStation bump + GLES3
- `apps:` package refactor; mako-notify, sdl2text plumbing
- unchanged: InputPlumber v0.75.2, BusyBox 1.36.1 (`get_setting` `[]` bug persists)

## 🏁 ETK Middleware

**v0.8.1**
- install: headless `--installpkg` PKG installer replaces on-screen dialog + folder-watch
- install: storage root set up before RPCS3 first-runs; strands moved to games card
- paddock PUSH: `save_aliases.tsv` resolves foreign-title-id save folders
- paddock PULL: silent shader truncation fixed; restores over empty saves with `.paddock.bak`; honest summary
- pad auto-bind: SDL device name written into RPCS3 pad config (`ETK_PAD_BIND=0`)
- licences: all staged `.rap` installed, not just the first; uninstall clears both storage roots
- changelog: `[0.8.0]` backfilled into the repo file

**v0.8.2**
- chiaki: new Tier P Remote Play lane — `mercurious/chiaki-rocknix` fork, build, deploy, ES menu entry
- chiaki: DualSense haptics-audio → rumble; trigger deadzone with rescale (Flip 2 L2 rests 12/255)
- chiaki: console wake-on-connect, rest-mode clean exit, dead-session self-heal; `ETK_CHIAKI=0`; uninstall in lockstep
- modules: `etk_modules_inject.py` becomes an N-app table registrar; Sentry re-asserts entries after boot wipe
- notify: `etk_chiaki_notify.sh` replace-in-place mako toasts, reusable by any lane

**v0.8.3**
- update: full-stack self-update — `gtk_stack.json` manifest + `kernel_stage.sh`; kernel is the fifth release asset
- osguard: OS-update coherence guard daemon — mismatch heal, sha-verified promotion, grubenv re-point; `ETK_OS_GUARD=0`
- gates: release_sanity asserts manifest↔install.sh lockstep; test_osguard 29/29 + test_kernel_stage 18/18, host + BusyBox
- catalog: certified allowlist relaxed — `drivers/` IS the catalog
- telemetry: `tune_tag` attributes the whole stack; blackbox self-relink guard

**v0.8.4**
- forge: every binary minted on etk-cloud (Oracle Ampere A1) from in-git recipes, verified against the artifact replaced
- gates: release_sanity probes pinned-artifact existence — a pinned unpublished Turnip had shipped stock
- boot-identity unit templated from `APP_VERSION` (was frozen `0.7.0`); image-lane stale defaults fixed
- notify: ES's two surfaces adopted (verdict toast + progress card); `etk_notify.sh` replaces four dbus-send copies; mako config idempotent with rollback
- installs: background out-of-process worker + queue; stand-down when a game launches; `ETK_BG_INSTALL=0`
- installs: `.rap` queue-field fix; PID-scoped timeout cleanup (no more pkill of a running game); postmortem rollup ordering
- telemetry: sentry tells installer RPCS3 from game RPCS3 by argv; postmortem archives `RPCS3.log`, stops SHM inheritance
- dyno: `--audio` skip/s ranking with retroactive SHM-contamination filter
- gates: `test_install_queue.py` (11 cases, BusyBox) + `test_notify.py`

**v0.8.5**
- forge.sh: etk-cloud build conductor — six lanes, detached builds, fingerprint skip, Pit Wall TUI
- image: gates manifest-vs-inputs, middleware version, baked kernel; bakes the WHOLE Turnip catalog (one-entry chooser fixed)
- gates: every pin compared to staged file + forge sidecar; DRIVER pin checked; `sync_game_configs.sh` freshness (24/41 had drifted)
- charts: `chart_library.py` committed — PLAYABLE filter, quit-hang stripe, fps + jitter, endings; wiki generators
- ledger: 39.7-year phantom session routed to the honest-unknown path
- input: pad self-heal on DP unplug; `ETK_DP_MIRROR=0` made real
- windows: PowerShell port re-synced; cert pins gated in lockstep (had drifted two releases)
- `SECURITY.md` added; golden tune adopted as a safe start, not a tune

**v0.8.6**
- boot: numeric boot-default pin (STEP 6.4 + image lane); `abl_dev` counter + `gpt` cmdline mirror; stock boot-config convergence
- boot: 4Kn law for internal-disk images; `fastboot flash ROCKNIX` documented as the recovery rung
- osguard: ordering fix — the guard raced its own mount and silently never ran
- blackbox: resource flight recorder, the silent-death witness (`After=` the sd-rebind mount)
- spotter: ADRENO-NOFAULT detector; log-fatal precision; SILENT counter no longer corpse-seeded
- cockpit: corpse-grab freeze forensics; paddock: preflight auth + loud failures
- deploy: stock-nuke class closed — no silent downgrade, cert pins must have sources, the bind states its verdict
- gates: core cap counts SHIPPING builds; `EXP-*` probes exempt and loudly NOTEd
- config: 14 dead keys removed from all 42 masters, zero behavior change
- docs: TRACK_MANUAL v1.0 on the A/B/C modality spine; AI_MANIFEST tombstoned

**0.8.7 (pending)**
- beacon: install.sh drives an on-rig "ETK INSTALL" progress card — percent + stage, explicit verdict, always on, fail-soft
- beacon: the PowerShell port drives the same card and verdicts; release_sanity gates the port's roster
- gates: `test_notify.py` [BEACON] suite — mutation-tested
- kernel deploy: `KERNEL_DEPLOY_MODE` fallback test → default; auto-boot withheld unless verified Flip 2 + module tree present
- self-update: reconciles shipped systemd unit changes; osguard Phase B re-applies the numeric default pin
- uninstall: per-game `dev_hdd1` cache no longer leaks

## 🛞 ETK Pitstop

**v0.8.1**
- install stays in-app: spinner, up-front size, size-scaled timeout

**v0.8.2**
- chiaki UI: title screen, console chooser, pairing wizard (8-digit PIN + PSN-id keyboard)
- in-stream chords: R1+L3 flips 1080p/720p, L1+R3 flips h265/h264, reconnect in place; `input_d` stands down during a stream

**v0.8.3**
- DRIVER: zlatez gears registered, dimlog surfaced as a diagnostic row
- chiaki: connection status screen; BITRATE row (auto/low/medium/high per console); real Flip 2 button hints

**v0.8.4**
- installs queue in the background; ES rescan on finish; one honest stand-down message
- TUNING → CORE: per-title emulator core swap (`ETK_CORE_SWAP=0`)
- TUNING → PATCH: community patch toggles over RPCS3's `patch_config.yml` (`ETK_PATCH_FETCH=0`)
- TUNING: PPU/SPU decoder + sleep-timers-accuracy fields; non-selectable section headers

**v0.8.5**
- screenshot chord L1 → L1+L2, hysteresis-gated; HUD punchbox needs a 0.4 s hold
- L1+R3 recovery deliberately untouched — fires on press, asserted by the suite
- TOOLS: Bog Sampler toggle; `test_chords.py` synthetic-evdev regression suite

**v0.8.6**
- TUNING: eight new dials from the 0.9.0 core review; duplicate Shader Precision dial dropped; Resolution fix restored
- TUNING: RR7 FIFO fetch-accuracy/reordering dials retired (falsified on rig 08-21)

**0.8.7 (pending)**
- GAME SWITCHER (hold SELECT): re-point Pitstop at any installed game, full in-place restart; refuses mid-game and pre-ledger-stamp; stick-probe rollback; `test_game_switcher.py`
- TOOLS entry 0 rebuilt as "Manage Shaders & Caches": both cache roots, real per-game dirs, refuse-while-running, honest PARTLY CLEARED, banked vault never touched
- TOOLS geometry: menu windowed in the body band, `[GEOM]` strict at every size; footer-anchor fix; AppleDouble `._*.psn` siblings skipped

---

### Corrections surfaced while compiling (vs `CHANGELOG.md`)
1. `CHANGELOG.md` had lost its `## [0.8.5]` header at the 0.8.6 cut — the 0.8.5 body sat inside the `[0.8.6]` section. Restored byte-identical to the v0.8.5 tag in `3921b2f`.
2. The 0.8.5 notes said Turnip was unchanged; `gtk_stack.json` at the v0.8.5 tag certifies `26.2.0_gtk_0.7`, and `26.3.0-devel` joined the catalog. Filed under v0.8.5 above; the 0.8.5 Stack block corrected 2026-08-31.
3. The 0.8.6 "pins deliberately unchanged" claim hid that RPCS3 core 0.9.0 was certified 08-20 and rolled back 08-27, and the shipped 0.8.5 sha changed. The 0.8.6 Changed block corrected 2026-08-31.
