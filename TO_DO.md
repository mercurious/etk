# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## BUGS
- Check spycraft tool, it's still running?

- New game install, GTA San Andreas and VAULT:ERROR appears on first load, might need to trap a bootstrap condition instead, second launch shows empty vault correctly

- Still finding orphan shaders in /vault so can't blame an old ETK script

- **PANIC sessions credit zero shaders harvested (GT6, 2026-05-26).** Orphan PANIC synthesis in `01-etk-sentry.sh` hardcodes the 12th `shaders_harvested` column to `0` because SHM (`vault_new.txt`) was wiped by the reboot — so a session that compiled hundreds of new shaders before the panic reads as zero-yield, understating productive crashes in the ledger. **Proposed fix:** persist an ignition-time vault file-count baseline alongside `$SESSION_ANCHOR` in `$TELEMETRY_DIR` (e.g. `vault_baseline.txt`, written when the breadcrumb is written, removed by `session_postmortem.sh` on clean RUNNING→IDLE rollup). On orphan synthesis at next boot, count current vault files for `$BC_GAME` and subtract the baseline → that delta is the panic'd session's harvest. Conservative bound: if the count went down (e.g. operator ran `vault_sweep.sh` mid-panic-window), fall back to `0` rather than report a negative. Mirrors the `$SESSION_ANCHOR` breadcrumb pattern that already closes the orphan-row gap.

- **MangoHUD native BATT broken on Rocknix nightly-20260520:** reads ~50% high vs Rocknix front-end, plugged-in/charging icon stale and doesn't update accurately. Workaround applied locally (hidden BATT, swapped in GPU + frametime graph). Investigate whether MangoHUD package itself needs bumping, our `config/MangoHud.conf` syntax needs updating, or Rocknix changed the power-supply sysfs paths. Hold the GPU/frametime swap as a separate eval — they may be keepers for the HUD DDU even after BATT is fixed. (See `RocknixNightlyMigrationCloseout.md` §4.2.)

- **R3 panic-button hardening — GT6 surfaces failure modes GT5P never did (2026-05-26).** GT6 panic frequency is empirically higher than GT5P's (Deep Forest stress session caught this), exposing R3's incomplete coverage against the diverse panic-state space. Current behavior: single R3 press fires `recovery.sh` IFF input_d.py is still listening on `/dev/input/event9`. Failure modes worth probing: (a) input_d.py killed by the panicking process tree before R3 can fire — Sentry respawns within ~2s but R3 may be pressed in the window; (b) `/dev/input/event*` node disappears or remaps mid-panic (InputPlumber may re-enumerate); (c) full kernel oops where userspace never gets the press at all — only escape is hard-power-cycle; (d) RPCS3 zombie state where the process is "alive enough" to keep CUR_STATE=RUNNING but unresponsive, so the Sentry's RUNNING→IDLE postmortem never fires even after recovery.sh tears it down. **Probe protocol:** deliberately trigger 5 GT6 panics (Deep Forest is reliable — tunnel exit + shader storm), press R3 in each, classify outcomes (R3 caught it / R3 visible-but-ineffective / R3 totally dead → power-cycle needed). **Hardening candidates ranked after probe:** (1) Sentry tightens input_d.py watchdog tick from current Sentry loop sleep to a dedicated faster respawn check; (2) recovery.sh adds defensive pkill -9 -f rpcs3 fallback chain instead of assuming clean teardown; (3) document the hard-power-cycle escape valve as the "R4" (10-second power button hold) in README so users know R3 is the soft-recovery, not the only recovery; (4) consider whether a kernel-level watchdog (`/dev/watchdog`) could auto-reboot after N seconds of unresponsiveness so the user doesn't have to think about it. **DO NOT** add a long-press / double-tap guard to R3 itself — memory `project_headless_refactor.md` and the load-bearing comment at `input_d.py:6` both prohibit that change.

## Enhancements

- Add a character highlight in ledger for sessions that resulted in shaders despite a crash to highlight their productivity vs sessions that crash without shaders

