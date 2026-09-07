#!/usr/bin/env python3
"""ETK RADIO -- host-side regression tests for the eval fixtures and assertion engine.

Run from the repo root:   python3 tools/radio/test_eval.py

No node, no rig, no network, no telemetry mirror: everything here runs off the twelve
case files and hand-built debriefs. The point of an eval is that it discriminates, so
these tests are mostly about the engine saying NO to the right things:

  [4] a debrief with a foreign top-level key must FAIL schema_valid - a closed contract
      is what keeps an invented field (and whatever a model put in it) off a surface.
  [5] comparison_needs_n must PASS a finding that cites two arms at N>=3 and FAIL the
      same claim at N=2. That is the no-crown-below-N guard; an op that passes both is
      decoration.
  [6] config_no_decrease must PASS an equal value and FAIL a lower one, because
      "resolution is not a KPI lever" is a direction, not a prohibition on the key.
  [7] keepalive_absent must NOT fire on a blank rescues cell. A pre-column-era row has
      no rescue count; reading that as "the keepalive did not fire" would invent an
      anomaly on every 2026-07 row (this test failed against the first draft).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, HERE)

import eval as ev                                          # noqa: E402  (tools/radio/eval.py)

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  [%s] %s%s" % ("ok" if ok else "XX", label,
                           "" if ok else "   got %r, wanted %r" % (got, want)))
    if not ok:
        FAILS.append(label)
    return ok


def ascii_offenders(obj, path="", out=None):
    out = [] if out is None else out
    if isinstance(obj, str):
        if not obj.isascii():
            out.append((path, obj[:60]))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.isascii():
                out.append((path + "/<key>", k))
            ascii_offenders(v, "%s/%s" % (path, k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            ascii_offenders(v, "%s[%d]" % (path, i), out)
    return out


def deb(**over):
    """A minimal valid DEBRIEF v1, overridable."""
    d = {"schema": "ETK-RADIO-DEBRIEF v1", "epoch": 1788285065, "game_id": "NPEA00050",
         "model": "etk-radio:9b", "prompt_sha256": "0" * 64, "corpus_commit": "cec9e43",
         "tokens": {"prompt": 3410, "completion": 402}, "latency_s": 271.3,
         "run_sheet": None,
         "radio": "Nothing to change yet.", "headline": "SURVIVED - arm at N=2 of 3",
         "tags": ["low_n"],
         "findings": [{"kind": "observation", "text": "Two runs on this arm.",
                       "evidence": [{"source": "dyno", "field": "n", "value": 2}]}],
         "recommendations": [{"kind": "next_run", "text": "One more warm race.",
                              "n_basis": {"arm": "a", "n": 2, "n_needed": 3},
                              "config_changes": [], "driver_dial": None,
                              "confidence": "high"}],
         "guards": {"passed": True, "dropped": []}}
    d.update(over)
    return d


print("\n[1] the twelve case files load and carry the contract")
cases = ev.load_cases()
check("twelve cases", len(cases), 12)
check("ids match the spec 10 table exactly", [c["id"] for c in cases], ev.CASE_IDS)
for c in cases:
    cid = c["id"]
    missing = [k for k in ev.CASE_KEYS if k not in c]
    check("%s: all required keys" % cid, missing, [])
    src = c.get("source") or {}
    check("%s: source.kind is known" % cid, src.get("kind") in ev.SOURCE_KINDS, True)
    check("%s: rubric is a one-liner" % cid,
          isinstance(c.get("rubric"), str) and "\n" not in c["rubric"] and len(c["rubric"]) > 20,
          True)
    check("%s: from names its provenance" % cid,
          isinstance(c.get("from"), str) and len(c["from"]) > 40, True)
    check("%s: pack is PACK v1" % cid, (c.get("pack") or {}).get("schema"), "ETK-RADIO-PACK v1")
    check("%s: has assertions" % cid, len(c.get("expect") or []) >= 3, True)
check("model_output only on hallucinated_key",
      sorted(c["id"] for c in cases if "model_output" in c), ["hallucinated_key"])
mo = [c for c in cases if c["id"] == "hallucinated_key"][0]["model_output"]
check("model_output carries a foreign yaml_key",
      any(ch["yaml_key"].strip() == "Shader Cache Depth"
          for r in mo["recommendations"] for ch in r["config_changes"]), True)
check("model_output carries an out-of-range value",
      any(ch["new_value"] == "125" for r in mo["recommendations"] for ch in r["config_changes"]),
      True)

print("\n[2] provenance: a ledger case's epoch is the pack's epoch, nothing is invented")
for c in cases:
    src = c["source"]
    if src["kind"] == "ledger":
        check("%s: source.epoch == pack.epoch" % c["id"], src.get("epoch"), c["pack"].get("epoch"))
        check("%s: generated_by names the packer and a rev" % c["id"],
              isinstance(src.get("generated_by"), str)
              and "radio_pack.py" in src["generated_by"] and " @ " in src["generated_by"], True)
    else:
        check("%s: no epoch claimed for a %s pack" % (c["id"], src["kind"]), src.get("epoch"), None)
        check("%s: no generated_by claimed" % c["id"], src.get("generated_by"), None)
        check("%s: pack_notes say synthetic" % c["id"],
              any("synthetic:" in str(n) for n in (c["pack"].get("pack_notes") or [])), True)
    pk = c["pack"]
    check("%s: pack uses changes_since_last_debrief" % c["id"],
          "changes_since_last_debrief" in (pk.get("history") or {})
          and "recent_changes" not in (pk.get("history") or {}), True)
    tl = pk.get("timeline")
    if isinstance(tl, dict):
        check("%s: timeline uses temp_c" % c["id"],
              "temp_c" in tl and "gpu_temp_c" not in tl and "perfect_windows" in tl, True)
    check("%s: rig carries os and kit keys" % c["id"],
          "os" in (pk.get("rig") or {}) and "kit" in (pk.get("rig") or {}), True)
    check("%s: run_sheet key present" % c["id"], "run_sheet" in pk, True)

print("\n[3] ASCII everywhere, and every op name is known")
for c in cases:
    raw = open(c["_path"], "rb").read()
    check("%s: file bytes are ASCII" % c["id"], raw.isascii(), True)
    bad = ascii_offenders(c)
    check("%s: no non-ASCII string" % c["id"], bad, [])
    errs = []
    for op in c["expect"]:
        errs += ["%s: %s" % (c["id"], e) for e in ev.op_errors(op)]
    check("%s: every assertion is a known, well-formed op" % c["id"], errs, [])
    for d, who in ((c["exemplar"], "exemplar"), (c["counter"], "counter")):
        check("%s: %s radio <= 280 / headline <= 60" % (c["id"], who),
              len(d["radio"]) <= 280 and len(d["headline"]) <= 60, True)
used = sorted({op["op"] for c in cases for op in c["expect"]})
print("      ops exercised by the cases: %s" % ", ".join(used))
print("      ops defined but unused by a case: %s"
      % (", ".join(sorted(set(ev.OPS) - set(used))) or "none"))

print("\n[4] schema_valid closes the contract")
op = {"op": "schema_valid"}
case0 = cases[0]
ok, why = ev.op_schema_valid(op, case0, deb())
check("a plain DEBRIEF v1 is valid", ok, True)
ok, why = ev.op_schema_valid(op, case0, deb(next_action="rm -rf /storage"))
check("a foreign top-level key fails", ok, False)
check("and the reason names it", "next_action" in why, True)
ok, _ = ev.op_schema_valid(op, case0, deb(schema="ETK-RADIO-DEBRIEF v2"))
check("a wrong schema string fails", ok, False)
ok, _ = ev.op_schema_valid(op, case0, deb(findings=[{"kind": "verdict", "text": "x",
                                                     "evidence": [{"source": "ledger"}]}]))
check("a finding kind outside the enum fails", ok, False)
ok, _ = ev.op_schema_valid(op, case0, deb(headline="x" * 61))
check("an over-long headline fails", ok, False)
# The em dash is written as an escape so this file itself stays ASCII on disk.
ok, _ = ev.op_schema_valid(op, case0, deb(radio="Fence park \u2014 caught it, done."))
check("a non-ASCII radio line fails", ok, False)
# The op defers to tools/radio/schemas.py when it is there (agent A owns that file); the
# built-in fallback has to hold the same line on its own, so check it directly too.
check("the built-in fallback also rejects a foreign top-level key",
      any("next_action" in e for e in ev.debrief_shape_errors(deb(next_action="rm -rf /storage"))),
      True)
check("the built-in fallback accepts a plain DEBRIEF v1", ev.debrief_shape_errors(deb()), [])
print("      validator in use: %s" % ("tools/radio/schemas.py"
                                      if ev._load_agent_a_validator() else "built-in shape check"))

print("\n[4b] the fixture packs against tools/radio/schema/pack.v1.json (informational)")
# Agent A owns the pack contract and is still moving it; a mismatch here is a note for
# the lead, not a red test, so it warns instead of failing.
try:
    import schemas as _sc                                   # noqa: E402
    _pack_schema = _sc.load("pack.v1")
except Exception as exc:                                    # no schemas.py yet, or no pack.v1
    print("      no pack.v1 contract to check against (%s)" % exc)
else:
    for c in cases:
        errs = _sc.validate(c["pack"], _pack_schema)
        print("      %-22s %s" % (c["id"], "valid" if not errs else "WARN " + "; ".join(errs[:2])))

print("\n[5] comparison_needs_n is the no-crown-below-N guard")
op = {"op": "comparison_needs_n"}


def cmp_finding(text, ns):
    return deb(findings=[{"kind": "observation", "text": text,
                          "evidence": [{"source": "dyno", "field": "%s.n" % chr(97 + i),
                                        "value": n} for i, n in enumerate(ns)]}])


ok, _ = ev.op_comparison_needs_n(op, case0,
                                 cmp_finding("The sddepth arm versus the default arm sits level.",
                                             [5, 35]))
check("two arms at N>=3 pass", ok, True)
ok, why = ev.op_comparison_needs_n(op, case0,
                                   cmp_finding("The zlatez arm versus the default arm wins.",
                                               [2, 35]))
check("the same claim with one arm at N=2 fails", ok, False)
check("and the reason shows the N it found", "[2, 35]" in why, True)
ok, _ = ev.op_comparison_needs_n(op, case0,
                                 cmp_finding("zlatez at 925 is better than the default arm.", [4]))
check("a comparison citing only one N fails", ok, False)
ok, _ = ev.op_comparison_needs_n(op, case0,
                                 cmp_finding("zlatez at 925 is better than the default arm.", []))
check("a comparison citing no N at all fails", ok, False)
ok, _ = ev.op_comparison_needs_n(op, case0,
                                 cmp_finding("The keepalive absorbed three fence parks.", [2]))
check("a non-comparison finding at N=2 is left alone", ok, True)

print("\n[6] config_no_decrease is a direction, not a ban on the key")
op = {"op": "config_no_decrease", "yaml_key": "  Resolution Scale", "below": 100}


def cfg(val, kind="config"):
    return deb(recommendations=[{"kind": kind, "text": "t", "n_basis": None,
                                 "config_changes": [{"yaml_key": "  Resolution Scale",
                                                     "new_value": val}],
                                 "driver_dial": None, "confidence": "high"}])


ok, _ = ev.op_config_no_decrease(op, case0, cfg("100"))
check("an equal value passes", ok, True)
ok, _ = ev.op_config_no_decrease(op, case0, deb())
check("no change at all passes", ok, True)
ok, why = ev.op_config_no_decrease(op, case0, cfg("66"))
check("a lower value fails", ok, False)
check("and the reason names the value", "66" in why, True)
res = [c for c in cases if c["id"] == "res_lowering_kpi"][0]
check("the res case resolves its floor from the pack when 'below' is absent",
      ev.pack_config_value(res["pack"], "  Resolution Scale") in (100, "100"), True)

print("\n[7] computed tags come from the pack, and only when the pack says so")
zl = [c for c in cases if c["id"] == "zlatez_925_lown"][0]["pack"]
check("zlatez_925_lown computes low_n", "low_n" in ev.compute_tags(zl), True)
ka = [c for c in cases if c["id"] == "keepalive_off_day"][0]["pack"]
check("keepalive_off_day computes keepalive_absent", "keepalive_absent" in ev.compute_tags(ka), True)
blank = json.loads(json.dumps(ka))
blank["session"]["rescues"] = None
check("a BLANK rescues cell does NOT compute keepalive_absent",
      "keepalive_absent" in ev.compute_tags(blank), False)
sd = [c for c in cases if c["id"] == "sddepth_verdict"][0]["pack"]
check("sddepth_verdict does not compute low_n (its arm is N=5)",
      "low_n" in ev.compute_tags(sd), False)
pn = [c for c in cases if c["id"] == "panic_silent"][0]["pack"]
check("panic_silent computes panic_silent", "panic_silent" in ev.compute_tags(pn), True)
loud = json.loads(json.dumps(pn))
loud["crash"]["blackbox_tail"] = list(loud["crash"]["blackbox_tail"]) + [
    "3,1155,230253999,-;Unable to handle kernel paging request at virtual address"]
check("a real kmsg lead-up removes panic_silent", "panic_silent" in ev.compute_tags(loud), False)
bk = [c for c in cases if c["id"] == "bake_session_fps"][0]["pack"]
check("bake_session_fps computes bake", "bake" in ev.compute_tags(bk), True)
at = [c for c in cases if c["id"] == "attract_row"][0]["pack"]
check("attract_row computes attract", "attract" in ev.compute_tags(at), True)

print("\n[8] --selftest: every exemplar passes, every counter fails")
r = subprocess.run([sys.executable, os.path.join(HERE, "eval.py"), "--selftest"],
                   capture_output=True, text=True, cwd=ROOT, timeout=300)
tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
print("      %s" % tail)
check("--selftest exits 0", r.returncode, 0)
check("--selftest is 24/24 discriminating", "24/24 discriminating" in tail, True)
check("--selftest says 12/12 exemplars pass", "12/12 exemplars pass" in tail, True)
check("--selftest says 12/12 counters fail", "12/12 counters fail" in tail, True)

print("\n[9] --list runs anywhere, with or without a telemetry mirror")
r = subprocess.run([sys.executable, os.path.join(HERE, "eval.py"), "--list"],
                   capture_output=True, text=True, cwd=ROOT, timeout=120)
check("--list exits 0", r.returncode, 0)
check("--list names every case", all(cid in r.stdout for cid in ev.CASE_IDS), True)

print()
if FAILS:
    print("FAILED: %d check(s)" % len(FAILS))
    for f in FAILS:
        print("   - %s" % f)
    sys.exit(1)
print("ALL RADIO EVAL CHECKS PASSED")
