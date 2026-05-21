# ETK Simple Telemetry — Implementation Dossier

**Target executor:** Claude Code with full repo access
**Author context:** Spec distilled from a long brainstorm + paper-prototype session that already produced real diagnostic findings against the live rig
**Scope discipline:** Build exactly what's specified. Do not build the deferred features. Do not propose features. Do not refactor adjacent code unless explicitly listed under "Pre-Flight Cleanups."

---

## 0. Read These Files First (in order)

Before writing or modifying any code, view these in order. They contain the load-bearing constraints:

1. `AI_MANIFEST.md` — the immutable laws (SHM sanctity, env.sh single source of truth, BusyBox-only shell, no GNU-isms, atomic writes, HUD format lock, the `PREV_STATE="$CUR_STATE"` line in the Sentry that must be preserved)
2. `scripts/env.sh` — current path/env-var truth; new exports get added here, never hardcoded
3. `bin/etk_pitstop.py` — the file being refactored; understand `load_menu_matrix`, `commit_and_verify`, `find_gamepad`, `EVENT_FORMAT`, the curses main loop, the existing gamepad/keyboard input handling
4. `config/pitstop_fields.json` — the curated tuning schema; do NOT modify in this work
5. `install.sh` — find the Sentry's IDLE↔RUNNING transition blocks (where `vault_d.sh` and `thermal_d.sh` are spawned/killed); the new post-mortem hook attaches there
6. `scripts/probe.sh` — the existing forensics tool; understand what it outputs and where (`$CRASH_LOG`)
7. `bin/mango_bridge.sh` — for understanding the HUD format string lock and current SHM read patterns (do NOT modify in this work)

---

## 1. What's Being Built

A **Simple Telemetry** layer for ETK consisting of two cleanly separated pieces:

**Piece A — Modular tab refactor of `etk_pitstop.py`** so that the existing matrix editor becomes one of two tabs, with a clean dispatcher for switching between TUNING and TELEMETRY tabs via L1/R1 (gamepad) and `[`/`]` (keyboard).

**Piece B — A Simple Telemetry data layer + new TELEMETRY tab** that shows a chronological session log with a career-stats anchor and (optionally) an AI-generated "pit note" string. Data is aggregated post-mortem from existing data sources at session-end. No new continuous-sampling daemon. Zero added runtime overhead during gameplay.

The headline UX: open Pitstop after a session, switch to TELEMETRY tab, see a glanceable career-stats line + recent sessions table with day-separator section headers + a short pit-engineer-voice paragraph at the bottom (when present).

---

## 2. The Single Most Important Discipline

**Heisenberg principle for this build:** observation overhead reduces durability. The MVP must NOT add a continuous-sampling daemon during gameplay. All telemetry aggregation happens **after** the RUNNING→IDLE transition, from sources that already exist (RPCS3.log, dmesg, thermal_d telemetry.log, vault state). The probe tool stays as an opt-in diagnostic, not a routine background process.

