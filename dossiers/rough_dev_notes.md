# New nightly
nightly-20260614 Pre-release
Changelog since last nightly:

gamescope: bump version fix wlroots submodule (by tiopex)
rocknix-fake-suspend: unfreeze before shutdown (by John Williams)
sm8250: gamepad fixes (by sunshineinabox)
sm8750: fix panel initialization in gamescope (by tiopex)
sm8550/ayaneo: add displayport audio support (by JS Deck)
abl: bump to 1.1.1 (by sunshineinabox)
sm8250: fixed regulator rumble (by sunshineinabox)
rocknixinfo: prevent SN hang (by sunshineinabox)
sm8250: Charger driver (by sunshineinabox)
sm8250: enable rumble (by sunshineinabox)
panel: unify regulators (by sunshineinabox)
sm8250: merge pm driver (by sunshineinabox)
sm8250: Mangmi Pocket Max (by sunshineinabox)



# ETK TOOLS > Update Games Feature
Integrate https://github.com/RainbowCookie32/rusty-psn

## UI/UX:
 ROCKNIX ES > TOOLS > ETK PITSTOP > TOOLS > Update Games > Lists games eligible for update > Select Game > Start update > mako notifications during entire process with sub-process updates.
 
# ETK TOOLS > Manage Shaders Feature
Integrate `etk/tools/vault_sweep.sh`

## UI/UX:
ROCKNIX ES > TOOLS > ETK PITSTOP > TOOLS > Manage Shaders > Shader Screen

### Shader Screen
  - Graph of fresh vs. stale shaders by MESA Turnip layer
  - [Sweep Shaders (XXMB saved)] -confirm
  - [Delete Vault (XXMB saved)] -confirm
  - [Clear Shader Cache (XXMB saved)] -confirm

### To do feature in future session
  - [Merge Shaders] with [eligible GAMEID]
     - scrape public catalogs of gameIDs and their title relations to enable automatic inference of which games can probably share shaders, because they are regional variants or version variants
  
# Handle installation of titles with same names, add region code to name.

# Reorder TELEMETRY TUNING TOOLS PADDOCK
DONE - so triggering of network pull is not on the way to tools 


# Overheat While Charging
DONE - need to recalibrate and let rig get hotter during charging too many overheats  
  

## Race Engineering
More technically, ETK is a custom Rocknix middleware composed of shell scripts and python curses that employ brute-force optimization, shader cache management, advanced in-game telematics, on-board screenshot tooling that includes the MangoHUD overlay, operates an automated file-drop headless install of PS3 PKG installations inside of RPCS3, and automatically archives shaders into an optional private unshared cloud 

## ETK Features
1. Native Rocknix ETK Pitstop App for on-device config editing, per game telemetry analysis over time, simple PS3 game installation (drop .pkg and .rap in `roms/etk/pkg_install_drop/`), and optional private shader repo.






5. Reboot, try a game, expect some shader storms, crashes, keep at it. It gets better every attempt. ETK has you covered.



"ETK private garage — PADDOCK development + personal vault distribution, pending legal review of public shader sharing" -let's continue to remove the term LEGAL wherever it cropped up.
  
