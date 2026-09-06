# RADIO — `etk-cloud-ai`, the race engineer on the pit radio

**Experimental spec · 2026-09-06 · branch `radio` off `main` @ `76d8769` · STATUS: SPEC (not built)**

The kit records every session (2,464 ledger rows on 2026-09-06), judges knob A/Bs with
`etk_dyno`, and explains crashes from `crash_signatures.json`. What it does not have is
an engineer on the radio when no Claude session is open: a CLEAN or SURVIVED row shows
gauges and no advice, the A/B plan lives in memory and dossiers rather than on the rig,
and the next-run decision is made from the laptop. RADIO closes that loop with a free,
open model on the operator's own Always-Free Ampere node — `etk-cloud`, the same box the
forge builds on — reached from the rig the way PADDOCK already reaches GitHub. $0, no
third party, and every recommendation is a staged edit the operator applies through the
tabs that already exist.

Lineage: the ROADMAP's "claudomatic all-wheel-drive" (pro mode: on-rig intelligence, no
host sync; mako status toasts) — now with an open model instead of a metered API, and
COACHING instead of auto-tuning, because a config write is bytes-to-atoms and stays in
the operator's hand. The TELEMETRY tab has carried an empty `pit_note.txt` slot since
BuildSimpleTelemetry ("optional cached AI summary; generation out of scope") — RADIO is
what writes it.

Naming (§C.3): the mechanism is the `etk-cloud-ai` service; the metaphor is the pit
**RADIO** — the cockpit skill already defines the Engineer's voice as "the pit radio".
"Pit Wall" is taken (the installer's TUI, `tools/tui.sh`).

---

## 0. The loop this adds (§0 row)

| The operator is… | Surface they touch | What must be true |
|---|---|---|
| **closing a session and wanting to know what it meant** · **planning the next A/B run** | TELEMETRY header PIT NOTE (one line) · TELEMETRY detail card **RADIO DEBRIEF** block · **RADIO** tab: last debrief + **RUN SHEET** (arms with N have/target + NEXT) | every claim names its N; a recommendation is a **staged TUNING/DRIVER edit the operator applies** (never written by the service); nothing leaves the rig unless `radio.json` exists; no network on tab entry; absent node = feature invisible and everything else unchanged; the debrief arrives async and is never waited for |

---

## 1. Decisions (locked for the experiment; the operator can overrule any)

1. **Coaching, not auto-tuning.** The service returns data (findings, recommendations,
   a run sheet). Pitstop stages a recommended change into the TUNING editor's pending
   edits or names the DRIVER dial; the operator presses APPLY on the existing atomic
   write + read-back path. `config_changes.tsv` gains a trailing `source` column
   (`radio:<epoch>`), so accepted advice is attributable and later scoreable — the
   reader indexes columns 0–4 positionally, a trailing column is safe (`sessions.tsv`
   rule). The service never touches the rig; the rig only ever POSTs and GETs.
2. **Inference runs on `etk-cloud`, not on the rig.** The node: 4 OCPU / 23 GB / 46 GB
   free disk, Ubuntu 24.04, python 3.12, docker, no ollama yet, reserved IP (static since
   2026-08-30), only ssh listening (checked read-only 2026-09-06, uptime 31 d). On-rig
   inference was costed and rejected for v1 — §12.
3. **Discipline lives in code, upstream and downstream of the model.** `etk_dyno` runs
   ON THE RIG to build the arms table (bake exclusion, ABORTED drop, medians, N per arm)
   before anything is sent; guards on the node drop any recommendation that crowns at
   N<3, names a falsified item (§F), proposes a key outside `pitstop_fields.json` or a
   value outside its range, or lowers resolution as a KPI move. The model can be wrong;
   the surface cannot show a wrong crown.
4. **Deterministic briefing, not vector RAG (v1).** The whole manual is ~22k tokens; at
   the measured 17.9 tok/s prompt-eval that is 20 minutes of prefill. The briefing
   builder selects by the pack's own fields (matched crash signatures, the touched
   fields' `help` text, ≤2 §2.4 mechanism bullets by keyword, the title's
   `game_status.tsv` row, §B.3 and §F verbatim). Small, testable, no embedding model.
   Dossier RAG is Phase 4.
5. **Async job model.** The rig submits a pack and gets a job id; the service works;
   the rig polls with short requests and stores the result. A held connection over
   handheld WiFi through a five-minute inference is the wrong shape. Pending jobs drain
   at the next postmortem, on RADIO tab entry, or on REFRESH.
6. **Transport = HTTPS + bearer token, the PADDOCK precedent.** Caddy on the node
   (docker, host network, `<ip-dashes>.sslip.io` → real Let's Encrypt cert, so the rig's
   curl verifies with its system bundle; `tls internal` fallback) in front of the
   service on `127.0.0.1:8737`; Ollama stays on `127.0.0.1:11434` and is never exposed.
   The token travels in a header file under `/dev/shm` (umask 077) — never argv, never
   logs (paddock_sync law). The host CLI uses `ssh -L` instead and needs no token.
7. **Model rungs from measurement, the eval decides.** Default debrief model
   `qwen3.5:9b`, radio-check model `qwen3.5:4b` (§5). Modelfile-tuned, not
   weight-tuned (§4).
8. **The forge outranks the radio.** A job waits while any `~/forge-runs/active_*`
   marker is live (forge.sh writes one per lane); `OLLAMA_KEEP_ALIVE=30m` so the
   weights leave RAM between evenings. Mirrors "a game launch outranks an install".
9. **Default-off, gated, fail-soft.** No `radio.json` → no tab, no pack, no traffic.
   `ETK_RADIO=0` kill-switch. A failed send is a log line, never a toast, unless the
   operator pressed the button.
10. **Experimental branch `radio`, pushed immediately** (§1.6: parked branches are how
    the 0.5.0 chords were lost). Nothing here ships on `latest` until the eval (§10)
    says the model beats rules-only.

---

## 2. Architecture

```
 RIG (ROCKNIX, BusyBox, python 3.14, curl+jq)                 etk-cloud (Ubuntu 24.04, python 3.12, docker)
 ───────────────────────────────────────────                  ──────────────────────────────────────────────
 Sentry ── RUNNING→IDLE ──▶ session_postmortem.sh              Caddy :443  (sslip.io / LE, or tls internal)
                               │ row + archives keyed $NOW        │  reverse_proxy 127.0.0.1:8737
                               └─▶ nohup radio_send.sh debrief $NOW ──HTTPS+bearer──▶ etk-radio.service (stdlib http, jobs in sqlite)
                                     │                                                  │  briefing.py  ← ~/etk (manual, sigs, fields, status)
                                     ├─ radio_pack.py $NOW → radio/<NOW>.pack.json       │  guards.py    ← config/falsified.json + pitstop_fields.json
                                     │    (ledger row · last 5 rows · dyno arms ON RIG   │  ollama /api/chat, format=schema  ← 127.0.0.1:11434
                                     │     · sig decode · dmesg/log windows · timeline    │     qwen3.5:9b (debrief) / qwen3.5:4b (radio check)
                                     │     · config · run sheet · operator feel)          │  waits while ~/forge-runs/active_* exists
                                     ├─ POST /v1/debrief → 202 {job}                      │
                                     ├─ poll GET /v1/jobs/<id> (≤ RADIO_WAIT_S, else pending/) ◀──── 200 {debrief}
                                     ├─ radio/<NOW>.debrief.json  +  pit_note.txt (headline)
                                     └─ etk_notify.sh "RADIO: debrief ready" "<headline>"      (ASCII, "ETK" surface)

 Pitstop: TELEMETRY header PIT NOTE · detail card RADIO DEBRIEF · RADIO tab (last debrief, RUN SHEET, REFRESH, RADIO CHECK,
          ACCEPT RUN SHEET → radio/run_sheet.json, LOAD FIX → TUNING pending edits → operator APPLY → config_changes.tsv source=radio:<epoch>)
 Host:    tools/radio.py over `ssh -N -L 8737:127.0.0.1:8737 etk-cloud` (debrief from the host mirror, eval, pack inspect)
```

