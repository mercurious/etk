# DOSSIER: Shader/Tune Distribution — The Fusion Decision (Models 1+2 now, Swarm later)

**Status:** DECISION RECORD (ADR-style). Strategy locked 2026-06-03; implementation not yet started.
**Audience:** operator (strategic) + Claude Code (future implementer)
**Provenance:** Operator crossroads 2026-06-03 — three candidate distribution models for shaders/tunes, framed as "central to ETK adoption from here." This dossier disentangles them and records the chosen path.
**Source dossiers:** [ProTuningExportDossier.md](ProTuningExportDossier.md) (model 2) · [ShaderSwarmFeasibility.md](ShaderSwarmFeasibility.md) + [PaddockSwarmFeasibility.md](PaddockSwarmFeasibility.md) (model 3).
**Rig ground truth:** SM8250.local, official ROCKNIX `20260601`, **Turnip `26.1.0` (now pinned)**, 2026-06-03.

---

## 0. THE THREE MODELS AS POSED

1. **Simple Shader Sharing** — GitHub as index + a free file host (Google Drive) for shader bytes. Single curator (mercurious) until competitors PR into the index. ETK auto-subscribes; shaders auto-load.
2. **Pro Tuning Exports** — give up on requiring an ETK install; a one-command Discord/DM paste self-extracts and installs the best of ETK using the GitHub repo + a Pro-Tuner JSON manifest. Bifurcates the UX: hardcore Pro Tuners (full ETK) distribute one-command installs to casuals.
3. **Shader Swarm** — device-centric decentralized P2P (Syncthing) + Paddock coordination API + R2 starter packs; a GitHub index could turbocharge the bootstrap.

---

## 1. THE REFRAME (why this isn't a 3-way pick)

- **Models 1 and 3 are the same artifact at two maturity levels** — a curated central index *is* the swarm's bootstrap registry; the swarm is its decentralized optimization.
- **Model 2 is a different axis** — it's the *acquisition funnel* (how a stranger gets the result), not the *network* (how the result improves and spreads).

So the real questions are: **(a) what is the minimum that delivers the kit's one guaranteed result to a second human now?** and **(b) is the export a terminus or a trojan horse?**

---

## 2. THE FOUR CONSTRAINTS THAT DECIDE IT

1. **You can't swarm alone.** Model 3 needs N nodes to validate, ~80hr deep-dev, a legal review, and Self-Update + Paddock shipped first. Today ETK ≈ 1 active rig and race-stability is **not yet reproducible** (career clean-rate ~28% — see [[project_race_baseline_status]]). Building the swarm now is foundation for a city of one.
2. **The deliverable is the saturated clean-room vault, not the tune.** [README.md] admits race-stability is only reachable on a *saturated* vault and is not reproducible from a fresh install. A ~2KB config alone doesn't reproduce the result, so a tunings-only Paddock MVP ships the *signal* but not the *payload*. For adoption, the vault is the hook.
3. **The official release just pinned Turnip 26.1.0 — the decisive timing fact.** The shader partition key is `chipset:turnip:game`. In the nightly era Mesa rebuilt ~weekly and rotted every share within days. On the certified official build the key is **stable for the first time**, so *any* sharing model only became viable as of the 20260601 release. (See [[project_customer_car_export]] homologation gate.)
4. **The GitHub index is the one primitive common to all three** — catalog (model 1), installer+manifest host (model 2), free bootstrap/starter-pack registry + peer-introducer fallback (model 3). Whatever gets built, this asset carries forward; a second tuner joins by opening a pull request.

---

## 3. THE DECISION

**Fuse Models 1 + 2 into a single build now. Defer Model 3 (the P2P swarm) explicitly.**

A **GitHub-indexed, curator-driven bundle** (config + clean-room vault, homologation-gated) delivered through the model-2 one-command installer, working **with or without** the full ETK:

- **Index** — a `manifest.json` per `chipset/game` committed to the repo (bundle pointer + `turnip_version` + `rocknix_build` + `sha256` + provenance). Free, versioned, diffable, PR-able. This is the "competition wants in" path: a PR.
- **Bytes** — **GitHub Releases, not Google Drive** (see §5). Deletes [ProTuningExportDossier.md](ProTuningExportDossier.md) §5.1's fragile Drive confirm-token dance outright.
- **No-ETK path** — the Discord one-paste `curl … pro-tuning/install-protune.sh | sh -s -- <GAME_ID>`; pure-data invariant intact (only the repo-hosted installer is code).
- **Has-ETK path** — Pitstop reads the *same index* and offers "new official-Turnip vault for GT5P — Download." This is model 1's seamless auto-subscribe expressed as a **pull against the index**, not a push the operator has to run.

This yields model 1's curation + auto-load, model 2's zero-friction funnel, on free/robust infra, on the exact substrate model 3 will later optimize. It is not a compromise — it is the shared prerequisite of all three, built in the order that produces a userbase *before* it needs one.

---

## 4. TWO HARD FLAGS

### 4.1 The #0 gate — does a clean-room vault reproduce on a *second* rig?
Upstream of all three models and **untested**: ship a clean-room saturated vault ([ProTuningExportDossier.md](ProTuningExportDossier.md) §5.2) to a second SM8250 (or a wiped-and-restored local rig as a stand-in) and confirm it reproduces race-stability. If the vault doesn't deliver the result on someone else's rig, the entire distribution premise collapses — you'd be shipping ~150MB of placebo. Per [[feedback_validate_before_integrate]], this is a disposable on-rig experiment that **gates everything else.** Build no pipes until one clean-room vault reproduces once.

### 4.2 Membrane, not wall
"Give up on people installing ETK" is right humility about the *funnel entrance*, wrong as a *ceiling*. The export is the first hit: paste → game works → "how?" → installs the kit → becomes a tuner/swarm node. Wire one breadcrumb back (the setup sheet already prints; add a "made with ETK — get it here" line). If the bifurcation becomes a wall, adoption caps at *recipients* and never grows *tuners* — and then Model 3 never gets its nodes.

---

## 5. WHY GITHUB RELEASES SOLVES SHADER STORAGE

The vault zips are 10–260 MB binary blobs (per-game, per-Turnip). They must NOT live in git history (clones would carry every version forever). GitHub **Releases** store *assets* (attached files) **outside** the git tree:

- **Free + unmetered storage for public repos.** Release assets don't count against repo size; there is no storage bill (unlike R2's $0.015/GB/mo in the swarm dossier).
- **Up to 2 GB per asset** — far above our largest vault (GT6 ≈ 262 MB).
- **Stable, auth-free, CDN-backed download URLs:** `https://github.com/<owner>/<repo>/releases/download/<tag>/<file>` → redirects to GitHub's object CDN; `curl -L` on the rig follows it. No virus-scan interstitial, no rotating confirm token, no API key — the exact failure modes that made Drive fragile.
- **Tags map cleanly to the partition key.** One release tag per `chipset:turnip` epoch (e.g. `vault-SM8250-turnip26.1.0`), one asset per game. A new official Turnip → cut a new tagged release; the old one stays as a frozen archive. That IS the swarm's "partition rotation," for free.
- **`gh` CLI is the whole producer toolchain** you already have: `gh release create <tag> ./NPUA80075.zip --notes …`, `gh release upload <tag> …`, `gh api …/releases` to enumerate assets for the index.
- **The natural graduation signal:** Releases bandwidth is "fair use." At bootstrap scale it's free and ample; if a vault ever gets popular enough to strain it, *that is precisely the userbase that justifies the P2P swarm* (Model 3). Releases carries you until the swarm earns its way in. (Avoid Git **LFS** for this — its free tier is ~1 GB storage / 1 GB-mo bandwidth, far too small; Releases is the correct tool.)

---

## 6. SEQUENCE

