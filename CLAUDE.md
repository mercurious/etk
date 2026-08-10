# CLAUDE.md — ETK operating context (auto-loaded every session)

## Session start — MANDATORY reading, IN THIS ORDER (before touching anything)
Teaching the system live costs real progress. Prior sessions re-derived *documented* facts
on the fly (the `pgrep -f` self-match, the Sentry SHM-reseed-on-IDLE, `input_d` being
in-game-only) because they read on-demand instead of front-loading. Don't. Open every ETK
session by reading, in order:
1. **`README.md`** — the public on-ramp + current certified stack (start here, like any user would).
2. **`TRACK_MANUAL.md`** - the system manual and map with project handbook, your orientation.
   **Read §0 OPERATOR LOOPS first** — it is ten lines and it is the only part written from
   the OPERATOR's side rather than the implementer's. Everything else tells you what a
   thing IS; §0 tells you what it is FOR and which surface the human actually touches.
   A change is not done until that surface shows it (2026-08-10: a cumulative driver
   catalog was landed correctly in four places and the flashed card still offered a
   one-entry chooser — three image rebuilds).
3. **`AI_MANIFEST.md`** — the dev/AI **technical layer**: BusyBox/Rocknix laws, the Sentry state
   machine, the SHM map, RPCS3 paths, the two packaging models. This is the deep reference, **not
   sacred scripture** — some specific "laws" have proven unverified (e.g. the autostart/MangoHud
   race), so verify before building on a given one.
4. **`install.sh`** — the deploy/sync flow (how anything reaches the rig). Rig changes go through
   `install.sh`, never a one-off `scp` (a reboot/reinstall reverts hand-surgery). **Read it; the
   OPERATOR runs it — you never do** (bytes-to-atoms, below). Same for `forge.sh`, the build
   conductor. This says which MECHANISM a change travels through, not whose hand starts it.
5. **The daemons** in `bin/` (`input_d.py`, `vault_d.sh`, `thermal_d.sh`, `mango_bridge.sh`,
   `recovery.sh`, `session_postmortem.sh`) + `scripts/env.sh` — their **behavior / state model**,
   not just their names. (e.g. `input_d.py` only fires in-game; the Sentry reseeds SHM on state change.)
   - **Uncommitted `bin/` changes may be side-tracked experiments — confirm scope before absorbing.**
     `git status` at the top; if a working-tree-modified or untracked file is an abandoned/parked
     spike (e.g. the input_d pad-movie record side + `padreplay.py` = B-fork autonomy, ROCKNIX-blocked),
     don't let it re-focus the session. Ask which files are in scope when the task and the diff diverge.
   - **Some rig scripts are NOT in the repo** (hand-pushed) — they vanish on uninstall/reinstall
     (the Manage-Shaders trap). NO current live example: the two historical offenders are now both
     install.sh-generated heredocs — `02-etk-coredump.sh` (STEP 6.8) and `etk-sd-rebind.sh` (STEP 6.85,
     rewritten 2026-07-07 label-based v3 + added to uninstall.sh). When you find a rig daemon that
     ISN'T in the repo, flag it for the install.sh push list rather than trusting it to persist.

