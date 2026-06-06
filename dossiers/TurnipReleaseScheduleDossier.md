# DOSSIER: Turnip Release Cadence — Chipset-Centric Crash Attribution (the anti-hopium calculus)

**Status:** METHOD / decision-aid. Captured 2026-06-05 for revisiting on every future Turnip bump. The technique is specced; the `floor-estimator` tool is not yet built.
**Audience:** operator (decides whether to chase a driver) + Claude Code (builds the floor-estimator; runs the scout).
**Provenance:** ROCKNIX nightly-20260605 bumped Mesa Turnip 26.1.0 (official-pinned) → 26.1.2. Operator asked whether to fork ETK for a nightly track, then for a technique to decide *when a driver bump is worth re-minting vaults*. This dossier is that technique + the worked 26.1.2/SM8250 example.
**Related:** [Release050PaddockDossier.md](Release050PaddockDossier.md) (0.5.0 makes rig-building cheap → industrializes scouting) · [ShaderDistributionFusionDossier.md](ShaderDistributionFusionDossier.md) (`chipset:turnip:game` epoch key; "a new Turnip → cut a new tag") · `RocknixNightly2026052*CertificationDossier.md` (the existing nightly-cert process).
**Memory:** [[project_race_baseline_status]] (race-stable cleared but ~28% career clean-rate, bursty) · [[project_rpcs3_wiki_starting_point]] (config-bound crashes; verify on Adreno) · [[feedback_validate_before_integrate]] (disposable on-rig harness) · [[noconstcheck_rejected]] / [[max_map_count_theatre]] (prior driver-knob probes that didn't move the floor).

---

## §0. SUMMARY

The handheld community treats every new Turnip release as a win ("hopium"). ETK can do better: decide per-**chipset** whether a driver bump is worth the cost of re-harvesting vaults, using data it already collects. The core move is to separate crashes into three species — **config-bound, vault-bound, driver-bound** — and recognize that **only the driver-bound residual (the "floor") is what a driver update can move.** The floor is measurable from the existing ledger, and — crucially — **driver-bound crashes are vault-independent, so a new driver can be tested in ~one session without paying the full re-saturation cost.** Re-mint vaults only when the floor is bad, a changelog line plausibly maps to the floor's crash signature, and a cheap scout confirms the signature stops firing.

---

## §1. THE PROBLEM: HOPIUM vs. CHIPSET-CENTRIC TRUTH

A Mesa point release is a single stable branch shared by Intel/AMD/Arm/Qualcomm. Most of any given release touches drivers ETK doesn't run. "Mesa got faster" is meaningless to a Snapdragon 865; what matters is whether **a6xx (Adreno 650)** code-paths ETK actually exercises changed. The discipline: **read the relnotes filtered to your GPU family, then verify on-rig — never infer benefit from version number alone** ([[project_rpcs3_wiki_starting_point]]).

---

## §2. WORKED EXAMPLE — 26.1.0 → 26.1.2 ON SM8250 (a6xx / Adreno 650)

The ROCKNIX changelog says "Update Mesa to 26.1.2" (the stable point release), so the complete delta is the 26.1.1 + 26.1.2 relnotes. Filtered to anything that can touch a650 at runtime:

| Release | a6xx-relevant entry | Touches a650? | Advertises hang/fault/corruption fix? |
|---|---|---|---|
| 26.1.1 | `tu: Always lazy_init_vsc for tiler rendering` | **Yes** — Turnip tiler/binning path (heavy on a6xx) | No — reads as init-ordering |
| 26.1.1 | `ir3: don't cache driver param instructions` | Yes — shared shader compiler | No — stale-param cache fix |
| 26.1.1 | `tu/a8xx: Fix reading border_color from sampler memory` | **No** — a8xx = Adreno 8xx, wrong chipset | (n/a) |
| 26.1.1 | `freedreno/computerator: fix UAV view size` | No — dev/test tool, not runtime | (n/a) |
| 26.1.2 | *(none — all Intel/AMD/Panfrost)* | No | No |

**Finding:** for SM8250 the whole bump reduces to **one Turnip tiler-init fix + one compiler-cache fix**, neither advertising a crash-class fix; 26.1.2 itself has zero Adreno content. **Low expected value for a650.** Caveat: titles undersell and we have titles only (not MR diffs), so `lazy_init_vsc` *could* fix a real tiler hang — which is why the decision goes to the on-rig discriminator (§4–§6), not the changelog.

Sources: [Mesa 26.1.1](https://docs.mesa3d.org/relnotes/26.1.1.html) · [Mesa 26.1.2](https://docs.mesa3d.org/relnotes/26.1.2.html) · [Mesa 26.1.0](https://docs.mesa3d.org/relnotes/26.1.0.html) · [Freedreno docs](https://docs.mesa3d.org/drivers/freedreno.html).

---

## §3. THREE SPECIES OF CRASH (each curable by a different layer)

| Species | Cured by | ETK layer | Tell |
|---|---|---|---|
| **Config-bound** | the right RPCS3 setting (e.g. GT6 WCB/RCB→false killed the Nordschleife hallucination) | AUTO-TUNE / config | fixed by a dial, reproducible until the dial flips |
| **Vault-bound** | shader saturation | AUTO-SHADERS / vault | decays as the vault grows; correlates with `shaders_harvested` |
| **Driver-bound** | a Turnip change — *nothing else* | (out of ETK's hands) | survives a saturated vault + the best tune |

The **floor** = the crash-rate that survives *after* the best tune **and** a saturated vault. That residual is the driver's fingerprint and the *only* thing a driver bump can improve.

---

## §4. THE BINARY DISCRIMINATOR (no statistics required)

> Does the crash still fire on a **fully-saturated** vault with **zero new shaders compiled that session**? If yes → it is **not** vault-curable, full stop. The shader was already cached and it crashed anyway → config- or driver-bound.

Everything this needs is already in `sessions.tsv`: `crash_sig`, `fence_at_crash`, `shaders_harvested`, `status`, plus the streak ledger.

1. **Filter to saturated sessions** (`shaders_harvested ≈ 0`).
2. **Cluster survivors by `crash_sig` + `fence_at_crash`.** A driver/correctness bug recurs at the **same GPU fence** (tight cluster); a vault straggler scatters across first-encounters. The cluster location is what you later match to a changelog line.
3. **Measure burstiness, not just the mean.** GT5P hit a 16-clean streak yet career clean-rate is ~28% ([[project_race_baseline_status]]) — bursty/state-dependent, not a constant per-lap hazard. Track the **streak-reset rate**; a driver fix for a state-dependent hang shows up as *longer streaks / fewer resets*.

Caveat: some driver bugs live *in* the shader compiler (ir3) and correlate with harvest, mimicking vault-bound. The saturated-vault test disambiguates by construction — if it crashes with nothing left to compile, the compiler isn't the proximate trigger.

---

## §5. THE SCOUT PROTOCOL — test a driver *before* re-saturating

Driver-bound crashes are vault-independent → they fire on an **unsaturated** rig too. So you don't re-harvest to find out if a driver helped:

1. On the saturated current rig, pin the dominant **driver-bound `crash_sig` + `fence_at_crash` cluster + its reproducer**.
2. Flash the candidate driver on a scout card (consciously — this forfeits that card as a §4.1 reproduce-gate rig).
3. Run the reproducer **to the trigger only** — *no saturated vault needed*.
4. Read it:
   - **Same sig at same fence** → driver didn't fix it → **stop; ~1 session spent, no re-mint.**
   - **Sig gone** → driver fixed the floor → *now* re-saturation is justified → mint a `turnip<NEW>` epoch vault.
5. **Regression check:** run a path known-clean on the old driver; a *new* sig = the driver regressed a650 (the a8xx/ir3 churn makes this plausible) → reason to stay put.

---

## §6. THE DECISION RULE

> **Re-mint vaults for a new driver only when (a) the saturated floor is unacceptable, AND (b) a relnote line plausibly maps to the floor's `crash_sig`/`fence` cluster, AND (c) a one-session scout confirms the sig stops firing with no new regression sig.** Otherwise ship the saturated *official* vault and keep scouting.

Corollary that validates the operator's instinct: **a low finish-rate on a *saturated* vault is itself the diagnosis — you are floor-bound, hence driver-bound, and saturation is the wrong lever.** When floor-bound, scout drivers aggressively; just don't re-mint for a bump whose a6xx delta doesn't touch your floor's class. (26.1.2: worth one scout *iff* the floor clusters in the tiler/binning fences — the lone candidate is `lazy_init_vsc`; otherwise skip.)

---

## §7. WHY 0.5.0 INDUSTRIALIZES THIS (the operator's insight)

Once [PADDOCK](Release050PaddockDossier.md) makes rig provisioning a one-command subscribe, **scouting a nightly stops being artisanal.** The loop becomes routine:

1. Take a spare card, flash the nightly (new Turnip), install the game.
2. PADDOCK-subscribe the config (config-bound layer locked to the known-good tune; the *shader* tier auto-greys on the homologation mismatch — correct, you *want* to re-harvest fresh on the new driver).
3. Let it run **a week of saturated-harvest sessions** — the telemetry daemons already write `sessions.tsv` / streak ledger with no extra setup.
4. Diff the nightly rig's **floor** (§4) against the official rig's floor. Did the `lazy_init_vsc`-class init change move any `crash_sig`/`fence` cluster, lengthen streaks, or drop the reset rate? The ledger answers empirically.

This turns "hopium" into a measured A/B: two cards, two ledgers, one floor-diff. The cost that used to gate this — building a clean comparison rig — is exactly what 0.5.0 removes.

---

## §8. OPEN ITEMS

1. **Build the `floor-estimator`** over `sessions.tsv` + streak ledger: outputs saturated floor crash-rate, residual `crash_sig`/`fence_at_crash` clusters, and streak-reset rate. That report *is* the changelog-diff input. First use: classify GT5P's current floor (tiler-class → 26.1.2 worth one scout; else skip).
2. **Decide the floor "acceptable" threshold** — what finish-rate / streak-reset rate counts as floor-bound enough to justify scouting. Tie to the race-stable bar ([[project_race_baseline_status]]).
3. **A changelog→fence mapping note** — keep a small table of which a6xx code-paths correspond to which `fence_at_crash` clusters, so future relnote lines can be matched faster.
4. **Scout-card bookkeeping** — a card on a nightly cannot also be the official §4.1 reproduce-gate rig. Track which card is which epoch.

---

## §9. TL;DR

- Filter every Turnip relnote to **a6xx**, then verify on-rig. Version number ≠ benefit. For 26.1.0→26.1.2 on SM8250 the relevant delta is **one tiler-init fix + one compiler-cache fix** — low expected value; 26.1.2 has no Adreno content.
- Three crash species: **config-bound (AUTO-TUNE), vault-bound (AUTO-SHADERS), driver-bound (only a driver fixes it).** The **floor** = what survives a saturated vault + best tune = the only thing a driver can move.
- **Binary test:** still crashes on a saturated vault with zero new compiles → not vault-curable. Cluster survivors by `crash_sig`+`fence_at_crash`; measure streak-reset rate (crashes are bursty).
- **Scout cheaply:** driver-bound crashes fire on an unsaturated rig, so test the new driver in ~1 session *before* re-minting. Re-mint only on a confirmed floor-fix with no regression.
- **0.5.0 makes this routine:** spare card + nightly + a week of saturated runs + a floor-diff. Build the `floor-estimator` to read it.
