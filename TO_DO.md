# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## BUGS
- Check spycraft tool, it's still running?

- New game install, GTA San Andreas and VAULT:ERROR appears on first load, might need to trap a bootstrap condition instead, second launch shows empty vault correctly

- Still finding orphan shaders in /vault so can't blame an old ETK script

- **MangoHUD native BATT broken on Rocknix nightly-20260520:** reads ~50% high vs Rocknix front-end, plugged-in/charging icon stale and doesn't update accurately. Workaround applied locally (hidden BATT, swapped in GPU + frametime graph). Investigate whether MangoHUD package itself needs bumping, our `config/MangoHud.conf` syntax needs updating, or Rocknix changed the power-supply sysfs paths. Hold the GPU/frametime swap as a separate eval — they may be keepers for the HUD DDU even after BATT is fixed. (See `RocknixNightlyMigrationCloseout.md` §4.2.)

## Enhancements

- Add a character highlight in ledger for sessions that resulted in shaders despite a crash to highlight their productivity vs sessions that crash without shaders

- Add a second HUD sequence during loading to briefly show instrumentation labels
1.(30s)  ETK|FULL|NPUA80075|73°C|5.60|34%|34MB 345 0+
1.(30s)  ETK|TEMP: 75°C|CORES: 6.60|MEM: 45%|SHDRS: 34MB 345 0+
1.(hide) ETK|78°C|6.70|46%|34MB 345 0+



## PINNED FOR NEXT SESSION (POST-MIGRATION)

- **`install.sh` Tier-B backup upgrade + stale-shader-sweep tool.** Bundle two related vault-management features into one focused workstream:
  - **Tier-B backup**: implement `ADDENDUM_install_sh_tiered_backup.md` end-to-end (decisions already locked — workstream order, `./state/` host dir + gitignore + §F privacy lock, stale `--update` rsync comments cleaned up, parent dossier §13 softened to point at `--restore-state`).
  - **Stale-shader-sweep tool**: new `tools/vault_doctor.sh stale-sweep` (or extend existing vault_doctor). Reads Mesa's live cache `index` file, identifies cache hashes Mesa actively writes to, deletes orphaned entries from the per-game vault. Addresses the empirical doubling-per-nightly pattern: 20260516→20260520 saw GT5P vault layer once (orphaning ~49k files), 20260520→20260525 layered again (doubling 9.5k→19k in one session). Each Rocknix Mesa rebuild — version string unchanged but `libvulkan_freedreno.so` build ID drifts — invalidates the prior layer. Run on demand post-migration. Manageable during alpha-with-nightlies; less critical once Rocknix ships an official release (long-shelf-life shaders). See `RocknixNightlyMigrationCloseout.md` §2.2.
  - Consider adding install.sh fingerprint-detect: hash `libvulkan_freedreno.so` against `vault/.last_mesa.hash`; on change, emit `MESA REBUILD DETECTED` to `$TRIPWIRE_LOG` so a stale-sweep prompt is one glance away.

- **GT5P shader harvest protocol write-up.** The operator's systematic sequence — track time trials → pit-crew animations → another level for camera pans → dealership for all cars → back to tracks for single-race camera pans — should be documented as a first-class harvest playbook. Captures the "productive crashing" UX argument and gives future operators (and AI) a reproducible procedure for stress-testing the rig. Candidate location: `dossiers/Gt5pHarvestProtocol.md`.

## PROPOSALS IN PROGRESS

# New Simple Telemetry Menu
`|DAY|TIME|DURATION|GAMEID|STATUS|DRAIN|AVG/PEAK:TEMP|AVG/PEAK:LOAD|AVG/PEAK:RAM|NEW:SHADERS|`
`|TODAY|8:45am|NPUA80075|1m34s|RECOVERY:Adreno Freeze|-5%|74°C/80°C|6/10|76%/91%|0|`
`|TODAY|8:47am|NPUA80075|-----|CONFIG: Multithreaded RSK: ON|`
`|TODAY|8:49am|NPUA80075|2m20s|CLEAN|-3%|75°C/79°C|8/9|77%/90%|10|`
`|20260518|12:34pm|NPUA80075|23s|RECOVERY:RPCS3 Fault Type:0F033A|-5%|74°C/80°C|6/10|76%/91%|0|`

# New Simple Telemetry Detail (AI-Synthesized)
'Today at 8:49am you ran Gran Turismo Prologue for longer than usual after tweaking Multithreaded RSK and the lap ran 3° cooler than ususal with -2% less load and -1% easier on the RAM but burned more battery than typical, +3% more than an average clean run. Compared to last week's stints, the **SPU Preferred Threads** bumped up from 3 to 4 and **Accurate RSX Reservation Access** have been improving your duration durability by 44% compared to before adjusting these dials.'

