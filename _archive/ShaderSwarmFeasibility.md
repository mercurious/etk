# SHADER SWARM — DECENTRALIZED VAULT SHARING — FEASIBILITY DOSSIER
**Status:** Speculative feasibility analysis. Long-term roadmap target. Not implemented; multi-sprint multi-quarter when scheduled.
**Audience:** Claude Code (deep-dev implementer, future sessions) + operator (strategic decision-maker, possibly a future foundation/org)
**Provenance:** Operator question 2026-05-26 — "Could shader swarming be introduced by ETK devices announcing they are online and sharing their shader catalog and status, other ETK devices auto-discover the largest/freshest set matching their game library, and some clever way the device acts as a temporary web server / IPFS / BitTorrent / SM8250+gameID-keyed swarm, fully fail tolerant?"
**Companion dossiers (required prerequisites):**
- `RigSelfUpdateFeasibility.md` — the safety net under any feature that talks to a live remote API.
- `PaddockSwarmFeasibility.md` — the swarm-coordination surface (API, device ID, opt-out primitive) reused for shaders.

---

## §A. WHY (the Final Mission, finally addressable)

The README's "Final Mission" articulates this explicitly: *"The long term vision for the ETK is an integrated shader swarm system where your device automatically seeds and leeches shaders and proven emulation tunings over auto-subscribing device-centric bittorrent whisper nets during a battery charge."* This dossier is the engineering response.

Why this is the right time to scope it (even if not the right time to build it):

1. **The hard prerequisites are now thinkable.** Self-update gives us safe rolling deployments. PADDOCK gives us a device-ID primitive, an API, an opt-out toggle, a community surface, and a track record of operating a live service. Shader swarm composes on top of both; it cannot be built first.
2. **The UX moment is enormous.** A new operator installs GT6, opens Pitstop, hits "Download Shaders," and 5 minutes later launches the game with a 260 MB consensus vault already in place — skipping ~10 hours of crash-and-bank harvest. That's not a feature, that's the difference between "this kit is for me" and "this kit isn't for me" for the non-masochist tester segment.
3. **Tunings-first (PADDOCK) was the trojan-horse for THIS.** The PADDOCK rationale (start with the small thing, learn community ops, inherit userbase) explicitly framed shaders as the second phase. This is that second phase.
4. **The shape of the problem is favorable.** Shaders are **content-addressable by nature** (the filename IS the cache key hash), **immutable** (a banked shader never mutates), **additive-only** (no deletes), and **already sharded** (the 256-hex-prefix vault tree maps cleanly to swarm piece boundaries). This is BitTorrent-friendly to the point of being designed for it.

---

## §B. THE MODEL (what makes this swarm different from generic file-sharing)

### B.1 The partition key — chipset:mesa_hash:game_id

Shaders are device-specific. The same RPCS3 compile path produces different shader binaries on different Mesa builds. The vault is structurally `vault/$CHIPSET/$GAME_ID/shaders/<256-shard>/<31-byte hash>`. A swarm member announcing "I have shaders" without naming the chipset + Mesa build + game is useless — those shaders won't load on a peer with a different Mesa build ID.

**The swarm partition key is:**
```
SM8250 : mesa_<sha256-first-64KB-of-libvulkan_freedreno.so> : NPEA00502
```

The Mesa hash is **already captured** by install.sh Step 0 (the fingerprint detect we shipped 2026-05-26). It becomes the swarm partition's freshness primitive automatically — when Rocknix rebuilds Mesa, every device's partition key rotates; the old partition becomes archival and the new partition is the live swarm. The existing `tools/vault_sweep.sh` is the local-side complement to swarm-side partition rotation.

### B.2 Immutable, content-addressable, append-only

Each shader file in the vault is named by its Mesa cache key (62 hex chars = 31 bytes; the 1-byte prefix is the shard directory). Two devices that compile the "same" shader produce the same filename and (essentially) identical bytes — **trivial dedup, free integrity check**. Hash-the-file, compare to filename: if they match the shader is intact; if they don't, it's corrupt and gets discarded silently.

This eliminates entire categories of complexity that generic file-sharing systems have to handle (file conflicts, mtime races, partial writes, malicious mutation). Shader swarm is a **monotonically-growing append-only content-addressable distributed set**, partitioned by chipset:mesa:game. Math-wise, that's the easiest distributed-systems problem there is.

