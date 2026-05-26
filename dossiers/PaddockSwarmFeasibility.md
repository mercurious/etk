# PADDOCK — TUNING SWARM SERVICE — FEASIBILITY DOSSIER
**Status:** Feasibility analysis. Not implemented. Captured for a future multi-sprint workstream.
**Audience:** Claude Code (deep-dev implementer, future sessions) + operator (strategic decision-maker)
**Provenance:** Operator question 2026-05-26 — `emulationtuningkit.com` + community tuning-swarm service hosted on Cloudflare; ETK Pitstop adds a SHARING / PADDOCK tab; auto-subscribed, accountless, signal-weighted; tunings-first (defer shaders + P2P).
**Companion dossier:** `RigSelfUpdateFeasibility.md` — self-update is the load-bearing precondition for shipping anything to alpha testers; this dossier assumes that ships first.

---

## §A. WHY (the strategic shape of the proposal)

The operator's framing is correct and load-bearing — quote-paraphrased and stress-tested below:

1. **Bootstrap community on the smallest surface first.** Tunings are tiny (~2 KB JSON), text (legible, debuggable, no binary blob legal questions), and have no plausible copyright exposure (settings are facts, not assets). Shaders are megabytes, binary, and adjacent to derived-work questions. **Ship the small thing first, learn community ops on it, inherit the audience for the bigger thing later.**
2. **Centralized IP before P2P.** A Cloudflare-hosted REST API is `O(weeks)` to ship; a P2P shader swarm (the "Final Mission" auto-subscribing whisper net) is `O(months)` and demands a real userbase to even validate. Centralized is the right entry point — it produces the userbase that justifies the P2P investment.
3. **Auto-subscribe with opt-out beats opt-in with effort.** A community feature that requires the operator to explicitly join is a feature that 10% of users join. Auto-subscribe with a single opt-out toggle in env.sh / Pitstop is the only architecture that produces a real swarm signal. **This must be documented loudly** in onboarding to be ethically defensible (see §F).
4. **Accountless and authorless makes the auto-tune insight work.** Reddit-style explicit votes require accounts, breed brigading, and produce social-graph drama. **Usage as vote** (a tuning rises because devices keep it, not because they click thumbs-up) maps directly to what the operator cares about — does this tune actually work in the wild — and is the only design where the swarm output is "the truth" rather than "the popular opinion."
5. **The killer differentiator over generic "config sharing" sites:** PADDOCK can show **WHY** a tune rose. ETK already captures `sessions.tsv` (crash_sig, peak_temp, duration, shaders_harvested) and `config_changes.tsv` (which dials moved when). The API can ingest aggregate session deltas and surface "this tune rose because median session length jumped from 4 min to 23 min and panic rate dropped from 60% to 8%." No other emulation-tuning community does this because no other kit instruments to this depth.

---

## §B. THE MODEL (auto-subscribe, accountless, signal-weighted)

### B.1 The unit of share — the literal config_<ID>.yml file

**The transaction artifact is the actual RPCS3 per-game config YAML** stored at `$RPCS3_CUSTOM_CONFIGS/config_<gameID>.yml` (e.g. `/storage/games-internal/roms/bios/rpcs3/custom_configs/config_NPEA00502.yml`, 278 lines for GT6 today). This is the file RPCS3 reads at game launch; sharing it whole means **the bits that produce the result are the bits that get transacted** — no schema-subset translation layer, no "does the subset capture everything that matters" debate, no `pitstop_fields.json`-vs-config drift risk.

Submission payload:

```json
{
  "game_id": "NPEA00502",
  "etk_schema_version": "v0.2.0",
  "rpcs3_version": "<probed from RPCS3 binary>",
  "device_id": "ETK_8f3a91c2",
  "config_yaml": "<full content of config_NPEA00502.yml as a string>",
  "config_hash": "<SHA256 of canonicalized YAML>",
  "session_signal": {
    "n_sessions": 12,
    "median_duration_s": 1380,
    "panic_rate": 0.08,
    "median_shaders_per_session": 47
  },
  "submitted_at": 1748257200
}
```

~10-15 KB per record (vs. the ~2 KB a curated subset would be). Still trivially within free-tier D1 limits (5 GB storage handles ~500k tunings comfortably).

