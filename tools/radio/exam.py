#!/usr/bin/env python3
"""ETK EXAM — a standard test for an open model that wants a seat on the pit radio.

RADIO spec §10 (docs/RADIO_SPEC.md): before any model advises the operator, it sits the
same exam. Three parts, one byte-identical study packet, deterministic grading:

  STUDY PACKET  the manual's doctrine sections (§1.1, §1.8, §2.1, the ledger + attribution
                part of §2.3, §2.4, §B.2, §B.3, §F) and two code excerpts, assembled from
                the repo at run time and sent as the SYSTEM message of every call — so the
                model pays the prefill once per session (Ollama's prefix cache) exactly as
                the RADIO doctrine prompt will.
  SECTION A     comprehension: short-answer questions graded by regex keys
                (tools/radio/exam/questions.json). Three of them are yesterday's real
                failures (4 MB read as 288 MB, requeue read as abort, a tool call read as
                a phone call), so a model that repeats them is caught.
  SECTION B     forensics: real cases from the ledger whose verdict the operator already
                paid for and the model has never seen (tools/radio/exam/cases/*.json).
                The model answers as JSON; grading is must-say / must-not-say properties
                plus a verdict_allowed check; a one-line rubric is kept for the human read.

Every case and question carries an `exemplar` (a known-good answer) and a `counter` (a
known-bad one); `--selftest` runs the grader over both, so a key that would pass a wrong
answer or fail a right one is caught before a model ever sees it. That is the kit's rule
for tests: they must discriminate.

Usage (host, stdlib only; the model is reached over an Ollama endpoint):
    ssh -N -L 11434:127.0.0.1:11434 <node>            # the operator's tunnel, in its own window
    python3 tools/radio/exam.py --selftest
    python3 tools/radio/exam.py --dry-run             # packet size, prompts, no calls
    python3 tools/radio/exam.py --models qwen3.5:4b qwen3.5:9b
    python3 tools/radio/exam.py --report              # rebuild the scorecard from the JSONL

Results land under state/radio_exam/<run>/ (gitignored, like every ledger mirror):
one JSONL per model with the full prompt hash, usage, raw answer and grade per call —
resumable (a completed call is never repeated) — and scorecard.md.

Numbers come from Ollama's own timing fields; nothing here is estimated except the
pre-call token estimate used to flag a truncated prompt (RADIO guard: never truncate
silently — the exam asks for a context the packet fits in and checks afterwards).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
EXAM_DIR = os.path.join(HERE, "exam")
MANUAL = os.path.join(ROOT, "TRACK_MANUAL.md")
OUT_BASE = os.path.join(ROOT, "state", "radio_exam")

CHARS_PER_TOKEN = 3.6          # conservative for English prose + code on the Qwen tokenizer
NS = 1e9

EXAM_RULES = """You are sitting the ETK EXAM. ETK is the Emulation Tuning Kit: a Retroid Pocket Flip 2
handheld (Snapdragon SM8250, Adreno 650) running ROCKNIX Linux with RPCS3 and a Mesa
Turnip driver, all forked and tuned to run PS3 Gran Turismo titles. You are being tested
as a candidate race engineer for its pit radio.

Rules of the exam:
- Answer ONLY from the study packet below and from the evidence given in each question.
- Never invent a number, a file name, a setting or an incident. If the packet does not say,
  say so.
- Every claim about performance or stability must carry its N (how many runs) when the
  evidence gives one, and you may not declare a knob or fix validated below N=3 per arm.
- Bake sessions (many new shaders compiled), ABORTED rows and attract-mode runs are not
  evidence for frame rate or feel.
- Lowering resolution is never a lever toward the KPI.
- Never re-propose an item from the falsified list.
- Reply in the exact JSON shape each question asks for, and nothing else.

=== STUDY PACKET (excerpts from TRACK_MANUAL.md and two source files) ===
"""

SECTION_A_TASK = """SECTION A — comprehension. Answer each question in one or two sentences, from the
study packet only. Return JSON exactly of the form {"answers": {"Q1": "...", "Q2": "..."}}.

"""

SECTION_B_TASK = """SECTION B — forensics. Read the evidence, then answer the question as a race engineer
would on the radio: precise, mechanism first, honest about what the evidence cannot show.

