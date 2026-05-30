# Proposal: TELEMETRY Session Detail View (v0.1.3 feature)

**Status:** PROPOSED — design only, no code. Greenlight before implementing.
**Scope (per operator, 2026-05-30):** **Data-viz + suggested fix.** ASCII gauges from the session row + `crash_signatures.json` `summary`/`explanation`/`suggested_changes` for crashes. **No AI narrative** (the TO_DO "AI-Synthesized detail" idea is explicitly deferred — out of scope here).
**Origin:** No prior dossier proposed this. The telemetry MVP (`BuildSimpleTelemetry.md:407`) explicitly ruled a drill-down detail view *out of scope* ("reading the data is the whole feature"). This proposal reverses that deliberately for v0.1.3.

---

## 1. The feature
In the TELEMETRY tab, **select a session row and press CONFIRM** to open a full-screen detail subscreen visualizing that one session. **B** returns to the table. The detail adapts to outcome:
- **CLEAN / ABORTED** → a "clean run card": duration, shaders harvested, and ASCII gauges for thermal / load / RAM / battery drain.
- **RECOVERY / crash** → a "crash card": the human-readable `summary` + `explanation` from `crash_signatures.json`, where it died (`fence_at_crash`), and the **suggested fix** (`suggested_changes`: "try `Driver Wake-Up Delay = 50`").

This turns the ledger from a glanceable log into something you can interrogate per-session — exactly the "why did *this* run crash / how clean was *this* lap" question.

## 2. Prerequisite: row selection (the real lift)
Today the TELEMETRY tab is **scroll-only** — `state["telemetry_scroll"]` is an offset into the `merged` list (sessions + config events, newest-first); there is **no selected row**. CONFIRM/A currently triggers `_refresh_telemetry_caches`.

Changes:
- Add `state["telemetry_cursor"]` — an **absolute index into `merged`**, with `telemetry_scroll` *derived* to keep the cursor on-screen. **Reuse the exact pattern already in `draw_tools` `uninstall_list`** (cursor + `off = min(max(0, cur - cap//2), n - cap)`), so this is proven code, not new invention.
- D-pad up/down (and arrows) move the **cursor** (not raw scroll); the visible window follows.
- Highlight the cursor row (reverse video / `color_pair(1)`, same idiom as TUNING/TOOLS).
- **Repurpose CONFIRM** → open detail for the selected row. Manual refresh is safe to drop from CONFIRM because entering the tab already invalidates the caches in `_switch_tab` (auto-refresh on entry); keep `r`/`R` on keyboard as the explicit refresh. *(Decision to confirm: whether to retain a pad refresh binding — proposal says no, rely on tab re-entry.)*
- **Config-change rows are not sessions** → they're skippable for detail (cursor may land on them and CONFIRM is a no-op, or we skip them on cursor movement). Recommend: cursor can rest on them, CONFIRM shows a minimal "config change" card (what dial moved). Low cost, keeps navigation uniform.

## 3. Sub-mode state machine
Mirror the TOOLS tab's `tools_mode` idiom: add `state["telemetry_mode"]` ∈ `{"table", "detail"}`.
- `table` (default): current scrollable list + cursor.
- `detail`: full-screen card for `merged[telemetry_cursor]`.
- CONFIRM (table→detail), B (detail→table). Tab-switch keys `[`/`]` still work from either (return to table first).

## 4. Data model — everything needed already exists
**Session row** (`sessions.tsv`, 14 cols): `epoch, duration_s, build, game_id, status, peak_cpu_pct, peak_ram_mb, peak_temp, avg_temp, crash_sig, fence_at_crash, shaders_harvested, drain_pct, thermal_overrides`. The detail view is a pure render of these — no new capture, no schema change.

**Crash enrichment** (`config/crash_signatures.json`, keyed by `id`): `label, severity, summary, explanation, suggested_changes[{yaml_key, new_value}]`. Look up by the row's `crash_sig`.
- `GPU_FENCE_TIMEOUT`, `OOM_KILL`, etc. → full match (summary + explanation + suggested fix).
- `R3_PANIC`, `PANIC_REBOOT`, empty → **no signature entry**: degrade gracefully (show the raw status + "manual recovery / kernel panic — no signature data"). The renderer must never assume a match.

