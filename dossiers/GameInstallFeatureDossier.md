# DOSSIER: TOOLS Tab — Headless PKG/RAP Installer & `.psn` Launcher Generator

**Target file (primary):** `bin/etk_pitstop.py` (v13.0.0, TABS refactor)
**Supporting edits:** `config/etk_pitstop.sh`, `scripts/env.sh`, `install.sh` (Step 1 provisioning), `01-etk-sentry.sh` (sentinel honor)
**Roadmap slot:** Phase 12 (native Rocknix utilities app)
**Author:** pre-implementation brief for Claude Code
**Status:** PROPOSAL — nothing herein is implemented. Treat as planning only.

---

## 0. ONE-PARAGRAPH SUMMARY

Add a third tab, `TOOLS`, to the existing tab-dispatched Pitstop engine. Its first
(and for now only) function is **INSTALL**: scan a staging directory for `.pkg` and
`.rap` files the user dropped there, install each `.pkg` via the RPCS3 CLI, copy each
`.rap` verbatim into the emulator's `exdata` license folder, discover the resulting
title ID, and write a `<GameTitle>.psn` launcher into the ES PS3 roms directory so the
game appears in the carousel — all driven by the gamepad, no mouse, no GUI, no
magnifying glass. This removes the single worst onboarding step for Retroid Rocknix
users and, as a side effect, fixes a latent bootstrap wall (see §3).

---

## 1. WHY THIS MATTERS (IMPACT)

1. **Removes the GUI-on-a-postage-stamp install ritual.** Today a PSN title requires
   launching RPCS3 from Rocknix Tools, then operating `File > Install Packages/Raps`
   with a mouse against a sub-6" panel. This is the most-cited PS3-on-handheld pain point.
2. **Fixes a bootstrap wall (critical, see §3).** The current Pitstop launcher hard-exits
   when no title ID resolves. A fresh rig with no installed PS3 games cannot open Pitstop
   *at all*. The installer is the missing front door — but only if it is reachable in the
   unresolved state.
3. **Architecturally cheap.** `etk_pitstop.py` was explicitly built for a 3rd tab:
   "Adding a future 3rd/4th tab is one line here plus a new `CURRENT_TAB_*` constant and
   matching draw_/handle_ pair." Geometry, chrome, tab-switch, and gamepad plumbing already
   exist and are reused unchanged.
4. **Low blast radius.** Install is an idle-time, atomic, file-level operation. It does not
   touch the SHM IPC backbone, the thermal governor, the vault accountant, or the shader
   symlink. The one cross-cutting interaction (the Sentry) is identified and contained in §4.

---

## 2. WHAT THE REPO ALREADY GIVES US (GROUND TRUTH)

| Need | Already in repo | Reference |
|---|---|---|
| `.psn` = `<GameTitle>` file containing `<TITLE_ID>` text | `resolve_game_name()` reads this map | `bin/etk_pitstop.py` |
| PS3 launcher dir | `/storage/games-internal/roms/ps3` | `PS3_ROMS_DIR` |
| RPCS3 custom config tree | `/storage/games-internal/roms/bios/rpcs3/custom_configs/` | manifest + Python |
| dev_hdd0 location | `/storage/games-internal/roms/bios/rpcs3/dev_hdd0/` | manifest ("VIRTUAL HDD PATH") |
| RPCS3 log (for parse/anchor) | `/storage/.cache/rpcs3/RPCS3.log` | `session_postmortem.sh` |
| Tab registry extension point | `TABS`, `CURRENT_TAB_*`, `_switch_tab()` | `bin/etk_pitstop.py` |
| Gamepad codes (single source) | `BTN_CONFIRM=305`, `BTN_BACK=304`, hat codes | `bin/etk_pitstop.py` H1 block |
| Atomic write convention | `os.replace` (Py), `echo>tmp && mv` (sh) | `save_menu_matrix`, H2 |
| Non-raising logger | `_log()` | `bin/etk_pitstop.py` |
| `RUNNING` detection pattern | `pgrep -f "rpcs3\|AppRun.wrapped"` | `01-etk-sentry.sh` |

