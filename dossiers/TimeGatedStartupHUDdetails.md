# DOSSIER: Time-Gated HUD Launch Header (MODE + GAMEID for first 60s)

**Primary file:** `bin/mango_bridge.sh`
**Supporting edit:** `scripts/env.sh` (one constant, per IMMUTABLE LAW #2)
**Sentry change required:** NONE — it already seeds the clock this depends on.
**Status:** PROPOSAL — not implemented. Planning only.

---

## 0. SUMMARY

For the first 60 seconds of a game session the HUD strip shows a
`MODE|GAMEID|` header in front of the live telemetry; after 60s the header
silently drops and the strip collapses to pure instrumentation. The mode and
game ID are launch-time *confirmation* data (useful while watching the rig boot
through IDLE → vault-find → running), not steady-state *telemetry*, so they earn
their screen space only during the launch window and then free it for the 99% of
the session that is actual racing. No animation, no second timer, no new state —
it reuses the session clock the Sentry already writes.

---

## 1. CURRENT STATE (GROUND TRUTH)

- `bin/mango_bridge.sh` runs a 1 Hz `while true; … sleep 1` loop, re-sourcing
  `env.sh` each tick, and writes the HUD string with the atomic
  `echo > "${LIVE_STAT}.tmp" && mv "${LIVE_STAT}.tmp" "$LIVE_STAT"` pattern.
- The string is consumed by MangoHud via `custom_text=ETK` + `exec=cat …live_stat.txt`.
  The `ETK:` prefix was removed from the bridge string; `custom_text=ETK` now
  supplies the persistent field label. **That label stays — it is not part of
  this change.**
- MODE (`$ETK_BUILD_TYPE`) and GAMEID (`$TARGET_ID`) are currently emitted as
  permanent leading fields of the telemetry body (kept on-strip for dev
  debugging). This dossier moves *only those two fields* into the time-gated
  header; the telemetry body (temp | load | ram | vault) is unchanged.
- The session clock already exists: the Sentry's IDLE→RUNNING block writes
  `date +%s > "$SHM_DIR/session_start.txt"` at ignition (it is consumed by
  `session_postmortem.sh`). **Reuse it. Do not invent a second timer.**

---

## 2. SCOPE & BLAST RADIUS

One behavioral file (`mango_bridge.sh`) plus one constant in `env.sh`. No Sentry
edit, no SHM schema change, no new file, no new daemon. The header is a pure
string prefix computed inside the existing loop; if the clock is unavailable the
strip simply runs in its compact form (see §5). It does not touch thermal, vault,
the symlink, or the IPC backbone.

---

## 3. THE CHANGE

### 3.1 `scripts/env.sh` — add the window constant
```sh
# Seconds the HUD shows the MODE|GAMEID launch header before collapsing to
# pure telemetry. Sourced by mango_bridge.sh.
HUD_HEADER_HOLD_S=60
```

### 3.2 `bin/mango_bridge.sh` — compute the header, prepend it
Inside the loop, after `source …/env.sh` and after the existing metric
computation, before the `FINAL_STRING` assignment:

```sh
# --- TIME-GATED LAUNCH HEADER ---
# MODE|GAMEID| only for the first $HUD_HEADER_HOLD_S of a session, then "".
# Clock is the Sentry's ignition-seeded session_start.txt — no second timer.
HEADER=""
S_START=$(cat "$SHM_DIR/session_start.txt" 2>/dev/null)
case "$S_START" in ''|*[!0-9]*) S_START=0 ;; esac
if [ "$S_START" -gt 0 ]; then
    AGE=$(( $(date +%s) - S_START ))
    if [ "$AGE" -ge 0 ] && [ "$AGE" -lt "${HUD_HEADER_HOLD_S:-60}" ] \
       && [ "$TARGET_ID" != "IDLE" ]; then
        HEADER="${ETK_BUILD_TYPE}|${TARGET_ID}|"
    fi
fi
```

Then change the string assignment from (current shape):
```sh
FINAL_STRING="${ETK_BUILD_TYPE}|${TARGET_ID}|${T_STAT}|...|${VAULT_STR}|"
```
to:
```sh
FINAL_STRING="${HEADER}${T_STAT}|...|${VAULT_STR}|"
```

i.e. the only edit to the body line is deleting the now-conditional
`${ETK_BUILD_TYPE}|${TARGET_ID}|` from the front and replacing it with
`${HEADER}`. Everything from `${T_STAT}` onward is untouched.

**Result:** first 60s → `ETK FULL|NPUA80075|49°|2.0|58%|446M 50k +0`; after →
`ETK 49°|2.0|58%|446M 50k +0` (the `ETK` is the persistent `custom_text` label).

---

## 4. WHY THE TIMING IS SAFE (DESIGN NOTE)

The bridge is a free-running watchdog — the Sentry keeps it alive every tick
regardless of game state, so it can tick *before* `session_start.txt` exists. Two
properties make that fine:

1. **Fallback collapses to compact, never to garbage.** Missing/non-numeric
   `session_start.txt` → `S_START=0` → header stays empty. The failure mode is
   "no launch header," never a broken or stale strip. The guard leans into this.
2. **The header can't show a stale/fallback ID.** The Sentry seeds
   `session_start.txt` only *after* its 4-second env-populate wait and *after*
   committing the resolved ID to `$ID_FILE`. So the same event that makes `AGE`
   computable also guarantees `$TARGET_ID` (derived from `$ID_FILE`) is already
   the real title. The header therefore appears a few seconds into the RPCS3
   splash with a correct ID and persists to the 60s mark — exactly the intended
   behavior. The extra `[ "$TARGET_ID" != "IDLE" ]` guard is belt-and-suspenders.

---

## 5. INVARIANTS (DO NOT VIOLATE)

1. **Reuse `session_start.txt`** — do not add a second clock or new SHM key.
2. **BusyBox-safe** — POSIX `sh` only (the snippet above is: `case`, `$(( ))`,
   `[ ]`, `date +%s`). No `--long-opts`, no `bc`.
3. **Atomic write unchanged** — keep the existing `echo > tmp && mv` for
   `$LIVE_STAT`. The header is built into the same single write.
4. **Telemetry body is untouched** — only the leading MODE|GAMEID fields move.
   The `custom_text=ETK` label is out of scope and stays.
5. **Best-effort, never blocking** — a clock read failure must not stall or skip
   the tick; it just yields an empty header.
6. **Window lives in `env.sh`** — the 60 is `HUD_HEADER_HOLD_S`, not a magic
   number in the bridge.

---

## 6. TEST PLAN

- **Test 1 — Launch window:** cold-launch a game; for ~0–60s of session age the
  strip carries `MODE|GAMEID|`; after 60s it's gone and gauges slide left. (Header
  first appears a few seconds in, once `session_start.txt` lands — expected.)
