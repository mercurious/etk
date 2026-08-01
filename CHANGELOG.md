# Changelog

All notable changes to the ETK are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Chiaki: connection status screen.** Choosing a console now shows the
  session's phases in the branded frame as they happen — WAKING CONSOLE,
  REQUESTING SESSION, CONTROL LINK UP, STREAMING — and an error quit shows
  its reason in big type for a beat before returning to the chooser. The
  blank terminal between the chooser and the video is gone.
- **Chiaki: BITRATE row in the chooser** — cycles auto / low / medium / high
  across every registered console. auto leaves the bitrate to the resolution
  preset (console-managed); low/medium/high scale it to 50%/75%/150% (capped
  30 Mbps). Client-side knob `bitrate` in each console config.

### Fixed
- **Chiaki: chooser button hints now show the Flip 2's real labels** — B
  selects, A backs out. The shell is Nintendo-labeled over the pad's
  south/east positions; behavior was always correct, the printed letters in
  the hint bar weren't.

### Staged (dev, not yet certified)
- **RPCS3 GTK Edition v0.8.1-dev** (2026-08-01) — 0.8.0-dev **plus a temporary
  revert of upstream `1d657c4e6`**, the bisect-proven cause (8 hardware rounds)
  of a deterministic GT5P Spec II [BCUS98158, ISO] `CellSpursKernel0` boot fatal
  on aarch64: skipping the SPU reduced-loop pattern reroutes an older-SPURS loop
  through standard SPU LLVM compilation, which is miscompiled on ARM64 (x86
  unaffected — upstream CI can't see it). Validated on-rig: Spec II boots past
  copyright, Spec III clean. Upstream regression report + #11912 thread reply
  drafted (operator posts).
  - **#11912 A/B result (the bump's motivating question): SEPARATE.** On one
    binary, env-toggled: default arm bright ×3 (marker present — the parked-TIU
    state is still programmed on 19638); `GTK_REMAP0_ONE=0` arm reproduces the
    dim road. Upstream's GT6 mirror fix (PR #19090) does not touch #11912; the
    force-ONE workaround stays default-on.
  - Turnip pin advanced to **26.1.6 gtk_0.6 (zlatez)** — racing live since
    07-30 with clean ledger rows; 26.1.3 gtk_0.4 remains in `drivers/` as
    fallback.
- **RPCS3 GTK Edition v0.8.0-dev — upstream base bump 19544 → 19638** (2026-07-31).
  New base `a1deb2921` = kd-11's PR #19090 (shader-interpreter MSAA + depth-redirect
  sampling — the fix a contributor reports cured GT6's track-shadow mirror flicker)
  plus the full rsxfp/rsxvp interpreter correctness series, 94 commits total. The
  complete 0.7.5 feature set (remap fix, tguard, perfstat, semapark-v2, ffs-v5,
  avwiden-v1) rebased clean — no conflicts; `decoded_remap()` untouched upstream.
  Staged via `etk.conf` dev override (CERT pins unchanged until certification).
  Patch: etk-rpcs3-gtk `ff8beab`; artifact sha256 `297d2f28e1db…b2b`.
  - **First boot per game does a full PPU recompile** (upstream obj-cache tag
    v7 → v8) — long and thermally heavy, not a hang.
  - **Old savestates will not load** (upstream global savestate version bump).
  - Pending: cold-boot certification + the #11912 A/B (does *stock* 19638 fix
    the GT5P road dim-state? `GTK_REMAP0_ONE=0` arm vs default). Turnip pinned
    at 26.1.3 gtk_0.4 for the A/B; the 26.1.6/zlatez lane stays parked.
  - ffs-v5 00E59005 rescue baseline resets with the new emulator — fresh
    baseline to be recorded; do not compare against 0.7.5 numbers.

## [0.8.2] - 2026-07-30 — Chiaki-Rocknix Remote Play Edition

The kit gains a whole new lane: PS4/PS5 Remote Play, built as a true fork of
chiaki and shipped as a first-class ETK citizen. Stack otherwise unchanged:
RPCS3 **GTK Edition v0.7.5**, Mesa Turnip **26.1.3 gtk_0.4**, kernel
**rocknix-gtk-20260706-0.2**, base **ROCKNIX official 20260701**; new
flashable image **ROCKNIX-GTK-SM8250.aarch64-20260730** with the Remote Play
lane staged. **Update if** you own a PS4 or PS5.

### Added
- **"Chiaki Remote Play" in the ES Tools menu.** Launching it opens an
  on-device app — title screen, console chooser, and a full gamepad pairing
  wizard: it scans the network for consoles, takes the console's 8-digit PIN
  and your PSN account id on a gamepad-driven on-screen keyboard (either form
  psntools.com shows works — base64 or the plain number — or reuse the
  account from an already-paired console with one press). Pair every
  PlayStation in the house; pick one and play. An existing pairing made over
  ssh migrates into the chooser automatically.
- **The client itself is our fork, published like the other GTK-lane forks:**
  [chiaki-rocknix](https://github.com/mercurious/chiaki-rocknix) — an SDL2
  controller-first frontend over Florian Märkl's chiaki (upstream's CLI
  cannot stream and its GUI needs a desktop). The committed binary + its
  provenance ride the kit; BUILDING.md reproduces it in one container run;
  STANDALONE.md installs it on stock ROCKNIX via Ports with no ETK at all.
- **In-game DualSense haptics, felt as rumble.** PS5 titles send no classic
  rumble events at all — GT7's road feel arrives as a haptics audio stream.
  The client negotiates it (protocol work ported from chiaki-ng) and converts
  it to controller rumble. `haptics = off/weak/normal/strong` per console.
- **Change resolution and codec mid-game.** Hold R1+L3 to flip 1080p/720p,
  L1+R3 to flip h265/h264; the setting persists and the stream reconnects in
  place (~10s) with mako toasts narrating. The Remote Play protocol pins both
  at session start — a live switch is impossible, so the kit makes the
  reconnect honest instead of hiding it.
- **Handheld-honest session UX.** Console in rest mode? The client wakes it
  on connect. Put it back to rest mid-game? Clean exit back to the chooser
  with a "Console Sleeping" toast — no terminal prompts anywhere (the rig has
  no keyboard). A previous session that died hard self-heals with retries
  instead of surfacing Sony's 0x80108b10.
- **Trigger deadzone with rescale** (`trigger_deadzone`, default 10%). The
  Flip 2's triggers rest off zero after their first pull — measured L2 at
  12/255 with no deadzone declared anywhere — which streamed as permanently
  dragging brakes in GT7. A full pull still delivers the full 255.
- **`ETK_CHIAKI=0`** in etk.conf skips the whole lane (kill-switch precedent).

### Changed
- **The Tools-menu registrar (`etk_modules_inject.py`) is now a table** and
  registers any number of ETK apps; the Sentry tripwire re-asserts the Chiaki
  entry alongside Pitstop after every boot wipe.
