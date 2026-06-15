# The Emulation Tuning Kit
## For Android
No ETK features, just house tuned.
- Use [aPS3e Shader Patch Edition](https://github.com/mercurious/aps3e/releases) for Android (until main release is updated with cache fix)
- Use [nihui's MESA Turnip drivers](https://github.com/nihui/mesa-turnip-android-driver) for Android during the aPS3e setup wizard.
- Use an ETK config tuning from [Tested Games](https://github.com/mercurious/etk/#tested-games-rpcs3).

## For the full Rocknix rig
The complete high performance system.
- [Download latest release](https://github.com/mercurious/etk/releases)
- [System Requirements](https://github.com/mercurious/etk/#etk-system-requirements)
- [Device Support](https://github.com/mercurious/etk/#handheld-system-support)
- [Tested Games](https://github.com/mercurious/etk/#tested-games-rpcs3)
- [Getting Started](https://github.com/mercurious/etk/#getting-started)

# ETK Introduction
<img src="docs/screenshots/etk_NPEA00502_20260526_194915.png" width="900"
     alt="Gran Turismo 6 chase-cam: Mini Cooper approaching the ivy-covered tunnel at Trial Mountain at 62 mph, fifth place lap 2 of 2, opponents listed; ETK telemetry HUD strip across the top of the frame" />

*GT6 — Trial Mountain, Lap 2 of 2, Position 5/6, Mini Cooper at 62 mph approaching the ivy-covered tunnel. DDU strip on top: `VULKAN 8FPS 126.6ms BATT 49% ETK 79° 12.46 96% 12+ 29.5k 269MB` — backend, framerate, frametime, battery, GPU temp, system load, GPU utilisation, new shaders harvested this session (`12+`), vault total (`29.5k`), live RAM. The `+` is the productive-crashing pitch made literal: the GPU is grinding hard, and the kit is banking every new shader for the next run.*

The Emulation Tuning Kit for Rocknix supports experimental PS3 emulation on ARM64 Retrogaming Handhelds. It excels at tuning games with device-specific emulation configurations while harvesting shaders into vaults. **ETK does not share or distribute shaders** — it manages your own, archived privately to your own GitHub, so your rig can swap games and their vaults on the go without a host computer. It also works by equipping your compatible handheld with special features to become a track day rig to literally and figuratively crash your way into making a game such as Gran Turismo 6 playable. Push your handheld to its limits while collecting shaders with tools to recover from crashes so the game plays well after several attempts. ETK automatically tracks sessions on a per-game race ledger.

## Racing UI
In the style of a race car Driver Data Unit (DDU) dashboard, the ETK instruments provide shader counts in real-time in a custom in-game overlay using MangoHUD support built-in to Rocknix. The kit also adds a custom ETK Pitstop app in the Rocknix Tools carousel menu for on-board telemetry analysis, quick tuning of Adreno-centric emulation settings, and simplified game package installation.

## Race Engineering
More technically, ETK is a custom Rocknix middleware composed of shell scripts and python curses that employ brute-force optimization, shader cache management, advanced in-game telematics, on-board screenshot tooling that includes the MangoHUD overlay, operates an automated file-drop headless install of PS3 PKG installations inside of RPCS3, and automatically archives shaders into an optional private, unshared cloud repository on GitHub.

## Race Durability
Built for abuse and race conditions, the ETK guards hard-earned shaders, custom tunings, screenshots, and game saves from SD card failure, OS flashing, data corruption, device failure, loss or theft. The ETK includes an emergency cooldown that automatically puts your device in PIT mode as needed, protecting your engine from overheating — and **automatically recovers back to racing once it cools, with no reboot required**.

## Gallery
Captured on-device with the ETK's `L1` screenshot shutter — MangoHUD overlay included (which is the whole point; RPCS3's built-in screenshot strips it).

<img src="docs/screenshots/etk_NPEA00502_20260526_124051.png" width="600"
     alt="GT6 chase cam: Mini Cooper at the same Trial Mountain tunnel a different lap, position 2 of 6 at 97 mph, 138 shaders banked this session" />

*GT6 — Trial Mountain, same ivy tunnel as the hero shot but a different race: Position 2/6, Lap 2/2, Mini Cooper at 97 mph. HUD: `VULKAN 15FPS 68.9ms BATT 66% ETK 79° 11.96 96% 138+ 28.6k 261MB` — **138 shaders harvested** by lap 2 because the cache from earlier laps is already paying out.*

<img src="docs/screenshots/etk_NPUA80075_20260526_132352.png" width="600"
     alt="GT5P Suzuka start grid, position 6 of 12 lap 1 of 3, red sedan rival ahead, Honda and Bridgestone trackside signage, speed 101" />

*GT5P — Suzuka Circuit start grid, Position 6/12, Lap 1/3, gear 3 at 101 mph. HUD: `VULKAN 22FPS 45.9ms BATT 48% ETK 75° 7.66 -95% 0+ 19.0k 172MB`. Honda / Bridgestone / Potenza trackside — daylight Suzuka renders cleanly on the cached set.*

<img src="docs/screenshots/etk_NPUA80075_20260526_132550.png" width="600"
     alt="GT5P Suzuka chase cam, blue Nissan Skyline GT-R approaching a sweeping corner, mini-map visible, position 12 of 12 lap 2 of 3" />

*GT5P — Suzuka, Position 12/12, Lap 2/3, blue Skyline GT-R at 71 mph into a sweeper. HUD: `VULKAN 23FPS 44.4ms BATT 46% ETK 75° 9.03 97% 0+ 19.0k 172MB`. Open daylight track on a saturated shader set is where the kit feels most like a stock console.*

<img src="docs/screenshots/etk_NPUA80075_20260526_132301.png" width="600"
     alt="Gran Turismo 5 Prologue 'My Page' menu showing a player's garage with eight cars; current car highlighted as Nissan Skyline GT-R V-spec II Nür '02" />

*GT5P — My Page / Garage. Player profile and car collection (`Skyline R34 GT-R V-spec II '02` selected). HUD: `VULKAN 18FPS 54.7ms BATT 49% ETK 72° 6.69 -96% 0+ 19.0k 172MB` — menus run on the cached shader set.*

<img src="docs/screenshots/etk_NPEA00502_20260526_170526.png" width="600"
     alt="GT6 Nürburgring Nordschleife at night, cockpit view headlights catching a white opponent car ahead through a dark turn; ETK HUD on top showing 132 shaders" />

*GT6 — Nürburgring Nordschleife at night, Lap 1 of 2, cockpit view at 40 mph into a moonlit corner. HUD: `VULKAN 8FPS 131.9ms BATT 54% ETK 79° 11.99 96% 132+ 29.4k 269MB`. The Green Hell at night, on a handheld, running PS3. **A clean lap landed 2026-05-26.***

## ETK System Requirements
ETK is certified against **ROCKNIX nightly `20260610`** on SM8250 (Retroid Pocket Flip 2), with a hard architectural floor at 20260520 (DS5 gamepad era). The nightly pin is deliberate: nightly-20260610 ships RPCS3 `0.0.41-19444`, which contains the upstream Gran Turismo 5 memory-leak fix ([RPCS3 #18819](https://github.com/RPCS3/rpcs3/issues/18819), ~300 MB leaked per car viewed — fatal on an 8 GB handheld and the prime suspect behind the former dominant "silent crash" class), plus Mesa Turnip 26.1.2 and kernel 7.0.11. Official release `20260601` predates the fix. The race-stability bar — five consecutive crash-free runs of the same target race to a graceful emulator exit — has been **cleared on GT5 Prologue** (best streak: 16 crash-free sessions / 8 back-to-back clean finishes). That result was earned on a **saturated** shader vault; it is **not yet consistently reproducible from a fresh install**, where the rig re-enters the harvest cycle and crashes until the cache re-saturates. Race-stable is proven *reachable*, not guaranteed every session.
| Type | Detail |
|---|---|
| Host System | macOS or Linux native ([Windows/PC port](#windows-install-guide)) |
| OS | ROCKNIX (Nightly: 20260610) |
| Driver | MESA Turnip 26.1.2 |
| Shell |  BusyBox v1.36.1 |
| Custom Overlay |  MangoHUD |

## Handheld System Support
The kit ships with one calibrated device profile (`SM8250`) which architecturally covers every SD865 / Adreno 650 / Turnip handheld. Only the Flip 2 has been on-rig verified so far; the other SD865 devices share the chipset and should run on the same profile, but each needs a real-world calibration pass to confirm thermal headroom and panel DPI. The RP6 needs a new SM8550 profile and a separate shader vault (Adreno 740 produces different cache binaries).

| Make | Model | Chipset | Profile | Status |
|---|---|---|---|---|
| Retroid Pocket | Flip 2 | SM8250 Adreno 650 | `SM8250` | 🏁 Verified |
| Retroid Pocket | 5 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | Mini V2 | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| AYN | Thor Lite | SM8250 Adreno 650 | `SM8250` | Expected (untested) |
| Retroid Pocket | 6 | SM8550 Adreno 740 | (needs new profile + vault) | Not yet supported |

## Tested Games (RPCS3)
Snapshot of per-game status from on-device testing with links to tuned RPCS3 configurations. 

| ID | Game Config | Status | FPS | Audio| Notes | Vault Size |
|---|---|---|---|---|---|---|
| NPEA90002 | [Gran Turismo HD Concept](config/config_NPEA90002.yml) | Playable, crashes rarely | 20–30 | Good (some race stutter) | Ideal baseline — small, fast, durable. Black sky artifacting; menus SPU-sensitive. | 2k 20MB |
| NPUA80075 | [Gran Turismo Prologue](config/config_NPUA80075.yml) | Playable, crashes occasionally | 12-30 | Menus only | Primary ETK target. Track surface flickers; Unstable over time. | 11k 100MB |
| BCUS98114 | [Gran Turismo 5](config/config_BCUS98114.yml) | Semi-playable, crashes frequently | 30 | Menus and brief 30FPS track | Tracks often kernel-panic. Menus stable, eventual freeze. | 12.k 110MB |
| NPEA00502 | [Gran Turismo 6](config/config_NPEA00502.yml) | Playable, crashes occasionally | 8-12 | Menus good | Full Nürburgring Nordschleife lap clean 2026-05-26. Tuning for FPS is deferred until the shader vault saturates. Rear-view mirror does not render and other glitches. | 139k 1296MB |
| NPUB30457 | [Ridge Racer 7](config/config_NPUB30457.yml) | Playable, crashes rarely | 20-30 | Good (some race stutter) | Very playable, with some crashing | 1.6k 16MB |
| NPUA80472 | [LittleBigPlanet](config/config_NPUA80472.yml) | Playable, crashes rarely | 12-24 | Good (some stutter) | No issues discovered yet beyond shader storm glitching. | 1.6k 20MB |

## ETK Features
1. Native Rocknix ETK Pitstop App for on-device config editing, per game telemetry analysis over time, simple PS3 game installation (drop .pkg and .rap in `roms/etk/pkg_install_drop/`), and an optional private shader repo (PADDOCK tab)
1. Customized in-game overlay dashboard with ETK telematics inside native Rocknix MangoHUD
1. Hardware and driver tunings for maximum performance going beyond config settings
1. Optimized emulator game configurations tuned to the device hardware
1. Smart thermal protection to safely overdrive the device during shader harvesting, with automatic overheat recovery that returns to racing once cooled — no reboot
1. Automatic shader backup from your device to your computer to shield hard-earned work from loss, plus an optional **Private Paddock** — push/pull your shaders, tunes, and saves to your own private GitHub repo straight from the rig over WiFi (self-custody, nothing shared publicly), and **Manage Shaders** to reclaim storage by sweeping shaders stranded by driver updates
1. Pit wall remote terminal screen to monitor and control device (`scripts/commander.sh`)
1. Install, configure, repair, and uninstall the kit remotely from a computer (`install.sh` and `uninstall.sh`) with mDNS autodiscovery of supported devices on your local network.
1. Multi-Installation Options: FULL installation for initial shader harvesting and tuning, LITE installation for saturated shader sets with thermal protection only, RAW for stress testing without shader and thermal protections (`ETK_BUILD_TYPE` in `etk.conf`)

## Getting Started with Rocknix Pro-tips
### To boot into Rocknix running on an SD card with Android as the default OS:
1. Start the device in Android and reboot. 
1. Before Retroid Pocket logo appears, hold down the Volume-Up button and let go as soon as you see the U-Boot logo (a little submarine icon in the corner)
### To always boot into Rocknix as the default OS:
1. Hold Volume-Down button while starting device to open loader menu, the `abl`
1. Use volume buttons to switch modes that include `Restart bootloader`,`Recovery mode`,`Emergency mode`,`Switch Boot mode`,`Power off`,`START`
1. Select `Switch Boot mode` with vol buttons and press `POWER`
1. `BOOT MODE` will switch to `Loader`
1. Select `START` with vol buttons and press `POWER`
1. Use the same process to `Switch Boot mode` back to `Android`.
### To share games between Rocknix and Android:
1. Store your games in `/storage/games-internal/roms/` and see [Rocknix documentation](https://rocknix.org/play/add-games/) for further details.
1. Let your Android apps gain permissions for this folder.
### To access your card after installing Rocknix:
Your PC or Mac will no longer read the card through an SD card reader over USB because of its Rocknix partition. Try one of these options instead: 
1. Use SMB in Windows or macOS to mount SM8250 as a drive
1. Use an SFTP client
1. Use [Rocknix USB-GADGET mode](https://rocknix.org/play/add-games/#option-2-usb-gaget-modes).

# Getting Started
1. [Flash](https://rocknix.org/play/install/) the [ROCKNIX nightly](https://github.com/ROCKNIX/distribution-nightly/releases) certified in [ETK System Requirements](#etk-system-requirements) above (`20260610`) to your handheld's SD card and complete its first-time setup so the rig joins your WiFi. 
If you've already installed Rocknix, switch the update channel to nightly (`START` → `UPDATES & DOWNLOADS`), update to the certified nightly, and let the auto-update complete and reboot first. 
**Do not update past the certified nightly** without checking the latest README.md for the last known ETK-supported Rocknix build.
2. Clone this repo to your computer.
```sh
git clone https://github.com/mercurious/etk
```
- (You can also download the code as a `.zip` and extract as `~/etk/`).
3. Install the ETK onto your handheld rig

**For macOS, WLS2, Linux:** from the repo root
to make it executable
```sh
chmod +x install.sh
```
to install the ETK on your handheld rig
```sh
./install.sh
```
whenever you want to update, repair, or sync your rig, `cd ~/etk` or wherever you keep it
```sh
./install.sh
```

**For Windows,** use the [PowerShell installer](https://github.com/mercurious/etk#windows-install-guide) which is a direct port of `install.sh` but use SMB backup to substitute for its file sync features.

**On the first run the installer auto-discovers your handheld as the rig.** You may be prompted once to accept the rig's SSH host key and enter the default root password unless you've changed it.
Using mDNS, Rocknix advertises itself on the LAN as `<SOC>.local` (e.g. `SM8250.local` for SD865 devices like the Retroid Pocket Flip 2 / Pocket 5).

<img src="docs/screenshots/etk_install_screenshot.png" width="600"
     alt="Pit Wall console TUI mid-install: 6-step dashboard, OVERALL 30%, HARVEST SHADERS at 84%, DATALOG showing 'Mirroring custom_configs'" />

*The `./install.sh` Pit Wall console — `RIG: SM8250.local`, `TIER: FULL`. Six steps deploy bottom-up; the OVERALL bar aggregates them. The 2-line DATALOG at the bottom surfaces what's happening right now without firehosing per-file rsync output. Pass `--verbose` to swap this for the raw rsync stream when something needs diagnosis.*

4. Reboot and start harvesting shaders.

## Removing ETK
- Use the provided `uninstall.sh` or PowerShell port `etk_uninstall.ps1` to remove the ETK from your system.

# ETK Track Manual
Getting installed is the hard part. Now you have a track-day setup to attempt the previously impossible. You might not make it across the finish line your first attempt. But keep at it and you will.

## The Heads-Up Display
Designed to feel like a race car Driver Data Unit style dashboard (DDU). From left-to-right, the instruments are:
frametime|framerate|battery|ETK|temp|load|ram|shaders

### ETK DDU Startup Sequence
The custom display has a 3-step startup sequence. The duration can be edited in `etk.conf`
1. Shows the installation mode of the ETK: FULL, LITE or RAW|the game ID number|and the shader vault being loaded.
2. Shows the labels of the main instruments without shader info.
3. Minimizes instrument labels to include shader count and vault size.

### Gauge Indicators
- The TEMP gauge will show `HOT` when you are getting close to overheating.
  - When you trigger the thermal protection system the device drops into a PIT-mode cooldown and the gauge shows `»COOLDOWN`. **No reboot needed** — once it cools back down the system automatically returns to racing and flashes `RACE OK`.
- The core LOAD and RAM gauges have 3-step meters: `»--` `»»-` `»»»`
   - Don't think of these as proportional to the numbers,
   - Instead, think of these as your "system overhead" and when you are maxed out with all three segments, you are pushing the device to its known limits.

## The ETK Punch Box
Custom buttons to get you around the track at dangerous speeds.
| Gamepad Button | ETK Command | Button Description | Details |
|---|---|---|---|
| `L1` | **single-finger screenshot** | left top trigger button | **Requires in-game un-binding/un-mapping**. Screenshots stored at `/storage/roms/etk/screenshots`, `install.sh` syncs `etk/screenshots`. Disable `L1` one-finger trigger feature in ETK Pitstop > TOOLS > `3. Screeshot on L1: disabled` and use two-handed `SELECT` + `D-pad-up` instead. |
| `L3` | **DDU HUD** | left analog button | Toggles ETK DDU dashboard between top and bottom of screen |
| `R3` | **PANIC RECOVERY** | right analog button | Recover from a crash or freeze. Reboot recommended after returning to Rocknix ES frontend. |
| `POWER` | **Kernel Panic** | device's power button | If you cause a *kernel panic* the ETK Recovery function will not work. Hold the `POWER` button down until the device reboots to the Retroid Pocket logo. |

## The ETK Pitstop Rocknix App
Found in the Rocknix ES front-end Tools carousel item.

### ETK Telemetry
- Career
  - Shows total playtime, number of sessions, percent clean (no crashes), crash stats (recovery/panic)
  - Number of shaders banked, avg shaders per session, clean streak (best streak)
- Ledger: Shows session history at a glance
  - TIME|STATUS|DURATION|RAM|LOAD|TEMP|BATTERY DRAIN|SHADERS HARVESTED
  - Records every session and tuning change from the Tuning tab.
- Session Detail View
  - Clean View: Shows Duration and telemetry summary
  - Crash View: Shows crash type with explanation, peak stats, and **suggested Tuning fixes**.
<img src="docs/screenshots/etk_ROCKNIX_20260531_191734.png" width="600" alt="ETK Pitstop App Clean Detail View" />  
<img src="docs/screenshots/etk_ROCKNIX_20260531_191740.png" width="600" alt="ETK Pitstop App Crash Detail View" />
 
### ETK Tuning
Easily tweak emulation settings on the device. The subset of RPCS3 settings can be customized in `config/pitstop_fields.json`

<img src="docs/screenshots/etk_ROCKNIX_20260526_132607.png" width="600"
     alt="ETK Pitstop TUNING tab for GT5P: RPCS3 settings list including Audio Backend FAudio, PPU Threads 2, Resolution Scale 75, Frame Limit 30, Shader Mode Async Recompiler with Shader Interpreter" />

*ETK Pitstop TUNING tab for GT5P. The on-board subset of RPCS3 settings most relevant to per-game tuning, gamepad-editable in place. The exposed field set is defined in `config/pitstop_fields.json` — extend or trim per device. `B` saves to the per-game config; `L1`/`R1` cycle tabs.*

### ETK Tools
1. Easily install PS3 packages. Follow the on screen instructions and overlays during the automated process.
2. Easily uninstall PS3 packages
3. Configure the screenshot tool's `L1` single-finger camera-shutter feature to work always, on in-game, or never. The `SELECT` + `dpad-up` combo will continue to take ETK style screenshots.
4. **Manage Shaders** — every ROCKNIX nightly rebuilds the graphics driver, which strands the shaders cached against the old build as dead weight (a saturated vault can be >90% stale). This screen shows a per-game fresh/stale graph and lets you **Sweep** the stale orphans to reclaim space, **Delete** a game's whole vault, or **Clear** the RPCS3 cache — each gated behind a confirm.

## How to Install PS3 Games with the ETK
**Note:** The ETK cannot solve the problem of needing to install the PS3 firmware into the emulator. You have to dump that from your console or go to Sony's website and then plug in a mouse to your device and use the RPCS3 application in Rocknix tools to get it installed as a one-time setup.
The ETK solves the problem of installing PS3 Packages on Rocknix which is otherwise a ridiculous process, as indicated above.
1. Place a single PS3 `.pkg` and `.rap` into `/storage/roms/etk/pkg_install_drop/`
1. In Rocknix Tools > ETK Pitstop > TOOLS > Install a staged PS3 Package
1. Wait for the automated process where ETK will handle RPCS3 installation for you and follow the on screen overlay instructions
1. Quit ETK Pitstop after installation and run **Update Gamelists** in Rocknix so the newly-installed PS3 game appears in the PS3 system list. (Note: this does NOT refresh the ETK Pitstop entry itself — that's installed once by `./install.sh` and persisted by the Sentry.)

<img src="docs/screenshots/etk_ROCKNIX_20260526_132606.png" width="600"
     alt="ETK Pitstop TOOLS tab: 'Install a staged PS3 Package' highlighted, 'Uninstall a Game' below; staging drop folder path shown" />

*ETK Pitstop TOOLS tab — headless PS3 `.pkg` installer. Drop one `.pkg` (plus a `.rap` licence if needed) into the staging folder shown, select **Install a staged PS3 Package**, and ETK drives RPCS3 through the install with overlay prompts. Solves the "you can't operate the RPCS3 desktop UI with just a gamepad" problem.*

## How to Use Simple Telemetry
ETK Pitstop's TELEMETRY tab shows the per-game session ledger of the last game launched. To switch the visible game, launch a different game in RPCS3, quit back to ROCKNIX, then reopen ETK Pitstop — it will show that game's career rollup and tuning history. Every session, every crash, and every config change is recorded so you can correlate tuning experiments with outcomes.

**Session detail:** in the TELEMETRY tab, move the row cursor with the **D-pad** and press the **confirm** button to open a full-screen detail card for that session (**back** returns). A clean run shows duration, shaders harvested, and ASCII gauges for temp / load / RAM / battery drain; a crash shows what failed, where it died, and the suggested tuning fix pulled from the crash-signature catalog.

<img src="docs/screenshots/etk_ROCKNIX_20260526_132108.png" width="600"
     alt="ETK Pitstop TELEMETRY tab showing GT5 career: 6 sessions, 50% clean, 3 crashes, 7639 shaders banked, recent session log with RECOVERY:Adreno and config-change events" />

*ETK Pitstop TELEMETRY tab for Gran Turismo 5 (BCUS98114). Career rollup: **6 sessions · 50% clean · 3 crashes · 7,639 shaders banked · +1,273 avg/session**. The session log shows the full ledger schema — duration, RAM peak, load, GPU temp, battery drain, new-shader count, and the recovery signature (`RECOVERY:Adreno` = fence timeout, `RECOVERY:Silent` = soft hang). Config changes are logged inline so every tuning experiment is reproducible.*

## Setting Up the Private Paddock (optional)
The **Private Paddock** is your own personal cloud backup for shaders, a 2GB per-game remote vault that includes your settings and saves, all pushed and pulled straight from the rig to and from **your own private GitHub repo** over WiFi, with no host computer in the loop. ETK ships the tooling; the bytes are yours and are never shared. The **PADDOCK** tab only appears in ETK Pitstop once you've configured a token, so this whole feature is opt-in — leave the token blank and nothing changes.

### 1. Create a GitHub token (and repo)
You need a GitHub Personal Access Token (PAT). Either type works:

- **Classic PAT (easiest — lets ETK create the repo for you).** On GitHub: *Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token*. Tick the **`repo`** scope and generate. You do **not** need to create the repo yourself — `install.sh` will create a private `etk-paddock` repo for you on the first run.
- **Fine-grained PAT (most locked-down).** First create the private repo yourself on github.com (e.g. name it `etk-paddock`, visibility **Private**). Then *Settings → Developer settings → Fine-grained tokens → Generate new token*, scope it to **only that one repo**, and grant **Repository permissions → Contents: Read and write**. (Fine-grained tokens can't create repos, which is why you make the repo first.)

> ⚠️ The repo **must be private**. ETK refuses to use a public repo — a public paddock would publicly *distribute* your vault, which is exactly what ETK is designed not to do.

### 2. Configure `etk.conf`
`etk.conf` lives in the repo root (it's generated on your first `./install.sh` and is gitignored, so your token never leaves your machine). Set:

```sh
PADDOCK_TOKEN="ghp_your_token_here"
# Optional — defaults to <your-github-username>/etk-paddock
PADDOCK_REPO=""
```

Leave `PADDOCK_REPO` blank to accept the default `<token-owner>/etk-paddock`, or set it to `owner/repo` to use a specific repo name.

### 3. Run the installer
```sh
./install.sh
```
The installer's **PADDOCK LINK** step then:
1. Verifies the token with GitHub and derives your username.
2. Finds — or, with a classic `repo`-scope token, **creates** — the private repo.
3. **Refuses to continue** if the repo is public.
4. Seeds an initial commit if the repo is empty (uploads are GitHub Releases, which need a commit to tag).
5. Writes the credential to the rig at `/storage/roms/etk/config/paddock.json` (root-only, `chmod 600`).

If the token is rejected, or the repo is missing and your (fine-grained) token can't create it, the step prints exactly what to fix — correct it and re-run `./install.sh`.

### 4. Use it on the rig
Reboot or relaunch ETK Pitstop and open the **PADDOCK** tab. Each game row shows where its data lives — `LOCAL-ONLY`, `REMOTE-ONLY`, `BOTH`, or `EPOCH-OLD` (a bundle built against a different driver build). Use the **D-pad** to select **PUSH** or **PULL** and press **confirm** to run it.

- **PUSH** uploads that game's vault + config + saves to your paddock, tagged to your current driver build.
- **PULL** brings it back down to the rig — after an SD swap or reflash, or onto a second SM8250 device running the same driver build. Pulled shaders are checked against your live driver (the *homologation gate*): a mismatched bundle installs the config only and skips the stale shaders.

> 💡 Sweep stale shaders with **TOOLS → Manage Shaders** before a PUSH so you bank a lean bundle — a fresh driver build can strand >90% of a vault as dead weight.

To disconnect, clear `PADDOCK_TOKEN` in `etk.conf` and re-run `./install.sh`, or run `uninstall.sh` (which removes the rig credential). Your remote repo is never touched — it's your backup.

## Customizing the ETK
1. The first run generates `etk.conf` from `etk.conf.example`
   - `RIG_SSH` — auto-populated to `root@<SOC>.local`; replace with a literal IP if your LAN blocks mDNS
   - `ETK_BUILD_TYPE` — `FULL` (shaders + thermal + HUD) / `LITE` (thermal + HUD) / `RAW` (HUD only)
   - `DEFAULT_MODE`, `HUD_HEADER_HOLD_S` (HUD launch-banner hold)
   - `ETK_VERBOSE` — 0 = Pit Wall console TUI (default), 1 = raw rsync output for debugging. Pass `--verbose` / `-v` on the install.sh CLI to force verbose for a single run.
2. Re-run `./install.sh` after any `etk.conf` edit to push changes to the rig.
3. Reboot the rig once to activate the ETK Pitstop entry in the Rocknix Tools menu.

## ETK File Structure
- `AI_MANIFEST.md`: System Manual and Immutable Laws of ETK Development for AI
- `LICENSE`: GNU GPL v2.0 — matches Rocknix
- `README.md`: You are reading it now.
- `CHANGELOG.md`: Release notes, newest first.
- `etk.conf.example`: Operator config template (committed). `install.sh` generates `etk.conf` from this on first run.
- `install.sh`: Flashes the ETK onto your handheld from a computer; auto-discovers the rig via mDNS on first run.
- `uninstall.sh`: Removes the ETK from your handheld from a computer; restores stock CPU/GPU governors before exiting.
- `/bin`:
  - `etk_modules_inject.py`: Handles installing the native Rocknix Pitstop app persistently
  - `etk_pitstop.py`: Native Rocknix Tools App — TELEMETRY, TUNING, TOOLS tabs
  - `gamepad_probe.py`: Gamepad inventory + mapping probe (dev utility)
  - `input_d.py`: Handles custom gamepad controls (R3 panic, L3 HUD toggle, L1 screenshot, SELECT chords)
  - `mango_bridge.sh`: Manages live telemetry and the in-game overlay display
  - `recovery.sh`: Headless Nuclear Recovery, invoked on-device by the `R3` panic button
  - `screenshot.sh`: `grim` Wayland capture, invoked by `L1` and `SELECT`+`D-pad Up`
  - `session_postmortem.sh`: Records Simple Telemetry to the Pitstop session ledger on game exit
  - `thermal_d.sh`: Manages system conditions and emergency PIT-mode cooldown
  - `vault_d.sh`: Archives compiled shaders to the per-game vault
- `/config`:
  - `crash_signatures.json`: Defines crash classification patterns for forensics
  - `etk_pitstop.sh`: Native Rocknix app launcher (deployed to `/storage/.config/modules/`)
  - `etk_pitstop.svg`: Native Rocknix app icon
  - `etk_template.yml`: Default RPCS3 per-game config template (used by the TOOLS-tab installer)
  - `MangoHud.conf`: ETK's custom in-game DDU overlay configuration
  - `pitstop_fields.json`: Subset of RPCS3 config keys exposed in the TUNING tab
  - `rsyncd.config`: Optional rsyncd configuration for backup paths
- `/scripts`:
  - `career_aggregate.sh`: Summarises the session ledger into career stats for the Pitstop TELEMETRY tab
  - `commander.sh`: Pit Wall DDU UI for the terminal interface (the live dashboard's voice and look)
  - `env.sh`: Establishes pit and race environment variables; sources the active device profile
  - `etk_probe.sh`: Three-mode thermal / freq probe for empirical thermal calibration
  - `probe.sh`: Provides forensic error logs from RPCS3 + dmesg
  - `/profiles`: Device profiles (one file per SoC family — `SM8250.sh` is the Tier-1 reference for SD865 handhelds)
- `/tools`: Host-side dev utilities. `tui.sh` is the Pit Wall console library shared by `install.sh` and `uninstall.sh`; the rest (`vault_doctor.sh`, `vault_sweep.sh`, `agnostify.sh`, etc.) are operator helpers. `etk_drift.py` runs on the rig to detect Rocknix OS-migration drift — it banks nightly-keyed OS profiles (by `OS_VERSION`, e.g. `20260525`) and diffs a live nightly against the pinned baseline (and against the device profile's assumptions) to decide whether a nightly is safe to adopt.
- `/dossiers`: Design dossiers driving the architecture (device-agnostic profile, rig self-update feasibility, telemetry, etc.). **Local-only — not committed** (gitignored as of 2026-06-14); kept privately at `$ETK_ROOT/dossiers`. Code comments cite these for rationale, but they are not part of the published repo, so they will not appear in a clone.
- `/docs`: Public-facing assets including the screenshot gallery used by this README.
- `/vault`: Local mirror of the harvested shader bank, organised as `vault/<CHIPSET>/<GAME_ID>/shaders/` (gitignored; populated by `install.sh` Tier-A sync).
	
# FAQ: What is the ETK and How Does it Really Work?
- **To enhance how the built-in PS3 emulator handles shader caching,** the ETK intercepts the Vulkan shader cache with a simple symlink and safely stores these files into a vault folder on your SD card organized by device and game ID so they can be archived on your computer. Even when you crash during a shader harvesting run, the vault has saved the shaders for the next run.
- **To enhance how the device handles high demand games during the shader compiling process and high performance gaming,** the ETK manages the system temp and performance to safely overtax the device when it needs to work the hardest while preventing a total meltdown. It also modifies how the OS manages virtual memory and fine tunes the video driver.
- **To enhance how you can monitor the device system stress while pushing it to its limits,** the ETK enables a custom dashboard overlay using built-in Rocknix features across a thin horizontal HUD strip designed to evoke the Driver Data Unit (DDU) found in GT and F1 racing cars. The custom HUD DDU also shows the number of shaders harvested during a game session so you realize even if you crash, it was worth it.
- **To streamline how you can tweak key emulation settings,** the ETK PITSTOP app in the Rocknix Tools menu, inspired by pit wall screens, allows you to easily adjust selected configuration settings using the gamepad controls. The subset of on-board configs can be customized in a JSON file. 
- **To solve the problem of installing `.pkg` files with the desktop version of RCPS3 inside of Rocknix with only a gamepad,** the ETK automates the process for you. All you do is drop files in a folder on your card and use ETK Pitstop Tools to start the process.
- **To simplify managing game shader vaults and software updates,** the ETK includes a simple command-line utility to install, repair, update, and automatically sync shader vaults as you harvest from games or trade device and game-specific shader folders with others. It also includes an uninstall utility to retire from the league. A typical game 300+ MB shader vault will involve tens of thousands of binary files so an efficient transfer mechanism to manage shader sets between a computer and the handheld devices is essential.
- ETK does all of this while trying to maintain a **minimal system footprint without subjecting your SD card to abuse.**
	  
# Windows Install Guide
## alpha-tester preview
Two ways to run the ETK host tooling from Windows:

**1. Native PowerShell installer (`windows_installer/`) — no WSL required.** A dependency-free port of `install.sh` / `uninstall.sh`. On first run it **auto-pairs over SSH** — you type the rig password once (Rocknix default `rocknix`), and every call after that is silent. Full guide: **[windows_installer/WINDOWS_HOST_README.md](windows_installer/WINDOWS_HOST_README.md)**.
```powershell
powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1
```
This option does not back your shaders up to the PC (the rig still vaults locally; see [Manual SMB Backup](#manual-smb-backup)).

**2. WSL2 (full-featured).** For the complete experience including host-side shader-vault backup/restore (Tier-B), install WSL2 + Ubuntu, clone the kit, and follow [Getting Started](#getting-started) above unchanged — `install.sh` runs in WSL2 with no modifications.

For the one-time fresh-card flash, use the official Rocknix [ImageBurner](https://github.com/ROCKNIX/ImageBurner/releases) — Windows-native, no dependency, to install the certified nightly (`20260610`) required for the ETK.

## Manual SMB Backup
- (the native PowerShell installer is no-vault, so use this for shader backups on Windows)
If you are on the PowerShell installer (no Tier-B host backup) or want belt-and-suspenders, Rocknix exposes Samba shares natively. In File Explorer, `Map network drive...` → `\\<rig-ip>\games-internal` → assign a letter (e.g. `R:`). Then save this as `etk_backup.bat` and run it before any reflash or risky migration:

```bat
robocopy R:\roms\etk\vault                  C:\etk_backup\vault           /MIR /R:1 /W:1
robocopy R:\roms\etk\etk_telemetry          C:\etk_backup\etk_telemetry   /MIR /R:1 /W:1
robocopy R:\roms\bios\rpcs3\custom_configs  C:\etk_backup\custom_configs  /MIR /R:1 /W:1
robocopy R:\roms\bios\rpcs3\dev_hdd0\home   C:\etk_backup\rpcs3_home      /MIR /R:1 /W:1
```

`robocopy /MIR` is Windows-native (no install), incremental like `rsync`, and idempotent — re-run as often as you like. To restore after a reflash, swap source and destination in each line. **Manual caveat:** you must remember to run the backup yourself; there is no Windows equivalent of `install.sh --restore-state` yet.

# Internal Storage (Advanced — Optional)
**SD-card support was proven first and is the recommended default.** For advanced operators who want a faster shader rig, ETK also supports running the shader vaulta and games on the device's internal **UFS** partition instead of the SD card. This is an **optional, opt-in** upgrade aimed at intensive shader-harvesting runs.

Retroid Pocket SD8250 devices support [Rocknix's Install (internal) option](https://rocknix.org/play/installtointernal/) which allows the OS to run on a partition inside the internal storage rather than from a partition on your SD card. This dramatically speeds up Rocknix boot and OS update times. The ETK also supports the creation of a medium sized internal partition for storing shaders, which improves performance and reduces SD-card treadwear. In addition, the ETK supports the creation of a large internal partition to store games and shaders for improved game launch performance. These partitions take away from your Android storage, so factor that into designing your partitions as your execute the `installtointernal.sh` command on your rig.

## Advancements
- **Durability.** The shader vault is rewritten every session; moving it off the wear-prone SD card to internal UFS reduces card wear and corruption exposure (the SD is the rig's single point of failure). Shaders write, credit, and survive an R3 recovery correctly on UFS.
- **Speed** OS boots faster. Games launch quicker. Shader I/O is just better.

## Caveats
- Retroid Flip2 firmware supports `fastboot` over USB in the Qualcomm abl (hold Volume button down during boot) to manage partitions from a host; `fastboot` in U-Boot is not supported.
- **`./install.sh` is internal-aware.** Once the vault is symlinked into internal UFS, the installer detects it and syncs symlink-safely — no workflow change.
- **The internal `/storage` is also the system partition.** Leave **≥1.5 GB headroom**; filling it breaks EmulationStation and boot.

# Power Pro Tip: Disable GRUB
You can disable the GRUB device select screen that appears at boot. This will shave seconds off your boot-time. The recovery option listed has been tested and it doesn't appear to do anything useful when you actually need a recovery.

1. Connect to the rig:\
`ssh root@SM8250.local`
1. Remount the boot partition read-write:  
`mount -o remount,rw /flash`
1. Back it up first so it's reversible:  
`cp /flash/EFI/BOOT/grub.cfg /flash/EFI/BOOT/grub.cfg.bak`
1. Set the menu timeout to 0 — skips the GRUB device-picker wait (edit the EFI config, not /flash/boot):  
`sed -i 's/set timeout=2/set timeout=0/' /flash/EFI/BOOT/grub.cfg && sed -i 's/set timeout=-1/set timeout=0/' /flash/EFI/BOOT/grub.cfg`
1. Verify both timeouts now read 0 (if either still shows 2 or -1, the remount in step 2 didn't take — redo from step 2):  
`grep timeout= /flash/EFI/BOOT/grub.cfg`
1. Flush and put it back read-only:  
`sync && mount -o remount,ro /flash`

> **Note:** Rocknix OS updates regenerate the EFI grub.cfg and revert this tweak — re-run these steps after every update.


# AI Disclosure
ETK was originally prototyped with Google Gemini and developed/maintained with Anthropic Claude Code.

# License
ETK is released under the [GNU General Public License v2.0](LICENSE), matching the licensing of [Rocknix](https://github.com/ROCKNIX/distribution) which it extends.
