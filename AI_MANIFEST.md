# ETK BRIEF
A custom Rocknix middleware rig to enable PS3 Emulation on ARM64 Retrogaming Handhelds by brute-force optimization, thermal and shader protection, shader cache management with advanced in-game telematics and controls.

# ETK (EMULATOR TOOLKIT) MISSION MANIFEST
**TARGET HARDWARE:** Retroid Pocket Flip 2 (SM8250)
**TARGET OS:** Rocknix (Read-Only Root, BusyBox Environment) **official release 20260701** (kernel 7.0.11). The old nightly pin existed to pick up emulator/driver fixes; ETK now bind-mounts its OWN RPCS3 (GTK Edition fork, carries the GT5 leak fix lineage + ETK patches) and Turnip (GTK driver) over the stock squashfs binaries, so stock emulator/driver versions are no longer load-bearing — the OS supplies the substrate only (kernel, InputPlumber DS5 pad target, sway/foot, BusyBox). Migration validated 2026-07-02 via tools/etk_drift.py (no structural drift vs pinned 20260628 profile). REMEMBER: OS updates regenerate the grub twins — re-run scripts/arm_blackbox.sh (panic=10) after any update; install.sh STEP 6.65 warns on this drift.
**CORE PHILOSOPHY:** Defeat Rocknix's filesystem limitations using a hybrid Persistent/Volatile architecture to enable high-performance PS3 emulation.

## THE IMMUTABLE LAWS (CRITICAL)
1. **NEVER TOUCH THE SHM:** The path `/dev/shm/etk_shm` is the sacred IPC backbone. Do not attempt to "optimize" or "unify" these paths into persistent storage.
2. **SINGLE SOURCE OF TRUTH:** `scripts/env.sh` is the ONLY file allowed to define environment variables. All other scripts MUST `source` it.
3. **SYMLINK SANCTITY:** The shader cache is a dynamic symlink: `/storage/.cache/mesa_shader_cache` -> `$VAULT_DIR`. Never use `rsync` on the live cache.
4. **AGNOSTIC IDENTITY:** The `TARGET_ID` must always be dynamic. It is sniffed from the active RPCS3 process via `pgrep` and `PARAM.SFO`.
5. **NO GNU-ISMS:** Rocknix uses **BusyBox**. Assume GNU-specific flags (`--long-options`, `grep -P`, `find -printf`) will fail. Use POSIX-compliant syntax only.
6. **NEVER REBOOT THE RIG REMOTELY:** The operator is physically at the rig (the Driver; the host AI is the Engineer). Do NOT `systemctl reboot` / `reboot` over ssh, nor trigger any host-driven power-cycle of the device. When a cold boot is genuinely required (driver-build swap to load, clearing a GPU wedge, any always-reboot-gate validation), do the host-side prep, then ASK the operator to reboot on-device (or via the Pitstop DRIVER-tab REBOOT row) and WAIT for the rig to return. Read-only telemetry/spotter over ssh is fine — the prohibition is on rebooting and other disruptive power actions.

## ROCKNIX ARCHITECTURE (THE BOOT CHAIN)
1. **THE REBOOT WIPE:** Rocknix vaporizes `/dev/shm` on every reboot. Background processes must rebuild `$SHM_DIR` instantly upon execution.
2. **NO ROOT SYSTEMD:** Do NOT attempt to write to `/etc/systemd/system/`. Do NOT use `mount -o remount,rw /` to brute-force the root partition. The OS will reject it.
3. **PERSISTENCE VECTOR:** All custom systemd services MUST be written to `/storage/.config/system.d/` (Note the dot, not systemd). Rocknix natively maps this directory at boot. Use `systemctl enable /storage/.config/system.d/etk.service` to ensure absolute path resolution.
4. **AUTOSTART LIMITATIONS:** Do NOT use `/storage/.config/autostart/` for backend/daemon execution — but for the correct reason. Rocknix's `rocknix-autostart.service` runs each script **synchronously** at boot and is ordered `Before=` the UI service (its final act is launching the UI). A long-running or blocking script dropped there **stalls UI bring-up**. A persistent supervisor therefore belongs in its own systemd unit under `/storage/.config/system.d/` (where ETK already puts `etk.service`). NOTE: the earlier claim that autostart "causes race conditions with MangoHud" was **unsupported and has been empirically disproven** (2026-06-01) — autostart completes at boot (~12 s), while MangoHud is a game-launch overlay loaded by `runemu.sh` at an arbitrary later time; the two never share a time window. Re-run the proof with `tools/probe_autostart_race.sh`.
5. **ENV-INJECTION VECTOR (profile.d):** To push an environment variable into the RPCS3 runtime (Turnip `TU_*`, `MESA_*`, etc.), write `export VAR=value` to `/storage/.config/profile.d/09x-etk-<name>`. `start_rpcs3.sh` sources `/etc/profile` → `profile.d` at **every game launch**, so the var reaches RPCS3/`AppRun.wrapped` (always verify via `/proc/$PID/environ`, NOT by assuming). **SURPRISE vs the volatile-dir boss above:** ROCKNIX regenerates only its OWN profile.d entries on boot/update and **leaves foreign ETK entries intact** — so a profile.d injection PERSISTS across cold boots like a real config file (no Sentry tripwire needed). This is the vector behind the Stage-III harness (`098-etk-stage3`, Mesa cache cap) and the Pitstop DRIVER-tab Turnip dials (`097-etk-turnip-dials`). Because they live OUTSIDE `$ETK_ROOT`, `uninstall.sh` MUST delete them explicitly (see the deployment laws below).

