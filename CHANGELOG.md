# Changelog

All notable changes to the ETK are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **A game installed from the TOOLS tab now records its config seed in the
  ledger** like the startup sweep always did, so the first title on a fresh
  card shows where its starting config came from (shipped tune or generic
  starter).

## [0.9.0] - 2026-09-03 — Official Chassis Edition

Certified stack: RPCS3 **GTK Edition 0.9.0.3** · Mesa Turnip **26.2.2 gtk_0.7** ·
kernel **rocknix-gtk-20260901-0.5** (7.2.0) · base **ROCKNIX official 20260901**.

The practice lap is over. 0.8.6 rehearsed the September chassis on a nightly;
this cut runs it on the official monthly the kernel was rebuilt against, moves
the certified Vulkan driver to the current Mesa stable, and ships the Turnip
dial the rig actually races on instead of leaving a fresh card at stock. A
note on honesty: mid-cycle a host-side config error switched the kernel half
of the anti-lock net off for about a day of sessions, and a large part of the
shader vault was cleared, so the ledger for this window reads noisier than
the rig felt. The release is certified on the operator's verdict — more
performant, no regression found — with the ledger discounted for that window
rather than crowned by it.

### Added
- **The shipped Turnip dial.** A rig with no dial used to run the driver at
  "no barrier". The kit now ships its default (`zlatez`, the stop the
  DRIVER tab calls Max Stability) on the card and seeds it on a host install
  — only where no dial exists; a dial you set is never overwritten. The first
  session is ledgered under the dial it ran. Kill-switch
  `ETK_TURNIP_DIAL_SEED=0`.
- **Shipped game tunes now apply.** The kit has long bundled a per-title
  reference tune for every game the rig has settled, and a fresh install
  never used them — every new title got the generic starter config. A title
  with a shipped tune now starts on it; titles without one still get the
  starter; a config you already have is never touched. Kill-switch
  `ETK_NOTEBOOK_SEED=0`.
- **Release identity gate.** The release tooling refuses to push or prepare a
  cut under any GitHub login but the project's own.

### Changed
- **ROAD FEEL: Max Stability is now `zlatez`.** The previous top stop,
  `syncdraw`, earned its "proven crash floor" on measurements from before the
  anti-lock net and the 7.2 chassis; `zlatez` works the fragment-stage depth
  hazard the fault decode points at and drew the fewest rescues in the field.
  `syncdraw` remains one Advanced toggle away.
- **Game tunes ship current.** The per-title reference tunes in `config/`
  match the rig's live notebook at the cut (four titles re-banked since
  0.8.7), and the card carries them.
- **Reflash re-pairing is one password again**: the pairing tool clears a
  rig's stale host keys after a reflash instead of failing on them.
- **Forge naming gate accepts dated development driver names**, so a
  development Turnip mint can no longer fail its own version-string check.

### Fixed
- **Install beacon invisible on a freshly flashed card.** On a card that
  had never shown a system notification, every ETK toast — the install
  progress card, verdicts, the boot-time recovery instruction — was accepted
  by the notification daemon and drawn underneath the fullscreen front-end,
  because the daemon's stock style (which puts notifications on top) is only
  created the first time ROCKNIX itself shows one. The kit now seeds that
  stock style itself: before the very first beacon, in the style step, on the
  card image, and from the Windows installer. Rigs that already have the
  style are untouched.

### Stack
- **Kernel `20260901-0.5`** — the 7.2.0 GTK kernel rebuilt on the official
  ROCKNIX 20260901 tag (the 0.8.6/0.8.7 kernel was built on the nightly it
  was cut from; the official delta touched nothing in the kernel, boot or
  device tree). Cold-boot certified on the rig. The card image is built on
  the official 20260901 base.
- **Turnip certified default advances to Mesa 26.2.2** (`gtk_0.7`), the
  rig's daily driver since 2026-09-02; the catalog grows to nine (nothing
  removed): 26.2.2 and a 2026-09-02 development snapshot join. 26.2.0, the
  previous default, stays one DRIVER-tab pick away.
- **RPCS3 GTK Edition 0.9.0.3 unchanged** (certified in 0.8.7).

### Known behaviour
- Two sessions on one title ended in a silent reset with nothing in the
  kernel log beforehand — the unexplained panic class already tracked; the
  black box records the lead-up for the next occurrence.
- A rig that already has a dial keeps it; to take the shipped default, pick
  Max Stability in the DRIVER tab.

## [0.8.7] - 2026-08-31 — Pit Board Edition

The pit board is the sign the crew hangs over the wall so the driver always
knows what is happening — this cut is three of those. Every install announces
itself on the handheld's own screen; the game Pitstop points at is one chord
away instead of a launch-and-abort; and the cache screen tells the truth. All
three features shipped through paired build/adversarial-review agent passes
(every claim independently re-verified, every fix mutation-tested), plus the
boot-config hardening staged since 0.8.6.

### Added
- **Install beacon — the rig announces its own install.** While `install.sh`
  runs, the handheld carries an "ETK INSTALL" progress card: overall percent
  and the running stage, nothing else. It opens before the first daemon is
  killed and closes on an explicit verdict — "ETK INSTALL COMPLETE" or
  "ETK INSTALL STOPPED" naming the stage that stopped; an abandoned card
  self-dismisses within 45 s, and long transfers carry in-loop heartbeats so
  the card never expires mid-stage. ALWAYS ON: no env gate, no etk.conf knob,
  no kill-switch anywhere — a switch is exactly what a silent installer would
  flip. This is cooperative announcement (it proves THIS installer announced
  itself), not foreign-installer detection; a Windows-host install does not
  yet announce (tracked as PS-port debt). Fail-soft throughout: a rig with no
  notification bus installs exactly as before, and a rig that drops off the
  network can never hang the install's exit. `tools/test_notify.py` pins the
  stage roster, the backstop map, monotonic percentages, fail-soft guards,
  and both verdict paths — and the pins are mutation-tested.