**Derived (not yet constants anywhere — add to `env.sh`, see §6):**
- Staging dir: `/storage/games-internal/roms/tmp/rpcs3_installs/`
- Installed games root: `<dev_hdd0>/game/<TITLE_ID>/`
- RAP target (exdata): `<dev_hdd0>/home/00000001/exdata/`

---

## 3. THE BOOTSTRAP WALL (MUST FIX ALONGSIDE THE TAB)

`config/etk_pitstop.sh` resolves `$ID_FILE` → `$RECENT_ID_FILE`, validates against
`^[A-Z]{4}[0-9]{5}$`, and on failure prints "NO TARGET RESOLVED" and `exit 1` **before**
ever `exec`-ing the Python engine. Rationale (sound, keep it for TUNING): editing a default
config strands tuning in the wrong file.

But the installer's whole purpose is the no-game-yet state. Resolution:

**Adopt launcher behavior: degrade, don't exit.**
- When a valid ID resolves → launch as today, default tab `TUNING` (byte-identical UX).
- When no ID resolves → `export ETK_NO_TARGET=1`, launch the engine anyway, force the
  initial tab to `TOOLS`.
- In the engine, when `ETK_NO_TARGET=1`: `TUNING` and `TELEMETRY` render an inert panel
  ("Install a game first — no PS3 title resolved yet") instead of touching a config or
  ledger. `TOOLS` is fully live. After a successful install + gamelist refresh + first
  launch, the normal resolved path takes over on next open.

This keeps a single module and a single Python entrypoint. Do **not** spawn a second
launcher; it duplicates the gamepad/foot/scaling logic and drifts.

---

## 4. THE SENTRY INTERACTION (MUST CONTAIN)

`01-etk-sentry.sh` is an event-driven state machine keyed on
`pgrep -f "rpcs3|AppRun.wrapped"`. The CLI installer **is** an `rpcs3` process, so a bare
install trips a full IDLE→RUNNING→IDLE cycle: spawns `vault_d.sh`/`thermal_d.sh`, seeds
`session_start.txt`/`battery_start.txt`/`thermal_log_start.txt`, then on exit runs
`session_postmortem.sh`, appending a phantom row to `$SESSIONS_LEDGER`.

The `<TELEMETRY_MIN_SESSION_S` ABORTED reclassification will likely tag it ABORTED (dim,
low-confidence), so it is not catastrophic — but it is spurious daemon churn and a junk
ledger row every install.

**Containment (sentinel flag, honored by the Sentry):**
1. TOOLS writes `"$SHM_DIR/etk_install_lock"` before invoking the installer and removes it
   in a `finally`.
2. In the Sentry loop, **before** the state-detection block, add:
   `[ -f "$SHM_DIR/etk_install_lock" ] && { PREV_STATE="IDLE"; sleep 2; continue; }`
   i.e. while an install is in progress, the Sentry stays parked in IDLE, never transitions,
   never spawns workers, never post-mortems.
3. SHM is volatile and the lock is ephemeral — a crash mid-install at worst leaves a stale
   lock that the next reboot clears. For belt-and-suspenders, have TOOLS also clear any
   pre-existing lock on entry (it owns the lock exclusively; installs are idle-only).

This is the highest-value cross-cutting fix in the dossier. Do not skip it.

---

## 5. TASK 0 — FEASIBILITY SPIKE (DO THIS BEFORE WRITING ANY UI)

The repo cannot tell us whether RPCS3's CLI installer runs cleanly headless on this nightly.
RPCS3 is Qt; `--installpkg` may still require a platform/display plugin. **Characterize it
on-device before building the tab.** SSH to the rig and answer, in order:

1. **What is the real RPCS3 invocation?** Find the binary/AppImage the Rocknix runner execs
   (the Sentry only greps `rpcs3|AppRun.wrapped`; the actual launch path is elsewhere —
   inspect the ports/runner that Tools > RPCS3 uses). Record the exact argv.
