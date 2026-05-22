# ETK Simple Telemetry — Round 2 Fix Dossier

**Target executor:** Claude Code with full repo access
**Context:** The Simple Telemetry layer shipped and ran. A real evening of testing (34 sessions, High Speed Loop → Daytona → Eiger TT) produced a populated `sessions.tsv`. Reading that real data surfaced bugs and design weaknesses that only show up against live output. This dossier is the fix pass.

**Scope discipline:** Same as before. Fix what's listed. Do not add deferred features. Do not refactor adjacent code beyond what's specified.

---

## 0. Read First

1. `bin/session_postmortem.sh` — most fixes land here
2. `bin/etk_pitstop.py` — TELEMETRY tab render fixes here
3. `bin/thermal_d.sh` — one grep tightening + one optional sampling addition
4. `scripts/career_aggregate.sh` — verify game-name resolution
5. The current `sessions.tsv` on the rig — the evidence behind every fix below

---

## 1. What The Live Data Proved

Before the fixes, the findings that justify them — all from the real 34-row ledger:

- **The system fundamentally works.** Crashes classified correctly (every real crash = `RECOVERY:Adreno / GPU_FENCE_TIMEOUT`, matching the known Adreno hangcheck reality). Career rollup math verified correct (27 eligible sessions, 6 clean, 22% — the ≥60s gate correctly excluded 6 sub-minute aborts).
- **`peak_cpu_pct` is dead weight as sourced:** **100 on 26 of 28** real sessions. Saturation percent carries zero diagnostic information. (Bug #3)
- **`peak_temp`/`avg_temp` were 0 for early rows** then populated once thermal sampling warmed up — confirming the SHM-seed dependency. (Bug #2 territory)
- **`shaders_harvested` = 0 on all 34 rows is EXPECTED and correct** — the GT5P vault is saturated (~49k shaders); these well-trodden tracks compile no new shaders. Not a bug, not investigated.
- **`fence_at_crash=123` repeats on 5 rows** — classic dmesg stale-fault bleed on sessions where the `session_start.txt` seed was missing, so windowing fell back to the 10-minute net. Real fence values (50775, 87010, 34830…) only appear once durations populate. (Bug #1)
- **RAM is the live discriminator.** Eiger cleans sat ~6.1GB; Eiger crashes climbed to 6.6–7.0GB. Same config all evening. RAM headroom, not temp, separates clean from crash.
- **The ledger needs human-skepticism guards.** A 16s "CLEAN" was a force-quit abort. An audio-backend CONFIG row was a fat-finger on the first menu item, netted to nothing. Both are noise the UI currently presents as signal. (Features #7, #8)

---

## 2. BUG FIXES (priority order)

### Bug #1 — Stale dmesg fence bleed on sessions with no start-seed
**Severity: high** (corrupts `fence_at_crash` and can misclassify CLEAN as crash)

**Symptom:** Rows 1–6 show `fence_at_crash=123`/`272`/`433` with `duration_s=0`. The `123` value repeats across 5 rows — it's a single stale fault sitting in the kernel ring buffer being re-read every session.

**Root cause:** `session_postmortem.sh` windows the dmesg scan by `DMESG_WINDOW_START = START_EPOCH - BOOT_EPOCH`. When `$SHM_DIR/session_start.txt` is missing (Sentry seed didn't fire, or RPCS3.log mtime fallback also failed), `START_EPOCH=0`, and the code falls back to `UPTIME_SEC - 600` — a 10-minute net that re-admits stale faults.

**Fix:**
1. **Make the absence explicit, not silently degraded.** When `START_EPOCH=0` (no reliable anchor), set a flag `ANCHOR_RELIABLE=0`. Still compute a best-effort window, but:
   - Write a sentinel into `crash_sig` or a new column note (see Feature #9) so the UI can show the row is low-confidence, OR
   - At minimum, when `ANCHOR_RELIABLE=0` AND `DURATION=0`, do **not** populate `fence_at_crash` from dmesg at all — set it to `0`. A zero-duration session has no trustworthy fault attribution. Better an honest 0 than a stale 123.
2. **Verify the Sentry actually seeds `session_start.txt` on IDLE→RUNNING.** The early 0-duration rows suggest the seed wasn't firing reliably at evening start. Confirm the install.sh Sentry edit writes it BEFORE the `PREV_STATE` line and that it survives the 4-second post-RUNNING settle. If the seed is racing the daemon spawn, move it earlier in the transition block.
3. **Defensive: dedup-detect.** If `fence_at_crash` equals the value from the immediately prior session row AND that prior row was also a crash, it's almost certainly a stale re-read — flag or zero it. (Optional, but cheap insurance.)

---

### Bug #2 — Thermal columns silently zero when SHM seed missing
**Severity: medium** (loses thermal data, but degrades honestly to `----`)

**Symptom:** `peak_temp`/`avg_temp` = 0 on early rows, populated later.

**Root cause:** `session_postmortem.sh` reads `$SHM_DIR/thermal_log_start.txt` for the line-count snapshot. This seed was NOT in the original dossier §10 (only `session_start.txt` and `battery_start.txt` were). If the Sentry doesn't write `thermal_log_start.txt` at IDLE→RUNNING, `START_LINE=0`, and `NEW_LINES` becomes the entire telemetry.log — which then gets windowed wrong or produces stale averages.

**Fix:**
1. Add to the Sentry's IDLE→RUNNING block (alongside the other two seeds):
   ```bash
   wc -l < "$ETK_ROOT/telemetry.log" 2>/dev/null > "$SHM_DIR/thermal_log_start.txt" || echo 0 > "$SHM_DIR/thermal_log_start.txt"
   ```
2. Confirm `thermal_d.sh`'s telemetry.log path (`$ETK_ROOT/telemetry.log`) matches what the post-mortem reads. (It does in the current code — just lock it.)
3. This is now a documented three-seed requirement: `session_start.txt`, `battery_start.txt`, `thermal_log_start.txt`. Update the dossier/comments so the next person doesn't miss it again.

---

### Bug #3 — `peak_cpu_pct` is the wrong metric (saturation %, not load)
**Severity: medium** (column is dead weight; misleads the operator)

**Symptom:** 26 of 28 real sessions show exactly `100`. The column distinguishes nothing.

**Root cause:** Post-mortem parses `CPU Usage: Total: NN%` from RPCS3.log. On a 6-core SM8250 running RPCS3, total CPU saturates to ~100% instantly and stays pinned. The metric the operator actually reasoned with throughout development was **loadavg** (values like 5.10, 8.43 — unbounded, and genuinely discriminating).

**Fix:**
1. **Capture peak loadavg instead.** The cleanest source is `/proc/loadavg` field 1 (1-minute load average), sampled into the existing thermal tick the same way `TEMP` is. Add to `thermal_d.sh`'s throttled sample line:
   ```bash
   LOADAVG=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
   echo "$(date +%s) SAMPLE $TEMP $LOADAVG" >> "$ETK_ROOT/telemetry.log"
   ```
   (Appends one field to the existing SAMPLE line — no new write, no new daemon.)
2. **Parse peak loadavg in post-mortem** from the windowed SAMPLE lines (4th field), same awk-max pattern already used for temp.
3. **Rename the ledger column** `peak_cpu_pct` → `peak_load`. Store as a float (e.g. `8.4`). Update:
   - The header row in `session_postmortem.sh`
   - The TSV field order (keep position 6 to avoid reshuffling downstream parsers — just change the name and the value source)
   - The `load_sessions_ledger()` dict key in `etk_pitstop.py` (`peak_cpu_pct` → `peak_load`, parse as float not int)
   - `career_aggregate.sh` if it references the field (it shouldn't)
4. **Keep `peak_cpu_pct` retired, don't dual-write.** One honest moving number beats two columns where one is always 100.

**Migration note:** existing rows have an int 0–100 in that column. After the change, old rows will show e.g. `100` under a `peak_load` header, which reads as load 100.0 — nonsensical but harmless and clearly historical. Acceptable. Do NOT rewrite history.

---

### Bug #4 — Duplicate `_draw_config_row` definition (dead code + overflow risk)
**Severity: medium** (the safe version is shadowed by the unsafe one)

**Symptom:** Two `def _draw_config_row(...)` exist in `etk_pitstop.py`. Python keeps the **last** definition. The first (overflow-safe: single clipped write + `try/except curses.error` + `[:w-4]` guards) is dead. The second uses bare `stdscr.addstr(y, 11, body, attr)` with only a manual truncate and no `curses.error` guard.

**Fix:**
1. Delete the **second** `_draw_config_row` definition entirely.
2. Keep the **first** (the one with the clipped base-write + colored overlay + `try/except curses.error` pattern matching `_draw_session_row`).
3. Verify no other duplicate-def shadowing exists in the file (grep `^def ` for dupes).

---

## 3. THERMAL DAEMON FIXES

### Fix #6 — Tighten the override grep
**Severity: low** (fragile, currently accidentally-correct)

**Symptom:** `THERMAL_OVERRIDES=$(echo "$WINDOW" | grep -c "PIT")` matches the substring `PIT` anywhere. Today only the override line contains it (`Switched to PIT at`), so it's right by accident.

**Fix:** Change to `grep -c "THERMAL OVERRIDE"` — matches the actual log idiom unambiguously and won't inflate if a future log line happens to contain the letters P-I-T.

**Also (consistency, optional):** the SAMPLE line uses `$(date +%s)` (epoch) but the OVERRIDE line uses `$(date)` (wall clock) in the same file. Line-count windowing doesn't care today, but make both epoch-prefixed now so the file is internally consistent and future timestamp-based windowing won't break. Cheap insurance.

---

## 4. FEATURE / UX FIXES

### Feature #7 — Force-quit aborts should be classified, not silently gated
**Severity: medium** (a force-quit abort logged as `CLEAN` is misleading even if the ≥60s gate excludes it from career stats)

**Context:** The 16s row was a Rocknix force-quit (SELECT+START+L1) before GT5P finished launching. It logged as `CLEAN` because no crash signature matched and no reboot happened. The ≥60s gate correctly keeps it out of career math, but it still *renders* as a green CLEAN row in the session table, which misleads at a glance.

**Fix:**
1. Add a status: **`ABORTED`** for sessions where `duration_s < 60` AND status would otherwise be `CLEAN`. (A sub-60s crash keeps its `RECOVERY:*` — those are real, just short.)
2. `session_postmortem.sh`: after status classification, if `STATUS="CLEAN"` and `DURATION < 60`, set `STATUS="ABORTED"`.
3. TELEMETRY tab: render `ABORTED` dimmed/grey (neither green nor red), no `*` mark. It's a non-event.
4. `career_aggregate.sh`: `ABORTED` is already excluded by the ≥60s gate, but make it explicit — exclude `ABORTED` by status name too, so the rule is legible and doesn't depend solely on the duration coincidence.

**Lock the threshold language:** per the manifest update, the 60s figure is a documented policy parameter, not magic. Keep it in env.sh as `TELEMETRY_MIN_SESSION_S=60` so it's tunable and visible rather than hardcoded in two scripts.

---

### Feature #8 — Guard the accidental audio-backend toggle
**Severity: low** (UX papercut that pollutes the CONFIG ledger)

**Context:** Audio Renderer Backend is the first item in the TUNING matrix, so it's the easiest field to change by accident when the app opens. The ledger shows a `Cubeb→ALSA→Cubeb` round-trip at 5:37pm that netted to nothing but logged two CONFIG rows.

**Fix (pick one, prefer A):**
- **A — Net-zero suppression:** `_diff_matrix()` already compares against `original_render` baseline. The round-trip *did* net to zero by the time of save — confirm that a field edited away and back to its original value produces NO diff row. If the two ledger rows came from two separate saves, this won't help; if from rapid re-toggling within one session, baseline-diff already suppresses it. Verify which.
- **B — Reorder the matrix:** move Audio Renderer Backend out of position 0 in `pitstop_fields.json` so the cursor doesn't land on the most-disruptive field at open. Put a benign read-mostly field first.
- **C — Confirm-on-change for renderer-class fields:** overkill for this scope; skip unless A and B both prove insufficient.

Recommend **A then B**: confirm net-zero suppression works, and reorder the schema so the default cursor doesn't sit on a footgun.

---

### Feature #9 — Low-confidence row marking
**Severity: low** (honesty feature, ties off Bug #1)

**Context:** Rows where the session anchor was unreliable (no `session_start.txt`, `duration_s=0`) carry untrustworthy fence/thermal data. The operator should be able to see "don't trust this row" at a glance.

**Fix:**
1. When `ANCHOR_RELIABLE=0` (from Bug #1 fix), the post-mortem renders the row's dynamic metrics as honest zeros (already specified) — but additionally, the TELEMETRY tab should mark these rows. Simplest: if a row has `duration_s=0`, render it dimmed with a `?` mark instead of a status color, the way `ABORTED` gets grey treatment.
2. Do NOT add a new ledger column for this if avoidable — `duration_s=0` is already a sufficient proxy. Derive the low-confidence treatment at render time from `duration_s==0`.

---

### Feature #10 — Column re-prioritization in TELEMETRY tab
**Severity: medium** (the columns that matter are cramped; the dead one had a labeled slot)

**Context:** The live data proved RAM is the discriminator on Eiger and LOAD (CPU%) was useless. The current layout gives the useless metric a labeled column and crams RAM.

**Fix:**
1. Header must be generated from the **same width constants** the row builder uses, not hand-spaced. The current header drifts from the data columns (the original #2 observation). Define column widths once as constants; build both header and rows from them.
2. New column priority (left to right), within the ~74-col budget:
   `TIME · STATUS · DUR · RAM(GB) · LOAD · TEMP · DRAIN · SHD`
   - **RAM promoted** to a prominent labeled column, rendered in GB to one decimal (`6.1G`, `7.0G`) since that's the live variable.
   - **LOAD** now shows peak loadavg (from Bug #3), e.g. `8.4` — a moving number worth a column again.
   - **TEMP** stays as `avg/peak` (`70/77°`).
   - **SHD** demoted to rightmost (it's all-zero on saturated vaults; lowest information density).
3. Keep the single-write + clipped-overlay render pattern for overflow safety (the `_draw_session_row` approach). Update the column-start offset for the colored STATUS overlay if column order shifts.

---

## 5. NON-CHANGES (explicitly leave alone)

- The ≥60s career eligibility gate logic is **correct** — verified against live data. Only make the threshold a named env var (Feature #7); don't change the value or the gate behavior.
- The crash signature classification is **working** — every real Adreno crash matched. Do not retune the patterns.
- The career rollup math is **correct** — do not touch `career_aggregate.sh`'s aggregation beyond the `ABORTED` exclusion clarity (Feature #7.4) and confirming it doesn't reference the renamed load field.
- `game_name` resolution currently falls back to the ID (`game_name=NPUA80075` in the career file) because `game_names.json` doesn't exist yet. That's the specified graceful fallback. Leave it — do not build the names file in this pass.
- Do NOT build any of the §17 deferred features from the original dossier (preset garage, adaptive schema, whisper-network, wear tracking, pit_engineer_d.py, etc.).

---

## 6. VALIDATION (after fixes)

1. **Seed reliability:** launch + immediately force-quit. Confirm `session_start.txt`, `battery_start.txt`, `thermal_log_start.txt` all exist in SHM during the run. Confirm the resulting row is `ABORTED`, dimmed, with `fence_at_crash=0` (not a stale value).
2. **Stale-fence fix:** force a crash, recover, then do a clean run. Confirm the clean run does NOT inherit the prior crash's fence value and is classified `CLEAN` not `RECOVERY:Adreno`.
3. **Loadavg capture:** run a real session. Confirm `peak_load` shows a believable loadavg (>1.0, likely 5–9), not `100`.
4. **Column alignment:** open TELEMETRY with real rows. Confirm header labels sit directly above their data columns with no drift. Confirm RAM renders in GB and is prominently placed.
5. **Config ledger:** toggle audio backend and back within one session without saving between. Confirm no CONFIG row is written (net-zero). Confirm the schema's default cursor no longer lands on the audio field (if Feature #8B applied).
6. **Override count:** if a thermal override fires, confirm `thermal_overrides` increments via the tightened `THERMAL OVERRIDE` grep.

---

## 7. Summary Of Files Touched

| File | Changes |
|---|---|
| `bin/session_postmortem.sh` | Bug #1 (anchor-reliable guard, honest-zero fence), #2 (thermal seed read), #3 (parse peak_load from SAMPLE 4th field, rename column), Feature #7 (ABORTED status) |
| `bin/thermal_d.sh` | Bug #3 (append loadavg to SAMPLE line), Fix #6 (tighten override grep, epoch-prefix override line) |
| `bin/etk_pitstop.py` | Bug #4 (delete duplicate `_draw_config_row`), #3 (rename dict key, parse float), Feature #7 (ABORTED render), #9 (low-confidence dim+`?`), #10 (column reorder + width-constant header) |
| `install.sh` | Bug #1/#2 (confirm/add three SHM seeds in Sentry IDLE→RUNNING) |
| `scripts/career_aggregate.sh` | Feature #7 (explicit ABORTED exclusion); confirm no reference to renamed load field |
| `scripts/env.sh` | Feature #7 (`TELEMETRY_MIN_SESSION_S=60`) |
| `config/pitstop_fields.json` | Feature #8B (reorder so audio backend isn't position 0) |

---

## 8. One Closing Note On Honesty

The whole reason these fixes exist is that the ledger told a slightly dishonest story on first read — a force-quit looked clean, an accidental toggle looked like tuning, a stale fence looked like a fresh fault. None of those were the system lying; they were the system recording literally what it saw without the human context. The fixes above don't add intelligence — they add **honest hedging**: ABORTED instead of fake-CLEAN, zeroed-fence instead of stale-fence, dimmed low-confidence rows, and the one moving CPU metric instead of a pinned-100 one.

That's the right direction for a durability tool. The ledger should under-claim, not over-claim. A row that says "I'm not sure about this one" is worth more than a row that confidently reports a stale number.