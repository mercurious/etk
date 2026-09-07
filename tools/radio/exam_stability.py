#!/usr/bin/env python3
"""ETK EXAM stability aggregator — re-grade N exam runs with the CURRENT keys and report,
per (model, item), how many runs passed. The kit's own rule is N>=3 before a verdict
(TRACK_MANUAL §B.3), so a single exam pass is one data point; this is the N.

    python3 tools/radio/exam_stability.py state/radio_exam/stability_YYYYMMDD/*/

Each argument is a run directory (one seed) holding <model>.jsonl. Grading is redone from
the stored `answer` field against tools/radio/exam/*.json, so every run is scored by the
same, current keys regardless of what shipped when it ran. Prints a per-item matrix and a
verdict line per model: an item is STABLE if it passed in every run, FLAPPED if some but
not all, STABLE-FAIL if none. Writes stability.md beside the first run dir's parent.
"""
import json
import os
import sys
import glob
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("exam", os.path.join(HERE, "exam.py"))
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)
QS = ex.load_questions()
CS = {c["id"]: c for c in ex.load_cases()}


def regrade_run(run_dir):
    """{model: {"A": {qid: bool}, "B": {cid: bool}, "seed": int, "walls": [..]}}"""
    out = {}
    for jf in sorted(glob.glob(os.path.join(run_dir, "*.jsonl"))):
        model = os.path.basename(jf)[:-6]
        rec_a, cases, walls = None, {}, []
        for line in open(jf, encoding="utf-8"):
            r = json.loads(line)
            walls.append(r.get("wall_s", 0) or 0)
            if r["section"] == "A":
                rec_a = r
            else:
                ok, _ = ex.grade_case(r.get("answer") or {}, CS[r["id"]])
                cases[r["id"]] = ok
        a = {}
        ans = ((rec_a or {}).get("answer") or {}).get("answers", {}) if rec_a else {}
        for q in QS:
            ok, _ = ex.grade_key(ans.get(q["id"], ""), q["key"])
            a[q["id"]] = ok
        seed = None
        mf = os.path.join(run_dir, "manifest.json")
        if os.path.exists(mf):
            seed = json.load(open(mf)).get("args", {}).get("seed")
        out[model] = {"A": a, "B": cases, "seed": seed, "walls": walls}
    return out


def main():
    run_dirs = [d.rstrip("/") for d in sys.argv[1:] if os.path.isdir(d)]
    if len(run_dirs) < 2:
        sys.exit("usage: exam_stability.py <run_dir> <run_dir> [<run_dir> ...]  (>=2 runs)")
    runs = [regrade_run(d) for d in run_dirs]
    models = sorted({m for r in runs for m in r})
    n = len(runs)
    lines = [f"# ETK EXAM stability — {n} runs, re-graded with current keys", "",
             "Runs: " + ", ".join(f"{os.path.basename(d)} (seed {runs[i].get(list(runs[i])[0], {}).get('seed') if runs[i] else '?'})"
                                  for i, d in enumerate(run_dirs)), ""]
    for model in models:
        a_items = [q["id"] for q in QS]
        b_items = list(CS.keys())
        def counts(section, item):
            return sum(1 for r in runs if r.get(model, {}).get(section, {}).get(item))
        a_scores = [sum(1 for q in a_items if r.get(model, {}).get("A", {}).get(q)) for r in runs]
        b_scores = [sum(1 for c in b_items if r.get(model, {}).get("B", {}).get(c)) for r in runs]
        flapped = ([f"A/{q}" for q in a_items if 0 < counts("A", q) < n]
                   + [f"B/{c}" for c in b_items if 0 < counts("B", c) < n])
        stable_fail = ([f"A/{q}" for q in a_items if counts("A", q) == 0]
                       + [f"B/{c}" for c in b_items if counts("B", c) == 0])
        walls = [sum(r[model]["walls"]) / 60 for r in runs if model in r]
        lines += [f"## {model}", "",
                  f"- Section A per run: {a_scores}  (of {len(a_items)})",
                  f"- Section B per run: {b_scores}  (of {len(b_items)})",
                  f"- wall per run: {', '.join(f'{w:.0f}m' for w in walls)}",
                  f"- **STABLE-PASS** (every run): {n - len(flapped) - len(stable_fail)} of {len(a_items) + len(b_items)} items",
                  f"- **FLAPPED** (some runs only): {', '.join(flapped) or 'none'}",
                  f"- **STABLE-FAIL** (no run): {', '.join(stable_fail) or 'none'}", ""]
        lines += ["| item | " + " | ".join(f"r{i+1}" for i in range(n)) + " | verdict |",
                  "|---|" + "---|" * n + "---|"]
        for q in a_items:
            row = [("✓" if r.get(model, {}).get("A", {}).get(q) else "✗") for r in runs]
            v = "stable" if all(x == "✓" for x in row) else ("FAIL" if all(x == "✗" for x in row) else "FLAP")
            lines.append(f"| A/{q} | " + " | ".join(row) + f" | {v} |")
        for c in b_items:
            row = [("✓" if r.get(model, {}).get("B", {}).get(c) else "✗") for r in runs]
            v = "stable" if all(x == "✓" for x in row) else ("FAIL" if all(x == "✗" for x in row) else "FLAP")
            lines.append(f"| B/{c} | " + " | ".join(row) + f" | {v} |")
        lines.append("")
    text = "\n".join(lines) + "\n"
    parent = os.path.dirname(run_dirs[0])
    out_path = os.path.join(parent, "stability.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