- **The Windows installer announces itself on the rig too.** A Windows-host
  install was silent on the handheld's screen — exactly the gap the beacon
  exists to close. The PowerShell port now drives the same "ETK Progress"
  card with install.sh's own stage labels and percentages, so both installers
  are directly comparable on the same rig, and posts the same
  "ETK INSTALL COMPLETE" / "ETK INSTALL STOPPED" verdicts. Always on, no
  parameter, no config value, no kill-switch; fail-soft, and a rig that falls
  off the network mid-install still exits fast. `tools/release_sanity.sh` now
  gates the port's beacon roster, percent map, ungated call sites and
  verdicts at every cut. (Live validation of the port requires a Windows
  host; the beacon semantics it mirrors are the rig-validated ones.)
- **Pitstop GAME SWITCHER (hold SELECT).** Re-point Pitstop at any installed
  game from inside the app: hold SELECT on TELEMETRY / TUNING / TOOLS, pick
  with the right stick, release to switch. Previously the only way was to
  launch the other game and R3-abort it, which put a junk abort row in the
  session ledger. The list is everything installed (.psn launchers, tagged
  .iso/.m3u discs, and RPCS3's games.yml); the switch is a full in-place
  restart, so every per-game binding re-derives, and unapplied TUNING edits
  are discarded by it. It refuses while a game is running — and through the
  few seconds after a session ends before its ledger row is stamped — because
  the ledger reads its game_id from the same last-played anchor the switcher
  writes. The stick's range is probed from the kernel at pad open; a pad that
  refuses the probe gets a guarded fallback that cancels visibly rather than
  ever committing a wrong game. On a fresh rig with no game bound, the chord
  works on the TOOLS tab, so the first game binds without launching anything.

### Changed
- **Manage Shaders is now "Manage Shaders & Caches" — rebuilt, and honest.**
  The old screen drew its action rows underneath the footer, which erased
  them every frame while leaving them cursor-selectable — a defect that only
  appeared as the vault filled, which is how it shipped: on a full vault the
  "Clear RPCS3 cache" row was selectable but invisible. The rebuilt page is a
  compact vault summary over four plain actions: clear RPCS3's caches for the
  current game, for all games, sweep stale vault shaders, Back. Clears now
  cover both RPCS3 cache roots (sizes used to understate the delete), find
  the real per-game directory forms (the old per-game path was a wrong
  hardcoded guess that could free nothing), refuse while any emulator is
  alive — a game or a background install — and a clear that removed nothing
  reports failure instead of success; a partial clear says PARTLY CLEARED on
  the toast too. The banked shader vault is never touched by any of it, and
  the confirm text says so before the delete.
- **Kernel deploy defaults to booting what it installed** (`KERNEL_DEPLOY_MODE`
  fallback is now `default`, not `test`): installing a custom kernel IS the
  intent to boot it. STEP 6.4 withholds auto-boot — silent downgrade to
  `test` plus a `[GUARD]` line — unless the rig is the verified Flip 2 AND
  the kernel's module tree exists on the target, so a stale KERNEL_IMAGE can
  never auto-boot a module-less system.
- **Self-update carries unit fixes to couch-only users:** the in-app updater
  now reconciles shipped systemd unit changes, so a rig that only ever
  updates from the TOOLS tab picks up unit repairs that used to require a
  host install.

### Fixed
- **osguard Phase B re-applies the numeric default pin**, so a post-OS-update
  self-heal converges to the same boot default a fresh install writes
  (heal == fresh; the string-id default does not resolve on the 4Kn internal
  ESP).
- **Game uninstall no longer leaks the per-game dev_hdd1 cache** — it
  resolved the same wrong hardcoded path the cache screen's clear did, and
  now shares the cache screen's resolver.
- **TOOLS-menu on-select help no longer lands under the footer** at short
  terminal heights — a pre-existing anchor bug affecting several menu
  entries, repaired for all of them.
- **The session ledger names the emulator build again.** Rows had been
  recording the emulator slot of the stack fingerprint as unknown (`r?`)
  since the new core line arrived — the old parser keyed on a label only the
  early builds carried. It now reads the build's own "GTK Edition" stamp,
  which every build carries, so A/B comparisons stay attributable across
  core updates. A stock emulator still reads `r?` on purpose: that is the
  signal that no custom core ran.

### Stack
- **RPCS3 GTK Edition 0.9.0.3 is the certified core** (was 0.8.5). The new
  line moves to a newer upstream base carrying months of emulator fixes,
  retires the temporary SPU workaround — the real fix landed upstream — and
  adds a fix for stacked duplicate recovery notices found while validating
  on a second machine.
  First launch after update recompiles shaders; per-game pins and saved
  tunes carry over untouched.
- **Turnip catalog grows to seven** (nothing removed, downgrade path
  intact): Mesa **26.2.1** joins as a stable option, and the development
  build the late-August sessions actually ran joins under its full name.
  Development builds now carry their snapshot date in the filename, so the
  newest one is obvious in the DRIVER list. The certified default stays
  **26.2.0** until the newcomers earn track time.
- **Kernel unchanged**: `20260827-0.5` (7.2.0), the build validated live in
  0.8.6, ships again as-is.

## [0.8.6] - 2026-08-28 — Qualifying Lap Edition

**One month from now the chassis changes; this release is the practice lap.**
ROCKNIX's September base moves the kernel a whole major version (7.1.2 → 7.2),
rebuilds the boot menu generator, and re-tunes the GPU's entire operating
envelope in the device tree. Rather than meet all of that on race day, this
cut runs the full course early on the public nightly the release will be cut
from — every brake point at speed: the in-place OS update, the one-bad-boot
self-heal, the kernel rebase, the boot-config rewrite, and (when it went wrong)
the recovery ladder all the way down to fastboot. The rig came off the lap
running the new chassis at full GTK spec. What broke on the way got fixed
where it belongs — in the kit — so the official monthly lands as a re-run,
not a first contact.

### Added
- **7.2 kernel lane** (`rocknix-gtk` `build_72.sh` + `stage_72.sh`, forge
  `FORGE_KERNEL_BUILD=72`): the GTK kernel rebased onto the 20260901-era
  base. Patch stack re-verified against real 7.2 sources — the q6afe
  silent-boot fix and KGSL-parity keepalive carry forward unchanged; one
  Type-C patch slims to its still-needed half; the DP enable-lock patch
  retires (upstream removed the lock it bounded). Ships as
  `KERNEL.rocknix-gtk-20260827-0.5`, validated live on track.
- **Numeric boot-default pin** (install.sh STEP 6.4 + the image lane): the
  new base's boot menu cannot resolve name-based default entries and its
  auto-detect overrides every earlier mechanism — the kit now pins the
  default by menu position, injected after everything that could override
  it, on the internal install and the flashable card alike. The install
  verdict names the resolved entry so a wrong default is a headline, not a
  surprise.
- **Stock boot-config convergence**: with no custom kernel staged, install.sh
  now converges the boot menu to the OS's own canonical config plus the
  numeric default — a recovered rig is a replica of a real install, owned by
  the kit instead of hand edits.

### Fixed
- **The OS-update self-heal actually arms on this rig's layout**: the guard
  service raced the mount that provides its own script and silently never
  ran — the exact failure the September update would have needed it for. It
  now orders after the storage rebinds (the same lesson the black box paid
  for on 2026-08-11).
- **4Kn law for internal-disk images**: any boot-partition image for the
  internal drive must be built with 4096-byte sectors — a 512-byte-sector
  filesystem crashes the bootloader itself into a reset loop that looks
  bricked (it isn't: fastboot's `flash ROCKNIX` rung recovers it, now a
  documented path).

### Changed
- Kernel manifest pin → `20260827-0.5` (7.2.0, nightly base); flashable image
  base → the same nightly. The Turnip certification pin is deliberately
  unchanged — this lap tests the track, not new machinery.
- RPCS3 core 0.9.0 (ARMSX3 base) was certified mid-window (2026-08-20) and
  rolled back on 2026-08-27 when the deploy gate began demanding cert pins
  with sources. The shipped core is 0.8.5 again, freshly re-minted — same
  filename, new sha. *Correction 2026-08-31: this entry originally said all
  certification pins were unchanged.*

## [0.8.5] - 2026-08-10 — Good Manners Edition

**The kit stopped being a Gran Turismo rig, and its controls had not noticed.**
For most of the campaign the ETK ran one series on one device, so an ETK chord
parked on a bare shoulder button cost nothing — the GT titles do not bind it.
In the week before this release the ledger recorded **35 distinct PS3 titles**,
up from a steady 6–14, with **66% of sessions outside the GT family** (18%
before). Every one of those titles has its own idea of what L1 does. This
release gets the kit's hands off the wheel: the chords that were stealing game
controls now need a deliberate gesture, or can be switched off outright.

Alongside that, three things were found by looking rather than by failing: the
published reference tunes had drifted a release behind the rig, the release
gate could not tell two different kernels apart, and one ledger row had been
quietly poisoning every rate the project computes.

<img src="https://raw.githubusercontent.com/mercurious/etk/main/docs/charts/library.png" width="720" alt="Bars of distinct PS3 titles raced per week rising from 7 to 35, with an orange line showing the share of sessions outside the Gran Turismo family climbing from near zero to 64 percent." />

### Changed
- **The screenshot chord moved from bare `L1` to `L1` + `L2`.** A lone shoulder
  button is a control real games use — handbrake, look-back, shift-down — and
  it was firing the shutter underneath them. L2 is the *analog* trigger on this
  pad and it rests **nonzero** after first actuation (~12/255, the H7
  trigger-cal finding), so the modifier is gated on a hysteresis pair rather
  than `value > 0`; the naive reading would latch the modifier on after the
  driver's first brake and turn every later L1 into a shutter press. The
  `SELECT`+`DPAD-Up` fallback is unchanged and still ungated.
- **The HUD punchbox (`R1` + `L3`) must now be held**, ~0.4 s, before the
  overlay cycles. A stick-click with a bumper down is ordinary play; holding
  both is not. Let go early and nothing happens. The input loop now waits on
  the pad with a timeout instead of blocking in a read, and arms that timeout
  *only* while a press is pending — with nothing pending it blocks exactly as
  before.
- **`L1` + `R3` recovery is deliberately untouched.** No hold, no queue, no
  policy gate — the panic path fires on the press, as it always has. The chord
  test suite asserts this directly so a future convenience cannot erode it.

### Added
- **TOOLS → Bog Sampler** switches the `R1`+`DPAD-Down` performance sampler on
  and off. The bog profiler is a forensic instrument; on a title that binds
  that combination it is pure interference. Sits next to the screenshot toggle,
  reads live, needs no restart. Default on — it has been the perf lane's entry
  point since 2026-07-10 and defaulting it off would retire it by stealth.
- **`tools/test_chords.py`** — the chord map gets a regression suite. It drives
  the matcher with synthetic evdev frames (no pad, no rig) and covers the
  analog-latch bug, hysteresis chatter, hold-and-cancel, the Chiaki
  stand-down, and that the dispatcher actually *consults* each gate — a check
  added after deleting a gate left the function-level tests entirely green.
- **`tools/sync_game_configs.sh`** — the per-game reference tunes in `config/`
  are what a fresh clone reads to learn a title's settled configuration, but
  every tune is authored on the rig and `install.sh` only ever carried the
  results as far as gitignored host state. The notebook had frozen on
  2026-07-24: **24 of 41 titles had drifted**, and 13 more titles the rig runs
  had no entry at all. A deploy now refreshes it, and the release gate fails a
  cut whose notebook is stale.
- **`tools/chart_library.py`** — the first chart generator committed to the
  repo. The three charts already in `docs/charts/` were rendered by scripts
  that live in no repository, which is the same defect the 2026-08-05 fleet
  audit found in four build lanes. A README chart is a shipped artifact.

### Fixed
- **The release gate could not tell two kernels apart.** `-0.3.1` and `-0.4.1`
  are both **exactly 60,246,528 bytes**; only the hash distinguishes them. The
  gate checked that a pinned artifact *exists* but never that its pinned sha is
  the sha of the build actually staged — so it passed while three of four pins
  named a kernel built eleven hours before the DisplayPort patch series landed.
  It now compares every pin against the staged file and its forge sidecar, and
  checks that the image lane and the forge target the kernel the manifest
  ships.
- **The certified RPCS3 pin was one build behind the forge.** The core was
  re-minted on 2026-08-07 and staged with its sha sidecar, while `install.sh`,
  `gtk_stack.json` and the PowerShell port all kept pinning the build it
  replaced. All three agreed with each other, which is exactly why nothing
  caught it.
- **One ledger row was poisoning every rate in the project.** A 2026-08 PANIC
  row carries `duration_s=1251432772` — **39.7 years** — from a session anchor
  that read as 1986. `session_postmortem.sh` guarded against a *negative*
  duration but not an absurd one, and the row is well-formed in every other
  column, so nothing rejected it: it made one month of racing total 347,694
  hours and drove rescues-per-hour to `0.0`. Medians were immune, which is why
  it survived weeks of analysis unnoticed. An implausible anchor is now routed
  to the existing honest-unknown path — timing dropped, row and classification
  kept. **`START_EPOCH` is deliberately not zeroed**: that would downgrade a
  genuine kernel panic to `ABORTED` and lose the crash record to fix a number.

### Stack
- **Kernel `20260801-0.4.1`** replaces `-0.3.1`. Same two core patches —
  verified by their markers *inside the built image*, not by reading the patch
  directory — plus five typec/DisplayPort/dpu patches. The withdrawn q6asm
  24-bit patch is staged in no build tier.
- **Turnip certified default advanced** `26.1.6_gtk_0.7` → `26.2.0_gtk_0.7`,
  and `26.3.0-devel-e40d93a_gtk_0.7` joined the catalog as the pre-release
  slot; the cumulative catalog keeps every earlier build as a downgrade path.
- **RPCS3 GTK Edition 0.8.5**, re-pinned to the 2026-08-07 re-mint it had
  fallen behind (see above).
- *Correction 2026-08-31: this block originally claimed Turnip was unchanged;
  the tag's own manifest certifies `26.2.0_gtk_0.7`.*

### Also in this release (landed after 0.8.4 shipped)
- **`forge.sh`** — the etk-cloud build conductor. Six lanes, detached builds that
  survive a dropped ssh, fingerprint-based skipping, and machine-readable status.
  See `TRACK_MANUAL.md` §8.6 for the operating guide and use cases.
- **DP capture audio fixed at its mechanism.** The DisplayPort sink negotiated
  S24_LE and lost ~25 dB — the "had to add +1000% gain in OBS" bug. A WirePlumber
  rule pins the capture sink to S16_LE; inert during normal speaker use.
  Kill-switch `ETK_DP_AUDIO_S16=0`.
- **Pad self-heal on DP unplug.** Unplugging the external display used to wedge
  the pad (an InputPlumber + EmulationStation double wedge) and cost a reboot.
  Now recovers in place. Default on; `ETK_DP_MIRROR=0` removes the daemon.
- **`wl-mirror` moved to the fork model** (`mercurious/wl-mirror-rocknix`), the
  same pattern as chiaki: docs and recipe above a pinned build ref.
- **`SECURITY.md`** and a re-synced PowerShell port, whose cert pins are now
  gated in lockstep with `install.sh` — they had drifted two releases.

### Known limitations
- **Kernel panics are up, and this release does not fix them.** Per hour of
  racing the rate went 2.1 → 2.7 → **9.7 per 10 h** across June, July and
  August. It is *not* the new titles: titles first seen in August panic at
  9.1/10 h and long-established ones at 9.8/10 h — the rise is fleet-wide.
  RPCS3's own `peak_ram` does not implicate emulator memory growth (panicking
  sessions report *lower* peaks than clean ones even at matched duration),
  but a panic truncates the session before the climb, so that instrument
  cannot answer the question — which is itself the finding. August was also a
  dense multi-arm bench period running several non-shipping cores and drivers;
  the arms carrying the shipping-adjacent core show ~2.7/10 h against ~18/10 h
  for an older one. That is a hypothesis for a controlled test, **not a
  verdict** — the arms differ in title mix and were never controlled.
