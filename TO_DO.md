# ALL ITEMS BELOW ARE NOT YET IMPLEMENTED
- Do not consider anything below this section as implemented, only in the proposal, planning, discussion phases, do not assume anything about this last section.

## PROPOSALS TO DO

### IN-PROGRESS: DEVELOP ALT GITHUB CONNECTED ACCESS REPLACEMENT TOOLS
- If we cannot get Github Connected Apps provisioned because of license limitations, what tool can integrate into the ETK to get closer to a GitHub enabled workflow despite not having full access, accepting certain limitations, but being clever about working around them?


### PLAN FOR A REFACTOR FOR FULLY ONBOARD ETK
- Go headless and move away from a tethered, commander.sh dependent rig
- Preserve and enrich commander.sh as dev tool rather than used during harvesting runs, more for crash analytics and to continue to support the overhaul of on-board systems
- Correct the location of `scripts/mango_bridge.sh` into `bin/` where daemons are expected to live as the ETK goes headless and increasingly event-based.
- Trap R3 as a PANIC BUTTON that calls Recovery command

### Native Rocknix Config Editor App
- see pit_wall_sync.sh
- needs reformatting for Flip 2 Rocknix actual resolution 100x font size or smaller terminal resolution
- otherwise shows massive potential
- needs install.sh and uninstall.sh provisioning without breakage
- tested but rolled back after token burnout and stability regression