Return JSON with exactly these keys:
  "diagnosis":      one sentence — what happened
  "mechanism":      one or two sentences — which layer (kernel / driver / emulator / kit /
                    config / operator / statistics) and why
  "evidence_used":  a list of 2 to 4 short quotes or values taken from the evidence
  "verdict_allowed": true or false — may the operator draw a knob or fix verdict from THIS
                    evidence alone under the ledger method (N>=3 per arm, warm runs only,
                    medians)?
  "next_action":    one sentence telling the operator what to do next
  "confidence":     "low", "medium" or "high"

"""


# ----------------------------------------------------------------------------- packet
def _between(text, start, end, label):
    i = text.find(start)
    if i < 0:
        sys.exit(f"packet: start marker not found for {label}: {start!r}")
    j = text.find(end, i + len(start))
    if j < 0:
        sys.exit(f"packet: end marker not found for {label}: {end!r}")
    return text[i:j].rstrip() + "\n"


def _lines_around(path, needle, before, after, label):
    lines = open(path, encoding="utf-8").read().splitlines()
    for n, ln in enumerate(lines):
        if needle in ln:
            lo, hi = max(0, n - before), min(len(lines), n + after + 1)
            return "\n".join(lines[lo:hi]) + "\n"
    sys.exit(f"packet: excerpt anchor not found for {label}: {needle!r}")


def build_packet():
    m = open(MANUAL, encoding="utf-8").read()
    parts = [
        ("§1.1 BYTES-TO-ATOMS — the three thresholds",
         _between(m, "### 1.1 BYTES-TO-ATOMS", "**The test for a tool that does not exist yet**", "§1.1")),
        ("§1.8 Session start", _between(m, "### 1.8 Session start", "\n---", "§1.8")),
        ("§2.1 Mission, KPI, roles", _between(m, "### 2.1 Mission, KPI, roles", "### 2.2", "§2.1")),
        ("§2.3 (excerpt) The attribution chain and the ledger",
         _between(m, "**The attribution chain (the keystone):**", "### 2.4", "§2.3")),
        ("§2.4 The mechanism catalog", _between(m, "### 2.4 The mechanism catalog", "## A. BUILDING", "§2.4")),
        ("§B.2 Analyzing telemetry", _between(m, "### B.2 Analyzing telemetry", "### B.3", "§B.2")),
        ("§B.3 The limits and skews of the data", _between(m, "### B.3 The limits and skews", "## C. RELEASING", "§B.3")),
        ("§F FALSIFIED & RETIRED", _between(m, "## F. FALSIFIED & RETIRED", "## Q. QUICK", "§F")),
        ("bin/session_postmortem.sh (excerpt: the bounded RPCS3.log read)",
         _lines_around(os.path.join(ROOT, "bin", "session_postmortem.sh"),
                       "Read via stdin redirect + bounded tail", 1, 6, "postmortem")),
        ("bin/etk_install_worker.py (excerpt: the game-launch rule, from the module docstring)",
         _lines_around(os.path.join(ROOT, "bin", "etk_install_worker.py"),
                       "THE ONE THING THAT MAKES THIS DANGEROUS", 0, 9, "worker")),
    ]
    body = "".join(f"\n--- {title} ---\n{text}" for title, text in parts)
    packet = EXAM_RULES + body + "\n=== END OF STUDY PACKET ===\n"
    manifest = {
        "chars": len(packet),
        "est_tokens": int(len(packet) / CHARS_PER_TOKEN),
        "sha256": hashlib.sha256(packet.encode()).hexdigest(),
        "parts": [(t, len(x)) for t, x in parts],
    }
    return packet, manifest


# ----------------------------------------------------------------------------- exam files
def load_questions():
    with open(os.path.join(EXAM_DIR, "questions.json"), encoding="utf-8") as f:
        return json.load(f)


def load_cases(only=None):
    cases = []
    d = os.path.join(EXAM_DIR, "cases")
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(d, name), encoding="utf-8") as f:
            c = json.load(f)
        if only and c["id"] not in only:
            continue
        cases.append(c)
    return cases


# ----------------------------------------------------------------------------- grading
def _flat(x):
    """Any answer (str / dict / list) as one lowercase string for regex keys."""
    if isinstance(x, str):
        return x
    return json.dumps(x, ensure_ascii=False)


def grade_key(text, key):
    """key = {"all": [regex...], "none": [regex...], "any_n": {"n": k, "patterns": [...]}}.
    Returns (passed, reasons[])."""
    t = _flat(text)
    reasons = []
    for pat in key.get("all", []):
        if not re.search(pat, t, re.I | re.S):
            reasons.append(f"missing: /{pat}/")
    for pat in key.get("none", []):
        if re.search(pat, t, re.I | re.S):
            reasons.append(f"forbidden: /{pat}/")
    any_n = key.get("any_n")
    if any_n:
        hits = [p for p in any_n["patterns"] if re.search(p, t, re.I | re.S)]
        if len(hits) < any_n["n"]:
            reasons.append(f"only {len(hits)} of {any_n['n']} required items")
    return (not reasons), reasons


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
    return None


def grade_case(answer, case):
    passed, reasons = grade_key(answer, case["key"])
    want = case["key"].get("verdict_allowed")
    if want is not None and isinstance(answer, dict):
        got = _as_bool(answer.get("verdict_allowed"))
        if got is not want:
            passed = False
            reasons.append(f"verdict_allowed: wanted {want}, got {answer.get('verdict_allowed')!r}")
    return passed, reasons


def selftest():
    bad = 0
    for q in load_questions():
        ok_e, r_e = grade_key(q["exemplar"], q["key"])
        ok_c, r_c = grade_key(q["counter"], q["key"])
        flag = "ok " if (ok_e and not ok_c) else "BAD"
        bad += flag == "BAD"
        print(f"[{flag}] {q['id']:<4} exemplar={'pass' if ok_e else 'FAIL ' + str(r_e)}  counter={'fail' if not ok_c else 'PASSES (key too loose)'}")
    for c in load_cases():
        ok_e, r_e = grade_case(c["exemplar"], c)
        ok_c, r_c = grade_case(c["counter"], c)
        flag = "ok " if (ok_e and not ok_c) else "BAD"
        bad += flag == "BAD"
        print(f"[{flag}] {c['id']:<22} exemplar={'pass' if ok_e else 'FAIL ' + str(r_e)}  counter={'fail' if not ok_c else 'PASSES (key too loose)'}")
    packet, man = build_packet()
    print(f"packet: {man['chars']} chars ≈ {man['est_tokens']} tokens, sha {man['sha256'][:12]}")
    for t, n in man["parts"]:
        print(f"   {n:>6} chars  {t}")
    print("SELFTEST", "FAIL" if bad else "PASS")
    return 1 if bad else 0


# ----------------------------------------------------------------------------- ollama
def chat(host, model, system, user, num_ctx, think, num_predict, timeout):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "keep_alive": "30m",
        "options": {"num_ctx": num_ctx, "temperature": 0.1, "num_predict": num_predict, "seed": 7},
    }
    if think is not None:
        body["think"] = think
    data = json.dumps(body).encode()
    req = urllib.request.Request(host.rstrip("/") + "/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        if think is not None and "think" in msg.lower():
            return chat(host, model, system, user, num_ctx, None, num_predict, timeout)
        raise RuntimeError(f"HTTP {e.code}: {msg[:300]}")
    wall = time.time() - t0
    content = (resp.get("message") or {}).get("content", "")
    usage = {k: resp.get(k) for k in ("prompt_eval_count", "prompt_eval_duration", "eval_count",
                                     "eval_duration", "load_duration", "total_duration", "done_reason")}
    return content, usage, wall


def parse_json_answer(content):
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s).rstrip("`").strip()
    try:
        return json.loads(s), None
    except json.JSONDecodeError as e:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0)), None
            except json.JSONDecodeError:
                pass
        return None, f"unparseable JSON: {e}"


# ----------------------------------------------------------------------------- run
def _rate(n, ns):
    return (n / (ns / NS)) if n and ns else None


def run_model(model, args, packet, manifest, questions, cases, out_dir):
    path = os.path.join(out_dir, model.replace("/", "_").replace(":", "_") + ".jsonl")
    done = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                rec = json.loads(line)
                done[(rec["section"], rec["id"])] = rec
            except json.JSONDecodeError:
                pass
    est_packet = manifest["est_tokens"]

    def record(rec):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done[(rec["section"], rec["id"])] = rec

    def one_call(section, cid, user, grader):
        if (section, cid) in done:
            print(f"  [{model}] {section}/{cid}: already done, skipping")
            return
        est = est_packet + int(len(user) / CHARS_PER_TOKEN)
        if est > args.num_ctx * 0.85:
            print(f"  [{model}] {section}/{cid}: REFUSED — est {est} tokens does not fit num_ctx {args.num_ctx}")
            record({"section": section, "id": cid, "model": model, "refused": True, "est_tokens": est})
            return
        print(f"  [{model}] {section}/{cid}: sending ~{est} tokens …", flush=True)
        try:
            content, usage, wall = chat(args.host, model, packet, user, args.num_ctx,
                                        (True if args.think else False), args.num_predict, args.timeout)
        except Exception as e:  # noqa: BLE001 — record the failure, keep the run alive
            print(f"  [{model}] {section}/{cid}: FAILED {e}")
            record({"section": section, "id": cid, "model": model, "error": str(e), "est_tokens": est})
            return
        answer, perr = parse_json_answer(content)
        truncated = bool(usage.get("prompt_eval_count")) and usage["prompt_eval_count"] < 0.6 * est
        grade = grader(answer) if answer is not None else {"passed": False, "reasons": [perr]}
        rec = {"section": section, "id": cid, "model": model, "ts": time.time(), "wall_s": round(wall, 1),
               "packet_sha256": manifest["sha256"], "est_tokens": est, "usage": usage,
               "prompt_tok_s": _rate(usage.get("prompt_eval_count"), usage.get("prompt_eval_duration")),
               "gen_tok_s": _rate(usage.get("eval_count"), usage.get("eval_duration")),
               "truncated_suspect": truncated, "answer_raw": content, "answer": answer, "grade": grade}
        record(rec)
        pe, ec = usage.get("prompt_eval_count"), usage.get("eval_count")
        print(f"  [{model}] {section}/{cid}: {'PASS' if grade['passed'] else 'fail'} · prompt {pe} tok "
              f"@ {rec['prompt_tok_s'] and round(rec['prompt_tok_s'], 1)} tok/s · gen {ec} tok "
              f"@ {rec['gen_tok_s'] and round(rec['gen_tok_s'], 1)} tok/s · wall {wall:.0f} s"
              + ("  ⚠ TRUNCATED?" if truncated else ""))
        for r in grade.get("reasons", []):
            print(f"       - {r}")

    if args.section in ("A", "AB"):
        user = SECTION_A_TASK + "\n".join(f"{q['id']}. {q['q']}" for q in questions) + "\n"

        def grade_a(answer):
            answers = (answer or {}).get("answers") if isinstance(answer, dict) else None
            if not isinstance(answers, dict):
                return {"passed": False, "score": 0, "of": len(questions), "reasons": ["no answers dict"], "per_q": {}}
            per_q, score = {}, 0
            for q in questions:
                ok, reasons = grade_key(answers.get(q["id"], ""), q["key"])
                per_q[q["id"]] = {"passed": ok, "reasons": reasons, "answer": answers.get(q["id"], "")}
                score += ok
            return {"passed": score == len(questions), "score": score, "of": len(questions),
                    "reasons": [f"{k}: {'; '.join(v['reasons'])}" for k, v in per_q.items() if not v["passed"]],
                    "per_q": per_q}
        one_call("A", "quiz", user, grade_a)

    if args.section in ("B", "AB"):
        for c in cases:
            user = SECTION_B_TASK + f"CASE {c['id']} — {c['title']}\n\nEVIDENCE:\n{c['evidence'].strip()}\n\nQUESTION: {c['question']}\n"

            def grade_b(answer, c=c):
                ok, reasons = grade_case(answer if isinstance(answer, dict) else {}, c)
                return {"passed": ok, "reasons": reasons}
            one_call("B", c["id"], user, grade_b)
    return path


# ----------------------------------------------------------------------------- report
def report(out_dir, questions, cases):
    lines = [f"# ETK EXAM scorecard — {os.path.basename(out_dir)}", ""]
    summary = {}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".jsonl"):
            continue
        recs = [json.loads(l) for l in open(os.path.join(out_dir, name), encoding="utf-8") if l.strip()]
        if not recs:
            continue
        model = recs[0]["model"]
        a = next((r for r in recs if r["section"] == "A"), None)
        b = {r["id"]: r for r in recs if r["section"] == "B"}
        walls = [r.get("wall_s", 0) for r in recs]
        p_rates = [r["prompt_tok_s"] for r in recs if r.get("prompt_tok_s")]
        g_rates = [r["gen_tok_s"] for r in recs if r.get("gen_tok_s")]
        a_score = f"{a['grade'].get('score', 0)}/{a['grade'].get('of', len(questions))}" if a and "grade" in a else "—"
        b_pass = sum(1 for r in b.values() if r.get("grade", {}).get("passed"))
        lines += [f"## {model}", "",
                  f"- Section A: **{a_score}**" + ("" if a and "grade" in a else "  (missing)"),
                  f"- Section B: **{b_pass}/{len(cases)}** cases passed",
                  f"- prompt-eval {min(p_rates):.1f}–{max(p_rates):.1f} tok/s, generation {min(g_rates):.1f}–{max(g_rates):.1f} tok/s, "
                  f"total wall {sum(walls) / 60:.1f} min over {len(recs)} calls" if p_rates and g_rates else "- no timings",
                  ""]
        if a and "grade" in a:
            lines += ["| Q | pass | answer (trimmed) |", "|---|---|---|"]
            for q in questions:
                pq = a["grade"].get("per_q", {}).get(q["id"], {})
                ans = _flat(pq.get("answer", "")).replace("\n", " ").replace("|", "/")
                lines.append(f"| {q['id']} | {'✓' if pq.get('passed') else '✗ ' + '; '.join(pq.get('reasons', []))[:80]} | {ans[:160]} |")
            lines.append("")
        lines += ["| Case | pass | diagnosis (trimmed) | reasons | wall |", "|---|---|---|---|---|"]
        for c in cases:
            r = b.get(c["id"])
            if not r:
                lines.append(f"| {c['id']} | — | (not run) | | |")
                continue
            g = r.get("grade", {})
            diag = ((r.get("answer") or {}).get("diagnosis", "") if isinstance(r.get("answer"), dict) else r.get("error", ""))
            diag = str(diag).replace("\n", " ").replace("|", "/")
            lines.append(f"| {c['id']} | {'✓' if g.get('passed') else '✗'} | {diag[:180]} | {'; '.join(g.get('reasons', []))[:120].replace('|', '/')} | {r.get('wall_s', '')} s |")
        lines.append("")
        summary[model] = {"A": a_score, "B": f"{b_pass}/{len(cases)}", "wall_min": round(sum(walls) / 60, 1)}
    lines += ["## Rubric (for the human read)", ""]
    for c in cases:
        lines.append(f"- **{c['id']}** — {c['rubric']}")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "scorecard.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(text)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434"))
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--section", choices=["A", "B", "AB"], default="AB")
    ap.add_argument("--cases", nargs="*", help="run only these case ids")
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--num-predict", type=int, default=1200)
    ap.add_argument("--think", action="store_true", help="leave thinking on (default: off)")
    ap.add_argument("--timeout", type=int, default=2400, help="seconds per call")
    ap.add_argument("--out", help="run directory (default: state/radio_exam/<UTC stamp>)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report", action="store_true", help="rebuild scorecard.md for --out (or the newest run)")
    args = ap.parse_args()

    questions, cases = load_questions(), load_cases(args.cases)
    if args.selftest:
        sys.exit(selftest())
    packet, manifest = build_packet()

    if args.report:
        out_dir = args.out or os.path.join(OUT_BASE, sorted(os.listdir(OUT_BASE))[-1])
        report(out_dir, questions, cases)
        return
    if args.dry_run:
        print(f"packet: {manifest['chars']} chars ≈ {manifest['est_tokens']} tokens (sha {manifest['sha256'][:12]})")
        for t, n in manifest["parts"]:
            print(f"   {n:>6} chars  {t}")
        print(f"Section A: {len(questions)} questions ≈ {int(len(SECTION_A_TASK + ''.join(q['q'] for q in questions)) / CHARS_PER_TOKEN)} tokens")
        for c in cases:
            print(f"Section B: {c['id']:<22} evidence ≈ {int(len(c['evidence']) / CHARS_PER_TOKEN)} tokens")
        return
    if not args.models:
        sys.exit("--models is required (e.g. --models qwen3.5:4b qwen3.5:9b)")

    out_dir = args.out or os.path.join(OUT_BASE, datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "packet.txt"), "w", encoding="utf-8") as f:
        f.write(packet)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"packet": manifest, "args": vars(args), "questions": len(questions),
                   "cases": [c["id"] for c in cases]}, f, indent=1)
    print(f"run dir: {out_dir}\npacket ≈ {manifest['est_tokens']} tokens; num_ctx {args.num_ctx}; models {args.models}")
    for model in args.models:
        print(f"\n== {model}")
        run_model(model, args, packet, manifest, questions, cases, out_dir)
    report(out_dir, questions, cases)


if __name__ == "__main__":
    main()