- **A title that hangs only on EXIT reads as unstable.** SoulCalibur V plays and
  then wedges when you quit, so every session ends on an R3 recovery and the
  ledger scores it 10% clean. The rows are correct; the inference is not.
  `config/game_status.tsv` now carries a `hangs-on-quit` flag and the charts
  stripe that segment, but the ledger still cannot tell the two apart on its
  own — it takes an operator's eyes.
- **`config_RXSTR3179.yml`** on the rig is a golden seed against a filename tag
  that never resolved to a serial, and `config_IDLE.yml` is the id resolver's
  own sentinel written as a game key. Neither is a tune; both are skipped by
  name-shape and reported rather than silently filtered.

## [0.8.4] - 2026-08-07 — Cloud Forge Edition

**Every binary in this release was built on a machine that is not the maintainer's
laptop.** Kernel, both Turnip drivers, the RPCS3 core, the Chiaki and wl-mirror
helpers and the flashable SD image were all minted on a free Oracle Ampere A1
node from recipes that now live in git — and each was verified against the
artifact it replaces rather than against the build log. Getting there forced out
a class of defect that had been invisible for releases: pins naming files that
no longer existed, a source lineage that survived only inside one Docker volume,
and rendered config snapshots frozen years behind the version they claimed.

### Fixed
- **Fresh installs were silently running the stock Vulkan driver.** `install.sh`
  pinned `etk_turnip_rocknix_26.1.6_gtk_0.6.so`, which had been unpublished after
  a shipped debug-flag bit collision was fixed in `gtk_0.7`. The pinned asset
  404s, and the fetch is deliberately fail-soft, so every new install quietly fell
  back to stock Turnip and lost the ETK gears. `config/gtk_stack.json` carried the
  same stale pin, so the release gate's consistency check passed them both.