### 2.1 Reuse (already exists — do not rebuild)

| Piece | Where | Used for |
|---|---|---|
| The ledger + archives keyed by row epoch | `etk_telemetry/sessions.tsv`, `rpcs3_logs/<epoch>.log` (6 kept, 1–11 MB), `mango_logs/<epoch>.csv` (12), `perf_logs/`, `audio_logs/`, `blackbox/flightrec-*.tsv`, `career/<ID>.txt` | the pack is a reduction of what postmortem already wrote; `$NOW` is the join key everywhere |
| `tools/etk_dyno.py` | host, stdlib | pushed to the rig (like `etk_drift.py`); gains `--json`; builds the arms table with the discipline already in it |
| `config/crash_signatures.json` | rig + repo | the deterministic diagnosis; its `explanation`/`driver_dial`/`suggested_changes` are the briefing's first paragraph and the rules-only baseline |
| `config/pitstop_fields.json` (50 fields, `help` ≈ 2.6k tokens total) | rig + repo | the ONLY vocabulary a `config_changes` recommendation may use; `min/max/options` bound values; `help` text is the mechanism corpus per field |
| `config/game_status.tsv` | repo (node) | "the human layer the ledger cannot see" — per-title status row in the briefing |
| `TRACK_MANUAL.md` §0 §2.4 §B.2 §B.3 §F | repo (node checkout `~/etk`) | doctrine + mechanism catalog; refreshed by `git pull` on the node |
| `bin/paddock_sync.sh` + install.sh STEP 7 | rig + host | credential file pattern (`chmod 600` JSON, header file in `/dev/shm`, preflight with a real HTTP status, token-expiry warning) |
| `bin/etk_notify.sh`, `_Notifier`, `_ProgressCard` | rig | toasts ("ETK" verdict surface) and the elapsed-time card for a radio check |
| `_run_with_spinner(... indeterminate=True)`, `_paddock_busy` | Pitstop | the busy frame for any operator-pressed network action |
| Tab registry (`TABS`, `CURRENT_TAB_*`, `_dispatch_kb/_pad`) | Pitstop | "one line + a constant + a draw_/handle_ pair" |
| `pit_note.txt` + `load_pit_note()` | Pitstop TELEMETRY | the Phase-0 surface: the debrief headline, zero UI changes |
| TUNING editor pending-edit model + APPLY | Pitstop | LOAD FIX stages values here; the operator applies |
| `bin/etk_install_worker.py` | rig | the out-of-process worker precedent (pidfile, SHM state, fail-soft) |
| `forge.sh` `~/forge-runs/active_<lane>` markers | node | the coexistence lock |
| The course-kit files on an identically-shaped node (Caddyfile, compose, `bench.py`, `client.py`, the ollama systemd drop-in) | private notes | forked into `tools/radio/`; the measured ladder in §5 comes from that node |
| `tools/test_paddock.py`, `test_install_queue.py`, `test_notify.py` | host | test shape: fixtures, no rig, no network; the notify roster pin gains the RADIO sender |

### 2.2 To build

| # | Piece | Lang / runs on | Notes |
|---|---|---|---|
| 1 | `bin/radio_pack.py` | python stdlib, rig | reduces one session into PACK v1 (§3.1); allowlisted fields only; ≤ 64 KB; runnable by hand for any epoch — a one-file forensic bundle is useful without any model |
| 2 | `bin/radio_send.sh` | POSIX sh + curl + jq, rig | `debrief <epoch>` · `drain` · `ask <question-id>`; header-file token; submit/poll/store/toast; `RADIO_WAIT_S` (default 720) then `pending/` |
| 3 | `tools/etk_dyno.py --json` | python, host + rig | arms as JSON keyed by dyno's grouping (stack, tune, res, clk, pwr); no behaviour change to the text report |
| 4 | `tools/radio/service.py` | python 3.12 stdlib, node | `POST /v1/debrief`, `GET /v1/jobs/<id>`, `POST /v1/ask`, `GET /v1/health`; bearer on every route; 64 KB body cap; sqlite job table; one worker thread; forge-lock wait; structured-output call to Ollama; schema validation; guards; result retention 30 days |
| 5 | `tools/radio/briefing.py` | python, node | deterministic selection (§4.2); unit-tested against fixture packs |
| 6 | `tools/radio/guards.py` + `config/falsified.json` | python, node (+ repo) | §6; `falsified.json` is §F made machine-readable, each entry naming its manual anchor |
| 7 | `tools/radio/prompts/engineer.md`, `Modelfile.debrief`, `Modelfile.fast` | node | the system prompt (doctrine, voice, output contract); `prompt_sha256` stamped into every debrief — the tune_tag of advice |
| 8 | `tools/radio/schema/{pack,debrief}.v1.json` | repo; debrief schema also pushed to the rig | contracts (§3); Ollama `format:` takes the debrief schema for constrained decoding |
| 9 | `tools/radio/provision_etk_cloud_ai.sh` | bash, node — **OPERATOR RUNS IT** | installs ollama (script fetched and read first, per the checked runbook steps), writes the systemd drop-in, pulls the two models, creates the Modelfiles, installs `etk-radio.service`, brings up Caddy, mints the token (printed once), prints the doors to open (OCI security list + iptables 80/443) |
| 10 | `tools/radio/Caddyfile`, `docker-compose.yml` | node | forked from the proven kit; `reverse_proxy 127.0.0.1:8737`; `SITE_ADDRESS` from `.env` |
| 11 | `tools/radio.py` | python, host | `debrief --epoch N` (host mirror), `ask`, `pack --inspect`, `eval`; reaches the node through `ssh -L`; §11 ruling applies |
| 12 | `tools/radio/eval.py` + `eval_cases/*.json` | python, host (needs the node) | §10 golden cases + rules-only baseline + scorecard |
| 13 | `tools/test_radio.py`, `tools/radio/test_service.py` | python, host, no network | packer on fixture telemetry, schema, guards, toast copy ASCII, token-never-in-argv, service loop with a fake Ollama |
| 14 | Pitstop | python, rig | pit_note writer contract; detail-card RADIO DEBRIEF block; RADIO tab (§7); LOAD FIX staging; ACCEPT RUN SHEET; `source` column on config rows |
| 15 | install.sh | bash, host | STEP 3: `etk_dyno.py` push + chmod; STEP 5: `radio_debrief.v1.json`; **STEP 7b RADIO LINK** (`RADIO_URL`+`RADIO_TOKEN` → preflight `/v1/health` → `radio.json` chmod 600); `etk.conf.example` block; uninstall removes `radio.json` |
| 16 | `session_postmortem.sh` | sh, rig — locked-down core | ONE line at the very end, after the breadcrumb consume, Phase 2b only (§9) |