- **Test 2 — Steady state:** past 60s, confirm the strip is exactly the telemetry
  body with no residual delimiters or leading `|`.
- **Test 3 — Relaunch after crash:** game crashes and re-ignites; header reappears
  for a fresh 60s (new `session_start.txt`) — desired re-confirmation.
- **Test 4 — Clock missing:** delete `session_start.txt` mid-run; strip degrades
  to compact (no header), no errors, telemetry intact.
- **Test 5 — Idle exposure:** exit a game; confirm the header doesn't linger
  objectionably on the idle HUD (the IDLE guard + the fact that idle gaps quickly
  exceed 60s should suppress it — verify it feels right on-device).
- **Test 6 — No flicker:** confirm the atomic write still prevents MangoHud from
  reading a half-built string at the moment the header drops.

---

## 7. COMPOSITION NOTE

This change is **orthogonal** to the other pending `mango_bridge.sh` edits
(status-symbol severity ramp, shader abbreviator, `°C` trim) and to the
LiberationMono `font_file` line. The header *prepends*; the others modify field
*contents*. They can land in any order and merge cleanly into the same loop. When
the consolidated changeset is assembled, this is one stanza added near the top of
the loop body plus the one-field edit to the `FINAL_STRING` line.

---

## 8. VERIFY ON-DEVICE

1. Confirm `session_start.txt` is present and fresh within the first few seconds
   of a real launch (it should be, post the Sentry's 4s wait).
2. Confirm `$TARGET_ID` resolves to the real title (not the `NPUA80075` fallback)
   during the header window — if a fallback ever leaks in, tighten the guard to a
   `^[A-Z]{4}[0-9]{5}$` shape check mirroring `etk_pitstop.sh`.
3. Eyeball the 60s drop: if a one-frame disappearance feels abrupt, an optional
   two-stage ramp (`MODE|GAMEID|` 0–45s → `GAMEID|` 45–60s → none) is a few extra
   lines; only add if the hard pop bothers you.

---

*CLAUDE IMMUTABLE NOTE: The session clock is the Sentry's, not the bridge's. Do
not seed, reset, or delete `session_start.txt` from `mango_bridge.sh` — the bridge
is a read-only consumer of it. Writing it from here would corrupt
`session_postmortem.sh`'s duration math.*