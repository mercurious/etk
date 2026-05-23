# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## BUGS
- Check spycraft tool, it's still running?

- New game install, GTA San Andreas and VAULT:ERROR appears on first load, might need to trap a bootstrap condition instead, second launch shows empty vault correctly

- Still finding orphan shaders in /vault so can't blame an old ETK script

## Enhancements

- Add a character highlight in ledger for sessions that resulted in shaders despite a crash to highlight their productivity vs sessions that crash without shaders

- Add a second HUD sequence during loading to briefly show instrumentation labels
1.(30s)  ETK|FULL|NPUA80075|73°C|5.60|34%|34MB 345 0+
1.(30s)  ETK|TEMP: 75°C|CORES: 6.60|MEM: 45%|SHDRS: 34MB 345 0+
1.(hide) ETK|78°C|6.70|46%|34MB 345 0+



## PROPOSALS IN PROGRESS

# New Simple Telemetry Menu
`|DAY|TIME|DURATION|GAMEID|STATUS|DRAIN|AVG/PEAK:TEMP|AVG/PEAK:LOAD|AVG/PEAK:RAM|NEW:SHADERS|`
`|TODAY|8:45am|NPUA80075|1m34s|RECOVERY:Adreno Freeze|-5%|74°C/80°C|6/10|76%/91%|0|`
`|TODAY|8:47am|NPUA80075|-----|CONFIG: Multithreaded RSK: ON|`
`|TODAY|8:49am|NPUA80075|2m20s|CLEAN|-3%|75°C/79°C|8/9|77%/90%|10|`
`|20260518|12:34pm|NPUA80075|23s|RECOVERY:RPCS3 Fault Type:0F033A|-5%|74°C/80°C|6/10|76%/91%|0|`

# New Simple Telemetry Detail (AI-Synthesized)
'Today at 8:49am you ran Gran Turismo Prologue for longer than usual after tweaking Multithreaded RSK and the lap ran 3° cooler than ususal with -2% less load and -1% easier on the RAM but burned more battery than typical, +3% more than an average clean run. Compared to last week's stints, the **SPU Preferred Threads** bumped up from 3 to 4 and **Accurate RSX Reservation Access** have been improving your duration durability by 44% compared to before adjusting these dials.'

