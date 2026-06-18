# CLAUDE.md — ETK operating context (auto-loaded every session)

## Session start — MANDATORY reading, IN THIS ORDER (before touching anything)
Teaching the system live costs real progress. Prior sessions re-derived *documented* facts
on the fly (the `pgrep -f` self-match, the Sentry SHM-reseed-on-IDLE, `input_d` being
in-game-only) because they read on-demand instead of front-loading. Don't. Open every ETK
session by reading, in order:
1. **`README.md`** — the public on-ramp + current certified stack (start here, like any user would).
2. **`AI_MANIFEST.md`** — the dev/AI **technical layer**: BusyBox/Rocknix laws, the Sentry state
   machine, the SHM map, RPCS3 paths, the two packaging models. This is the deep reference, **not
   sacred scripture** — some specific "laws" have proven unverified (e.g. the autostart/MangoHud
   race), so verify before building on a given one.
3. **The README "ETK File Structure" section** — the file/daemon inventory (what each piece *is*).
4. **`install.sh`** — the deploy/sync flow (how anything reaches the rig). Rig changes go through
   `install.sh`, never a one-off `scp` (a reboot/reinstall reverts hand-surgery).
5. **The daemons** in `bin/` (`input_d.py`, `vault_d.sh`, `thermal_d.sh`, `mango_bridge.sh`,
   `recovery.sh`, `session_postmortem.sh`) + `scripts/env.sh` — their **behavior / state model**,
   not just their names. (e.g. `input_d.py` only fires in-game; the Sentry reseeds SHM on state change.)

## Non-negotiables
- **Always-reboot gate** — every tune/config change must survive a COLD boot; reboot is the only honest validation. ROCKNIX reverts non-persistent changes; `/storage` persists, read-only root does not.
- **Use the kit** — prefer ETK's own tools (`install.sh` / `uninstall.sh` / `scripts/` / Pitstop TOOLS / `etk_telemetry` / `recovery.sh`) over bespoke manual rig surgery. Check the kit before hand-crafting a workaround.
- **Validate before integrate** — prove speculative tuning on a disposable on-rig harness, cold-booted, before touching locked-down core (`install.sh`, daemons, telemetry schema, the R3 panic path).
- **Verdict from the operator's screen, mechanism from the log** — don't crown a fix from one run or a mid-process read; the noise floor on this title is huge (GT5P ~77–2886 s). Rule out our own code before blaming hardware (bad cable/card).

## Repo / git
- `origin` = github.com/mercurious/etk. `garage` = etk-garage (private) — **NEVER push `garage` to `origin`**.
- Do **not** commit or push unless explicitly asked. Branch off `main` for changes.
- Public history was legal-scrubbed (turnip fair-use abandoned, distribution-intent dossiers purged) — keep new docs development/tuning-focused, not distribution-intent.

## Target rig (verify against `AI_MANIFEST.md` / `README.md` — may drift)
- SM8250 / Adreno 650 (Retroid Pocket Flip 2), ROCKNIX nightly + Mesa Turnip + RPCS3; primary target title GT5P Spec III (NPEA00050).
- Cockpit skill drives the live rig (adb=Android / ssh=ROCKNIX). Stage IV = forking Mesa Turnip (see `dossiers/`).
