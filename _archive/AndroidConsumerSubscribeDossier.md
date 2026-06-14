# DOSSIER (ADDENDUM, SPECULATIVE): Cross-Platform ETK — Rocknix Mints, Android Subscribes

**Status:** SPECULATIVE DESIGN. Pure forward-thinking; nothing implemented. Written after the 2026-06-08 reckoning that **Android PS3 emulation (aPS3e + nihui's Turnip-Android driver) passed Rocknix** on GT/SM8250 performance + stability.
**Extends:** [Release050HandoffDossier.md](Release050HandoffDossier.md) §7, [ShaderDistributionFusionDossier.md](ShaderDistributionFusionDossier.md) (the locked model this re-projects onto two OSes), [ProTuningExportDossier.md](ProTuningExportDossier.md) (the pro/casual bifurcation this extends across platforms).
**Provenance:** Operator question — can Rocknix ETK *pros* mint tunings that **Android** users *auto-subscribe* to (Android = consumers, never producers)? Is the Android side a limited port of ETK Pitstop?
**Ground truth checked:** aPS3e (`aenu1/aps3e`) is self-described as *"ported and optimized based on RPCS3"* → RPCS3-format config + PS3-standard savedata are the working assumption (verify exact paths, §7). Android Turnip = `nihui/mesa-turnip-android-driver`.

---

## §0. THE IDEA IN ONE PARAGRAPH

ETK already bifurcated **pros** (full kit: harvest, instrument, tune, mint) from **casuals** (one-command consume) — see ProTuningExport. This addendum projects that split across the *OS* line, in the direction the userbase actually went: **Rocknix is the recording studio** (where pros productively-crash, instrument, and mint clean-room tunings), **Android is the concert hall** (where the masses just want GT to play well — and now it does). Rocknix pros publish to the same GitHub index; an Android *consumer-only* client auto-subscribes. The studio needs the low-level Linux control to *produce*; the hall just plays back.

---

## §1. WHY THE DIVISION IS COHERENT (not a consolation prize)

| | Rocknix (producer) | Android (consumer) |
|---|---|---|
| Role | works team / studio | the masses / playback |
| Needs | governors, cooling, vault symlink mgmt, systemd daemons, telemetry, the harvest cycle | tap "subscribe," play |
| Has the right tools? | **Yes** — low-level control is *why* you mint here | **Yes** — best GT perf/stability now |
| Population | few (the pros) | many (the favored team's userbase) |

The asymmetry is real and load-bearing: **you can only do the productive-crash harvest + instrumentation on a rig you fully control** (Rocknix). Android is locked-down by design — great for playback, wrong for production. So "pros on Rocknix, consumers on Android" isn't a hack; it's each platform doing what it's actually good at.

---

## §2. THE TECHNICAL HEART — WHAT CROSSES THE OS BOUNDARY, AND WHAT DOESN'T

This is the make-or-break section. The 0.5.0 bundle has three tiers; they do **not** cross equally.

| Tier | Crosses Rocknix → Android? | Why |
|---|---|---|
| **config** (the tune) | ✅ **Yes** (RPCS3-format) | aPS3e is RPCS3-derived → reads `config_<ID>.yml`. RPCS3 ignores unknown keys / defaults missing ones, so cross-version is forgiving. *The dialed-in settings — ETK's hardest-won knowledge — transport.* |
| **savedata** (progress) | ✅ **Yes** | PS3 savedata is emulator-independent (`dev_hdd0/home/00000001/savedata/<ID>*`). Both implement it. (Modulo profile/`localusername`.) |
| **vault** (shaders) | ❌ **NO** | **This is the constraint.** The Mesa disk_cache key folds in the *driver build ID*. Rocknix-Turnip and nihui's Android-Turnip are **separately-built `.so`s** — different build IDs → different cache keys → **a Rocknix vault simply won't be found by the Android driver**, even at the same Mesa version on the same Adreno 650. |

**So the cross-OS pro-tuning is `config + save`, not the shader vault.** The single most valuable Rocknix payload — the storm-killing saturated vault — is the one tier that's OS-locked. Accept this up front; the design is honest only if it's built around it.

What still ships is genuinely valuable: **a pro-dialed aPS3e config + an unlocked save.** The "which settings make GT playable on a 650, and here's a save at the good part" knowledge is exactly ETK's edge, and it transports. The Android user still compiles their *own* shaders on first run — but with a config tuned to minimize that pain.

---

## §3. THE HOMOLOGATION GATE ALREADY HANDLES THIS — SAFELY, FOR FREE

The best part: **no new safety machinery is needed.** The existing `mesa_hash` gate (sha256 of the Turnip `.so`) was built to refuse driver-mismatched vaults. On Android it does exactly the right thing **by construction**:

- The Android consumer computes its *own* Turnip `.so` hash (nihui's driver, a different path — §7).
- A Rocknix-minted vault carries the *Rocknix* `mesa_hash` → **mismatch → gate refuses the vault → config-only.**
- Result: the Android user **fails safe to config + save**, never loading broken Rocknix shaders. No corruption, no dead weight, no special-casing.

The gate that protects against driver drift *is* the gate that makes cross-OS subscription safe. Same index, same bundle, same gate — the platform difference resolves itself in the hash.

---

## §4. IS THE ANDROID SIDE A "LIMITED PORT OF ETK PITSTOP"? — YES, AND NO

**The producer stays Rocknix-only.** Harvest, telemetry, `export.sh`, the tuning editor, the Sentry/daemons — none of that ports, and shouldn't. That's the studio.

**The consumer is a *small subset*, re-implemented — not a literal port.** ETK Pitstop is a Python/curses TUI in a sway session; that doesn't exist on Android. But the *consumer logic* it wraps is tiny:

> fetch the index → match installed games → gate on `mesa_hash` → download the bundle → unzip into the emulator's dirs.

That logic already lives almost entirely in **`pro-tuning/install-protune.sh`** — POSIX `sh`, no Rocknix internals except the inject *paths*. So the realistic Android client is:

1. **`install-protune.sh`, re-pathed for aPS3e** (config → aPS3e config dir, save → its savedata, vault → its Turnip cache; homologation `.so` path swapped). The engine is ~90% portable already.
2. **A thin native shell on top**, in rough order of preference:
   - **Best: a feature inside aPS3e itself** ("Subscribe to ETK tunes") — if the aPS3e dev integrates it, zero install friction for the masses.
   - A tiny **companion Android app** (Kotlin) that drives the same fetch/gate/inject.
   - A **Termux one-tap** (`curl … | sh`) for the technical minority — the literal cross-OS twin of the no-ETK one-liner.

So: **not a port of Pitstop the app — a re-host of PADDOCK's consumer half (its `install-protune.sh` engine) behind an Android-native trigger.** The curses UI, the harvest, the tuning — all stay home.

---

## §5. THE ANDROID VAULT PROBLEM (the deferred half)

Config+save cross today; the vault needs an **Android-native source**. Options, least-to-most ambitious:

1. **Accept the first-launch storm** (config-only on Android). Simplest; the tune still helps; the user's own cache saturates over time. *Ship this first.*
2. **Android-side swarm seed** — the *first* Android consumer to play a game compiles the shaders; their cache is uploaded under the **Android `mesa_hash`** and shared with the next consumer. The first consumer is an inadvertent harvester; everyone after subscribes. This is the deferred Model-3 swarm ([PaddockSwarmFeasibility.md](PaddockSwarmFeasibility.md)), now Android-side — and it's where the *numbers* are, so it's actually viable here in a way it never was on a city-of-one Rocknix.
3. **A cross-platform pro** who harvests on Android too (contradicts "Android users aren't pros" — but a *Rocknix pro who also runs Android* could mint both, keyed by each platform's hash).

The index already supports this cleanly: a game can have **two vault entries, one per `mesa_hash`** (Rocknix-Turnip, Android-Turnip), config+save shared. The consumer's gate picks the one that matches — or none.

---

## §6. INDEX / MANIFEST EVOLUTION (small)

The `pro_tuning_index/1` schema barely changes:
- Allow **multiple `vault` entries per game, keyed by platform `mesa_hash`** (Rocknix-Turnip vs Android-Turnip). `config`/`savedata` stay single + shared.
- Optionally tag each tier `portable: true|false` for client clarity (config/save true; vault platform-locked).
- The Android client reads the **same `manifest.json`** — it's just JSON on GitHub; nothing Linux about it.

---

## §7. OPEN QUESTIONS TO VERIFY (before any build)

1. **aPS3e config path + format** — confirm it's `config_<ID>.yml` RPCS3-format and where it lives in Android app storage. (Derivation says yes; verify in source/wiki.)
2. **aPS3e savedata path** — confirm `dev_hdd0/home/00000001/savedata/` structure + the profile ID (does it match `00000001`?).
3. **The Android homologation primitive** — *where* nihui's `libvulkan_freedreno.so` lives at runtime (AdrenoTools driver dir / app-private storage) so the gate can hash it. This is the Android analog of `/usr/lib/libvulkan_freedreno.so`.
4. **aPS3e Mesa cache location** — where Turnip writes its disk_cache under aPS3e, i.e. the vault inject target (for option §5.2).
5. **Will the aPS3e dev integrate it natively?** (§4 best case) — worth an upstream conversation; the science + the model are a gift to their community either way.

---

## §8. HONEST ASSESSMENT

- **The model transfers; the headline payload (vault) mostly doesn't.** Cross-OS pro-tuning is **config + save today**, vault is **Android-native (swarm) tomorrow**. Anyone promising "Rocknix pros pre-warm your Android shaders" is wrong about the driver key — say so plainly.
- **But config alone is a real product.** ETK's edge was never just the cache; it was *knowing the settings*. That knowledge crosses intact.
- **The Android side is cheap** — it's `install-protune.sh` re-pathed behind a tap. The expensive, control-hungry half (production) stays where it belongs, on Rocknix.
- **This is also the membrane, finally pointed the right way:** the studio is small and on the losing-OS-for-playback, but it feeds the hall where everyone actually is. ETK stops being a city of one and becomes the *supply side* for the platform that won.
- **And it keeps ETK's real contributions alive** on the winning platform: the tune knowledge, the distribution model, and the crash-attribution science — none of which were ever Rocknix-specific.

> One line: **Rocknix records the lap; Android plays it back. The vault stays in the studio; the setup sheet and the save go out to the crowd — and the crowd, eventually, harvests its own vault.**