**Implementation wrinkle — canonical hashing.** YAML serialization is non-deterministic (key ordering, whitespace, comment preservation). For dedup-by-content the API must canonicalize before hashing: parse → sort keys → re-emit with stable formatter → SHA256. Otherwise two byte-identical-in-effect tunes get treated as distinct tunings. This belongs in the Worker, not in Pitstop (the rig sends raw YAML; the server canonicalizes + hashes).

**Why this is better than a curated-subset share unit:**
- No translation layer between what the operator tuned and what gets shared → no "the subset missed the dial that mattered" failure mode.
- `pitstop_fields.json` is the editor's curated dial-set (UX-scoped); the YAML is the truth. Conflating editor scope with share scope was my mistake in v1 of this dossier.
- RPCS3-version compatibility is **easier** with the full file than the subset: RPCS3 silently ignores unknown keys and defaults missing ones, so an old-RPCS3 tune mostly works on new-RPCS3 without translation.
- The future "this rose because median duration jumped 400%" surface is unaffected — that math is in `session_signal`, independent of the YAML.

### B.2 The outbound signal — already wired

`bin/etk_pitstop.py:84-92` already writes `$TELEMETRY_DIR/config_changes.tsv` on every edit:
```
epoch	game_id	field_label	old_value	new_value
```