## Non-negotiables
- **BYTES-TO-ATOMS — always defer to the humans at the threshold.** Claude works in bytes:
  the repo, the analysis, the staged candidate. Bytes are cheap and reversible. There are
  **three moments where our bytes reach real atoms**, and at each one a HUMAN decides,
  because a human is the only thing that can be accountable for the consequence. You
  **NEVER** cross these, not once, not to "check" (operator rationale, 2026-08-10):
  - **`./install.sh`** (or `uninstall.sh`, or the PowerShell port) — **the rig can be bricked.**
    A human decides to take that risk, and owns it.
  - **`./forge.sh`** (or a `tools/forge/lane_*.sh`, or a build on `etk-cloud`/the Air) — it runs
    on **someone else's computer and can trigger an invoice. Money is atoms.**
  - **publishing** — `gh release create/upload/edit`, moving a tag, `git tag` on a release
    version (cutting the tag IS the release), or any upload of an artifact to a fetch path —
    **our bytes land on other humans' machines.** ⚠️ **This threshold is the PRODUCT, not the
    source.** A `git push` of code to the public `origin/main` in the course of development is
    **normal and permitted as usual** (operator, 2026-08-10) — it is subject to the trunk
    protocol below, not to this law. What crosses is the thing a user CONSUMES: the release,
    the tag, the artifact.

  **`--dry-run` and `--status` are NOT exemptions.** `forge.sh --dry-run` sshes to the build
  node in preflight; a dry run is still a hand on the control. If the flag is on a
  bytes-to-atoms tool, the tool is off-limits — read its source instead.

  **What you DO instead:** stage the artifact, set the `etk.conf` knob, run the gates
  (`tools/*.sh`, `tools/test_*.py` — those are bytes), write the handoff, and **hand off**.
  Then verify afterward from read-only telemetry. **Still allowed:** read-only `ssh` to rig
  or build node, `git` on the repo (subject to the trunk protocol below), and every
  host-side analysis tool.

  **HOW TO HAND OFF — give the operator the one-click prompt (operator-directed 2026-08-10).**
  Deferring is not stopping and going quiet; it is putting the control **in their hand, ready
  to press.** End the handoff with the exact command in its own ```bash fenced block (one
  command, no `$`, no interleaved output) — Claude.app renders a Run button on it, so the
  operator triggers it in this terminal and we BOTH watch the TUI's progress bars and status
  live. That is the pit wall: the Driver's hand on the control, the Engineer reading the
  instruments beside them. Say plainly what the command will do, what to watch for, and what
  would falsify it. **A handoff without a runnable prompt is an unfinished handoff.**

  **The RATIONALE is the test, not the filenames** — that is what makes this a class. The
  names age out: `forge.sh` was three days old on 2026-08-10 when Claude ran it three times,
  having inherited no law because every document named only `install.sh`. So don't ask "is
  this on the list?" Ask the three questions: **could this brick hardware? could it spend
  someone's money or consume someone else's machine? could it reach another human's
  computer?** Any yes = the threshold = a human decides. A tool written tomorrow is covered
  today. **When unsure whether something crosses, it does — ask.** Full text: `AI_MANIFEST.md`
  Law #9.
- **Always-reboot gate** — every tune/config change must survive a COLD boot; reboot is the only honest validation. ROCKNIX reverts non-persistent changes; `/storage` persists, read-only root does not.
- **Use the kit — including the skills, to their FULL capability** — prefer ETK's own tools (`install.sh` / `uninstall.sh` / `scripts/` / Pitstop TOOLS / `etk_telemetry` / `recovery.sh`) AND the purpose-built **skills** (`cockpit`, etc.) over bespoke manual rig surgery. **"Prefer the kit's tools" is about WHICH MECHANISM the change travels through — it is NOT permission to run one yourself.** For the bytes-to-atoms tools above (`install.sh`, `uninstall.sh`, `forge.sh`) preferring the kit means *preparing that tool's input and handing it to the operator*; running it yourself violates the first non-negotiable. This sentence used to read as an invitation and on 2026-08-10 it was taken as one. **This means using a skill's *automation*, not just borrowing its data path.** Concrete miss to never repeat (2026-06-18): for a live multi-crash watch, Claude invoked the `cockpit` skill but hand-rolled per-freeze `ssh`/`grim` grabs and made the operator manually call "freeze" 8+ times — when `cockpit/scripts/rocknix_spotter_loop.sh` ships a **crash-watch loop built for exactly that**. When a task is "watch the rig until X happens," reach for the skill's monitoring loop FIRST (arm it at idle, before launch; have it auto-break + notify). Check the kit/skill's actual capabilities before building the workaround by hand.
- **Validate before integrate** — prove speculative tuning on a disposable on-rig harness, cold-booted, before touching locked-down core (`install.sh`, daemons, telemetry schema, the R3 panic path).
- **Verdict from the operator's screen, mechanism from the log** — don't crown a fix from one run or a mid-process read; the noise floor on this title is huge (GT5P ~77–2886 s). Rule out our own code before blaming hardware (bad cable/card).

## Repo / git — TRUNK-BASED protocol (adopted 2026-06-30, operator-directed)
- `origin` = github.com/mercurious/etk. `garage` = etk-garage (private) — **NEVER push `garage` to `origin`**.
- **Work directly on `main`. Do NOT create feature branches by default** — parked branches and
  uncommitted work get stranded and wiped (this is how the 0.5.0 input_d chords were lost; see
  [[feedback_push_promptly_origin_churns]]). Branches are for genuinely risky/experimental spikes
  ONLY, and even then push the branch to `origin` immediately (never local-only) and delete after merge.
- **Commit + push to `origin/main` are PRE-AUTHORIZED** for any change the operator has validated —
  do it the SAME session, no need to ask each time. The done-bar is **"pushed to origin," not
  "committed locally."** Commit in logical chunks with clear messages.
- **`origin/main` receives out-of-band edits** (GitHub web-editor commits) and history rewrites.
  A push may be rejected as stale → **`git fetch` + `git rebase origin/main`, then push. NEVER
  force-push** (force is what clobbers others' work / causes the losses).
- Still ask before: force-pushing, anything touching `garage`, history rewrites on `origin`, or
  destructive ops (hard resets that discard work, branch deletion of unmerged work).
- Public history was legal-scrubbed (turnip fair-use abandoned, distribution-intent dossiers purged) — keep new docs development/tuning-focused, not distribution-intent.

## Target rig (verify against `AI_MANIFEST.md` / `README.md` — may drift)
- SM8250 / Adreno 650 (Retroid Pocket Flip 2), ROCKNIX nightly + Mesa Turnip + RPCS3; primary target title GT5P Spec III (NPEA00050).
- Cockpit skill drives the live rig (adb=Android / ssh=ROCKNIX). Stage IV = forking Mesa Turnip (see `dossiers/`).