## ROCKNIX SD CARD BASED STORAGE PATHS
1. `/storage/roms` is the same as `/storage/games-internal/roms` on single-card devices such as the target device (Retroid Pocket Flip 2)
1. `/storage/games-external/roms` is used for storing games on the second card slot on a two slot device. Do not let this throw you off.

## ROCKNIX TOOLS ARCHITECTURE & LAYER MAPPING

### Dynamic Tools Menu Intercept
Unlike traditional emulation platforms, Rocknix compiles its native "Tools" system carousel by scraping a specialized backend configuration path rather than loose ROM folders.
- **Persistent Source Path:** `/storage/.config/modules/`
- **Target Extensions:** `.sh` (Must possess explicit `chmod +x` execution permissions)
- **Frontend Render Engine:** EmulationStation spawns items within this module using the Wayland-native terminal emulator `foot`.

### Terminal Rendering Architecture
The system invokes scripts using the absolute execution command: `/usr/bin/foot %ROM%`.
- To adjust text sizing and resolution matrices for high-DPI panels (e.g., Retroid Pocket Flip 2), downstream middleware scripts must implement a nested runtime override.
- Runtime scaling is achieved by invoking a secondary breakout layer: 
  `foot -F -o font="monospace:size=XX" <target>`
- This technique bypasses immutable system-wide font profiles and avoids breaking layout stability for neighboring OS tools.

## ROCKNIX WAYLAND / SWAY & GARAGE LEARNINGS (hard-won; defies stock-Linux intuition)
These cost real debugging time in the garage. Front-load them.

