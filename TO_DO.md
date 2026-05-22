# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## BUGS
- ETK still not perfectly classifying CLEAN vs Crash
- ETK still not perfectly loading last launched game
- Enable MangoHUD without forcing user to use Rocknix advanced game settings > system to enable it, so find the advanced-game-settings config and tweak it, but when?
- New game onboarding/discovery isn't getting the right settings template, can't be fixed with pitstop; need to setup a default template for all new games or fix my emulator?
- Add audio codec from emulator menu and reconcile in-game edits with post-pre-game edits.
after install complete to clear folder for another game)
- Still finding orphan shaders in /vault so can't blame an old ETK script


## PROPOSALS

- Add TOOLS tab to ETK PITSTOP with a Install (.pkg not .iso) feature that looks inside `/roms/tmp/rpsc3_install/` (only support one game at a time and auto-delete .pkg .rap 


## PROPOSALS IN PROGRESS

# New Simple Telemetry Menu
`|DAY|TIME|DURATION|GAMEID|STATUS|DRAIN|AVG/PEAK:TEMP|AVG/PEAK:LOAD|AVG/PEAK:RAM|NEW:SHADERS|`
`|TODAY|8:45am|NPUA80075|1m34s|RECOVERY:Adreno Freeze|-5%|74°C/80°C|6/10|76%/91%|0|`
`|TODAY|8:47am|NPUA80075|-----|CONFIG: Multithreaded RSK: ON|`
`|TODAY|8:49am|NPUA80075|2m20s|CLEAN|-3%|75°C/79°C|8/9|77%/90%|10|`
`|20260518|12:34pm|NPUA80075|23s|RECOVERY:RPCS3 Fault Type:0F033A|-5%|74°C/80°C|6/10|76%/91%|0|`

# New Simple Telemetry Detail (AI-Synthesized)
'Today at 8:49am you ran Gran Turismo Prologue for longer than usual after tweaking Multithreaded RSK and the lap ran 3° cooler than ususal with -2% less load and -1% easier on the RAM but burned more battery than typical, +3% more than an average clean run. Compared to last week's stints, the **SPU Preferred Threads** bumped up from 3 to 4 and **Accurate RSX Reservation Access** have been improving your duration durability by 44% compared to before adjusting these dials.'