- **The boot line announced the wrong edition.** The ETK boot-identity service
  printed `ROCKNIX-GTK 0.7.0-<date>`: install.sh templated the date but the
  version was a literal frozen at the release the unit was written in. It now
  templates from `APP_VERSION`, the same string self-update compares against.
- **A freshly flashed card announced a worse one.** The hostless seed payload
  carried a *rendered* copy of that unit, baked at `0.7.0-20260706`, installed
  directly by the `/flash` hook without ever running install.sh — so the fix
  above could not reach an SD install. The image build now re-renders it from the
  template using the version and kernel date it is actually baking.
- **The image lane could not have run at all.** Its default inputs still named
  the deleted `gtk_0.6` driver and a superseded kernel, so the build would have
  died at its own input check; its label comment claimed the distributable used
  standard partition labels, which reading the published image disproves
  (`ROCKNIX-GTK` / `GTKSTOR`).

### Added
- **The release gate now asks whether a pinned artifact is real.** Every existing
  check validated names and cross-file consistency — which is how two files could
  agree with each other and both be wrong. `tools/release_sanity.sh` now probes
  local existence as a hard failure and release reachability as an advisory one
  (a cut legitimately precedes its own upload).
- **`tune_tag` records the PPU/SPU decoders.** A decoder is a tune, and a
  non-default one was invisible everywhere. A diagnostic left on static
  interpreters made one title appear broken on every emulator core, survived
  reboots and reinstalls, and cost a full session before `config_changes.tsv`
  gave it up. Off-default decoders now ride in the ledger row.