| When | Build | Why |
|---|---|---|
| **Now (gate)** | Clean-room vault reproduces on a 2nd rig (§4.1) | Prove the payload is real before building pipes |
| **Then** | GitHub index (`vault-index/manifest.json`) + Releases-hosted bundles + `pro-tuning/install-protune.sh` (1+2 fused) | Ships the guaranteed result at lowest friction, on stable Turnip, $0 infra |
| **Next** | Pitstop auto-subscribe *pull* against the same index | Model 1's seamless side, for installed users |
| **Later** | Paddock tuning-signal layer, then the Syncthing swarm | Optimizes a *working* distribution system once N nodes exist + legal clears + Self-Update shipped |

---

## 7. WHAT THIS DOSSIER DELIBERATELY DEFERS

- **The P2P swarm** ([ShaderSwarmFeasibility.md](ShaderSwarmFeasibility.md)) in full — gated on Self-Update + Paddock + legal + a real N-node userbase. Its architecture (content-addressed bundles, `chipset:turnip:game` key, the index-as-registry) is *pre-built* by this fusion, so it remains the endgame, not a rewrite.
- **The Paddock tuning API / leaderboard** ([PaddockSwarmFeasibility.md](PaddockSwarmFeasibility.md)) — valuable community-signal layer, but it ships the signal without the payload; it slots in at the "Next/Later" rung, not the entry point.
- **Legal review of hosting derived-shader bytes centrally** — GitHub Releases is a *central* host (higher exposure than Model 3's tracker-only P2P). Bounded and one-time, but get the cheap review ([ShaderSwarmFeasibility.md](ShaderSwarmFeasibility.md) §H) before *public* bytes flow. Does not block a private/invite-only alpha.

---

## 8. OPEN ITEMS

1. Run the §4.1 reproduce-on-2nd-rig gate. Everything waits on green.
2. Decide the index repo layout: a dedicated `vault-index/` dir in the existing `etk` repo vs. a separate public repo (keeps the kit repo lean; separates legal surface).
3. Confirm `curl -L` on the rig fetches a GitHub Release asset end-to-end against a real >150 MB public asset (the Releases analog of the old Drive end-to-end check).
4. Tag scheme final: per-`chipset:turnip` release with per-game assets (recommended) vs. per-game tags.
5. `manifest.json` schema — extend the `pro_tuning/1` tier schema ([ProTuningExportDossier.md](ProTuningExportDossier.md) §6) with an index-level wrapper (list of games, asset URLs, hashes, provenance).
6. The breadcrumb-back-to-ETK line (§4.2) — exact copy + where it prints.

---

## 9. SCAFFOLD STATUS (2026-06-03)

Skeletons committed, syntax-clean, **not yet exercised end-to-end** (waiting on the §4.1 gate):
- `vault-index/manifest.json` — index with real GT HD (NPEA90002) + GT5P (NPUA80075) entries; homologation `mesa_hash` is the live official-20260601 value `c3e9e641…d834a7`. `bundle.sha256`/`size_mb`/`shader_count` are `PENDING_CLEAN_ROOM`, filled by `export.sh` after the fresh compile.
- `vault-index/README.md` — index schema + the PR-to-contribute path.
- `pro-tuning/install-protune.sh` — consumer (POSIX sh, rig-side): index lookup → `mesa_hash` gate → Releases download → sha256 verify → inject config + merge shaders → swappiness one-shot → setup sheet + ETK breadcrumb. Pure-data invariant held.
- `pro-tuning/export.sh` — producer (host bash + `gh`): build bundle from `./config` + `./vault`, write self-describing `manifest.json`, `gh release upload`, patch the index, emit the share one-liner. `pro-tuning/dist/` gitignored.

**Next exercise (post fresh GT HD/GT5P clean-room compile):** `export.sh NPUA80075 --publish --write-index` → grab the printed one-liner → run it against the wiped clean rig → confirm reproduce. That *is* the §4.1 gate.
