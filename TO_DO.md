# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## BUGS
- Check spycraft tool, it's still running?

- New game install, GTA San Andreas and VAULT:ERROR appears on first load, might need to trap a bootstrap condition instead, second launch shows empty vault correctly

- Still finding orphan shaders in /vault so can't blame an old ETK script

- **PANIC sessions credit zero shaders harvested (GT6, 2026-05-26).** Orphan PANIC synthesis in `01-etk-sentry.sh` hardcodes the 12th `shaders_harvested` column to `0` because SHM (`vault_new.txt`) was wiped by the reboot — so a session that compiled hundreds of new shaders before the panic reads as zero-yield, understating productive crashes in the ledger. **Proposed fix:** persist an ignition-time vault file-count baseline alongside `$SESSION_ANCHOR` in `$TELEMETRY_DIR` (e.g. `vault_baseline.txt`, written when the breadcrumb is written, removed by `session_postmortem.sh` on clean RUNNING→IDLE rollup). On orphan synthesis at next boot, count current vault files for `$BC_GAME` and subtract the baseline → that delta is the panic'd session's harvest. Conservative bound: if the count went down (e.g. operator ran `vault_sweep.sh` mid-panic-window), fall back to `0` rather than report a negative. Mirrors the `$SESSION_ANCHOR` breadcrumb pattern that already closes the orphan-row gap.

- **MangoHUD native BATT broken on Rocknix nightly-20260520:** reads ~50% high vs Rocknix front-end, plugged-in/charging icon stale and doesn't update accurately. Workaround applied locally (hidden BATT, swapped in GPU + frametime graph). Investigate whether MangoHUD package itself needs bumping, our `config/MangoHud.conf` syntax needs updating, or Rocknix changed the power-supply sysfs paths. Hold the GPU/frametime swap as a separate eval — they may be keepers for the HUD DDU even after BATT is fixed. (See `RocknixNightlyMigrationCloseout.md` §4.2.)

## Enhancements

- Add a character highlight in ledger for sessions that resulted in shaders despite a crash to highlight their productivity vs sessions that crash without shaders

- Add a second HUD sequence during loading to briefly show instrumentation labels
1.(30s)  ETK|FULL|NPUA80075|73°C|5.60|34%|34MB 345 0+
1.(30s)  ETK|TEMP: 75°C|CORES: 6.60|MEM: 45%|SHDRS: 34MB 345 0+
1.(hide) ETK|78°C|6.70|46%|34MB 345 0+



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