This is the **per-device tuning history feed** for the swarm — no new instrumentation needed. PADDOCK upload is the existing ledger streamed to the API on a schedule (or on Pitstop's "I kept this tune" confirmation).

### B.3 The inbound weighting signal — already wired

`bin/session_postmortem.sh` writes `$TELEMETRY_DIR/sessions.tsv`:
```
epoch  duration_s  build  game_id  status  peak_load  peak_ram_mb  peak_temp  avg_temp  crash_sig  fence_at_crash  shaders_harvested  drain_pct  thermal_overrides
```

Per game, per device, per session. **The swarm weighting math has its inputs already.** A tuning's "score" can be derived from the aggregate ledger of all devices that ran it. No new on-device telemetry is needed for the MVP.

### B.4 The vote mechanism — usage IS the vote (frictionless to the literal click)

**No thumbs-up button anywhere.** A tune rises because devices keep it past their road-test; it sinks because devices revert it. The road-test UX, exactly as the operator designed it:

```
ETK Pitstop → PADDOCK → [select top tune from list] → "Road Test"
  └── Pitstop:
        1. cp config_<ID>.yml  →  config_<ID>.yml.bak
        2. curl https://api.emulationtuningkit.com/v1/tuning/<tuning_id>
           → write to config_<ID>.yml
        3. queue road_test_started event (silent, eventually consistent)

[operator launches the game, plays a session]

ETK Pitstop → PADDOCK → "Revert Tune, End Road Test"
  └── Pitstop:
        1. mv config_<ID>.yml.bak  →  config_<ID>.yml  (restore)
        2. queue revert event for upload (= downvote signal)
```

The complementary "keep" path is the **absence of revert**: the user just doesn't come back to End Road Test. The `.bak` lingers on disk, the tune stays applied, and after a configurable confirmation window (e.g. "you've used this tune for 3 sessions / 30 minutes of gameplay") Pitstop auto-promotes the road test to permanent (deletes the `.bak`, queues `keep` event). No "Are you sure?" modal — the user proved they like it by not reverting.

**Auto-revert on PANIC within N seconds** of road-test apply (configurable, default 60s) → `panic_revert` event uploaded next time the rig is online; restores the `.bak` automatically on next Sentry boot. Hard signal that this tune broke something.

The swarm top-10 is ranked by **active install count** (weighted by recency + session-signal quality + diversity bonus, see §C.4), not by accumulated clicks. Sybil-resistant by construction: you need a real session of real shader compile to register, which is a meaningful cost.

### B.4.a Auto-submission of operator-tuned configs (the load-bearing default)

The same loop runs **without** the road-test ceremony when the operator tunes a config themselves in the TUNING tab. Every saved edit to `config_<ID>.yml` is uploaded automatically — your own tuned config goes into the swarm list with no confirmation step. This is the load-bearing default that makes the swarm self-bootstrapping: every active ETK install becomes a contributing node from day one, not "from the day the operator remembered to share."

The privacy contract carries proportional weight: §F.1 must spell out that **every** tuning save uploads, and the opt-out toggle must be **trivially discoverable** (it is — it's right there in the PADDOCK tab itself). No surprises, no hidden upload paths.

### B.5 Anonymous, pseudonymous, or accountless — pick one and own the distinction

Truly anonymous means no stable identifier, which means no vote-tracking, which kills the auto-swarm. The proposal needs **pseudonymous + accountless**:

- A stable per-device hash `ETK_<8hex>` derived from `SHA256(first_MAC + first_install_epoch)` written once to `$ETK_ROOT/.etk_device_id` and exported via env.sh as `ETK_DEVICE_ID`.
- No email, no account, no PII.
- **Operator-rotatable** — Pitstop offers "Reset Device ID" which regenerates the hash, breaking linkage to any prior swarm history. This is the GDPR right-to-be-forgotten primitive without lawyers.

This is honest pseudonymity (a stable hash IS technically linkable across calls from the same device), not false anonymity. Privacy policy must say this plainly.

### B.5.a Hash-as-bragging-right (the elegant attribution primitive)

The leaderboard publicly displays the `first_seen_device` hash next to each tune (e.g. `tune #a3f7 · first seen from ETK_8f3a91c2`). Pitstop renders YOUR device's hash visibly on the PADDOCK screen ("Your device: ETK_8f3a91c2").

The mechanism this creates: **the only way to claim authorship of a top tune is to publicly share your own hash**. There is no `author_name` field on any record, no profile, no account. If someone wants the social credit for ETK_8f3a91c2's #1 tune for GT6, they post their hash on Reddit / Discord / wherever and let other people verify against the leaderboard. They opted out of anonymity, deliberately, with no infrastructure to support the opt-out.

This is genuinely elegant:
- **Default state is anonymous.** Nobody sees your hash except you.
- **Attribution is operator-controlled and operator-cost.** If you want credit, you share your hash. The act of sharing it is the act of de-anonymizing.
- **Reset Device ID is the un-brag button.** Severs your hash from prior history; you're nobody again.
- **No moderation surface.** No accounts to ban, no profiles to police, no impersonation to chase. The only identity is a hash you do or don't share.

This is the cleanest authorless-but-attributable design I've seen in any community-tuning system. It deserves naming in the privacy policy and the PADDOCK README so users understand the mechanic on first contact, not by accident.

---

## §C. ARCHITECTURE

### C.1 The four surfaces

1. **`emulationtuningkit.com`** — static promo site (Cloudflare Pages). Hero + screenshots + "Get the kit" GitHub link + Privacy/Terms.
2. **`api.emulationtuningkit.com`** — Cloudflare Worker REST API. Endpoints in §C.3.
3. **Cloudflare D1** — SQLite at the edge. Schema in §C.2.
4. **Pitstop PADDOCK tab** — new 4th tab in `bin/etk_pitstop.py`. The `TABS` registry at line 183 was designed for additions ("Adding a future tab is one line here").

Cloudflare is the right pick for free-tier-generous + geo-distributed + Workers/D1/R2 stack-compatibility. Operator already mentioned it; no reason to second-guess.

### C.2 D1 schema (MVP)

```sql
-- Each unique tuning (deduped by content hash) is one row.
CREATE TABLE tunings (
  id TEXT PRIMARY KEY,              -- SHA256(canonical_json(tuning))[:16]
  game_id TEXT NOT NULL,
  etk_schema_version TEXT NOT NULL,
  tuning_json TEXT NOT NULL,        -- the 21-field share unit, canonical JSON
  first_seen INTEGER NOT NULL,      -- epoch of first submission
  first_seen_device TEXT NOT NULL   -- ETK_xxxxxxxx (NOT exposed publicly)
);
CREATE INDEX idx_tunings_game ON tunings(game_id, etk_schema_version);

-- Active install events. One row per Keep/Revert/PanicRevert.
CREATE TABLE install_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tuning_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  game_id TEXT NOT NULL,
  event TEXT NOT NULL,              -- 'keep' | 'revert' | 'panic_revert'
  session_signal_json TEXT,         -- {n_sessions, median_dur, panic_rate, ...}
  reported_at INTEGER NOT NULL
);
CREATE INDEX idx_events_tuning ON install_events(tuning_id, reported_at);
CREATE INDEX idx_events_device ON install_events(device_id, reported_at);

-- Materialized per-game leaderboard (recomputed periodically by a Worker cron).
CREATE TABLE leaderboard (
  game_id TEXT NOT NULL,
  etk_schema_version TEXT NOT NULL,
  rank INTEGER NOT NULL,
  tuning_id TEXT NOT NULL,
  active_installs INTEGER NOT NULL,
  ledger_score REAL NOT NULL,       -- composite, see §C.4
  why_text TEXT,                    -- "rose because median duration +400%, panic rate -85%"
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (game_id, etk_schema_version, rank)
);
```

### C.3 API endpoints (Worker)

| Method + Path | Purpose | Rate-limit |
|---|---|---|
| `GET /v1/tunings/<game_id>?etk=<ver>&list=top10` | Top 10 by score for game | 60/hr/device |
| `GET /v1/tunings/<game_id>?etk=<ver>&list=newest10` | Newest 10 first-seen | 60/hr/device |
| `GET /v1/tuning/<tuning_id>` | Fetch one tuning by ID | 120/hr/device |
| `POST /v1/submit` | Submit a new tuning (idempotent on content hash) | 10/hr/device |
| `POST /v1/event` | Report keep/revert/panic_revert | 20/hr/device |
| `GET /v1/health` | Liveness for Pitstop's "PADDOCK: online/offline" indicator | unlimited |

Auth: none. Identity: `X-ETK-Device-ID: ETK_xxxxxxxx` header. Rate limits keyed by `device_id` + IP (Cloudflare WAF). Cloudflare Turnstile gating considered (§F.3).

### C.4 The ranking math (composite score, recomputed every N hours via Worker cron)

```
score(tuning) =
    α * log1p(active_installs)                        // popularity
  + β * median(keep_duration_s across devices)        // session length signal
  - γ * panic_revert_rate                             // crash penalty
  + δ * recency_boost(last_keep_epoch)                // freshness
  + ε * diversity_bonus(unique_devices)               // anti-Sybil
```

Tune α/β/γ/δ/ε empirically. Surface `why_text` as a one-line explanation derived from which terms dominated the score:
- Dominant α → "popular across the field"
- Dominant β → "delivers long stints — median 23 min"
- Dominant -γ → "exceptionally stable — 2% panic rate vs 40% baseline"
- Dominant δ → "freshly discovered"
- Dominant ε → "validated across 14 different devices"

### C.5 Pitstop PADDOCK tab — UX

```
PADDOCK tab (4th tab — TELEMETRY | TUNING | TOOLS | PADDOCK)
├── ◆ Status header: "PADDOCK online · 47 tunings for NPEA00502"
├── ─ TOP 10 ──────────────────────────────────────────────
│  1. ◇ tuning #a3f7 · 12 devices · "delivers long stints — median 23 min"
│  2.   tuning #91e2 · 9 devices · "exceptionally stable — 2% panic rate"
│  3.   tuning #5b1d · 7 devices · "popular across the field"
│  ...
├── ─ NEWEST 10 ───────────────────────────────────────────
│  1. ◇ tuning #ffe8 · seen 2h ago · ledger pending
│  ...
├── ─ Your active tuning ──────────────────────────────────
│   ◇ Custom (not in swarm) — Submit?
│   ─ OR ─
│   ◇ tuning #a3f7 (from PADDOCK 2 days ago) — Keep | Revert
└── [PADDOCK: ON · Reset Device ID · Privacy/Terms]  ← opt-out controls
```

Selecting a tuning launches the road-test flow:
1. Pitstop backs up `config_<ID>.yml` → `config_<ID>.yml.prerace`
2. Applies the new tune
3. Sets `$ETK_PADDOCK_ACTIVE_TUNING=<tuning_id>` in env / SHM
4. Operator launches the game, plays a session
5. On RUNNING→IDLE, `session_postmortem.sh` notices the active-tuning flag and emits a `keep_pending` notification
6. Pitstop next launch (or end-of-session toast): "Keep tune #a3f7 or revert?" → user picks → API event posted
7. Auto-revert if session ended in PANIC within N seconds of launch

---

## §D. COMPONENTS (the build list)

| Surface | Artifact | Approx. effort |
|---|---|---|
| Site | `emulationtuningkit.com/index.html` + minimal CSS, screenshots, GH link | 4 hr |
| Site | Privacy policy + Terms of Service (real text, see §F.1) | 4 hr + legal review |
| API | Cloudflare Worker `worker.ts` with the 6 endpoints from §C.3 | 16 hr |
| API | D1 schema + migrations | 4 hr |
| API | Cron Worker for leaderboard recomputation | 4 hr |
| API | Rate limiting + Turnstile gate (if needed) | 4 hr |
| Pitstop | New PADDOCK tab — list view + detail view + road-test flow | 24 hr |
| Pitstop | `bin/paddock_d.sh` background worker — uploads, downloads, event queue with retry on network drops | 8 hr |
| env.sh | `ETK_DEVICE_ID` derivation + `ETK_PADDOCK_API`, `ETK_PADDOCK_ENABLED` knobs | 1 hr |
| install.sh | `.etk_device_id` provisioning + PADDOCK opt-out documentation in the screenshots-README-style drop | 2 hr |
| Docs | README PADDOCK section + privacy callout + opt-out instructions | 2 hr |

**Total rough estimate: 75-100 hr deep-dev** across both the rig side and the service side. Substantially larger than Self-Update (~12 hr). Multi-sprint.

---

## §E. DEPENDENCIES & PRECONDITIONS

1. **Rig self-update must ship first.** `RigSelfUpdateFeasibility.md` Phase 1-4. Otherwise any breaking change to the PADDOCK protocol strands every alpha tester until they ssh in for a host install. Self-update is the load-bearing safety net under any feature that talks to a live remote API.
2. **A real ETK release (`v0.1.0` or later).** PADDOCK protocol versioning hinges on `etk_schema_version` matching `pitstop_fields.json` schema version. No versioning ceremony = no versioned protocol = no safe schema migrations.
3. **`pitstop_fields.json` is the contract.** Any field added/renamed/removed bumps `etk_schema_version`. Pitstop must refuse to apply a tune whose `etk_schema_version` is incompatible — surface as "PADDOCK has 200 tunings, 47 compatible with your kit version."
4. **Cloudflare account.** Free tier handles MVP traffic comfortably (D1 free tier: 5 GB storage, 5M reads/day, 100k writes/day; Workers free tier: 100k req/day). Operator action: register account + claim `emulationtuningkit.com` domain.
5. **Domain registration.** `emulationtuningkit.com` availability check + registrar pick.
6. **Network on rig.** Same as Self-Update — Rocknix manages WiFi natively. Operator-side precondition.
7. **`curl`, `jq` on rig** — already audited green (2026-05-26).

---

## §F. PRIVACY, SECURITY, SYBIL RESISTANCE

### F.1 The privacy contract (must be real, must be honest)

The auto-subscribe default is **ethically defensible only if** every one of these is true and visibly documented:

1. **No PII collected.** No email, no IP retention beyond rate-limit windows (Cloudflare WAF default), no MAC address transmitted (hashed once during install, never sent).
2. **`ETK_DEVICE_ID` is pseudonymous, not anonymous.** Stable per device → linkable across calls from the same device. Privacy policy says this in plain English, and PADDOCK shows you your own hash so the linkability is visible to you, not hidden.
3. **Rotatable on demand.** "Reset Device ID" in PADDOCK tab → regenerates the hash, severs all prior linkage. This is the right-to-be-forgotten primitive AND the un-brag button (§B.5.a).
4. **One-click opt-out.** `ETK_PADDOCK_ENABLED=0` in env.sh disables ALL outbound traffic. PADDOCK tab shows "OFF" and offers no controls except "ON" toggle. The toggle is the first thing the operator sees on first PADDOCK open.
5. **Opt-out is documented at install time.** install.sh's screenshots-README-style drop should include a `~/etk/PADDOCK_README.txt` explaining auto-subscribe and how to opt out, written BEFORE the rig ever talks to the API.
6. **Auto-upload of every TUNING-tab save is the default** (§B.4.a) — must be the first line of the privacy policy, not buried. "Every time you save a tuning edit, ETK uploads the resulting RPCS3 config_<gameID>.yml file to PADDOCK along with your device hash and the aggregated session signals for that game. To stop this, toggle PADDOCK OFF or set ETK_PADDOCK_ENABLED=0."
7. **Data scope is published.** Privacy policy lists every field sent: `device_id, game_id, config_yaml (full RPCS3 per-game config), rpcs3_version, etk_schema_version, event_type, session_signal_json{n_sessions, median_duration_s, panic_rate, median_shaders_per_session}`. No surprises.
8. **GDPR posture:** auto-subscribed users in the EU is the trickiest part. Likely defensible (no PII, no identifiable individual, opt-out trivial AND visible AND documented at install), but the operator should get a real lawyer's quick review before launch if there's any EU tester intent. Cost: $500-1500 for a one-time review.

### F.2 Sybil resistance — usage-as-vote is the structural defense

Reddit-style upvotes are trivially Sybil-able with cheap accounts. Usage-as-vote is harder to fake because:
- Each "keep" event requires the device to have completed a real session against a real game-ID
- The session signal (duration, panic rate, shader yield) is reported alongside the event
- Bad-actor strategies that work against thumbs-votes (1000 fake accounts) don't work here without 1000 fake gameplay sessions

Residual risks + defenses:
- **Mass-fabricated device IDs with plausible session signals.** Mitigation: the leaderboard math's `ε * diversity_bonus(unique_devices)` weights "this tune is liked by many different devices" higher than "one device reports liking it 1000 times" (which is already capped by rate-limit).
- **Coordinated swarm of real devices artificially submitting.** This is essentially the design WORKING — if 100 real ETK installs all keep tune X, that's the swarm signal we want. No defense needed.
- **API abuse / DoS.** Cloudflare WAF + per-device rate limits + optional Turnstile gate on submit endpoints.

### F.3 Turnstile — gate or not?

Cloudflare Turnstile is invisible by default (challenges only suspicious traffic). Pros: free Sybil defense. Cons: any challenge that requires interaction breaks the curses-TUI PADDOCK flow. **Recommend: enable Turnstile on submit endpoint only, configure for invisible challenges only, gracefully degrade Pitstop's submit retry on challenge failure.**

### F.4 Malicious tunings (someone uploads a setting that bricks the game)

Tunings are pre-validated by the share-unit schema: every key MUST be a valid `yaml_key` from `pitstop_fields.json`, every value MUST match the field's `type` and `options`. **Garbage doesn't pass the contract.** Worst case a malicious tuning sets all 21 dials to their most-aggressive valid values, which might tank performance but won't brick — and the auto-revert-on-panic defense (§B.4) catches the tunes that crash.

---

## §G. FAILURE MODES & RECOVERY

| Failure | Detection | Recovery |
|---|---|---|
| API offline (Cloudflare incident) | `GET /v1/health` non-200 | PADDOCK tab shows "OFFLINE — using cached top 10 from last sync" |
| Rig has no WiFi | curl fails | Same as above; "OFFLINE" badge |
| Schema version mismatch | API returns 409 on submit | Pitstop shows "Update ETK to v0.X.Y to submit tunings" — couples back to self-update flow |
| User wants to revert AFTER session ended without confirming | `config_<ID>.yml.prerace` lingers until confirmed | Pitstop on next launch: "You road-tested tune X. Keep or Revert?" — never silently committed |
| Power loss between road-test apply and Keep/Revert | `.prerace` backup intact on disk | Same as above — next Pitstop launch surfaces the pending decision |
| Bad tune crashes the rig hard | session_postmortem records PANIC | Auto-revert path fires (§B.4); device upload-queues `panic_revert` event when next online |
| API ingestion lag (cron behind) | Leaderboard `computed_at` is stale | Surface `computed_at` in PADDOCK header: "ranks updated 47 min ago" — operator sees the freshness |

---

## §H. STRATEGY: TUNINGS BEFORE SHADERS (the rationale, captured)

| Property | Tunings (first) | Shaders (later) |
|---|---|---|
| Payload size | ~2 KB JSON | 1-50 MB per game |
| Format | Text, schema-validated | Binary blobs |
| Legal exposure | Settings (facts) — none | Derived from game assets — gray area worth lawyer time |
| Validation | Schema check (instant) | Replay-and-test (expensive) |
| Storage cost (D1) | ~5 KB/tuning × 10k tunings = 50 MB → free tier easily | 50 GB+ per popular game — R2 ($0.015/GB/mo) or P2P |
| Bandwidth cost | Negligible | Significant; needs CDN strategy |
| MVP signal value | High — proves the swarm math + UX | Higher, but only after swarm UX is validated |
| Time to ship | ~75-100 hr | ~200+ hr (P2P design + legal review + R2/CDN ops) |

The operator's strategic call is **right**: ship tunings first, learn the swarm operationally, inherit the userbase + UX + trust + infra learnings for the shader system. Don't skip the cheap experiment for the expensive one.

---

## §I. OPEN QUESTIONS

### I.1 Cadence of swarm sync

How often does Pitstop poll the API? Options:
- On PADDOCK tab open (lazy) — minimal traffic, minimal freshness
- Every N hours via background worker (`paddock_d.sh`) — better freshness, more traffic
- On every Sentry tick — overkill

Recommend lazy + background-on-launch (poll once when Pitstop opens, cache for the session).

### I.2 (RESOLVED 2026-05-26) Operator-tuned configs auto-upload — no confirmation step

Operator decision: every save in the TUNING tab uploads automatically with no per-save confirmation. See §B.4.a. The privacy contract carries the weight (§F.1) — opt-out is the primitive, not per-submission consent. This is the **only** model that produces a real swarm signal from day one rather than from the day the operator remembered to share. Captured here so future-Claude doesn't re-litigate.

### I.3 Per-device tuning history privacy

The API knows that `ETK_8f3a91c2` has submitted tunings for NPEA00502, NPUA80075, and BCUS98114. That set is a behavioral fingerprint. Two questions:
- Should the leaderboard query return whether YOUR device has tried this tune (UX nice-to-have)? Implies the API discloses your history to YOU.
- Should the API ever disclose a device's tuning history to anyone other than that device? **Hard no.** Document this in privacy policy.

### I.4 Compatibility across schema versions

When `pitstop_fields.json` adds a field, what happens to tunings stored under the old schema? Options:
- **Strict:** Old tunings only visible to old ETK versions. Cleanest but partitions the swarm.
- **Lenient with defaults:** Old tunings load on new ETK with the new field defaulted. Looser but breaks the "this exact tune produced these results" assumption.

Recommend strict in v0.x; lenient with explicit `auto_migrated: true` marker in v1.x once schema is stable.

### I.5 Domain ownership

Verify `emulationtuningkit.com` is available (likely; obscure niche). Decide registrar (Cloudflare Registrar is operator-friendly if Cloudflare is the host).

### I.6 Site copy ownership

If the .com site evolves into a real marketing surface (testimonials, screenshots, "as seen in" press), who owns the copy and assets? Probably the operator personally for now; revisit if a foundation/org forms.

### I.7 Reset Device ID's effect on swarm history

When a user resets their device ID, their prior keep/revert events still exist in the DB under the old hash. Two postures:
- Leave them (votes persist, identity is severed) — better for swarm signal continuity
- Purge them (true forget) — better for privacy purity, costs swarm signal

Recommend leave-by-default with explicit "Reset AND purge prior events" as a separate, slower path. Document tradeoff in privacy policy.

---

## §J. PHASED IMPLEMENTATION PLAN

### Phase 0 — Strategic prep (no code; weeks 0-1)
- Verify `emulationtuningkit.com` availability + register
- Open Cloudflare account
- Draft privacy policy + ToS (lawyer review if EU testers planned)
- Confirm self-update has shipped (`RigSelfUpdateFeasibility.md` Phase 1-4 complete)

### Phase 1 — Schema + identity (rig side; ~6 hr)
- Bump `pitstop_fields.json` to declare `etk_schema_version: v0.2.0`
- Add `ETK_DEVICE_ID` derivation to install.sh / env.sh
- Pitstop "About" item displays device ID + schema version (read-only)

### Phase 2 — API spike (server side; ~12 hr)
- Cloudflare Worker with `/v1/health`, `/v1/submit`, `/v1/tunings/<game>?list=newest10`
- D1 with the §C.2 schema (no leaderboard cron yet)
- Manual `curl` testing — no Pitstop integration

### Phase 3 — Pitstop PADDOCK tab read-only (~12 hr)
- New tab via the `TABS` registry one-liner
- Show "newest 10" from API
- Detail view shows tune JSON + metadata
- NO road-test flow yet — pure browsing

### Phase 4 — Road-test loop (~16 hr)
- `.prerace` backup convention
- Apply / Keep / Revert flow
- session_postmortem hook for auto-revert on panic
- `paddock_d.sh` background event queue + retry

### Phase 5 — Swarm math + leaderboard cron (~8 hr)
- Worker cron computes leaderboard every N hours
- API serves top10 with why_text
- Pitstop PADDOCK shows the ranked list

### Phase 6 — Site (~8 hr)
- `emulationtuningkit.com` static page on Pages
- Screenshots gallery, GH link, Privacy/Terms

### Phase 7 — Privacy + opt-out polish (~6 hr)
- install.sh drops PADDOCK_README.txt at install
- Pitstop opt-out toggle wired
- Reset Device ID flow
- Documentation pass

### Phase 8 — Soft launch (alpha testers; weeks N+)
- Invite-only; collect feedback
- Monitor for Sybil patterns
- Iterate on ranking math

**Total rough estimate: 70-100 hr across 8 phases.** Multi-sprint; needs Self-Update shipped as precondition; needs operator-side work outside dev (domain, account, legal).

---

## §K. ACCEPTANCE CRITERIA

1. **Default-installed rig is auto-subscribed AND has functional opt-out before first API call.** install.sh drops the opt-out doc; Pitstop's first PADDOCK open prompts "Auto-subscribe? [Yes] [Opt out]" with a clear privacy explainer.
2. **No PII ever leaves the rig.** Audit: every outbound payload's keys are listed in privacy policy; nothing else is sent.
3. **Opt-out is real.** `ETK_PADDOCK_ENABLED=0` → zero outbound traffic; verifiable with packet capture.
4. **Schema-version compatibility is enforced.** Pitstop refuses to apply or submit tunings whose `etk_schema_version` doesn't match local.
5. **Road-test is always recoverable.** Power loss mid-test leaves `.prerace` intact; next Pitstop launch surfaces the pending decision. No silent commits.
6. **Auto-revert fires on PANIC within N seconds.** Verified with a deliberately-broken test tune.
7. **Sybil math holds in simulation.** A test with 1 device submitting 1000 keep events for one tuning produces ranking score ≤ 10 real devices each keeping it once.
8. **API offline degrades gracefully.** PADDOCK tab still works; shows cached data + OFFLINE badge; no Pitstop crash.
9. **Reset Device ID severs linkage.** New ID resolves; prior events untouched but unattributable; verifiable in DB.
10. **Self-update remains the safety net.** A broken PADDOCK release can be rolled back via the self-update flow without intervention.

---

## §L. TL;DR

- **Feasible — but bigger than self-update.** ~75-100 hr deep-dev across 8 phases vs. self-update's ~12 hr.
- **The strategic call to start with tunings before shaders is correct** for storage, bandwidth, legal exposure, and validation cost reasons (§H).
- **The share unit is the literal `config_<gameID>.yml` file** RPCS3 already reads — no curated-subset translation layer, no `pitstop_fields.json`-vs-config drift risk. The signal feeds already exist in ETK (`sessions.tsv` for post-tune weighting, `config_changes.tsv` for per-device tuning history). No new instrumentation needed for MVP.
- **Auto-upload is the default for every TUNING-tab save** (§B.4.a). The swarm self-bootstraps from day one. Opt-out is the privacy primitive; there is no per-submission consent prompt because friction kills swarms.
- **Hash-as-bragging-right (§B.5.a) is the elegant authorless-but-attributable mechanism.** Default state is anonymous; the only way to claim authorship of a top tune is to publicly share your own `ETK_xxxxxxxx` hash. No accounts to ban, no profiles to police, no impersonation to chase.
- **The auto-subscribe + accountless + usage-as-vote model** is the only design that produces a real swarm signal AND avoids the moderation overhead of accounts; it's ethically defensible only if opt-out is loud, real, and one-toggle.
- **Self-update is the load-bearing precondition.** Without it, any breaking PADDOCK protocol change strands every alpha tester. Ship `RigSelfUpdateFeasibility.md` Phase 1-4 first; THEN start this dossier.
- **Cloudflare Pages + Workers + D1** is the right stack: free-tier-generous, geo-distributed, schema-friendly, operator already named it.
- **The PADDOCK swarm bootstraps the eventual P2P shader swarm.** Same userbase, same trust, same UX vocabulary. Don't skip the cheap learning experiment for the expensive one.
- **Domain registration + Cloudflare account + privacy/ToS drafting** are operator-side preconditions outside dev hours, queue these in Phase 0.