### SWAY IS A TILING COMPOSITOR — launching ANY window splits Pitstop
ETK Pitstop runs in a **`foot` terminal, fullscreen**, inside the sway tree. The instant you launch a *second* window (RPCS3 for an install, `swayimg` for a crash-frame preview), sway **tiles it alongside foot** and knocks foot out of fullscreen → the curses UI is left clipped/split (the operator sees "two/three columns"). This is sway behaving normally, NOT a bug in your launch.
- **THE FIX (proven — the PKG-installer pattern, reused by the crash-frame preview):** (a) fullscreen the launched window yourself via `swaymsg '[app_id="X"] fullscreen enable'` **once it has mapped** (poll `swaymsg -t get_tree` for the `app_id`; the app's own `--fullscreen` flag is NOT enough — it still tiles); (b) on close, **re-assert foot** with `_restore_pitstop_window()` → `swaymsg '[app_id="foot"] fullscreen enable'`. The next `_draw` picks up the restored size via `getmaxyx()`. Skipping step (b) is what leaves the split.
- **`swaymsg` needs `SWAYSOCK`, which is NOT in the service/ssh ambient env.** Derive it: `glob $XDG_RUNTIME_DIR/sway-ipc.*.sock` (`_tools_env()` already does this). Any Wayland tool (grim/swayimg/swaymsg) launched from a Sentry-spawned context also needs `XDG_RUNTIME_DIR=/var/run/0-runtime-dir` + `WAYLAND_DISPLAY=wayland-1` set explicitly (the service env has none — `screenshot.sh` is the reference).

### MAKO HAS NO IMAGE DECODER — you cannot render a PNG in a notification
mako links `libgdk_pixbuf` + `libpng`, so it *looks* image-capable — but the gdk-pixbuf **loader `.so` modules are STRIPPED** from this build. Only the stale `/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache` remains, naming `libpixbufloader-*.so` files that are **absent from disk**. Result: mako renders **text only**; `app_icon`, the `image-path` hint, and `file://` URIs ALL come up blank (diagnosed live 2026-06-18 — do NOT re-attempt mako image rendering).
- **To show an image on-device, use `swayimg`** (it decodes PNG itself, no gdk-pixbuf). Launch with `--class=<app_id>` so `swaymsg` can target it; fullscreen + foot-restore per the sway rule above. The **gamepad cannot close a swayimg window** (it's a joystick, not a keyboard), so the launching curses app must trap the next pad event and `pkill -x swayimg`, with a `timeout` backstop so a window can never pin open.
- **mako STYLING is keyed on app-name.** ETK notifs MUST send `app_name = "ETK Pitstop"` to match the criteria in `/storage/.config/mako/config` (cyan `#00b4d8` border, 1280×560). A wrong app-name silently falls through to the black default style. ETK notifs go out via `dbus-send … Notify` (there is **no `notify-send`** on the rig).

### PITSTOP READS THE GAMEPAD VIA RAW EVDEV (focus-independent) — and input_d does NOT run here
Pitstop opens `/dev/input/eventN` directly and reads evdev frames, **independent of Wayland focus** — so it keeps receiving pad input even when a swayimg window is on top. That is how "press any button to dismiss the preview" works (trap the next press, `pkill swayimg`, swallow the event so it doesn't also navigate underneath).
- **`input_d` is IN-GAME ONLY** (the Sentry spawns it only in `RUNNING`; ES owns the pad in menus). In the Pitstop/Tools/ES context it is simply **not running** — do not try to route menu-context input through it; the reading app handles its own pad.
- On this pad the **D-pad is a HAT axis (`ABS_HAT0X/ABS_HAT0Y`), not `EV_KEY`** — "any button" logic must cover both, and must gate on PRESS (`val != 0`), since the release (`val == 0`) of the press that opened a thing arrives immediately after.

### THE DOMINANT GPU CRASH IS DMESG-ONLY — and grim can still photograph it
The a6xx GPU fault → `recover_worker hangcheck` → wedged `rsx::thread` freeze leaves **NO core, NO RPCS3 fatal, process `state=S`**. Core/log/proc-exit watches ALL stay silent — **only a `dmesg` watch** (`grep -iE 'a6xx|gpu fault|hangcheck|recover_worker'`) catches it. SURPRISE: **`grim` cleanly captures the frozen frame** even after the kernel hangcheck-recovers the GPU — sway is a *separate* GPU client from the wedged emulator thread, so it still composites the last frame. `recovery.sh` grabs it (best-effort, `timeout`-bounded so it can NEVER stall the nuclear recovery) BEFORE the kill; the frame is bound to the crash's ledger row (`crash_shot`, col 16) and previewed via swayimg.

### DEPLOYMENT & LEDGER-SCHEMA DISCIPLINE (garage gotchas)
- **`install.sh` pushes `tools/` SELECTIVELY** — only the genuinely rig-runtime scripts (`etk_drift.py`, `vault_sweep.sh`); the rest of `tools/` is host-side. **Any new rig-runtime dependency MUST be added to install.sh's push list.** A one-off `scp` does NOT survive: `uninstall.sh` does `rm -rf $ETK_ROOT/tools` and a reinstall only restores the listed files. (This was the live "Manage Shaders: no boundary" bug — `vault_sweep.sh` had only ever been hand-pushed, so a plain uninstall/reinstall cycle silently dropped it while leaving the data it reads intact.)
- **profile.d injections live OUTSIDE `$ETK_ROOT`** (`/storage/.config/profile.d/09x-etk-*`), so an `$ETK_ROOT` cleanup does NOT remove them — `uninstall.sh` must delete them explicitly, or ETK keeps altering Turnip/Mesa after it's "uninstalled."
- **The telemetry ledger grows by APPEND-ONLY TRAILING columns** (`tune_tag` col 15, `crash_shot` col 16). Every reader indexes positionally (`cut -fN` / `fields[n]` with `len`-guards), so older narrower rows stay valid. NEVER insert a column mid-row — it silently shears every prior row.

## ROCKNIX AUDIO STACK (SM8250) — BOOT RACE + PIPELINE MAP (validated live 2026-07-02)
The rig's audio path: game SPURS/SPU jobs → cellAudio HLE (256-sample blocks @48 kHz = 5.33 ms each)
→ cubeb (backend "pulse") → pipewire-pulse → PipeWire (`clock.force-quantum=1024` = 21.3 ms, rate
locked 48 kHz) → ALSA card 0 "RetroidPocket" (snd-sm8250 machine driver + WCD938x codec over
soundwire, clocked off the ADSP via q6afe/q6prm).
1. **PER-BOOT AUDIO COIN FLIP (THE SILENT-BOOT TRAP):** the sound card intermittently NEVER
   probes (observed 4 of 13 boots, 2026-07-02, incl. panic-reboot clusters). Root: an early
   `qcom-q6afe: AFE failed to vote (3)` (race against ADSP `audio_pd` bring-up) fails the probe of
   `3370000.codec` (va_macro — the clock supplier for ALL LPASS macros); every downstream device
   then parks in `/sys/kernel/debug/devices_deferred` FOREVER (nothing re-triggers deferred probe).
   Result: zero ALSA cards → PipeWire has only "Dummy Output" → EVERY client (ES, RPCS3 — cubeb
   pins `auto_null`) is silent for the entire boot. The operator's session sees "no audio", not an error.
   - **DETECT:** `/proc/asound/cards` → "no soundcards"; `wpctl status` → only Dummy Output;
     RPCS3.log → `DeviceID: "auto_null"`.
   - **REVIVE WITHOUT REBOOT (validated live, N=1):** `echo 3370000.codec >
     /sys/bus/platform/drivers_probe` — the deferred chain cascades up in ~2 s (macros → soundwire
     → wcd938x → card 0) and PipeWire hot-swaps in the real Speaker/Headphones sinks. NOTE:
     `drivers/va_macro/bind` is Permission denied — use the bus-level `drivers_probe` node.
   - **LAW: any audio experiment or A/B session MUST gate on card presence first** — a silent-boot
     session poisons audio data and masquerades as "audio broken".
2. **AUDIO UNDERRUNS ARE FORENSICALLY INVISIBLE:** when RPCS3's backend ring runs dry it
   zero-fills SILENTLY (AudioBackend.cpp memset — no log line, no counter, at any log level).
   Audible stutter leaves NO trace in RPCS3.log; objective measurement requires fork-side counters
   (campaign spec: `dossiers/AudioCampaign_20260702.md`).
3. Dial semantics headline (full map in the dossier): `Time Stretching Threshold` is BUFFER-FILL %
   (not fps) and is DEAD unless `Enable Time Stretching: true`; buffer size absorbs jitter only —
   sustained production deficit (game below full speed) is what time stretching exists for.

## STAGE IV — TURNIP FORK TOOLCHAIN & GPU-HANG FORENSICS (hard-won; not stock-mesa intuition)
The Stage-IV loop (build a patched Turnip → deploy → spot a hang → decode the `hangrd` redump) has its
own gotchas. None are obvious from stock Mesa docs; each cost real garage time (2026-06-19). Front-load.

### hangrd captures are routinely INCOMPLETE — `size-stable` is NOT enough to trust one
`cat /sys/kernel/debug/dri/0/hangrd > x.rd` frequently emits a **truncated** final `RD_BUFFER_CONTENTS`
**AND omits `RD_CMDSTREAM_ADDR`** (the cmdstream-entry pointer `cffdump` needs). Result: `cffdump`
decodes **0 draws** even though the full GPU memory image is present. Seen 2/2 on addr-less captures; one
truncated *before* any R3, so it is the **kernel node's own dump, not an early-R3 race**. The capture is
RECOVERABLE: drop the incomplete trailing section, synthesize `RD_CMDSTREAM_ADDR` from the dmesg
faultinfo `ib1` (size it to the FULL containing `RD_GPUADDR` buffer — the dmesg `ib1_size` is the
*remaining* count and truncates the decode). Tooling: `scripts/turnip/rd_inspect.py` (section/truncation
check) + `rd_repair.py` (validate+repair); `rocknix_spotter_loop.sh` now self-repairs in place on catch.
Redump layout: section = `[u32 type][u32 size][bytes]`; enum in `src/freedreno/common/redump.h`
(`RD_GPUADDR=3`, `RD_CMDSTREAM_ADDR=6`, `RD_BUFFER_CONTENTS=12`); the type-3/6 payload is
`[gpuaddr_lo, size, gpuaddr_hi]` (per `decode/rdutil.h parse_addr`).
**TWO DISTINCT truncation causes — don't conflate (2026-06-19):** (a) the NODE emits an incomplete redump even when size-stable (above; recover with `rd_repair.py`); (b) **`rocknix_spotter_loop.sh` KILLS the `cat` only 6 s after the fault** — for a large working set (long play session) the redump streams for >6 s, so the kill TRUNCATES it mid-stream, dropping BOTH cmdstream IBs (a London capture lost ib1 AND ib2 → undecodable, unrepairable). **FIX (validated this session): wait for the `.rd` to be SIZE-STABLE (~10 s of no growth) before killing, not a fixed 6 s** — a no-kill-timer manual capture got a complete 587 MB decodable redump where the 6 s-kill got a 211 MB truncated one. Fold the size-stable wait into the spotter's post-fault finalize.

### VERIFY THE ACTIVE FORK DRIVER BY SIZE, NOT `vulkaninfo`
A patched and a stock build BOTH report `driverInfo = Mesa 26.1.3` (we don't bump the version string), so
`deploy_rocknix.sh`'s `vulkaninfo` line is NOT a real verification. Confirm the live driver by
`stat -c %s /usr/lib/libvulkan_freedreno.so` (or hash). Consider stamping a per-build marker
(unique exported symbol / `MESA_GIT_SHA1`) on fork builds.

### `deploy_rocknix.sh` bind-mounts STACK across iterate-deploys
Each deploy adds a `mount --bind` over the previous (`mount | grep -c freedreno` climbs to 3+); the
topmost wins but it's a confusing state and a fragile revert path. Unstack to a single clean bind:
`while mount | grep -q " $TGT "; do umount "$TGT"; done; mount --bind /storage/turnip/...so "$TGT"`.
The bind is RUNTIME-only — a cold boot reverts to the persistent (`etk-turnip.service`) driver.

### The build loop: use INCREMENTAL ninja, and NEVER pipe ninja to `tail`
- `build_rocknix.sh` does `rm -rf build-rocknix` → a FULL mesa rebuild every run. For the iterate loop,
  if the build dir survives, **`ninja -C build-rocknix -j4 src/freedreno/vulkan/libvulkan_freedreno.so`**
  recompiles only the changed file + relinks (~1–2 min vs a full build).
- **`ninja … | tail` MASKS ninja's failure exit** (`set -e` does not catch a piped command's failure) →
  the script copies the STALE prior `.so` and falsely reports "BUILT". Gate on ninja's real exit:
  `if ninja … > log 2>&1; then cp …; else …; fi`. (A patch build "succeeded" on a stale binary this way.)

### Turnip 26.1.x source is `.cc` (C++); the build tree is NOT a git checkout
- Files are `tu_clear_blit.cc` / `tu_cmd_buffer.cc` (not `.c`). `struct tu_cache_state x = {};` **fails to
  compile** — a `BitmaskEnum` member rejects aggregate brace-init; copy-init from an existing instance
  (`= cmd->state.cache`) then override fields.
- `/work/mesa-26.1.3` (from the `build_rocknix.sh` tarball curl) has **no `.git`** → no `git diff`/`stash`
  to inspect or revert a patch; keep a manual `*.etk-stock` backup. (The `26.2` rebase intel comes from a
  SEPARATE git clone — don't conflate the two trees.)

### cffdump defaults to PER-TILE decode (400 MB+) — always `--once`
Without `--once` the cmdstream is decoded for every tile → hundreds of MB / millions of lines. Use
`--once` for a compact per-draw summary; `-D N` for one draw; `--dump-shaders` / `--bindless` for the
ir3 / descriptor contents at the faulting draw. These tools must run IN-TREE in the `turnip-rocknix`
container (libarchive rpath; need libxml2). `.rd` are freedreno REDUMP → decode with **`cffdump`**, NOT
crashdec (crashdec is for ASCII kernel devcoredumps; it emits garbage on a redump).

### Driver-update cache invalidation = a long COLD LAUNCH, not a shader storm
A new `.so` build ID invalidates the cache; the existing vault's pipeline objects are **recompiled for the
new driver at launch, BEFORE the menu** (observed +9 K shaders pre-menu). There is **no in-game stutter**
from this — shader *storms* come only from not-yet-encountered shaders (a function of game content).
Correct A/B after any driver swap: launch once (cold recompile) → **graceful-exit to bank the warm,
driver-matched cache → relaunch warm and test** (true warm-vs-warm vs the saturated baseline).

### Cockpit forensics: false-SILENT on graceful exit; manual captures lack faultinfo
- A graceful emulator exit leaves `live_stat` lingering at its last value (process gone), while a real
  silent freeze leaves the process ALIVE. The spotter now auto-distinguishes via an `emu_alive()`
  cmdline `/proc`-walk and gates the SILENT class on it: a graceful exit reports `>>> GRACEFUL EXIT`
  with NO crash, NO stub, NO capture. The old false-`SILENT` + ~28 B header-only stub is **FIXED
  (2026-06-29)** — the manual disarm-around-exit dance is no longer needed.
- Only the spotter writes the `.faultinfo` sidecar (dmesg `ib1/ib2/fence/status`). Hand-grabbed `manual_*`
  captures lack it → no fault address → only structural profiling is possible. Write a faultinfo alongside
  any manual capture.
- **Turnip dials MUST be set via the Pitstop DRIVER tab, never a remote `profile.d` hand-write (DEFIES EXPECTATION).**
  `_driver_apply` writes the `097-etk-turnip-dials` export AND `active_tune.txt` *atomically*; the spotter +
  the ledger stamp each race's `tune_tag` (col 15) from `active_tune.txt`. A remote `echo > 097-etk-turnip-dials`
  desyncs the tag → the A/B session is mis-attributed, poisoning the saturated-vs-saturated comparison. Drive
  the DRIVER tab (operator, or cockpit pad-injection); a flag NOT in the ladder gets ADDED to the tab, not hand-injected.
- **A dmesg-only crash-watch CANNOT capture the redump.** The hangrd node is arm-BEFORE-the-hang — it blocks for
  the *next* wedge; a post-hoc `cat` of an already-recovered hang captures 0 B. An unsupervised crash-hunt must
  arm the `cat` at idle, not just watch dmesg.
- **The rig is always ready at session start** — powered, on USB-net (`169.254.170.2`) + WiFi. Do NOT spend a
  tool call confirming reachability before starting; go straight to the work and handle an actual failure
  reactively if one occurs.

### Forensic doctrine reaffirmed (Stage IV): NEVER crown a fix from one clean run
GT5P's clean-run noise floor is huge (77–2886 s). A single clean race — even a Gold Trophy — is variance,
not a cure: this bit us twice (stock 26.1.3, then Turnip Patch #1). A/B saturated-vs-saturated on a fixed
dial, with a contemporaneous control, and **confirm any apparent win to N≥3 full races** before believing
it. Forced-serialization around the *symptom* (where the CP parks) does not fix an *upstream* wedge —
rule out your own patch's effect by confirming the patched events are actually in the cmdstream decode.

## BUSYBOX LIMITATIONS & PITFALLS
* **DU:** Use `du -k` (KB) or `du -m` (MB). BusyBox `du` often lacks `-h` (human-readable) or behaves inconsistently with it.
* **FIND:** BusyBox `find` is extremely limited. Avoid complex `-exec` or `-regex`. Use `find | wc -l` for counts.
* **STAT:** Do not use `stat --format`. Use `readlink` for symlink resolution.
* **PROC:** Since RPCS3 runs in an encapsulated AppImage/Dwarfs mount, always use `/proc/$PID/cmdline` and `/proc/$PID/environ` for discovery.
* **AWK:** Use `awk` for floating-point math; BusyBox `sh` cannot handle decimals and `bc` may not be present in all builds.


## THE TWO PACKAGING MODELS (.pkg vs .iso) — FORMAT IS A FIRST-CLASS VARIABLE
PS3 titles arrive as two DISTINCT RUNTIME MODELS, not two file extensions. ETK tooling is ~100% .pkg-biased (measured 2026-06-12: zero iso/bdvd handling) — mostly rational economics (.pkg needs machinery: headless installer, .rap licenses, gamelist injection; .iso = copy a file), but the runtime differences are load-bearing:
1. **Spawn topology:** digital/.pkg GT titles are DRM-SPAWN (`EBOOT.BIN` launcher → spawns `EMAIN.SELF`); disc/.iso boots direct. This single axis explained the card#2 saga (disc fine, DRM-spawn crashed) and forced the flush-after-burst design in the aPS3e VkPipelineCache patch (spawned executables never reach clean teardown).
2. **Cache topology:** per-executable `ppu-<hash>` cache dirs → DRM-spawn titles carry TWO pipeline caches (launcher + game), disc titles one. Vault accounting and pro-tune bundles must know which executable's cache matters.
3. **I/O model:** .pkg = scattered reads from dev_hdd0; .iso = streaming one large file (GT5 = 19.4 GB). Treat as separate test classes in any storage-tier experiment.
4. **DRM:** .pkg needs `.rap`; .iso needs a decrypted image (or `.dkey`). Failure smells differ: `CELL_ESRCH` (DRM-spawn init) vs `CELL_ENOTMOUNTED /dev_bdvd`.
5. **Update asymmetry (UNTESTED PATH):** disc games take updates AS .pkg into `dev_hdd0/game/<ID>` — ETK's PKG installer has never been tested with an update-PKG whose base game is a disc image.

**LAW: any emulator/cache/install fix MUST be validated on BOTH models before being called done.** The cache-bug history shows why: a teardown-save pipeline cache would have *appeared correct* on .iso titles and silently failed on DRM-spawn .pkg titles — the format axis hides bugs from fixers.

**OBSERVABILITY BIAS (operator doctrine, 2026-06-12):** shader-storm pain filters which games stay in player rotation. Light-shader titles (e.g. RR7) play well immediately → high rotation → generate most community evidence. Shader-heavy titles are abandoned before saturation → the players who CAN observe deep cache/stability bugs are rare "gluttons for punishment" obsessing over one series on one device. Therefore: absence of community reports on a shader-heavy-title bug is WEAK evidence of absence; compatibility lore systematically under-represents exactly the titles ETK exists for. Weigh on-rig telemetry over community consensus accordingly.

## ROCKNIX RPCS3 PATHS
* **CUSTOM CONFIGURATION FILES** `/storage/roms/bios/rpcs3/custom_configs/config_[GAMEID].yml`
* **ETK Cache Vault Symlimk** `/storage/roms/bios/rpcs3/cache/[GAMEID]`
* **VIRTUAL HDD PATH PATTERNS:** Do NOT assume standard desktop path resolutions or that global mapping roots are used (e.g., `dev_hdd0/savedata` only anchors empty `vmc` volumes). Rocknix isolates actual emulator user save blocks inside localized nested structures under the individual user profile index:
  `~/roms/bios/rpcs3/dev_hdd0/home/00000001/savedata/`
  Targeted cleaning operations or resets must trace files from this exact explicit directory footprint.
* **RUNTIME PROCESS = `AppRun.wrapped` (NOT `rpcs3-sa`):** `rpcs3-sa` is only a launcher stub; the live emulator is the `AppRun.wrapped` process (target it for runtime probes — VMAs/RSS/FDs — and for "is a game running?" checks). This matters because **RPCS3 rewrites its config on exit**, so any edit to `input_configs/` (e.g. `global/Default.yml` pad config) or `custom_configs/` MUST be done with RPCS3 **closed**, or it gets clobbered. ⚠️ PITFALL: `pgrep -f "rpcs3|AppRun.wrapped"` self-matches its own shell (the pattern string is in the command line) → false "RPCS3 running". Use `pgrep -x AppRun.wrapped` (matches the process name, not the cmdline), or exclude `$$`. ⚠️ FURTHER (Stage IV, 2026-06-19): `pgrep -x AppRun.wrapped` ITSELF proved UNRELIABLE for liveness — observed EMPTY for entire live in-game runs. For "is a game running?" prefer gating on `live_stat` freshness (what `rocknix_spotter_loop.sh` does), or iterate `pgrep -f 'AppRun.wrapped|rpcs3'` and read each `/proc/<pid>/environ`.

## FILE REGISTRY (THE ETK ANATOMY)
### 1. CORE ENGINE
* **env.sh:** The Heart. Defines paths, thermal thresholds, and the Agnostic ID logic.
* **install.sh:** The Provisioner. Deploys the toolkit, sets up symlinks, and generates the `01-etk-sentry.sh` Sentry.
* **uninstall.sh:** The Cleaner. Restores stock hardware states (GPU/CPU governors) and stops daemons.

### 2. DAEMONS (THE WORKERS)
* **01-etk-sentry.sh (etk_sentry.service):** The State Machine. Runs constantly in the background. Tracks emulator ignition (IDLE vs RUNNING), resolves the Game ID, and orchestrates the live/die cycles of the thermal and vault daemons.
* **vault_d.sh:** The Accountant. Resides in `/bin`. Tracks "NEW" vs "BANKED" shaders in real-time.
* **thermal_d.sh:** The Governor. Resides in `/bin`. Manages CPU/GPU clocks and triggers "PIT" (Cooldown) vs "RACE" (Performance) modes.
* **input_d.py:** The Shifter (v10.2.0). Python evdev daemon on the InputPlumber virtual pad; maps chords to ETK commands — R3 panic, L3 HUD toggle, L1 screenshot, SELECT-clutch DPAD chords (Right=VAULT, Left=mango toggle, Up=screenshot), and **R1+DPAD-Down = pad-movie 3-mark pulse (IN→OFFSET→OUT) + record** (record armed via `pad_movie.mode`, frames stream to `pad_movie.dat`, marks to `pad_movie.offset`; replay is a separate uinput injector, not built yet). Pad-model-agnostic match by NAME (DS5 era), not node index. **IN-GAME ONLY:** it reads *passthrough* events, so chords/record only fire while the emulator owns the pad — at the ES carousel the frontend owns the controller and the daemon sees no frames. Never `EVIOCGRAB`s (events always reach the game). All pad-movie file I/O is fail-silent so it can never break the R3 panic path.

### 3. INTERFACES (THE UI)
* **mango_bridge.sh:** The Dashboard. Translates SHM data into MangoHud custom text.
* **commander.sh:** The Pit Wall. Remote terminal UI for manual overrides, shader backups, and diagnostics.
* **probe.sh:** The Forensics. Captures kernel logs and RPCS3 traces for crash analysis.

### 4. UTILITIES
* **agnostify.sh:** The Migrator. Converts legacy hardcoded GT5P installs into the new Agnostic vault structure. (CONSIDER FOR DEPRECATION)

### 5. DESIGN DOSSIERS (LOCAL-PRIVATE — NOT IN GIT)
* **`dossiers/`** is the design-rationale archive: one Markdown file per major decision (e.g. `ProTuningExportDossier.md`, `Stage3CustomRigDossier.md`, certification dossiers). It lives at `$ETK_ROOT/dossiers` on the operator's machine ONLY.
* **STATUS:** pulled from the public repo and **gitignored as of 2026-06-14** — same precedent as `vault/` and `state/`. The dossiers stay local and private; they are never committed, cloned, or archived.
* **DO NOT BE CONFUSED BY THEIR ABSENCE:** code comments and docs cite them heavily (`dossiers/<Name>Dossier.md §N`, or shorthand `dossier §N`). Those citations are design provenance, not a promise the file is in the checkout. If you clone the repo, the referenced files will not be present — this is expected, not a missing-file bug. Read them locally if available; do not try to "restore" them into the repo.

## SHM DATA MAP (/dev/shm/etk_shm/)
* `active_id.txt`: The current Game ID (e.g., NPUA80075).
* `etk_mode.txt`: Current thermal profile (RACE/PIT).
* `live_stat.txt`: The final string injected into MangoHud.
* `vault_count`: Total shaders in the active vault.
* `vault_new.txt`: Shaders compiled during the *current* session.
* `etk_cmd_queue`: Pipe for `commander.sh` to send instructions to daemons.

## [LOCKDOWN] AGNOSTIC HUD STABILITY
- **CRITICAL**: The `TARGET_ID` must be resolved via the Deep Scan loop in the Sentry daemon to handle multi-process emulator environments.
- **HUD FORMAT**: `mango_bridge.sh` MUST use the atomic `echo > file.tmp && mv file.tmp file` pattern.
- **STRING ARCHITECTURE**: Do NOT add configuration keys like `custom_text_center=` inside the `live_stat.txt` file; keep the string raw for external parsing.
- **PATHING**: All background daemons (Bridge, Vault, Thermal) must be invoked using absolute paths via `$ETK_ROOT` to ensure reliability across reboots.

## [LOCKDOWN] ARCH: install.sh must remain Tier-Aware.
- VARIABLE: ETK_BUILD_TYPE (defined in env.sh) controls the execution of thermal_d.sh and mango_bridge.sh.
- CLEANUP: Moving from FULL to LITE must trigger the Sentry to kill active HUD/Thermal processes.

## [LOCKDOWN] DEV TOOLS

## UI ARCHITECTURE: commander.sh MUST maintain a split-pane layout. Top pane = Live Telemetry. Bottom pane = Raw Forensic Text. Do not consolidate into a single "Mode."

## FORENSIC INTEGRITY: probe.sh MUST use strings when reading any file from /storage or /dev/shm to prevent terminal corruption during binary floods.

## COPY-PASTE OPTIMIZATION: The bottom pane of the Pit Wall terminal MUST remain free of ANSI color codes or borders within the data content to facilitate seamless copying into an AI assistant for analysis.

## IMMUTABLE COMMENTS: Any script update MUST include a "GEMINI IMMUTABLE RULE" block in the header to inform subsequent models of structural constraints.

## [LOCKDOWN] HUD FORMATTING

- **HUD FORMAT STRICT LOCK**: The instrument string layout is locked to a dense, space-trimmed format that uses punctuation and short text strings to serve as the DDU (Driver Data Unit from racing cars) to preserve Flip 2 screen real estate. Future iterations MUST NOT expand spacing or add decorative characters unless custom font and unicode support is feasible, recommended, tested, and approved.
  - *Format:* `ETK:INSTALL_MODE|TARGET_ID|XX°C STAT|X.XX STAT|XX% STAT|XXMB XXX NEW:XX|`
  - **2026-06-28 MORATORIUM (operator-granted re-design): the HUD BODY is now MODE-SWITCHED via `ETK_HUD_MODE` (etk.conf).** `BASIC` = the original `TEMP|LOAD|RAM%|shaders` body. `GINSTR` = `TEMP|JITTER|SLIP|shaders`, swapping LOAD+RAM for two live, fixed-width (5-slot) frame-pacing gauges off the MangoHud autolog, animated by a per-loop phase counter — `JITTER` (direction/flow; `»`=settling-forward/improving, `«`=degrading-back, arrow count=velocity, arrows marquee across the slots) and `SLIP` (pacing-slip severity bar 0..5 filled with `+`: `·····`→`+++++`; at MAX it pulses `+++++`⇄`=====`). GINSTR refreshes at 0.5s (BusyBox fractional-`sleep`-gated, else 1s); BASIC stays 1s. The three-stage time-gated header and the strict-lock (no decorative bloat, atomic swap, raw text, dense-punctuation DDU) STILL apply to BOTH bodies — the lock now covers a two-mode format, not one. `«` and `·` are Latin-1 (same range as the existing `»`). Gauge thresholds feel-tuned on-rig. Impl: `bin/mango_bridge.sh` §4.5.
  
### [LOCKDOWN]

- **SYNC Logic Specification**:
- PULL: New shaders always archived on computer at every run of `install.sh` so it works as a repair, update, and sync tool. All transfers must include professional `--progress` reporting indicators and clear out `.DS_Store` metadata noise.
- PUSH: sync found games on handheld with shaders found in computer vault.
- `uninstall.sh` should remove vaults (unless they can be injected into the regular Vulkan or RCPS3 cache so ETK would not be needed for their use after uninstall?)


## DEFEATING VOLATILE DIRECTORIES (THE SENTRY TRIPWIRE)

**THE ROCKNIX REBOOT BOSS:** Rocknix aggressively wipes and rebuilds user-interface directories like `/storage/.config/modules/` during the boot sequence. This wipe is highly asynchronous and often happens *after* `systemd` services tied to `multi-user.target` have already executed.

**THE IMMUTABLE RULE:** Never rely on a one-shot deployment or a static boot script to persist files in Rocknix volatile directories. 

**THE SOLUTION (ACTIVE TRIPWIRE):** To keep native bash apps (like `etk_pitstop.sh`) persistent in the Tools menu, the `etk_sentry` daemon MUST employ a self-healing active tripwire inside its primary polling loop.

1. Store the master copy of the module safely in persistent storage (e.g., `$ETK_ROOT/config/`).
2. Inside the Sentry's `while true` loop, continuously monitor the volatile target directory.
3. If the file is missing, immediately re-inject it and re-apply `chmod +x`.

**Implementation Standard:**
`bash
# Inside the Sentry while loop:
if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ]; then
    cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
    chmod +x /storage/.config/modules/etk_pitstop.sh
fi]`


## MISC

- **SENTRY STATE MACHINE (THE IGNITION LOCK)**: The `etk_sentry` service MUST operate as an event-driven state machine. It tracks the `rpcs3` process transitioning between `IDLE` and `RUNNING`.
  - Background daemons (`vault_d.sh`, `thermal_d.sh`) MUST NOT be executed during the OS boot sequence.
  - The Sentry must wait 4 seconds after `RUNNING` is detected before launching daemons to ensure the `TARGET_ID` string is captured cleanly from process parameters.
  - **RACE-PROOF IDENTITY SYNC**: To eliminate path-shifting race conditions, the Sentry must completely resolve and commit the definitive Game ID string to `ID_FILE` **before** spawning any downstream worker daemons.
  - **ATOMIC SESSION RESET**: The Sentry must atomically reset `vault_new.txt` to `0` at the exact moment of emulator ignition. This ensures worker daemons baseline cleanly from zero every single game launch without relying on internal automated loop resets.
  - When transitioning back to `IDLE` (graceful exit or nuclear recovery), the Sentry MUST actively `pkill` the daemons to flush stale memory.


