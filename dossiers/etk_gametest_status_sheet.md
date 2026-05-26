# ETK GAME TEST STATUS SHEET

## NPUA80075 GRAN TURISMO PROLOGUE
- Saturation: Highly saturated
- Playability: Credits earned, cars purchased, event trophies accumulated
- Durability: Random crashes from apparent variance
- Audio: Menus Ok, Race stutters, no setting found to fix
- Known Issues: Can't finish Fuji. Track surface flickers. Write Color Buffers does not fix.
- Comment: The primary ETK target game.

## NPEA90002 GRAN TURISMO HD
- Saturation: 16 MB 1551 Highly Saturated
- Playability: Possible to progress and unlock cars.
- Durability: Highly durable, FPS 20-30
- Audio: Good. Some race stutter.
- Known Issues: Black artifacting in the sky. Menus sensitive to SPU threads, can get real clunky.
- Comment: Ideal testing and baseline game because it's small, loads fast, and relatively durable. Never pushes the chipset hard.

## PNUB30457 RIDGE RACER 7
- Saturation: 5 MB 266 Somewhat saturated
- Playability: Highly playable but difficult to finish races.
- Durability: Works quite well until it doesn't well into the set of laps.
- Audio: Impressive
- Known Issues: Sometimes the distant backgrounds don't load in.
- Comment: Stunning contrast with the Gran Turismo games. If you're looking to get racing, this is the answer.

## NPUA80472 LITTLEBIGPLANET
- Saturation: 11 MB 767 Not very saturated.
- Playability: Highly playable but FPS is pokey
- Durability: Haven't played enough to crash.
- Audio: Impressive.
- Known Issues: None discovered.

## NPEA00502 GRAN TURISMO 6
- Saturation: 192 MB 21256 Saturating fast (post-20260525 fresh harvest, recovered from Mesa rebuild)
- Playability: PLAYABLE. Full Nürburgring Nordschleife lap completed CLEAN 2026-05-26 (17:45 session, 11min pure driving, 744 shaders banked, 0 thermal overrides, peak 81°C).
- Durability: Sub-30 FPS but stable through a marquee-track full lap. 5 lifetime kernel panics in ledger, all NPEA00502 — concentrated pre-tuning-pass. Post-tuning-pass durability dramatically improved.
- FPS: Sub-30 (lower than GT5P's 30). Tuning for FPS deferred until vault is saturated and shader storms are clear of the rear view.
- Audio: Menus good. Race audio works on the tunings below; further evaluation pending.
- Known Issues:
  - **Rear-view mirror does not render.** (game element; not yet investigated as a tuning issue.)
  - Pre-tuning: launching a race kernel-panicked the rig (5 ledger rows confirm).
  - Pre-tuning: car model rendering panicked in dealership view.
  - Long initial load (PPU recompile is significant on first Mesa-build-ID).
- Critical tuning lineage (without these the game is the "kernel-panic generator" the prior scorecard entry described):
  - `Write Color Buffers: false` (flipped from RPCS3-wiki-recommended true — wiki value caused **hallucinatory track-element layering**, driver going haywire visually)
  - `Read Color Buffers: false` (same epoch, same fix)
  - `Resolution Scale: 75` (internal 960×540 → upscale to 720p — the asterisk to playability; full 720p exceeds GPU budget on Nordschleife)
  - `Min Scalable Dimension: 512` (skip-track tiny render targets)
  - `Driver Wake-Up Delay: 100` (fine-tuned from 50)
  - `Force CPU Blit: true` (wiki-derived, kept — useful offload to ARM cores)
  - Frame Limit 30, Shader Precision Low, ZCULL Relaxed, Async Texture Streaming, Disable ZCull Occlusion Queries — standard speed-favored stack
- Config fingerprint at first Nordschleife lap: md5 9c4b48872071b868f43aa8590248e879 (config_NPEA00502.yml, mtime May 25 10:04)
- Comment: The kernel-panic generator turned out to be a **rendering-correctness bug**, not a thermal/SPU/PPU failure. The wiki-recommended Write/Read Color Buffers settings (intended for x86 desktop RPCS3) produced visual corruption on Turnip that the engine couldn't recover from. Flipping both to false unlocked GT6 entirely. Theoretical observation: GT6 (2013) may be **structurally easier to emulate than GT5P (2008)** because by the end of the PS3 generation Polyphony had figured out how to use Cell efficiently. Earlier titles experimented harder with the architecture; later titles converged on what worked. The dial-tuning win on GT6 is amplified by the host hardware accidentally matching what the title was already optimized for.

## BCUS98114 GRAN TURISMO 5
- Saturation: 38 MB 4133 Not very saturated.
- Playability: Some menys. No track opens. Kernel Panics.
- Durability: Menus stable, eventual freeze. Tracks panic.
- Audio: Menus good.
- Known Issues: All game files installed in-game utility, doesn't help load simple loop track.