- **Crash logs survive the next launch.** RPCS3 truncates its log at every start,
  so a crashed session's evidence lived only until the next one. The postmortem
  now archives it beside the audio and perf logs, keyed to the ledger row.
- **`etk_dyno.py --audio`** ranks titles by audio skip-per-second, with a
  retroactive filter for SHM cross-contamination in pre-fix rows.

### Changed
- **RPCS3 GTK Edition 0.8.5** is the certified core, replacing 0.8.1. It carries
  the full LLVM-22 toolchain gains and keeps GT5P Spec II bootable via a
  `noinline` barrier on `spu_thread::stop_and_signal` — a cheaper mitigation than
  the `optnone` it replaces, which de-optimised the whole function (1300
  instructions and 243 out-of-line calls) on the SPU stop/signal hot path where
  `noinline` leaves it fully optimised at 1549. See "known limitations".
- **Kernel `20260801-0.3.1`** — same source as `-0.3`, rebuilt on the forge.
- **Turnip `26.1.6_gtk_0.7` and `26.2.0-rc3_gtk_0.7`** reminted. New driver build
  ids invalidate cached pipeline objects, so **the first launch after updating
  recompiles shaders before the menu.** That is expected, not a hang.
- **The RPCS3 0.8.x patch lineage is published** in `etk-rpcs3-gtk/patches/`.
  It had never been committed anywhere; five revisions, including the shipped
  core, existed only in a Docker volume on one machine.

### Known limitations
- **GT5P Spec II's fix is a mitigation, not a repair.** Five one-variable builds
  isolated it: `optnone` on an inert function, and on two other SPURS-path
  functions, all still crash identically; only `stop_and_signal` boots. Both
  working fixes act by forcing a real call where `cpu_task` would otherwise
  inline it, so the defect is an ordering problem at that inlining boundary —
  which is why an AddressSanitizer campaign found nothing (ASan cannot see
  ordering bugs). **It must be re-verified after any toolchain or base change.**
- Ridge Racer 7 fails on both cores and both drivers; not a toolchain regression.

### Changed
- **Installing a game no longer takes the app hostage.** A PS3 package or
  firmware install used to run inside a single Pitstop main-loop iteration:
  no input, no tab switching, no leaving until RPCS3 was done — several
  minutes of staring at a spinner for a big game. It now runs in the
  background, the way EmulationStation scrapes: the Pitstop queues the job and
  hands it to a worker that runs **out of process**, so you can keep using the
  Pitstop, go back to your games, or close it entirely while the package
  unpacks. Progress and the verdict arrive on the notification surfaces.
  Queue several and they install in order. Kill-switch `ETK_BG_INSTALL=0`
  restores the old modal installer.
- **A game always wins.** ROCKNIX rebuilds RPCS3's config directories at every
  game launch — the same directories an install is writing into — so starting
  a race mid-install was never safe. The worker watches for one, stands its
  install down, puts the job back at the head of the queue and says so; it
  resumes when you are finished playing. "RPCS3 is already running, close the
  game first" is therefore no longer a refusal: it queues.
- **Your telemetry survives an install now.** The Sentry used to park itself
  for the whole install — the only way it had to avoid mistaking the
  installer's RPCS3 for a game — which left it blind to everything, including
  a real race. It now tells the two apart directly (the headless installer
  carries `--installpkg`/`--installfw` in its arguments; a game launch never
  does), so a session started during a background install is recorded like any
  other.

### Fixed
- **Pausing an install showed two contradictory messages.** Standing an install
  down to let a game start makes the installer report a genuine failure — it
  was killed — so "install failed" flashed up immediately before "install
  paused". The installer is now muted while the kit is deliberately tearing it
  down, leaving the one message that is actually true.
- **A firmware or package install could get stuck in a retry loop**, flickering
  between "install failed" and "install paused" and never finishing. The
  emulator ships as an AppImage, which spawns a filesystem helper carrying the
  image's own name — so the check for "is a game running?" was seeing the
  install's own helper and standing the install down, over and over. Found on
  the rig and fixed by identifying the emulator by the program being run rather
  than by anything that merely mentions it. As a backstop, an install that
  stands down repeatedly now gives up cleanly and says so instead of retrying
  forever.
