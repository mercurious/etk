# ETK TRACK MANUAL — v1.0 (2026-08-11)

The ONE system manual for the **Emulation Tuning Kit (ETK / GTK Edition)**. It bootstraps every
session: the mission, the machinery, the method, and the laws — each law carrying the incident
and date that bought it. `AI_MANIFEST.md` was synthesized into this manual and retired on
2026-08-11 (its file is a tombstone pointer; git history is the archive). Where a claim is
load-bearing it carries its evidence (date, N, commit, or dossier).

**How to bootstrap a session:** read §0, then §1–§2 (the preludes), then the modality section
for today's workstream — and only that section, in depth:

- **§A. BUILDING the kit** — forging binaries, deploying, UI/Pitstop work, roadmap.
- **§B. USING the kit** — debugging a game: collecting, analyzing, and distrusting telemetry.
- **§C. RELEASING the kit** — public voice, evidence, naming, packaging.

§F is the falsified list (never re-propose); §Q is the quick reference.

---

## 0. OPERATOR LOOPS — what the kit is FOR

Everything below this section describes what the kit **is**. This describes what the operator
**does with it**. Read it first — it is short on purpose, and it is the part that has been
missing: every surface here was documented as a mechanism ("BUILD selector, reboot-gated")
and never as a purpose, so a change could satisfy every mechanism and still not reach the
person using it.

| The operator is… | Surface they touch | What must be true |
|---|---|---|
| trying a new **Turnip** as Mesa ships it | Pitstop **DRIVER** tab | catalog is CUMULATIVE **and present on the device** — installed rig *and* flashed card; cold boot to load; `loaded` is boot-stamped ground truth, `selected` is only intent |
| **A/B-ing an emulator core** | **TUNING → CORE** | exactly two cores staged, never published; a per-title pin is never pruned |
| **certifying / swapping the emulator core** | install.sh **NEXT-BOOT BIND** block (STEP 6.553) + the first post-boot ledger row's `core=` | the CERT pin resolves to a REAL source (local catalog copy or published asset — release_sanity gates it); a staging failure NEVER downgrades a staged rig; `loaded=stock` on a custom-expecting rig is a RED headline, not an OK |
| **tuning a title** | **TUNING** | atomic write + read-back; `tune_tag` reaches the ledger, or the A/B is unattributable |
| **surviving a wedge** | `L1`+`R3` | fires INSTANTLY — no hold, no gate, no queue, ever |
| **capturing what just happened** | in-game chords | the chord must not collide with a control the game itself binds |
| **installing a game** | **TOOLS** | runs in the background; a game launch outranks an install |
| **updating from the couch** | **TOOLS → Check for ETK Updates** | every `gtk_stack.json` asset is fetchable from `releases/latest` |
| **judging whether a change helped** | the ledger · `etk_dyno` · charts | the row is attributable and every claim carries its N |

> **THE RULE THIS TABLE EXISTS FOR: every loop ends at a SURFACE, and a change is not done
> until that surface shows it.**
>
> Paid for on 2026-08-10. The Turnip catalog was made cumulative in `install.sh`, in
> `gtk_stack.json`, in the PowerShell port and in the release assets — four places, all
> correct, all gated — and a freshly flashed card still offered a **one-entry chooser**. The
> image lane staged one driver, and nothing connected "cumulative catalog" to "the thing the
> operator looks at." A host install hid it completely, because STEP 6.5 fetches the catalog
> to the rig; only the card was crippled. Three image rebuilds in one release.
>
> **Verify the surface on the ARTIFACT, not the pipeline that fed it.**

*(This table is deliberately incomplete — it lists the loops known to have bitten. Add the
ones you find; a missing row is how the last gap got missed.)*

---

## 1. NON-NEGOTIABLES (every session, every modality)

### 1.1 BYTES-TO-ATOMS — always defer to the humans at the threshold (Law #9; canonical text)

The Engineer works in **bytes** — repo, analysis, staged candidate, gate output — cheap,
private, reversible. There are **exactly three moments where our bytes reach real atoms**,
and each one gets a **human**, not because the human reasons better about it, but because
a human is the only thing that can be accountable for the consequence (operator rationale,
2026-08-10):

- **Deploy** — `./install.sh`, `./uninstall.sh`, `windows_installer/etk-install.ps1`.
  *Why:* **the rig can be bricked** — a physical device the operator holds, already
  frankenbooted once by an unattended kernel write.
- **Mint** — `./forge.sh`, `tools/forge/lane_*.sh`, `tools/rocknix-bin/build_*.sh`, any
  build on `etk-cloud` or in a colima container. *Why:* it runs on **someone else's
  computer** and can **trigger an invoice. Money is atoms.**
- **Publish** — `gh release create|upload|edit|delete`, moving a tag, `git tag` on a release
  version (cutting the tag IS the release), any upload to a fetch path. *Why:* **our bytes
  land on other humans' machines** — unreviewed, unrecallable once fetched.
  ⚠️ **SCOPE — the PRODUCT, not the SOURCE (operator, 2026-08-10):** an ordinary development
  `git push` to public `origin/main` is normal and permitted as usual, governed by the trunk
  protocol (§1.6), not by this law. The threshold is what a user CONSUMES.

**The test for a tool that does not exist yet** — do not ask "is it on the list," ask:
could this brick hardware? could it spend someone's money or consume someone else's machine?
could it reach another human's computer? Any yes = threshold = hand it to the operator.
The incident that made this a class, not a list (2026-08-10): every document named only
`install.sh`; three-day-old `forge.sh` inherited nothing and was run three times — its own
header ("NEVER contacts the rig, NEVER publishes") misread as reassurance — before the
operator stopped it. **When unsure whether a command crosses into atoms: it does. Ask.**

**`--dry-run`, `--status`, `--check` are NOT exemptions** — `forge.sh --dry-run` opens ssh
to the build node in preflight; a dry run is a hand on the control. Read the source instead.

