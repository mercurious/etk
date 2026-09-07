#!/usr/bin/env python3
"""ETK RADIO -- host-side regression tests for the guards, the tags and the
rules-only debrief.

Run from the repo root:   python3 tools/radio/test_guards.py

House discipline (tools/radio/exam.py, tools/radio/eval.py, tools/test_paddock.py):
EVERY check needs an exemplar that PASSES untouched and a counter that FAILS -- a guard
that leaves a wrong debrief alone is worse than no guard, because the surface then shows
a wrong crown with a green tick beside it. Each of the eleven gets both here.

Sections:

  [1] tags parity          tags.compute == eval.compute_tags on all 12 fixture packs,
                           and the comparison regex the crown guard uses is the same
                           pattern the eval grades with
  [2] the eleven guards    exemplar untouched / counter corrected, one pair per guard
  [3] falsified.json       loads, every entry is anchored and matchable, and naming an
                           item in order to REFUSE it never fires the guard
  [4] the 12/12 bar        rules_only.build over every fixture pack: valid DEBRIEF v1,
                           and eval.grade green on every case (spec 10's rules-only bar)
  [5] headline()           <= 60 printable ASCII on every fixture
  [6] render()             printable ASCII, <= 80 columns, on every fixture
  [7] hallucinated_key     the case's own model_output through guards.apply: the
                           foreign key and the range miss both go, the debrief stays
                           valid, and the dropped prescription does not survive in the
                           prose either

Case 12 (`hallucinated_key`) is the eval's SERVICE case: its debrief is a model answer
put through the guards (the case ships `model_output` for exactly that), so section [4]
grades it that way and section [7] pins the behaviour. The other eleven are graded on
rules_only.build alone.

Stdlib only, no network, no rig, no node.
"""
import copy
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import guards                                                           # noqa: E402
import rules_only                                                       # noqa: E402
import schemas                                                          # noqa: E402
import tags as tagmod                                                   # noqa: E402

FAILS = []


def check(name, got, want):
    if got == want:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s\n          got  %r\n          want %r" % (name, got, want))
        FAILS.append(name)


def section(title):
    print("\n[%s]" % title)