- Add a second HUD sequence during loading to briefly show instrumentation labels
1.(30s)  ETK|FULL|NPUA80075|73°C|5.60|34%|34MB 345 0+
1.(30s)  ETK|TEMP: 75°C|CORES: 6.60|MEM: 45%|SHDRS: 34MB 345 0+
1.(hide) ETK|78°C|6.70|46%|34MB 345 0+

- **`input_d.py` SELECT-clutch chord pass-through refinement (DOWNGRADED 2026-05-26 after L1-screenshot workaround landed).** Track-probe data captured 2026-05-26 (etk_NPEA00502_20260526_11252* screenshot series) confirms SELECT pass-through cycles GT6's camera view per chord press — VAULT/HUD-reload/screenshot all affected. **The pragmatic workaround shipped:** L1 as a single-button screenshot trigger with a one-time per-game onboarding step to unbind L1 in PS3 game controls (GT5P/GT6 default = rear-view camera, which doesn't render on Turnip anyway — a free trade). SELECT+D-pad-Up retained as UI/menu fallback. This makes the deeper refactor lower priority. **If revisited:** option (a) `EVIOCGRAB` while clutch=true is cleanest but kills SELECT-as-camera entirely when ETK runs; option (b) tap-vs-hold discrimination (suppress SELECT release only when a chord direction lands before release) preserves both behaviors at the cost of timing complexity; option (c) alt modifier — exhausted, no game-free button left. Pick (b) if the L1 onboarding ask proves friction-heavy with alpha testers; otherwise leave as-is.



## PINNED FOR NEXT SESSION (POST-MIGRATION)

- **GT5P shader harvest protocol write-up.** The operator's systematic sequence — track time trials → pit-crew animations → another level for camera pans → dealership for all cars → back to tracks for single-race camera pans — should be documented as a first-class harvest playbook. Captures the "productive crashing" UX argument and gives future operators (and AI) a reproducible procedure for stress-testing the rig. Candidate location: `dossiers/Gt5pHarvestProtocol.md`.

- **Validate `tools/vault_sweep.sh` on a real Mesa-rebuild boundary.** Tool ships with mtime-vs-rebuild-boundary algorithm (deviation from addendum §G.5's index-parse spec — see tool header for rationale). On the next Rocknix nightly that bumps `libvulkan_freedreno.so`, run `install.sh` (Step 0 logs `MESA REBUILD DETECTED` and updates `vault/.last_mesa.hash` mtime), play one short session to seed post-rebuild shaders, then `bash vault_sweep.sh` on the rig to confirm: (a) orphan count > 0 and roughly matches the pre-rebuild file count for that game, (b) `--apply` reclaims the expected MB, (c) the post-session shaders survive (newer than the cutoff). If the heuristic over-sweeps, fall back to "Defer sweep tool" and trace Mesa source for the index format.

## PROPOSALS IN PROGRESS

# New Simple Telemetry Menu
`|DAY|TIME|DURATION|GAMEID|STATUS|DRAIN|AVG/PEAK:TEMP|AVG/PEAK:LOAD|AVG/PEAK:RAM|NEW:SHADERS|`
`|TODAY|8:45am|NPUA80075|1m34s|RECOVERY:Adreno Freeze|-5%|74°C/80°C|6/10|76%/91%|0|`
`|TODAY|8:47am|NPUA80075|-----|CONFIG: Multithreaded RSK: ON|`
`|TODAY|8:49am|NPUA80075|2m20s|CLEAN|-3%|75°C/79°C|8/9|77%/90%|10|`
`|20260518|12:34pm|NPUA80075|23s|RECOVERY:RPCS3 Fault Type:0F033A|-5%|74°C/80°C|6/10|76%/91%|0|`

# New Simple Telemetry Detail (AI-Synthesized)
'Today at 8:49am you ran Gran Turismo Prologue for longer than usual after tweaking Multithreaded RSK and the lap ran 3° cooler than ususal with -2% less load and -1% easier on the RAM but burned more battery than typical, +3% more than an average clean run. Compared to last week's stints, the **SPU Preferred Threads** bumped up from 3 to 4 and **Accurate RSX Reservation Access** have been improving your duration durability by 44% compared to before adjusting these dials.'