- **input_d stands down its R1+L3 / L1+R3 chords while a stream is active**
  (they belong to the stream's toggles); punchbox and recovery resume the
  instant the stream ends. Screenshots stay live throughout.
- **New rig-side toast helper** (`bin/etk_chiaki_notify.sh`) posts mako
  notifications that replace in place instead of stacking — usable by any
  future lane.
- `uninstall.sh` removes the Remote Play lane in lockstep; console pairings
  are preserved unless `--zap-vault`.

## [0.8.1] - 2026-07-27 — maintenance

A bug-fix release. Everything here was found by running v0.8.0 on real
hardware and every fix was verified on the rig before shipping. No stack
change: RPCS3 **GTK Edition v0.7.5**, Mesa Turnip **26.1.3 gtk_0.4**, kernel
**rocknix-gtk-20260706-0.2** and base **ROCKNIX official 20260701** are all
byte-identical to v0.8.0. **Update if** you install PS3 games on-device, use
the Private Paddock, or set up a rig from a freshly flashed card.

### Fixed
- **Paddock PUSH now finds saves for games that store them under a different name (GT6).** A game's save folder is named by the game, not by the disc you own — and Gran Turismo 6's US disc (BCUS98296) writes its saves as `BCJS37016-*`, the Japanese title ID. ETK only looked for folders named after the game, so GT6 pushed a bundle with no save in it and the pull had nothing to restore. Every other tested title happens to use its own ID, which is why only GT6 failed. There is nothing inside a save that points back at the disc, so this is now a lookup: `config/save_aliases.tsv` ships with GT6 mapped, and you can add a line for any other game that does this. A push that finds no save now says so plainly and lists the unclaimed save folders with the exact line to add — instead of reporting success.
- **Paddock PULL now actually restores your shaders.** A pull into a game that already had *any* shaders — which is every game you have launched even once — stopped after the first few and reported success. On the rig a 7,201-shader Gran Turismo HD Concept bundle delivered 67. Cause: the merge used a "keep existing files" flag that on this OS does not skip existing files at all, it **aborts the whole copy at the first one**, and the error was being discarded. The merge now completes, and it checks the result against what the bundle carried instead of reporting whatever happens to be in the vault. The same flaw was in the Pro Tuning installer and is fixed there too.
- **Paddock PULL now actually restores your save.** The restore refused to touch a save that already existed locally. Launching a game even once — which you must do to check the controller — makes RPCS3 write an empty save, and that empty save then blocked your real one from ever landing. On the rig the ten replay saves restored fine while the career save, the only one that mattered, was silently skipped. PULL now restores your save as you asked it to, and anything it replaces is kept alongside as `.paddock.bak.<timestamp>` rather than deleted. An identical save is left untouched.
- **A pull that goes wrong now says so.** The summary previously reported the vault total, which looked healthy even when almost nothing had been restored. It now reports what arrived — shaders added versus shaders in the bundle, config, and saves restored/replaced/already-current — and warns explicitly if the shader count comes up short.
- **The PS3 game installer no longer reports a false failure on a successful install.** It now installs headlessly — `rpcs3 --headless --installpkg` — exactly like the firmware installer, so nothing opens on screen, RPCS3 exits by itself when it's done, and the result comes from RPCS3's own report rather than from Pitstop guessing by watching folders. Previously the installer drove RPCS3's on-screen dialog, tapped Enter through a virtual keyboard, and then decided the install was finished by watching a directory stop growing. Field failure that closed this out (2026-07-24): GT HD Concept finished installing in 42 seconds and installed correctly, but RPCS3's windowed mode never exits on its own — so Pitstop watched an empty folder for its full 10-minute limit and declared "Install did not complete". No launcher was written and the game never appeared in the library. Failures are also explained properly now: an update package that doesn't match your installed game says so, instead of reporting a generic timeout.
- **A game or firmware installed before your first game launch is no longer deleted by that launch.** ROCKNIX points RPCS3's storage at your games card from its game-launch script, so on a brand-new rig — flash the card, drop a `.pup` and a `.pkg`, install both from Pitstop — RPCS3 had nowhere correct to put them and wrote into a temporary folder that the first game launch wipes. Both installers (and `install.sh`) now set that storage up before running RPCS3, and safely move anything already stranded into your games card. Found live on a fresh rig with PS3 firmware 4.93 and a 706 MB game sitting one launch away from deletion.
- **The firmware and game drop folders now exist on a rig set up from a flashed card.** They were only ever created by the host installer, so a card image — which never runs it — came up without them, leaving nowhere to copy your `.pup` or `.pkg` to. They are now created at every boot, on any install path, so they are there before you go looking; a card flashed before this fix gets them on its next boot. (This was not new in 0.8.1 — v0.8.0's image had the same gap.)
- **The README named the PKG drop folder `pkg_drop`**; it is `pkg_install_drop`. Corrected in all three places.
- **Uninstall finds games installed either way** — it now clears both storage locations, so a game installed before this fix is still fully removable.

### Added
- **The controller now just works in RPCS3 — no pad-config screen, no remapping.** ROCKNIX ships RPCS3 a pad config pointing at a controller called "InputPlumber GameController 1", which is the Xbox-style virtual pad the OS used to present. It presents a DualSense now, so on a Flip 2 that name matches nothing and RPCS3 quietly falls back to its "no controller" handler: buttons do nothing in game, and there is no error anywhere on screen to explain it. ETK now asks the rig's own SDL what your pad is really called and corrects the device line — at PS3 firmware install, and again at every Pitstop open so it repairs itself if a future OS update renames the pad again. Your button map, dead zones and trigger calibration are never touched, and a config that is already correct is left byte-for-byte alone. `ETK_PAD_BIND=0` in `etk.conf` to manage the pad config yourself.

### Changed
- **Installing a package keeps you in Pitstop.** The screen no longer hands over to RPCS3; you get the same spinner the firmware install shows, and the confirm screen reports the package size up front. The time limit now scales with package size instead of a flat 10 minutes, so a large title (GT5 is 19.4 GB) has room to finish on slow media.
- All staged `.rap` licence files are installed with the package, not just the first one found.

## [0.8.0] - 2026-07-22 — "the productization release"

Everything since v0.7.0. Certified stack: RPCS3 **GTK Edition v0.7.5** · Mesa Turnip **26.1.3 gtk_0.4** · kernel **rocknix-gtk-20260706-0.2** · base **ROCKNIX official 20260701**.

### Added
- **ISO onboarding** — copy a disc `.iso` into `roms/ps3/` and it becomes a real ES game: launcher generated, IRISMAN-style `[TAG]` names repaired to `(SERIAL)` form (brackets silently break ALL per-game ROCKNIX settings), whitespace runs collapsed, overlay enabled, config seeded. Automatic on next Pitstop open; `ETK_ISO_ONBOARD=0` kill-switch.
- **Golden Tune Seeding** — any playable title with no per-game config (disc or PKG) starts on the ETK golden tune instead of raw RPCS3 defaults; seeds are ledgered. `ETK_GOLDEN_SEED=0` kill-switch.
- **Disc identity resolution** — running-ISO titles resolve their game ID (games.yml path match + live-log serial) instead of falling back to a wrong PKG ID; ISO titles display real names throughout Pitstop (filename-stem resolution, no PSN dependency).
- **One-line install, every platform** — macOS/Linux/WSL `curl -fsSL …/get-etk.sh | bash`, Windows `irm …/get-etk.ps1 | iex`. Both fetch the kit without git, update in place on re-run, and hand off to the installer.
- **Hostless self-update** — Pitstop TOOLS → *Check for ETK Updates* checks GitHub releases and updates the ETK middleware in place, on device, with no computer. Idle-gated, fail-soft, ledgered; `ETK_SELF_UPDATE=0` kill-switch.
- **Flashable card image (hostless lane)** — two boots from flash to a fully live GTK stack: boot 1 auto-resizes to the whole card, boot 2 activates ETK with no host machine involved. Unique `ROCKNIX-GTK`/`GTKSTOR` labels make the card safe beside an internal ROCKNIX install.
- **SD-card boot entries in the GRUB menu** (install.sh-managed, self-healing dual-mode) — pick *ROCKNIX-GTK from SD card* once and the card boots while inserted; pull it and one detour boot restores the internal default. Never touches the default UFS entry.
- **GTK KERS — Kinetic Emulation Recovery System.** The in-race profiler measured **≈38% of all CPU cycles** burning in spins, polls and fault storms, recovered by four shipped units, each in the layer that owns it: **semapark** (GTK Edition 0.7.3), **avwiden readahead** (0.7.4), the **pad-poll golden default** and the **Relaxed-ZCULL pairing** (ETK v0.7.0 dials).
- **perfstat channel** — the fork reports its own PPU/SPU/RSX split, frametime, access-violation and flip-retire counters to telemetry, independent of MangoHud (ledger `perf` column).
- **Bog profiler** — chord `R1+DPAD-Down` mid-race takes a 30 s flame-graph perf sample of the live emulator, symbolized at capture and self-labeled with live fps. (RSX frame capture moved to `R1+DPAD-Up`.)
- **GRID mode** — big.LITTLE thread-affinity rungs (off/A/B/C) in the POWER tab, engagement marks persisted to telemetry.
- **Pad Poll Interval dial** + golden default 1000→4000 µs — the bog profiler found the 1 kHz pad re-check burning ~8% of ALL cycles in pack racing.
- **TUNING gains a Video section** (Default Resolution, Frame Limit, MSAA, VSync, Renderer, Shader Precision) plus an Accurate ZCULL stats field and refreshed pit-engineer help throughout.
- **`tools/release_sanity.sh`** — a release gate enforcing version-only artifact filenames (law #8): a strict `KERNEL.rocknix-gtk-<date>-<n.n>` pattern plus a feature-word denylist over the shipping config, so cruft like `-audiofix0` can't ship again.
- **`TRACK_MANUAL.md`** — the system manual and map (mission, machinery, forensic method, live frontier), now the session-start orientation document.

### Changed
- **RPCS3 GTK Edition v0.7.1 → v0.7.5** — anti-lock stage 4 (**ffs-v5** flip-status force-retire, converting the dominant remaining post-rescue freeze class into survived races), **avwiden-v1** and **semapark-v2** KERS units, crash overlay text unified to mixed-case "GTK Crash Recovery", and self-ID (`--version` / log header / about) reading *GTK Edition v0.7.5*.
- **G-INSTR is the default HUD** (live frame-pacing gauges); BASIC remains the simple option. Gauge label `JITTER` → `JTTR`.
- **Golden template defaults** — output resolution 720×480 → **1280×720** (native target) and SPU Block Size → **Safe**, a defaults-first stance for unknown titles. GT titles keep their tuned configs.
- **Strict Rendering Mode disc overlay removed** — refuted as a false fix; discs now seed the same clean template as PKGs.
- **Windows host port revived and synced to the current install.sh** (was 0.6.0): transport hardened from live field failures — wall-clock-bounded ssh/scp with closed-pipe stdin (kills the Win32-OpenSSH channel-close wedge), resolve-`.local`-once with wire-address pinning (kills mDNS stalls while keeping zero-config), visible progress on big transfers — plus a live-session guard, SD rebind and firmware drop folder.
- **Pitstop UX** — header simplified to a single version (`// ETK PITSTOP vX //`); TOOLS tab redesigned with a single-spaced menu, on-select help anchored above the footer, and honest footer labels (`A: Quit` at top level, `A: Back` inside).
- **Ledger semantics documented** — `epoch` is the session END, bog samples carry a `session_start` join key.
- **Kernel artifact renamed to a version-only scheme** (law #8): the v0.7.0 kernel `KERNEL.rocknix-gtk-20260706-audiofix0` became `KERNEL.rocknix-gtk-20260706-0.2` (same bits; sha256 `7207dbce…` unchanged). The published v0.7.0 asset was left as-is so its download links keep working.
- README: one-liners lead Getting Started; feature table refreshed.

### Fixed
- **LittleBigPlanet (and any double-spaced ROM name)** — MangoHud overlay and all per-game settings now work; filenames are whitespace-normalized at onboarding, working around a ROCKNIX `get_setting` unquoted-expansion bug (upstream report queued alongside the known `[]`-escaping flaw).
- **Windows installer** — four field-diagnosed stall classes eliminated (see the transport work above).
- **Ledger/bog analysis** — session-join semantics hardened (epoch = session END).
- `install.sh` law #7 (HARNESS vs TOOLING) codified; misc doc/manifest corrections.

### Known behaviour
- **ISO vs PKG frametime** — characterized with a same-game A/B (GT5P Spec II disc vs PSN): the ISO frametime sawtooth is access-violation/SPU-led invalidation churn, ~5× spike density vs PKG. Documented as known behaviour with the fix ladder banked.

## [0.7.0] - 2026-07-07 — "GTK Edition"

**ETK now owns the whole stack.** Where 0.6.0 introduced the custom RPCS3 emulator, v0.7.0 completes the set — a custom **kernel**, **Turnip driver**, and **RPCS3 emulator**, all ETK-built and deployed by `install.sh` — and turns the headline feature on **by default**: the **anti-lock rescue system** that catches a GPU wedge mid-race and keeps you driving instead of dropping to the menu. It also closes the last turn-key gap with a **one-tap PS3 firmware installer**, and lands the SM8250 silent-boot audio fix at the kernel root. Still targeted at Gran Turismo 5 Prologue (Spec II/III) and GT HD Concept, with GT5/GT6 riding the crash-net.

### Added
- **PS3 Firmware Installer** (Pitstop TOOLS → *Install PS3 Firmware*). Drop the official Sony `PS3UPDAT.PUP` into `roms/etk/firmware_drop/` and install it on-device — RPCS3 runs headless in the background (~1 min, no dialog to confirm). Firmware is system-wide, installed once; the `.pup` is kept for reuse. This removes the last setup step that needed a desktop RPCS3 (legal, free firmware — the download link + steps ship in the drop folder's README).
- **Fable's Challenge KPIs** — the race ledger now scores every session against the console-quality bar: `lock%` (share of the race at the locked frame target) and `perfect%` (share inside the perfect frame window), plus a per-session `rescues` count. Per-title targets (GT HD = locked-60, GT5P/GT6 = locked-30) and a **`SURVIVED`** classification that labels a keepalive-absorbed hang honestly instead of scoring it as a crash or a clean finish.
- **ETK Dyno** — a host-side, ledger-driven A/B judge that ranks a knob's settings by `perfect%` across N trials, so tuning calls come from data, not vibes.
- **Cockpit UX pass** in Pitstop: a 4-stop **MangoHUD punchbox** (R1+L3 cycles custom-top → custom-bottom → default → off, remembered per game); the **JITTER** and **ANTI-LOCK** DDU gauges (frame-pacing flow + a per-session rescue counter); a **Stability↔Performance** driver dial-view over proven `TU_DEBUG`+gear combos (raw dials demoted to Advanced); on-screen progress bars for the Manage-Shaders and Paddock long ops; and raw-ledger-data blocks in the telemetry detail views.
- **Resolution Scale** 85% and 90% rungs; **Trigger Calibration** top-end (H7b) for a saturating R2/L2.
- **Per-frame MangoHUD logging** + a per-session frame-curve archive (`mango_logs/`) — the data behind the road-feel detector.
- **`KERNEL_DEPLOY_MODE`** (Tier-K): `install.sh` deploys and default-boots the GTK kernel with a stock fallback entry, one grub-pick away.
- Forensics offload (rig → host move at every deploy) and core-dump hygiene (SD routing + per-crash prune + staging pre-flight).

### Changed
- **Full stack, and anti-lock is now DEFAULT-ON.** The certified stack is ETK's own **RPCS3 GTK Edition 0.7.0** (fence force-signal / RSX watchdog / FIFO-resync all on in-build), **GTK Turnip `gtk_0.4`** (query-survive on), and the **GTK kernel** (`audiofix0`, KGSL-parity keepalive baked into the boot cmdline). A GPU wedge that used to freeze the handheld is now caught and released mid-race — no `etk.conf` flags required. Each switch keeps a documented `=0` kill-switch. GTK-kernel default-boot is **Flip 2-guarded** (other SD865 devices stay stock-default until a tester verifies them).
- **SM8250 silent-boot audio is fixed in the kernel** ([rocknix-gtk](https://github.com/mercurious/rocknix-gtk) Patch #2 `q6afe-vote-probe-race`): the ADSP's dropped clock-vote reply used to park the whole audio chain in deferred-probe on ~1-in-4 boots; the kernel now retries the vote in place and the card comes up first pass.
- **The forks self-identify** — RPCS3's About/version string, the Turnip driver marker, and a GTK boot-identity line now report "GTK Edition v0.7.0", so a field build is unambiguous.
- **Boot menu** reordered — "ROCKNIX-GTK for Flip 2" is the default entry, a verbose entry second, stock fallback third.
- The install-time **notification** (mako) is centered and its applier rewritten in place, so already-installed rigs pick up the reposition on update instead of appending a duplicate.
- **RACE** power preset pins the GPU at the 800 MHz OPP ceiling (a floor pin, not a runtime OC).
- **PS3 install storage-coherence** — the firmware and PKG installers now resolve RPCS3's real data paths (its config-dir symlinks under `/storage/roms/bios/rpcs3`), self-provision the tree on a fresh card (RPCS3 checks free space *before* creating the folder, which broke a first install), and refuse a split-brain (a second SD card shadowing your games tree) with a clear message.

### Fixed
- **SD game-tree rebind** (crash-card storage model) rewritten to v3: discovers the games card by **label** instead of a hard-coded UUID, exits cleanly when no games card is present, and can no longer stall UI bring-up ~30 s on boot. Now generated by `install.sh` (previously a hand-pushed script that could silently drift).
- The **Sentry env bomb** — `PYTHONPATH` self-appended every tick and eventually blew the environment-size limit (`E2BIG`), wedging the Sentry after ~1h45m uptime; env exports are now absolute.
- `install.sh` refuses to deploy while a game is running (a mid-race install cost a session).
- The DRIVER tab's currently-selected build is never pruned from the Turnip catalog.
- The Black Box drift tripwire accepts any `panic=` value; ETK daemons/scripts ship with the exec bit set.

### Removed
- **Audio watchdog** (`etk-audio-watchdog.service` + `scripts/audio_watchdog.sh`) — the silent-boot bug it worked around is fixed in the kernel (above), so the userspace revive is retired; `install.sh` tears the old unit down on update. The ledger `snd=` column is now card-presence-only (`ok`/`nocard`/`dummy`). (Unrelated and unchanged: the RPCS3-fork audio **stutter/underrun** telemetry, ledger `aud=`, stays.)

### Known issues / deferred
- **Image branding deferred.** The flashable plug-n-play image gets its boot logo + OS self-report in a dedicated post-0.7.0 image session (they need a SYSTEM squashfs repack); this release ships the light fork-ID only.
- The **a6xx GPU hang** is now *absorbed* by anti-lock (a wedge becomes a brief hitch scored `SURVIVED`, not a session death) but not cured; the DRM-spawn teardown deadlock remains an intermittent launch race.
- **"Boot roulette":** on a handheld that already has ROCKNIX on internal storage, an inserted SD mounts as secondary rather than booting standalone — so the single-card plug-n-play image can't be full-boot-tested there (it's validated by construction; a GRUB switcher is planned to make the boot a choice).
- The **Windows PowerShell** installer is frozen at 0.6.0 (`install.sh` is the maintained engine going forward).

## [0.6.0] - 2026-07-03 — "GTK Prologue Edition"

**Introducing the Gran Turismo Kit.** A custom-tuned **RPCS3 emulator** with integrated **Turnip driver**, and a race-tested ROCKNIX middleware with its native ETK Pitstop app, upgrades the SM8250 for track day with advanced crash prevention for maximized but experimental playability — while embedding deep telemetry to chase audio support and console-grade framerates in future releases. Targeted for Gran Turismo 5 Prologue Spec II and Spec III and GT HD Concept only; GT5 and GT6 support pending.

### Added
- **RPCS3 GTK Edition** — ETK's custom emulator build, now the **default emulator**: `install.sh` fetches it from the release and deploys it automatically (sha256-verified, zero configuration — the same pattern as the certified GTK driver; `RPCS3_APPIMAGE="stock"` in etk.conf opts out, a path stages a local dev build) via a boot-persistent bind (STEP 6.55). The build carries: the **GT5P road-shadow flicker FIX** (upstream RSX bug [#11912](https://github.com/RPCS3/rpcs3/issues/11912), 5 years open — cured on GT tracks; **on by default**, `GTK_REMAP0_ONE=0` is the diagnostic kill-switch), bounded fence-wait timeouts, and end-to-end **audio telemetry** (underrun/skip/stretch counters + a 2-second phase timeline). Full source delta + build recipe published in the sister repo **[etk-rpcs3-gtk](https://github.com/mercurious/etk-rpcs3-gtk)** (GPLv2).
- `RPCS3_ENV_FLAGS` runtime env injection for build-gated switches (STEP 6.56).
- **Audio watchdog** (STEP 6.57): the SM8250 silently loses ALL audio on roughly 1 in 4 boots (a q6afe/ADSP probe race at boot — likely a years-old ghost); now detected and self-healed in seconds, validated against a natural failure. Uninstall restores stock behavior.
- **Ledger audio columns**: `aud` (per-session cellAudio counters from the GTK Edition build) and `snd` (audio-path health: `ok|revived|dummy|nocard`) — silent-boot sessions self-quarantine from audio comparisons; per-session audio timelines are archived under `etk_telemetry/audio_logs/`.
- **Panic Black Box** (read side): pstore harvester + kmsg flight recorder for kernel-panic forensics; the ramoops write side stays operator-armed via `scripts/arm_blackbox.sh` (install.sh never edits grub).
- **Trigger Calibration** screen in Pitstop TOOLS (handler-aware threshold scaling).
- Pitstop TUNING additions: **Enable Time Stretching** master switch (the threshold dial was inert without it), **Disable Sampling Skip**, **Max SPURS Threads**, **RSX FIFO Accuracy**; AUDIO help text corrected (threshold is buffer-fill %, not fps) and the bogus "ALSA" backend option removed (never a real RPCS3 backend).
- Ledger `gpu_fault_status` / `gpu_fault_fence_hex` columns; RSX frame-capture pad chord (R1+DPAD-Down).

### Changed
- **Certified against ROCKNIX official release `20260701`** — the nightly treadmill is over. ETK now supplies its own emulator (GTK Edition) and driver (GTK Turnip) via boot-persistent binds; the OS provides the substrate only.
- `etk_template.yml` audio defaults: time stretching ships **ON** (threshold 75) — validated by the new counters; inert on titles that hold pace.
- **Windows PowerShell port synced** step-for-step to v0.6.0 (Turnip catalog, GTK Edition bind + env flags, watchdog, POWER applier, Black Box, DP-mirror, rig-side `etk.conf` generation). Runtime smoke test on a Windows host still pending (alpha, as before).

### Fixed
- GT5P track-shadow flicker (#11912) — fixed in the GTK Edition build (carried as a known upstream issue since 0.5.0).
- Restored L1+R3 / R1+L3 recovery + HUD-toggle chords (input_d v10.4.1).
- AppImage staging without the exec bit produced a silent "quits on launch" (exit 126, zero log trace) — install.sh now force-sets it on both sides.

### Known issues / deferred
- **GT5P race audio stutters under load.** Now instrumented and root-caused: the game's own audio production misses ~15–27% of its 5.33 ms periods when emulation runs below full pace — the delivery layer measures clean (zero backend underruns). The v0.6.1 audio campaign (SPU/SPURS ladder + a production-side fix in the GTK Edition) chases it with the telemetry this release embeds.
- The **a6xx GPU hang** remains managed-not-cured (GTK driver + dials push it later); the DRM-spawn teardown deadlock remains an intermittent launch race (R3 + relaunch clears it).

## [0.5.0] - 2026-06-30

**The custom driver goes public.** ETK's flagship is now the **GTK custom Mesa/Turnip driver for ROCKNIX** — a Gran-Turismo-tuned fork that nearly doubles GT playtime on the SM8250 (median run ~204s → ~394s; the p90 ceiling more than doubles; time-to-crash **+42%**) and decouples the shader vault from ROCKNIX nightlies so the cache survives OS updates instead of spoiling on every bump. The a6xx GPU hang is honestly **reduced, not cured** (crash-rate ~73% → ~50%; the hang persists, the dials just push it far later). A new **DRIVER-build selector** swaps whole `.so` builds (reboot-gated), a **POWER tab** pins CPU/GPU governors, and the in-game **G-INSTR HUD** surfaces live frame-pacing so you can watch the driver's limited-slip mitigation working. Re-pinned to ROCKNIX nightly **20260628**.

### Added
- **GTK custom Turnip driver**, shipped in the on-rig DRIVER-tab catalog — **stock + the proven `gtk_0.2` build only** (`install.sh` stages a certified allowlist; dev/experimental builds stay local).
- **DRIVER tab** build selector + `TU_DEBUG` dial ladder (sddepth/syncdraw LSD gears), every race stamped in the ledger with its dial set.
- **POWER tab** — CPU/GPU governor + clock-pin presets, live + boot-persistent (no runtime OC; the SM8250 OPP table is hard-capped).
- **G-INSTR HUD mode** (`ETK_HUD_MODE=GINSTR`): replaces the LOAD/RAM gauges with live **JITTER** (frame-pacing direction) + **SLIP** (pacing-slip severity) gauges off the MangoHud autolog; ledger gains `fps_med` / `fps_1low` / `ft_p99_ms` / `ft_jitter_ms` columns.
- **USB-C DisplayPort capture + handheld mirror** (`dpmirror_d`) for capture-card/OBS recording.
- README **hero chart** (duration-vs-time, generated from the live race ledger) replaces the lead screenshot; the photo gallery moved to the **[Screenshot Gallery](https://github.com/mercurious/etk/wiki/ETK-Screenshot-Gallery)** wiki page.

### Changed
- **Re-pinned to ROCKNIX nightly `20260628`** (kernel 7.0.11 + RPCS3 0.0.41-19444 unchanged; `etk_drift.py` reported no structural drift). The prior `20260622` pin had aged off ROCKNIX's published nightly list, so a new user could no longer fetch it.
- Pitstop tabs reclaim scroll height (DRIVER/POWER/TELEMETRY/PADDOCK/TUNING); TUNING counter relabeled `SETTING`; 3-line PIT ENGINEER hint band.

### Fixed
- **Cockpit spotter** now distinguishes a real silent freeze from a graceful exit via an `emu_alive` `/proc` gate — no more false `>>> CRASH: SILENT` + 28 B header-only stub `.rd` on a clean exit.

### Known issues / deferred
- **GT5P "road flicker" is upstream RPCS3, not the ETK driver.** The track-shadow flicker is RSX bug [#11912](https://github.com/RPCS3/rpcs3/issues/11912) (reproduces on desktop RPCS3/MoltenVK too, root-caused upstream to shader program-constants/binary); no driver or config lever fixes it. Carried as a known upstream issue.
- **DRM-spawn teardown deadlock** — rapid relaunch / the EBOOT→EMAIN spawn handoff can wedge RPCS3 in Vulkan-instance teardown (`vkDestroyInstance`/`pthread_cond_destroy`), presenting as a black-screen launch freeze. Clear with R3 and relaunch (intermittent race). Emulation-side; an RPCS3-fork fix target, not curable from the driver.
- The **a6xx GPU hang** persists as a managed residual — the GTK driver delays it (`sddepth`/`syncdraw` dials) but does not cure it.

## [0.4.0] - 2026-06-18

**Tune the driver, photograph the crash.** The hunt for ROCKNIX's headline instability — the a6xx GPU-fault freeze — produced two operator-facing instruments. A new **DRIVER tab** exposes the Mesa/Turnip dials the crash signature points at and stamps every session in the ledger with the exact dial set it ran under, so genuine-play tuning finally yields *attributable* data instead of N=1 guesses. And a **crash-cam** turns every recoverable freeze into a photographed, dial-tagged ledger entry — the frozen frame is grabbed at the R3 panic, bound to its session, and previewed full-screen on the device. Plus the Manage Shaders engine now deploys reliably. The throughline holds: ETK ships tooling, never bytes.

### Added
- **DRIVER tab — Turnip dials, ledger-tagged (Pitstop).** A fifth tab exposing the Mesa/Turnip environment knobs as gamepad dials: `TU_AUTOTUNE_ALGO` (the GMEM↔system-memory render decision engine) and the `TU_DEBUG` isolation ladder (`nolrz`/`noubwc`/`sysmem`/`gmem` + an Advanced group). APPLY injects via the proven `profile.d` path (`097-etk-turnip-dials`) — effective next launch, survives a cold boot; Reset reverts to Turnip's built-in autotune. Every APPLY writes `active_tune.txt`, which `session_postmortem` records as the ledger's `tune_tag` column — so each race is attributable to the exact dial set it ran under. One knob per soak, on-screen.
- **Crash-cam — frozen-frame capture + on-device preview, bound to the ledger.** The dominant ROCKNIX crash leaves no core and no RPCS3 fatal — only a frozen screen. `recovery.sh` now grabs that frame via `grim` at the R3 panic (best-effort, hard-timeout-bounded so it can NEVER stall the nuclear recovery), and `session_postmortem` binds it to the crash's ledger row (`crash_shot` column). In the Pitstop crash-detail card, press **↑** to view the frame **full-screen** (via `swayimg`), dismissable with any button. A crash entry is now signature + frame + dial, all linked.
- **Manage Shaders deploy fix.** `tools/vault_sweep.sh` — the engine the Manage Shaders screen drives — is now deployed to the rig by `install.sh`. It had only ever reached the rig via a dev-time push, so a plain uninstall/reinstall cycle silently dropped it and broke the screen with a misleading "no boundary." The screen now distinguishes engine-missing from a genuinely absent rebuild boundary, and Sweep / Delete-vault / Clear-cache are field-confirmed.
- **Busy-frame throbber (Pitstop).** TOOLS shader scan/clean and PADDOCK sync/push/pull now animate a ROCKNIX-style ASCII spinner on a background thread instead of freezing on a single frame.

### Known issues / deferred
- **The a6xx GPU-fault freeze remains the headline instability** (carried from 0.3.1) — and 0.4.0's new instruments sharpened it without yet beating it. On-rig DRIVER-tab A/B established the fault is a **NULL texture/vertex descriptor** (`iova=0x0, source=TP|VFD`) on the live-race render path (the High Speed Ring tunnel), **not** memory pressure or tiling — so the `TU_AUTOTUNE_ALGO` dials (gmem/sysmem) do not move it (4/4 froze). A first probe of RPCS3 **Write/Read Color Buffers** (the GT6 render-correctness knob) did not eliminate the freeze either, but indicatively moved the fault *deeper, past the tunnel-mouth transition* (N=1, suggestive). All of which reinforces the **Stage-IV Turnip fork** as the real next lever — the residual fault sits below the RPCS3 render-setting layer.
- **mako cannot render images on ROCKNIX** — the gdk-pixbuf loader modules are stripped from the build, so a notification renders text but never an image. The crash-cam preview therefore uses `swayimg`; a build lacking it degrades to a text toast with the SMB path.

## [0.3.1] - 2026-06-17

**The Cockpit pit-engineer goes cross-platform, and the fork learns to record the wheel.** ETK's Cockpit skill — until now an Android/`adb`-over-USB spotter — was proven this session to run **unchanged against ROCKNIX over `ssh`**, on both the **USB-net gadget** (`169.254.170.2`, sub-ms) and the **WiFi LAN**, reading the same live telemetry through standard Linux sysfs on the same SM8250. The basic **Spotter (read-only telemetry)** and **Engineer (telemetry→tuning)** tiers are now transport- and OS-agnostic. Separately, the **aPS3e Shader Fork v4** lands the native **pad-movie** record/replay hook. The throughline holds: ETK ships tooling, never bytes.

### Added
- **Cockpit · ROCKNIX support (cross-platform telemetry).** The Spotter/Engineer tiers now drive a ROCKNIX rig over `ssh` (USB-net or LAN), not just Android over `adb`. New `scripts/rocknix_spotter_loop.sh` streams thermal (full per-zone map: gpu-top, cpu clusters, battery), GPU devfreq (drm/msm — no KGSL), CPU-prime freq, `MemAvailable`, the ETK `/dev/shm/etk_shm` live-stat bridge, and a crash-watch. RPCS3.log is byte-identical to the Android fork's, so the crash-taxonomy/log skills port unchanged — and ROCKNIX adds **real core dumps + on-device `gdb`** (cleaner forensics than Android tombstones).
- **aPS3e Shader Fork v4 — native pad-movie (Cockpit T3, undocumented/experimental).** Frame-exact gamepad record/replay baked into the fork (`cellPadGetData` hook), keyed to the game's read cadence: race-start cursor-sync + a three-mark region (MARK-IN / MARK-OFFSET-at-lap-line / MARK-OUT) that survives a cold boot. Doubles as a **deterministic repro/benchmark harness** (replay an identical input stream across builds/drivers). Synthesis pipeline (`analyze`/`synth`/`extract`) for multi-lap capture. APK staged; self-driving stays hidden (open-loop ceiling — a clean autonomous lap needs the closed-loop CV layer).

### Known issues / deferred
- **ROCKNIX WiFi is not reliable** (separate from the skill, which works whenever a transport is up). `iwd` + `ath11k_pci` loses the WPA2 4-way handshake (`Reason 15`) and churns; **not fixable on stock ROCKNIX** (no `wpa_supplicant`, read-only `/etc`). Use the **USB-net gadget** as the stable channel. A real fix needs a custom image or AP-side change — folded into the GPU-stack work below.
- **ROCKNIX GPU-driver lockup is the headline instability.** Under RPCS3's Vulkan load the mainline **Freedreno `a6xx`** driver faults (`a6xx_irq gpu fault` → `hangcheck recover!`, offending task `rsx::thread`) → emulation freezes (no core, no RPCS3 fatal — detect via `dmesg`). This — not emulation bugs — is the bulk of ROCKNIX's instability, and it correlates with mid-lap shader compiles. **Next: fork Turnip** (Mesa's userspace Vulkan driver — surgically deployable via `VK_ICD` override from `/storage`, no OS fork) before any ROCKNIX fork; the Cockpit (pad-movie repro + Spotter crash-watch) is its test harness.
- **Perf note:** ROCKNIX ran GT5P Spec III at **30–60 FPS** (vs Android's harnessed low-FPS) — confirms the high-performance-rig thesis; the ghost car is the perf/variance sink (60→24 FPS while tailing).

## [0.3.0] - 2026-06-14

**Overheating no longer ends your session, and shader management goes self-custody.** ETK's thermal failsafe is recalibrated for the hotter ROCKNIX nightly-`20260610` stack and now **auto-recovers**: an overheat drops the device into a PIT-mode cooldown that clears itself back to racing once temps fall — *no reboot*, with a live HUD `»COOLDOWN` → `RACE OK` indicator. Shader management then splits in two: the PADDOCK tab becomes the **Private Paddock** (push/pull YOUR vaults, tunes, and saves against YOUR own private GitHub repo, from the rig, over WiFi, no host computer), and a new **Manage Shaders** screen reclaims storage by sweeping the dead-epoch shaders every driver update strands. The PADDOCK tab only exists when GitHub is connected (`PADDOCK_TOKEN` in `etk.conf` before `./install.sh`); the throughline of the whole release — ETK ships tooling, never bytes.

### Added
- **Automatic overheat recovery — no more reboot (`bin/thermal_d.sh` v14).** A thermally-tripped PIT now self-clears back to RACE once the governing zone holds at/under `RECOVER_THRESHOLD` (80 °C) for `RACE_TRIP_TICKS` ticks — a hysteresis sawtooth (92 °C trip → 80 °C recover) that ends the reboot-to-reset era. PIT entry is **debounced** (`RACE_TRIP_TICKS=2`, ~4 s sustained) so transient 2 s spikes no longer trip it, and a `THERMAL_PIT` latch keeps a *manual* (commander/dashboard) PIT from being auto-overridden. The HUD gains a `»COOLDOWN` (auto-cooling) state and a `RACE OK` recovery flash.
- **Manage Shaders (Pitstop TOOLS tab).** A per-game fresh/stale shader graph with a scope toggle (current game / all games) and three confirm-gated actions — **Sweep** (prune dead-epoch orphans), **Delete vault**, **Clear RPCS3 cache** — a gamepad front-end over `tools/vault_sweep.sh`. Reclaims the storage every ROCKNIX-nightly Mesa rebuild strands (a saturated GT vault can be >90 % pre-bump corpse).
- **TUNING — `Disable ZCull Occlusion Queries`** added to the TUNING-tab field set (`config/pitstop_fields.json`): a per-game RPCS3 Video toggle for ZCull-sensitive titles.
- **Tab order is now TELEMETRY · TUNING · TOOLS · PADDOCK** so the three offline tabs cycle without landing on PADDOCK (its only network surface); PADDOCK stays credential-gated and only appears when a paddock is configured.
- **`bin/paddock_sync.sh`** — rig-side sync engine (BusyBox curl+jq): `status` / `push <ID>` / `pull <ID>`. Epoch-tagged releases per driver build (`vault-<CHIPSET>-turnip<VER>`, version read from the driver library itself), sha256 sidecars, last-write-wins uploads, mesa_hash homologation gate on pull (config-only via manual `--force` only), no-clobber merges for shaders and saves. Token travels via header file in tmpfs — never argv, never logs.
- **`install.sh` STEP 8 `PADDOCK LINK`** — conditional on `PADDOCK_TOKEN`: derives the GitHub user from the token, verifies the repo is **private** (refuses public — a public paddock would *distribute* the vault), auto-creates it with a classic-scope token (fine-grained tokens get a one-click instruction), seeds the initial commit (release tags need one — discovered live), writes the rig credential (chmod 600). No token → step self-completes, zero behavior change.
- **Pitstop PADDOCK tab reworked** — gated on the credential file (unconfigured rigs see three tabs); rows show per-game `LOCAL / PADDOCK` state (`LOCAL-ONLY · REMOTE-ONLY · BOTH · EPOCH-OLD`); dpad selects PUSH/PULL, CONFIRM executes with mako progress. The known_repo GET hatch survives unchanged (operator-supplied sources, local + gitignored).
- **`tools/paddock_probe.sh`** — the disposable validation harness (10/10 pass on first full run; the whole API loop was proven against real infrastructure before a line of integration was written).
- `uninstall.sh` removes the credential (the remote paddock repo is never touched — it's the user's backup).
- **`tools/vault_sweep.sh` promoted to the paddock-trim companion** and gains `--game <ID>` + `--porcelain` (machine-readable fresh:stale tallies) to back the Manage Shaders screen. The epoch-mtime orphan sweep (boundary = install.sh's Mesa-build fingerprint) is how vaults stay push-worthy: first full run reclaimed **174,954 dead-epoch files / 1.2 GB** (GT6's vault was 97.6% pre-bump corpse), and the re-pushed GT5P bundle shrank 113 MB → 34 MB. `paddock_sync.sh push` now warns when a vault still carries pre-bump orphans, so dead-epoch shaders never get banked under a live epoch tag.
- **PADDOCK tab name resolution** — rows resolve via ES `gamelist.xml` pretty names → `.psn` stems → `games.yml` ISO filenames → names banked in the paddock itself (`paddock_names.json`, maintained on push — so a cold card's REMOTE-ONLY list shows titles, not IDs) → PARAM.SFO → raw ID.

### Fixed
- **Recalibrated thermal thresholds for the hotter `20260610` stack** (`scripts/profiles/SM8250.sh`, `scripts/env.sh`). Turnip 26.1.2 / RPCS3 19444 runs the GPU hotter, pushing the normal 70–82 °C operating band against the old 86 °C ceiling and causing spurious `OVERHEAT` trips (3 in one session on 0.2.0's day one — the v0.2.0 watch item). Raised `ALARM` 83 → 88 and `RACE` 86 → 92, anchored to the kernel's zone-14 trip points: above the kernel's reversible 90 °C passive throttle (so the kernel governs first and ETK's PIT is a true backstop) and still under its 95 °C / 110 °C hard trips.
- **PIT required a cold boot to clear (bug).** The RACE upshift restored only the PRIME cluster's `scaling_max_freq`, leaving the GOLD cluster pinned at `CPU_PIT_CAP_KHZ` until reboot — which is *why* recovery needed a reboot at all. Upshift now restores **both** clusters at runtime (verified on-rig), the mechanism behind the auto-recovery above.
- **BusyBox `cp -rn src/. dest/` is a SILENT NO-OP** (rc=0, zero files copied) — discovered during pull validation. This also silently broke `install-protune.sh`'s shader injection (its `||` fallback never fired because rc was 0). Both injectors now use the BusyBox-native `tar -k` no-clobber merge.

### Removed
- **`vault-index/` retired entirely** (the public Pro Tuning index — already neutralized in 0.2.0, now gone). The public-index fetch path is deleted from Pitstop. With no public distribution surface left in the tree, **releases now ship from `main`** — the cherry-pick release era ends.
- Swarm/sharing-era dossiers moved to `_archive/` (ShaderSwarm, PaddockSwarm, ShaderDistributionFusion, AndroidConsumerSubscribe).

## [0.2.0] - 2026-06-11

**Back to the bleeding edge — ETK re-pins to ROCKNIX nightly `20260610` to ship the upstream Gran Turismo 5 memory-leak fix, and adds the Stage III stability harness (Mesa cache-cap lift + silent-crash core capture).** Operator-validated on-rig the same day: GT5P racing at full 720p, RAM peaks down ~1.5 GB, and the formerly dominant "silent crash" class absent from the ledger.

### Added
- **Stage III stability harness (new install step 7, `STAGE3 HARNESS`).** Two rig-side primitives from the Stage III forensics sprint (`dossiers/Stage3CustomRigDossier.md`):
  - `profile.d/098-etk-stage3` — sets `MESA_SHADER_CACHE_MAX_SIZE=10G`. Mesa's disk cache silently caps at **1 GB with LRU eviction**; the ETK vault crosses 1 GB on a saturated GT suite, meaning the vault could **evict its own oldest shaders** and quietly un-saturate between sessions. Also raises the core-size ulimit for emulator processes.
  - `02-etk-coredump.sh` + `etk-stage3.service` (oneshot) — Rocknix ships `kernel.core_pattern = |/bin/false` (crash cores are *discarded*) and the sysctl resets every boot. The unit re-arms capture to `/storage/cores/` (keeps newest 2) on every boot. Silent-class crashes — process death with no RPCS3 log signature and no dmesg trace — are undiagnosable without a core; this is the forensic capture path that finally gives Rocknix parity with Android's crash-dropbox.
  - `uninstall.sh` fully reverts all three artifacts and restores the stock core_pattern.
- **README:** verify step added to the GRUB-disable Power Pro Tip (a silently failed `remount,rw` previously made the seds no-op), plus a note that OS updates revert the tweak.

### Changed
- **OS pin: ROCKNIX nightly `20260610`** (was official release `20260601`). The pin is evidence-driven, not novelty-driven: nightly-20260610 ships **RPCS3 `0.0.41-19444`**, the first build containing the upstream GT5 memory-leak fix ([RPCS3 #18819](https://github.com/RPCS3/rpcs3/issues/18819) / PR #18844, merged 2026-06-05 — ~300 MB leaked per car model viewed, lethal on an 8 GB handheld and matching ETK's dominant silent-crash signature), plus **Mesa Turnip 26.1.2** (driver parity with the Android/aPS3e comparison rig) and kernel 7.0.11. The official `20260601` release predates the fix by four days. README System Requirements / Getting Started / Windows flash guide, `scripts/profiles/SM8250.sh`, and `AI_MANIFEST.md` all re-pinned. **Migration note:** the Turnip 26.1.0→26.1.2 bump invalidates the existing Mesa-side shader vault (driver hash keys the cache) — a fresh harvest cycle follows the update; with the new 10G cap it will never self-evict.
- **Per-title tunes:** GT5P + GT HD Concept switched `Shader Mode` from `Async Recompiler with Shader Interpreter` to `Async Recompiler (multi-threaded)` (matching the shipped template default). The interpreter hybrid does not avoid compile stalls — it *adds* a heavy GPU über-shader pass exactly during compile bursts, a credible amplifier of the Adreno fence-timeout crash class on a 7 W GPU (the same interpreter path is currently crashing the AMD Mesa driver upstream, RPCS3 #18838). Expect brief first-encounter pop-in instead of approximated rendering; the change is A/B-logged in `config_changes.tsv`.

### Verified
- **Live race validation on nightly-20260610** (SM8250, 2026-06-11): GT5P career sessions at full 720p with sessions 2–3× longer than the official-build era (366–519 s), RAM peaks 5.0–6.1 GB (vs 6.7–7.5 GB pre-fix), zero silent-class crashes on the boot's ledger, and the operator's verdict — comparable stability/feel to the patched Android build at higher fidelity, credits ground, car purchased. Known watch item: 3 thermal-failsafe activations during load/contact-heavy racing (longer sessions = more sustained heat); thermal ceiling behavior unchanged, monitoring continues.

## [0.1.4] - 2026-06-02

**Certified on the official ROCKNIX release `20260601` — ETK graduates from chasing nightlies to a pinned official build — and adds an optional internal-storage (UFS) path for shader-harvesting durability and smoothness.** First non-prerelease tag.

### Added
- **Internal-storage (UFS) support — optional, advanced.** The shader vault, RPCS3 caches, and small games can now run on the device's internal UFS partition instead of the SD card. `install.sh` is internal-aware: it autodetects a vault symlinked into internal UFS and syncs symlink-safely (`--copy-links` on pull, `--keep-dirlinks` on push) so it never de-internalizes the vault. The layout is symlink-based and reversible (on-SD `.presplit` safety copies + `ROLLBACK.sh`). **Durability is proven** (the per-session-rewritten vault moves off the wear-prone SD; shaders write/credit/survive R3 on UFS); **smoothness is operator-confirmed** for GT HD Concept + GT5P running fully internal (ETK has no frame-pacing instrument — a known MangoHUD limitation on this platform — so operator subjective A/B is treated as a first-class datapoint). It does **not** improve crash stability. New README **Internal Storage (Advanced)** section documents the partition layout, the `LABEL=ROCKNIX/STORAGE` collision, fastboot-only full revert, config divergence, and the ≥1.5 GB system-partition headroom rule. See `dossiers/InstallToInternalRecovery.md` and `dossiers/RocknixOfficialReleaseCertification.md`.

### Changed
- **OS pin: official release `20260601`** (was nightly-20260531). README System Requirements, Getting Started, Warnings, and the Windows flash guide now point at the official release and its update path; `scripts/profiles/SM8250.sh` and `AI_MANIFEST.md` re-pinned. Driver line unchanged — verified still `Mesa Turnip 26.1.0`.
- **Stability framing corrected (it was stale).** GT5P has **cleared and exceeded** the race-stability bar — career best streak of **16 crash-free sessions** (8 back-to-back clean finishes), one streak straddling into the official release. This was captured only in the live telemetry ledger, never in the docs; README §54 previously (wrongly) said "no version yet certified as race stable." The result was earned on a **saturated** vault; the official-`20260601` migration resets the vault, so a fresh install re-enters the harvest cycle and crashes until the cache re-saturates — race-stable is proven *reachable*, not guaranteed every session.

### Verified
- **Certified on the official ROCKNIX release `20260601`** (build `e7b9e9a3`, kernel 7.0.2, Turnip Mesa 26.1.0) on SM8250. `etk_drift.py --check` clean (no structural drift); drift baseline `20260601.json` banked + pinned (build_id matches `os-release`); headless gate passed (gamepad codes unchanged, R3 survives suspend/resume, RPCS3 binds `Turnip Adreno (TM) 650`); per-game render re-validated on GT5P + GT HD Concept, both running **fully on internal UFS**. Internal Tier-B layout (game data + vault + `dev_hdd1` caches symlinked to internal, `.presplit` SD copies retained, `ROLLBACK.sh` present) confirmed live on-rig. See `dossiers/RocknixOfficialReleaseCertification.md`.

## [0.1.3] - 2026-05-31

### Added
- **TELEMETRY session detail view.** Select a row in the TELEMETRY tab (D-pad) and press **CONFIRM** to open a full-screen card for that session; **B** returns. CLEAN/ABORTED runs show an ASCII data-viz summary — duration, shaders harvested, and proportional gauges for temp / load / RAM / battery drain. Crash/RECOVERY rows pull the human-readable `summary`, `explanation`, where it died (`fence_at_crash`), and the **suggested fix** straight from `config/crash_signatures.json` (e.g. *Driver Wake-Up Delay → 50*), degrading gracefully when a `crash_sig` has no catalog entry (e.g. `PANIC_REBOOT`). The TELEMETRY table gains a row cursor (was scroll-only); pure read-side UI — no Sentry/ledger/schema changes. The crash-signature copy in `config/crash_signatures.json` was rewritten as plain **player-facing diagnostics** (no internal jargon), and multi-cause crashes headline the real cause rather than the R3 trigger (R3 is shown as "Recovered manually"). See `dossiers/SessionDetailViewProposal.md`.

### Fixed
- **R3 recovery now targets the correct emulator process names.** `recovery.sh` killed `rpcs3` + `AppRun.wrapped`, but on this build RPCS3 runs as an AppImage whose launcher `comm` is `rpcs3-sa` (no plain `rpcs3` process exists), so `killall -9 rpcs3` matched nothing. More importantly, with the corrected Sentry detection (`pgrep -f "rpcs3-sa|AppRun.wrapped"`), leaving the `rpcs3-sa` launcher alive would keep the Sentry in RUNNING — the RUNNING→IDLE handoff and post-mortem rollup would never fire after an R3 press. Recovery now `killall`s `rpcs3-sa`/`AppRun.wrapped`/`rpcs3` **and** runs an authoritative `pkill -9 -f "rpcs3-sa|AppRun.wrapped"` that mirrors the Sentry's exact detection pattern, guaranteeing the post-recovery IDLE transition.
- **Phantom `ABORTED` sessions polluting the ledger (crash-analytics integrity).** The Sentry detected a running emulator with `pgrep -f "rpcs3|AppRun.wrapped"`, where `-f` matches the whole command line — so it also matched any process whose argv merely referenced an **rpcs3 path**, notably `session_postmortem.sh`'s `strings /storage/.cache/rpcs3/RPCS3.log`. On a log-verbose title (Ridge Racer 7's `RPCS3.log` reaches ~288 MB) that `strings` outlived the Sentry's 2 s tick, so the Sentry mistook its own log-parser for a live game and ignited a **self-reinforcing loop** of phantom sub-threshold sessions — each <60 s, reclassified `ABORTED` — burying the real `CLEAN`/`RECOVERY` rows (RR7 read 21 ABORTED vs 4 CLEAN). Fixes: (1) the Sentry now matches the emulator on a path-specific cmdline token — `pgrep -f "rpcs3-sa|AppRun.wrapped"` — present in the launch argv (`/usr/bin/rpcs3-sa …`) but never in the rpcs3 log path, at both the state-detection and orphan-PANIC-guard sites; (2) `session_postmortem.sh` reads the log via bounded stdin redirect (`tail -c 4M <"$RPCS3_LOG" | strings`) so the parser's argv no longer carries the rpcs3 path and the scan is bounded. (`pgrep -x rpcs3` is **not** usable — `/usr/bin/rpcs3-sa` is a static ELF whose `comm` is `rpcs3-sa`, so exact-comm match misses it and ignition never fires.) Verified on-rig: the log-parser no longer registers as a running emulator, and a real game ignites correctly.

### Verified
- **Certified on Rocknix nightly-20260531** (in-place migration from 20260529, SM8250). `etk_drift.py --check` clean (no structural drift); the `--diff` input CRITICALs were benign node renumbering (DualSense buttons device drifted `event8→event9`; `find_gamepad()` self-heals by name). 20260531 bumped the Turnip driver — per-game render re-validated on GT5P (vault re-layered cleanly, +10k shaders, HUD nominal). Headless gate passed: gamepad codes unchanged, ignition fires, R3 survives suspend/resume, RPCS3 binds `Turnip Adreno (TM) 650`. ETK Pitstop tile still registers + renders after the two ES-engine package bumps (the upstream Tools-artwork bug remains unfixed; ETK's tile is insulated by its `thumbnail`/`marquee` injection). Profile re-cal notes + README bumped to 20260531. See `dossiers/RocknixNightly20260531CertificationDossier.md`.

## [0.1.2] - 2026-05-29

**Screenshot trigger is now operator-controlled, Tools-menu icon fixed, and certified against Rocknix nightly-20260529.** The `L1` screenshot shutter no longer fires unconditionally — it has a three-state mode so you can scope it to gameplay or free the button for the game entirely.

### Added
- **Three-state `L1` screenshot mode** — `in-game` (default) / `always` / `disabled`, cycled live from **Pitstop → TOOLS → "Screenshot on L1"**. Persisted to `etk_telemetry/screenshot_mode.txt` and read by `bin/input_d.py` on every L1 press, so a toggle takes effect with **no daemon restart**. The mode is shared via `$SCREENSHOT_MODE_FILE` (`scripts/env.sh`).

### Fixed
- **ETK Pitstop Tools-menu icon now renders on the stock theme.** The default Rocknix theme (`es-theme-art-book-next`) hides the standard `<image>` mapping and draws Tools art from `<thumbnail>`/`<marquee>` instead, so our image-only entry showed no icon. `etk_modules_inject.py` now emits all three artwork fields (→ `etk_pitstop.svg`), so the tile appears regardless of which artwork subset the theme uses — independent of the platform-wide Rocknix bug where *no* stock Tools icon renders (diagnosed on-rig, reported upstream; see `dossiers/ToolsMenuArtworkDiagnosis.md`). The SVG was never the problem — a PNG in the same field was equally blank.
- **`L1` screenshot fired in every context, with no way to disable it.** It now respects the mode above: `disabled` stops ETK shooting on L1 — genuinely freeing the button for a game that binds it (ETK never `EVIOCGRAB`s the pad, so L1 always reaches the game regardless); `in-game` suppresses accidental frontend/Pitstop captures. The deliberate `SELECT` + `D-pad Up` chord is **not** gated and always works as a manual shutter.

### Changed
- **Default screenshot behavior is now `in-game`** (was effectively `always`). Existing rigs with no mode file inherit `in-game` on first boot after upgrade. Set `always` in Pitstop → TOOLS if you capture the frontend / Pitstop UI (e.g. for README shots).

### Verified
- **Certified on Rocknix nightly-20260529** (in-place migration from 20260528, SM8250). `etk_drift.py --check` clean (no structural drift); the 10 `--diff` input CRITICALs were benign node renumbering (the DualSense buttons device moved `event9→event8`; `find_gamepad()` self-healed by name). Manual headless gate passed: gamepad codes unchanged (R3=318/L3=317/L1=310/SELECT=314/D-pad=16,17), **R3 panic survives suspend/resume** (29's fake-suspend rewrite), RPCS3 binds `Turnip Adreno (TM) 650`, and the v0.1.2 screenshot + Tools-icon features work on 29. Profile re-cal notes bumped to 20260529. See `dossiers/RocknixNightly20260529CertificationDossier.md`.

## [0.1.1] - 2026-05-29

**Windows installer port + automatic SSH pairing.** A Windows PC can now act as the ETK host without WSL, and first-run SSH setup is automatic — type the rig password once, never again.

### Added
- **Automatic host → rig SSH pairing wizard** — `scripts/etk_pair.sh` (bash) and `windows_installer/etk-pair.ps1` (PowerShell). Idempotent and test-first: a cold pair takes **at most one** password (Rocknix default `rocknix`); every later `ssh`/`scp` is silent. It:
  - generates a dedicated, no-passphrase key `~/.ssh/etk_rig` (never touches your existing `id_*` keys),
  - installs it on the rig **carriage-return-safe** and **without clobbering** an existing key (an unrelated user key in `authorized_keys` is preserved),
  - writes an `~/.ssh/config` block so the bare `root@<rig>` target the installer uses is passwordless.
  - Re-runs cost **zero** passwords; a host that already has working SSH is detected and left untouched.
- `./install.sh --pair` and `etk-pair.ps1` run pairing **standalone** (e.g. to re-pair after a card reflash). The installers also auto-pair before their first remote call.
- The rig-side key-install logic lives **once** in `etk_pair.sh` and is pulled into the PowerShell port via `Get-Heredoc` — single source of truth, exactly like the Sentry/systemd-unit blocks.
- **Verified the Windows PowerShell installer end-to-end** on a real SM8250 rig (no-vault): cold pair → full deploy → live Sentry, zero passwords after the first.
- **OS-migration drift detector** — `tools/etk_drift.py` (repurposed from the unused recon tool). Banks nightly-keyed OS profiles and diffs a live Rocknix nightly against your pinned baseline and the device profile's pinned assumptions (`--save-baseline` / `--diff` / `--check` / `--list`), so you can tell whether a nightly is safe to adopt before committing to it.

### Fixed
- **`Invoke-Rig` CRLF bug (Windows port):** multi-line remote commands built from PowerShell here-strings (`.ps1` is `eol=crlf`) were shipped with `\r`, so the rig's `sh` died on `syntax error near 'do\r'` — silently, because the exit code wasn't checked. This had been breaking the on-rig CRLF normalization and the Pitstop launcher arming. `Invoke-Rig` now strips CR from every command.
- **PowerShell pairing abort:** `ssh.exe` stderr on a deliberately-failing probe became a *terminating* `NativeCommandError` under `$ErrorActionPreference='Stop'`. Pairing now scopes the error preference so probes fail gracefully and control flows off the exit code.
- The generated SSH config uses `IdentityFile ~/.ssh/etk_rig` (portable across Windows OpenSSH, Git's bundled ssh, and Mac/Linux) — an absolute MSYS path had made the bare target unusable from Windows OpenSSH.
- **PowerShell 5.1 parser break:** em-dashes in the `.ps1` files decoded as curly quotes under Windows PowerShell's ANSI codepage (BOM-less UTF-8), desyncing the string tokenizer; the scripts are now pure ASCII.

### Changed
- `windows_installer/etk-env.ps1` `$RigSsh` now defaults to `root@SM8250.local` (matching `env.sh` / `etk.conf.example`), so most setups need **no configuration** at all.
- `windows_installer/WINDOWS_HOST_README.md` rewritten around the zero-config flow (clone → run → reboot); the old 7-step manual SSH handshake is demoted to a documented fallback.
- Main README "Windows Install Guide": the native PowerShell installer is now the primary no-WSL path; WSL2 remains the full-featured (vaulted) route.

### Known limitations
- The Windows port is **no-vault** — no host-side shader backup/restore. Use the SMB `robocopy` recipe (README) or WSL2 for that.
- mDNS auto-discovery is **not** ported to PowerShell. Set `$RigSsh` in `etk-env.ps1` (or pass `etk-pair.ps1 -RigSshOverride root@<ip>`); a literal IP always works.
- `etk.conf` operator overrides are not pushed by the Windows port (the rig runs the Sentry's baked-in defaults unless a prior Mac/Linux install left an `etk.conf`).

## [0.1.0]

Initial tagged release: bash `install.sh` / `uninstall.sh` host tooling (macOS / Linux / WSL2) with mDNS rig auto-discovery, the native Rocknix ETK Pitstop app (telemetry / tuning / PS3 `.pkg` installer), the systemd Sentry, the per-game shader vault with host-side backup, and Simple Telemetry.
