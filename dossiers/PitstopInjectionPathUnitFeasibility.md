# PITSTOP INJECTION — REPLACE THE POLLING TRIPWIRE WITH A systemd `.path` UNIT — FEASIBILITY DOSSIER
**Status:** Speculative feasibility analysis. ToDo / future refactor. **NOT implemented.** Single short sprint when scheduled.
**Audience:** Claude Code (deep-dev implementer, future sessions) + operator (decision-maker)
**Provenance:** Exploratory session 2026-06-01 — operator asked "are we over-engineering vs. using Rocknix built-ins (autostart/syncthing)?" The same session sourced the ROCKNIX `next` branch directly and empirically disproved the autostart↔MangoHud "race" claim (see `tools/probe_autostart_race.sh` + `log/autostart_race_probe_*.txt`, and `AI_MANIFEST.md` law #4 as corrected). This dossier captures the *second* finding from that research: half of the Sentry's polling loop has a clean native replacement.
**Companion / related:**
- `AI_MANIFEST.md` §"DEFEATING VOLATILE DIRECTORIES (THE SENTRY TRIPWIRE)" — the mechanism this dossier modernizes.
- `project_headless_refactor.md` (memory) — Phase 10 untether work; this composes cleanly with it.

---

## §A. THE FINDING (one sentence)

The `etk_sentry` loop currently does **two unrelated jobs on the same 2-second tick**, and only one of them genuinely needs polling:

1. **Emulator ignition detection** — `pgrep -f "rpcs3-sa|AppRun.wrapped"` to drive the IDLE↔RUNNING state machine. **No native replacement exists** (confirmed below) — polling stays.
2. **Volatile-dir tripwire** — re-inject `etk_pitstop.sh` into `/storage/.config/modules/` after Rocknix wipes it. **A systemd `.path` (inotify) unit replaces this natively** — and ROCKNIX itself uses exactly this pattern elsewhere.

Splitting the tripwire out into a `.path` unit removes a busy-poll responsibility, makes file re-injection *event-driven and instant* (no up-to-2s gap where the Tools-menu entry is missing), and lets the Sentry's own `sleep` widen since it would only be watching emulator state.

This is **not a rewrite of the Sentry** and **not** a claim that the Sentry is over-engineered — its emulator-state half is load-bearing and irreplaceable. It is a targeted modernization of one responsibility.

---

## §B. WHY THE TRIPWIRE EXISTS (current behavior — do not regress)

Rocknix rebuilds `/storage/.config/modules/` on every boot via a **destructive** native sync. Confirmed in source:

- `packages/misc/modules/autostart/001-sync-modules` runs `rsync -a --delete /usr/config/modules/ /storage/.config/modules/` inside `rocknix-autostart.service`, before the UI.
- The source `/usr/config/modules/` is baked into the **read-only squashfs** SYSTEM image at build time (`packages/misc/modules/package.mk` does `cp -rf … ${INSTALL}/usr/config/modules`). **You cannot add ETK's module to the sync source.**
- Therefore anything ETK places in the volatile destination that is not in the read-only source is **deleted on next boot** (`--delete`). This is the "reboot boss" `AI_MANIFEST.md` documents.

Current ETK defense (the active tripwire, in the generated `01-etk-sentry.sh` loop):
```sh
# Inside the Sentry while-true (every ~2s):
if [ ! -f "/storage/.config/modules/etk_pitstop.sh" ]; then
    cp -f "$ETK_ROOT/config/etk_pitstop.sh" /storage/.config/modules/etk_pitstop.sh
    chmod +x /storage/.config/modules/etk_pitstop.sh
fi
```
It works. The cost is: (a) a re-check every tick forever, and (b) a worst-case ~2s window after a wipe where the Tools-menu entry is absent.

---

## §C. THE NATIVE REPLACEMENT (confirmed against ROCKNIX `next`)

### C.1 ROCKNIX's own precedent — `hdmi-hotplug.path`
`projects/ROCKNIX/packages/rocknix/system.d/hdmi-hotplug.path`:
```ini
[Path]
PathModified=/run/hdmi-status.last
[Install]
WantedBy=multi-user.target
```
paired with a `Type=oneshot` `hdmi-hotplug.service`. **This is the canonical Rocknix idiom: an inotify `.path` unit triggers a oneshot instead of a polling loop.** The platform clearly supports and blesses `.path` units.

### C.2 `/storage/.config/system.d/` accepts `.path` units
ROCKNIX recompiles systemd so `SYSTEM_CONFIG_UNIT_DIR = /storage/.config/system.d` (patch `packages/sysutils/systemd/patches/systemd-0001-move-etc-systemd-system-to-storage-.config-system.d.patch`). The in-tree `config/system.d/README` lists `.path` (and `.timer`, `.socket`, `.mount`) as valid unit types for that dir. ETK already deploys `etk.service` there — the `.path`/re-injector units land beside it with the same mechanism.

### C.3 The catch that shapes the design
A systemd `.path` unit with `PathExists=`/`PathModified=` fires when the watched path changes — **but at the instant of the boot-time `rsync --delete`, the parent dir is being rebuilt and the unit may not yet be (re)started.** So the design needs BOTH:
- a **oneshot at boot** (ordered `After=rocknix-autostart.service`) that asserts the file once after the destructive sync has run, AND
- a **`.path` unit** that re-asserts it on any later disappearance/modification (covers mid-session wipes, manual deletion, EmulationStation refreshes).

---

## §D. PROPOSED DESIGN (3 small units + a tiny re-injector)

Deployed by `install.sh` into `/storage/.config/system.d/`:

1. **`etk-pitstop-inject.service`** (`Type=oneshot`)
   - `ExecStart=/bin/sh -c 'cp -f $ETK_ROOT/config/etk_pitstop.sh /storage/.config/modules/etk_pitstop.sh && chmod +x …'`
   - `After=rocknix-autostart.service` (so it runs *after* the destructive `001-sync-modules`)
   - `WantedBy=rocknix.target`

2. **`etk-pitstop-inject.path`**
   - `[Path] PathExists=/storage/.config/modules/` + `PathModified=/storage/.config/modules/` (watch the dir; refire the oneshot when the entry goes missing)
   - The oneshot is idempotent (only copies if absent / differing), so spurious refires are cheap.
   - `WantedBy=rocknix.target`

3. **(unchanged) `etk.service`** — the Sentry. Its tripwire `if [ ! -f … ]` block is **removed**; its loop now watches only emulator state, and `sleep` can widen (e.g. 2s → 3–5s) since nothing time-sensitive rides on it anymore.

Net: file persistence becomes event-driven and effectively instant; the Sentry sheds one responsibility.

---

## §E. WHAT CANNOT BE REPLACED (scope discipline — don't try)

Emulator **start/stop** detection has **no** native hook. Confirmed by reading `projects/ROCKNIX/packages/rocknix/sources/scripts/runemu.sh` on `next`:
- `runemu.sh` is monolithic — a hardcoded `case ${EMULATOR}` dispatcher with inline pre/post setup. **No `hooks/*` drop-in dir, no pre-game/post-game extension point.**
- It emits no general "emulator started/stopped" signal or state file (only PortMaster's `mapper.txt ACTIVE_GAME`, which is Ports/Windows-only — not RPCS3).
- systemd cannot fire on "process X appeared" without supervising X itself, and RPCS3 is launched by EmulationStation→`runemu.sh`, not by an ETK unit.

⇒ The Sentry's `pgrep` loop is **not redundant** and **must stay**. (grep across the whole ROCKNIX tree for `pre-game|post-game|game-exit|post-launch`: zero matches.)

---

## §F. TRADEOFFS

| | Polling tripwire (today) | `.path` unit (proposed) |
|---|---|---|
| Latency to restore after wipe | up to one Sentry tick (~2s) | effectively instant (inotify) |
| Steady-state cost | a stat() every tick forever | zero until the dir changes |
| Failure surface | one process; if Sentry dies, tripwire dies with it | independent unit; survives a Sentry crash |
| Moving parts | 1 (inside Sentry) | 3 small units |
| Platform-idiomatic | no (hand-rolled) | yes (mirrors `hdmi-hotplug.path`) |
| Risk | known-good, shipped | new boot-ordering edge cases (§C.3) |

The only real cost is **more unit files** and the **boot-ordering subtlety** in §C.3. Mitigated by keeping the oneshot idempotent and ordering it `After=rocknix-autostart.service`.

---

## §G. RISKS & OPEN QUESTIONS

1. **`.path` refire storms.** EmulationStation may touch `/storage/.config/modules/` often; `PathModified=` on a dir could refire frequently. Mitigation: the oneshot is a no-op when the file is present+correct, so refires are cheap. If noisy, watch the *file* (`PathExists=…/etk_pitstop.sh`) rather than the dir — but a `--delete` of the whole dir may need dir-level watching to catch. **Prototype both; measure refire rate from the journal.**
2. **Ordering race at boot (§C.3).** If the `.path` unit activates before `001-sync-modules` deletes the file, the oneshot copies, then the rsync deletes it, then the `.path` must catch the delete. The boot oneshot ordered `After=rocknix-autostart.service` is the belt; the `.path` unit is the suspenders. Verify with a reboot probe.
3. **Does removing the Sentry tripwire reduce robustness if both `.path` units fail to enable?** Keep a feature flag: if `ETK_PATHUNIT_INJECT=0` (env.sh), `install.sh` falls back to deploying the in-Sentry tripwire as today. De-risks rollout.
4. **`uninstall.sh`** must `systemctl disable --now etk-pitstop-inject.path etk-pitstop-inject.service` and remove the unit files (currently it only stops the Sentry).

---

## §H. ACCEPTANCE CRITERIA

1. After a cold boot, `etk_pitstop.sh` is present in `/storage/.config/modules/` and executable, with **no Sentry tripwire code** running.
2. `rm /storage/.config/modules/etk_pitstop.sh` during a session → file reappears in **< 1s** (inotify), verified by timestamp.
3. The Sentry loop contains no module-injection block; its `sleep` widened; IDLE↔RUNNING transitions and daemon lifecycles unchanged (regression-test a game launch + clean exit + an orphan-panic boot).
4. `install.sh` deploys + enables both units idempotently; re-running install is a no-op.
5. `uninstall.sh` fully removes both units; Tools-menu entry stops reappearing after the next boot.
6. `ETK_PATHUNIT_INJECT=0` cleanly restores the legacy in-Sentry tripwire.
7. Journal shows no `.path` refire storm (sane refire count over a 10-minute idle + one game session).

---

## §I. PHASED PLAN (~1 short sprint)

- **Phase 0 — Reboot probe (~1 hr):** drop the two units on the rig by hand, reboot, confirm §C.3 ordering holds and the file survives the `001-sync-modules` wipe. Measure refire rate. Decide file-watch vs dir-watch.
- **Phase 1 — install.sh wiring (~3 hr):** generate/deploy `etk-pitstop-inject.{service,path}`; gate behind `ETK_PATHUNIT_INJECT` (default 1, fallback 0).
- **Phase 2 — Sentry slimming (~2 hr):** remove the tripwire block from the generated `01-etk-sentry.sh`; widen `sleep`; update the `AI_MANIFEST.md` "DEFEATING VOLATILE DIRECTORIES" section to describe the `.path`-unit approach as primary and the in-loop tripwire as the documented fallback.
- **Phase 3 — uninstall + docs (~1 hr):** teardown in `uninstall.sh`; note the new units in README/CHANGELOG.
- **Phase 4 — regression (~1 hr):** game launch / clean exit / orphan-panic boot all unaffected; acceptance §H green.

---

## §J. TL;DR

- The Sentry polls for **two** things; only **emulator ignition** needs polling (no native hook in `runemu.sh` — confirmed). The **`etk_pitstop.sh` re-injection tripwire** can move to a systemd **`.path` unit**, which is the idiom ROCKNIX itself uses (`hdmi-hotplug.path`).
- Win: instant (inotify) re-injection instead of up-to-2s, zero steady-state cost, survives a Sentry crash, platform-idiomatic, and lets the Sentry loop slow down.
- Cost: 3 small units + one boot-ordering subtlety (oneshot `After=rocknix-autostart.service` + `.path` suspenders). Keep a `ETK_PATHUNIT_INJECT=0` fallback to the legacy tripwire.
- The volatile wipe itself is **unavoidable** — `001-sync-modules` rsyncs `--delete` from read-only squashfs. We re-assert; we cannot persist into that dir.
- **Not over-engineering to fix; a clean, bounded modernization.** ~1 sprint, low risk, fully reversible via the feature flag.
