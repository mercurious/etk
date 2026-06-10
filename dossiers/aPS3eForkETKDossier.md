# DOSSIER (SPECULATIVE SPEC): aPS3e-ETK — A Fork Where Pro-Tuning and Subscribing Are Native

**Status:** SPECULATIVE SPEC. Nothing implemented. A design exercise for an *experimental fork* of aPS3e that absorbs ETK's load-bearing features as first-class, native emulator functions — making ETK genuinely Android/Rocknix cross-platform on the feature set that matters.
**Asserted premise (this dossier builds on it):** *Turnip-generated shaders constitute fair use by nature of their production.* This is **asserted as a working axiom for the design**, not a legal determination — the real ruling belongs to the legal consult ([[project_distribution_fusion]]). Everything downstream of §1 assumes it holds.
**Extends:** [AndroidConsumerSubscribeDossier.md](AndroidConsumerSubscribeDossier.md) (Android-as-*consumer*; this goes further — Android as *producer + consumer*), [ShaderDistributionFusionDossier.md](ShaderDistributionFusionDossier.md) (the locked model), [Release050PaddockDossier.md](Release050PaddockDossier.md) (the Rocknix PADDOCK build this re-hosts).
**Ground truth (repos, 2026-06-08):** aPS3e (`aenu1/aps3e`) — C++ ≈91%, Android Studio/Gradle, JNI/NDK, *"ported and optimized based on RPCS3"* (so RPCS3-format config + PS3-standard savedata). nihui driver (`nihui/mesa-turnip-android-driver`) — ships `libvulkan_freedreno.so`, runtime `dlopen` hot-swap (AdrenoTools-style), Mesa **26.1.2**, 14 tagged releases → versioned + hashable.

---

## §0. THE IDEA IN ONE PARAGRAPH

The consumer addendum kept Android a *playback hall* and Rocknix the *studio* — Android subscribes, never mints, and the vault never crosses. That was the honest design **while the vault was legally radioactive and the platform was a stranger's app**. This dossier asks the next question: if (§1) the shader bytes are fair use by their nature, and (§4) we *own the fork*, then the studio/hall split collapses — **Android can mint too.** aPS3e-ETK is a fork of aPS3e where the three ETK primitives — **subscribe**, **pro-tune**, **mint** — are native UI, not a bolted-on shell script. ETK stops being "a Rocknix kit with an Android export" and becomes a **cross-platform standard** whose reference producer happens to have started on Rocknix.

---

## §1. THE ASSERTED PREMISE — AND EXACTLY WHAT IT UNLOCKS

**Asserted:** a Turnip shader cache is fair use *by nature of its production*. The argument the assertion rests on:

- It is **compiler output, not game content.** The bytes are produced on-device by an open-source toolchain (Mesa/Turnip) translating the user's own legally-acquired title into Adreno GPU programs. No copyrightable expression of the original work survives in protectable form — the artifact is a **functional intermediate representation** dictated by the target ISA (merger/functionality), closer to a JIT cache than to a ripped asset.
- The purpose is **interoperability and preservation** — running owned software on owned hardware — which the intermediate-copying line (*Sega v. Accolade*, *Sony v. Connectix*) treats as transformative and favored.
- In ETK's reframed posture it is additionally **non-commercial research** ([[project_distribution_fusion]]).

**What the premise actually unlocks — and what it does NOT:**

| Axis | Without premise | With premise asserted |
|---|---|---|
| **Legal** — may you publish/share the vault bytes? | Gated (the open item that held 0.5.0) | **Yes — gate dissolved** |
| **Technical** — will a published vault *load* on another device? | Only on a byte-identical Turnip build (`mesa_hash` match) | **Unchanged — still driver-build-keyed** |

This split is the spine of the whole spec: **fair use frees the bytes to travel; it does not change which device can use them.** Anyone who says "the premise lets Rocknix pre-warm Android shaders" is conflating the two axes. It doesn't. It lets **Android mint and publish Android-keyed vaults at the scale where the users actually are.** That is the prize.

---

## §2. WHY A FORK (NOT THE CONSUMER CLIENT FROM THE PRIOR DOSSIER)

[AndroidConsumerSubscribeDossier.md](AndroidConsumerSubscribeDossier.md) specced the cheap version: `install-protune.sh` re-pathed behind a tap, Android consuming `config + save` only, vault left home. That is still the correct **MVP** (§7, Phase 0) and needs no premise and no fork.

A **fork** buys three things the bolt-on client cannot:

1. **Minting on Android.** Harvest + export as native features means Android stops being consumer-only. Under §1, Android-minted vaults are publishable — and Android is where the population (and therefore a viable *swarm*, [PaddockSwarmFeasibility.md](PaddockSwarmFeasibility.md)) lives.
2. **Native homologation + cache control.** Hashing the live `libvulkan_freedreno.so` and writing into Turnip's `disk_cache` from inside the process is reliable and first-class, instead of guessing app-private paths from outside.
3. **Zero install friction.** "Subscribe to ETK tunes" as a menu item beats a Termux one-liner for the masses — the difference between a model and a product.

The cost is real (C++/JNI, GPL obligations §6, upstream divergence) — hence "experimental fork," and hence the phased path in §7 that earns each step.

---

## §3. THE THREE NATIVE PRIMITIVES (the feature set "where it matters most")

The fork's whole job is to host ETK's *portable* contributions — **the tune knowledge, the distribution model, the productive-crash science** — none of which were ever Rocknix-specific.

| Primitive | Native form in aPS3e-ETK | ETK source it re-hosts |
|---|---|---|
| **SUBSCRIBE** | In-emulator "ETK Tunes" browser: read `manifest.json`, match installed games, gate on `mesa_hash`, pull bundle from Releases, inject. | PADDOCK tab + `install-protune.sh` (the consumer half) |
| **PRO-TUNE** | Native settings overlay editing the per-game RPCS3-format config; the curated ETK field subset. | Pitstop TUNING tab + `pitstop_fields.json` |
| **MINT** | Native harvest: a "productive-crash" session mode + a one-tap **Export** that bundles `config + Android-keyed vault + save`, hashes the driver, and publishes/queues to the index. | `export.sh` + the Sentry/harvest loop (the genuinely hard port) |

SUBSCRIBE is mostly portable today. PRO-TUNE is a UI re-skin of known logic. **MINT is the frontier** — it's the productive-crash forensics made native, and it's what turns Android from consumer to producer.

---

## §4. ARCHITECTURE / INTEGRATION POINTS (grounded in the repos)

aPS3e is RPCS3-derived C++ with a Gradle/JNI Android shell. The fork hangs ETK off existing seams rather than rewriting the emulator:

- **Config** — RPCS3-format `config_<ID>.yml` in app-private storage. SUBSCRIBE writes it; PRO-TUNE edits it. RPCS3 ignores unknown keys / defaults missing ones → cross-version forgiving (carry the AndroidConsumerSubscribe §2 assumption; **verify path**, §8).
- **Savedata** — PS3-standard `dev_hdd0/home/00000001/savedata/<ID>*`, emulator-independent. SAVEHUB tier copies in/out (**verify profile ID**, §8).
- **Vault target** — Turnip's Mesa `disk_cache` directory under aPS3e. MINT reads it; SUBSCRIBE (vault tier) writes into it. The cache key folds the driver build id → see homologation.
- **Homologation primitive** — the live driver is nihui's `libvulkan_freedreno.so`, `dlopen`-loaded (AdrenoTools-style). The gate is `sha256` of *that runtime `.so`*. Native code already holds its path/handle → the Android analog of hashing `/usr/lib/libvulkan_freedreno.so` is trivial *inside* the fork (and fragile outside it — the §2 argument for forking). Index entries are keyed by this **Android `mesa_hash`** (distinct from Rocknix's), so the existing gate ([AndroidConsumerSubscribeDossier.md](AndroidConsumerSubscribeDossier.md) §3) *fails safe by construction* — a Rocknix vault simply won't match and degrades to config-only.
- **Index transport** — `manifest.json` + GitHub Releases are just HTTPS/JSON; nothing Linux about them. The schema already allows **multiple vault entries per game keyed by platform `mesa_hash`** with shared `config`/`save` (AndroidConsumerSubscribe §6) — so one index serves both OSes natively.

Net: the emulator engine is untouched; ETK is a **feature layer** over config I/O, savedata I/O, the disk_cache dir, the driver handle, and an HTTPS fetch.

---

## §5. THE VAULT, RE-EXAMINED UNDER THE PREMISE

Restating §1's payoff concretely, because it inverts the prior dossier's headline:

- **Prior dossier (no premise):** "vault is OS-locked; ship config-only to Android." Honest, but a thin product.
- **This dossier (premise asserted):** the vault is still **driver-build-keyed** (a Rocknix vault won't load on Android — *technical*, unchanged), **but Android-minted vaults are now legally publishable** (*legal*, unlocked). So the storm-killing payload **does** reach Android users — sourced from **Android producers**, gated by the **Android `mesa_hash`**. The first Android player of a title becomes an inadvertent harvester whose cache seeds everyone after (the swarm, finally on a platform with the numbers). nihui's 14 tagged driver releases mean the homologation key is well-defined and re-mintable per driver bump.

The membrane finally points the right way *and* carries its heaviest payload: not by smuggling Rocknix bytes across the OS line, but by **letting the winning platform mint its own.**

---

## §6. LICENSING REALITY CHECK (a DIFFERENT legal axis — do not conflate)

The §1 premise is about **shader data**. The fork is about **code**, and that has its own, *separate*, well-trodden answer:

- aPS3e is "based on RPCS3" → **GPLv2 lineage**; aPS3e itself notes multiple licenses across the tree. A fork must therefore **publish source under the compatible terms** and honor per-file headers. This is ordinary GPL hygiene, not a novel risk — and it actually *reinforces* the research posture: an open fork of an open emulator loading an open driver.
- **Keep the two axes labeled in all external comms:** *code* = GPL-clean open fork (settled); *shader bytes* = the fair-use premise (the thing the consult rules on). Collapsing them muddies both.

---

## §7. PHASING — EARN EACH STEP

| Phase | Deliverable | Needs premise? | Needs fork? |
|---|---|---|---|
| **0 — Consumer bolt-on** | `install-protune.sh` re-pathed; config+save subscribe via Termux/companion tap | No | No |
| **1 — Native SUBSCRIBE** | In-emulator ETK Tunes browser; config+save inject; native `mesa_hash` gate | No | **Yes** |
| **2 — Native PRO-TUNE** | In-emulator ETK settings overlay (curated field subset) | No | Yes |
| **3 — Android vault subscribe** | Vault tier injects into Android `disk_cache`, Android-keyed | **Yes** (to publish the bytes) | Yes |
| **4 — Native MINT** | Productive-crash harvest mode + one-tap Export/publish | **Yes** | Yes |
| **5 — Swarm seed** | First-player-harvests → auto-share to next ([PaddockSwarmFeasibility.md](PaddockSwarmFeasibility.md)) | **Yes** | Yes |

Phase 0 ships value with zero new legal/eng risk and validates demand. Phases 3–5 are the ones the consult gates. **Best outcome of all:** the aPS3e maintainer integrates Phase 1 upstream — then it's not even a fork, it's a feature, and ETK becomes a portable spec rather than a codebase.

---

## §8. OPEN QUESTIONS TO VERIFY (before any fork commit)

1. **aPS3e config path + format** — confirm `config_<ID>.yml` RPCS3-format and its app-storage location.
2. **aPS3e savedata path + profile id** — `dev_hdd0/home/00000001/savedata/`? Does the profile match `00000001`?
3. **aPS3e Mesa `disk_cache` location** — the vault inject/read target.
4. **Driver load path** — does aPS3e use AdrenoTools / the ncnn `load_vulkan_driver` path to `dlopen` nihui's `.so`, and what is the runtime path/handle to hash? (Confirms the §4 homologation primitive.)
5. **Build/signing** — aPS3e ships `app/build.gradle.bak` needing signing config; map the minimal fork build.
6. **License inventory** — enumerate per-dir/per-file licenses for §6 compliance before publishing a fork.
7. **Upstream appetite** — would the aPS3e maintainer take Phase 1 natively? (Open the conversation; the science + model are a gift either way.)

---

## §9. HONEST ASSESSMENT

- **The premise is the hinge — and it's the one thing this dossier can't supply.** Everything from Phase 3 on is gated on the legal consult, not on engineering. Build Phases 0–2 (which need neither premise nor risk) while that resolves.
- **Two legal axes, kept separate, are both manageable:** GPL-clean fork (settled practice) + asserted shader fair use (the consult's call). The danger is rhetorical conflation, not legal novelty.
- **The technical inversion is the real insight:** fair use moves the *bytes*, never the *build key*. So the win isn't smuggling Rocknix vaults to Android — it's **Android minting its own, at population scale**, with the homologation gate making cross-platform safe for free.
- **ETK's durable contributions were never Rocknix artifacts.** The tune knowledge, the distribution model, and the productive-crash science are Turnip-and-PS3 artifacts that transfer to whatever platform the users chose — which is now Android.
- **Cheapest highest-value move:** ship Phase 0, open the upstream conversation, and let the consult decide whether Phases 3–5 are a fork or a footnote.

> One line: **Rocknix proved the model on a city of one; the fork hands it to the platform that won — where, under the asserted premise, Android stops merely playing the lap back and starts recording its own.**