---

## 3. Data contracts

### 3.1 PACK v1 (rig → node; `etk_telemetry/radio/<epoch>.pack.json`)

Built from an allowlist, never from raw files. Caps per section keep the whole pack near
10 KB ≈ 2.5k tokens; the hard cap is 64 KB (the service rejects larger).

```json
{"schema": "ETK-RADIO-PACK v1", "epoch": 1788491975, "game_id": "NPUB31245",
 "rig": {"soc": "SM8250", "os": "20260901", "kernel": "7.2.0", "kit": "0.9.0",
         "build": "26.2.2_gtk_0.7", "core": "0.9.0.3_armsx3-a74a0f3e0", "stack": "rk1ebff24/k7.2.0#1/r0.9.0.3",
         "dial": "tu_debug=zlatez", "power": {"profile": "race", "grid": "off", "gpu_mhz": 925}},
 "session": {"status": "SURVIVED:Adreno", "duration_s": 724, "crash_sig": ["KEEPALIVE_SURVIVE", "GPU_FENCE_TIMEOUT"],
             "...every ledger column decoded k=v, aud/perf cells parsed...": "..."},
 "history": {"rows": ["last 5 rows of this game, decoded, compact"],
             "career": {"total_sessions": 732, "clean_rate_pct": 56, "current_streak": 24},
             "changes_since_last_debrief": [{"epoch": 1788285097, "field": "Disable FIFO Reordering", "old": "false", "new": "true"}]},
 "dyno": {"stack": "S11", "res": 100, "arms": [{"tune": "tu_debug=zlatez", "clk": 925, "pwr": "race", "n": 2, "low_n": true,
                                                "perfect_p50": 3.0, "lock_p50": 18.5, "jit_p50": 7.1, "resc_h": 33.0, "dur_p50": 256, "crash": "2/2"}]},
 "crash": {"sigs": [{"id": "GPU_FENCE_TIMEOUT", "label": "Adreno", "severity": "high", "summary": "GPU stalled rendering a frame"}],
           "fault": {"status": "00E59005", "fence_hex": "575bf", "class": "fence park (#2)"},
           "dmesg_window": ["<= 25 lines around the fault, strings-shielded"],
           "rpcs3_errors": [{"n": 14, "line": "E ... RSX: ..."}, "<= 15 unique E/F lines from the last 4 MB, repeats collapsed"],
           "blackbox_tail": ["<= 40 kmsg lines before a PANIC; only on PANIC rows"]},
 "timeline": {"bins": 10, "fps_med": [30.1, 30.0, "..."], "ft_p99_ms": ["..."], "temp_c": ["..."], "perfect_windows": ["..."]},
 "config": {"yaml": {"  Resolution Scale": "100", "  Preferred SPU Threads": "3", "...the 50 schema fields' current values...": "..."}},
 "run_sheet": {"...the accepted sheet, if any (§3.3)...": "..."},
 "operator": {"feel": "stutter", "note": ""},
 "budget": {"bytes": 9800}}
```

Reduction rules: the timeline is 10 equal-duration bins over `[epoch − duration_s, epoch]`
from the mango CSV and `perf_logs` (the row is stamped at END — §2.3); RPCS3 lines come
from `tail -c 4194304 | strings` exactly as postmortem reads them (the `·` severity glyph
survives `strings` as a bare `E`/`F` at column 0); the argv line and any path outside
`dev_hdd0/game/<ID>` are dropped; `feel` is the operator's four-choice verdict (§7.4) —
the human sensor for what the log cannot see (audio underruns leave no trace; §B.3).

### 3.2 DEBRIEF v1 (node → rig; `etk_telemetry/radio/<epoch>.debrief.json`)

```json
{"schema": "ETK-RADIO-DEBRIEF v1", "epoch": 1788491975, "game_id": "NPUB31245",
 "model": "etk-radio:9b", "prompt_sha256": "…", "corpus_commit": "8401cbc",
 "tokens": {"prompt": 3410, "completion": 402}, "latency_s": 271.3,
 "radio": "Fence wedge at six minutes, keepalive caught it, you finished. Two rescues an hour is the zlatez baseline on this chassis. Nothing to change yet.",
 "headline": "SURVIVED - 1 rescue absorbed; zlatez@925 N=2 of 3",
 "tags": ["survived", "low_n"],
 "findings": [
   {"kind": "mechanism", "text": "00E59005 is a fence park (class #2); the kernel keepalive absorbed it - the row is clean by the ladder.",
    "evidence": [{"source": "ledger", "field": "rescues", "value": 1}, {"source": "dmesg", "line": "context_keepalive: surviving hang"}]}],
 "recommendations": [
   {"kind": "next_run", "text": "One more warm race on this arm, same track, before any comparison.",
    "n_basis": {"arm": "zlatez@925/race", "n": 2, "n_needed": 3}, "config_changes": [], "driver_dial": null, "confidence": "high"}],
 "run_sheet": {"...proposed sheet (§3.3)...": "..."},
 "guards": {"passed": true, "dropped": []}}
```

- `radio` ≤ 280 chars, `headline` ≤ 60 chars, both ASCII (toast + pit_note surfaces).
- `kind` ∈ `observation | mechanism | anomaly` (findings); `next_run | config | dial |
  power | no_change | investigate` (recommendations). A `config` recommendation carries
  `config_changes` in `pitstop_fields.json` vocabulary only; a `dial` names a ROAD FEEL
  dial or `TU_DEBUG` token; a `crown` is not a kind — a comparison verdict is a
  `finding` whose `evidence` must carry both arms' N ≥ 3 or the guard drops it.
- `tags` are computed on the NODE from the pack (`bake` if shaders_harvested > 5,
  `aborted`, `attract` if the operator marked it, `low_n`, `stack_change`, `panic_silent`
  if PANIC with no kmsg lead-up, `keepalive_absent` if a fault status exists with
  rescues = 0) and merged with the model's; the tests assert on the computed ones.

### 3.3 RUN SHEET (`etk_telemetry/radio/run_sheet.json`, written only by ACCEPT)

```json
{"schema": "ETK-RADIO-RUNSHEET v1", "accepted_epoch": 1788491975, "game_id": "NPUB31245",
 "stack": "S11", "res": 100,
 "hypothesis": "zlatez vs sddepth on the 20260901 chassis at res 100, warm runs only",
 "arms": [{"label": "A", "tune": "tu_debug=zlatez", "clk": 925, "pwr": "race", "n_target": 3},
          {"label": "B", "tune": "tu_debug=sddepth", "clk": 925, "pwr": "race", "n_target": 3}],
 "stop_rule": "perfect_pct median gap >= 5 pts at N>=3 per arm, or 6 runs without a gap",
 "next": "one more warm race on A, same track; then DRIVER -> Balanced for B"}
```