def load_eval():
    """eval.py is agent E's file and is imported lazily, by path, exactly the way it
    imports schemas.py -- so this test still runs if it is mid-edit."""
    spec = importlib.util.spec_from_file_location("etk_radio_eval",
                                                  os.path.join(HERE, "eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EV = load_eval()
CASES = {c["id"]: c for c in EV.load_cases()}
PACKS = {cid: c["pack"] for cid, c in CASES.items()}
SCHEMA = schemas.load("debrief.v1")


def valid(debrief):
    return schemas.validate(debrief, SCHEMA)


def base(pack, **over):
    """A minimal VALID debrief for a pack: the frame every synthetic case hangs on."""
    d = {
        "schema": "ETK-RADIO-DEBRIEF v1",
        "epoch": int(pack.get("epoch") or 0),
        "game_id": str(pack.get("game_id") or "unknown"),
        "model": "test:0b",
        "prompt_sha256": "0" * 64,
        "corpus_commit": "0" * 7,
        "tokens": {"prompt": 100, "completion": 10},
        "latency_s": 1.0,
        "radio": "A test debrief.",
        "headline": "a test headline",
        "tags": [],
        "findings": [],
        "recommendations": [],
        "run_sheet": None,
        "guards": {"passed": True, "dropped": []},
    }
    d.update(over)
    return d


def rec(kind, text, **over):
    r = {"kind": kind, "text": text, "config_changes": [], "driver_dial": None,
         "confidence": "medium"}
    r.update(over)
    return r


def dropped_kinds(debrief):
    return [d["kind"] for d in (debrief.get("guards") or {}).get("dropped") or []]


def dropped_blob(debrief):
    return json.dumps((debrief.get("guards") or {}).get("dropped") or []).lower()


# ===================================================================== [1] tags
section("1] tags: the node's computed tags are the eval's computed tags")
for cid in EV.CASE_IDS:
    pack = PACKS[cid]
    check("tags.compute == eval.compute_tags on %s" % cid,
          tagmod.compute(pack), EV.compute_tags(pack))
check("session_arms agrees on all 12",
      [len(tagmod.session_arms(PACKS[c])) for c in EV.CASE_IDS],
      [len(EV.session_arms(PACKS[c])) for c in EV.CASE_IDS])
check("the crown guard reads comparisons with the eval's own regex",
      guards.COMPARISON_RE.pattern, EV.COMPARISON_RE.pattern)
check("the crown guard reads cited N with the eval's own regex",
      guards.N_FIELD_RE.pattern, EV.N_FIELD_RE.pattern)
check("every tag tags.py can emit is one the schema accepts",
      sorted(set(t for c in EV.CASE_IDS for t in tagmod.compute(PACKS[c]))
             - set(tagmod.TAGS)), [])


# =============================================================== [2] the eleven
section("2] the eleven guards: an exemplar passes untouched, a counter is corrected")
GUARD_IDS = [g[0] for g in guards.GUARDS]
check("GUARDS is the spec 6 table, in order", GUARD_IDS, [
    "no_crown_below_n", "never_repropose_falsified", "schema_vocabulary_only",
    "resolution_is_not_a_kpi_lever", "bake_and_aborted_are_not_feel_evidence",
    "attribution_before_narrative", "data_never_commands", "ascii_surfaces",
    "evidence_beside_every_claim", "never_truncate_silently",
    "diagnosis_is_not_prescription"])
check("every guard names the law it enforces",
      [gid for gid, law, fn in guards.GUARDS if not law or not fn.__doc__], [])

# --- 1. no crown below N ----------------------------------------------------------
SD = PACKS["sddepth_verdict"]
crown = {"kind": "mechanism",
         "text": "The sddepth arm cuts the wedge rate by 4-5x against the default arm.",
         "evidence": [{"source": "dyno", "field": "arms.10.n", "value": 5},
                      {"source": "dyno", "field": "arms.9.n", "value": 35}]}
out = guards.apply(base(SD, findings=[copy.deepcopy(crown)]), SD)
check("no_crown_below_n: a crown with two arms at N>=3 stands",
      (out["findings"][0]["kind"], "no_crown_below_n" in dropped_kinds(out)),
      ("mechanism", False))
weak = copy.deepcopy(crown)
weak["evidence"] = [{"source": "dyno", "field": "arms.10.n", "value": 2}]
out = guards.apply(base(SD, findings=[weak]), SD)
check("no_crown_below_n: a crown citing N=2 is demoted and tagged",
      (out["findings"][0]["kind"], "low_n" in out["tags"],
       "no_crown_below_n" in dropped_kinds(out)), ("observation", True, True))
invented = copy.deepcopy(crown)
invented["evidence"] = [{"source": "dyno", "field": "arms.10.n", "value": 44},
                        {"source": "dyno", "field": "arms.9.n", "value": 55}]
out = guards.apply(base(SD, findings=[invented]), SD)
check("no_crown_below_n: N the pack's dyno table does not carry is not evidence",
      out["findings"][0]["kind"], "observation")

# --- 2. never re-propose section F ------------------------------------------------
ZL = PACKS["zlatez_925_lown"]
legal = rec("config", "Give the driver a longer wake-up window.",
            config_changes=[{"yaml_key": "  Driver Wake-Up Delay", "new_value": 50}])
out = guards.apply(base(ZL, recommendations=[copy.deepcopy(legal)]), ZL)
check("never_repropose_falsified: a live knob is left alone",
      (len(out["recommendations"]), out["guards"]["passed"]), (1, True))
banned = rec("config", "Set a Thread Scheduler mode for the render thread.",
             config_changes=[{"yaml_key": "  Thread Scheduler", "new_value": "OS"}])
out = guards.apply(base(ZL, recommendations=[banned]), ZL)
check("never_repropose_falsified: a Thread Scheduler proposal is dropped by name",
      (out["recommendations"], "thread_scheduler_on_arm" in dropped_blob(out)),
      ([], True))
out = guards.apply(base(ZL, recommendations=[
    rec("dial", "Switch the Adreno LSD dial to sysmem for this pack.",
        driver_dial="sysmem")]), ZL)
check("never_repropose_falsified: sysmem as a dial is dropped (it was never a verdict)",
      (out["recommendations"], "sysmem_as_a_verdict" in dropped_blob(out)), ([], True))

# --- 3. schema vocabulary only ----------------------------------------------------
out = guards.apply(base(ZL, recommendations=[copy.deepcopy(legal)]), ZL)
check("schema_vocabulary_only: an in-schema key on its step survives",
      out["recommendations"][0]["config_changes"],
      [{"yaml_key": "  Driver Wake-Up Delay", "new_value": 50}])
for label, change, needle in (
        ("a foreign key", {"yaml_key": "  Shader Cache Depth", "new_value": "8"},
         "not in pitstop_fields.json"),
        ("an out-of-options value", {"yaml_key": "  Resolution Scale",
                                     "new_value": "125"}, "options"),
        ("an off-step int", {"yaml_key": "  Driver Wake-Up Delay", "new_value": 37},
         "step")):
    out = guards.apply(base(ZL, recommendations=[
        rec("config", "A change.", config_changes=[change])]), ZL)
    check("schema_vocabulary_only: %s is dropped, and so is the empty rec" % label,
          (out["recommendations"], needle in dropped_blob(out)), ([], True))

# --- 4. resolution is not a KPI lever ---------------------------------------------
res_drop = rec("config", "Bring the render scale down.",
               config_changes=[{"yaml_key": "  Resolution Scale", "new_value": "66"}])
out = guards.apply(base(ZL, recommendations=[copy.deepcopy(res_drop)]), ZL)
check("resolution_is_not_a_kpi_lever: allowed under a signature that lists it, tagged",
      (len(out["recommendations"]), "crash_net" in out["tags"]), (1, True))
# a CLEAN row with no crash signature at all, warmed so the bake guard stays out of it
NOSIG = copy.deepcopy(PACKS["audio_from_aud_cell"])
NOSIG["session"]["shaders_harvested"] = 0
check("the no-signature fixture is warm and uncrowned", tagmod.compute(NOSIG), ["low_n"])
out = guards.apply(base(NOSIG, recommendations=[copy.deepcopy(res_drop)]), NOSIG)
check("resolution_is_not_a_kpi_lever: with no signature on the row it is cheating",
      (out["recommendations"], "resolution_is_not_a_kpi_lever" in dropped_kinds(out)),
      ([], True))
up = rec("config", "More pixels.",
         config_changes=[{"yaml_key": "  Resolution Scale", "new_value": "100"}])
out = guards.apply(base(NOSIG, recommendations=[up]), NOSIG)
check("resolution_is_not_a_kpi_lever: it only ever guards the way DOWN",
      len(out["recommendations"]), 1)

# --- 5. bake and ABORTED are not feel evidence ------------------------------------
fps_claim = {"kind": "observation",
             "text": "The fps median held at 29.8 all run, so the frame rate is fine.",
             "evidence": [{"source": "ledger", "field": "fps_med"}]}
out = guards.apply(base(ZL, findings=[copy.deepcopy(fps_claim)],
                        recommendations=[copy.deepcopy(legal)]), ZL)
check("bake_and_aborted: on a WARM row a speed claim is nobody's business here",
      (len(out["findings"]), len(out["recommendations"])), (1, 1))
BK = PACKS["bake_session_fps"]
out = guards.apply(base(BK, findings=[copy.deepcopy(fps_claim)],
                        recommendations=[copy.deepcopy(legal),
                                         rec("next_run", "One more warm run.")]), BK)
check("bake_and_aborted: on a bake row the speed claim and the tune advice both go",
      (out["findings"], [r["kind"] for r in out["recommendations"]]),
      ([], ["next_run"]))
out = guards.apply(base(BK, recommendations=[rec("investigate", "Check our own code.")]),
                   BK)
check("bake_and_aborted: an investigate survives a bake row (attribution outranks)",
      [r["kind"] for r in out["recommendations"]], ["investigate"])

# --- 6. attribution before narrative ----------------------------------------------
KA = PACKS["keepalive_off_day"]
ordered = [rec("investigate", "Check the keepalive is armed on this boot."),
           rec("next_run", "Then one more warm run.")]
out = guards.apply(base(KA, recommendations=copy.deepcopy(ordered)), KA)
check("attribution_before_narrative: investigate already leading is left alone",
      ([r["kind"] for r in out["recommendations"]],
       "attribution_before_narrative" in dropped_kinds(out)),
      (["investigate", "next_run"], False))
out = guards.apply(base(KA, recommendations=[rec("next_run", "Just race it again.")]),
                   KA)
check("attribution_before_narrative: with none offered, one is put first",
      ([r["kind"] for r in out["recommendations"]][0],
       "attribution_before_narrative" in dropped_kinds(out)), ("investigate", True))
out = guards.apply(base(KA, recommendations=[
    rec("next_run", "Race it again."),
    rec("investigate", "Check the keepalive.")]), KA)
check("attribution_before_narrative: a buried investigate is moved to the front",
      [r["kind"] for r in out["recommendations"]], ["investigate", "next_run"])

# --- 7. data, never commands ------------------------------------------------------
clean = base(ZL, findings=[{"kind": "mechanism", "text": "A fence parked.",
                            "evidence": [{"source": "ledger",
                                          "field": "gpu_fault_status"}]}])
out = guards.apply(copy.deepcopy(clean), ZL)
check("data_never_commands: a debrief already inside the contract is untouched",
      (out["guards"]["passed"], valid(out)), (True, []))
smuggled = base(ZL)
smuggled["shell_command"] = "rm -rf /storage/roms/etk"
smuggled["findings"] = [{"kind": "verdict", "text": "A crown.", "evidence": [],
                         "run_this": "reboot"}]
out = guards.apply(smuggled, ZL)
check("data_never_commands: foreign keys are stripped and a bad kind is coerced",
      ("shell_command" in out, "run_this" in out["findings"][0],
       out["findings"][0]["kind"], valid(out)), (False, False, "observation", []))
out = guards.apply("not an object at all", ZL)
check("data_never_commands: a hopeless answer degrades to a valid debrief, not a crash",
      (valid(out), out["headline"], out["findings"],
       "not a json object" in dropped_blob(out)),
      ([], guards.pit_note(ZL), [], True))
check("data_never_commands: the minimal debrief is itself always valid",
      valid(guards.minimal(ZL, "because")), [])

# --- 8. ASCII surfaces ------------------------------------------------------------
out = guards.apply(base(ZL, radio="Plain ASCII, well inside the cap.",
                        headline="zlatez@925 N=2 of 3 - one more warm run"), ZL)
check("ascii_surfaces: an ASCII headline inside the cap is untouched",
      (out["headline"], out["guards"]["passed"]),
      ("zlatez@925 N=2 of 3 - one more warm run", True))
out = guards.apply(base(ZL, headline="the fence parked — " + "verylongword " * 8,
                        radio="café “quoted” → done"), ZL)
check("ascii_surfaces: non-ASCII is transliterated and the cap is a word boundary",
      (len(out["headline"]) <= 60, out["headline"].isascii(),
       out["headline"].endswith("verylongword"), out["radio"],
       "ascii_surfaces" in dropped_kinds(out)),
      (True, True, True, 'cafe "quoted" -> done', True))

# --- 9. evidence beside every claim -----------------------------------------------
cited = {"kind": "mechanism", "text": "The keepalive absorbed it three times.",
         "evidence": [{"source": "ledger", "field": "rescues", "value": 3}]}
out = guards.apply(base(ZL, findings=[copy.deepcopy(cited)]), ZL)
check("evidence_beside_every_claim: evidence that resolves keeps the mechanism",
      (out["findings"][0]["kind"], "uncited" in out["tags"]), ("mechanism", False))
check("evidence_beside_every_claim: the resolver prints the pack's own value",
      guards.resolve_evidence({"source": "ledger", "field": "rescues"}, ZL),
      (True, "ledger.rescues = 3"))
floating = {"kind": "mechanism", "text": "The unicorn count was high.",
            "evidence": [{"source": "ledger", "field": "unicorn_count", "value": 9}]}
out = guards.apply(base(ZL, findings=[floating]), ZL)
check("evidence_beside_every_claim: evidence that resolves to nothing is demoted",
      (out["findings"][0]["kind"], "uncited" in out["tags"],
       "evidence_beside_every_claim" in dropped_kinds(out)),
      ("observation", True, True))
check("evidence_beside_every_claim: an invented pack section never resolves",
      guards.resolve_evidence({"source": "wikipedia", "field": "n"}, ZL)[0], False)

# --- 10. never truncate silently --------------------------------------------------
out = guards.apply(base(ZL, tokens={"prompt": 3410, "completion": 402}), ZL,
                   num_ctx=8192)
check("never_truncate_silently: a prompt inside num_ctx is not a drop",
      "never_truncate_silently" in dropped_kinds(out), False)
out = guards.apply(base(ZL, tokens={"prompt": 9001, "completion": 402}), ZL,
                   num_ctx=8192)
check("never_truncate_silently: a prompt over num_ctx is recorded as over budget",
      ("never_truncate_silently" in dropped_kinds(out),
       "over budget" in dropped_blob(out)), (True, True))
out = guards.apply(base(ZL, tokens={"prompt": 9001, "completion": 402}), ZL)
check("never_truncate_silently: with no num_ctx passed there is nothing to judge",
      "never_truncate_silently" in dropped_kinds(out), False)

# --- 11. diagnosis is not prescription --------------------------------------------
diag = rec("dial", "The DRIVER tab is the lever for this class: Adreno LSD -> sddepth",
           driver_dial="Adreno LSD dial -> sddepth")
out = guards.apply(base(ZL, recommendations=[copy.deepcopy(diag)]), ZL)
check("diagnosis_is_not_prescription: a DRIVER-dial recommendation is stageable",
      (out["recommendations"][0].get("review_only"), out["guards"]["passed"]),
      (None, True))
overreach = rec("config", "Patch env.sh and restart the etk daemon before racing.",
                config_changes=[{"yaml_key": "  Driver Wake-Up Delay",
                                 "new_value": 50}])
out = guards.apply(base(ZL, recommendations=[overreach]), ZL)
check("diagnosis_is_not_prescription: kit internals keep the diagnosis, lose the hands",
      (out["recommendations"][0]["review_only"],
       out["recommendations"][0]["config_changes"],
       "diagnosis_is_not_prescription" in dropped_kinds(out)), (True, [], True))
check("diagnosis_is_not_prescription: the render says so in words",
      "engineer to review" in rules_only.render(out, ZL).lower(), True)


# ============================================================ [3] falsified.json
section("3] config/falsified.json: section F, machine-readable")
raw = json.load(open(os.path.join(ROOT, "config", "falsified.json"), encoding="utf-8"))
entries = guards.load_falsified()
check("falsified.json loads and carries entries", len(entries) >= 20, True)
check("guards.load_falsified reads the shipped document shape",
      [e["id"] for e in entries], [e["id"] for e in raw["entries"]])
bad_anchor = [e.get("id") for e in entries
              if "TRACK_MANUAL.md section F" not in (e.get("anchor") or "")]
check("every entry is anchored in TRACK_MANUAL.md section F", bad_anchor, [])
bad_match = [e.get("id") for e in entries
             if not set(e.get("match") or {}) & {"tu_debug", "yaml_key", "env",
                                                 "power", "text"}]
check("every entry carries at least one match key", bad_match, [])
check("every entry carries a one-sentence disproof",
      [e.get("id") for e in entries if not (e.get("disproof") or "").strip()], [])
check("every entry is ASCII (it reaches guards.dropped, which reaches a surface)",
      [e.get("id") for e in entries
       if not json.dumps(e, ensure_ascii=False).isascii()], [])
for want in ("ramoops_pstore", "autostart_mangohud_race", "spurs_ladder_for_audio",
             "thread_scheduler_on_arm", "noconstcheck", "max_map_count",
             "eviocsabs_trigger_cal", "grid_as_the_gt5p_pack_fix",
             "mangohud_as_teardown_murderer", "android_survives_as_a_solution",
             "non_latin1_hud_glyphs", "mako_image_rendering",
             "attract_mode_trials_for_crash_classes", "srm_on_disc_for_iso_stutter",
             "mako_has_no_progress_widget", "no_headless_install",
             "zfunc_theory_for_road_flicker", "fifo_combos_for_rr7",
             "smartctl_through_a_usb_card_reader", "sysmem_as_a_verdict",
             "boss_avoidance_rtalign", "boss_avoidance_ccu_cache_cap",
             "boss_avoidance_dsbypass_dsany", "boss_avoidance_patch1_wfi",
             "boss_avoidance_patch2_discriminator"):
    check("section F item %r is encoded" % want,
          want in [e["id"] for e in entries], True)

# The refute-not-propose rule: E's falsified_tempt exemplar names max_map_count in
# order to REFUSE it, and must sail straight through.
FT = PACKS["falsified_tempt"]
refuting = base(FT, findings=[{
    "kind": "observation",
    "text": "The note re-opens max_map_count and Thread Scheduler; both are falsified "
            "(manual section F) and stay retired.",
    "evidence": [{"source": "operator", "field": "note"}]}],
    recommendations=[rec("investigate", "Check the keepalive first.")])
out = guards.apply(refuting, FT)
check("refuting an item in a FINDING never fires the guard",
      ("never_repropose_falsified" in dropped_kinds(out), len(out["findings"])),
      (False, 1))
proposing = base(FT, recommendations=[
    rec("investigate", "Check the keepalive first."),
    rec("config", "Raise vm.max_map_count to 1048576 before the next race.")])
out = guards.apply(proposing, FT)
check("proposing the same item in a RECOMMENDATION does fire it",
      ("never_repropose_falsified" in dropped_kinds(out),
       [r["kind"] for r in out["recommendations"]]), (True, ["investigate"]))
ex = guards.apply(CASES["falsified_tempt"]["exemplar"], FT)
check("E's own falsified_tempt exemplar keeps every recommendation it offered",
      ("never_repropose_falsified" in dropped_kinds(ex),
       [r["kind"] for r in ex["recommendations"]]),
      (False, [r["kind"] for r in CASES["falsified_tempt"]["exemplar"]["recommendations"]]))


# ================================================== [4] the rules-only 12/12 bar
section("4] rules_only.build: DEBRIEF v1 on every fixture, and the spec 10 12/12 bar")
built, scored = {}, 0
for cid in EV.CASE_IDS:
    pack = PACKS[cid]
    case = CASES[cid]
    d = rules_only.build(pack)
    built[cid] = d
    errs = valid(d)
    check("rules_only.build validates against debrief.v1 on %s" % cid, errs, [])
    check("rules_only.build stamps the pipeline on %s" % cid,
          (d["model"], d["prompt_sha256"], d["tokens"]),
          ("rules-only", hashlib.sha256(b"rules-only:v1").hexdigest(),
           {"prompt": 0, "completion": 0}))
    graded = d if not case.get("model_output") else guards.apply(
        case["model_output"], pack)
    res = EV.grade(case, graded)
    bad = [(EV.op_label(o), det) for o, ok, det in res if not ok]
    scored += not bad
    print("       %-20s %-10s %d/%d assertions%s" % (
        cid, "rules-only" if not case.get("model_output") else "guarded-model",
        len(res) - len(bad), len(res),
        "" if not bad else "   <- " + "; ".join(l for l, _ in bad)))
    for label, det in bad:
        print("            %s -> %s" % (label, det))
check("the rules-only baseline scores 12/12 (spec 10.3: it IS the contract)",
      scored, len(EV.CASE_IDS))
check("rules_only never stages a config change (bytes-to-atoms stays the operator's)",
      [cid for cid, d in built.items()
       for r in d["recommendations"] if r.get("config_changes")], [])
check("rules_only proposes a run sheet only when there is one to extend",
      [cid for cid, d in built.items() if d["run_sheet"] is not None], [])
check("rules_only measures its own latency", [cid for cid, d in built.items()
                                              if not isinstance(d["latency_s"], float)],
      [])


# ================================================================ [5] headline()
section("5] headline(): the rig-side pit note, computed from dyno alone")
for cid in EV.CASE_IDS:
    h = rules_only.headline(PACKS[cid])
    check("headline is <= 60 printable ASCII on %s (%r)" % (cid, h),
          (len(h) <= 60, h.isascii(), "\n" not in h, bool(h.strip())),
          (True, True, True, True))
check("headline reads the way spec 6 writes it",
      rules_only.headline(PACKS["zlatez_925_lown"]),
      "zlatez@925 N=2 of 3 - one more warm run")
check("headline leads with the attribution question when the keepalive is missing",
      rules_only.headline(PACKS["keepalive_off_day"]),
      "fault with no rescue - check the keepalive first")
check("headline says what a silent panic is",
      rules_only.headline(PACKS["panic_silent"]),
      "PANIC - no lead-up in the tail; investigate first")
check("headline on a settled arm names its N",
      rules_only.headline(PACKS["sddepth_verdict"]), "SURVIVED - sddepth@800 at N=5")


# ================================================================== [6] render()
section("6] render(): 80 columns of printable ASCII, evidence under every claim")
for cid in EV.CASE_IDS:
    text = rules_only.render(built[cid], PACKS[cid])
    lines = text.split("\n")
    check("render is printable ASCII on %s" % cid,
          [ln for ln in lines if not ln.isascii()
           or any(ord(c) < 0x20 or ord(c) > 0x7E for c in ln)], [])
    check("render is <= 80 columns on %s" % cid,
          max(len(ln) for ln in lines) <= 80, True)
sample = rules_only.render(built["zlatez_925_lown"], PACKS["zlatez_925_lown"])
check("render quotes the radio line", 'RADIO: "' in sample, True)
check("render prints the resolved evidence line under the claim",
      "evidence crash.fault.status = 00E59485" in sample, True)
check("render prints the N basis beside the recommendation",
      "N=2 of 3" in sample, True)
check("render summarises the guards",
      "GUARDS" in sample and "dropped" in sample, True)
uncited = guards.apply(base(PACKS["zlatez_925_lown"], findings=[
    {"kind": "mechanism", "text": "The unicorn count was high.",
     "evidence": [{"source": "ledger", "field": "unicorn_count"}]}]),
    PACKS["zlatez_925_lown"])
check("render marks an unresolved citation instead of hiding it",
      "UNCITED" in rules_only.render(uncited, PACKS["zlatez_925_lown"]), True)


# ======================================================== [7] the service case
section("7] hallucinated_key: a model answer through the guards")
HK = CASES["hallucinated_key"]
out = guards.apply(HK["model_output"], HK["pack"])
keys = [c.get("yaml_key") for r in out["recommendations"]
        for c in (r.get("config_changes") or [])]
check("the foreign key is gone", "  Shader Cache Depth" in keys, False)
check("the out-of-range change is gone (dropped, never clamped)",
      "  Resolution Scale" in keys, False)
check("both drops are recorded with their reasons",
      ("shader cache depth" in dropped_blob(out),
       "resolution scale" in dropped_blob(out)), (True, True))
check("the debrief is still a valid DEBRIEF v1", valid(out), [])
prose = json.dumps({k: v for k, v in out.items() if k != "guards"}).lower()
check("the dropped prescription does not survive in the prose either",
      ("shader cache depth" in prose, "scale to 125" in prose), (False, False))
check("but guards.dropped names it, because a drop is recorded and never silent",
      "shader cache depth" in dropped_blob(out), True)
check("what is left still says something about the wedge",
      len(out["findings"]) >= 1, True)
check("guards.passed is honest about the correction", out["guards"]["passed"], False)


# The dir the eval scores: `python3 tools/radio/test_guards.py --write-debriefs DIR`
# then `python3 tools/radio/eval.py --debriefs DIR` reproduces the scorecard by hand.
if "--write-debriefs" in sys.argv:
    OUT = sys.argv[sys.argv.index("--write-debriefs") + 1]
    os.makedirs(OUT, exist_ok=True)
    for cid, case in CASES.items():
        d = guards.apply(case["model_output"], case["pack"]) \
            if case.get("model_output") else built[cid]
        with open(os.path.join(OUT, cid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1, ensure_ascii=True)
    print("\nwrote 12 debriefs to %s (score them with eval.py --debriefs)" % OUT)


print()
if FAILS:
    print("FAILED: %d check(s) -> %s" % (len(FAILS), FAILS[:12]))
    sys.exit(1)
print("ALL RADIO GUARD CHECKS PASSED")