2. **Does `--installpkg` work from a `foot` terminal in the ES Wayland session?**
   Try `<rpcs3> --installpkg /path/to/test.pkg` with the session's `WAYLAND_DISPLAY` /
   `XDG_RUNTIME_DIR` exported. Capture: exit code, whether a window appears, whether it
   auto-exits or hangs, and where files land under `<dev_hdd0>/game/`.
3. **If it hangs / needs interaction:** test `--no-gui` or equivalent for this build; if none
   exists, the fallback is to run it within the live compositor and auto-dismiss, or to
   extract the pkg with a standalone tool. Document which path the build forces.
4. **RAP handling:** confirm that a verbatim copy of `<contentID>.rap` into
   `<dev_hdd0>/home/00000001/exdata/` is sufficient on this build (it is the documented PS3
   path), versus needing the RAP to pass through RPCS3's converter to a `.rif`.
5. **PARAM.SFO presence:** confirm `<dev_hdd0>/game/<ID>/PARAM.SFO` exists post-install for
   PSN titles (source of the human `TITLE` for the `.psn` filename).
6. **Gamelist refresh:** determine whether ES exposes a scriptable gamelist reload, or whether
   the user must do START → Game Settings → Update Gamelists (as `install.sh` already instructs).

**Gate:** if Q2 shows the installer cannot run without manual GUI on this nightly, STOP and
revise the approach before touching `etk_pitstop.py`.

---

## 6. PATH CONSTANTS — ROUTE THROUGH `env.sh` (IMMUTABLE LAW #2)

The manifest mandates `scripts/env.sh` as the only definer of env vars. Add there
(BusyBox `sh` syntax), then have the Python read with fallbacks (mirroring the existing
`ETK_ROOT`/`TELEMETRY_DIR` pattern so isolated dev runs still work):

```
RPCS3_DEV_HDD0="/storage/games-internal/roms/bios/rpcs3/dev_hdd0"
RPCS3_GAME_DIR="$RPCS3_DEV_HDD0/game"
RPCS3_EXDATA_DIR="$RPCS3_DEV_HDD0/home/00000001/exdata"
PKG_STAGING_DIR="/storage/games-internal/roms/tmp/rpcs3_installs"
PS3_LAUNCHER_DIR="/storage/games-internal/roms/ps3"
RPCS3_BIN="<resolved in Task 0>"
```

`install.sh` Step 1 must `mkdir -p` the staging dir, exdata dir, and game dir so a fresh
rig has them before first use (it already provisions `custom_configs`).

---

## 7. FEATURE SPEC — TOOLS / INSTALL

### 7.1 Tab registration (the "one line")
- Add `CURRENT_TAB_TOOLS = 2`.
- Append `("TOOLS", CURRENT_TAB_TOOLS)` to `TABS`.
- Add `draw_tools(stdscr, state)` and `handle_tools_kb` / `handle_tools_pad`.
- Wire into `_draw()` dispatch, `_switch_tab()`, and the keyboard/gamepad tab-switch
  interceptors in `main()`. Keep L1/R1 + `[`/`]` semantics; with three tabs, `]` from
  TELEMETRY should advance to TOOLS (consider cycling, or add a third explicit key — match
  whatever the existing two-tab `[`/`]` idiom extends to most naturally; document the choice).

### 7.2 Install flow (the `A`/CONFIRM action on the TOOLS tab)
1. **Pre-flight.** Refuse to run if `pgrep -f "rpcs3|AppRun.wrapped"` shows RPCS3 already
   running (installing into a live HDD risks corruption). Surface a clear status line.
2. **Lock.** Write `etk_install_lock` to SHM (§4).
3. **Scan.** Enumerate `*.pkg` and `*.rap` (case-insensitive) in `PKG_STAGING_DIR`.
   Empty → friendly "drop .pkg/.rap files in <path>" panel; no error.