`n_have` is never stored: the packer recounts it from the ledger with dyno's key on
every pack, and Pitstop recounts it on render. A stack tag change voids the sheet on
screen ("STACK CHANGED — arms reset") because dyno refuses cross-stack comparison.

### 3.4 Credential (`/storage/roms/etk/config/radio.json`, chmod 600, written by STEP 7b)

`{"url": "https://203-0-113-7.sslip.io", "token": "…"}` — absent = feature off. Never
deployed by the card image, never synced by PADDOCK, never in `tune_tag`.

---

## 4. Specialization — what "tuned" means here

No weights are trained: the node has no GPU, a CPU LoRA on four cores is days per epoch,
and a few dozen golden cases would overfit anything. The specialization is four layers,
each versioned in the repo and each stamped into the debrief so advice is attributable
the way a session is:

1. **The Modelfile** — `FROM qwen3.5:9b`, `temperature 0.2`, `num_ctx 8192`,
   `num_predict 700`, `SYSTEM` = `prompts/engineer.md`; `ollama create etk-radio:9b`.
   Thinking off by default (a 4B spent 2,766 tokens deliberating over a two-sentence
   question on the same node); `think` is a service knob the eval may flip.
2. **The system prompt (the doctrine)** — the Engineer's role and voice (cockpit skill:
   short, calm, useful; "GPU's got headroom; you're CPU-limited"); Fable's Challenge
   (`perfect_pct` is THE KPI, res 100 only, lowering resolution is cheating); the
   ledger method (§B.2: duration + time-to-crash ceiling, medians, N on every claim,
   SURVIVED counts clean, bake and ABORTED excluded); the skews (§B.3 verbatim, ~500
   tokens); the falsified list (§F verbatim, ~400 tokens: never re-propose); the
   output contract (the schema in words: what each `kind` means, that a comparison
   needs both N, that `config_changes` may only use schema keys). ~1,200 tokens, first
   in the prompt so Ollama's prefix cache carries it between calls (observed 888 tok/s
   on a cached prefix vs 18 tok/s cold).
3. **The briefing (deterministic retrieval)** — per pack: each matched signature's
   `explanation`/`driver_dial`/`suggested_changes`; the `help` text of every field the
   session's config differs from the template on plus every field in
   `changes_since_last_debrief`; up to two §2.4 bullets chosen by keyword (`fence`,
   `query`, `FIFO`, `watchdog`, `GRID`, `bog`, `vault`, `audio`, `flicker`, `panic`); the
   title's `game_status.tsv` row; the accepted run sheet. ~800–1,200 tokens.
4. **Constrained output + guards** — Ollama `format:` with the debrief schema, then §6.

Corpus refresh = `git -C ~/etk pull` on the node (the image lane already keeps that
checkout current); `corpus_commit` in the debrief says which manual the advice read. A
Phase-5 LoRA on accumulated (pack → accepted debrief) pairs is a rented-GPU hour — money,
atoms, the operator's call — and Ollama imports it as an `ADAPTER` line, so the deploy
path exists if it is ever earned.

---

## 5. Model rungs and the time budget (measured, same-shape node, 2026-09-02..04)

4 OCPU A1, 24 GB, Ubuntu 24.04, Ollama pinned `OLLAMA_HOST=127.0.0.1:11434`,
`OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=30m`.
Long prompt = ~4,000 tokens, `num_ctx 8192`.

| Model | On disk | RAM peak | Prompt-eval, long | Gen short / long | TTFT long | Load (cold) |
|---|---|---|---|---|---|---|
| `qwen3.5:4b` | 3.4 GB | 3.9 GiB resident | **27.7–29.9 tok/s** (4.1k and 7.2k prompts, 2026-09-06, §10.1) | 10.2 / 6.4–7.6 | 137–259 s | 7 s warm |
| **`qwen3.5:9b`** | 6.3 GB | 8.1 GiB | **17.9 tok/s** | 5.9 / 5.0 | 225 s | 91 s |
| `qwen3:8b` | 6.7 GB | 7.3 GiB | 15.4 | 6.4–7.0 / 3.3 | ~240 s | 122 s |
| `qwen3:14b` | 10.9 GB | 11.4 GiB | 8.8 | 3.7 / 2.1 | 457 s | 90 s |
| `qwen3-coder:30b-a3b` (MoE) | 18.6 GB | 19.5 GiB | 20.1 | 14.5 / 4.0 | 200 s | 227 s |

Cold weights load at ~43 MB/s off the free-tier boot volume; a model already in the
page cache loads in seconds. The MoE reads like an 8B and writes like a 14B once the
context is long, and leaves no room for the forge — rejected (on 2026-09-06 it also cold-loaded for
225–240 s before each of two reviews, 8¼ minutes wall apiece; §10.1). The 14B is
batch-only.

**Debrief arithmetic on the 9b:** doctrine 1,200 (cached after the first call of the
evening) + briefing 1,000 + evidence 1,500 + task 300 ≈ 4,000 tokens → ~140 s prefill
warm (~225 s cold) + ~450 output tokens at 5 tok/s ≈ 90 s → **4–5.5 min per debrief**,
async, toasted when ready. The 4b, at its measured 28 tok/s prefill and 6.4–7.6 tok/s
generation, lands near **3.5 min** for the same pack (143 s + 64 s) and is the
radio-check model (interactive; the `_ProgressCard` shows elapsed time, which is honest
movement); a 7,170-token prompt cost it 259 s of prefill alone, which is why the pack
budget is a hard cap. With `OLLAMA_MAX_LOADED_MODELS=1`, every 4b↔9b switch pays a
reload (7 s from page cache for the 4b; up to 91 s cold for the 9b) — Phase 1 measures
whether the two-model split is worth it or one model should do both jobs.
Measure both on the node with `bench.py`-style timing before choosing; the eval (§10)
scores them. If the tenancy is ever cut to 2 OCPU / 12 GB (§12), the 9b still fits at
roughly half the speed and the 4b becomes the default.

---

## 6. Guards (discipline in code)

Each guard names the law it enforces and what `tools/test_radio.py` asserts.

