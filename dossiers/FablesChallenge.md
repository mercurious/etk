# ETK Project: Fable's Challenge

## General Emulation Tuning Kit Objective
Get Gran Turismo PS3 games emulated and playable on SM8250 with MESA Turnip
- Retroid Pocket Flip 2 as target device
- Gran Turismo 5 Prologue as the target game
- Latest releases, pre-releases, and custom software tools are all fair game in the game of getting the game to play.

## Project Status: 
The rig is already highly customized and supporting significant progress in both boots, Android and Rocknix. A game test suite of GT HD Concept, GT 5 Prologue, GT 5 (.iso format) and GT6 are preinstalled in both device boots which share game savedata progress over syncthing.

### Rocknix OS
- The rig is running a custom middleware
  - http://github.com/mercurious/etk
- Storage
  - Running SD Card #2 (1TB A2 speed): Currently official release 2026
  - SD Card #1 (256GB A1 speed): upgraded to latest Rocknix nightly to gain latest MESA Turnip drivers and RPSC3
  - a UFS partition can be created for OS, games and shader I/O, etc. to achieve UFS parity to Android, dossier plans it
- Rig Access
  - Live access via `ssh root@sm8250.local` with default password of 'rocknix'
  - Also supports fastboot over usb when in Qualcomm abl (boot with volume button down)

### Android OS
- The rig boots Android 13 by default and a custom fork .apk of aPS3e with its shader cache bug fixed was built by Opus 4.8 yesterday. 
- An external SSD has a complete Android aPS3e toolchain.
- Access rig over USB debug mode

## Fable's Challenge
In the racing and tuning metaphor that surrounds and grounds the project, we might have upgraded the Flip2 to Stage II in the tuning world. But we have not optimized and overhauled enough deeply enough inside the "vehicle" to advance the race stability of GT5P etc. toward the absolute edge of console quality. Let's go to Stage III.

You have two OSs to choose from. They both have the advantages and disadvantages. You can build a custom rig given all the resources, learning, and forensics that can be gathered from the ETK project. It should be tailored for the Gran Turismo series. If necessary, it could involve a fork of MESA Turnip, RPSC3 or further customization of aPS3e (Android) or deeper middleware work inside Rocknix. 

If there's an opensource repo and a toolchain to assemble, it's on the table. We can continue to expand the ETK's garage facilites toward the pursuit of the previously impossible: A single handheld gaming device that plays the entire Gran Turismo series (GT7 via Chiaki Up Remote Play). The device already handles emulation for PS1 and PS2 beautifully. PS3 emulation is ultimate tuning challenge. (We cheat and use streaming for PS4/5)

## Recommended Approach
- Review ETK repo including dossiers, memory, manifest, commits, etc.
- Review codebases of relevant software in the opensource (rocknix, rpcs3, turnip, etc.)
- Review telemetry data produced by ETK to analyze crash patterns
- Develop a methodology and criteria for selecting target OS and runtime
- Analyze game suite installations, binaries, packages
- Analyze project shader caches and optimization strategies
- Do not focus or get distracted by the positioning and documentation of ETK project for the purposes of shader sharing as a method; this is an exploratory and speculative direction and **tabled pending legal review**; 
  - pretense being open source turnip shaders will constitute fair use
- in the meantime, pivot your understanding of the ETK as a toolkit being developed with Claude for my own personal use as a summer mad science project in productive crashing, "overcomputing" GPU active cooled handhelds, and being generally obsessed with the Gran Turismo series. 

## Player Context and Requirements
- The target games can be played on the rig at FPS under 30 without audio (it stutters distorts on the track). Once shaders are saturated in the cache, game progress is much more possible, but variance upstream in the driver and emulator "oem" mean that common sections of tracks will cause various categories of crashes and kernel panics at worst. 
- The feeling as a driver of this rig during significant progress in open source product advancements is that we are getting closer and closer to better race stability. 
- Can you develop a mechanism or solution to the specific problems related to supporting the Gran Turismo PS3 games on RPSC3 MESA Turnip SM8250 even if it means building a rig that makes no promises to support other games, indeed specialized for GT on SM8250, even if means it can't support other games, but it enables the specialized emulation challenges?
- Playable can be defined as FPS less than 30 with no audio support; crash prevention takes priority over FPS and audio.

## Repo Status
- Currently, the ETK and aPS3e repos on local on in-progress towards final decisions, and release resolution, regarding a next release, the outstanding potential PR with aPS3e and other matters.

## Conclusion
- If the analysis arrives at the conclusion that the objective is not possible because of unmovable ceilings and limits for which no amount of clever engineering or custom tuning can mitigate, then the request is to provide the reasoning for accepting the Class Limits of the SM8250 devices against emulating Polyphony Cell Broadband titles. It's a research project, so the request is toward discovery and experimentation, and the production of new knowledge and pushing prior assumptions about technical requirements.
