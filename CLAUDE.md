# CLAUDE.md — ETK operating context (auto-loaded every session)

**`TRACK_MANUAL.md` is the ONE system manual.** (`AI_MANIFEST.md` is retired — a tombstone
pointer; git history is its archive.) Don't re-derive documented facts live: front-load.

## Session start
1. Read `TRACK_MANUAL.md` **§0 OPERATOR LOOPS** (the charter: every loop ends at a SURFACE,
   and a change is not done until that surface shows it) and skim §1–§2 (non-negotiables,
   rig map).
2. Then read, in depth, **the modality section for today's workstream** — and only it:
   - **§A BUILDING the kit** — forging binaries, deploying, Pitstop/UI work, roadmap.
   - **§B USING the kit** — debugging a game: collecting/analyzing/distrusting telemetry.
   - **§C RELEASING the kit** — voice, evidence, naming, packaging.
   §F = falsified (never re-propose) · §Q = paths/BusyBox/diagnostics quick reference.
3. `git status` scope-check: uncommitted/untracked files may be parked experiments. When
   the task and the diff diverge, ask which files are in scope before absorbing them.
4. The rig is always ready (USB-net `169.254.170.2` / `root@SM8250.local`) — don't burn a
   call confirming reachability. New regression tests must run against the BROKEN version
   too (host GNU ≠ BusyBox), or they don't discriminate.

## Non-negotiables (full text with incidents: manual §1)
- **BYTES-TO-ATOMS — always defer to the humans at the threshold (§1.1).** Three moments
  turn bytes into atoms; at each a HUMAN decides: **deploy** (`install.sh`/`uninstall.sh`/
  the PS port — the rig can be bricked) · **mint** (`forge.sh`, any `lane_*.sh` or build on
  etk-cloud/colima — someone else's computer, can trigger an invoice) · **publish**
  (`gh release`, cutting/moving a tag, any upload to a fetch path — our bytes land on other
  humans' machines; the PRODUCT, not the source: ordinary dev pushes to `origin/main` are
  normal). `--dry-run`/`--status` are NOT exemptions. The test for a tool not on the list:
  could it brick hardware, spend someone's money, or reach another human's computer? Any
  yes = hand it over; when unsure, it crosses — ask.
- **The operator runs the controls; Claude preps and hands off.** Never reboot the rig
  remotely; never run install/forge/publish "to check." A handoff ends with the exact
  command in its own ```bash fenced block (ONE command, no `$`) so the operator gets a Run
  button — say what it does, what to watch, what would falsify it. A handoff without a
  runnable prompt is unfinished.
- **Always-reboot gate** — every tune/config change must survive a COLD boot; `/storage`
  persists, nothing else does.
- **Use the kit, to its FULL capability** — ETK tools and the skills' own automation (e.g.
  the cockpit skill's spotter loop for any "watch until X" task) before bespoke ssh
  surgery. This names the MECHANISM a change travels through — never permission to run a
  bytes-to-atoms tool yourself. Any script worth running twice is a TOOL: repo +
  install.sh push list + a surface, from its first run.
- **Validate before integrate** — disposable on-rig harness, cold-booted, before touching
  locked-down core (install.sh, daemons, ledger schema, the R3 panic path).
- **Verdict from the operator's screen, mechanism from the log** — no crowns from one run
  (GT5P noise floor 77–2886 s); rule out our own code before blaming hardware.

## Git — TRUNK-BASED (manual §1.6)
- `origin` = github.com/mercurious/etk; `garage` = private — **NEVER push garage to
  origin**. Work on `main`; feature branches only for risky spikes, pushed immediately.
- Commit+push to `origin/main` **pre-authorized** for operator-validated changes, same
  session; done-bar = *pushed*. Stale push → `fetch` + `rebase`; **never force-push**.
  Ask before: force-push, garage, history rewrites, destructive ops.
- Public artifacts under the **mercurious** pseudonym only; docs stay development/tuning-
  focused; never republish the `etk.conf` PAT.

## The rig (map: manual §2)
SM8250 / Adreno 650 (Retroid Pocket Flip 2), ROCKNIX + Mesa Turnip + RPCS3, whole stack
forked and owned; primary title GT5P Spec III (NPEA00050). Cockpit skill drives the live
rig (adb=Android / ssh=ROCKNIX).