- **A licence (`.rap`) could be silently dropped by a background install**, so
  a DRM'd game installed, reported success, and then refused to boot. The queue
  wrote the licence list as one field, which turned it into text the installer
  read as a single bogus path. Caught in review before it shipped; licences now
  ride as separate fields and the round-trip is gated.
- **A background failure could be silent.** Several of the installers' early
  returns predate the background path and only ever reported themselves on the
  result screen — which nobody is looking at once the job has been handed off.
  The worker now always leaves a verdict on screen.
- **An install could start on top of the session postmortem.** The Sentry reads
  RPCS3.log whole to write a finished race's ledger row, and an installer
  rewrites that log the moment it launches. The worker now waits for the rollup
  before taking a job, so finishing a race and queueing an install no longer
  costs the race its telemetry row.
- **A game ending during an install lost its ledger row**, because the Sentry's
  install-lock branch swallowed the RUNNING→IDLE edge that fires the postmortem.
- **An install timing out could kill the game you were playing.** The
  installers' cleanup was a pattern-wide `pkill -f rpcs3-sa`, which matches
  any RPCS3 — including a race the operator had just started. Install paths
  now terminate only their own emulator, by PID.

### Changed
- **Notifications now speak EmulationStation.** ETK had invented its own
  toast — a 1280×560 cyan-bordered panel unlike anything else on the rig.
  It is replaced by ES's own two surfaces, mirrored one-for-one, so the kit
  reads as part of the system instead of a bolt-on: a **top-center verdict
  toast** (ES's `GuiInfoPopup` — ~10 s, centered, the black system pill
  ROCKNIX already uses for volume and brightness) and an **upper-right
  progress card** (ES's Scraper card — left-aligned title + name, a real
  progress bar, alive for exactly as long as the job, then dismissed and
  answered by a separate verdict toast, which is ES's own order of events).
  Message copy is rewritten to ES's character economy throughout: an
  uppercase verb for the summary, the name and one fact for the body. The
  build-era prose that explained what the kit was doing to itself is gone.
- **Real progress bars.** ETK believed mako had no progress widget and drew
  ASCII meters into toast text. The shipped mako is 1.10.0, which renders the
  standard `value` hint natively — so downloads, PADDOCK syncs and the
  self-update now show a true bar. `dbus-send` cannot marshal the hint's
  nested `a{sv}`, so those go out through `busctl`; where that is missing the
  bar degrades to the old ASCII meter rather than losing the notification.
  Jobs that genuinely cannot report a fraction (a headless PKG extract) show
  an elapsed clock and no bar — the same thing ES does — instead of a
  fabricated percentage.
- **A finished install just appears in your library.** Pitstop asks
  EmulationStation to rescan (its localhost HTTP API) after an install or
  uninstall, and the rescan lands on the first ES frame after Pitstop exits.
  The "Press START > Game Settings > Update Gamelists" instruction is
  retired from the toast, the results screen and the README.
- **One shell sender.** `bin/etk_notify.sh` replaces four hand-rolled copies
  of the same `dbus-send` incantation (Pitstop, osguard, bog profiler,
  Chiaki). Chiaki toasts previously sent an app-name that matched no mako
  criteria and fell through to the stock style by accident; they are now on
  the ETK surface on purpose.
- **The ETK self-update reports itself.** It ran silently before — several
  minutes of download and kernel staging behind a bare spinner. It gets the
  same progress card and verdict toast as every other long job.

### Fixed
- **`tools/test_installers.py` was scoring its own notifications.** The
  fixtures patch `pit.subprocess.Popen`, and `subprocess.run()` resolves
  `Popen` from the same module globals — so the installers' progress-card
  heartbeat was being routed into the fake RPCS3. The firmware fake ignores
  its arguments, so a notification performed the install: `[FW-1]` passed even
  when the real `--installfw` did nothing. The suite now stubs the
  notification surfaces, and fails as it should against a no-op install.
- **`uninstall.sh` left its notification styling on the rig.** The mako
  criteria block lives in ROCKNIX's own config, outside `$ETK_ROOT`, so an
  uninstall never removed it and the rig kept styling toasts for a kit that
  was gone. Now stripped (legacy block included), leaving the operator's own
  sections untouched.
- **Re-installing grew the operator's mako config by a blank line every
  time.** The 0.8.3 strip-and-append left the previous block's leading blank
  behind, forever. Trimmed, and the round-trip is now byte-idempotent.
- **Guarded against a mako config outage.** One invalid option makes mako
  reject the whole config — a reload keeps the old one, but the next boot
  mako exits and the rig loses *every* notification, ROCKNIX's included.
  install.sh now backs up, reloads, and rolls back on rejection. (`max-visible`
  is illegal inside an app-name criteria; the new blocks do not use it.)

### Added
- **`tools/test_install_queue.py`** — release gate for background installs.
  Runs install.sh's own game-vs-installer test as shipped text against a
  fixture `/proc` across eleven cases (each installer kind, game-only, a game
  launched during an install, AppRun-era games, the postmortem's own log grep,
  the worker's and Pitstop's argv, two installers, and a ROM whose filename
  contains `--installfw`), holds the Python rule to the same answers, and
  proves the install kill never targets a game PID. Checked against four
  deliberately broken variants; the shell logic verified under BusyBox.
- **`tools/test_notify.py`** — release gate for the notification surfaces:
  pins every sender's app-name to install.sh's criteria headers (mako matches
  byte-exact, so a typo silently downgrades a toast to the stock style),
  refuses config-killing options, asserts each criteria has room for at least
  three text lines (mako's default height renders the summary alone and
  silently drops the body), holds the shell sender to its exit-status contract,
  and runs the real installer/uninstaller config surgery for idempotency and
  clean removal. Verified against nine deliberately broken variants; the awk
  and the sender checked under BusyBox rather than host tools.
- **TUNING > CORE: per-title emulator core swap** (multigame lane). The LLVM
  19-vs-22 split made core choice per-title (RR7/Ratchet/Spec II regress on
  22; ABC/TTT2/GTA:SA gain), so the DRIVER-tab catalog pattern is replicated
  for RPCS3 cores — per-title and launch-cadence: a launch wrapper bound
  over `/usr/bin/rpcs3-sa` resolves serial → core via
  `$ETK_ROOT/emulators/core_map.tsv` and execs the pinned AppImage; no pin =
  the certified default, no reboot per swap, fail-soft everywhere. The
  catalog is operator A/B tooling (host `emulators/*.AppImage` staged to the
  game card by install.sh, mirror-with-GC that never prunes a mapped pin) —
  not a distribution channel; the only shipped emulator remains the
  certified build. Attribution is non-negotiable and marker-based: the
  wrapper stamps `active_core.txt` (persistent, so even a PANIC row knows
  its core) and `tune_tag` gains a `core=` segment — ground truth from the
  resolved file, not the binary's baked branch string (retiring the
  `r0.8.0-19638` wart). Kill-switch `ETK_CORE_SWAP=0`.