| Guard | Law | Effect |
|---|---|---|
| **No crown below N** | §B.3 "N≥3 before any crown"; dyno's `LOW-N` | a finding that compares arms must carry `evidence` with both arms' `n ≥ 3` (from the pack's dyno table, not the model's claim); otherwise it is rewritten to `kind: observation` with `low_n` tag or dropped |
| **Never re-propose §F** | §F "never re-propose; the disproof is the asset" | `config/falsified.json` entries match on `TU_DEBUG` token, `yaml_key`, env knob, or power rung (e.g. `sysmem` as a verdict, `max_map_count`, `Thread Scheduler`, `noconstcheck`, GRID as the GT5P pack fix, attract-mode trials for crash classes, SRM-on-disc for ISO stutter, FIFO fetch/reorder combos for RR7); a match drops the recommendation and records it under `guards.dropped` |
| **Schema vocabulary only** | TUNING's section-aware injector refuses foreign keys | `config_changes[].yaml_key` must exist in `pitstop_fields.json`; `new_value` must be in `options` or within `[min,max]` on `step`; otherwise dropped |
| **Resolution is not a KPI lever** | §2.1 "resolution-lowering = cheating" | `Resolution Scale` below the session's value is allowed only under a crash signature that lists it (`suggested_changes`) and is tagged `crash_net`; never as `kind: config` for a KPI claim |
| **Bake and ABORTED are not feel evidence** | §B.3 | packs tagged `bake`/`aborted` forbid `fps`/`perfect` claims in findings; the model may only recommend `next_run` (a warm run) |
| **Attribution before narrative** | §B.3 | a `keepalive_absent` or `stack_change` tag forces an `investigate` recommendation ahead of any tune advice ("rule out our own code before blaming hardware") |
| **Data, never commands** | §12 security | the debrief is JSON validated against the schema; prose fields are rendered as text; nothing in it is ever executed, sourced, or written to a config by the service or the rig |
| **ASCII surfaces** | §A.3 glyph law, notification law | `radio`/`headline` are transliterated to ASCII before any toast or `pit_note.txt` write; over-length is truncated at a word boundary |
| **Evidence beside every claim** | §B.3 "attribution outranks narrative"; the 2026-09-06 pre-prototype (§10.1) | every finding's `evidence[]` must resolve to a pack field or a quoted pack line; the renderer prints the resolved line verbatim under the claim, so a semantic flip ("abort" for "requeue", 4 MB read as 288 MB) is visible at a glance; a finding whose evidence does not resolve is demoted to `observation` and tagged `uncited` |
| **Never truncate silently** | §10.1: three of four prompts were cut at 4,098 tokens and the models judged fragments as whole files | the service sets `num_ctx` explicitly, counts the prompt before the call, and REJECTS a pack that would not fit (`failed: over budget`, logged, toasted only for operator-pressed sends); Ollama's own truncation is never allowed to decide what the model saw |

A rules-only debrief (no model) is produced by the same pipeline with the model stage
skipped: crash-signature text + dyno arms + computed tags + the run sheet's `next`. It
is the eval baseline (§10) and the fallback when the node is unreachable for the
`headline`/`pit_note` (computed on the rig from dyno alone: "zlatez@925 N=2 of 3 — one
more warm run").

---

## 7. Surfaces (Pitstop)

### 7.1 Phase 0 — PIT NOTE (zero UI change)

`radio_send.sh` writes the debrief `headline` to `$PIT_NOTE_FILE` (atomic tmp+mv,
ASCII, ≤ 2 wrapped lines as `load_pit_note` already renders). TELEMETRY shows it under
the career anchor exactly as BuildSimpleTelemetry drew it in 2026-06. When no debrief
exists the file is absent and the block is suppressed, as today.

### 7.2 TELEMETRY detail card — RADIO DEBRIEF block

Rendered after SUGGESTED FIX (crash rows) or after the gauges (clean rows) when
`radio/<epoch>.debrief.json` exists; the card already scrolls.

```
RADIO DEBRIEF  (qwen3.5:9b · 4m31s · manual 8401cbc)
  "Fence wedge at six minutes, keepalive caught it, you finished. Two rescues an
   hour is the zlatez baseline on this chassis. Nothing to change yet."
  * next run: one more warm race on this arm (zlatez@925/race, N 2 of 3)
  * mechanism: 00E59005 fence park (#2), absorbed by keepalive          [dmesg]
```

### 7.3 RADIO tab

Registry: `CURRENT_TAB_RADIO = 6`; `TABS` order `TELEMETRY TUNING TOOLS DRIVER POWER
RADIO PADDOCK`. RADIO reads local files only on entry (no fetch), so the registry's
rule holds: PADDOCK stays the only tab that fires a network event on entry. Gated like
PADDOCK: the tab exists only when `radio.json` exists. Live in `ETK_NO_TARGET` mode
(reads nothing per-title that is unresolved; the run sheet is per-game and hides).

```
 TELEMETRY  TUNING  TOOLS  DRIVER  POWER  [RADIO]  PADDOCK
 RADIO  ·  SM8250 · ROCKNIX 20260901 · 26.2.2_gtk_0.7 · core 0.9.0.3
 ------------------------------------------------------------------------
 LAST DEBRIEF   NPUB31245   Sep 3 23:19   SURVIVED:Adreno    9b · 4m31s
   "Fence wedge at six minutes, keepalive caught it, you finished. Two
    rescues an hour is the zlatez baseline on this chassis. Nothing to
    change yet."
   EVIDENCE   rescues=1 [ledger]  ·  00E59005 fence park [dmesg]  ·  N=2
 ------------------------------------------------------------------------
 RUN SHEET   zlatez vs sddepth · 20260901 chassis · res 100 · warm only
   A  tu_debug=zlatez    925  race    N 2/3   perfect 3.0%   LOW-N
   B  tu_debug=sddepth   925  race    N 0/3   -
   NEXT   one more warm race on A, same track; then DRIVER -> Balanced for B
   [proposed 23:19 — not yet accepted]
 ------------------------------------------------------------------------
 PENDING  none          RADIO  linked · last contact 23:19 · 2 debriefs today
 CONFIRM: accept run sheet   X: load fix -> TUNING   Y: radio check   SELECT: refresh
```

Actions (all operator-pressed; pad map follows the existing CONFIRM/BACK vocabulary):

- **REFRESH** — drains `pending/` and re-reads files; runs under
  `_run_with_spinner(..., indeterminate=True)` with the RADIO busy frame.
- **ACCEPT RUN SHEET** — writes `run_sheet.json` (§3.3) atomically. Bytes only: the
  sheet changes what the next packs carry and what the tab counts; it applies no dial.
- **LOAD FIX → TUNING** — stages the recommendation's `config_changes` into the TUNING
  tab's pending edits (already validated on the node; re-validated here against the
  same schema) and switches to TUNING with the changed rows highlighted. The operator
  reviews and presses APPLY; the resulting `config_changes.tsv` rows carry
  `source=radio:<epoch>`. A `dial` recommendation switches to DRIVER with the dial
  named in the status line; nothing is pre-selected there (a reboot-gated surface).
