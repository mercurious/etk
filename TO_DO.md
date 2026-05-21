# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## PROPOSALS TO DO

### IN-PROGRESS: DEVELOP ALT GITHUB CONNECTED ACCESS REPLACEMENT TOOLS
- If we cannot get Github Connected Apps provisioned because of license limitations, what tool can integrate into the ETK to get closer to a GitHub enabled workflow despite not having full access, accepting certain limitations, but being clever about working around them?


### PLAN FOR A REFACTOR FOR FULLY ONBOARD ETK
- Go headless and move away from a tethered, commander.sh dependent rig
- Preserve and enrich commander.sh as dev tool rather than used during harvesting runs, more for crash analytics and to continue to support the overhaul of on-board systems
- Trap R3 as a PANIC BUTTON that calls Recovery command

### Native Rocknix Config Editor App
- see pit_wall_sync.sh
- needs reformatting for Flip 2 Rocknix actual resolution 100x font size or smaller terminal resolution
- otherwise shows massive potential
- needs install.sh and uninstall.sh provisioning without breakage
- tested but rolled back after token burnout and stability regression

# Turnip and System Settings

# Force Turnip to prioritize throughput over power saving
`export TU_DEBUG=sysmem,gmem,noconstcheck`
`export MESA_VK_WSI_PRESENT_MODE=mailbox`

# Force Turnip to prioritize throughput over power saving
`export TU_DEBUG=sysmem,gmem,noconstcheck`
`export MESA_VK_WSI_PRESENT_MODE=mailbox`

`
# 1. Thermal & Clock Lockdown
echo "performance" | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
echo "performance" | tee /sys/class/kgsl/kgsl-3d0/devfreq/governor
echo 670000000 > /sys/class/kgsl/kgsl-3d0/devfreq/max_freq

# 2. Turnip 26.0.6 Special Directives
export TU_DEBUG=sysmem,gmem,noconstcheck
export MESA_VK_WSI_PRESENT_MODE=mailbox
export MESA_EXTENSION_OVERRIDE="-VK_KHR_variable_pointers"

# 3. Memory Management for the Save Process
echo 1024 > /proc/sys/vm/max_map_count
sysctl -w vm.swappiness=1`


# New Simple Telemetry Menu
`|DAY|TIME|DURATION|GAMEID|STATUS|DRAIN|AVG/PEAK:TEMP|AVG/PEAK:LOAD|AVG/PEAK:RAM|NEW:SHADERS|`
`|TODAY|8:45am|NPUA80075|1m34s|RECOVERY:Adreno Freeze|-5%|74°C/80°C|6/10|76%/91%|0|`
`|TODAY|8:47am|NPUA80075|-----|CONFIG: Multithreaded RSK: ON|`
`|TODAY|8:49am|NPUA80075|2m20s|CLEAN|-3%|75°C/79°C|8/9|77%/90%|10|`
`|20260518|12:34pm|NPUA80075|23s|RECOVERY:RPCS3 Fault Type:0F033A|-5%|74°C/80°C|6/10|76%/91%|0|`

# New Simple Telemetry Detail (AI-Synthesized)
'Today at 8:49am you ran Gran Turismo Prologue for longer than usual after tweaking Multithreaded RSK and the lap ran 3° cooler than ususal with -2% less load and -1% easier on the RAM but burned more battery than typical, +3% more than an average clean run. Compared to last week's stints, the **SPU Preferred Threads** bumped up from 3 to 4 and **Accurate RSX Reservation Access** have been improving your duration durability by 44% compared to before adjusting these dials.'

