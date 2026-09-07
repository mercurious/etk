#!/usr/bin/env python3
"""ETK RADIO -- host-side regression tests for the briefing builder.

Run from the repo root:   python3 tools/radio/test_briefing.py

No network, no node, no rig: the twelve eval fixtures (tools/radio/eval_cases/*.json) are
real PACK v1 bundles and everything the briefing reads is in the repo, so this is a pure
function under test.

WHAT WOULD BREAK IF THESE WERE NOT HERE (each check exists for one of these):

  1. BUDGET. spec 12: every 1,000 tokens of evidence is a minute of prefill on the 9b, and
     the eval fails a case over 4,500 prompt tokens. The builder trims itself to fit; if a
     corpus edit (a longer manual bullet, a new crash signature) pushes a case over, the
     operator finds out here and not at minute eleven of a debrief.
  2. THE PREFIX CACHE. The doctrine must be the SAME BYTES on every call or Ollama
     re-prefills from scratch every time (18 tok/s cold against 82-118 cached). A per-pack
     substitution sneaking into the system message would cost minutes per debrief and
     nobody would see it in the output.
  3. prompt_sha256 IS THE TUNE_TAG OF ADVICE. It has to move when the evidence moves and
     hold still when it does not, or a debrief cannot be attributed to what it read.
  4. THE VOCABULARY IS THE GUARD'S CONTRACT. A yaml key that reaches the model but is not
     in pitstop_fields.json is a staged edit the TUNING injector will refuse -- or worse,
     a foreign key the operator is invited to apply. Check 6 also runs a NEGATIVE CONTROL:
     the same extractor over a briefing carrying an invented key must FAIL, or the check
     is decorative.
  5. ASCII. The toast, pit_note.txt and the HUD are ASCII/Latin-1 surfaces (glyph law,
     A.3). The manual is not ASCII, so an extraction path that forgets to transliterate
     ships tofu to the rig.
  6. ONE ESTIMATOR. exam.py, the eval and the service must count a prompt the same way, or
     "it fits" means different things in different rooms.
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
CASE_DIR = os.path.join(HERE, "eval_cases")

FAILS = []


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


briefing = _load("etk_briefing", os.path.join(HERE, "briefing.py"))
# exam.py is imported READ-ONLY and only to compare estimators (check 10). It is agent
# A's file and this test must never change its behaviour: nothing below calls anything
# but CHARS_PER_TOKEN and build_packet().
exam = _load("etk_exam", os.path.join(HERE, "exam.py"))


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label
          + ("" if ok else "   got %r, want %r" % (got, want)))
    if not ok:
        FAILS.append(label)
    return ok


def load_cases():
    out = []
    for name in sorted(os.listdir(CASE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(CASE_DIR, name), encoding="utf-8") as f:
                c = json.load(f)
            out.append((c["id"], c["pack"]))
    return out


CASES = load_cases()
FIELDS = json.load(open(os.path.join(ROOT, "config", "pitstop_fields.json"),
                        encoding="utf-8"))
SIG_IDS = {s["id"] for s in json.load(open(os.path.join(ROOT, "config",
                                                        "crash_signatures.json"),
                                           encoding="utf-8"))}
FIELD_KEYS = {f["yaml_key"] for f in FIELDS} | {f["yaml_key"].strip() for f in FIELDS}

print("ETK RADIO -- briefing builder tests")
print("cases: %d   corpus: %s" % (len(CASES), ROOT))

# --- [0] build them all, once -------------------------------------------------------
print("\n[0] every fixture builds")
BUILDS = {}
for cid, pack in CASES:
    try:
        BUILDS[cid] = briefing.build(pack, repo_root=ROOT)
    except Exception as e:  # noqa: BLE001 -- a build that raises is the failure
        print("  FAIL %s raised %r" % (cid, e))
        FAILS.append("build " + cid)
check("all 12 fixtures built", len(BUILDS), len(CASES))
if len(BUILDS) != len(CASES):
    print("\nFAILED early: %s" % FAILS)
    sys.exit(1)

# --- [1] the token table + the budget ------------------------------------------------
print("\n[1] token budget (spec 12: a case over 4,500 prompt tokens fails the eval)")
print("    %-22s %7s %8s %6s %7s %7s %s" %
      ("case", "system", "briefing", "pack", "TOTAL", "num_ctx", "trims"))
for cid, _ in CASES:
    b = BUILDS[cid]
    t = b["tokens"]
    trims = [s["id"] for s in b["sources"] if s["kind"] == "budget_trim"]
    print("    %-22s %7d %8d %6d %7d %7d %d" %
          (cid, t["system"], t["briefing"], t["pack"], t["total"], b["num_ctx"],
           len(trims)))
over = [cid for cid in BUILDS if BUILDS[cid]["tokens"]["total"] > briefing.TOKEN_BUDGET]
check("no case over the %d-token budget" % briefing.TOKEN_BUDGET, over, [])
check("tokens.total is system + user, not a guess",
      all(BUILDS[c]["tokens"]["total"] ==
          briefing.estimate_tokens(BUILDS[c]["system"])
          + briefing.estimate_tokens(BUILDS[c]["user"]) for c in BUILDS), True)
check("num_ctx is 8192 while the prompt fits it",
      sorted({BUILDS[c]["num_ctx"] for c in BUILDS}), [briefing.NUM_CTX_DEFAULT])

# --- [2] the doctrine is byte-identical every call (the prefix cache) ----------------
print("\n[2] the system prompt is the same bytes on every build")
systems = {BUILDS[c]["system"] for c in BUILDS}
check("one distinct system message across 12 builds", len(systems), 1)
disk = open(os.path.join(HERE, "prompts", "engineer.md"), encoding="utf-8").read()
check("it is prompts/engineer.md verbatim", systems.pop(), disk)
check("load_system() agrees", briefing.load_system(repo_root=ROOT), disk)

# --- [3] prompt_sha256 moves with the pack and only with the pack --------------------
print("\n[3] prompt_sha256 -- the tune_tag of advice")
shas = {c: BUILDS[c]["prompt_sha256"] for c in BUILDS}
check("all 12 packs hash differently", len(set(shas.values())), 12)
again = briefing.build(dict(CASES[0][1]), repo_root=ROOT)
check("same pack, same hash", again["prompt_sha256"], shas[CASES[0][0]])
mutated = json.loads(json.dumps(CASES[0][1]))
mutated["session"]["rescues"] = (mutated["session"].get("rescues") or 0) + 7
check("a changed ledger cell changes the hash",
      briefing.build(mutated, repo_root=ROOT)["prompt_sha256"] != shas[CASES[0][0]], True)
check("every hash is 64 hex chars",
      all(re.fullmatch(r"[0-9a-f]{64}", s) for s in shas.values()), True)

# --- [4] the crash cases carry their signature; the audio case carries the aud cell ---
print("\n[4] selection is driven by the pack's own fields")
for cid, pack in CASES:
    want = [s for s in (pack.get("session", {}).get("crash_sig") or []) if s in SIG_IDS]
    if not want:
        continue
    got = [s["id"] for s in BUILDS[cid]["sources"] if s["kind"] == "crash_signature"]
    check("%s selects its signatures %s" % (cid, want), sorted(set(got)), sorted(set(want)))
aud_src = [s["id"] for s in BUILDS["audio_from_aud_cell"]["sources"]
           if s["kind"] == "keyword" and s["id"].startswith("audio")]
check("audio_from_aud_cell's audio keyword names the aud cell",
      bool(aud_src) and "aud." in aud_src[0], True)
check("...and the briefing itself carries the aud counters",
      "skip" in json.dumps((dict(CASES)["audio_from_aud_cell"]
                            .get("session", {}).get("aud") or {}))
      and '"skip":116' in BUILDS["audio_from_aud_cell"]["user"].replace(" ", ""), True)

# --- [5] the mechanism bullets are the ones the row is about --------------------------
print("\n[5] section 2.4 bullets, chosen by keyword")
ka = [s["id"] for s in BUILDS["keepalive_off_day"]["sources"]
      if s["kind"] == "mechanism_bullet"]
check("keepalive_off_day pulls the anti-lock (fence/keepalive) bullet",
      bool(ka) and "Anti-Lock" in ka[0], True)
ka_kw = [s["id"] for s in BUILDS["keepalive_off_day"]["sources"] if s["kind"] == "keyword"]
check("...triggered by fence", any(k.startswith("fence") for k in ka_kw), True)
ps = [s["id"] for s in BUILDS["panic_silent"]["sources"] if s["kind"] == "mechanism_bullet"]
check("panic_silent pulls the Panic Black Box bullet first",
      bool(ps) and "Panic Black Box" in ps[0], True)
ps_kw = [s["id"] for s in BUILDS["panic_silent"]["sources"] if s["kind"] == "keyword"]
check("...triggered by panic", any(k.startswith("panic") for k in ps_kw), True)
# --- [5b] the computed tags come from tags.py, and the fallback has not drifted -------
print("\n[5b] COMPUTED TAGS")
tags_py = os.path.join(HERE, "tags.py")
src_note = [s["id"] for s in BUILDS["panic_silent"]["sources"]
            if s["kind"] == "computed_tags"]
check("panic_silent's tags carry panic_silent",
      "panic_silent" in (src_note[0] if src_note else ""), True)
check("keepalive_off_day's tags carry keepalive_absent",
      "keepalive_absent" in "".join(s["id"] for s in BUILDS["keepalive_off_day"]["sources"]
                                    if s["kind"] == "computed_tags"), True)
if os.path.exists(tags_py):
    tagmod = _load("etk_tags", tags_py)
    fn = getattr(tagmod, "compute", None) or getattr(tagmod, "compute_tags")
    check("the briefing says it used tags.py, not its own copy",
          [c for c in BUILDS if "[tags." not in BUILDS[c]["briefing"]], [])
    drift = {cid: (briefing._fallback_tags(pack), fn(pack))
             for cid, pack in CASES if briefing._fallback_tags(pack) != fn(pack)}
    check("the local fallback still agrees with tags.py on all 12", drift, {})
else:
    print("  --   tags.py not present yet; the fallback is in use (recorded in sources)")

check("never more than two bullets",
      max(len([s for s in BUILDS[c]["sources"] if s["kind"] == "mechanism_bullet"])
          for c in BUILDS) <= briefing.MECH_BULLETS, True)

# --- [6] the vocabulary can only ever name a real pitstop field ----------------------
print("\n[6] CONFIG VOCABULARY holds no key outside pitstop_fields.json")
VOCAB_LINE = re.compile(r"^- .* \| key '([^']*)' \|", re.M)


def vocab_keys(text):
    """The keys a briefing offers. Reads the rendered section, not the internals: what
    reaches the model is what matters."""
    i = text.find("== CONFIG VOCABULARY")
    if i < 0:
        return []
    j = text.find("\n== ", i + 1)
    return VOCAB_LINE.findall(text[i:j if j > 0 else len(text)])


bad = {}
for cid in BUILDS:
    keys = vocab_keys(BUILDS[cid]["briefing"])
    off = [k for k in keys if k not in FIELD_KEYS]
    if off:
        bad[cid] = off
check("no foreign key in any of the 12 briefings", bad, {})
check("the vocabulary is not empty on a crash case",
      len(vocab_keys(BUILDS["keepalive_off_day"]["briefing"])) > 0, True)
check("sources agree with the rendered section",
      sorted(k.strip() for k in vocab_keys(BUILDS["panic_silent"]["briefing"])),
      sorted(s["id"] for s in BUILDS["panic_silent"]["sources"]
             if s["kind"] == "config_field"))
# NEGATIVE CONTROL. Without this the check above passes on a broken extractor that finds
# no keys at all, or on a builder that renders the section in some other shape.
FAKE = ("== CONFIG VOCABULARY (the ONLY keys config_changes may name) ==\n"
        "- Made Up Knob | key '  Nonexistent Key' | int, range 0..1\n"
        "  now 1, template 0 [differs from template]\n"
        "  invented\n== NEXT ==\n")
check("negative control: the extractor DOES catch an invented key",
      [k for k in vocab_keys(FAKE) if k not in FIELD_KEYS], ["  Nonexistent Key"])
# And the builder must not launder one in from the pack's own config map either.
poisoned = json.loads(json.dumps(dict(CASES)["keepalive_off_day"]))
poisoned["config"]["values"]["  Nonexistent Key"] = "1"
poisoned["history"]["changes_since_last_debrief"].append(
    {"epoch": 1, "field": "Nonexistent Key", "old": "0", "new": "1"})
pb = briefing.build(poisoned, repo_root=ROOT)
check("a foreign key in the pack never reaches the vocabulary",
      [k for k in vocab_keys(pb["briefing"]) if k not in FIELD_KEYS], [])
check("...and the crash-suggested keys are still first",
      vocab_keys(pb["briefing"])[0].strip(), "Driver Wake-Up Delay")

# --- [7] the arms table carries N and LOW-N, and marks this session's arm -------------
print("\n[7] the dyno table")
kb = BUILDS["keepalive_off_day"]["briefing"]
check("the arms table names N", " N " in kb or "N " in kb.split("== DYNO ARMS")[1][:200],
      True)
check("this session's arm is marked",
      "  * " in kb.split("== DYNO ARMS")[1].split("== ")[0], True)
check("dyno.arms is replaced in the JSON, not duplicated",
      "see BRIEFING section DYNO ARMS" in BUILDS["keepalive_off_day"]["user"], True)
check("config.values is replaced in the JSON too",
      "see BRIEFING section CONFIG VOCABULARY" in BUILDS["keepalive_off_day"]["user"], True)

# --- [8] ASCII everywhere -------------------------------------------------------------
print("\n[8] ASCII on every surface")
non_ascii = {}
for cid in BUILDS:
    b = BUILDS[cid]
    blob = b["system"] + b["briefing"] + b["user"] + json.dumps(b["sources"])
    bad_chars = sorted({ch for ch in blob if ord(ch) > 127})
    if bad_chars:
        non_ascii[cid] = bad_chars
check("no non-ASCII byte in any built prompt", non_ascii, {})
for name in ("prompts/engineer.md", "Modelfile.debrief", "Modelfile.fast",
             "briefing.py", "test_briefing.py"):
    txt = open(os.path.join(HERE, name), encoding="utf-8").read()
    check("%s is ASCII" % name, txt.isascii(), True)
# The manual IS non-ASCII, so the transliteration is doing real work here, not nothing.
check("negative control: the manual really does contain non-ASCII",
      open(os.path.join(ROOT, "TRACK_MANUAL.md"), encoding="utf-8").read().isascii(), False)
check("to_ascii transliterates rather than dropping",
      briefing.to_ascii("N\u22653 \u2014 fence \u00b7 park"), "N>=3 - fence . park")

# --- [9] the doctrine says the load-bearing things ------------------------------------
print("\n[9] prompts/engineer.md carries the phrases the 2026-09-06 reviews needed")
DOCTRINE = disk
for phrase in ("4,194,304", "never re-propose", "review_only", "evidence",
               "perfect_pct", "SURVIVED", "BusyBox", "env.sh", "2>/dev/null"):
    check("doctrine says %r" % phrase, phrase in DOCTRINE, True)
check("doctrine states the N rule",
      ("N >= 3" in DOCTRINE) or ("N of 3" in DOCTRINE), True)
check("doctrine forbids resolution as a KPI lever",
      "resolution" in DOCTRINE.lower() and "CHEATING" in DOCTRINE, True)
check("doctrine is roughly the ~1,200-token budget of spec 4",
      1000 <= briefing.estimate_tokens(DOCTRINE) <= 1700, True)

# --- [10] one estimator across the kit -------------------------------------------------
print("\n[10] the estimator agrees with exam.py's")
check("the constant is shared", briefing.CHARS_PER_TOKEN, exam.CHARS_PER_TOKEN)
packet, manifest = exam.build_packet()
samples = [("exam study packet", packet), ("the doctrine", DOCTRINE),
           ("a briefing", BUILDS["sysmem_no_crown"]["briefing"]),
           ("a whole user message", BUILDS["attract_row"]["user"])]
worst = 0.0
for label, text in samples:
    mine = briefing.estimate_tokens(text)
    theirs = int(len(text) / exam.CHARS_PER_TOKEN)
    drift = abs(mine - theirs) / max(1, theirs)
    worst = max(worst, drift)
    print("    %-22s briefing %6d   exam %6d   drift %.3f%%" % (label, mine, theirs,
                                                                drift * 100))
check("drift under 2% on every sample", worst < 0.02, True)
check("exam's own manifest count matches ours",
      briefing.estimate_tokens(packet), manifest["est_tokens"])

# --- [11] the API the service codes against -------------------------------------------
print("\n[11] the public shape service.py depends on")
b = BUILDS["zlatez_925_lown"]
check("keys", sorted(b), ["briefing", "corpus_commit", "num_ctx", "prompt_sha256",
                          "sources", "system", "tokens", "user"])
check("tokens keys", sorted(b["tokens"]), ["briefing", "pack", "system", "total"])
check("sources entries carry kind/id/tokens",
      all(sorted(s) == ["id", "kind", "tokens"] for s in b["sources"]), True)
check("the user message ends with the output instruction",
      b["user"].rstrip().endswith(briefing.OUTPUT_INSTRUCTION.rstrip()), True)
check("the user message opens with the briefing", b["user"].startswith(b["briefing"]), True)
check("the pack rides in a fenced json block", "```json" in b["user"], True)
pj = b["user"].split("```json\n", 1)[1].split("\n```", 1)[0]
check("that block is valid JSON", isinstance(json.loads(pj), dict), True)
check("corpus_commit is a short sha or None",
      b["corpus_commit"] is None or bool(re.fullmatch(r"[0-9a-f]{7,40}", b["corpus_commit"])),
      True)
# run_sheet: passed in, it must reach the prompt; absent, it must not be invented.
sheet = {"schema": "ETK-RADIO-RUNSHEET v1", "game_id": "NPEA00050",
         "hypothesis": "zlatez vs sddepth at res 100, warm runs only",
         "arms": [{"label": "A", "n_target": 3}], "next": "one more warm race on A"}
with_sheet = briefing.build(dict(CASES[0][1]), repo_root=ROOT, run_sheet=sheet)
check("an accepted run sheet reaches the briefing",
      "zlatez vs sddepth" in with_sheet["briefing"], True)
check("...and is recorded in sources",
      any(s["kind"] == "run_sheet" for s in with_sheet["sources"]), True)
check("without one the briefing says so",
      "No accepted run sheet" in BUILDS["keepalive_off_day"]["briefing"], True)
check("a run sheet changes the hash too",
      with_sheet["prompt_sha256"] != shas[CASES[0][0]], True)

# --- [12] every trim is recorded ------------------------------------------------------
print("\n[12] never truncate silently")
for cid in BUILDS:
    trims = [s for s in BUILDS[cid]["sources"] if s["kind"] == "budget_trim"]
    manual = [s for s in BUILDS[cid]["sources"] if s["kind"] == "manual_section"]
    verbatim = any("verbatim" in s["id"] and "over budget" not in s["id"] for s in manual)
    if trims and verbatim:
        check("%s: a trimmed briefing never claims verbatim manual text" % cid, True, False)
check("a trimmed case names its trims",
      all(bool([s for s in BUILDS[c]["sources"] if s["kind"] == "budget_trim"])
          for c in BUILDS if BUILDS[c]["tokens"]["briefing"] < 1500), True)
check("trimmed packs say so in pack_notes",
      "trimmed to" in BUILDS["attract_row"]["user"], True)
# NEGATIVE CONTROL for the guard the service depends on: squeeze the budget until no
# ladder rung can save it, and the build must SAY it is over rather than pretend.
_saved = briefing.TOKEN_BUDGET
try:
    briefing.TOKEN_BUDGET = 400
    tight = briefing.build(dict(CASES[0][1]), repo_root=ROOT)
    over_src = [s for s in tight["sources"] if s["kind"] == "budget_exceeded"]
    check("an impossible budget is reported, not hidden", len(over_src), 1)
    check("...and it names how far over", "over the 400 budget" in over_src[0]["id"], True)
    check("...and the ladder really was spent",
          len([s for s in tight["sources"] if s["kind"] == "budget_trim"]),
          len(briefing._LADDER))
finally:
    briefing.TOKEN_BUDGET = _saved
check("no real case is over budget after that",
      [c for c in BUILDS if BUILDS[c]["tokens"]["total"] > briefing.TOKEN_BUDGET], [])

print()
if FAILS:
    print("FAILED: %d check(s) -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL BRIEFING CHECKS PASSED")
