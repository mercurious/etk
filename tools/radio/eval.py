#!/usr/bin/env python3
"""ETK RADIO -- the eval: twelve golden cases and the assertion engine (spec 10).

Each case in tools/radio/eval_cases/ is a PACK v1 fixture drawn from a verdict the
ledger already paid for, plus a list of DETERMINISTIC assertions the debrief for that
pack must satisfy. The assertions are the section 6 guards restated as things you can check on
a finished debrief: no crown below N, no falsified item, schema vocabulary only,
resolution is not a KPI lever, bake/aborted rows are not feel evidence, attribution
before narrative, evidence beside every claim.

The house discipline (copied from tools/radio/exam.py): every case carries an
`exemplar` -- a known-GOOD debrief that must pass every assertion -- and a `counter` -- the
realistic WRONG answer (the crown at N=2, the fps claim on a bake row, the Resolution
Scale drop, the max_map_count proposal) that must fail at least one. `--selftest` grades
both, so an assertion that would pass a wrong debrief or fail a right one is caught
before any model is scored. Target: 12/12 exemplars pass, 12/12 counters fail.

Scoring (spec 10.3): the rules-only baseline must be 12/12 -- it is the pipeline's
contract -- and so must any model, with the operator's blind read deciding usefulness on
top. The `rubric` line in each case is that human column; nothing here grades it.

Usage (host or node, stdlib only, no network, no rig):
    python3 tools/radio/eval.py --list
    python3 tools/radio/eval.py --selftest
    python3 tools/radio/eval.py --debriefs state/radio_eval/rules_only
    python3 tools/radio/eval.py --refresh-packs        # needs state/etk_telemetry/

--refresh-packs re-runs bin/radio_pack.py --stdout for every ledger-sourced case against
the host mirror and stores the result under the DECIDED PACK v1 field names (see
normalize_pack). Synthetic packs are never touched. With no mirror present it says so
and exits 0 -- the eval itself runs anywhere, which is the point: it has to run on the
node where there is no telemetry at all.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
CASE_DIR = os.path.join(HERE, "eval_cases")
PACKER = os.path.join(ROOT, "bin", "radio_pack.py")
MIRROR = os.path.join(ROOT, "state", "etk_telemetry")
SCHEMAS_PY = os.path.join(HERE, "schemas.py")

# The twelve, in spec 10 table order. The set is a contract: test_eval.py pins it.
CASE_IDS = [
    "sysmem_no_crown",
    "sddepth_verdict",
    "grid_gt5p_split",
    "bake_session_fps",
    "attract_row",
    "zlatez_925_lown",
    "audio_from_aud_cell",
    "keepalive_off_day",
    "res_lowering_kpi",
    "falsified_tempt",
    "panic_silent",
    "hallucinated_key",
]

CASE_KEYS = ("id", "from", "source", "pack", "expect", "rubric", "exemplar", "counter")
SOURCE_KINDS = ("ledger", "synthetic", "service")

# DEBRIEF v1 (spec 3.2)
DEBRIEF_TOP = {"schema", "epoch", "game_id", "model", "prompt_sha256", "corpus_commit",
               "tokens", "latency_s", "radio", "headline", "tags", "findings",
               "recommendations", "run_sheet", "guards"}
DEBRIEF_REQUIRED = {"schema", "epoch", "radio", "headline", "tags", "findings",
                    "recommendations", "guards"}
FINDING_KINDS = {"observation", "mechanism", "anomaly"}
REC_KINDS = {"next_run", "config", "dial", "power", "no_change", "investigate"}
RADIO_MAX, HEADLINE_MAX = 280, 60

TEXT_FIELDS = ("radio", "headline", "findings.text", "recommendations.text")

# A finding "compares arms" when it says so in the ways an engineer says it. The
# comparison_needs_n op then demands two cited N >= 3 (the section 6 no-crown-below-N guard).
COMPARISON_RE = re.compile(
    r"(?i)(\bvs\.?\b|\bversus\b|\bcompared (?:to|with)\b|\bagainst the (?:default|baseline|other)\b"
    r"|\b(?:better|worse|faster|slower|cleaner|higher|lower|longer|shorter) than\b"
    r"|\bbeats\b|\boutperform\w*\b|\bwins? over\b|\bahead of\b|\bimproves? on\b"
    r"|\b\d+(?:\.\d+)?\s*(?:x|times)\s+(?:better|worse|fewer|more|longer|the)\b"
    r"|\bcuts? (?:the )?\w+ (?:rate )?(?:by|to)\b)")

# One evidence entry cites an arm's N when its `field` is n / *_n / arm n.
N_FIELD_RE = re.compile(r"(?i)^(?:n|arm[_ ]?n|n[_ ]?arm|[a-z0-9_ .\[\]-]*[_. ]n)$")


# --------------------------------------------------------------------- pack shaping
def normalize_pack(pack):
    """A PACK v1 object under the DECIDED field names (agent A is reconciling the packer
    to these; a pack generated today still carries the older ones). Renames only; never
    invents a value. Returns the same object, mutated.

      history.recent_changes  -> history.changes_since_last_debrief
      timeline.gpu_temp_c     -> timeline.temp_c
      timeline.perfect_windows, run_sheet, rig.os, rig.kit -> present, null when unknown
    """
    if not isinstance(pack, dict):
        return pack
    rig = pack.setdefault("rig", {})
    for k in ("os", "kit"):
        rig.setdefault(k, None)
    hist = pack.setdefault("history", {})
    if "changes_since_last_debrief" not in hist:
        hist["changes_since_last_debrief"] = hist.pop("recent_changes", [])
    hist.pop("recent_changes", None)
    tl = pack.get("timeline")
    if isinstance(tl, dict):
        if "temp_c" not in tl:
            tl["temp_c"] = tl.pop("gpu_temp_c", None)
        tl.pop("gpu_temp_c", None)
        tl.setdefault("perfect_windows", None)
    pack.setdefault("run_sheet", None)
    return pack


def _sess(pack):
    return (pack.get("session") or {}) if isinstance(pack, dict) else {}


def _dial(pack):
    d = (pack.get("rig") or {}).get("dial")
    return (d or "default").strip() or "default"


def _arm_dial(arm):
    m = re.search(r"tu_debug=([^;]+)", arm.get("tune") or "")
    return m.group(1).strip() if m else "default"


def session_arms(pack):
    """The dyno arms that describe THIS session's condition (clk + power rung + dial).
    N comes from dyno, never from the model and never guessed here."""
    s, out = _sess(pack), []
    dial = _dial(pack)
    clk, pwr = s.get("gpu_mhz"), (s.get("pwr") or "").strip()
    if clk in (None, "") or not pwr:
        return out
    for arm in ((pack.get("dyno") or {}).get("arms") or []):
        if arm.get("clk") == clk and (arm.get("pwr") or "") == pwr and _arm_dial(arm) == dial:
            out.append(arm)
    return out


def compute_tags(pack):
    """The tags the NODE computes from the pack (spec 3.2). The tests assert on these.

    stack_change is NOT computed: today's pack carries no per-history-row stack, so
    there is nothing to compare against. It is left to the node and asserted by no case.
    """
    s = _sess(pack)
    tags = []
    status = (s.get("status") or "").upper()
    sigs = [str(x).upper() for x in (s.get("crash_sig") or [])]

    if (s.get("shaders_harvested") or 0) > 5:
        tags.append("bake")
    if status.startswith("ABORTED"):
        tags.append("aborted")

    op = pack.get("operator") or {}
    blob = " ".join(str(op.get(k) or "") for k in ("note", "feel")).lower()
    if "attract" in blob:
        tags.append("attract")

    if not any((a.get("n") or 0) >= 3 for a in session_arms(pack)):
        tags.append("low_n")

    fault = (s.get("gpu_fault_status") or "").strip()
    resc = s.get("rescues")
    # rescues must BE zero, not merely absent: a blank cell is a pre-column era row,
    # not a keepalive that failed to fire.
    if fault and fault != "-" and resc is not None and _num(resc) == 0:
        tags.append("keepalive_absent")

    if status.startswith("PANIC") or "PANIC_REBOOT" in sigs:
        tail = (pack.get("crash") or {}).get("blackbox_tail") or []
        lead = re.compile(r"(?i)(kernel panic|Oops|BUG:|Call trace|hung task|watchdog|"
                          r"rcu_sched|Unable to handle|smmu|page fault|gpu fault|"
                          r"fence timeout|a6xx|kgsl|adreno)")
        if not any(lead.search(str(ln)) for ln in tail):
            tags.append("panic_silent")

    return tags


def pack_config_value(pack, yaml_key):
    """The session's current value for a pitstop_fields yaml_key, from the pack.
    Accepts both the packer's stripped keys and the schema's indented ones, and falls
    back to the ledger's res_scale column for Resolution Scale."""
    cfg = pack.get("config") or {}
    for holder in (cfg.get("values"), cfg.get("yaml")):
        if isinstance(holder, dict):
            for k in (yaml_key, yaml_key.strip()):
                if k in holder:
                    return holder[k]
    if yaml_key.strip() == "Resolution Scale":
        return _sess(pack).get("res_scale")
    return None