- **RADIO CHECK** — a pad-friendly menu of question templates (no keyboard on the
  couch): *Why did it crash? · Is arm B ahead yet? · What should I run next? · Explain
  this row · Am I CPU- or GPU-bound?* Free text when a keyboard is attached. Runs
  `radio_send.sh ask <id>` on the fast model behind a `_ProgressCard` ("RADIO: engineer
  thinking · 0:47"); the answer renders in a result card and is kept in
  `radio/asks.log`. Refuses while a game is running or the ledger row is unstamped
  (the game-switcher's own gate, `_game_running()` OR `session_anchor.txt`).

### 7.4 FEEL (the human sensor)

After a session, the first Pitstop launch or the next RADIO entry offers a one-press
verdict: **SMOOTH · STUTTER · HITCH · SILENT** (or skip). It lands in
`radio/<epoch>.feel` and in the next pack as `operator.feel`. This is the manual's
"verdict from the operator's screen" made an input, and the only sensor for audio
underruns, which are forensically invisible.

### 7.5 Toasts (ASCII, "ETK" surface, byte-exact app-name)

`RADIO: debrief ready` / `<headline>` on arrival · `RADIO: sent, engineer thinking` only
for operator-pressed sends · `RADIO: no signal` only for operator-pressed sends that
fail. Automatic sends fail silently to `radio/radio.log`. `tools/test_notify.py` pins
the new sender to the roster and the ASCII rule.

---

## 8. Rig side

- **`bin/radio_pack.py <epoch>`** — python stdlib; sources nothing (paths from
  `env.sh` are re-derived from `ETK_ROOT` like `etk_drift.py` does); reads only under
  `$TELEMETRY_DIR`, `$RPCS3_CUSTOM_CONFIGS`, `/storage/turnip/loaded`,
  `$ACTIVE_TUNE_FILE`, `$ACTIVE_CORE_FILE`, `/storage/etk-power/profile`; calls
  `tools/etk_dyno.py --json --ledger $SESSIONS_LEDGER --game <ID> --res <session res>`;
  writes `radio/<epoch>.pack.json` atomically. Keeps the newest 50 packs/debriefs
  (tiny; SD wear is a rotation, not a stream).
- **`bin/radio_send.sh`** — POSIX sh; `source env.sh`; `set -u` after; reads
  `radio.json` with `jq`; header file `$WORK/hdr` under `/dev/shm` (umask 077); `curl
  -fsS --connect-timeout 10 --max-time 30` per call; `debrief`: pack → POST → poll every
  20 s up to `RADIO_WAIT_S` (default 720) → store → `pit_note.txt` → toast; on timeout
  write `pending/<job>.json`; `drain`: GET every pending job; `ask`: POST the question
  with the last pack's id. Exit codes are for the log; nothing here can fail the
  postmortem.
- **Postmortem hook (Phase 2b, ONE line, last thing in the file):**
  ```sh
  # --- RADIO (experimental): hand the row to the engineer, detached, off the <2 s path ---
  [ "${ETK_RADIO:-1}" = "1" ] && [ -f /storage/roms/etk/config/radio.json ] && \
      nohup sh "$ETK_ROOT/bin/radio_send.sh" debrief "$NOW" >/dev/null 2>&1 &
  ```
  `$NOW` is the row's epoch, the join key of every archive postmortem just wrote. It
  runs after the breadcrumb consume, so the game switcher's gate is already clear.
- **install.sh** — STEP 3 pushes `tools/etk_dyno.py` beside `etk_drift.py` (the
  `tools/` rm-rf-on-uninstall reasoning) and chmods it; STEP 5 pushes
  `config/radio_debrief.v1.json`; **STEP 7b RADIO LINK**: if `RADIO_URL` and
  `RADIO_TOKEN` are set, preflight `GET /v1/health` through a header file (401/403 →
  "token rejected", 000 → "no route: check the doors"), print the node's model and
  corpus commit, write `radio.json` (umask 077, chmod 600) over ssh exactly as STEP 7
  writes `paddock.json`; otherwise "RADIO: no URL/token in etk.conf — skipped".
  `uninstall.sh` removes `radio.json` and `etk_telemetry/radio/` stays (telemetry is
  Tier-B state).
- **`etk.conf.example`**
  ```
  # --- RADIO (experimental): the race engineer on the pit radio, on YOUR free node ---
  # Post-session debriefs and A/B run sheets from an open model running on your own
  # etk-cloud (tools/radio/provision_etk_cloud_ai.sh sets it up and prints the token).
  # Leave RADIO_URL empty = feature off: no tab, no traffic, nothing leaves the rig.
  ETK_RADIO="1"
  RADIO_URL=""
  RADIO_TOKEN=""
  ```
- **Always-reboot gate:** `radio.json`, `radio/`, `run_sheet.json` live under
  `/storage`; nothing is kept in SHM except the header file, which is per-call.

---

## 9. Node side

- **`provision_etk_cloud_ai.sh` (operator runs it — deploy on someone else's computer,
  multi-GB pulls).** Steps, each with a *you know it worked when* line: fetch the
  ollama installer to a file and print its head before running it; write
  `/etc/systemd/system/ollama.service.d/override.conf` with the four `Environment=`
  lines; `ollama pull qwen3.5:9b` + `qwen3.5:4b`; `ollama create etk-radio:9b` /
  `etk-radio:4b` from the Modelfiles; `python3 -m venv` is NOT needed (stdlib service);
  install `etk-radio.service` (user `ubuntu`, `WorkingDirectory=~/etk/tools/radio`,
  `Restart=always`, `Environment=RADIO_CONFIG=/etc/etk-radio/config.json`); mint the
  token (`openssl rand -hex 32` → `/etc/etk-radio/token`, 0600, printed ONCE); write
  `.env` with `SITE_ADDRESS=<reserved-ip-dashes>.sslip.io`; `docker compose up -d
  caddy`; print the two doors to open (OCI security list ingress 80/443, node iptables)
  and the verification: `ss -ltn` must show `127.0.0.1:11434` and `127.0.0.1:8737`, never
  `0.0.0.0` on either; `curl https://<site>/v1/health` with the token from the laptop.
- **`service.py`** — `ThreadingHTTPServer`; every route checks `Authorization: Bearer`
  with a constant-time compare; bodies capped at 64 KB; `POST /v1/debrief` validates
  PACK v1, inserts a job (sqlite, `~/etk-radio/jobs.db`), returns 202; one worker thread:
  wait while `~/forge-runs/active_*` exists (poll 30 s, log "deferred: forge active");
  build the briefing; call `POST 127.0.0.1:11434/api/chat` with `stream:false`,
  `format:` = debrief schema, `options.num_ctx 8192`, `keep_alive "30m"`; validate;
  compute tags; run guards; store `~/etk-radio/results/<epoch>.json`; `GET
  /v1/jobs/<id>` → `queued | deferred | running | done | failed` + the debrief when
  done; `POST /v1/ask` runs on the fast model synchronously (≤ 180 s) against the named
  pack; `GET /v1/health` → model names, corpus commit, ollama reachable, forge state.
  Results retained 30 days; logs to journald; no shell is ever spawned from a request.
- **Coexistence** — `OLLAMA_KEEP_ALIVE=30m`; the forge lock above; `MemoryHigh=10G` on
  the service unit is meaningless (Ollama holds the weights), so the honest guard is the
  lock plus `forge.sh` preflight printing `ollama ps` when it is non-empty ("a model is
  resident: the radio is live; the build will page it out").
- **Corpus** — `~/etk` (already present, `git pull` before each debrief is cheap and
  keeps `corpus_commit` honest; a pull failure uses the checkout as is). Optional
  private clone `~/etk-dossiers` for Phase 4 RAG — operator's call (§13).

---

## 10. The eval (validate before integrate — the model has to earn its place)

`tools/radio/eval_cases/*.json`: a PACK v1 fixture plus assertions, each drawn from a
verdict the ledger already paid for.

| Case | From | Asserts on the debrief |
|---|---|---|
| `sysmem_no_crown` | 2026-06-17 near-crowning (one 906 s run, warm-up not knob) | no comparison finding; `low_n`; recommendation ∈ {next_run} |
| `sddepth_verdict` | 2026-07-10, N=5 vs N=35, 4–5× wedge-rate cut at equal fps | a comparison finding is allowed; both N present; no KPI-gain claim |
| `grid_gt5p_split` | GRID-B on the GT5P pack: contention cut, fps unmoved | no GRID recommendation for fps on this title |
| `bake_session_fps` | 7,423-shader run logging fps 30.8 | tag `bake`; no fps/perfect claim; next_run only |
| `attract_row` | attract-mode run | tag `attract`; "invalid for crash-class" finding |
| `zlatez_925_lown` | S11, N=2 (today's ledger) | `low_n`; next_run on the same arm |
| `audio_from_aud_cell` | `skip=116` in the aud cell, silent log | audio finding cites `aud`; no "audio fine" from log silence |
| `keepalive_off_day` | 2026-09-01/02: fault present, rescues=0, same stack | `keepalive_absent`; `investigate` before any tune |
| `res_lowering_kpi` | a KPI question where 66% "would help" | no `Resolution Scale` decrease as `config` |
| `falsified_tempt` | a crash where `max_map_count`/`Thread Scheduler` tempt | none proposed; `guards.dropped` may show the attempt |
| `panic_silent` | BCUS98114 PANIC rows with no kmsg lead-up | `panic_silent`; investigate; no config change |
| `hallucinated_key` (service test, fake Ollama) | synthetic model output with a foreign key and an out-of-range value | both dropped; debrief still valid |

### 10.1 Pre-prototype evidence — four code reviews through generic RAG (2026-09-06)

Before any RADIO code existed, four ETK files were reviewed on an identically-shaped
node through Open WebUI's attached-file retrieval (generated queries → chunk retrieval →
prompt; the prompt was cut at 4,098 tokens in three of four runs and ran at `num_ctx`
8192 in the fourth). `qwen3.5:4b` reviewed `blackbox_d.py` (with the manual attached) and
`session_postmortem.sh`; `qwen3-coder:30b-a3b` reviewed `etk_install_worker.py` and
answered "which file first" over the manual. Every claim was checked against the source.

| Run | Model | Prompt → prefill · generation | Wall | Verified right | Wrong or invented |
|---|---|---|---|---|---|
| `blackbox_d.py` + manual | 4b | 4,098 tok @ 29.9 tok/s = 137 s · 1,038 tok @ 7.6 | 4m40s | `parse_psi` −1.0 fallback · `/proc/self/fd` re-open after an unlink · `cur.close()` · the pstore path · the SSX campaign shape · the 2026-06-23 "armed 25 s late" incident · `_driver_apply` writing both files atomically — all real, correctly paraphrased | `tripwire` "has no implementation" (it does; chunk artifact) · `idle_ticks` "incomplete in context" (chunk artifact) · a daemon's `while True` called an infinite-loop risk · "burn a call" read as a phone call · `cffdump --once` given an invented rationale · ETK described as game-development telemetry |
| `session_postmortem.sh` | 4b | 7,170 @ 27.7 = 259 s · 457 @ 6.4 | 5m37s | the bounded `tail -c` read · the epoch comparison · the "Defense-in-depth alongside the Sentry's -x fix" comment | 4,194,304 bytes "≈ 288 MB" (288 MB is the RR7 log size on the next comment line) · "PC and RPCS3 logs" · "PS5 debug sessions" · `$HAYSTACK` "never used" (grepped three times in a chunk it did not get) · "hard-coded paths" for `${RPCS3_LOG:-…}` · a race condition that is not in the code |
| `etk_install_worker.py` | 30B MoE | 4,098 @ 20.1 = 204 s · 254 @ 3.8 · cold load 225 s | 8m15s | the docstring restated accurately: out-of-process, the SHM queue files, the game-launch yield | "aborting jobs if a game is launched" — the file requeues at the head and says abandoning would be unsafe · no code-level finding at all |
| "first file to review" over the manual | 30B MoE | 4,098 @ 19.9 = 206 s · 203 @ 3.9 · cold load 240 s | 8m19s | cites the §A.3 install-worker laws correctly | the "first file" is whichever chunk the generated queries retrieved, not a judgment |

What it says for RADIO, each mapped to a decision above:

- **Retrieval decided the answers, and truncation hid the loss.** The models judged
  fragments as whole files ("incomplete", "no implementation"). Decision 4 stands
  (a complete, bounded pack, deterministic briefing) and gains the never-truncate guard.
- **The 4b paraphrases real mechanisms well and invents when it concludes.** Its
  strengths sections verified line for line; its issues sections carried arithmetic
  errors, domain confabulation and advice against stated design (env-derived paths,
  fail-soft `2>/dev/null`, a daemon that runs forever). Decision 3 stands: findings from
  the model, verdicts in code — and the doctrine must state the kit's design intent
  explicitly (fail-soft, BusyBox, env.sh paths, daemons) or it gets "reviewed" as a
  defect; the pack carries computed numbers and the prompt forbids deriving new ones.
- **Semantic flips are the dangerous class**: abort/requeue, phone/tool call, 4 MB/288
  MB. The guards catch schema, N and §F, not a flipped sentence — hence the
  evidence-beside-every-claim guard and flip traps among the eval cases.
- **The cost matches §5.** The 4b at 28 tok/s prefill is ~3.5 min per 4k-token pack;
  the 30B cold-loaded for four minutes before each review and produced no findings —
  rejected on quality as well as RAM.
- **The next cheap probe, operator-run in Open WebUI:** the same two small files
  (`blackbox_d.py`, `etk_install_worker.py`, each under 8k tokens) on `qwen3.5:9b` with
  full-context mode instead of chunk retrieval — the direct A/B for the rung decision
  before any code is written. The 42 KB postmortem script is ~11k tokens and would cost
  the 9b ten minutes of prefill; it is the case for the pack budget, not a review target.

Scoring: the deterministic assertions pass or fail per case for the **rules-only
baseline** (must be 12/12 — it is the pipeline's contract) and for each model. Then the
operator reads both debriefs per case blind and marks the model's as *more useful /
same / worse* than rules-only on mechanism and next-action specificity. **Ship the
model only if it is "more useful" on ≥ 8 of 12 with zero assertion failures.** Otherwise
the rules-only debrief ships — still a win (one-file forensic bundle, pit note, run
sheet with dyno counts) — and the model waits for a better rung. This is the
audio-watchdog template: the plumbing is the root fix; the model is the part that has to
prove it is not a workaround.

The live scorecard afterwards: `etk_dyno` groups `config_changes.tsv` rows by `source`
and joins the sessions that followed — accepted radio advice vs operator-only changes,
by `perfect_pct` and time-to-crash, N on every line.

---

## 11. Bytes-to-atoms map (what the operator runs)

| Action | Threshold | Who |
|---|---|---|
| `provision_etk_cloud_ai.sh` on the node | deploy to someone else's computer; GB pulls | operator |
| Opening 80/443 at the OCI security list and iptables | infrastructure change | operator |
| `./install.sh` (STEP 7b writes `radio.json`) | deploy | operator |
| Cold boot after STEP 3 (the packer/sender on the rig) | always-reboot gate | operator |
| The rig's automatic debriefs once linked | standing authorization: the operator set `RADIO_URL` | the kit |
| `tools/radio.py debrief/ask/eval` from the host | consumes the node — the §1.1 test's letter, at $0 | **operator, pending a ruling** (§13) |
| Reading `radio/*.json` over ssh, running `test_radio.py`, building packs on the host mirror | bytes | Claude |

Every handoff in the phases below ends with one command in a `bash` block.

---

## 12. Risks, limits, and the rejected alternative

- **The Always-Free tier itself.** Press and Oracle's own pages disagreed in
  July–August 2026 about a halving to 2 OCPU / 12 GB; `etk-cloud` (a lapsed trial) still
  held 4 / 24 on 2026-09-06 (31 days uptime). If it halves: the 9b fits at ~half speed
  (10-minute debriefs, still async), the 4b becomes the default, and forge coexistence
  gets tighter. If the node ever goes: `radio.json` absent = the kit is exactly what it
  was.
- **Prefill is the cost, not generation.** Every 1,000 tokens of evidence is a minute
  on the 9b. The packer's caps are the performance budget; the eval measures
  `tokens.prompt` per case and fails a case that exceeds 4,500.
- **Numeric discipline in a 9B.** Small models miscount and over-conclude — the reason
  N is computed on the rig by dyno and checked by the guard, never trusted from prose.
- **Couch WiFi.** Short requests, job polling, `pending/` drain, no held connections.
- **Cold weights.** 60–120 s to load after the 30-minute keep-alive lapses; the first
  debrief of an evening is slower; `/v1/health` reports whether a model is resident.
- **Prompt-cache invalidation.** Any change to `engineer.md` re-prefills once; that is
  fine and the `prompt_sha256` records it.
- **PII and paths.** The pack is allowlisted: game serials, ledger fields, log lines
  with the argv line dropped and paths reduced to `dev_hdd0/game/<ID>`; the node is the
  operator's own tenancy; nothing is shared onward. `tools/release_sanity.sh`'s
  pseudonym/PII sweep covers `tools/radio/` like any other tree.
- **Semantic flips in prose.** The 2026-09-06 reviews (§10.1) flipped requeue to abort
  and a tool call to a phone call; a flipped mechanism sentence in a debrief is a wrong
  recommendation carrying a correct N. Mitigation: the evidence-beside-every-claim
  guard, flip traps in the eval, and the 9b over the 4b unless the eval says otherwise.
- **The rejected alternative — inference on the rig.** Measured on 2026-09-06: 7.5 GB
  RAM (6.2 GB free idle), 4×A77 @2.42 + prime @2.84 + 4×A55, NEON + dotprod, no
  i8mm/SVE, 4 KB pages, python 3.14, read-only root (a binary would live under
  `/storage/etk/`, the python site-packages precedent), 3.5 GB free on internal storage.
  A 4B Q4 model fits only while IDLE (RPCS3 peaks at 3.5 GB) and would run its ~4,000-
  token prefill at roughly 15–25 tok/s on the big cores with ~4–6 tok/s generation —
  about the same five minutes as the 9b on the node, at 4b quality, on battery, at
  full thermal load, with the weights on the card. Not for v1. The spike condition:
  no network at the track, or the free tier disappearing — then `radio_send.sh`'s
  `RADIO_URL` can point at `http://127.0.0.1:11434` behind a local `etk-radio` runner
  and nothing above changes shape.

---

## 13. Open questions for the operator

1. **The §1.1 ruling on host-side inference.** `tools/radio.py ask` consumes the node
   at $0. Strict reading: operator-run, one command per handoff. Carve-out reading:
   inference is analysis, not a mint. Which?
2. **Dossiers on the node** for Phase 4 RAG (`~/etk-dossiers`, private clone on the
   operator's own tenancy) — yes / no / later.
3. **Tab position** — RADIO between POWER and PADDOCK (proposed), or beside TELEMETRY.
4. **Name** — RADIO for the surface, `etk-cloud-ai` for the mechanism; alternatives
   considered: ENGINEER, DEBRIEF, PIT BOARD (taken by 0.8.7's edition name).
5. **FEEL prompt timing** — at the next Pitstop launch (proposed) or as a
   post-session toast with a chord.

---

## 14. Phases — each ends at a surface and a handoff

**Phase 0 — host harness, no node, no rig (Claude builds; operator reviews).**
Contracts (`schema/*.json`), `radio_pack.py` run against the host mirror
`state/etk_telemetry/` for any epoch, `etk_dyno.py --json`, `briefing.py`, `guards.py`,
`falsified.json`, `engineer.md`, `service.py` under a fake Ollama, `test_radio.py` +
`test_service.py` green, the 12 eval fixtures written from the ledger. Exit: a pack for
row `1788491975` inspected by the operator; rules-only debrief renders in a terminal.

**Phase 1 — the node (operator runs the mint).** After `git -C ~/etk pull` on the node:

```bash
ssh etk-cloud 'cd ~/etk && git checkout radio && git pull && tools/radio/provision_etk_cloud_ai.sh --model qwen3.5:9b --fast qwen3.5:4b'
```

What it does: installs Ollama pinned to localhost, pulls two models, creates the
Modelfiles, installs the service and Caddy, mints the token. Watch for: `ss -ltn`
showing `127.0.0.1:11434` and `127.0.0.1:8737` only; the token printed once. Falsifier:
any `0.0.0.0:11434` line, or the health check answering without the token. Then the
eval over a tunnel, and the model decision (§10). Exit: `/v1/health` from the laptop;
scorecard in `docs/RADIO_EVAL.md`.

**Phase 2a — rig, operator-triggered only (validate before integrate).** `etk.conf`
gains `RADIO_URL`/`RADIO_TOKEN`; the operator runs `./install.sh` (STEP 3 + 5 + 7b) and
cold-boots; TOOLS gains "RADIO: send last session" which runs `radio_send.sh debrief
<last epoch>` behind the spinner. Exit: a toast, then the PIT NOTE line on TELEMETRY
and the detail-card block for that row — verified on the rig's screen, not the log.

**Phase 2b — automatic.** The one-line postmortem hook, after ≥ 5 manual sends have
gone through cleanly. Exit: a race ends, the operator does nothing, and within ~6
minutes the toast arrives; a cold boot later, `radio/` and `run_sheet.json` are still
there.

**Phase 3 — the RADIO tab.** Tab, RUN SHEET, ACCEPT, LOAD FIX → TUNING with the
`source` column, FEEL, REFRESH. Exit: an accepted sheet drives two arms to N=3 and the
tab shows the count climbing from the ledger alone.

**Phase 4 — deeper.** RADIO CHECK on the fast model; dossier RAG (`nomic-embed-text`
already proven on the same node shape; flat cosine index, stdlib); `tools/radio.py
debrief --attach decode.txt` to append a cffdump summary from the decode lane that
already runs on the same node; the on-rig spike if its condition ever arrives.

---

## 15. Documentation deltas when it ships

`TRACK_MANUAL.md`: §0 row (above), §2.3 component rows (`radio_pack.py`,
`radio_send.sh`), §A.3 tab row + the RADIO-reads-local-files rule beside PADDOCK's, §Q
(node paths, `radio.json`, the doors), §F if the eval retires the model. `CHANGELOG.md`
`[Unreleased] → Added: RADIO` in user voice ("your own free node writes you a pit
engineer's debrief after every session; nothing leaves the rig unless you link it").
`README.md` tab table. `etk.conf.example` block (§8). The PowerShell port mirrors STEP
7b only after the experiment graduates.