- **TUNING tab: PPU/SPU Decoder fields** — the interpreter A/B without ssh.
  Enum values verified against fork source; ASMJIT deliberately omitted
  (x86-only, dead code on ARM64).
- **TUNING > PATCH: community patch toggles** (multigame lane §3 — NOT a
  new patch system; RPCS3 ships the framework, ETK surfaces it). install.sh
  refreshes the rig's community `patches/patch.yml` from the same official
  endpoint the desktop GUI uses (JSON contract verified against fork
  source; sha-verified, offline-safe, gated on the RPCS3 config dir
  existing). Pitstop auto-detects the patches declaring the selected title
  and shows them as switches — Notes become the pit-engineer help text —
  writing RPCS3's own `patch_config.yml` (dependency-free parser/emitter in
  `bin/etk_patchlib.py`; no PyYAML on the rig). The upstream version
  anti-trap is defused structurally: enabling writes an Enabled leaf under
  EVERY app version the patch declares, so a version mismatch can't
  silently no-op. Attribution: the launch wrapper stamps the serial's
  active set and `tune_tag` gains `patches=slug,slug` (omitted when empty —
  patch-free rows group unchanged). A patch is a tune. Kill-switch
  `ETK_PATCH_FETCH=0`.
- **TUNING tab: CORE / CONFIG / PATCH section headers** — non-selectable
  rules; the classic flat list is preserved on kill-switched or pre-wrapper
  rigs.

### Staged (dev, not yet certified)
- **RPCS3 GTK Edition v0.8.4-dev** (2026-08-05) — restores the **`GTK_PROBE_11912`**
  TIU transition probe, the diagnostic that localized the #11912 road-flicker in the
  first place. It was silently dropped during the 0.7.x patch consolidation and was
  absent from 0.7.5 through 0.8.3, so `GTK_PROBE_11912=1` did nothing on any of those
  builds. Found during an SSD audit, not by any gate — so a gate now exists:
  **`scripts/verify-markers.sh`** asserts all 13 shipped-feature markers survive into
  both the cumulative patch and the built binary, and fails the publish otherwise
  (it flagged 0.8.3 immediately; 0.8.4 is 13/13). This is the second silent feature
  loss in the same consolidation step — the first dropped `RSXTexture.cpp` and with it
  the flicker fix itself (corrected 2026-07-09).
- **RPCS3 GTK Edition v0.8.3-dev** (2026-08-05) — 0.8.2-dev **plus a one-attribute
  workaround for a clang 22 miscompile**. `clang 22.1.8 -O2` on aarch64
  miscompiles `spu_thread::stop_and_signal` (clang 19.1.7 does not), corrupting
  the SPURS group exit/restart context; pre-2008-SDK SPURS kernels — GT5P Spec II
  [BCUS98158] — then restart at a garbage PC and die before the copyright screen.
  Decoder-independent (SPU LLVM, dynamic and static interpreters fail
  identically), which is why no config change could ever fix it. Isolated across
  **8 hardware-boot rounds** of pragma/`optnone` bisection. **Keeps every LLVM 22
  gain and boots Spec II**, so it supersedes the dual-core plan unless other
  titles still need the LLVM 19 core (the 0.8.1 artifact is retained for that).
  Mitigation only — to be dropped once the root cause (compiler bug vs. latent UB)
  is settled; upstream report drafted.
- **RPCS3 GTK Edition v0.8.2-dev** (2026-08-04) — LLVM 22.1.8 toolchain rebuild
  of 0.8.1 (same source; new `etk-rpcs3-jammy-aarch64:llvm22` image from
  upstream rpcs3-docker `e261762`). Closes the last gap against upstream's arm64
  advancement set: the LLVM 22 arm64 backend that official builds have carried
  since ~Jul 18. With this, GTK carries the SDOT/UDOT SPU optimizations, the
  loop-iteration-prediction path (ACTIVE, unlike current upstream master), and
  the LLVM 22 backend. **Deploy requires a per-title PPU cache clear** (`ppu-*`
  dirs only; shader caches preserved) — LLVM-19-built v8 objects must not mix
  with the LLVM 22 binary; first boot per game recompiles PPUs.
  Toolchain saga: ~30 h; first attempt died ENOSPC at hour 22 (colima disk
  100%); BUILDING.md gained image-freshness + VM-disk-preflight doctrine.

## [0.8.3] - 2026-08-01 — GTK 0.8.0 Full-Stack Edition

The whole chassis moves together: **ROCKNIX official 20260801** (kernel
7.1.2), **rocknix-gtk-20260801-0.3**, **RPCS3 GTK Edition v0.8.1** (base
19638), **Mesa Turnip 26.1.6 gtk_0.6** — the GTK stack steps 0.7.0 → **0.8.0**
in one release. And because an OS update on a kit rig used to be a trap, this
release makes it survivable: the OS Guard self-heals the post-update boot and
the couch update now carries the kernel. **Update ETK to 0.8.3 BEFORE taking
the ROCKNIX 20260801 OS update** — see README § ROCKNIX OS Updates.