**THE HANDOFF IS PART OF THE LAW (operator-directed 2026-08-10).** Deferring means putting
the control **in the operator's hand ready to press** — not going quiet. Close every handoff
with the exact command in its own ```` ```bash ```` fenced block (ONE command, no `$`, no
output interleaved) — Claude.app renders a Run button on it, the operator fires it, and both
watch the TUI live. This is the pit wall: Driver's hand on the control, Engineer reading the
instruments. Say what the command does, what to watch, and what would falsify it. **A
handoff without a runnable prompt is an unfinished handoff.**

**Still allowed (the carve-out matters as much as the prohibition):** read-only `ssh` to rig
or build node; every host-side gate/analysis tool (`release_sanity.sh`, `tools/test_*.py`,
`etk_dyno.py`); staging artifacts; editing `etk.conf` knobs; `git` under the trunk protocol.
Preparing a bytes-to-atoms tool's inputs is the whole job — running it is not part of it.

### 1.2 Always-reboot gate
Every tune/config change must survive a COLD boot; reboot is the only honest validation.
`/storage` persists; nothing else does (`/dev/shm` and `/tmp` die every boot).

### 1.3 Use the kit — including skill automation; and HARNESS vs TOOLING
Prefer ETK tools (install/uninstall, Pitstop, `etk_telemetry`, `recovery.sh`) and the
**cockpit skill's own loops** over hand-rolled ssh surgery. Concrete miss (2026-06-18):
hand-rolled per-freeze grim grabs while `rocknix_spotter_loop.sh` — a purpose-built
crash-watch — sat unused. "Prefer the kit's tools" names the MECHANISM a change travels
through; it is never permission to run a bytes-to-atoms tool yourself (§1.1).
**HARNESS vs TOOLING (2026-07-10, operator-directed):** disposable on-rig state is licensed
for tuning **values** only. Any script that watches, measures, toggles, or captures is a
**tool** and enters through the kit from its first run — file in the repo, on install.sh's
push list, driven from an existing surface. A `/tmp` hand-push died to two cold boots and
was re-pushed three times before this became law. *If a temporary script is worth running
twice, it is a tool.*

### 1.4 Validate before integrate
Prove speculative tuning on a disposable on-rig harness, cold-booted, before touching
locked-down core (install.sh, daemons, ledger schema, the R3 panic path).

### 1.5 Never reboot the rig remotely; the operator runs install.sh — always
Claude prepares and hands off (§1.1). No `reboot`/`systemctl reboot` over ssh, no
host-driven power actions. When a cold boot is needed, ask the operator (on-device or the
DRIVER-tab REBOOT row) and wait. A mid-race install on 2026-07-05 cost the operator a
race's credits and its telemetry (guard: `49a0aa1`). Read-only ssh is always fine.

### 1.6 Git — TRUNK-BASED protocol (2026-06-30, operator-directed)
Work on `main`; no feature branches by default (parked branches are how the 0.5.0 chords
were lost). Commit+push to `origin/main` **pre-authorized** for operator-validated changes,
same session — done-bar = *pushed*, not committed. Origin receives out-of-band web edits →
`git fetch` + `rebase`, **never force-push**. `garage` (private) never pushes to `origin`.
Still ask before: force-push, anything touching `garage`, history rewrites, destructive ops.

### 1.7 Identity, legal, and the dossiers
Public artifacts under the **mercurious** pseudonym only; docs stay development/tuning-
focused (public history was legal-scrubbed — keep it that way). Live `etk.conf` holds a
real GitHub PAT — never republish it. `dossiers/` is a private, gitignored clone: cite
freely, never commit here; **citations are expected to dangle in public checkouts** — a
missing `dossiers/…` file is provenance, not a bug; never "restore" one into the repo.

### 1.8 Session start
`git status` scope-check first: uncommitted/untracked files may be parked experiments
(e.g. pad-movie B-fork). When the task and the diff diverge, ask which files are in scope.
The rig is **always ready** (USB-net `169.254.170.2` + WiFi `root@SM8250.local`) — don't
burn a call confirming reachability.

---

## 2. THE RIG MAP (mission, machine, machinery)

### 2.1 Mission, KPI, roles

**Mission:** specialize ONE SM8250 handheld (Retroid Pocket Flip 2) to run the PS3 Gran
Turismo series at race stability approaching console quality — RPCS3 + Mesa Turnip on
ROCKNIX, the **whole stack fair game**: kernel, driver, emulator, middleware, OS image
(`dossiers/MissionBrief.md`). Honest-outcome clause: if a ceiling is truly unmovable, the
deliverable is a reasoned account of SM8250 class limits. Current verdict: **silicon is not
the wall** — every crash class mapped to fixable software; the true adversary is
non-determinism, treated as an instrument-calibration problem.

**THE KPI — Fable's Challenge (operator-set 2026-07-04):** locked **60 FPS / 16.7 ms at
NATIVE 720p (res 100)**. Judged by **`perfect_pct`** (ledger col 29), never fps averages.
Resolution-lowering = cheating. The crash-net is the **tax** that makes racing for the KPI
free — not the mission. Per-title lock targets (never compare across titles): GT HD =
locked-60, frametime [15.5, 18.0] ms; GT5P family = locked-30, [31.0, 36.0] ms.

**Roles — Garage-to-Car:** Claude = **Engineer** (preps, arms, analyzes, builds); operator
= **Driver** (at the rig: reboots on-device, runs install.sh, presses R3, races, gives the
feel verdict). *Verdict from the operator's screen, mechanism from the log.*

**Client orientation (2026-07-03):** the kit has real users. Expertise ships **as defaults**
(anti-lock, flicker fix, golden dials — default-ON, kill-switchable); user surfaces get
plain language and fail-soft behavior ("it still plays"); jargon stays in dossiers and this
manual's A/B sections.

### 2.2 The machine and the owned stack

| Layer | What runs | Fork repo | Deploy key (etk.conf → install.sh) |
|---|---|---|---|
| Hardware | Retroid Pocket Flip 2 — SM8250: 4×A55 @1.80 + 3×A77 @2.42 + prime A77 @2.84, Adreno 650, 8 GB | — | — |
| OS | ROCKNIX **official 20260801** (read-only root, BusyBox, sway/foot, InputPlumber DS5 pad; kernel 7.1.2) | — | — |
| Kernel | **rocknix-gtk -0.3** (7.1.2 rebase) — `msm.context_keepalive` KGSL-parity + q6afe audio-probe-race fix | mercurious/rocknix-gtk | `KERNEL_IMAGE`/`KERNEL_CONTEXT_KEEPALIVE`/`KERNEL_DEPLOY_MODE` → STEP 6.4 |
| Driver | Mesa Turnip **gtk** — `TU_ETK_QUERY_SURVIVE` default-on | mercurious/etk-turnip-gtk | `TURNIP_SO` → STEP 6.5 |
| Emulator | **RPCS3 GTK Edition** — anti-lock nets + #11912 fix default-on | mercurious/etk-rpcs3-gtk | `RPCS3_APPIMAGE` (+`RPCS3_ENV_FLAGS`) → STEP 6.55/6.56 |
| Middleware | The ETK itself (Sentry, daemons, Pitstop, vault, telemetry) | mercurious/etk | `./install.sh` |

- The OS supplies substrate only; ETK **bind-mounts its own emulator and driver** over the
  stock squashfs binaries at boot (`etk-rpcs3.service`, `etk-turnip.service`) and boots its
  own kernel from a separate grub entry (`/flash/KERNEL.gtktest`; pristine
  `/flash/KERNEL.etk-stock` fallback always present). Stock is one grub pick or one
  `"stock"` knob away.
- **Doctrine: never build a distro's package inside the distro** — warm cross-build
  containers off-rig (§A.1). Every fork feature is a **runtime flag** (default-off, or
  default-on with `=0` kill-switch) so A/B needs no rebuild.
- Primary title: GT5P Spec III `NPEA00050`; actives: GT HD `NPEA90002`, GT5P US
  `NPUA80075`, + vaulted IDs. **Games arrive as `.pkg` or `.iso` — format is a first-class
  variable** (§B.3); validate fixes on both.

### 2.3 The ecosystem (how the pieces interlock)

**The Sentry** (`01-etk-sentry.sh`, generated by install.sh STEP 6, `etk.service`, 2 s
tick) is the state machine everything hangs off: seeds SHM at boot, pre-links the shader
vault, re-injects Pitstop into the boot-volatile Tools menu (**the active tripwire** — ROCKNIX
wipes `/storage/.config/modules/` asynchronously during boot, often *after* systemd units
have run, so persistence there is a polling re-inject, never a one-shot deploy), detects
ignition, resolves the game ID and **commits it to SHM before spawning workers** (race-proof
identity; 4 s settle after RUNNING), atomically resets `vault_new.txt` at ignition, `pkill`s
the workers on return to IDLE, and on shutdown fires the postmortem. At boot it synthesizes
**PANIC** ledger rows from an orphaned `session_anchor.txt` breadcrumb — kernel panics are
accounted for even though nothing survived them.

| Component | Role (state model) | Key I/O |
|---|---|---|
| `bin/vault_d.sh` | Shader accountant (spawned at ignition) | `vault_count`, `vault_new.txt`, `vault_size.txt` (2 s, atomic) |
| `bin/thermal_d.sh` | Thermal governor v14: RACE→PIT at 92 °C, auto-recover at 80 °C, no reboot; re-asserts GRID masks | `etk_mode.txt`, `thermal_stat`, `telemetry.log` |
| `bin/mango_bridge.sh` | The DDU: sole writer of `live_stat.txt`; GINSTR gauges + ANTI-LOCK gauge | MangoHud CSV in `$SHM/mangolog/`, dmesg survive counter |
| `bin/input_d.py` | The Shifter: pass-through evdev chords, **in-game only** (the Sentry spawns it only in RUNNING; ES owns the pad in menus), Sentry-watchdogged; **nothing may break the R3 path** (all side-file I/O fail-silent); never `EVIOCGRAB`s | chords → recovery.sh / hud_apply.sh / bog_profile.sh / CMD_QUEUE |
| `bin/recovery.sh` | Nuclear recovery (R3 chord + commander both call it): grim the frozen frame → kill emulator → flush SHM w/ preserve-list | `r3_pressed.txt`, `crash_shot.txt` |
| `bin/session_postmortem.sh` | Ledger writer on RUNNING→IDLE (<2 s budget): status ladder, dmesg windowing, FPS/KPI cols, archives | `sessions.tsv` row + `mango_logs/ audio_logs/ perf_logs/` |
| `bin/blackbox_d.py` | Panic flight recorder: pstore harvest + `/dev/kmsg` tail (1 s fsync, 8 MB × 12) | `etk_telemetry/blackbox/` |
| `bin/bog_profile.sh` | Chord-triggered perf sampler (§2.4) | `perf_samples/bog_<epoch>.*` |
| `bin/grid_apply.sh` | big.LITTLE affinity rungs off/A/B/C (§2.4) | `grid_mark` + `grid_marks.log` |
| `bin/etk_pitstop.py` | The native app (§A.3) | — |
| `scripts/commander.sh` | Remote pit-wall TUI (manual overrides, diagnostics): split-pane — top live telemetry, bottom raw ANSI-free forensic text for copy-paste into analysis | `etk_cmd_queue` |
| `scripts/probe.sh` | Crash-report dump; uses `strings` on anything from /storage or SHM (binary-flood shield) | — |
| `scripts/env.sh` | THE single source of env truth — all scripts `source` it, none redefine; exports absolute or `:-default` only (the env-bomb law: a self-appending PYTHONPATH re-sourced per tick hit E2BIG at ~1h45m, 2026-07-03) | sources `etk.conf`, profile `SM8250.sh` |

**Chord map** (input_d, in-game only; R1 = the mid-race modifier): `L1+R3` = RECOVERY
PANIC · `R1+L3` = HUD punchbox cycle · `L1` = screenshot (3-state mode) · `R1+DPAD-Down` =
bog profiler · `R1+DPAD-Up` = RSX frame capture · `SELECT+DPAD-Right/Left/Up` = vault
backup / HUD toggle / screenshot fallback.

**SHM map** (`/dev/shm/etk_shm/`, wiped every boot; daemons rebuild it instantly on start;
**never persist or "unify" these paths** — it is the IPC backbone): `active_id.txt`,
`etk_mode.txt`, `live_stat.txt`, `vault_count`, `vault_new.txt`, `vault_size.txt`,
`thermal_stat`, `etk_cmd_queue`, `session_start.txt`, `r3_pressed.txt`, `crash_shot.txt`,
`bog_sample`, `grid_mark`, `mangolog/`, `etk_install_lock`, `install_queue/`. Fork-side
channels: `/dev/shm/rpcs3_audio_stat`, `rpcs3_audio_log`, `rpcs3_perf_stat`,
`rpcs3_perf_log`.

**The attribution chain (the keystone):** Pitstop DRIVER tab APPLY → writes
`profile.d/097-etk-turnip-dials` AND `active_tune.txt` **atomically** → postmortem stamps
ledger `tune_tag` (col 15); POWER tab APPLY → `/storage/etk-power/profile` → `pwr` (col
22); config edits → `config_changes.tsv`. Every session is self-labeled with the exact tune
that produced it — this is what makes the ledger an experiment log instead of a diary.

**The ledger** — `etk_telemetry/sessions.tsv`, 31 append-only-trailing columns (**never
insert mid-row** — readers index positionally, so an inserted column silently shears every
prior row): `epoch duration_s build game_id status peak_load peak_ram_mb peak_temp avg_temp
crash_sig fence_at_crash shaders_harvested drain_pct thermal_overrides tune_tag crash_shot
fps_med fps_1low ft_p99_ms res_scale gpu_mhz pwr ft_jitter_ms gpu_fault_status
gpu_fault_fence_hex aud snd lock_pct perfect_pct rescues perf`. **`epoch` (col 1) is
stamped at session END** — a row covers `[epoch − duration_s, epoch]`; join samples/events
by that interval (bog metas carry `session_start=`), never by treating `epoch` as ignition
(the 2026-07-22 "identity misfire" dissolved under this rule). Status ladder: `PANIC` >
`RECOVERY:*` > `SURVIVED:*` (keepalive absorbed every hang — counts as clean) > `CLEAN` >
`ABORTED` (<60 s, excluded from all denominators). Host mirror under `state/etk_telemetry/`.

### 2.4 The mechanism catalog (what has been built, with its evidence)

- **The full-stack GTK Anti-Lock (the flagship).** A GPU wedge on stock ROCKNIX is a
  **dmesg-only soft-freeze** (a6xx fault → hangcheck → `rsx::thread` parks; no core, no
  fatal, state S). Ledger proof of the gap: 81% of 318 RECOVERY:Adreno rows had a kernel
  fault that never surfaced as `VK_ERROR_DEVICE_LOST`; all 318 needed a human R3. Four
  wedge classes, one net per layer: **#1** query park `00C5xxxx` → `TU_ETK_QUERY_SURVIVE`
  (driver); **#2** fence park `00E5xxxx` (dominant) → kernel `msm.context_keepalive` +
  `GTK_FENCE_FORCE_SIGNAL`; **#3** FIFO-desync rsx-death → `GTK_FIFO_RESYNC` (a watchdog
  can't ride the thread it guards); **#4** post-rescue guest-state deadlock → 
  `GTK_RSX_WATCHDOG` (fingerprint: mango CSV mtime freezes while live_stat stays fresh).
  All default-ON since v0.7.0 with `=0` kill-switches. Observability: `mango_bridge`
  counts dmesg `context_keepalive: surviving hang` deltas → HUD ANTI-LOCK gauge; postmortem
  writes `rescues` (col 30) and reclassifies `SURVIVED:*`. Evidence: N≥9 live bosses over
  two days; a 32-min GT5P run absorbed 13 rescues and finished; first fully-autonomous
  recovery 2026-07-05 (row 1783262636).
- **Attributable tuning: tune_tag + ETK Dyno.** `tools/etk_dyno.py` groups warm race
  sessions by (tune_tag, res, clk, pwr), scores by `perfect_pct`, with discipline in code:
  bake runs excluded, ABORTED dropped, **no verdicts at N<3**, medians never means. Born
  from the near-crowning of `TU_DEBUG=sysmem`; delivered the sddepth verdict (4–5×
  wedge-rate cut at equal fps, 2026-07-10) and the dial-era result (median duration +67%).
- **The shader vault + homologation.** Per-game vault (`vault/$CHIPSET/$ID/shaders`)
  live-symlinked over Mesa's cache with `ln -sfn` (plain `-sf` once built a self-loop that
  hung rsync at the kernel's 40-link limit); **never rsync the live cache — the symlink is
  the mechanism** (install.sh works around it). Staleness keys on the **Mesa VERSION-string
  fingerprint** (`vault/.last_mesa.hash`); `vault_sweep.sh` prunes dead-epoch files (first
  run reclaimed 174,954 files / 1.2 GB). Stage III lifts Mesa's silent 1 GB LRU cap to 10 G.
  Sharing gates on a **homologation hash** (sha256 of the live driver's first 64 KB);
  bundles are pure data.
- **The #11912 road-flicker fix (`GTK_REMAP0_ONE`).** 5-year-open upstream RPCS3 bug,
  root-caused headlessly from 2023 RenderDoc captures: dim frames park the shadow sampler
  on a 1×1 texture with zeroed `TEXTURE_CONTROL1` → remap force-ZERO → black roads; the
  fork decodes remap 0x00 as ONE×4. Field-validated on four platforms. Operator's PS3
  hwtest: the console renders black too — so this is a **patch-not-fix**, default-ON; root
  cause reopened at the constants/shader layer. Upstream lane: psl1ght hardware test.
- **Kernel-root fixes over workarounds (the audio precedent).** The SM8250 ~1-in-4
  silent-boot coin flip (q6afe clock-vote error never woke the waiter; every LPASS device
  parked in `devices_deferred` forever) was bridged by a userspace watchdog, then **fixed
  in the kernel** (Patch #2 `q6afe-vote-probe-race`: in-place retry, 250 ms/15 s bound) —
  and the watchdog was actively **retired** (STEP 6.57 tears it down). The template:
  workaround → root fix → tear the workaround down. Emergency revive on a pre-fix kernel:
  `echo 3370000.codec > /sys/bus/platform/drivers_probe` (validated live, N=1).
- **The bog profiler.** `R1+DPAD-Down` mid-race → `perf record -F 199 -g` for
  `BOG_PROFILE_SECS` (default 30), **symbolized AT CAPTURE TIME** — the AppImage dwarfs
  mount dies with the session, so a later `perf script` resolves nothing. Meta sidecar
  self-labels with `fps_end`. One 33 K-sample capture (2026-07-10) named two pure-waste
  thieves: pad-poll at 1 kHz ≈ 8% of all cycles (→ Pad Poll Interval dial, golden 4000 µs)
  and the ZCULL query poll ≈ 5% (→ Relaxed ZCULL fields) — ~a full core reclaimed.
- **GRID mode (big.LITTLE affinity).** POWER-tab rung → `grid_apply.sh` pins by
  comm-prefix (rsx→prime, PPU/SPU→golds, cellAudio→silvers); thermal_d re-asserts every
  60 s; attribution rides `pwr=race+gB`. Honest split verdict: on GT5P pack it cut
  contention but moved fps not at all (banked: "contention is INSIDE RPCS3"); on GT HD
  Eiger it relieved the serialized rsx chain (min-fps 25→39). Standing: GRID-B for GT HD,
  off for GT5P.
- **The Panic Black Box.** `/dev/kmsg` flight recorder fsync'd ≤1 s behind, `panic=10` on
  all grub lines, Sentry orphan-detection synthesizes the PANIC row. Diagnosis = lead-up
  kmsg + PANIC rows + deterministic narrowing, not backtraces (ramoops falsified — §F).
- **The DDU HUD (G-INSTR).** MangoHud custom-text as a racing Driver Data Unit; sole
  writer `mango_bridge.sh`, atomic tmp+mv, raw string (no config keys inside
  `live_stat.txt`). **Format strict-lock:** dense, space-trimmed, punctuation-as-gauge —
  no decorative bloat. Mode-switched bodies (`ETK_HUD_MODE`): BASIC =
  `TEMP|LOAD|RAM%|shaders`; GINSTR = `TEMP|JITTER|SLIP|shaders` with animated fixed-width
  5-slot gauges (0.5 s refresh) + the persistent ANTI-LOCK gauge (idle `·×NN·` per-session
  rescue count; alert `·«!»·` ~8 s). **GLYPH LAW: Latin-1 ONLY** (`·`=B7 `×`=D7 `«`=AB
  `»`=BB) — anything beyond renders as tofu on the rig font (proven on-rig 2026-07-06).
  Companion instruments: `lock_pct`/`perfect_pct` KPI cols; fork perfstat channel → col 31
  (MangoHud-independent, closes the "HUD off = ledger blind" gap).
- **ps3lift / ps3probe / gdb_rig** (PRIVATE, `~/ps3lift/`, never pushed):
  question-scoped PS3 guest-code analysis; `ps3probe.py NPEA00050 --lens flip` decodes
  firmware-import call sites lift-free; `gdb_rig.sh` breaks on guest PPC addresses via
  RPCS3's gdb stub.

---

## A. BUILDING THE KIT (forging, deploying, UI, roadmap)

### A.1 Forging the binaries (the build fleet + the forge)

#### WHERE A CHANGE MUST LAND — read this FIRST (2026-08-11, after a patch landed one layer too low)

Every lane builds from a **canonical committed input**, not from whatever tree you just
edited — each lane's fingerprint hashes exactly that input, so a change in the wrong layer
produces `SKIP … fresh`, *correctly*. (The 2026-08-11 case: an RPCS3 net applied to the
node's `~/rpcs3` working tree; the lane does `reset --hard` + re-applies the committed
patch, so the edit was invisible AND would have been wiped.)

| Lane | Canonical input (what the build actually uses) | Land your change HERE | Edits that are INVISIBLE/wiped |
|---|---|---|---|
| `rpcs3` | pinned base (`FORGE_RPCS3_BASE`) + **newest `patches/*-dev.patch` in the Air's `~/etk-rpcs3-gtk` checkout** (lane resets hard, then applies it) | a new **cumulative** `*-dev.patch` (version-only name, §C.4), committed+pushed to the fork; generate as `git diff` from a node tree at base+patches, reverse-check it | node `~/rpcs3` working tree |
| `turnip` | prepared `/work/mesa-<V>` trees in the node's container (base tag + fork patches as commits; `tu_etk_gears.h` is the generation tripwire) | fork patches via `prepare-fork-branch` into each `mesa-<V>` tree; bump `FORGE_TURNIP_VERS`/`GTKVER` | ad-hoc container edits outside the prepared trees |
| `kernel` | node `~/rocknix-gtk` checkout at its tip (`build_712.sh`, KCC=gcc-15 enforced) | commit+push to rocknix-gtk, pull on node | — (but never re-run over a validated kernel) |
| `chiaki`/`wlmirror` | **pinned refs** in the fork repos (recipes live in the forks; cloud builds published commits only) | fork commit, then bump the pin the stager passes | unpushed fork commits |
| `image` | `config/gtk_stack.json` pins + the node's `~/etk` checkout at `APP_VERSION`+`HEAD` + `TURNIP_CATALOG` | the manifest + the five-places catalog table (§C.4); push etk `main` and pull the node | a stale node kit (now refused, not silently baked) |

**The test before any forge handoff:** name the lane's canonical input and confirm your
change is IN it (committed, pushed, pulled where the lane reads it). If the answer involves
the phrase "working tree", it isn't landed. A `SKIP … fresh` after a change you believe you
made is this table telling you it never saw the change.

#### The fleet

**Two build hosts, one rule: the rig is only ever reached from the LAN-local Mac.** The
cloud node builds; the Mac stages and relays; the rig never faces the internet.

| Node | Spec | Owns | Reached by |
|---|---|---|---|
| **The Air** (M1 MacBook Air, 8 GB, fanless) | colima aarch64 VM | staging, `install.sh` handoff, **all rig contact**, quick warm rebuilds | local |
| **etk-cloud** (Oracle Always-Free Ampere A1) | 4 cores / 23 GB / 145 GB, Ubuntu 24.04 aarch64, native docker | long/heavy builds — toolchain images, clean rebuilds, sanitizer builds | `ssh etk-cloud` (alias in `~/.ssh/config`; IP is **ephemeral** — re-read from console after stop/start) |

Why it exists (2026-08-05): the LLVM-22 toolchain image took ~30 h across two failed
attempts on the Air (ENOSPC, memory overload) and built unattended on etk-cloud first try.
Native docker also removes the colima-virtiofs trap class. All six lanes validated
end-to-end 2026-08-05 (chiaki + wl-mirror byte-identical; kernel exact shipped size, same
237 modules; Turnip within 296 B — **Mesa builds are not byte-reproducible across hosts:
don't split an A/B's arms across boxes**). The audit's real finding: four lanes' recipes
had lived only on the laptop (one `docker rm` from gone) — all now in git and installed by
provisioning scripts. Lanes: **rpcs3** (`etk-rpcs3-jammy-aarch64:llvm22`, `~/rpcs3`) ·
**turnip** (`~/etk-turnip-gtk` + `turnip-rocknix` container) · **kernel** (`~/rocknix-gtk`
+ `rocknix-gtk-kernel-sid`) · **image** (`etk-imgtool`, unprivileged mtools/mke2fs; base
img fetched sha-verified on the node) · **chiaki** / **wl-mirror** (the fork model:
recipes live IN the forks, base pinned by digest, cloud builds published commits only —
byte-identical parity proven) · **aPS3e APK** (`aps3e-ndk`, recipe in the fork's
`cloud-forge` branch; the Mac's `aps3e-build.sparseimage` is RETIRED — a second compiler
in the fleet; **trap:** aPS3e work spans branches with no canonical integration branch —
`git cherry HEAD origin/<branch>` before building; 2026-08-07 shipped without the flicker
fix off a wrong tip, caught only by the operator at the Daytona start line).

**TOOLCHAIN PARITY IS NOT AUTOMATIC — pin it.** On 2026-08-05 the Air and etk-cloud held
different images behind the same `ubuntu:24.04` tag, and sid's default gcc had moved to 16
while every validated kernel was gcc-15 (**gcc-14 and gcc-16 kernels black-screen
pre-userspace; failure is silent** — clean build, clean verify) — `build_stock.sh`
defaults `KCC=gcc-15`. Chiaki pins its base by digest and gates on the NEEDED manifest (an
ffmpeg-7 noble emits an undeployable `libavcodec.so.61`).

**Artifact flow:** build on etk-cloud → stream to the Air → Air stages per §A.2 → operator
runs `install.sh`. Only what a build needs goes up; game dumps and rig-derived assets stay
home.

#### The forge (`forge.sh`) — the conductor

**⚠️ THE OPERATOR RUNS IT. ALWAYS** (the "mint" threshold, §1.1; `--dry-run` opens ssh in
preflight and is not a loophole; its header "NEVER contacts the rig / NEVER publishes"
describes its contract toward the rig, not its safety for Claude — misread exactly that way
on 2026-08-10). Claude prepares: stage inputs, set `FORGE_*` knobs in `etk.conf`, reconcile
pins with `config/gtk_stack.json`, run host-side gates, write the handoff with the runnable
command. Never edit forge.sh mid-run.

A conductor, not a second install.sh: lane logic lives in the fork repos and
`tools/forge/lane_*.sh`; forge sequences, polls, verifies, stages. Heavy builds run
DETACHED on the node (`setsid nohup` + rc marker) — a dropped ssh can't kill a build, and
re-running **reattaches** rather than restarts.

| Lane | Builds | ~Time | Its own gates |
|---|---|---|---|
| `rpcs3` | the emulator AppImage | 25 m | `rpcs3_perf_stat` marker + VERIFY (loads without system ffmpeg) |
| `turnip` | one `.so` per `MESA_VER` | 5 m ea | unstripped + `ETK-GTK` version-string |
| `kernel` | the GTK kernel | 15 m | config-drift diff SURFACED; **cold-boot gated** (rocknix-gtk BUILDING.md §7) — a cloud kernel is unvalidated until the operator boots it |
| `chiaki` / `wlmirror` | rig-native helpers | 2 m | delegate to the proven stagers; byte-identical rebuilds must not churn `.buildinfo` |
| `image` | the flashable SD card | 40 m | **LAST**; input shas + node-kit version + labels + baked-kernel hash |

Use cases: middleware-only release (the common 0.8.x case) = `./forge.sh image` · driver
A/B = set `FORGE_TURNIP_VERS`, `./forge.sh turnip` (the image lane reads `gtk_stack.json`,
not this knob, so an experiment can't ship by accident) · after a failed lane just re-run
(fresh lanes skip; a `WORK` row means reattach) · check state with `./forge.sh --status`
(no ssh) · node down = `--local` colima fallback · **never re-run the kernel lane over a
cold-boot-validated kernel** — it would replace it with a same-named, different-sha,
unvalidated binary.

**Freshness is a fingerprint, and it is only as good as what it hashes.** A lane cannot
notice an input its fingerprint does not include. The four image-lane defects (all
2026-08-10, all the same shape — *a name or a path looked right and nothing hashed the
content*): (1) node-side artifact drift → inputs now sha-verified against `gtk_stack.json`;
(2) a verify that printed OK without running (`strings` absent + `set -e` only sees a
pipeline's last element) → the baked kernel is read back out of the FAT partition and
**hashed**; (3) the middleware travelled by git ungated → a 0.8.5 cut etched a 0.8.4 card;
the lane now asserts node `APP_VERSION`+`HEAD` match the cut; (4) the fingerprint hid the
repair → corrective rebuild skipped as fresh; `kit=` term added. **The standing lesson:
verify what the card CONTAINS, not what the build was TOLD.**

Reading a run: `state/forge/status.tsv` (`lane state pct runid note`); logs in
`state/forge/logs/<runid>/`. **A run that finishes in seconds built nothing** — check for
`SKIP … fresh` before trusting a fast result.

#### The mint loop against etk-cloud — recoveries, not surprises (2026-08-27: the 0.9.0.1 mint took FIVE runs)

Every failure was minutes-recoverable once named; the cost was the ~25-min round-trips.
Walk this list BEFORE handing the operator `./forge.sh rpcs3`:

1. **Base bump? Fetch the node first.** The rpcs3 lane does NO fetch, and the node's
   remote is named `armsx3`, not origin — `git reset --hard <new-base>` dies on an
   unknown object. Handoff step 0: `ssh etk-cloud 'git -C ~/rpcs3 fetch armsx3'`.
2. **Preflight recognizes exactly two tree states** — clean, or incoming-patch-applied.
   The previous release's resting state (old base + old patch) is neither, so EVERY
   version bump fails `tree-has-UNKNOWN-changes` with the diff banked. Recovery:
   byte-compare the banked diff against the published previous patch in the fork's
   `patches/` (2026-08-27: matched exactly), then the operator clears with
   `ssh etk-cloud 'git -C ~/rpcs3 reset --hard'`. The same reset is needed whenever the
   PATCH REVISION changes between failed runs (a failed lane rests at base + the
   superseded revision); a re-run with an UNCHANGED patch needs none — that resting
   state is the one preflight recognizes.
3. **The lane bundles from the Air's fork checkout** — the patch, `package-appimage.sh`
   AND `verify-markers.sh`, from whatever branch is CHECKED OUT. Preflight asserts the
   packager but not the gate script (bit 2026-08-27: verify-markers.sh existed only on a
   stale side branch; the bundle went up one file short and the lane died at install).
   Before any mint: fork checkout on pushed `main`, both scripts present.
4. **An ARMSX3 base bump means a fresh Linux-build-fix commit series** — nobody
   desktop-builds ARMSX3 but us, and the rot surfaces in build-phase order: SCAN (a
   header that exists nowhere in their repo — their Android releases build from dirty
   trees), COMPILE (config members declared `#ifdef __ANDROID__`, referenced ungated by
   desktop TUs), LINK (a desktop stub whose signature drifted from its header). Three
   pre-mint static sweeps, each of which would have saved a run: resolve every quoted
   `#include` across new/changed desktop sources; list Android-gated config members and
   grep desktop TUs for ungated references; diff every `#else` stub signature against
   its header declaration. Know the sweeps' limit — they prove resolution and
   declaration, not semantics; **lld's undefined-symbol list is the only exhaustive
   check**, so a link failure names the complete remaining debt in one shot.
5. **release_sanity enforces the CORE cap on the host catalog** — it counts
   `emulators/*.AppImage` (non-recursive) and a fresh mint makes three. Retire the
   outgoing core into `emulators/retired/` (invisible to the gate AND to install.sh's
   staging loop; one `mv` reverses it) — **but NEVER retire the core the CERT pins
   name.** The certified default's auto-fetch exists only after a release publishes
   the asset; between a cert-pin bump and its release cut, the local catalog copy is
   the ONLY source. Retiring it orphans AUTO — 2026-08-27: the next install 404'd,
   deleted `rpcs3-sa.custom`, and the rig silently ran STOCK all evening (a "0.9.0.1
   Spec II regression" that dissolved once attributed). Closed the same day:
   release_sanity FAILs a cert pin with zero sources, install.sh keeps the staged
   custom through any staging failure, and STEP 6.553 prints the NEXT-BOOT BIND
   verdict — read it at every install; `loaded=stock` unrequested is the falsifier.

#### Build-loop traps (Stage IV garage learnings)

- **Iterate loop:** if the build dir survives, incremental
  `ninja -C build-rocknix -j4 src/freedreno/vulkan/libvulkan_freedreno.so` (~1–2 min vs
  full). **NEVER pipe ninja to `tail`** — it masks the failure exit (`set -e` doesn't catch
  a piped command) and the script then copies the STALE prior `.so` and reports "BUILT".
- Turnip 26.1.x source is **`.cc` (C++)**: a `BitmaskEnum` member rejects aggregate
  brace-init — copy-init from an existing instance and override fields. The
  `/work/mesa-<V>` tarball tree has **no `.git`** — keep manual `*.etk-stock` backups.
- `deploy_rocknix.sh` **bind-mounts STACK** across iterate-deploys — unstack to one clean
  bind (`while mount | grep -q " $TGT "; do umount "$TGT"; done`). The bind is
  RUNTIME-only; a cold boot reverts to the persistent driver.
- **Verify the live fork driver by SIZE or hash, never `vulkaninfo`** — patched and stock
  both report the same `driverInfo` string. `stat -c %s /usr/lib/libvulkan_freedreno.so`.

### A.2 Deploying to the rig (`install.sh` and persistence)

**Everything reaches the rig through `install.sh`** (idempotent repair/update/sync; ~2,200
lines, 8 TUI steps). The operator runs it (§1.1); Claude stages artifacts, sets `etk.conf`
knobs, writes the handoff, verifies afterward from read-only telemetry.

- **Step map (abridged):** wizard/pairing/live-session guard → 0 quiesce + Mesa
  fingerprint → 1 dirs → 2 vault PULL + Tier-B state backup + forensics offload → 3
  bin/scripts deploy → 4 vault PUSH → 5 Pitstop → 6 Sentry heredoc + `etk.service` → 6.4
  kernel (grub twins, snapshot, device guard) → 6.5 Turnip catalog (sha-pinned; never
  prunes the rig's `selected`) → 6.55 RPCS3 AppImage (sha-verified; free-space preflight)
  → 6.553 NEXT-BOOT BIND verdict → 6.56 env flags → 6.57 watchdog teardown → 6.6 power
  applier → 6.65 black box (+ grub-drift tripwire) → 6.7 DP-mirror → 6.8 Stage III →
  6.85 SD rebind (label-based v3) → 7 PADDOCK link.
- **The emulator lane's deploy surface is STEP 6.553 NEXT-BOOT BIND** — printed at
  every install, read from the rig AFTER the bind service restarts (truth, not
  prediction): what `rpcs3-sa` binds next boot, which certified build, wrapper
  presence, pin count, rig-copy sha vs the staged source. RED on any stock
  resolution the operator didn't ask for. The ledger self-announces too:
  `core=stock` when the bind is stock, `core=<token>?stale` when a session never
  passed the launch wrapper (2026-08-27: 17 stock sessions stamped `core=0.8.5` by a
  marker that had rotted five hours earlier; the `r?` stack field was the only
  tell). "Install validated" for this lane MEANS this block plus the first
  post-boot row — step exit codes lied once already (`RPCS3_OK loaded=stock`
  printed green during the nuke).
- **Persistence vectors — the ONLY things that survive a cold boot:**
  `/storage/.config/system.d/` units (enable by absolute path; **never** `/etc/systemd/`,
  never `mount -o remount,rw /` — read-only root rejects both) · `custom_scripts/` boot
  scripts · `profile.d/09x-etk-*` env injections. **The profile.d vector:** `start_rpcs3.sh`
  sources `/etc/profile` → `profile.d` at **every game launch**, so `export VAR=value` in
  `/storage/.config/profile.d/09x-etk-<name>` reaches RPCS3 (096 rpcs3-flags, 097
  turnip-dials, 098 stage3). ROCKNIX regenerates only its OWN profile.d entries and leaves
  foreign ones intact — so these persist like real config, **and because they live OUTSIDE
  `$ETK_ROOT`, `uninstall.sh` must delete them explicitly** or ETK keeps altering the
  driver after it's "uninstalled." `/storage/.config/autostart/` is for none of this: its
  scripts run **synchronously before the UI**, so a long-running script stalls boot — a
  supervisor belongs in its own system.d unit. (The old "autostart races MangoHud" claim
  was disproven 2026-06-01 — §F.) `modules/` is boot-VOLATILE (Sentry tripwire re-injects).
- **`install.sh` pushes `tools/` SELECTIVELY** — exactly `etk_drift.py`, `vault_sweep.sh`,
  `wl-mirror`. Any new rig-runtime dependency MUST be added to the push list: a one-off
  `scp` does not survive uninstall/reinstall (the Manage-Shaders trap — `vault_sweep.sh`
  had only ever been hand-pushed, and a plain reinstall cycle silently dropped it). When
  you find a rig daemon that isn't in the repo, flag it for the push list.
- **uninstall.sh** restores stock governors, removes every unit/script/profile.d entry and
  the full kernel deploy, preserves the vault by default (`--zap-vault` to remove).
- **Config = `etk.conf`** (gitignored; `etk.conf.example` is the template). Knobs:
  `RIG_SSH`, `ETK_BUILD_TYPE` (FULL/LITE/RAW — tier-aware: FULL→LITE kills HUD/thermal
  daemons), `VAULT_SYNC`, `TURNIP_SO`, `KERNEL_*`, `RPCS3_APPIMAGE` (empty=certified
  auto-fetch / `stock` / dev path), `RPCS3_ENV_FLAGS`, `DEFAULT_MODE`, `ETK_HUD_MODE`,
  `HUD_HEADER_HOLD_S`, `ETK_DP_MIRROR`, `BOG_PROFILE_SECS`, `PADDOCK_TOKEN`/`PADDOCK_REPO`,
  `FORGE_*`. Its comment block is the fork-build provenance changelog.
- **⚠️ THE OS-UPDATER TRAP (cost a frankenboot):** the ROCKNIX in-place updater writes the
  new kernel over **whatever file the running boot used** (`BOOT_IMAGE=`) — on a rig booted
  from `/flash/KERNEL.gtktest` the update clobbers the GTK build THERE while `/flash/KERNEL`
  keeps the old kernel, and it regenerates both grub twins (ETK entries stripped) AND
  grubenv (observed seeded to the wrong device). An old-kernel boot on the new system loads
  ZERO modules. After ANY OS update: verify `/flash` slot contents via
  `strings | grep "Linux version"`, restore `saved_entry`, re-run install.sh with a kernel
  built for the new module tree, and re-arm the black box (`panic=10` grub lines — STEP
  6.65 warns on drift). In-place updates only — never reflash. **20260901+:** the
  regenerated grub.cfg gains an ABL model auto-select (`fdtdump` → `set abl_dev=`) that
  runs AFTER `load_env` and **overrides `saved_entry`** — left stock in default mode it
  silently dissolves the GTK auto-boot AND the panic=10 crash-reboot return. STEP 6.4
  counters it (re-points the Flip-2 match at `etk-gtk-test`, default mode only; verdict
  field `abl=` in `KERNEL_OK`, RED when it didn't take; `tools/test_grub_abl.sh` is the
  harness, host + rig-BusyBox legs). The counter re-applies at every install; between an
  OS update and the next install the failure direction is SAFE (stock boots). **Two live
  findings from the 2026-08-28 migration harden this further:** (1) **grubenv is INERT
  on the internal 4Kn ESP under the new grub** (load_env AND save_env no-op; savedefault
  writes nothing) and fdtdump does not fire on this ABL — so the RELIABLE default-boot
  mechanism is the generator's own no-saved-entry else-branch, which STEP 6.4 rewrites
  to `set default=<etk-gtk-test|rpflip2>` by mode (grubenv seeding kept for FATs where
  env I/O works, e.g. SD cards; verdict field `fallback=`). (2) STEP 6.4 now converges
  from the **SYSTEM's baked canonical grub.cfg** (`/usr/share/bootloader/boot/grub/`,
  the exact artifact update.sh deploys) instead of editing the live file — every install
  heals hand edits and diagnostic states to stock-plus-ETK-deltas; with KERNEL_IMAGE
  empty and no ETK entries present, a stock-convergence pass (`KERNELCFG_OK
  base=system fallback=rpflip2`) does the same minus the entries, so a recovered rig is
  a replica of a real install, kit-owned. Related 4Kn law: any FAT image built for the
  internal ESP MUST be `mkfs.fat -S 4096` — a 512-sector FAT crashes U-Boot itself
  (Synchronous Abort loop; fastboot `flash ROCKNIX` is the proven recovery rung).
- **Windows:** the PowerShell port (`windows_installer/`) is ACTIVE, kept in lockstep —
  **not retiring** (re-affirmed 2026-08-07). Rig-side bodies are pulled VERBATIM from
  install.sh heredoc markers at runtime, so daemon logic can't drift; only the PS-native
  host side needs manual sync — `release_sanity.sh` gates the cert pins (they HAD drifted
  two releases). Port debt: STEPs 6.45 / 6.552 / 6.554.
- **Flashable image lane:** `os-install/build/build_gtk_image_v2.sh` bakes base ROCKNIX +
  the three artifacts into `ROCKNIX-GTK-SM8250.aarch64-<date>.img.gz`. Since 0.8.0 the
  shipped image is the **v4 hostless lane** — UNIQUE labels `ROCKNIX-GTK`/`GTKSTOR` (safe
  beside an internal ROCKNIX, NOT an install.sh target), hostless two-phase hook via
  `/flash/mount-storage.sh`, `.seed_config` staged. Recover a previous image's labels by
  decompressing it before rebuilding — do not trust prose about which labels ship (a
  stale "standard labels" line stood for two releases after it stopped being true).

### A.3 The UI layer (Pitstop, notifications, HUD, installs)

**The Pitstop app** (`bin/etk_pitstop.py`, ~6,300 lines, curses in a fullscreen `foot`
terminal, launched from the ROCKNIX Tools carousel; raw evdev pad input,
focus-independent; L1/R1 cycle tabs). The operator's interface to every subsystem — and
the reason no A/B session is ever mis-attributed.

| Tab | What it does | Writes / drives |
|---|---|---|
| **TELEMETRY** (default) | career anchor + scrollable ledger merged with config-change rows; detail card decodes `crash_sig` → plain-language diagnosis + SUGGESTED FIX + TRACTION CONTROL advice; KPI gauges; crash-frame preview via swayimg | reads `sessions.tsv`, `career/`, `crash_signatures.json` |
| **TUNING** | schematic per-game RPCS3 YAML editor (36 schema fields; **section-aware injector** that refuses cross-section corruption; atomic save + read-back verify) | `config_<ID>.yml`; one row per change → `config_changes.tsv` |
| **TOOLS** | Manage Shaders (over `vault_sweep.sh`) · background installs (below) · PKG install (uinput Enter-taps through RPCS3's GUI-only dialog; **waits for self-exit** — early kill truncates installs) · headless firmware install (`--installfw`, self-provisions dev_flash) · game uninstall (preserves saves + vault) · Trigger Calibration · screenshot mode | `etk_install_lock`, storage-coherence gate |
| **DRIVER** | BUILD selector (Turnip catalog; `selected` vs boot-stamped `loaded` ground truth; reboot-gated) + ROAD FEEL dial (Max Stability=`syncdraw` / Balanced=`sddepth` / Max Performance=none; falsified gears exiled to Advanced) | `/storage/turnip/selected`; `097-etk-turnip-dials` + `active_tune.txt` **atomically** → `tune_tag` |
| **POWER** | schema-driven gov/floor knobs (no OC — OPP cap 800 MHz), presets, **grid** rung | `/storage/etk-power/profile` → `pwr` col; live sysfs |
| **PADDOCK** | private-repo vault sync (homologation-gated; only tab that touches the network, deliberately last) | `paddock_sync.sh` |

**The Tools-menu architecture:** ROCKNIX builds its Tools carousel by scraping
`/storage/.config/modules/*.sh` (must be `chmod +x`), spawning each via `/usr/bin/foot
%ROM%`. High-DPI font sizing = nested breakout `foot -F -o font="monospace:size=XX"
<target>` (foot has `-F`, not `-f`). The modules dir is boot-volatile → Sentry tripwire.

**Sway laws (a tiling compositor — launching ANY window splits Pitstop):** the instant a
second window maps (RPCS3 install dialog, swayimg preview), sway tiles it and knocks foot
out of fullscreen. The proven pattern: (a) fullscreen the new window yourself via
`swaymsg '[app_id="X"] fullscreen enable'` **after it maps** (poll `get_tree`; the app's
own `--fullscreen` flag still tiles); (b) on close, **re-assert foot** the same way.
`swaymsg` needs `SWAYSOCK` derived from `$XDG_RUNTIME_DIR/sway-ipc.*.sock`; any Wayland
tool from a Sentry-spawned context also needs `XDG_RUNTIME_DIR=/var/run/0-runtime-dir` +
`WAYLAND_DISPLAY=wayland-1` set explicitly (`screenshot.sh` is the reference).

**Notification laws (mako):**
- mako renders **TEXT ONLY** on this build — the gdk-pixbuf loader `.so`s are stripped, so
  `app_icon`/`image-path`/`file://` all come up blank (diagnosed 2026-06-18; do not
  re-attempt — §F). To show an image, use `swayimg` (decodes PNG itself; launch with
  `--class=<app_id>`; the gamepad cannot close it — trap the next pad event and
  `pkill -x swayimg`, with a `timeout` backstop). Keep notification copy ASCII.
- Styling keys on app-name with **byte-exact strcmp** — a wrong name silently falls through
  to stock black. TWO ETK surfaces, aligned to EmulationStation's own: `app_name="ETK"` =
  top-center verdict popup; `app_name="ETK Progress"` = top-right card with a real progress
  bar, dismissed at job end. `tools/test_notify.py` pins senders to install.sh's criteria.
  Shell senders go through `bin/etk_notify.sh` (there is no `notify-send` on the rig).
- mako 1.10.0 DOES render a progress bar (the old belief was false): standard `value` hint,
  **int32 only** (`v`/`i` — a uint32 fails the whole Notify call), via `busctl --user call`
  (`dbus-send` can't send nested containers; `gdbus` is absent). Use
  `progress-color=over #RRGGBBAA` translucent; a replace that omits the hint clears the bar.
- **A bad mako config is a rig-wide outage:** one invalid option and the next boot mako
  exits, killing every notification ROCKNIX-wide. `max-visible` is illegal inside an
  `[app-name=…]` criteria; `sort`/`max-history` are global-only. install.sh backs up,
  reloads, rolls back on rejection. Two different anchors DO work on 1.10.0 (do not
  backport to older mako).
- **EmulationStation's HTTP API** (`127.0.0.1:1234`, IPv4 numeric address only): ETK uses
  exactly `GET /reloadgames` after install/uninstall — queued onto ES's UI thread, runs on
  the first ES frame after Pitstop exits. `POST /notify` is deliberately NOT used (ES
  destroys its SDL window while a Tools module is foreground); `/emukill` is dead on
  ROCKNIX.

**Input architecture:** Pitstop reads the pad via **raw evdev**, focus-independent — that
is how "press any button to dismiss the preview" works (trap, pkill, swallow the event).
`input_d` does NOT run here (in-game only — §2.3); the reading app handles its own pad. On
this pad the **D-pad is a HAT axis** (`ABS_HAT0X/Y`), not `EV_KEY` — "any button" logic
covers both and gates on PRESS (`val != 0`), since the release arrives immediately after.
Pitstop and input_d share `PAD_HINTS`/`PAD_EXCLUDE` — keep in sync. Pad matching is by
NAME, never node index. All schema/config writes are atomic with read-back verify;
unresolved game → inert TOOLS-only mode.

**Background installs (0.8.4) — an installer and a game are both RPCS3:** installs run out
of process (`bin/etk_install_worker.py` drains a SHM queue, calling Pitstop's own
`_run_install` — no second copy of the verdict-from-the-log contract; default-ON,
`ETK_BG_INSTALL=0`). The laws that make it safe:
- **Identify the emulator by `argv[0]`, never "any argument mentions rpcs3"** — the
  AppImage spawns a dwarfs FUSE helper whose argv carries the image's path; a
  whole-cmdline test read it as a game and made the worker kill its own install on the
  live rig (2026-08-06). This trap bites anywhere a match is made against a whole cmdline.
- **The discrimination law:** a headless installer's argv carries `--installpkg`/
  `--installfw` as **WHOLE argv TOKENS** (`grep -qxE`; a substring test misreads a ROM
  path containing the flag and silently costs the session its telemetry). The Sentry
  ignites only on a NON-installer RPCS3 (a race started during an install still gets its
  row); kills are **PID-scoped** (`pkill -f rpcs3-sa` would take the operator's game down).
- **A game launch outranks an install:** `start_rpcs3.sh` does `rm -rf` + `ln -sf` on the
  config dirs at every launch — the very tree an install writes into — so the worker polls,
  terminates its OWN emulator, requeues at head, toasts `INSTALL PAUSED`. Part-installed
  PKGs are re-runnable, which is why requeueing is safe.
- The queue is **volatile by design** (SHM); installs are operator-initiated and nothing
  resumes itself across a reboot. Retries cap at `MAX_YIELDS` so a false-positive fails
  visibly instead of thrashing. BusyBox `grep` floods the journal on `\-\-` escapes — write
  the pattern `'^(--installpkg|--installfw)$'`. Any change here goes through the harness
  first: `tools/test_install_queue.py` (11 fixture cases) holds sh and Python to the same
  answers.
- Both installers are headless and **log-first verdict**: PKG exits 143 on success.

**ISO first-class + onboarding (0.7.1/0.7.2 — the .pkg-bias closures):** `resolve_game_id`
falls back to games.yml path→serial then `Serial:` from the live RPCS3.log head (a bare
.iso cmdline has no serial). Pitstop's `_iso_onboard_sweep` (default-ON, `ETK_ISO_ONBOARD=0`;
idle-gated, fail-soft): generates a one-line `<stem>.m3u` per ISO (ES's ps3 extension list
is exactly `.ps3 .psn .m3u`); renames `[TAG]`→`(TAG)` (ROCKNIX's `get_setting` regex-escapes
`'()&` but NOT `[`/`]`, so bracketed ROM names silently kill every per-game setting incl.
MangoHud); upserts the MangoHud key against the .m3u filename; golden-seeds
`config_<serial>.yml`. **The golden-seed doctrine (operator, 2026-08-10): the seed is a
SAFE START, not a tune.** A setting earns a place only if it is (a) the emulator's default,
or (b) a generic win with a named mechanism (`MSAA: Disabled`; `Accurate ZCULL stats:
false` + `Minimum Scalable Dimension: 512`, from the bog profiler). A proven-but-
title-specific value is a **DIAL, not a default** (e.g. `Preferred SPU Threads` validated
at 3, but the template ships auto). Do not promote a GT-weighted session majority into the
template — ~86% of ledger weight is one series, and the seed exists for the titles that
are NOT it. Disc/ISO seeds additionally set `Strict Rendering Mode: true`. Seeds are
ledgered as GOLDEN SEED rows; existing configs never touched.

**HUD work** obeys the DDU strict-lock and glyph law (§2.4). The mode bodies, gauge
animations, and anti-lock gauge history live with the mechanism entry; the operative laws:
Latin-1 only, atomic tmp+mv, raw string, no decorative expansion, `mango_bridge.sh` is the
sole writer.

### A.4 Roadmap & community signals

**Shipped ladder (the method compounds):** v0.1 (05-27) Pitstop+Sentry+vault → v0.2 Stage
III harness → v0.3 Private Paddock + thermal v14 → v0.4 DRIVER tab + tune_tag → v0.5 GTK
Turnip public → v0.6 GTK RPCS3 default + #11912 fix + official-ROCKNIX certification →
v0.7.0 full stack owned, anti-lock default-ON → v0.7.1/0.7.2 perf instruments + ISO
first-class → v0.8.0 (07-22) productization (ISO first-class + delivery triangle + GTK
KERS) → v0.8.3 full-stack edition + osguard → **v0.8.4 Cloud Forge Edition (08-07): every
binary minted on etk-cloud.**

**Live frontier:** the pack front is **CPU/SPU-oversubscription** (peak_load 9.6 on 8
cores; the "GPU-bound everywhere" era is over); resolution lever DEAD in pack racing.
Ranked: access-violation tax fork design (10–12% of cycles in locked GT HD sections —
highest-value KPI lever) · ffs-v5 flip-status force-retire · GRID-B accumulation to N≥3 ·
lap-mapping with 15 s bog samples. Campaign state lives in memory + dossiers, not here.

**Upstream lanes (operator posts all upstream comments):** #11912 psl1ght hardware test
for kd-11 · ROCKNIX audio-race patch not yet reported · aPS3e PRs #122/#127 open · ROCKNIX
EFI fix landed (#2874) · candidates: InputPlumber malformed-udev-remove wedge, DP-sink
default volume, `get_setting []` regex bug.

**On hold / shelved (don't resurrect without an operator pivot):** rig-native installer ·
full hostless-image delivery default · Grid Start cardless installer · "claudomatic"
auto-tuning + pad-movie autonomy (record works; replay injection dead-ends on
InputPlumber's grab).

---

## B. USING THE KIT (debugging a game with it)

### B.1 Collecting telemetry (track-testing protocol)

**The session shape (the SSX campaign is the template):** arm the watch at IDLE → operator
launches and drives → discriminator ladder (one variable per run) → capture on the wedge →
postmortem/ledger verdict. The engineering doctrine, each rule bought with an incident:

| Rule | Incident that set it |
|---|---|
| **Arm first, confirm later** — the auto-catch goes live at idle BEFORE launch; verify while it runs | 2026-06-23: spotter armed 25 s after the race-start hang; frozen telemetry misread as healthy |
| **Fast arming = ONE consolidated ssh call**, pre-staged before the driver is ready | 2026-06-28: a dozen sequential round-trips ≈ 10 min; rig crashed before the watch was up |
| **Dials via the Pitstop DRIVER tab, never a hand-written profile.d** | `_driver_apply` writes the export AND `active_tune.txt` atomically — a hand-write desyncs `tune_tag` and poisons the A/B |
| **Witness-first: no tuning to feel until the feel has a number** | SPURS paradox: operator felt better while every instrument read worse |
| **Verify the LIVE process/artifact** — `/proc/PID/environ`, gdb on the pid, sha256 on staged files | two builds size-collided at exactly 17,064,536 B; an ENOSPC silently kept the old emulator |
| **Pre-commit kill criteria; falsify cleanly and bank it** | GRID on GT5P: mechanism confirmed, fps unmoved → "PROVISIONAL KILL", redirect already named |

**The GPU-wedge capture chain (the crown jewel):**
1. **Arm at IDLE, before launch** — `cat /sys/kernel/debug/dri/0/hangrd > cap.rd &`. The
   node blocks until the *next* wedge; a post-hoc cat captures 0 bytes. A dmesg-only watch
   can never capture the redump — arming is the capture.
2. **Watch dmesg** — the dominant crash class is dmesg-ONLY. `rocknix_spotter_loop.sh`
   runs four detectors: ADRENO (a6xx fault) · **ADRENO-NOFAULT** (hangcheck newer than
   mark with NO fault line — the SSX class, 2026-08-11: forward-progress collapse both the
   fault watch and the SILENT watch are blind to) · SILENT (live_stat staleness +
   `emu_alive()` cmdline-walk, never pgrep; a graceful exit reports `>>> GRACEFUL EXIT`,
   no stub) · NEW CORE / RPCS3 fatal (scans the log's BYTE DELTA since last tick — a fatal
   can land pre-buried under the syscall-stats flush; on a size DROP it re-baselines so a
   relaunch can't re-fire on the previous session's fatal).
3. **Finalize size-stable** — wait ~10 s of no growth, never a fixed timer (a 6 s kill
   truncated a capture to 211 MB undecodable; size-stable got 587 MB complete). Captures
   can still be node-truncated even when size-stable — `rd_repair.py` recovers them
   (synthesize `RD_CMDSTREAM_ADDR` from faultinfo ib1, sized to the FULL containing
   buffer — dmesg `ib1_size` is the *remaining* count).
4. **`.faultinfo` sidecar** — dmesg `fence/status/ib1/ib2` next to the capture; it aims
   the decode at the faulting draw. Write one alongside any manual capture or it is
   structurally-decodable only.
5. **Storm captures are CONCATENATED crashstates — slice before decoding.** On a
   keepalive-absorbed storm the armed cat appends a full crashstate per re-hang (SSX first
   catch: 95 hangchecks → 41 dumps → 5.26 GB). The FIRST dump is the original wedge: find
   the offset past the first ib1+ib2 `RD_CMDSTREAM_ADDR` pair and `head -c` a `.first.rd`
   ON THE RIG (118 MB travels; 5.26 GB doesn't). Verify with `rd_inspect.py`.
6. **Ledger join** — postmortem stamps `gpu_fault_status`/`gpu_fault_fence_hex` (cols
   24–25); `recovery.sh` binds the grim'd frozen frame as `crash_shot` (sway is a separate
   GPU client and still composites the last frame after kernel recovery).

**Warm-cache discipline for any driver A/B:** a new `.so` build-id invalidates the cache;
the vault recompiles at launch BEFORE the menu (no in-game stutter from this). Protocol:
launch once (cold recompile) → graceful-exit to bank the warm driver-matched cache →
relaunch warm and test (true warm-vs-warm).

**Audio sessions gate on card presence first** — a silent-boot session (pre-fix kernels,
§2.4) poisons audio data and masquerades as "audio broken." Detect: `/proc/asound/cards`
empty; RPCS3.log `DeviceID: "auto_null"`.

**Tool inventory:**

| Tool | Catches / does |
|---|---|
| `cockpit` skill (`.claude/skills/cockpit/`) | the live-rig instrument: SEE→READ→DECIDE→ACT; Spotter (read-only) / Engineer (guardrailed tuning) / Driver (opt-in pad injection); adb=Android, ssh=ROCKNIX; `preflight.sh` always first |
| `rocknix_spotter_loop.sh` | THE crash-watch loop (arm at idle; auto-break + capture + classify + self-repair). Use for any "watch until X" task |
| `contact_sheet.sh` | one tar-over-ssh pull → labeled montage of a run's screenshots |
| `grab_bog_sample.sh` | pulls newest bog perf samples + named-hotspot summary |
| `session_postmortem.sh` + `sessions.tsv` | the evidence spine; every claim scores against it |
| `etk_dyno.py` | knob A/B judge over the ledger (KPI-scored, N-disciplined) |
| `blackbox_d.py` + `arm_blackbox.sh` | panic lead-up recorder |
| `bog_profile.sh` | in-race CPU flame-graph, symbolized at capture |
| `etk_drift.py` | OS-migration drift detector (build_id-keyed profile of every ROCKNIX surface ETK touches) |
| `vault_sweep.sh` / `vault_doctor.sh` | Mesa-epoch shader pruning / vault chain diagnostic |
| `probe.sh` / `etk_probe.sh` | crash-report dump (binary-flood-shielded) / 1 Hz thermal-freq CSV |
| `rd_inspect.py` / `rd_repair.py` | redump triage & repair (also deployed on-rig for pre-pull slicing) |
| `cffdump` (etk-cloud `build-decode`) | redump decoder (§B.2); always `--once` |

### B.2 Analyzing telemetry (tooling and demystification)

**Ledger method:** headline = **duration + time-to-crash ceiling**, never raw crash-rate
(the row-unit drifted once — race-attempt vs whole sitting — and crash-rate compares
non-equivalent units); 5-day buckets against the noise floor; **medians/IQR** (a 4 h idle
row drags any mean); ABORTED and bake sessions excluded; every claim carries its N;
`SURVIVED:*` counts as clean. Companion analytics: `dossiers/etk_telemetry/_analyze.py` →
`ledger_timeseries.csv`, `feature_landings.csv`.

**The decode lane (redumps):** `rd_inspect.py` (truncation/section check) → `rd_repair.py`
→ `cffdump --once` **on etk-cloud** (per-tile default = 400 MB of noise; `-D N` for one
draw; `--dump-shaders`/`--bindless` at the faulting draw). Kernel **devcoredumps**
(`.devcd`, auto-expire in 5 min!) decode with `crashdec`, never cffdump (and vice versa).
cffdump lives in a tools-only Meson tree at `/work/mesa-<ver>/build-decode/` inside the
`turnip-rocknix` container — minted on demand, **a build on etk-cloud, so the operator
runs the mint** (§1.1); running the built tool on a capture is analysis (Claude-side,
fine). Match the `mesa-<ver>` tree to the ledger row's `build=` so the decode reads the
right register generation. Redump layout: `[u32 type][u32 size][bytes]`; `RD_GPUADDR=3`,
`RD_CMDSTREAM_ADDR=6`, `RD_BUFFER_CONTENTS=12`. Fault-code decode: `status` =
A6XX_RBBM_STATUS — `00C5*` = query park, `00E5*` = fence park; rptr<wptr = CP stalled with
work queued; zero SMMU faults = hang, not page fault.

**Config-dial semantics worth keeping straight:** `Time Stretching Threshold` is
BUFFER-FILL % (not fps) and is dead unless `Enable Time Stretching: true`; buffer size
absorbs jitter only — sustained production deficit is what time stretching exists for.

**Evidenced capture sets (what the method has decoded):** the 787B boss (armed-first +
size-stable → ONE depth-CCU-resolve wedge, lap-4/5 trigger, N=3) ·
`survive_00E51485_20260705` (first full-stack survive, race finished CLEAN) ·
`suzuka_wedge_1783305651` (wedge class #3 discovered live; the 58 MB devcd salvaged
seconds before expiry) · `defeat_1783367968` + `hslrev_wedge_1783369769` (class #4 found
twice in one evening, cross-title → ffs-v3 at N=2) · the env bomb (journalctl `Argument
list too long` → one-line fix onto the live wedge, Sentry resurrected mid-game,
`23df9d2`).

### B.3 The limits and skews of the data (confront them, every campaign)

- **The noise floor is huge:** GT5P clean-run range 77–2886 s. A single clean race — even
  a Gold Trophy — is variance, not a cure (bit us twice). **N≥3 before any crown**;
  contemporaneous control; saturated-vs-saturated.
- **Bake sessions lie:** a 7,423-shader run logged fps 30.8 that was really compile-stall
  cadence (real warm run: 20.0). Only warm runs (shd≈0) count for feel/fps.
- **Attribution outranks narrative:** verify `active_core`/`tune_tag` names the test
  BEFORE interpreting a run. An unattributed row is a diary entry, not evidence.
- **Attract-mode runs are invalid for crash-class studies** — the fault is
  live-race-path-specific (attract survived 1200 s+ where racing crashed in ~2 min).
- **Audio underruns are forensically invisible:** RPCS3 zero-fills silently (no log line
  at any level). Audible stutter leaves NO trace; objective measurement requires the
  fork-side counters (`aud=` col).
- **Observability bias (operator doctrine, 2026-06-12):** shader-storm pain filters which
  games stay in player rotation, so community evidence systematically under-represents
  exactly the titles ETK exists for. Absence of community reports on a shader-heavy-title
  bug is WEAK evidence of absence; weigh on-rig telemetry over compatibility lore.
- **The format axis hides bugs from fixers:** .pkg = DRM-spawn (EBOOT→EMAIN, TWO ppu/
  pipeline caches, scattered dev_hdd0 reads, `.rap`, `CELL_ESRCH` smell) vs .iso = direct
  boot (one cache, one 19 GB streaming read, decrypted image, `CELL_ENOTMOUNTED` smell).
  A teardown-save cache fix would have *appeared correct* on .iso and silently failed on
  .pkg. **Any emulator/cache/install fix is validated on BOTH models before being called
  done.** Untested path: an update-PKG over a disc-image base game.
- **Fix verdicts come from the operator's screen; mechanism from the log.** Rule out our
  own code before blaming hardware.

---

## C. RELEASING THE KIT (preparing to ship)

### C.1 Public-facing voice
Changelogs and UI copy are for real users: plain language, what-it-does-for-you, fail-soft
framing ("it still plays"). Expertise ships **as defaults** with kill-switches, not as
settings homework. Jargon and mechanism stay in this manual's A/B sections and the
dossiers. **Changelog contract:** `CHANGELOG.md` is canonical and gets the `[X.Y.Z]`
section; the release body EMBEDS it in a `<details>Full changelog</details>` block — v0.8.0
did only the release half and the repo file silently lost a release (backfilled at 0.8.1).

### C.2 Evidence with visual impact
Claims ship with their chart: `tools/chart_library.py` renders the ledger-derived
timeseries (feature-landing dates joined to per-day stability) and the README's dyno
section carries the N-disciplined verdicts. Every public number traces to a ledger query
someone could re-run; charts carry their N and their date window.

### C.3 The naming system (thematic metaphors)
Innovations ship under the racing metaphor, mechanism named beside it: **anti-lock**
(the GPU-wedge net stack), **KERS** (sync-economy cycle reclaim), **DDU** (the HUD),
**Pitstop / DRIVER / POWER / PADDOCK** (the app surfaces), **the vault** (shader bank),
**Fable's Challenge** (the KPI), **Garage-to-Car** (the roles). In A/B sections:
mechanisms before metaphors. In user-facing copy the metaphor leads — it is how expertise
reads as a feature instead of a warning label.

### C.4 Packaging consistently for user trust (the release cut)
Full procedure: `dossiers/ReleaseRunbook.md`. The order of operations:

1. **First, walk §0's surfaces on the ARTIFACT itself** — flash the card, open the DRIVER
   tab, confirm the catalog is all there and the boot string names this release. A host
   install re-fetches everything and will hide a crippled image.
2. **The gates:** `tools/release_sanity.sh` (law #8 filename gate: version-only artifact
   names, digits/dots only — `-audiofix0` shipped once in a published kernel; never again)
   + `test_installers.py` + `test_paddock.py` + `test_notify.py` + `test_install_queue.py`
   + asset shas + pseudonym/PII sweep.
3. **ALL FOUR assets ship on EVERY release even when unchanged** — `install.sh` fetches
   from `releases/latest/download`, and publishing moves `latest`, so a missing asset
   breaks every fresh install. `APP_VERSION` == the tag (load-bearing: self-update
   compares them).
4. **Publishing is the third bytes-to-atoms threshold (§1.1)** — the operator cuts the
   tag, creates the release, uploads the assets.

**Two catalogs, two opposite rules:** TURNIP is **CUMULATIVE** — each cut adds, nothing is
removed, every listed driver ships as a release asset (a dropped asset is unfetchable for
every fresh install), `CERTIFIED_BUILDS[0]` = certified default = the manifest's pin.
RPCS3 CORES are the opposite — **capped at TWO shipping builds, never published** (A/B
tooling, not a distribution channel); `rpcs3-EXP-*` probes are campaign-transient,
exempt from the cap and loudly NOTEd by the gate — retire them when the campaign ends. Changing the driver pair means changing **FIVE places** (they had
drifted for two releases): `install.sh CERTIFIED_BUILDS` · `install.sh driver_sha()` ·
`config/gtk_stack.json` · the image-build default · the PowerShell `$certified` block
(**generate it, never sed it** — a hand-edit leaves the old sha attached and fails every
Windows fetch). `release_sanity.sh` gates all five. **The card bakes the WHOLE catalog**,
and any catalog change requires an image rebuild.

---

## F. FALSIFIED & RETIRED (never re-propose; the disproof is the asset)

ramoops/RAM-backed pstore on SM8250 (DDR scrambling re-keys per reset; efi_pstore persists
nothing) · the autostart/MangoHud race (disproven 2026-06-01 — they never share a time
window; re-run with `tools/probe_autostart_race.sh`) · the five boss-avoidance angles
(rtalign, CCU cache-cap, dsbypass/dsany, Patch #1 WFI, Patch #2 discriminator) ·
SPURS-ladder for audio · Thread Scheduler config on ARM (source-proven no-op) ·
noconstcheck · max_map_count · EVIOCSABS trigger cal · GRID as the GT5P pack fix ·
MangoHud as the teardown murderer · "Android survives" as a solution (diagnostic only) ·
non-Latin-1 HUD glyphs · mako image rendering · attract-mode trials for crash classes ·
SRM-on-disc for the ISO stutter · "mako has no progress widget" (it does — §A.3) · "no
headless install" (`--no-gui` ≠ `--headless`) · zfunc theory for road flicker (retracted;
console renders black too) · FIFO fetch-accuracy/reordering combos for RR7 (2026-08-21:
every rung worse than stock Atomic, menus unreachable — the fault is not FIFO ordering;
the PPU-decoder interpreter A/B is the indicated probe when RR7's turn comes).

---

## Q. QUICK REFERENCE

- **Repos:** `origin`=github.com/mercurious/etk · sisters: rocknix-gtk / etk-turnip-gtk /
  etk-rpcs3-gtk · aPS3e fork: mercurious/aps3e · `garage`=private, never to origin ·
  `dossiers/`=private clone, citations dangle by design.
- **Rig paths:** `ETK_ROOT=/storage/games-internal/roms/etk` · vault symlink
  `/storage/.cache/mesa_shader_cache` · RPCS3 configs
  `/storage/roms/bios/rpcs3/custom_configs/config_<ID>.yml` · saves
  `.../dev_hdd0/home/00000001/savedata/` (not the global `dev_hdd0/savedata` — that
  anchors empty vmc volumes) · `/storage/roms` == `/storage/games-internal/roms` on this
  single-card device (`games-external` is the second slot on two-slot devices).
- **The live process is `AppRun.wrapped`** (`rpcs3-sa` is a launcher stub). ⚠️ `pgrep -f`
  self-matches its own shell; `pgrep -x AppRun.wrapped` observed EMPTY for whole live
  runs — gate on `live_stat` freshness or cmdline-walk `/proc` and read each `environ`.
  **RPCS3 rewrites its config on exit** — edit `input_configs/`/`custom_configs/` only
  with RPCS3 closed.
- **Host dirs:** `state/` = Tier-B rig mirror · `vault/` = host shader vault ·
  `manual_forensics/` = wedge capture sets · build trees `~/rocknix-gtk`,
  `~/rpcs3-linux-build`, colima containers · `drivers/` gitignored (`drivers/android/` is
  aPS3e-only bionic — never sweeps).
- **Golden diagnostics:** `journalctl -u etk.service` (Sentry) · `dmesg | grep -E
  'a6xx|hangcheck|context_keepalive'` (wedges/rescues) · `tail sessions.tsv` (a wedged row
  is written BY R3/postmortem — don't look pre-recovery) · `/proc/PID/environ` (dial
  ground truth) · `stat -c %s /usr/lib/libvulkan_freedreno.so` (live driver — vulkaninfo
  lies).
- **BusyBox laws (POSIX only):** no `--long-options`, `grep -P`, `find -printf`,
  `find -regex`/complex `-exec`, `du -h` (use `-k`/`-m`), `stat --format` (use
  `readlink`) · **`cp -rn` is a silent no-op AND `tar -xkf` ABORTS at the first existing
  file** (both cost live data-loss bugs; content-addressed merge = plain `tar -xf` +
  verify the count) · `awk` for float math (`sh` can't do decimals; `bc` may be absent) ·
  foot has `-F` not `-f`.
- **Forge:** `./forge.sh [lanes] [--status|--force|--local|-v]` — OPERATOR RUNS IT (§1.1).
  Common case `./forge.sh image`. A run that finishes in seconds built nothing. Base-bump
  mints: node fetch first (`armsx3` remote) + the preflight UNKNOWN-changes reset — walk
  §A.1's mint-loop list before the handoff.

---

*v1.0, 2026-08-11: restructured onto the A/B/C modality spine and absorbed the retired
`AI_MANIFEST.md` (per-law dispositions in the synthesis commit message). §0 is the charter;
verify manual-only claims against code before building on them.*
