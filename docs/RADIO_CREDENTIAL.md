# RADIO credential — qwen3.5 for the ETK pit-radio analyst role

**Homologation record · 2026-09-06 · authority: operator (mercurious) · evidence: `tools/radio/exam.py` + `exam_stability.py`, run `state/radio_exam/stability_20260907-002146`**

This is the ETK equivalent of a driver certification (§C.3 homologation): a dated record
that a component is fit for a named role, carrying the evidence and the limits. It
certifies an **open model, running for $0 on the operator's own Always-Free Ampere node
over ollama**, for the RADIO analyst role defined in `docs/RADIO_SPEC.md`. It is not a
claim that the model is good in general; it is a claim about one job, under named guards,
on stated evidence.

## Certified

- **Model:** `qwen3.5`, two rungs measured and both cleared: **`qwen3.5:9b`** (debrief) and
  **`qwen3.5:4b`** (interactive radio-check, and a legitimate single-model fallback).
- **Role:** the RADIO **analyst** — read a session's pack (ledger row, dyno arms, crash
  decode, log/dmesg windows, timeline, config) and produce a debrief: diagnosis, mechanism,
  evidence, and an A/B run sheet, in the pit-engineer voice.
- **Under the guards** of RADIO spec §6, load-bearing here: no crown below N, evidence
  beside every claim, schema-vocabulary only, and **diagnosis is not prescription**.
- **Cleared for:** building Phase 0/1 and running **Phase 2a** (operator-triggered
  debriefs) on the rig. The model drafts; the operator applies every atom-threshold action.

## NOT certified (the limits are the point)

- **Autonomous fixes.** The model's `next_action` for kit internals is not trusted: on the
  env-bomb case (F6) the 4b was stably wrong and the 9b flapped between an acceptable and a
  Law-#2-breaking fix. Such recommendations are `review_only` and never staged by LOAD FIX.
- **Shipping ahead of rules-only.** The §10 blind read (model vs a deterministic
  rules-only debrief) has **not** been run. Until it is, the model does not ship as the
  default over the rules-only fallback. This credential clears integration and
  operator-triggered use, not default-ship.
- **Live/messy packs.** The exam cases are clean and hand-built. A live-rig debrief on a
  real fresh session is still owed before the loop is trusted unattended.
- **One comprehension edge (Q13):** both rungs stably read "don't burn a call" as a
  reachability check rather than a tool/ssh command. Mild, but the doctrine prompt should
  state the sense explicitly.

## Evidence

**Exam** (`tools/radio/exam.py`): 15 comprehension questions (regex keys) + 6 forensics
cases whose verdicts the ledger already paid for and the model had never seen. The
self-test grades a known-good and known-bad answer for all 21 items and passes only if the
key fails the bad and passes the good: **21/21 discriminate.** Study packet 5,343 tokens,
sent as the system message, `num_ctx` 16384, temperature 0.1, thinking off.

**Stability** (`exam_stability.py`, 3 seeds 7/17/27, all runs re-graded with the final
keys — the kit's N≥3 rule applied to the model):

| Model | Comprehension (×3) | Forensics (×3) | Stable-pass | Flapped | Stable-fail |
|---|---|---|---|---|---|
| qwen3.5:4b | 14 / 14 / 14 | 5 / 5 / 5 | 19/21 | none | Q13, F6 |
| qwen3.5:9b | 14 / 14 / 14 | 5 / 6 / 5 | 19/21 | F6 | Q13 |

Every discipline and mechanism case (F2 crown-refusal, F3 career-pollution, F4
bake-session, F5 class-4 deadlock, F7 invisible-audio) passed on **all three seeds for
both rungs**, and `verdict_allowed` was correctly `false` on every forensic case in every
run. The properties RADIO relies on are stable, not lucky. The only instability is F6, the
gated fix.

**Comparison with the pre-prototype** (spec §10.1, dossier `RadioPreprototype…`): the same
model through generic chunk-retrieval RAG made confident, dangerous errors (4 MB read as
288 MB, requeue read as abort, a tool call read as a phone call, whole-file judgments off
fragments). The packet-plus-contract structure removed all of them. **The credential is as
much for the harness design as for the model.**

**Timing** (packet prefix-cached after the first call): warm debrief ~2 min on the 9b,
~1.5 min on the 4b; first call of a session pays cold prefill (~225 s on the 9b) and a
weight load if the keep-alive lapsed.

## Adjudication note (why the automated score is not the whole story)

Four grader keys were too strict and were caught by the human-read column, not the
self-test, then the stored answers were re-graded: Q10 (accept DRIVER-APPLY without the
literal `active_tune` token), F3 (accept "remove/recalculate"), F7 (negation-blind
"audio was fine"), and **F2 found during the stability run** — the key forbade "4–7×" and
so failed a 9b answer that quoted the operator's "4–7×" claim in order to refute it. All
four are the same class: a regex substring is blind to negating or quoting context. The
self-test only guards the author's own exemplars, so exam cases must carry negated and
quote-to-refute phrasings a real model produces. The eval keeps a human-read column for
exactly this reason; a clean automated score is necessary, not sufficient.

## Standing conditions

- Pinned to `qwen3.5` at the exam's corpus commit and the packet SHA in the run manifest.
  A model bump, a Modelfile change, or a doctrine edit re-opens the credential.
- The rules-only blind read is a **precondition** before the model ships ahead of the
  deterministic fallback.
- Re-sit the exam whenever the manual's cited sections (§0/§1.1/§2.1/§2.3/§2.4/§B.2/§B.3/§F)
  or `pitstop_fields.json` / `crash_signatures.json` change materially.

## Verdict

**qwen3.5 is credentialed as the RADIO analyst, both rungs, under the §6 guards, cleared to
integrate and to run operator-triggered on the rig.** Recommended: 9b for debriefs (richer,
its only instability confined to the gated fix), 4b for the interactive radio-check and as
the stable, faster fallback. The fix half stays with the operator by design, not by
accident. The remaining gate before default-ship is the rules-only blind read.