This rules out: `telemetry_d.sh`, MangoHud logging integration (MangoHud's logging is stripped from the Rocknix build anyway — `strings /usr/bin/mangohud | grep -i 'autostart_log\|output_folder'` returns empty), any per-second sampling loop.

---

## 3. Files To Create

```
$ETK_ROOT/
├── bin/
│   ├── etk_pitstop.py              (MODIFY: refactor for tabs + add TELEMETRY tab)
│   └── session_postmortem.sh       (CREATE: aggregates session data at RUNNING→IDLE)
├── config/
│   └── crash_signatures.json       (CREATE: seed pattern library)
├── scripts/
│   ├── env.sh                      (MODIFY: add new path exports + helper)
│   └── career_aggregate.sh         (CREATE: recomputes per-game career stats)
└── etk_telemetry/                  (CREATE on first session-end via mkdir -p)
    ├── sessions.tsv                (append-only ledger, created on first write)
    ├── config_changes.tsv          (append-only ledger, created on first write)
    ├── career/
    │   └── <GAME_ID>.txt           (per-game career stats, recomputed at session-end)
    └── pit_note.txt                (optional cached AI summary; UI renders gracefully when absent)
```

`install.sh` gets two surgical additions (see §10). The Sentry block is the only place it touches.

---

## 4. env.sh Additions

Append to `scripts/env.sh` after the existing exports:

```bash
# --- [ SIMPLE TELEMETRY ] ---
export TELEMETRY_DIR="$ETK_ROOT/etk_telemetry"
export SESSIONS_LEDGER="$TELEMETRY_DIR/sessions.tsv"
export CONFIG_CHANGES_LEDGER="$TELEMETRY_DIR/config_changes.tsv"
export CAREER_DIR="$TELEMETRY_DIR/career"
export PIT_NOTE_FILE="$TELEMETRY_DIR/pit_note.txt"
export SIGNATURES_FILE="$ETK_ROOT/config/crash_signatures.json"

# Helper: ensure telemetry tree exists; safe to call repeatedly
telemetry_init_dirs() {
    mkdir -p "$TELEMETRY_DIR" "$CAREER_DIR"
}
```

All paths in subsequent scripts derive from these exports. **No hardcoded paths.**

---

## 5. Session Ledger Schema (`sessions.tsv`)

**Format:** tab-separated values, append-only, one row per completed session.

**Header row** (written exactly once on first creation):
```
epoch	duration_s	build	game_id	status	peak_cpu_pct	peak_ram_mb	peak_temp	avg_temp	crash_sig	fence_at_crash	shaders_harvested	drain_pct	thermal_overrides
```

**Field definitions:**

| Field | Type | Source | Notes |
|---|---|---|---|
| `epoch` | int | `date +%s` at post-mortem run | Session-end timestamp |
| `duration_s` | int | RPCS3.log thread time OR file mtime delta | Seconds. `0` if unknown |
| `build` | string | `$ETK_BUILD_TYPE` | FULL / LITE / RAW |
| `game_id` | string | `$RECENT_ID_FILE` | e.g. `NPUA80075`; `UNKNOWN` if missing |
| `status` | string | derived (see §6) | Closed set: `CLEAN`, `RECOVERY:Adreno`, `RECOVERY:OOM`, `RECOVERY:SPU`, `RECOVERY:Unknown`, `PANIC` |
| `peak_cpu_pct` | int | RPCS3.log PERF lines | `0` if not parseable |
| `peak_ram_mb` | int | RPCS3.log Peak lines | `0` if not parseable |
| `peak_temp` | int | thermal_d telemetry.log max during window | `0` if no thermal data |
| `avg_temp` | int | thermal_d telemetry.log mean during window | `0` if no thermal data |
| `crash_sig` | string | crash_signatures.json match against dmesg+RPCS3.log | empty for CLEAN |
| `fence_at_crash` | int | dmesg `rb 0: fence:` last value | `0` if no fence event |
| `shaders_harvested` | int | vault delta (start vs end) | From SHM `vault_new.txt` or vault count delta |
| `drain_pct` | int | battery delta (start vs end) | Negative integer; `0` if not captured |
| `thermal_overrides` | int | count of PIT events in thermal_d telemetry.log during window | `0` if no telemetry.log |

**Why TSV not CSV:** awk-friendlier with values that might contain commas (game names later, etc.). Tab is a safer field separator on BusyBox.

**Concurrency:** sessions write at session-end only (single writer, single event). No locking needed. Append is atomic at the filesystem level for small writes (single row << pipe buffer size).

---

## 6. Status Encoding (closed set)

Status determination logic in `session_postmortem.sh`:

1. **No RPCS3 process exited within last 30s + no fault patterns in dmesg** → `CLEAN`
2. **Kernel `a6xx_irq` / `gpu fault` / hangcheck in dmesg** → `RECOVERY:Adreno`
3. **Kernel `oom-killer` / `Killed process.*rpcs3` in dmesg** → `RECOVERY:OOM`
4. **RPCS3.log shows `SPU.*decoder` / `spu_recompiler.*fail` near end** → `RECOVERY:SPU`
5. **Process exited unexpectedly but no signature matched** → `RECOVERY:Unknown`
6. **Reboot detected (uptime < session start)** → `PANIC`

PANIC detection: if at post-mortem time `$(uptime -s)` is later than the session's start epoch, the rig rebooted during the session — classify as panic. This catches kernel panics that take the device down hard.

CONFIG changes (user saves a tuning edit in Pitstop) are NOT sessions. They live in a parallel ledger (`config_changes.tsv`) and get interleaved chronologically in the UI render. See §8.

---

## 7. `session_postmortem.sh` — Specification

**Trigger:** invoked once by the Sentry on RUNNING→IDLE transition, after the existing pkill block.

**Behavior:**

```
1. Source env.sh
2. telemetry_init_dirs
3. Trigger probe.sh fresh (so $CRASH_LOG is current)
4. Read $RECENT_ID_FILE for game_id (fall back to UNKNOWN)
5. Compute session start_epoch:
   - From SHM session_start.txt if it exists (written at IDLE→RUNNING; see §10)
   - Else from RPCS3.log's earliest timestamp
6. Compute current_epoch = $(date +%s)
7. duration_s = current_epoch - start_epoch (clamped >= 0)
8. PANIC check: if $(date -d "$(uptime -s)" +%s) > start_epoch → status=PANIC, skip further detection
9. Otherwise, classify status via dmesg + RPCS3.log scan (see §6)
10. If status != CLEAN: match $CRASH_LOG against crash_signatures.json
    - Use grep -E against each signature's patterns array
    - First match wins; write signature id to crash_sig field
11. Aggregate metrics:
    - peak_cpu_pct: awk-parse RPCS3.log PERF lines for max CPU
    - peak_ram_mb: awk-parse RPCS3.log Peak: lines for max
    - peak_temp, avg_temp: awk over thermal_d telemetry.log entries within
      [start_epoch, current_epoch] window (if telemetry.log exists)
    - fence_at_crash: dmesg | grep "rb 0: fence:" | tail -1 | extract first int
    - shaders_harvested: read $SHM_DIR/vault_new.txt (or 0)
    - drain_pct: read $SHM_DIR/battery_start.txt vs current
      /sys/class/power_supply/.../capacity (or 0 if either missing)
    - thermal_overrides: grep -c "PIT" thermal_d telemetry.log within window (or 0)
12. Build TSV row with tab-separated fields
13. If $SESSIONS_LEDGER doesn't exist: write header row first
14. Append row to $SESSIONS_LEDGER (single `printf "%s\n" "$ROW" >> "$SESSIONS_LEDGER"`)
15. Invoke career_aggregate.sh for this game_id (regenerates career file)
16. Exit cleanly
```

**Constraints:**
- BusyBox-compliant. No `du -h`, no GNU `find -printf`, no `stat --format`.
- Wrap all `cat`/`awk`/`grep` against possibly-missing files with `2>/dev/null` and default-empty fallbacks.
- Atomic header write: write to `$SESSIONS_LEDGER.tmp`, then `mv`. Subsequent row appends are direct (single-line appends are atomic enough for our needs).
- Total runtime budget: < 2 seconds. This runs while the user is closing the game; can't block them.

---

## 8. CONFIG Changes — Parallel Ledger

When the user saves a config change via Pitstop's TUNING tab (i.e., `commit_and_verify()` succeeds), `etk_pitstop.py` writes a row to `$CONFIG_CHANGES_LEDGER` with format:

```
epoch	game_id	field_label	old_value	new_value
```

Example: `1747668700	NPUA80075	Multithreaded RSX	false	true`

Header row written on first creation.

The TELEMETRY tab merges sessions.tsv and config_changes.tsv by epoch when rendering, so CONFIG rows appear chronologically interleaved with session rows. Visual treatment of CONFIG rows in §11.

---

## 9. `crash_signatures.json` — Seed Library

Seed with these four signatures (calibrated from the live paper-prototype session against the actual Rocknix 20260517 + Mesa 26.1.0 stack):

```json
[
  {
    "id": "GPU_FENCE_TIMEOUT",
    "label": "Adreno",
    "patterns": [
      "a6xx_irq.*gpu fault",
      "msm_dpu.*hangcheck recover",
      "drm:recover_worker.*offending task.*rpcs3"
    ],
    "severity": "high",
    "summary": "GPU stalled waiting on render completion",
    "explanation": "The Adreno driver gave up waiting for the GPU to finish a frame. Often caused by per-frame complexity exceeding the watchdog window (lap-2 tunnel territory in GT5P).",
    "suggested_changes": [
      {"yaml_key": "  Driver Wake-Up Delay", "new_value": "50"},
      {"yaml_key": "  Resolution Scale", "new_value": "75"}
    ]
  },
  {
    "id": "OOM_KILL",
    "label": "OOM",
    "patterns": [
      "out of memory",
      "oom-killer",
      "Killed process.*rpcs3"
    ],
    "severity": "critical",
    "summary": "Kernel killed RPCS3 — out of RAM",
    "explanation": "System exhausted memory. Reduce graphical fidelity to lower RAM footprint.",
    "suggested_changes": [
      {"yaml_key": "  Resolution Scale", "new_value": "66"},
      {"yaml_key": "  Write Color Buffers", "new_value": "false"}
    ]
  },
  {
    "id": "SPU_RECOMPILER_FAULT",
    "label": "SPU",
    "patterns": [
      "SPU.*decoder",
      "spu_recompiler.*fail",
      "Predecessor not found for target"
    ],
    "severity": "medium",
    "summary": "SPU recompiler tripped on an instruction",
    "explanation": "Aggressive SPU block size couldn't trace a code path cleanly. Safer block size usually resolves.",
    "suggested_changes": [
      {"yaml_key": "  SPU Block Size", "new_value": "Safe"}
    ]
  },
  {
    "id": "THERMAL_INFERRED",
    "label": "Thermal",
    "patterns": [],
    "severity": "medium",
    "summary": "Possible thermal-related instability",
    "explanation": "No specific crash signature matched, but peak temperature exceeded ALARM_TEMP. Could be thermal-induced.",
    "suggested_changes": [
      {"yaml_key": "  Resolution Scale", "new_value": "75"}
    ]
  }
]
```

**Empty-patterns convention:** `THERMAL_INFERRED` matches when nothing else does AND post-mortem detects `peak_temp > $ALARM_TEMP`. Always have a fallback so non-CLEAN sessions get *some* signature attribution.

**Match precedence:** signatures evaluated in array order; first match wins.

---

## 10. `install.sh` — Sentry Integration

Find the existing IDLE→RUNNING and RUNNING→IDLE transition blocks in the Sentry script (where `vault_d.sh` and `thermal_d.sh` spawn/pkill happens). Add the following surgical lines.

**On IDLE→RUNNING transition** (after the existing vault_d/thermal_d spawn block, BEFORE the `PREV_STATE="$CUR_STATE"` line):

```bash
# Capture session start state for post-mortem
date +%s > "$SHM_DIR/session_start.txt"
cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1 > "$SHM_DIR/battery_start.txt"
```

**On RUNNING→IDLE transition** (after the existing pkill block, BEFORE the `PREV_STATE="$CUR_STATE"` line):

```bash
# Run telemetry post-mortem in background (non-blocking)
nohup bash "$ETK_ROOT/bin/session_postmortem.sh" >/dev/null 2>&1 &
```

**The `PREV_STATE="$CUR_STATE"` line at the end of each loop iteration is load-bearing for state machine correctness. Do not move it. Do not duplicate it. The additions go BEFORE it, in the transition branches.**

Also add to `install.sh`'s deployment section: copy `session_postmortem.sh`, `career_aggregate.sh`, and `crash_signatures.json` to their destinations. Ensure executable bits on the shell scripts.

---

## 11. `etk_pitstop.py` — Refactor for Tabs

**Keep single-file architecture.** Splitting into multiple Python files is premature for this scope. Use clear section headers within the file.

**Structural changes:**

```
# === GLOBALS ===
# Existing constants + new tab dispatch constants
CURRENT_TAB_TUNING = 0
CURRENT_TAB_TELEMETRY = 1
BTN_TL = 310  # L1 on gamepad
BTN_TR = 311  # R1 on gamepad

# === SHARED HELPERS ===
# Existing: find_gamepad, EVENT_FORMAT, color pair setup, etc.
# New:
#   load_sessions_ledger()  -> list of session dicts, newest first
#   load_config_changes()   -> list of config-change dicts
#   load_career_stats(gid)  -> dict or None
#   load_pit_note()         -> string or None
#   load_signatures()       -> list of signature dicts

# === TUNING TAB (existing logic, lifted unchanged in BEHAVIOR) ===
# Existing functions become:
#   draw_tuning(stdscr, state)
#   handle_tuning_input(state, event)
# Internally still uses load_menu_matrix, commit_and_verify, etc.

# When commit_and_verify succeeds, ALSO append to $CONFIG_CHANGES_LEDGER

# === TELEMETRY TAB (new) ===
#   draw_telemetry(stdscr, state)
#   handle_telemetry_input(state, event)
# Reads sessions.tsv + config_changes.tsv + career file + pit_note.txt
# Render described below

# === MAIN LOOP (modified) ===
# Maintains state["current_tab"]
# On each tick, dispatches draw_* and handle_*_input based on current_tab
# L1/R1 (BTN_TL/BTN_TR) and [/] toggle current_tab
# State (e.g., loaded matrix) loaded once at startup, persists across tab switches
```

**Tab strip rendering (row 1 of screen, both tabs):**

```
═[ TUNING ]══[ TELEMETRY ]═══════════════════════════════════════════════════
```

Active tab gets inverse-video (curses `A_REVERSE`) on its label.

**TELEMETRY tab layout** (constrained to ~74 cols × ~21 rows at size-28 monospace):

```
═[ TUNING ]══[ TELEMETRY ]═══════════════════════════════════════════ Pg 1/3

CAREER — Gran Turismo 5 Prologue (NPUA80075)
────────────────────────────────────────────────────────────────────────────
14h 32m total · 187 sessions · 62% clean · 71 crashes (58 recov / 13 panic)
48,931 shaders banked · +8 avg/session · streak 3 (best 7)
────────────────────────────────────────────────────────────────────────────

TIME      STATUS              DUR    TEMP    LOAD    RAM     SHD  DRAIN
────────────────────────────────────────────────────────────────────────────
8:49a  ★  CLEAN               2m20s  75/79°  8/9     77/90%   10  -3%
8:47a  ▸  CONFIG              ----   MT-RSX  OFF→ON
8:45a     RECOVERY:Adreno     1m34s  74/80°  6/10    76/91%    0  -5%

─ Yesterday ────────────────────────────────────────────────────────────────
3:12p  ★  CLEAN               2m47s  73/78°  7/10    78/91%    8  -4%
2:58p  ▸  CONFIG              ----   Reso    100→75
2:42p     RECOVERY:Adreno     0m42s  78/85°  9/11    88/96%    0  -2%

────────────────────────────────────────────────────────────────────────────
PIT NOTE — 8:49am session (★) ran your longest GT5P stint since
Multithreaded RSX flipped ON. 3°C cooler, ~3% extra battery drain
vs your 3 clean runs this week. Small sample, trend looks real.
                              Updated 12m ago · 4/20 calls today
────────────────────────────────────────────────────────────────────────────

[↑↓] Scroll  [L1/R1] Switch Tab  [B] Quit
```

**Visual conventions:**
- Day separator rules with the day name: `Today`, `Yesterday`, `Wed 5/18`, etc. Day groupings derived from row epochs, computed at render time.
- `★` (or fallback `*`) marks CLEAN rows; `▸` (or fallback `>`) marks CONFIG rows; recovery rows get no decoration.
- CONFIG rows use the TEMP column area to render `FIELD  OLD→NEW`. Other metric columns blank (`----`).
- Time column lowercase a/p to save chars: `8:49a` not `8:49am`.
- Avg/peak fields: `75/79°` reads as "avg 75, peak 79".
- LOAD: `avg/peak` with no unit decoration (`8/9`).
- RAM: `avg/peak%` (`77/90%`).
- ANSI color via curses color pairs is allowed (the manifest's no-ANSI rule applies to commander.sh's pit-wall pane, NOT to Pitstop curses UI). Suggested:
  - CLEAN status → green
  - RECOVERY:* → red or yellow per severity in signature library
  - CONFIG → cyan or dim
  - Day separators → dim

**Scrolling:** Up/Down on gamepad d-pad or keyboard arrows scroll the session list. Pg indicator at top right shows current page. Career row + pit note stay pinned; only the middle section table scrolls.

**When pit_note.txt is absent or empty:** suppress the PIT NOTE block entirely. The UI must render gracefully without it.

**When career file is absent (first session ever for this game):** show `CAREER — <game name> (<id>)` then `First session — no career stats yet`. Skip the stats line.

**When sessions.tsv has no rows for current game:** show `No sessions recorded for this game yet. Launch a game to start collecting telemetry.` in the table area.

**Cursor/highlight:** the TELEMETRY tab is read-only in this MVP. No "Apply Suggested Changes" action button. No drill-down detail view. **Both are out of scope.** Reading the data is the whole feature.

---

## 12. `career_aggregate.sh` — Specification

**Trigger:** invoked by `session_postmortem.sh` after appending a session row. Also safe to invoke manually.

**Behavior:**

```
1. Source env.sh
2. Accept game_id as $1 (required)
3. Read $SESSIONS_LEDGER, filter to rows where field 4 == $1
4. Compute aggregates with awk:
   - total_duration_s = sum of field 2
   - total_sessions = row count
   - clean_count = count where field 5 == "CLEAN"
   - crash_count = total_sessions - clean_count
   - panic_count = count where field 5 == "PANIC"
   - recov_count = crash_count - panic_count
   - clean_rate_pct = round(100 * clean_count / total_sessions)
   - total_shaders = sum of field 12
   - avg_shaders = round(total_shaders / total_sessions)
   - current_streak = consecutive trailing CLEAN rows (from newest backwards)
   - best_streak = max run of consecutive CLEAN rows ever
5. Format human-readable into $CAREER_DIR/<game_id>.txt:

   game_id=NPUA80075
   game_name=Gran Turismo 5 Prologue
   total_duration_human=14h 32m
   total_sessions=187
   clean_rate_pct=62
   crash_count=71
   recov_count=58
   panic_count=13
   total_shaders=48931
   avg_shaders_per_session=8
   current_streak=3
   best_streak=7

6. Atomic write via tmp+mv
```

**Game name resolution:** read from `$ETK_ROOT/config/game_names.json` if it exists, else use game_id as the display name. (This file may not exist yet — graceful fallback only.)

**Session minimum duration:** sessions with `duration_s < 60` are EXCLUDED from career stats (they're aborted launches, not real attempts). Lock this rule — never change it, or the historical numbers shift.

---

## 13. Pit Note — Deferred but UI-Ready

The TELEMETRY tab reads `$PIT_NOTE_FILE` if it exists and renders it as the bottom PIT NOTE block. Generation of this file is OUT OF SCOPE for this build.

For the MVP: `pit_note.txt` simply doesn't exist on first install. The UI renders without the block. Done.

Future generation paths (DO NOT BUILD): either host-side via `pit_wall_sync.sh` pinging an LLM and writing back, or rig-side via a future `pit_engineer_d.py`. Both are designed elsewhere; both are explicitly deferred.

If you encounter a `pit_note.txt` during testing (manually created), the UI should render its contents as-is in the bottom block, word-wrapped to fit within ~70 cols, capped at ~4 lines visible (truncate with `…` if longer).

---

## 14. Pre-Flight Cleanups (also in scope, small)

- stripped manually by human

---

## 15. Hard Constraints Recap

- **All shell scripts BusyBox-compliant.** No `du -h`, no GNU `find -printf`, no `stat --format`, no bash arrays in shell daemons, no `[[ ]]` (use `[ ]`). Python in `etk_pitstop.py` may use stdlib freely (already a dependency).
- **All paths derive from env.sh.** No hardcoded paths anywhere except inside env.sh itself.
- **Atomic file writes via tmp+mv** for any file that's read concurrently with being written (header creation, career files, pit_note when regenerated). Single-line appends to TSV ledgers are non-atomic by design — partial last line on a crash is itself a diagnostic signal.
- **SHM is sacred.** `/dev/shm/etk_shm/` is the IPC backbone. Don't add to it. Don't remove from it. The two SHM writes specified in §10 (`session_start.txt`, `battery_start.txt`) are the only new SHM keys; they're ephemeral session-scoped and don't violate the lifecycle rules.
- **HUD format string in `mango_bridge.sh` is locked.** Do not touch it. The telemetry layer does not surface in the HUD.
- **The `PREV_STATE="$CUR_STATE"` line in the Sentry must be preserved.** Additions go before it within each transition branch.
- **All config writes route through `commit_and_verify()`.** Don't add a new write path. When the TELEMETRY tab needs to log a CONFIG event, that happens in the TUNING tab's save handler, after `commit_and_verify()` returns success.

---

## 16. Validation Plan

After implementation, three on-device tests determine success:

**Test 1 — Clean session produces clean ledger row.**
Launch GT5P, race C-1 in the Alfa cleanly through trophy + save + graceful exit. Open Pitstop → switch to TELEMETRY tab. Expect: one row showing `CLEAN` with reasonable duration, temp, load, RAM aggregates. Career row shows `1 session, 100% clean, streak 1 (best 1)`.

**Test 2 — Forced crash produces matched signature.**
Set Resolution Scale = 100 + Driver Wake-Up Delay = 0 (known unstable on this stack per the paper prototype). Launch GT5P, race until the inevitable lap-1 Adreno hangcheck. Recover. Open Pitstop → TELEMETRY. Expect: row showing `RECOVERY:Adreno` with `crash_sig=GPU_FENCE_TIMEOUT` populated, fence_at_crash populated from dmesg.

**Test 3 — CONFIG row interleaves chronologically.**
After Test 2, in Pitstop TUNING tab, change Driver Wake-Up Delay from 0 to 50 and save. Switch to TELEMETRY tab. Expect: between the crash row and any new session row, a CONFIG row showing `Driver Wake-Up Delay  0→50`.

**Test 4 — Tab switching preserves state.**
In TUNING tab, navigate to a field and start editing. Press L1 (or `[`) to switch to TELEMETRY. Press R1 (or `]`) to switch back to TUNING. Expect: cursor in same field, unsaved edits preserved.

**Test 5 — Graceful absence of pit_note.txt.**
Without ever creating pit_note.txt, open TELEMETRY. Expect: bottom PIT NOTE block is absent, no error, layout adjusts cleanly.

If all five pass, the MVP closes the loop.

---

## 17. Explicit OUT-OF-SCOPE List

Do not build, do not stub, do not reference in code or comments any of:

- Preset garage / GT7-style tuning sheets / multiple presets per game
- Adaptive field schema / `_field_intel.yml` / per-field intelligence ranking
- Whisper-network / P2P / mDNS discovery / fleet_id / swarm anything
- SD card wear tracking / `wear_d.sh` / tire-gauge HUD element
- `pit_engineer_d.py` / on-device LLM calls / API key handling
- Session compare view / lineage tree / parent-preset relationships
- FPS metrics from MangoHud (logging is stripped from this Rocknix build; verified empty)
- `telemetry_d.sh` continuous sampling daemon (Heisenberg-deferred)
- Confidence levels, signature attempt counters, escalation chains, UPSTREAM_SUSPECTED state
- "Apply Suggested Changes" cross-tab action button
- Baseline marker / baseline_gen field
- Anonymous telemetry aggregation / cloud upload
- "Lifetime garage overview" multi-game career table (single-game career row only)

These are all designed in the conversation history. They are all explicitly deferred. The MVP is smaller than the design conversation by intent.

---

## 18. Code Conventions

- Python: use existing `etk_pitstop.py` style (no new dependencies, stdlib only). Functions snake_case. Constants ALL_CAPS at file top.
- Shell: POSIX `#!/bin/bash` shebang to match existing scripts (Rocknix has bash even though we constrain to BusyBox compatibility). Use `[ ]` not `[[ ]]`. Use `$(...)` not backticks. Always quote variable expansions.
- TSV writes: use `printf '%s\t%s\t...\n'` with explicit field count, never construct rows by string concatenation with manual `\t` insertions (typos slip in).
- Comments: explain *why*, not *what*. The code shows what.

---

## 19. Deliverable Checklist

When done, this should exist on the rig and pass the validation tests:

- [ ] `bin/etk_pitstop.py` refactored with TUNING + TELEMETRY tabs, L1/R1 + [/] switches tabs, state persists across switches
- [ ] `bin/session_postmortem.sh` created, BusyBox-compliant, < 2s runtime
- [ ] `scripts/career_aggregate.sh` created, recomputes per-game career file from sessions.tsv
- [ ] `config/crash_signatures.json` created with 4 seed signatures
- [ ] `scripts/env.sh` augmented with telemetry path exports and `telemetry_init_dirs` helper
- [ ] `install.sh` Sentry block augmented with session_start capture (IDLE→RUNNING) and session_postmortem invocation (RUNNING→IDLE), `PREV_STATE` line preserved
- [ ] `etk_pitstop.py` writes config_changes.tsv row on every successful `commit_and_verify()`
- [ ] Docs typo fixed: `Mesa Turnip 26.1.0` in AI_MANIFEST.md and README.md
- [ ] Duplicate `Frame Limit` line removed from `config_NPUB30457.yml`
- [ ] All five validation tests pass on the live rig

---

## 20. One Final Note On Honest Framing

This MVP intentionally avoids causal claims. The TELEMETRY tab shows **what happened**; it does not claim **what fixed what**. The deferred pit-engineer LLM layer will eventually add interpretation, but only with strict prompt constraints (cite specific sessions, hedge sample sizes, use correlation language not causation). For this build, the data itself is the value. A user reading their own ledger draws their own conclusions.

The career row's `clean_rate_pct` is a statistical metric, not a verdict. The session ledger preserves the record honestly — clean runs and crashes both, with their full context — so future analysis (manual or LLM-assisted) has solid ground to stand on.

The integrity of everything downstream depends on the ledger being honest. Build it that way.