# ------------------------------------------------------------------------- helpers
def _texts(debrief, fields):
    out = []
    for f in fields:
        if f == "radio":
            out.append(str(debrief.get("radio") or ""))
        elif f == "headline":
            out.append(str(debrief.get("headline") or ""))
        elif f == "findings.text":
            out += [str((x or {}).get("text") or "") for x in (debrief.get("findings") or [])]
        elif f == "recommendations.text":
            out += [str((x or {}).get("text") or "") for x in (debrief.get("recommendations") or [])]
        elif f == "all":
            out += _texts(debrief, TEXT_FIELDS)
        else:
            out.append("")
    return out


def _config_changes(debrief):
    for rec in (debrief.get("recommendations") or []):
        for ch in (rec.get("config_changes") or []):
            yield rec, ch


def _num(v):
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _evidence_ns(finding):
    ns = []
    for ev in (finding.get("evidence") or []):
        fld = str((ev or {}).get("field") or "")
        if N_FIELD_RE.match(fld.strip()):
            n = _num((ev or {}).get("value"))
            if n is not None:
                ns.append(int(n))
    return ns


def _load_agent_a_validator():
    """Defer to tools/radio/schemas.py when it lands; agent A owns that file. Returns a
    callable(debrief) -> list_of_errors, or None to use the built-in shape check."""
    if not os.path.exists(SCHEMAS_PY):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("etk_radio_schemas", SCHEMAS_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return None
    # The shipped API: load("debrief.v1") -> schema dict, validate(obj, schema) -> errors.
    load_fn, val_fn = getattr(mod, "load", None), getattr(mod, "validate", None)
    if callable(load_fn) and callable(val_fn):
        try:
            schema = load_fn("debrief.v1")
        except Exception:
            schema = None
        if isinstance(schema, dict):
            def call(debrief, val_fn=val_fn, schema=schema):
                return [str(x) for x in (val_fn(debrief, schema) or [])]
            return call
    for name in ("validate_debrief", "debrief_errors"):
        fn = getattr(mod, name, None)
        if callable(fn):
            def call(debrief, fn=fn, name=name):
                res = fn(debrief)
                if res is None or res is True:
                    return []
                if res is False:
                    return ["schemas.%s returned False" % name]
                if isinstance(res, (list, tuple)):
                    return [str(x) for x in res]
                return [str(res)]
            return call
    return None


def debrief_shape_errors(debrief):
    """The built-in DEBRIEF v1 shape check (spec 3.2) -- used when schemas.py is absent
    or does not expose a validator. Foreign top-level keys are an error: the contract is
    closed, and an invented key is exactly how a model smuggles a command in."""
    errs = []
    if not isinstance(debrief, dict):
        return ["not an object"]
    missing = DEBRIEF_REQUIRED - set(debrief)
    if missing:
        errs.append("missing keys: %s" % ", ".join(sorted(missing)))
    foreign = set(debrief) - DEBRIEF_TOP
    if foreign:
        errs.append("foreign top-level keys: %s" % ", ".join(sorted(foreign)))
    if str(debrief.get("schema") or "") != "ETK-RADIO-DEBRIEF v1":
        errs.append("schema is not 'ETK-RADIO-DEBRIEF v1'")
    for fld, cap in (("radio", RADIO_MAX), ("headline", HEADLINE_MAX)):
        val = debrief.get(fld)
        if not isinstance(val, str):
            errs.append("%s is not a string" % fld)
            continue
        if len(val) > cap:
            errs.append("%s is %d chars (max %d)" % (fld, len(val), cap))
        if not val.isascii():
            errs.append("%s is not ASCII" % fld)
    if not isinstance(debrief.get("tags"), list):
        errs.append("tags is not a list")
    for i, f in enumerate(debrief.get("findings") or []):
        if not isinstance(f, dict):
            errs.append("findings[%d] is not an object" % i)
            continue
        if f.get("kind") not in FINDING_KINDS:
            errs.append("findings[%d].kind %r not in %s" % (i, f.get("kind"), sorted(FINDING_KINDS)))
        if not isinstance(f.get("text"), str):
            errs.append("findings[%d].text is not a string" % i)
        if not isinstance(f.get("evidence"), list) or not f.get("evidence"):
            errs.append("findings[%d] carries no evidence" % i)
    for i, r in enumerate(debrief.get("recommendations") or []):
        if not isinstance(r, dict):
            errs.append("recommendations[%d] is not an object" % i)
            continue
        if r.get("kind") not in REC_KINDS:
            errs.append("recommendations[%d].kind %r not in %s" % (i, r.get("kind"), sorted(REC_KINDS)))
        if not isinstance(r.get("text"), str):
            errs.append("recommendations[%d].text is not a string" % i)
        for j, ch in enumerate(r.get("config_changes") or []):
            if not isinstance(ch, dict) or "yaml_key" not in ch or "new_value" not in ch:
                errs.append("recommendations[%d].config_changes[%d] needs yaml_key + new_value" % (i, j))
    g = debrief.get("guards")
    if not isinstance(g, dict) or "passed" not in g or not isinstance(g.get("dropped"), list):
        errs.append("guards needs {passed, dropped[]}")
    return errs


# ----------------------------------------------------------------------------- ops
def op_tags_include(op, case, d):
    have, computed = set(d.get("tags") or []), set(compute_tags(case["pack"]))
    miss = [t for t in op["tags"] if t not in have]
    if not miss:
        return True, "tags carry %s" % ", ".join(op["tags"])
    note = [t for t in miss if t in computed]
    detail = "debrief is missing %s" % ", ".join(miss)
    if note:
        detail += " (the pack computes %s)" % ", ".join(note)
    return False, detail


def op_tags_exclude(op, case, d):
    have = set(d.get("tags") or [])
    hit = [t for t in op["tags"] if t in have]
    return (not hit), ("carries forbidden tag %s" % ", ".join(hit) if hit else "no forbidden tag")


def op_finding_kinds_exclude(op, case, d):
    hit = [f.get("kind") for f in (d.get("findings") or []) if f.get("kind") in set(op["kinds"])]
    return (not hit), ("finding kind %s present" % ", ".join(sorted(set(hit))) if hit else "clear")


def op_finding_kind_present(op, case, d):
    kinds = [f.get("kind") for f in (d.get("findings") or [])]
    ok = op["kind"] in kinds
    return ok, ("present" if ok else "no finding of kind %r (have %s)" % (op["kind"], kinds))


def op_recommendation_kinds_subset(op, case, d):
    allowed = set(op["kinds"])
    kinds = [r.get("kind") for r in (d.get("recommendations") or [])]
    bad = [k for k in kinds if k not in allowed]
    return (not bad), ("recommendation kind %s outside %s" % (sorted(set(bad)), sorted(allowed))
                       if bad else "kinds %s within %s" % (kinds, sorted(allowed)))


def op_recommendation_kind_present(op, case, d):
    kinds = [r.get("kind") for r in (d.get("recommendations") or [])]
    ok = op["kind"] in kinds
    return ok, ("present" if ok else "no %r recommendation (have %s)" % (op["kind"], kinds))


def op_recommendation_kind_first(op, case, d):
    kinds = [r.get("kind") for r in (d.get("recommendations") or [])]
    ok = bool(kinds) and kinds[0] == op["kind"]
    return ok, ("%r leads" % op["kind"] if ok else "first recommendation is %r, wanted %r"
                % (kinds[0] if kinds else None, op["kind"]))


def _key_eq(a, b):
    return str(a).strip() == str(b).strip()


def op_config_key_absent(op, case, d):
    hit = [rec.get("kind") for rec, ch in _config_changes(d) if _key_eq(ch.get("yaml_key"), op["yaml_key"])]
    return (not hit), ("proposes %r (in a %s recommendation)" % (op["yaml_key"], hit[0])
                       if hit else "%r not proposed" % op["yaml_key"])


def op_config_no_decrease(op, case, d):
    floor = op.get("below")
    if floor is None:
        floor = pack_config_value(case["pack"], op["yaml_key"])
    fl = _num(floor)
    if fl is None:
        return False, "no session value for %r in the pack; the op cannot be decided" % op["yaml_key"]
    for rec, ch in _config_changes(d):
        if not _key_eq(ch.get("yaml_key"), op["yaml_key"]):
            continue
        nv = _num(ch.get("new_value"))
        if nv is None:
            return False, "%r set to a non-numeric %r" % (op["yaml_key"], ch.get("new_value"))
        if nv < fl:
            return False, "%r lowered to %s (session value %s) in a %r recommendation" % (
                op["yaml_key"], ch.get("new_value"), floor, rec.get("kind"))
    return True, "%r never taken below %s" % (op["yaml_key"], floor)


def op_text_forbids(op, case, d):
    rx = re.compile(op["regex"], re.I | re.S)
    for t in _texts(d, op.get("fields") or list(TEXT_FIELDS)):
        m = rx.search(t)
        if m:
            return False, "forbidden phrase %r in %r" % (m.group(0), t[:90])
    return True, "no forbidden phrase"


def op_text_requires(op, case, d):
    rx = re.compile(op["regex"], re.I | re.S)
    for t in _texts(d, op.get("fields") or list(TEXT_FIELDS)):
        if rx.search(t):
            return True, "required phrase found"
    return False, "nothing matches /%s/ in %s" % (op["regex"], op.get("fields") or list(TEXT_FIELDS))


def op_evidence_source_present(op, case, d):
    srcs = [str((ev or {}).get("source") or "")
            for f in (d.get("findings") or []) for ev in (f.get("evidence") or [])]
    ok = op["source"] in srcs
    return ok, ("cited" if ok else "no evidence with source %r (have %s)" % (op["source"], sorted(set(srcs))))


def op_comparison_needs_n(op, case, d):
    for i, f in enumerate(d.get("findings") or []):
        text = str(f.get("text") or "")
        if not COMPARISON_RE.search(text):
            continue
        ns = _evidence_ns(f)
        good = [n for n in ns if n >= 3]
        if len(good) < 2:
            return False, ("findings[%d] compares arms but cites N %s - the guard needs two "
                           "arms at N>=3: %r" % (i, ns or "nowhere", text[:90]))
    return True, "every comparison carries two arms at N>=3"


def op_guards_dropped_mentions(op, case, d):
    rx = re.compile(op["regex"], re.I | re.S)
    dropped = (d.get("guards") or {}).get("dropped") or []
    blob = json.dumps(dropped, ensure_ascii=True)
    ok = bool(rx.search(blob))
    return ok, ("guards.dropped names it" if ok else "guards.dropped %s does not match /%s/"
                % (blob[:120], op["regex"]))


def op_guards_passed(op, case, d):
    want = bool(op.get("value", True))
    got = (d.get("guards") or {}).get("passed")
    return (got is want), "guards.passed=%r, wanted %r" % (got, want)


def op_schema_valid(op, case, d):
    validator = _load_agent_a_validator()
    errs = validator(d) if validator else debrief_shape_errors(d)
    who = "tools/radio/schemas.py" if validator else "built-in shape check"
    return (not errs), ("valid (%s)" % who if not errs else "%s: %s" % (who, "; ".join(errs[:3])))


OPS = {
    "tags_include": (op_tags_include, ("tags",)),
    "tags_exclude": (op_tags_exclude, ("tags",)),
    "finding_kinds_exclude": (op_finding_kinds_exclude, ("kinds",)),
    "finding_kind_present": (op_finding_kind_present, ("kind",)),
    "recommendation_kinds_subset": (op_recommendation_kinds_subset, ("kinds",)),
    "recommendation_kind_present": (op_recommendation_kind_present, ("kind",)),
    "recommendation_kind_first": (op_recommendation_kind_first, ("kind",)),
    "config_key_absent": (op_config_key_absent, ("yaml_key",)),
    "config_no_decrease": (op_config_no_decrease, ("yaml_key",)),
    "text_forbids": (op_text_forbids, ("regex",)),
    "text_requires": (op_text_requires, ("regex",)),
    "evidence_source_present": (op_evidence_source_present, ("source",)),
    "comparison_needs_n": (op_comparison_needs_n, ()),
    "guards_dropped_mentions": (op_guards_dropped_mentions, ("regex",)),
    "guards_passed": (op_guards_passed, ()),
    "schema_valid": (op_schema_valid, ()),
}

# "why" is a free-text note kept beside an assertion for the human reader; ignored here.
OPTIONAL_KEYS = {"why", "fields", "below", "value", "kind", "kinds", "tags", "regex",
                 "source", "yaml_key"}


def op_label(op):
    name = op.get("op")
    for k in ("tags", "kinds", "kind", "yaml_key", "source", "regex", "value"):
        if k in op:
            v = op[k]
            v = ",".join(map(str, v)) if isinstance(v, list) else str(v)
            return "%s(%s)" % (name, v if len(v) < 46 else v[:43] + "...")
    return "%s()" % name


def op_errors(op):
    if not isinstance(op, dict) or "op" not in op:
        return ["not an object with an 'op' key: %r" % (op,)]
    name = op["op"]
    if name not in OPS:
        return ["unknown op %r" % name]
    _, required = OPS[name]
    errs = ["%s: missing %r" % (name, k) for k in required if k not in op]
    extra = set(op) - {"op"} - OPTIONAL_KEYS
    if extra:
        errs.append("%s: unknown keys %s" % (name, sorted(extra)))
    return errs


# --------------------------------------------------------------------------- cases
def load_cases(only=None):
    cases = []
    for cid in CASE_IDS:
        path = os.path.join(CASE_DIR, cid + ".json")
        if only and cid not in only:
            continue
        if not os.path.exists(path):
            sys.exit("eval: missing case file %s" % path)
        with open(path, encoding="utf-8") as fh:
            case = json.load(fh)
        case["_path"] = path
        cases.append(case)
    return cases


def grade(case, debrief):
    """-> [(op, passed, detail)] for every assertion in the case."""
    out = []
    for op in case.get("expect") or []:
        errs = op_errors(op)
        if errs:
            out.append((op, False, "malformed assertion: " + "; ".join(errs)))
            continue
        fn, _ = OPS[op["op"]]
        try:
            ok, detail = fn(op, case, debrief if isinstance(debrief, dict) else {})
        except Exception as exc:                                  # a broken debrief is a fail, not a crash
            ok, detail = False, "%s while grading: %s" % (type(exc).__name__, exc)
        out.append((op, bool(ok), detail))
    return out


# ------------------------------------------------------------------------ commands
def cmd_list():
    for c in load_cases():
        src = c.get("source") or {}
        print("%-20s %-10s epoch=%-12s %d assertions" % (
            c["id"], src.get("kind"), src.get("epoch"), len(c.get("expect") or [])))
        print("    from : %s" % c.get("from"))
        print("    tags : %s" % (", ".join(compute_tags(c["pack"])) or "-"))
        print("    ops  : %s" % ", ".join(op_label(o) for o in (c.get("expect") or [])))
    return 0


def cmd_selftest(only=None):
    cases = load_cases(only)
    bad = good_e = good_c = 0
    print("case                  exemplar  counter   assertions")
    for c in cases:
        ge = grade(c, c["exemplar"])
        gc = grade(c, c["counter"])
        fe = [(op_label(o), d) for o, ok, d in ge if not ok]
        fc = [op_label(o) for o, ok, _ in gc if not ok]
        ok_e, ok_c = (not fe), bool(fc)
        good_e += ok_e
        good_c += ok_c
        flag = "ok " if (ok_e and ok_c) else "BAD"
        bad += flag == "BAD"
        print("[%s] %-20s %-9s %-9s %d" % (
            flag, c["id"],
            "pass" if ok_e else "FAIL",
            ("fails:" + str(len(fc))) if ok_c else "PASSES!",
            len(ge)))
        for label, detail in fe:
            print("       exemplar FAILED %s -> %s" % (label, detail))
        if not ok_c:
            print("       counter passed every assertion - the case does not discriminate")
        elif os.environ.get("ETK_EVAL_VERBOSE"):
            print("       counter caught by: %s" % ", ".join(fc))
    n = len(cases)
    print("\nSELFTEST %d/%d exemplars pass, %d/%d counters fail (%d/%d discriminating) -> %s"
          % (good_e, n, good_c, n, good_e + good_c, 2 * n, "FAIL" if bad else "PASS"))
    return 1 if bad else 0


def cmd_debriefs(dirpath, only=None):
    cases = load_cases(only)
    lines, total_ok = [], 0
    print("scoring %d cases against %s" % (len(cases), dirpath))
    for c in cases:
        path = os.path.join(dirpath, c["id"] + ".json")
        if not os.path.exists(path):
            print("[----] %-20s no debrief at %s" % (c["id"], path))
            lines.append("| `%s` | MISSING | no debrief file |" % c["id"])
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (ValueError, OSError) as exc:
            print("[FAIL] %-20s unreadable: %s" % (c["id"], exc))
            lines.append("| `%s` | FAIL | unreadable: %s |" % (c["id"], exc))
            continue
        res = grade(c, d)
        fails = [(op_label(o), det) for o, ok, det in res if not ok]
        total_ok += not fails
        print("[%s] %-20s %d/%d" % ("PASS" if not fails else "FAIL", c["id"],
                                    len(res) - len(fails), len(res)))
        for label, det in fails:
            print("       %s -> %s" % (label, det))
        lines.append("| `%s` | %s | %s |" % (
            c["id"], "PASS" if not fails else "FAIL",
            "; ".join("`%s`" % l for l, _ in fails) or "-"))
    print("\n%d/%d cases pass every assertion" % (total_ok, len(cases)))
    out = os.path.join(dirpath, "scorecard.md")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("# RADIO eval scorecard\n\n")
            fh.write("Source: `%s`  ---  %d/%d cases pass every assertion.\n\n" % (
                dirpath, total_ok, len(cases)))
            fh.write("Deterministic assertions only. The blind read (model vs rules-only,\n"
                     "more useful / same / worse) is the human column and is not scored here;\n"
                     "spec 10.3 ships the model only at >= 8 of 12 more-useful with zero\n"
                     "assertion failures.\n\n")
            fh.write("| case | result | failing assertions |\n|---|---|---|\n")
            fh.write("\n".join(lines) + "\n")
        print("wrote %s" % out)
    except OSError as exc:
        print("could not write %s: %s" % (out, exc))
    return 0 if total_ok == len(cases) else 1


def _git_rev():
    try:
        r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def cmd_refresh():
    if not os.path.isdir(MIRROR):
        print("no telemetry mirror at %s - nothing to refresh (this is fine off-host)" % MIRROR)
        return 0
    if not os.path.exists(PACKER):
        print("no packer at %s - nothing to refresh" % PACKER)
        return 0
    rev, changed = _git_rev(), 0
    for c in load_cases():
        src = c.get("source") or {}
        if src.get("kind") != "ledger" or not src.get("epoch"):
            print("[skip] %-20s %s" % (c["id"], src.get("kind")))
            continue
        cmd = [sys.executable, PACKER, str(src["epoch"]), "--stdout"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not r.stdout.strip():
            print("[FAIL] %-20s packer rc=%d %s" % (c["id"], r.returncode, r.stderr.strip()[:120]))
            continue
        pack = normalize_pack(json.loads(r.stdout))
        if pack.get("epoch") != src["epoch"]:
            print("[FAIL] %-20s packer returned epoch %s" % (c["id"], pack.get("epoch")))
            continue
        c["pack"] = pack
        c["source"]["generated_by"] = "bin/radio_pack.py %d --stdout @ %s" % (src["epoch"], rev)
        body = {k: v for k, v in c.items() if not k.startswith("_")}
        with open(c["_path"], "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=1, ensure_ascii=True, sort_keys=False)
            fh.write("\n")
        changed += 1
        print("[ok  ] %-20s epoch %s, %d bytes, tags %s" % (
            c["id"], src["epoch"], len(json.dumps(pack)), ", ".join(compute_tags(pack)) or "-"))
    print("\nrefreshed %d ledger-sourced packs @ %s (synthetic packs untouched)" % (changed, rev))
    return 0


def main():
    ap = argparse.ArgumentParser(description="ETK RADIO eval - golden cases and assertions")
    ap.add_argument("--list", action="store_true", help="the cases, their sources and assertions")
    ap.add_argument("--selftest", action="store_true",
                    help="every exemplar must pass all assertions, every counter must fail one")
    ap.add_argument("--debriefs", metavar="DIR",
                    help="grade <case_id>.json debriefs in DIR; writes DIR/scorecard.md")
    ap.add_argument("--refresh-packs", action="store_true",
                    help="regenerate ledger-sourced packs from state/etk_telemetry/")
    ap.add_argument("--case", action="append", metavar="ID", help="restrict to this case (repeatable)")
    args = ap.parse_args()
    only = set(args.case) if args.case else None
    if only:
        unknown = only - set(CASE_IDS)
        if unknown:
            sys.exit("eval: unknown case(s): %s" % ", ".join(sorted(unknown)))
    if args.list:
        sys.exit(cmd_list())
    if args.selftest:
        sys.exit(cmd_selftest(only))
    if args.debriefs:
        sys.exit(cmd_debriefs(args.debriefs, only))
    if args.refresh_packs:
        sys.exit(cmd_refresh())
    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