### Added
- **Full-stack self-update: the couch update now carries the GTK kernel.**
  "We never ship a GTK without its feature set" — Pitstop's Check for ETK
  Updates was middleware-only, which after a ROCKNIX OS update would have
  parked hostless users on the stock kernel (no ANTI-LOCK kernel net)
  indefinitely. Now: `config/gtk_stack.json` (the stack manifest, riding the
  release tarball so pins are always the installed tag's own) names the
  kernel asset + sha256 + the ROCKNIX release it requires; self-update
  fetches it from the same release, sha-verifies, and hands it to
  `bin/kernel_stage.sh`, which banks the osguard heal bundle and harvests
  the live grub block. **Activation stays osguard's job** — the staged
  kernel goes live only on a boot whose OS module tree matches
  (`requires_os` gating), so staging can never make a rig unbootable, and
  the advised update order (ETK first, then ROCKNIX) lands users on the
  full GTK 0.8.0 stack automatically, no computer involved. Release
  contract: the kernel artifact is now the FIFTH asset on every release;
  `tools/release_sanity.sh` asserts manifest↔install.sh pin lockstep at
  every cut. Suites: `tools/test_kernel_stage.sh` 18/18 on host and rig
  BusyBox.
- **OS-update coherence guard (`bin/osguard.sh` + `etk-osguard.service`,
  default-ON, `ETK_OS_GUARD=0` kill-switch).** Makes ROCKNIX in-place updates
  survivable on a GTK-kernel rig. Born from the 2026-08-01 update-day
  frankenboot: the ROCKNIX updater writes the new kernel over the slot the
  *running* boot used, strips the ETK grub entries from both twins, and
  re-seeds grubenv (observed: wrong device id) — leaving an old-kernel/
  new-SYSTEM boot with zero loadable modules (no WiFi, no sound). The guard
  runs once per boot: **Phase A** detects the kernel/module-tree mismatch,
  promotes the matching kernel into `/flash/KERNEL` (staged GTK artifact
  preferred, sha-verified; displaced kernel backed up), refreshes the
  pristine-stock snapshot, and re-points grubenv at this device
  (confidence-gated); **Phase B** re-activates the GTK kernel on the next
  coherent boot by replaying the heal bundle install.sh now renders at
  deploy time (kernel + pre-rendered grub block + deploy mode — the guard
  replays, never re-derives). It never reboots the device; it toasts and
  the user reboots. install.sh STEP 6.45 deploys it unconditionally
  (Phase A protects stock-kernel rigs too); uninstall.sh removes it.
  Validated by `tools/test_osguard.sh` — 7 fixture scenarios replaying the
  real incident (heal, check-mode no-write, healthy no-op, Phase B replay,
  no-donor fail-loud, corrupt-stage rejection, kill-switch), 29/29 on host
  and on the rig's BusyBox.
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

### Changed — the GTK 0.8.0 stack
- **ROCKNIX base bump 20260701 → official 20260801 + kernel rocknix-gtk-20260801-0.3**
  (2026-08-01) — the OS leg of the full-stack bump (with RPCS3 19638 + Turnip
  26.1.6 already staged). Kernel rebased 7.0.11 → 7.1.2 against the 20260801
  patch stack; both ETK patches (anti-lock `msm.context_keepalive` KGSL parity,
  q6afe audio-probe-race) apply clean — no hunk drift. Stock-parity build:
  exact stock byte size, 237/237 modules, new 7.1 `ARM64_LSUI` toolchain-probe
  feature forced off to match the release binary. §4.4 cold-boot gate PASSED
  on the reference rig (keepalive armed, audio up, drift tool: no structural
  drift); warm-race certification pending.
  - **Field hazard for updaters (documented in AI_MANIFEST):** the ROCKNIX
    in-place updater writes the new kernel over the file the running boot
    used — on a GTK-kernel boot that clobbers `/flash/KERNEL.gtktest` and
    leaves the old kernel in `/flash/KERNEL`, and it regenerates grub twins
    + grubenv (seen seeded to the wrong device entry). Result: an
    old-kernel/new-SYSTEM boot with zero loadable modules (no WiFi, no
    sound). v0.8.x users updating the OS in place must re-run the installer
    with the new kernel; recovery procedure banked.
  - 20260801 verified-unchanged surfaces: `start_rpcs3.sh`, `get_setting`
    (the `[]` escaping bug persists — ISO rename workaround stays), ps3 ES
    extensions, MangoHud, BusyBox, python3, grim. Notable upstream changes:
    Retroid pad driver is now a module (input nodes renumber; ETK matches by
    name), InputPlumber starts earlier, `output_monitor` now forces external
    output to 1080p60 + auto-switches the HDMI audio profile, and the SM8250
    DT gains DP/HDMI **audio** (new feature lane for video-out).
- **RPCS3 GTK Edition v0.8.1** (2026-08-01; certified pin this release) — 0.8.0-dev **plus a temporary
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

### Added — for the curious
- **Turnip 26.2.0-rc3 prerelease ships in the DRIVER-tab catalog** alongside
  the certified 26.1.6 — installed automatically, sha-verified, one
  selection away. Prerelease and unvalidated; the `rc3` in the filename is
  the warning label, and that's all the gating an expert enthusiast needs
  (operator doctrine, 2026-08-01). Newest-upstream Turnip packaged for
  ROCKNIX is also a deliberate Android↔ROCKNIX-parity signal.

### Known issues
- **Non-GT titles may regress on the emulator base bump.** The GTK is a
  GT-specialized project; incidental titles ride along at their own risk.
  Observed: **Ridge Racer 7 (NPUB30457)**, playable on 0.7.5, now struggles
  to reach its menu on the 19638 base (three short RECOVERY:Silent sessions
  in the ledger, 2026-08-01). Observation banked, not diagnosed — GT titles
  are the mission.
- **First boot per game does a full PPU recompile** on the new emulator
  (upstream obj-cache v7→v8) — long and thermally heavy, not a hang.
- **Old savestates will not load** (upstream global savestate version bump).

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