4. **Per `.pkg`:**
   a. Snapshot `set(os.listdir(RPCS3_GAME_DIR))`.
   b. Invoke the installer (argv from Task 0) with output captured to `$LOG_PATH`.
   c. On nonzero exit → record failure for this file, continue to next; leave the `.pkg`
      in staging (do not delete on failure).
   d. Diff `listdir` → the new dir name is the `TITLE_ID`. If zero/many new dirs, fall back
      to PARAM.SFO scan or report ambiguity rather than guessing.
5. **Resolve human title.** Parse `<game>/PARAM.SFO` `TITLE` (UTF-8). On any parse failure,
   fall back to `TITLE_ID` as the display name. Never abort the whole install on a SFO miss.
6. **Per `.rap`:** copy verbatim filename into `RPCS3_EXDATA_DIR` (create dir, lowercase, if
   absent). Idempotent (skip if identical exists). RAPs are licenses, not "installed."
7. **Write `.psn`.** `PS3_LAUNCHER_DIR/<sanitized title>.psn` containing exactly the raw
   `TITLE_ID` (no trailing newline issues — match `resolve_game_name()`'s `.strip()` read).
   Atomic `tmp` + `os.replace`. Sanitize the *filename* (strip/replace `/ \ : " ' ` and
   control chars); the *content* stays the raw ID. Idempotent: if a `.psn` already maps to
   this ID, leave it.
8. **Unlock** in `finally`; always remove `etk_install_lock`.
9. **Report.** Per-file outcome list in the tab body (installed / license-copied /
   already-present / failed). Footer: instruct the gamelist refresh (or trigger it if Task 0
   Q6 found a scriptable path). Optionally offer to clear successfully-installed files from
   staging on a second confirm (never auto-delete).

### 7.3 Edge handling
- `.pkg` with no matching `.rap`: install + `.psn`, no exdata write (free/license-not-required
  titles, updates).
- `.rap` with no `.pkg`: copy to exdata only (DLC/update license), no `.psn`. Say so.
- Multiple `.pkg`s: batch; one `.psn` each.
- Re-run with same files: all idempotent; no dup `.psn`, no error.

---

## 8. INVARIANTS (DO NOT VIOLATE)

1. **Reuse the H1 gamepad constants.** Do not redefine `BTN_CONFIRM`/`BTN_BACK`/hat codes
   in the tools handlers. The InputPlumber confirm=305 quirk is already centralized; mirror,
   never fork. (Also mirror into `input_d.py` only if you touch codes there — you shouldn't.)
2. **Atomic writes only** for `.psn` (`os.replace`), matching H2 / `save_menu_matrix`.
3. **`_log()` for all diagnostics; never raise from logging or from the tools draw path.**
   A logging or staging-scan failure must not crash the engine (`_fatal` is for unrecoverable
   schema/YAML only).
4. **BusyBox-safe** for any shell added to `env.sh`/`install.sh`/`Sentry`: POSIX only, no
   `--long-opts`, no `grep -P`, no `find -printf`, no `stat --format` without fallback.
   The Python side may use `os`/`shutil` freely.
5. **Never touch SHM/thermal/vault hot paths.** The only SHM contact is the install lock (§4).
6. **TUNING remains byte-identical** in the resolved-ID path (all FIX 1–5 invariants intact).
7. **No GUI dependency in the success path.** If Task 0 forces a transient window, it must be
   auto-managed; the user never operates a pointer.
8. **Do not auto-delete user files.** Failed installs leave staging untouched; successful
   cleanup is opt-in.

---

## 9. TEST PLAN

- **Test 1 — Cold bootstrap:** wiped rig, zero PS3 games, one `.pkg` + matching `.rap` in
  staging. Open Pitstop (must open onto TOOLS via §3), install, confirm `.psn` written,
  game appears after gamelist refresh, launches, and ETK identity/tuning then work normally.
- **Test 2 — pkg, no rap:** installs, `.psn` written, no exdata write, no error.
- **Test 3 — rap, no pkg:** copied to exdata, no `.psn`, clear messaging.
- **Test 4 — batch:** three `.pkg`s → three installs, three `.psn`s.
- **Test 5 — idempotency:** re-run Test 1 inputs → no dup `.psn`, no error, existing install
  detected.
- **Test 6 — corrupt pkg:** installer nonzero exit → file reported failed, no `.psn`, staging
  intact, other files in the batch still process.
- **Test 7 — hostile title:** PARAM.SFO `TITLE` with `/` and quotes → filename sanitized,
  `.psn` content still the raw ID, `resolve_game_name()` round-trips it.
- **Test 8 — Sentry containment:** run an install; confirm no IDLE→RUNNING transition fired
  (no `vault_d`/`thermal_d` spawned, no new `$SESSIONS_LEDGER` row, no daemon churn).
- **Test 9 — RPCS3 running:** attempt install while a game runs → refused with clear status,
  no filesystem mutation.
- **Test 10 — tab state:** switch TOOLS↔TUNING↔TELEMETRY mid-session; cursor/edits/gamepad
  status preserved (extends existing Test 4 convention); a transient install-result panel
  clears cleanly on tab switch.
- **Test 11 — no-target degrade:** with `ETK_NO_TARGET=1`, TUNING/TELEMETRY render the inert
  panel and never write a config or ledger.

---

## 10. OPEN QUESTIONS (RESOLVE VIA TASK 0 / ON-DEVICE)

1. Exact `RPCS3_BIN` argv on this nightly. *(blocking)*
2. Headless `--installpkg` behavior: window? exit code? auto-exit? *(blocking — see §5 gate)*
3. RAP: verbatim exdata copy sufficient, or converter required? *(blocking for RAP path)*
4. PARAM.SFO reliably present post-install? *(non-blocking — TITLE_ID fallback exists)*
5. Scriptable ES gamelist reload, or manual refresh instruction? *(non-blocking)*
6. Three-tab `[`/`]` cycling vs. third key — pick the idiom that extends the existing two-tab
   behavior most cleanly. *(cosmetic)*

---

## 11. SUGGESTED COMMIT SEQUENCE

1. Task 0 spike; record findings in this dossier's §10 before code.
2. `env.sh` path constants + `install.sh` Step 1 `mkdir -p`.
3. Sentry install-lock honor (§4) + sentinel.
4. Launcher degrade-don't-exit (§3) + engine `ETK_NO_TARGET` inert panels.
5. TOOLS tab scaffold (registration, empty draw/handlers, tab-switch wiring) — verify
   navigation in isolation, no install logic yet.
6. Install flow (§7.2) behind the scaffold; PARAM.SFO parser with TITLE_ID fallback.
7. Full test pass (§9) on-device.

---

*GEMINI/CLAUDE IMMUTABLE NOTE: This dossier proposes; it does not implement. The §5 gate is
real — if the CLI installer cannot run without manual GUI on the pinned nightly, revise the
approach (do not paper over it with a forced window the user has to dismiss with a pointer,
which would reintroduce the exact pain point this feature exists to remove).*



# DOSSIER ADDENDUM A — Tools-Menu Registration & Auto-MangoHud Onboarding

**Extends:** `DOSSIER_tools_tab_pkg_installer.md` (the PKG/RAP installer + `.psn` generator).
**Touches:** `install.sh` (Step 5 + embedded Sentry), `bin/etk_pitstop.py` (TOOLS flow),
`scripts/env.sh`, plus two **new assets** (an SVG logo and an ASCII description).
**Status:** PROPOSAL — not implemented. Planning only.

This addendum adds two onboarding requirements that the original installer dossier did
not cover. Both are gated behind on-device discovery spikes because they depend on
Rocknix internals that are not reliably documented.

---

## R1 — REGISTER "ETK Pitstop" AS A POLISHED TOOLS-MENU APP

### R1.0 Goal
The launcher should appear in Rocknix Tools as **ETK Pitstop** (spaces, mixed case) with a
logo and a short description, indistinguishable from a stock Rocknix tool — instead of a
bare `etk_pitstop.sh` filename entry.

### R1.1 Ground truth (verified)
- Rocknix ES reads a standard `gameList` per system:
  `<game><path>./etk_pitstop.sh</path><name>ETK Pitstop</name><image>./etk_pitstop.svg</image><desc>…</desc></game>`.
- **ASCII-ONLY for `<name>` and `<desc>`** — ES text rendering has no Unicode support.
  "ETK Pitstop" is safe; the description must avoid em-dashes, curly quotes, °, etc.
- ES prefers image paths **relative to the system's games path** and may rewrite absolute
  paths on its next scrape/gamelist write. Plan for ES rewriting the file, not just us.
- The Tools menu's backing dir, `/storage/.config/modules/`, is the **boot-volatile**
  directory the manifest's "SENTRY TRIPWIRE" already re-injects `etk_pitstop.sh` into.

### R1.2 The real problem: the gamelist is volatile too
This is not an `install.sh`-only write. `/storage/.config/modules/gamelist.xml`, the logo,
and the `.sh` all sit in the directory Rocknix wipes asynchronously at boot. The Sentry
already re-injects the `.sh` every loop; the metadata entry and the logo must be maintained
the **same way**, or the polished name/icon/description survive exactly until the next reboot
and then silently revert to a bare filename entry (or vanish).

### R1.3 Design
Keep persistent masters under `$ETK_ROOT/config/` and treat the volatile copies as
disposable, mirroring the existing `.sh` pattern:
- `$ETK_ROOT/config/etk_pitstop.sh` (already exists)
- `$ETK_ROOT/config/etk_pitstop.svg` (**new asset**, see R1.5)
- The Sentry tripwire, in its existing self-heal block, additionally:
  1. `cp -f` the `.svg` into `/storage/.config/modules/` (so a relative `./etk_pitstop.svg`
     in the gamelist always resolves even after a wipe).
  2. **Ensures** the gamelist has our enriched `<game>` entry for `./etk_pitstop.sh` —
     idempotently: inject if absent, replace if present-but-stale (e.g. a Rocknix-generated
     bare entry whose `<name>` is the filename).

Why relative `./etk_pitstop.svg` and not an absolute persistent path: ES rewrites image
paths to relative on scrape anyway (R1.1), and re-injecting the SVG into the volatile dir is
the same cheap `cp -f` the tripwire already does for the `.sh`. One consistent mechanism.

### R1.4 Spike R1 — modules gamelist regeneration behavior (BLOCKING for merge strategy)
Determine, on-device, after a clean reboot:
1. Does `/storage/.config/modules/gamelist.xml` exist, and does Rocknix **regenerate** it
   (scanning `.sh` files into default entries), or does it preserve a hand-authored file?
2. If it regenerates: does it **preserve** existing `<game>` entries it didn't create, or
   clobber the whole file? (Determines whether we can pre-author vs. must post-process.)
3. Are there other tools' entries in it we must not destroy? (Almost certainly yes — so
   **never overwrite the whole file**; operate only on our `<game>` block.)

Outcome decides the injector:
- **Preserves/merges** → tripwire does an idempotent block-level upsert of our `<game>`.
- **Clobbers/regenerates each boot** → tripwire must run *after* Rocknix's regeneration and
  re-assert our entry; a pre-written file alone won't survive.

### R1.5 New assets to produce (currently undesigned/undrafted)
- **`etk_pitstop.svg`** — a logo. Constraints: renders at Tools-tile size on the Flip 2
  panel; readable small; flat (no tiny detail). **Confirm ES renders SVG for a game
  `<image>`** (it renders SVG for system logos, so likely yes — but if not, ship a PNG
  fallback). Lives at `$ETK_ROOT/config/etk_pitstop.svg`.
- **Description string** — one or two ASCII sentences (no Unicode punctuation). Something
  like: "ETK Pitstop - on-device tuner and telemetry for PS3 emulation. Edit per-game RPCS3
  settings and review crash/shader history with the gamepad."
- I can draft both on request; this addendum only specifies the slots and constraints.

### R1.6 install.sh changes (Step 5)
Step 5 already deploys the `.sh` master and the immediate modules copy. Extend it to also:
- `rsync` `./config/etk_pitstop.svg` to `$ETK_ROOT/config/` (and an immediate copy to
  `/storage/.config/modules/`).
- Write/refresh the master gamelist fragment used by the tripwire upsert (or embed the
  canonical `<game>` block in the Sentry heredoc so there is a single source of truth).
- Keep the existing `sync` + launcher-verify; add a verify that the gamelist contains
  `>ETK Pitstop<` after deploy, failing loudly if not (mirrors the existing launcher check).

---

## R2 — AUTO-ENABLE MANGOHUD FOR THE NEWLY INSTALLED GAME

### R2.0 Goal
After a `.pkg` install creates `roms/ps3/<GameTitle>.psn`, the game's MangoHud overlay
should already be **Enabled** — without the user diving select → X → Advanced Game Options →
System Options → MangoHud Overlay → Enabled. The whole point of ETK is the HUD; shipping a
freshly onboarded game with it off defeats the feature.

### R2.1 The unknown (honest)
Where Rocknix persists the per-game "MangoHud Overlay = Enabled" override is **not known**
and not reliably documented. Do not guess a path into the implementation. Two leading
hypotheses:
- **(a) Extended tag in the PS3 gamelist** — `/storage/games-internal/roms/ps3/gamelist.xml`
  may carry a per-game feature tag (Batocera/ES forks store advanced options as gamelist
  attributes). If so, our `.psn` entry gets an extra child tag.
- **(b) A separate per-game settings file** — keyed by game path or a hash, under something
  like `/storage/.config/system/…`. If so, we write/append a keyed entry.

### R2.2 Spike R2 — locate the setting (BLOCKING; concrete method)
On-device, definitively locate it by observation, not documentation:
1. Pick a test game. Note current state (overlay = Default).
2. `find /storage/.config /storage/games-internal/roms/ps3 -type f > /tmp/pre.txt` and grab
   mtimes.
3. In the Rocknix UI, set that game's MangoHud Overlay = Enabled.
4. `find … -newermt '2 minutes ago'` (or diff mtimes) to see exactly which file changed.
5. Inspect the diff: the tag name / key format / value is the spec for our writer.

Capture: file path, whether it's the gamelist or a separate file, the exact key/tag, the
value for "enabled", and how the game is keyed (full `.psn` path? basename? hash?).

### R2.3 Design (once R2 resolves)
- Write the setting idempotently for the new `.psn`, BusyBox-safe (Python `os`/`shutil` if
  done in the TOOLS flow; POSIX `sh` if a shell helper). Never rewrite the whole gamelist or
  settings file — upsert only the target game's entry/tag.
- **Sequencing dependency:** if the setting lives *in the PS3 gamelist* (hypothesis a), the
  game must first exist in that gamelist — i.e. the write happens **after** the post-install
  "Update Gamelists" step from the original dossier (§7.2 step 9), not before. If it lives in
  a *separate keyed file* (hypothesis b), it can be written immediately at install time. The
  spike outcome picks the order.
- Make the writer reusable: the TOOLS-tab installer calls it per newly installed game; a
  host-side `install.sh` helper can batch-apply it to any pre-seeded `.psn`s.

### R2.4 Don't fight ES
If the setting is a gamelist tag, ES may rewrite that gamelist on its own metadata
operations. Verify the tag survives an "Update Gamelists" cycle; if ES strips unknown tags,
the setting must be (re)written after gamelist regeneration, same hazard class as R1.

---

## NEW env.sh CONSTANTS (per IMMUTABLE LAW #2)
```sh
PS3_GAMELIST="/storage/games-internal/roms/ps3/gamelist.xml"
MODULES_DIR="/storage/.config/modules"
MODULES_GAMELIST="$MODULES_DIR/gamelist.xml"
ETK_PITSTOP_SVG="$ETK_ROOT/config/etk_pitstop.svg"
# per-game MangoHud setting path: TBD by Spike R2 — add once located
```

## ADDED INVARIANTS
1. **Never overwrite a whole gamelist or settings file** — upsert only the one `<game>` /
   key that is ours. Other tools' and other games' entries are sacred.
2. **ASCII-only** for any `<name>`/`<desc>` written into a gamelist.
3. **Volatile-dir parity** — anything written into `/storage/.config/modules/` (sh, svg,
   gamelist entry) must be re-asserted by the Sentry tripwire, never assumed persistent.
4. **Idempotent** — re-running install or re-onboarding the same game must not duplicate
   entries, tags, or `<game>` blocks.
5. **BusyBox-safe** XML/text edits; if `xmllint`/`sed` XML handling proves too fragile on the
   pinned build, prefer a small Python pass (python3 is present and already used by Pitstop).

## ADDED TESTS
- **Test A1 — Polished entry:** after install, Tools shows "ETK Pitstop" with logo + desc,
  not a filename.
- **Test A2 — Reboot survival:** reboot; the polished entry and logo are still there (tripwire
  re-asserted them through the modules wipe).
- **Test A3 — No collateral:** other Tools entries are intact after our injection.
- **Test A4 — SVG render:** logo renders (not a broken-image placeholder); if not, PNG
  fallback renders.
- **Test A5 — Auto-MangoHud:** install a game via the TOOLS flow; without touching Advanced
  Game Options, the game launches with the overlay already on.
- **Test A6 — Idempotent re-onboard:** re-install the same game; no duplicate gamelist entry,
  no duplicated MangoHud tag.
- **Test A7 — Survives Update Gamelists:** run START → Game Settings → Update Gamelists; the
  MangoHud setting and the `.psn` entry survive ES's regeneration.

## ADDED SPIKES (do before coding either feature)
- **Spike R1:** modules `gamelist.xml` regeneration/preservation behavior (R1.4).
- **Spike R2:** per-game MangoHud setting location via toggle-and-diff (R2.2).
- **Spike R1b:** confirm ES renders SVG as a game `<image>` (else PNG).

## UPDATED COMMIT SEQUENCE (folds into original §11)
1. Spikes R1, R1b, R2 — record findings before code.
2. `env.sh` constants (+ the R2 path once known).
3. Produce assets: `etk_pitstop.svg` + ASCII description.
4. install.sh Step 5: deploy SVG + master gamelist fragment + verify.
5. Sentry tripwire: extend self-heal to re-inject SVG and upsert the gamelist `<game>` entry.
6. Auto-MangoHud writer (per R2 outcome), wired into the TOOLS install flow after `.psn`
   creation, with the correct sequencing relative to gamelist refresh.
7. Full test pass (A1–A7) on-device.

---

*CLAUDE IMMUTABLE NOTE: Both features edit files that something else also owns — Rocknix
regenerates the modules gamelist, and ES rewrites game gamelists. The safe contract is
**upsert, never replace**, and **re-assert through the tripwire, never assume persistence**.
A single whole-file overwrite here can wipe other tools from the Tools menu or other games'
settings. When in doubt, operate on the one `<game>` block that is ours and leave the rest
byte-for-byte intact.*


## ETK PITSTOP ASSETS
- Placeholder `.svg` avail in `config`
- Placeholder description
`Description copy (ASCII-clean, since ES can't render Unicode — no em-dashes, smart quotes, or °):

ETK Pitstop - your trackside pit wall for PS3 emulation. Tune RPCS3 settings per game, install new titles, and read live thermal, load, and shader-harvest telemetry, all from the gamepad with no mouse required.

A shorter variant if the Tools tile clips long text:

ETK Pitstop - on-device pit wall for PS3 emulation. Gamepad-driven RPCS3 tuning, game installs, and crash/shader telemetry.

`