### B.3 The vault as a Merkle-friendly artifact

A vault's "manifest" is the union of its shard filenames. Two devices comparing manifests can detect deltas in O(N) without transferring any payload:
- Compute `manifest_hash = SHA256(sort(filenames))` per shard
- Compare per-shard manifest hashes
- Differing shards → request the file list for those shards → diff client-side → request missing files

This is the foundation of efficient incremental sync. **Bitmap-style "I have these shaders" announcements are tiny** (28k file vault → ~28k × 32 bytes = 900 KB Bloom filter is enough to negotiate any sync delta; or per-shard manifest hashes are 256 × 32 bytes = 8 KB and lose almost no precision).

### B.4 The vote-by-keep model carries over

From PADDOCK: a tune rises because devices keep using it. From SHADER-SWARM: a shader "rises" by virtue of existing on many devices. The swarm doesn't need an explicit popularity vote because **every device that has a shader file IS the popularity signal** — the existence count IS the heat metric. Cold shaders (one device only) get distributed minimally; hot shaders (50% of swarm membership has them) get prioritized in starter-pack downloads.

### B.5 What this swarm does NOT do (scope discipline)

- **Does not share tunings** — PADDOCK already does that, and the data shape is utterly different (text settings vs. binary blobs).
- **Does not share game files / .pkg / .rap / ROMs** — out of scope, legally and ethically distinct.
- **Does not share saves or telemetry** — out of scope; that's Tier-B host-side backup (install.sh).
- **Does not cross chipsets** — an SM8250 swarm member never talks to a Snapdragon X1 device about shaders; partition keys never collide.

---

## §C. ARCHITECTURE — comparative analysis of distribution options

### C.1 Five candidate models (with one favored)

| Model | Discovery | Transfer | NAT-friendliness | Cost model | Operational complexity |
|---|---|---|---|---|---|
| **A. R2-centric starter-pack only** | PADDOCK API | HTTPS pull from Cloudflare R2 | trivial (outbound only) | $0.015/GB/mo + free egress on CF | low |
| **B. BitTorrent w/ Cloudflare tracker** | PADDOCK API as tracker | torrent client on rig | medium (DHT helps) | tracker is cheap; seeders are devices | high (client install, tuning, port forwarding, legal posture) |
| **C. IPFS + cluster pinning** | IPFS DHT or PADDOCK as bootstrap | IPFS native | medium-good | pinning service or self-hosted cluster | very high (IPFS daemon resource cost; reliability is poor in practice) |
| **D. WebRTC P2P with TURN relay** | PADDOCK API as signaling | WebRTC data channels | excellent (designed for it) | TURN relay ~$5-50/mo at small scale | high (WebRTC stack on Rocknix is nontrivial) |
| **E. Syncthing folder per partition key** | PADDOCK API as device-ID introducer | Syncthing native (BEP) | excellent (built-in NAT traversal + free relay net) | $0 (Syncthing handles it) | **low** — daemon already on rig |

**The favored model is E (Syncthing) layered with A (R2 starter-pack) for cold-start UX.**

Reasoning below.

### C.2 Why Syncthing wins

The 2026-05-26 audit found `syncthing` already installed on the Rocknix rig. This is not coincidence — Syncthing ships with many embedded distros because it's a small, battle-tested, BSD-3-licensed continuous file synchronization tool that solves the EXACT problem set:

| Shader-swarm need | Syncthing native primitive |
|---|---|
| Device discovery | Global Discovery Server (free public infra) or LAN broadcast |
| NAT traversal | Built-in (uses STUN-like discovery + Global Relay Server pool) |
| Cryptographic device identity | Long ed25519 device IDs (stronger than our `ETK_xxxxxxxx` hash) |
| Folder versioning & resume | Native, atomic, resumable |
| Per-folder ACLs | Per-folder device list; partition keys map naturally |
| Bandwidth throttling | Native, configurable per-device |
| Operational maturity | 10+ years, large user base, active maintenance |
| License | MPL-2.0 (compatible with ETK's GPL-2) |

**The shader swarm becomes "a Syncthing folder per partition key, with the PADDOCK API serving as the device-ID introduction registry."** Each partition (chipset:mesa:game) is one Syncthing folder. Devices learn each other's Syncthing IDs via PADDOCK; once introduced, Syncthing handles transfer.

This eliminates:
- ~80% of the custom code (Syncthing IS the swarm engine)
- All NAT traversal logic (built in)
- All cryptographic identity work (built in, stronger than what we'd roll)
- All resume-on-blip logic (built in)
- All file integrity verification (built in via BEP)

### C.3 Why R2 starter-pack still matters (layered with Syncthing)

A pure-Syncthing swarm has a cold-start problem: when device #1 installs a brand-new game, there's no swarm to sync with. Even when the swarm has 50 members, joining mid-stream means a slow build-up as Syncthing discovers and pulls.

**R2 starter-pack closes this gap:**
- Once a partition has > N devices contributing, a Worker cron exports a consensus tarball (the union of shaders present on ≥ M% of partition members) to R2
- A fresh device's Pitstop UX: "Download starter pack (262 MB, takes ~3 minutes on home wifi)" before it even joins Syncthing
- After starter-pack lands, Syncthing joins for incremental sync going forward

The R2 cost is bounded (compressed per-partition tarballs are deduplicated; weekly regeneration). The user-experience win is the difference between "instant playable" and "slowly building up over days."

### C.4 The composed architecture

```
                                                                
   ┌───────────────────────┐                                    
   │  PADDOCK Worker API    │                                    
   │  (Cloudflare)         │                                    
   │                       │                                    
   │  GET /swarm/peers      ◄────┐  rig asks: "who's online      
   │   ?partition=...        │   │  with my partition key?"    
   │                       │   │                                
   │  POST /swarm/announce  ◄────┤  rig says: "I'm here, my     
   │                       │   │  syncthing_id=XXXX,            
   │                       │   │  manifest_hash=YYYY"           
   │                       │   │                                
   │  GET /swarm/starter    ◄────┤  rig says: "give me cold-     
   │   ?partition=...        │   │  start tarball URL"          
   └─────────────────┬─────┘   │                                
                  │           │                                
                  ▼           │                                
   ┌───────────────────────┐  │   ┌──────────────────┐         
   │  Cloudflare R2        │  │   │  Other ETK rigs   │         
   │  (starter packs only) │  │   │  (peers)          │         
   │                       │  │   │                  │         
   │  /SM8250/<mesa>/      │  │   │  syncthing       │         
   │   <game>/starter.tgz  │  │   │  daemon         │         
   └─────────────────┬─────┘  │   └────────┬─────────┘         
                  │           │            │                  
                  │ HTTPS     │            │ BEP (P2P)         
                  ▼           │            ▼                  
            ┌─────────────────┴─────────────────┐              
            │     YOUR ETK RIG                  │              
            │  ┌───────────────────────┐         │              
            │  │ bin/swarm_d.sh        │         │              
            │  │  - announce loop      │         │              
            │  │  - starter-pack pull  │         │              
            │  │  - syncthing config   │         │              
            │  │    generation         │         │              
            │  └────────┬──────────────┘         │              
            │           ▼                       │              
            │  ┌───────────────────────┐         │              
            │  │ syncthing daemon       │         │              
            │  │  - one folder per      │         │              
            │  │    partition key       │         │              
            │  │  - peers from PADDOCK  │         │              
            │  └────────┬──────────────┘         │              
            │           ▼                       │              
            │  $VAULT_DIR/SM8250/<mesa>/<game>/  │              
            │  /shaders/  (Mesa reads from here) │              
            └───────────────────────────────────┘              
```

### C.5 What the PADDOCK API adds for shaders (vs. tunings)

New endpoints on the existing API (no new service):

| Endpoint | Purpose |
|---|---|
| `POST /v1/swarm/announce` | Rig announces presence: partition_key, syncthing_id, manifest_hash, manifest_card_count, last_seen |
| `GET /v1/swarm/peers?partition=<key>` | Returns up to N best peers (largest+freshest manifests) for a partition |
| `GET /v1/swarm/starter?partition=<key>` | Returns signed R2 URL for the consensus tarball (or 404 if partition lacks consensus quorum) |
| `POST /v1/swarm/leave` | Rig announces it's going offline (best-effort; lease-expires anyway) |

Leases auto-expire (e.g. 15 min); devices that drop offline disappear from the peer list without explicit teardown.

### C.6 Mesa-rebuild partition rotation (the failure-mode-as-feature)

When the operator runs install.sh and Step 0 detects a new Mesa build:
1. install.sh logs `MESA REBUILD DETECTED` (current behavior, shipped 2026-05-26)
2. Pitstop PADDOCK could automatically: announce the OLD partition's manifest one last time (archival), then start announcing under the NEW partition key
3. The Worker cron eventually retires old-partition starter packs when no devices remain on the old Mesa build (after ~30 days)

This is **not a failure** — it's the swarm's renewal cycle, mapping cleanly to Rocknix's nightly cadence.

---

## §D. COMPONENTS (the build list)

| Surface | Artifact | Effort |
|---|---|---|
| Rig | `bin/swarm_d.sh` — announces presence, queries peers, manages syncthing config | ~16 hr |
| Rig | Syncthing configuration generator (per-partition-key folder management) | ~12 hr |
| Rig | install.sh additions — syncthing systemd unit, opt-out wiring | ~4 hr |
| API | New PADDOCK endpoints (§C.5) | ~12 hr |
| API | D1 schema additions: `swarm_announcements`, `swarm_partitions` | ~4 hr |
| API | R2 starter-pack cron Worker (consensus union → tarball → upload) | ~16 hr |
| Pitstop | SWARM section under existing PADDOCK tab (or new tab) — peers online, manifest size, "Download Starter Pack" CTA, "Pause Sharing" toggle | ~12 hr |
| Docs | README SWARM section + privacy + legal callouts | ~4 hr |
| Legal | Lawyer review of "is sharing GPU-compiled shaders derived from PS3 game code legal" — must happen BEFORE first public bytes flow | $1500-5000, ~2 weeks |

**Total deep-dev: ~80 hr** spread across multiple sprints. Operator-side prep includes the legal review which is the only true blocker (everything else can be MVP'd and iterated).

---

## §E. DEPENDENCIES & PRECONDITIONS

**On rig (audited 2026-05-26):**
- ✓ `syncthing` — already installed; this is the load-bearing finding
- ✓ `curl`, `jq`, `python3`, `systemctl`, `tar` — from prior audits
- ✗ no `rust`, `go`, `ipfs`, `transmission` — irrelevant given Syncthing-as-engine
- ✓ Mesa fingerprint already captured by install.sh (`$ETK_ROOT/vault/.last_mesa.hash`)
- ✓ Vault already structured as `vault/$CHIPSET/$GAME/shaders/<256-shard>/<hash>` — maps to Syncthing folder root

**On host:**
- Cloudflare account, R2 enabled
- `gh` CLI (for any release tooling)

**Pre-shipping:**
- Self-update shipped (`RigSelfUpdateFeasibility.md` Phase 1-4)
- PADDOCK live (`PaddockSwarmFeasibility.md` Phase 1-7)
- Legal review of shader-distribution posture complete (see §H)

**Network:** rig is on standard home NAT (private LAN behind a residential ISP). Syncthing's Global Relay handles NAT traversal natively at zero extra ops cost.

---

## §F. PARTITION KEY MATH (Mesa rebuilds = swarm renewal events)

A partition is `chipset:mesa_hash:game_id`. Empirical scale today (one operator's rig):

| Game | Partition | Vault size | File count |
|---|---|---|---|
| GT6 | SM8250:<current-mesa>:NPEA00502 | 262 MB | 28,688 |
| GT5P | SM8250:<current-mesa>:NPUA80075 | 172 MB | 19,086 |
| GT5 | SM8250:<current-mesa>:BCUS98114 | 38 MB | 3,989 |
| GT HD | SM8250:<current-mesa>:NPEA90002 | 15 MB | 1,373 |
| LBP | SM8250:<current-mesa>:NPUA80472 | 10 MB | 750 |

Storage math:
- Per-partition starter-pack tarball (gzipped): ~30-60% of vault size → ~80-160 MB for GT6
- R2 storage for 100 partitions × 100 MB avg = 10 GB = **$0.15/mo**
- R2 egress to Cloudflare → free (Workers serve URLs from same region)
- Syncthing P2P transfer cost: $0 (peer-to-peer, free relays as fallback)

Mesa-rebuild cadence: Rocknix nightly rebuilds Mesa weekly-ish per the empirical data the operator captured. Each rebuild creates a new partition; old partitions go cold within ~2 weeks as the swarm migrates. R2 should garbage-collect partitions with no active devices for > 30 days.

---

## §G. FAILURE MODES & RECOVERY

| Failure | Detection | Recovery |
|---|---|---|
| No peers online for your partition | PADDOCK `/swarm/peers` returns empty list | Pitstop shows "be the first to seed SM8250 GT6 on Mesa <hash>" — graceful empty state |
| Cold-start partition (no starter-pack yet) | PADDOCK `/swarm/starter` returns 404 | Same as above; partition needs to reach quorum (e.g. 3 devices) before starter pack generates |
| Syncthing daemon crash | `systemctl is-active syncthing` returns false | Sentry-style watchdog respawn (same model as input_d.py); reported to PADDOCK as `swarm_pause` event |
| Disk full mid-sync | Syncthing's own out-of-space handling | Syncthing pauses the folder; Pitstop SWARM tab shows "PAUSED — disk full" |
| Corrupt shader received | Content hash mismatch (filename ≠ SHA256(file content within tolerance)) | File quarantined; never loaded into vault; reported as `swarm_bad_blob` event |
| Mesa fingerprint changes mid-sync | install.sh Step 0 detects on next run | Old partition's folder enters archival mode (no new fetches); new partition's folder bootstraps |
| Bandwidth abuse (rig saturates uplink during gameplay) | Empirically observed FPS drop | Syncthing bandwidth throttle config; pause-during-game heuristic via Sentry RUNNING→IDLE state |
| Malicious peer floods with junk | PADDOCK reputation aging | Peers ranked by manifest agreement with consensus; bad peers naturally sink in rankings; reports collected |
| Total swarm collapse / Cloudflare incident | PADDOCK health endpoint down | Local vault unaffected; SWARM tab shows OFFLINE; falls back to "what you have"; install.sh from host still works as ultimate backup |
| Partition fork (two consensus tarballs disagree) | Worker cron detects diverging quorum | Two starter packs published as `consensus_a.tgz` / `consensus_b.tgz`; Pitstop offers both, weighted by recency |

---

## §H. LEGAL POSTURE (the real gate; lawyer required before public bytes flow)

**This is the open question the dossier cannot answer alone.** Shaders are GPU-compiled binaries derived from PS3 shader bytecode through Mesa Turnip. Three possible legal framings:

1. **Pure derivative work compatible with RPCS3's GPL-2.** The shader binaries are Mesa's output, not Sony's input. Mesa is open-source; the compilation result inherits that posture. **Likely correct, but needs lawyer confirmation.**
2. **Functional element of the copyrighted game.** A court could argue the shader cache is a "running representation" of the game's GPU code, no different from a runtime memory dump. **Less likely, but possible.**
3. **Hardware-specific compilation outputs.** Specific to Snapdragon Adreno + Mesa build ID; not portable to other hardware; arguably no different from a JIT cache. **Probably the strongest defense.**

**Mitigations that reduce exposure regardless of framing:**
- The PADDOCK API never hosts shader bytes — devices share peer-to-peer. The central party (Cloudflare-hosted PADDOCK) only brokers introductions. Same legal posture as BitTorrent trackers (settled US law: trackers are not infringing).
- R2 starter-pack tarballs DO host bytes centrally → this is the higher-exposure component → may need to be omitted if lawyer says so → swarm functions without it, just with worse cold-start UX.
- All shared content is content-addressable + chipset-specific + Mesa-version-specific → demonstrably non-portable, demonstrably derived from open-source Mesa output.
- Explicit ToS: "shader cache contents may include derivative GPU bytecode; you participate in this swarm at your discretion" — informed consent layer.

**Recommendation:** $1500-5000 lawyer review focused specifically on (a) tracker-only model legality, (b) starter-pack-hosted model legality, (c) ToS language for informed consent. This is a one-time cost; results are durable.

**Until lawyer review is complete, this dossier remains speculative.** No code that talks to a real network of devices should ship without that gate cleared.

---

## §I. PHASED IMPLEMENTATION PLAN

### Phase 0 — Strategic prep + legal (no code; weeks 0-4)
- Confirm Self-Update + PADDOCK have shipped
- Engage lawyer for §H review (~2 weeks turnaround)
- If lawyer green-lights tracker-only model: proceed. If red-lights: redesign as "shader-recipe sharing" (config snippets that reproduce the compile) instead of "shader-binary sharing." Re-scope the dossier.

### Phase 1 — Local Syncthing dry-run (~8 hr)
- Stand up two test rigs OR one rig + one Mac with Syncthing
- Manually configure a Syncthing folder pointing at one game's vault
- Verify shader files sync correctly; verify Mesa loads them post-sync
- Zero API involvement; pure proof-of-concept

### Phase 2 — PADDOCK swarm API endpoints (~16 hr)
- `POST /v1/swarm/announce`, `GET /v1/swarm/peers`, `POST /v1/swarm/leave`
- D1 schema additions
- Manual curl testing

### Phase 3 — `bin/swarm_d.sh` rig-side daemon (~16 hr)
- Reads `$ETK_ROOT/vault/$CHIPSET/$MESA_HASH/$GAME_ID/shaders/` (note: vault path may need refactor to include mesa_hash in path — see §K.1)
- Announces presence every 5 min
- Queries peers, configures Syncthing accordingly
- Manages Syncthing folder lifecycle (add/remove on Mesa rebuild)

### Phase 4 — Pitstop SWARM surface (~12 hr)
- New section in PADDOCK tab (or new tab if cleaner): peer count, manifest size, sync status
- "Pause Sharing" toggle (different from full opt-out — temporary, in-session pause)
- Starter-pack CTA for fresh games

### Phase 5 — R2 starter-pack cron (~16 hr)
- Worker cron computes per-partition consensus (shader files present on ≥ M% of partition members)
- Builds tarball, uploads to R2
- API serves signed URL to requesters

### Phase 6 — Pause-during-game heuristic (~4 hr)
- Sentry RUNNING state pauses Syncthing folders to preserve in-game bandwidth + thermal
- Sentry RUNNING→IDLE resumes
- Configurable bandwidth ceiling

### Phase 7 — Reputation / consensus monitoring (~8 hr)
- Worker cron detects partition forks
- Peers ranked by manifest agreement with consensus
- Bad-actor blob reports collected for review

### Phase 8 — Soft launch + monitoring (weeks N+)
- Invite-only, monitored
- Bandwidth telemetry, abuse detection
- Iterate on starter-pack quorum thresholds

**Total deep-dev: ~80 hr across 8 phases.** Comparable to PADDOCK scope. Phase 0 (legal) is the gating step; everything else is downstream.

---

## §J. ACCEPTANCE CRITERIA

1. **Self-update + PADDOCK shipped first.** Non-negotiable; this dossier composes on top of both.
2. **Opt-out is one toggle.** Same UX as PADDOCK; `ETK_SWARM_ENABLED=0` in env.sh kills all swarm traffic.
3. **No PII transmitted.** Same as PADDOCK; device ID is a hash; shader content carries no metadata except its cache key.
4. **Partition key is correctly computed.** chipset + Mesa hash + game ID; verified by canary test (devices with different Mesa hashes don't see each other).
5. **Sync pauses during gameplay.** RUNNING state → Syncthing folders paused; verified by bandwidth measurement during a known game session.
6. **Corrupt shaders quarantined.** Test with deliberately corrupted blob; verified rejected before vault placement.
7. **Mesa rebuild rotates partitions gracefully.** Probe: bump Mesa hash, verify old partition enters archival, new partition bootstraps without operator action.
8. **Cold start delivers measurable UX win.** Fresh-vault test against partition with consensus starter-pack: time-to-playable < 10 min vs. >10 hours baseline.
9. **Local vault never destructively modified by swarm.** Swarm only adds files; deletes are local-operator-only (via `vault_sweep.sh`).
10. **Disaster recovery preserved.** A broken swarm release rolls back via self-update; install.sh from host remains the ultimate restoration path.

---

## §K. OPEN QUESTIONS

### K.1 Vault path refactor to include mesa_hash?

Today the vault is `vault/$CHIPSET/$GAME_ID/shaders/<shards>`. For the swarm to partition correctly, the path may need to become `vault/$CHIPSET/$MESA_HASH/$GAME_ID/shaders/<shards>` so the local layout matches the swarm partition naturally.

**Tradeoffs:**
- Pro: zero translation layer between local layout and swarm partition; `vault_sweep.sh` becomes simpler (sweep = delete obsolete mesa-hash subtree)
- Con: every Mesa rebuild forces a full re-link of `RPCS3_CACHE_DIR` (currently handled by `etk_link_cache` in the Sentry); doable but invasive
- Alternative: keep the current layout, swarm-side maintain a symbolic `mesa_hash → path` mapping per device

Recommend: prototype both in Phase 1, pick whichever Mesa-cache-symlink experience is cleaner.

### K.2 Pause-during-game: bandwidth or thermal?

Sentry RUNNING state could pause Syncthing for two reasons:
- Bandwidth (preserve wifi for game)
- Thermal (Syncthing-induced CPU work adds heat)

Likely both matter. Phase 6 should measure each empirically before deciding the heuristic threshold.

### K.3 Operator's own seeded shaders — flag attribution?

PADDOCK has the "hash as bragging right" mechanic (`PaddockSwarmFeasibility.md` §B.5.a). Does the shader swarm get the same? "I was the first device to seed this shader file" — but this is meaningless because the same shader compiles identically on identical hardware+Mesa. There's no creativity to attribute. **Recommend: no attribution layer for shaders.** Tunes have authorship-like attribution; shaders are pure facts.

### K.4 What about non-SM8250 chipsets?

If ETK ever supports a second chipset, the partition key gracefully handles it (it includes `$CHIPSET`). The two chipsets' swarms are entirely separate; no cross-contamination is even possible (the shaders won't load on the wrong chip).

### K.5 P2P privacy — does peer-discovery leak more than centralized?

In the proposed model, two devices that sync the same partition see each other's Syncthing device IDs and IP addresses (because P2P). This is a step beyond PADDOCK's "API knows your hash." Privacy-conscious users may want a relay-only mode where Syncthing only uses Global Relay (never direct), at bandwidth cost.

Recommend: add `ETK_SWARM_RELAY_ONLY=1` env knob for paranoid users. Document the tradeoff.

### K.6 Shader expiration / GC

Vault grows monotonically without `vault_sweep`. Should the swarm participate in suggesting sweeps? E.g. "this shader file is on no other partition member, last accessed N days ago → candidate for sweep"?

Probably not in v1 — keeps the swarm scope clean. Local `vault_sweep.sh` remains operator-controlled.

### K.7 Starter-pack signing

If R2 hosts starter packs, those packs should be signed by a release key so a compromised R2 bucket can't inject hostile shaders. Public key embedded in install.sh; signing key held by operator.

Add to Phase 5 work.

---

## §L. TL;DR

- **Feasible — but the heaviest of the three dossiers** (~80 hr deep-dev + legal review). Composes on top of Self-Update + PADDOCK; cannot ship before either.
- **Syncthing is the load-bearing finding** of the 2026-05-26 audit — already installed on the rig, solves NAT/discovery/integrity/identity/resume natively. The swarm is "Syncthing folder per partition key with PADDOCK as the device-ID introduction registry." ~80% less custom code than rolling our own WebRTC / IPFS / BitTorrent stack.
- **Partition key = chipset:mesa_hash:game_id.** Mesa rebuilds rotate the swarm partition cleanly; the existing `vault/.last_mesa.hash` mechanism (shipped 2026-05-26) becomes the swarm's freshness primitive automatically. Nothing else captures the swarm's structural reality as cleanly.
- **Shaders are the easiest distributed-systems shape possible:** content-addressable, immutable, additive-only, already sharded. The math is "monotonically-growing append-only set partitioned by chipset:mesa:game" — well-understood territory.
- **R2 starter-pack layer is the cold-start UX win.** Optional in the legal-conservative design (omit if lawyer says so), but the difference between "10 minutes to playable" and "10 hours to playable" for a fresh tester.
- **Legal review is the real gate** (§H). $1500-5000 for a one-time review focused on (a) tracker-only model legality, (b) R2-hosted starter-pack legality, (c) ToS informed-consent language. Until that clears, this dossier remains speculative.
- **The killer UX moment is operator-defining:** "I installed GT6, opened ETK Pitstop SWARM, hit Download Starter Pack, played a clean session 5 minutes later." That's the experience that turns the kit from "for ETK developers" into "for emulation tourists." It's also the experience that justifies the entire effort if it lands.
- **No PII, no accounts, opt-out is one toggle, pause-during-game preserves thermals + bandwidth, local vault is never destructively modified by swarm.** The contract carries the same shape as PADDOCK's.
- **The three dossiers compose into one roadmap:** Self-Update → PADDOCK → SHADER-SWARM. Each layer earns the right to build the next. Each layer's userbase + trust + ops experience seeds the next layer's success.