## 5. The two cards (ASCII data-viz — curses, no graphics)
Visualization = the same DDU/HUD spirit as the in-game strip: labeled ASCII bar gauges, scaled to sane maxes.

**CLEAN / ABORTED card:**
```
  GT5P · NPUA80075        2026-05-26 17:45        CLEAN
  ───────────────────────────────────────────────────
  Duration   ████████████░░░░░░  7m 43s
  Shaders +  ██████████████████  138        (vault delta this run)
  ───────────────────────────────────────────────────
  Temp   peak 81°C  avg 74°C   ███████████████░░  (max 90)
  Load   peak 9.1   avg 6.7    ██████████░░░░░░░  (max 16)
  RAM    peak 4.8GB            ████████████░░░░░  (max 8)
  Drain  -5%                   ███░░░░░░░░░░░░░░░
  Thermal overrides: 0
```

**Crash / RECOVERY card** (enriched from crash_signatures.json):
```
  GT5P · NPUA80075        2026-05-26 14:25        RECOVERY: Adreno   [HIGH]
  ───────────────────────────────────────────────────
  GPU stalled waiting on render completion
  The Adreno driver gave up waiting for the GPU to finish a frame.
  Often caused by per-frame complexity exceeding the watchdog window.
  ───────────────────────────────────────────────────
  Died at fence:  33173            Ran:  6m 32s        Temp peak: 81°C
  ───────────────────────────────────────────────────
  SUGGESTED FIX (TUNING tab):
    • Driver Wake-Up Delay → 50
    • Resolution Scale     → 75
```
Gauges reuse the bar helper; bar max constants live next to the existing `_TEL_W_*` column constants. ASCII-only (manifest rule for non-Pitstop panes doesn't bind here, but staying ASCII keeps it theme-proof).

## 6. Files touched (estimate)
| File | Change |
|---|---|
| `bin/etk_pitstop.py` | `telemetry_cursor` + cursor-follows-scroll (reuse uninstall-list math); highlight selected row in `draw_telemetry`; `telemetry_mode` state machine; new `draw_session_detail()`; crash_signatures.json loader (cache once); repurpose CONFIRM; footer hint update |
| `config/crash_signatures.json` | none (already carries summary/explanation/suggested_changes) |
| `CHANGELOG.md` | v0.1.3 feature entry |
| `README.md` | TELEMETRY controls: "DPAD select · CONFIRM detail · B back" |

No Sentry / ledger / schema changes. Pure read-side UI.

## 7. Test plan
1. **Selection:** cursor moves, highlights, window follows past a screen of rows; clamps at both ends.
2. **CLEAN detail:** open a real CLEAN row → gauges render, shaders/duration correct, returns on B.
3. **Crash detail:** open the RR7 `GPU_FENCE_TIMEOUT` RECOVERY row → summary/explanation/suggested fix from JSON; `fence_at_crash` shown.
4. **Graceful degrade:** open an `R3_PANIC` / empty-`crash_sig` row → no crash on missing signature; sane fallback text.
5. **Config row:** cursor on a config-change row → CONFIRM shows minimal card or no-ops cleanly.
6. **Small terminal:** detail card clips with `try/except curses.error` like the rest; no crash on narrow/short screens.
7. **Tab integrity:** `[`/`]` from detail returns cleanly; re-entry still auto-refreshes.

## 8. Out of scope (this dossier)
- AI-synthesized narrative paragraph (TO_DO item) — deferred.
- Applying suggested changes from the detail view (read-only surfacing only; editing stays in TUNING).
- Sparkline *time-series* within a session (we only persist peak/avg, not per-tick history) — gauges show peak/avg, not a trace. A future capture change could add intra-session sampling.

## 9. Open decisions for greenlight
- **a.** Drop the pad CONFIRM-refresh entirely (rely on tab-entry auto-refresh)? (Proposal: yes.)
- **b.** Let the cursor rest on config-change rows with a minimal card, or skip them on movement? (Proposal: rest + minimal card.)
- **c.** Gauge maxes — fixed sane caps (temp 90, load 16, RAM 8GB) vs per-device profile values? (Proposal: pull from `SM8250.sh` where available, fixed fallback.)
