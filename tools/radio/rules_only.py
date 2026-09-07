#!/usr/bin/env python3
"""ETK RADIO -- the RULES-ONLY debrief: the same pipeline with the model stage skipped.

Spec 6, last paragraph: "A rules-only debrief (no model) is produced by the same
pipeline with the model stage skipped: crash-signature text + dyno arms + computed tags
+ the run sheet's `next`." It is two things at once:

  * the EVAL BASELINE (spec 10). It must score 12/12 on the golden cases, because the
    assertions are the guards restated -- if the pipeline cannot pass them with no model
    at all, the assertions are grading the model on the pipeline's own bugs.
  * the FALLBACK. When the node is unreachable the rig still has a pit note, computed
    from dyno alone: "zlatez@925 N=2 of 3 - one more warm run" (spec 6).

Everything it says is already in the pack. It derives no number, and it never says
anything the guards would have to take away -- then it runs the guards over itself
anyway, so its output is shaped exactly like a model's and the renderer, the schema and
the service cannot tell them apart except by `model: "rules-only"`.

    build(pack, *, corpus_commit=None) -> a complete DEBRIEF v1 (guarded)
    headline(pack)                     -> the <= 60 char ASCII pit note
    render(debrief, pack)              -> the 80-column ASCII terminal read

What it will NOT do, on purpose:

  * no `config` recommendation. A staged config edit is bytes-to-atoms in waiting, and
    nothing in the ledger justifies one from a single row; the crash catalog's
    suggested_changes are shown as the CRASH NET's own advice, in the finding, where a
    human reads them -- not staged as a proposal.
  * no comparison finding. Rules-only never crowns; that is what a run sheet at N>=3 is
    for.
  * a proposed run sheet only when there is an accepted one to extend. A first sheet is
    a hypothesis, and a hypothesis is the operator's to set.

Stdlib only; python 3.12+. ASCII on every surface string.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import guards                                                           # noqa: E402
import tags as tagmod                                                   # noqa: E402

__all__ = ["build", "headline", "render", "PROMPT_ID", "MODEL"]

MODEL = "rules-only"
PROMPT_ID = "rules-only:v1"
W = 80

# The operator's note re-opens the resolution question.
_RES_QUESTION = re.compile(
    r"(?i)((?:lower|lowering|drop|dropping|reduce|reducing|cut|cutting|halv\w*)"
    r"[^.]{0,40}resolution|resolution[^.]{0,40}(?:down to|to)\s*(?:50|66|75|85|90)\b)")


# ------------------------------------------------------------------ small helpers
def _sess(pack):
    return (pack.get("session") or {}) if isinstance(pack, dict) else {}


def _dur(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "an unknown time"
    return "%dm%02ds" % (sec // 60, sec % 60) if sec >= 60 else "%ds" % sec


def _num(v):
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _first_sentence(text):
    parts = re.split(r"(?<=[.!?])\s+", str(text).strip())
    return parts[0] if parts else str(text)


def _catalog():
    return {str(s.get("id")): s for s in guards.load_signatures()}


def _corpus_commit():
    """The manual this debrief read. git when there is a checkout, zeros when there is
    not -- the schema wants 7-40 hex, and a lie would be worse than a zero."""
    try:
        r = subprocess.run(["git", "-C", guards.repo_root(), "rev-parse", "--short",
                            "HEAD"], capture_output=True, text=True, timeout=20)
        rev = r.stdout.strip()
        if re.match(r"^[0-9a-f]{7,40}$", rev):
            return rev
    except (OSError, subprocess.SubprocessError):
        pass
    return "0" * 7


def _keep_resolvable(pack, evidence):
    """Keep only the evidence that actually resolves in THIS pack. Rules-only cites
    nothing it cannot show, so the evidence guard never has to demote it."""
    return [ev for ev in evidence if guards.resolve_evidence(ev, pack)[0]]


def _finding(pack, kind, text, evidence):
    ev = _keep_resolvable(pack, evidence)
    if not ev:
        ev = _keep_resolvable(pack, [{"source": "session", "field": "status",
                                      "value": _sess(pack).get("status")}])
    if not ev:
        return None
    return {"kind": kind, "text": guards.trim(guards.to_ascii(text), 400),
            "evidence": ev[:6]}


# ------------------------------------------------------------------------ findings
def _falsified_in_note(pack):
    """Which falsified entries the operator's own note re-opens. Reading the note is
    not proposing the item -- naming it in order to refuse it is the disproof doing
    its job, which is why the guard looks at recommendations and this looks here."""
    note = "%s %s" % ((pack.get("operator") or {}).get("note") or "",
                      (pack.get("operator") or {}).get("feel") or "")
    game_id = str(pack.get("game_id") or "")
    hits = []
    for entry in guards.load_falsified():
        scope = (entry.get("scope") or {}).get("game_id")
        if scope and game_id not in scope:
            continue
        m = entry.get("match") or {}
        found = False
        for group in ("tu_debug", "env", "power"):
            for tok in (m.get(group) or []):
                if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tok),
                             note, re.I):
                    found = True
        for key in (m.get("yaml_key") or []):
            if str(key).strip().lower() in note.lower():
                found = True
        for rx in (m.get("text") or []):
            try:
                if re.search(rx, note, re.I):
                    found = True
            except re.error:
                continue
        if found:
            hits.append(entry)
    return hits



def _clip(line, n):
    line = str(line or "").strip()
    return line if len(line) <= n else line[:n - 3] + "..."


def _needle(line):
    """A stable substring for the evidence resolver: the line after its timestamp."""
    line = str(line or "").strip()
    m = re.match(r"^[EF] \d+:\d+:\d+\.\d+ (.*)$", line)
    body = m.group(1) if m else line
    return body[:60]


def _findings(pack, tg):
    out, cat = [], _catalog()
    ses, crash = _sess(pack), (pack.get("crash") or {})

    for i, sig in enumerate(crash.get("sigs") or []):
        sid = str(sig.get("id") or "")
        ref = cat.get(sid, {})
        summary = sig.get("summary") or ref.get("summary") or sid
        text = "%s. %s" % (summary, ref.get("explanation") or "")
        out.append(_finding(pack, "mechanism", text, [
            {"source": "crash", "field": "sigs.%d.id" % i, "value": sid},
            {"source": "ledger", "field": "crash_sig", "value": sid}]))

    fault = crash.get("fault")
    if isinstance(fault, dict) and fault.get("status"):
        out.append(_finding(
            pack, "mechanism",
            "Fault status %s decodes as %s (fence %s). The class comes from the "
            "A6XX_RBBM_STATUS decode, not from a guess." % (
                fault.get("status"), fault.get("class") or "unclassified",
                fault.get("fence_hex") or "-"),
            [{"source": "crash", "field": "fault.status", "value": fault.get("status")},
             {"source": "crash", "field": "fault.class", "value": fault.get("class")}]))

    if "keepalive_absent" in tg:
        out.append(_finding(
            pack, "anomaly",
            "A GPU fault status is on this row (%s) with rescues=0: the keepalive net "
            "did not show up on this boot. Our own code gets ruled out before the "
            "hardware does." % (ses.get("gpu_fault_status") or "-"),
            [{"source": "ledger", "field": "gpu_fault_status",
              "value": ses.get("gpu_fault_status")},
             {"source": "ledger", "field": "rescues", "value": ses.get("rescues")}]))

    if "panic_silent" in tg:
        out.append(_finding(
            pack, "anomaly",
            "PANIC with no lead-up: the kept message tail carries no fault, no oops "
            "and no trace before the reset, so there is nothing in this pack to "
            "attribute a cause to. It is the silent-reset class until it is reproduced "
            "with the recorder armed.",
            [{"source": "session", "field": "status", "value": ses.get("status")},
             {"source": "crash", "field": "blackbox_tail"}]))

    if "attract" in tg:
        out.append(_finding(
            pack, "observation",
            "Attract-mode run - invalid for crash-class work: the fault is "
            "live-race-path specific (attract survived 1200 s+ where racing wedged in "
            "about two minutes), so nothing here settles a crash class either way.",
            [{"source": "operator", "field": "note"},
             {"source": "session", "field": "status", "value": ses.get("status")}]))

    hits = _falsified_in_note(pack)
    if hits:
        out.append(_finding(
            pack, "observation",
            "The note re-opens falsified items: %s. Each is retired and never "
            "re-proposed; the disproof is the asset. %s" % (
                ", ".join(h.get("id") for h in hits),
                " ".join(h.get("disproof") or "" for h in hits)),
            [{"source": "operator", "field": "note"}]))

    if _RES_QUESTION.search(str((pack.get("operator") or {}).get("note") or "")):
        out.append(_finding(
            pack, "observation",
            "Resolution Scale is not a KPI lever: the KPI is judged at native res 100, "
            "so fewer pixels is cheating and not a gain. A smaller render target is a "
            "CRASH NET move under a signature that calls for it, never an answer to a "
            "KPI question.",
            [{"source": "operator", "field": "note"},
             {"source": "ledger", "field": "res_scale", "value": ses.get("res_scale")}]))

    if "bake" in tg:
        out.append(_finding(
            pack, "observation",
            "Bake session: %s shaders were compiled during this run, so the speed "
            "columns describe the shader compiler and not the game. Only a warm run "
            "(shd near zero) counts for feel." % ses.get("shaders_harvested"),
            [{"source": "ledger", "field": "shaders_harvested",
              "value": ses.get("shaders_harvested")}]))

    if "aborted" in tg:
        out.append(_finding(
            pack, "observation",
            "ABORTED row: the session never became a race, so it carries no feel "
            "evidence at all - the ledger method drops it from every median.",
            [{"source": "session", "field": "status", "value": ses.get("status")}]))

    aud = ses.get("aud") or {}
    if _num(aud.get("skip")) or _num(aud.get("ur")):
        out.append(_finding(
            pack, "observation",
            "Audio counters from the fork: skip=%s periods dropped, ur=%s underruns, "
            "urb=%s bytes zero-filled, rmin=%s. The emulator zero-fills an underrun "
            "without writing a log line at any level, so an empty log settles nothing "
            "here." % (aud.get("skip"), aud.get("ur"), aud.get("urb"),
                       aud.get("rmin")),
            [{"source": "aud", "field": "skip", "value": aud.get("skip")},
             {"source": "aud", "field": "ur", "value": aud.get("ur")},
             {"source": "aud", "field": "rmin", "value": aud.get("rmin")}]))

    fatals = [e for e in (crash.get("rpcs3_errors") or [])
              if isinstance(e, dict) and re.match(r"^F ", str(e.get("line") or ""))]
    exits = [e for e in (crash.get("rpcs3_errors") or [])
             if isinstance(e, dict) and "did not react to the exit request"
             in str(e.get("line") or "")]
    if fatals or exits:
        parts = []
        if fatals:
            parts.append("%d fatal (F) line%s in the RPCS3 log that no crash signature "
                         "covers: %s" % (len(fatals), "" if len(fatals) == 1 else "s",
                                          _clip(fatals[0].get("line"), 110)))
        if exits:
            parts.append("the session ended by an exit request the game did not answer "
                         "in time")
        text = ("Uncatalogued: " + "; ".join(parts) + ". Not a diagnosis - the catalog "
                "has no entry for this, so the engineer reads the log window.")
        ev = [{"source": "rpcs3", "line": _needle(e.get("line"))}
              for e in (fatals[:2] + exits[:1])]
        out.append(_finding(pack, "anomaly", text, ev))

    if "low_n" in tg:
        arms = tagmod.session_arms(pack)
        n = max([a.get("n") or 0 for a in arms] or [0])
        idx = next((i for i, a in enumerate((pack.get("dyno") or {}).get("arms") or [])
                    if a in arms), None)
        ev = [{"source": "dyno", "field": "arms.%d.n" % idx, "value": n}] if idx is not None \
            else [{"source": "dyno", "field": "arms"}]
        out.append(_finding(
            pack, "observation",
            "No dyno arm at N>=3 describes this condition (%s stands at N=%d of 3), "
            "so nothing on this row can be crowned - one clean race is variance, and "
            "the title's own noise floor is 77-2886 s." % (guards.arm_label(pack), n),
            ev))

    return [f for f in out if f][:8]


# ----------------------------------------------------------------- recommendations
def _recommendations(pack, tg):
    out, cat = [], _catalog()
    label = guards.arm_label(pack)
    arms = tagmod.session_arms(pack)
    n = max([a.get("n") or 0 for a in arms] or [0])
    pwr = (_sess(pack).get("pwr") or "-").strip() or "-"
    basis = {"arm": guards.to_ascii("%s/%s" % (label, pwr))[:120], "n": int(n),
             "n_needed": 3}

    forcing = [t for t in ("keepalive_absent", "panic_silent", "stack_change")
               if t in tg]
    if forcing:
        out.append({"kind": "investigate",
                    "text": guards.INVESTIGATE_TEXT[forcing[0]],
                    "config_changes": [], "driver_dial": None,
                    "confidence": "high"})

    stops = {"low_n", "bake", "aborted", "attract"} & set(tg)
    if stops:
        why = {"bake": "the shaders were still compiling on this one",
               "aborted": "this row never became a race",
               "attract": "an attract lap settles nothing about the race path",
               "low_n": "the arm has not cleared N=3"}
        out.append({
            "kind": "next_run",
            "text": guards.trim(
                "One more warm run on %s: the arm stands at N=%d of 3, and %s." % (
                    label, n, why[sorted(stops)[0]]), 400),
            "n_basis": basis, "config_changes": [], "driver_dial": None,
            "confidence": "high"})

    if not (forcing or stops):
        dial, cur = None, ((pack.get("rig") or {}).get("dial") or "default").strip()
        for sig in ((pack.get("crash") or {}).get("sigs") or []):
            ref = cat.get(str(sig.get("id") or ""), {})
            cand = ref.get("driver_dial")
            if cand and cur.lower() not in cand.lower():
                dial = cand
                break
        if dial:
            out.append({
                "kind": "dial",
                "text": guards.trim("The DRIVER tab is the lever for this class: %s"
                                    % guards.to_ascii(dial), 400),
                "n_basis": basis, "config_changes": [],
                "driver_dial": guards.to_ascii(dial)[:120], "confidence": "medium"})
        if not out:
            out.append({
                "kind": "no_change",
                "text": guards.trim(
                    "Nothing to change on this arm: %s stands at N=%d and the row "
                    "carries no unexplained signal." % (label, n), 400),
                "n_basis": basis, "config_changes": [], "driver_dial": None,
                "confidence": "medium"})
    return out[:6]


def _run_sheet(pack):
    """A proposed sheet only when there is an ACCEPTED one to extend. A first sheet is
    a hypothesis and a hypothesis is the operator's to set (spec 3.3: the sheet is
    written only by ACCEPT)."""
    rs = pack.get("run_sheet")
    if not isinstance(rs, dict) or not rs.get("arms"):
        return None
    out = {"schema": "ETK-RADIO-RUNSHEET v1",
           "accepted_epoch": rs.get("accepted_epoch"),
           "game_id": str(pack.get("game_id") or rs.get("game_id") or "unknown"),
           "stack": rs.get("stack"), "res": rs.get("res"),
           "hypothesis": guards.trim(guards.to_ascii(rs.get("hypothesis") or ""), 280),
           "arms": [], "stop_rule": guards.trim(
               guards.to_ascii(rs.get("stop_rule") or ""), 280)}
    for arm in (rs.get("arms") or [])[:4]:
        out["arms"].append({
            "label": guards.to_ascii(arm.get("label") or "A")[:8],
            "tune": guards.to_ascii(arm.get("tune"))[:120] if arm.get("tune") else None,
            "clk": arm.get("clk"), "pwr": arm.get("pwr"),
            "n_target": max(3, int(arm.get("n_target") or 3))})
    label, arms = guards.arm_label(pack), tagmod.session_arms(pack)
    n = max([a.get("n") or 0 for a in arms] or [0])
    out["next"] = guards.trim("one more warm run on %s; it stands at N=%d of 3"
                              % (label, n), 280)
    return out if out["arms"] else None


# --------------------------------------------------------------------------- build
def headline(pack):
    """The rig-side fallback pit note, computed from dyno alone (spec 6):
    "zlatez@925 N=2 of 3 - one more warm run". <= 60 ASCII characters, one line."""
    return guards.pit_note(pack)


def _radio(pack, findings, recs):
    ses = _sess(pack)
    bits = ["%s on %s after %s." % (
        guards.to_ascii(ses.get("status") or "-"),
        guards.to_ascii(pack.get("game_id") or "-"), _dur(ses.get("duration_s")))]
    if findings:
        bits.append(_first_sentence(findings[0]["text"]))
    if recs:
        bits.append(recs[0]["text"])
    return guards.trim(guards.to_ascii(" ".join(bits)), 280)


def build(pack, *, corpus_commit=None):
    """A complete DEBRIEF v1 for a PACK v1, with no model in the loop. Guarded like a
    model's answer, because the guards are the pipeline, not a model wrapper."""
    t0 = time.perf_counter()
    pack = pack if isinstance(pack, dict) else {}
    tg = tagmod.compute(pack)
    status = str(_sess(pack).get("status") or "").split(":")[0].lower()
    status = re.sub(r"[^a-z0-9_]", "", status)
    if status and status not in tg:
        tg = tg + [status]

    findings = _findings(pack, tg)
    recs = _recommendations(pack, tg)
    try:
        epoch = int(str(pack.get("epoch") or 0))
    except (TypeError, ValueError):
        epoch = 0

    debrief = {
        "schema": "ETK-RADIO-DEBRIEF v1",
        "epoch": epoch,
        "game_id": str(pack.get("game_id") or "unknown"),
        "model": MODEL,
        "prompt_sha256": hashlib.sha256(PROMPT_ID.encode("ascii")).hexdigest(),
        "corpus_commit": corpus_commit or _corpus_commit(),
        "tokens": {"prompt": 0, "completion": 0},
        "latency_s": 0.0,
        "radio": _radio(pack, findings, recs),
        "headline": headline(pack),
        "tags": tg[:12],
        "findings": findings,
        "recommendations": recs,
        "run_sheet": _run_sheet(pack),
        "guards": {"passed": True, "dropped": []},
    }
    out = guards.apply(debrief, pack)
    out["latency_s"] = round(max(0.0, time.perf_counter() - t0), 3)
    return out


# -------------------------------------------------------------------------- render
def _ascii_line(line):
    """ASCII a rendered line WITHOUT collapsing its indentation (to_ascii is for prose,
    where runs of whitespace are noise; here they are the layout)."""
    return "".join(c if 0x20 <= ord(c) <= 0x7E else "?" for c in str(line))


def _wrap(text, indent, width=W):
    out, line = [], ""
    for word in str(text).split():
        if len(line) + len(word) + 1 > width - indent:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return [(" " * indent) + ln for ln in out] or [(" " * indent) + "-"]


def render(debrief, pack):
    """The ASCII terminal read, 80 columns. Every claim is followed by its RESOLVED
    evidence line, printed verbatim from the pack (spec 6, evidence beside every
    claim) -- so a semantic flip is visible at a glance instead of being believed."""
    L = ["=" * W]
    L.append("RADIO DEBRIEF  %s  %s   model %s" % (
        debrief.get("epoch"), debrief.get("game_id"), debrief.get("model")))
    L.append("corpus %s   prompt %s   %s tok in / %s out   %.1fs" % (
        debrief.get("corpus_commit"), str(debrief.get("prompt_sha256"))[:12],
        (debrief.get("tokens") or {}).get("prompt"),
        (debrief.get("tokens") or {}).get("completion"),
        float(debrief.get("latency_s") or 0)))
    L.append("-" * W)
    L.append("PIT NOTE  %s" % debrief.get("headline"))
    L.extend(_wrap('RADIO: "%s"' % debrief.get("radio"), 2))
    L.append("TAGS      %s" % (", ".join(debrief.get("tags") or []) or "-"))

    L.append("-" * W)
    findings = debrief.get("findings") or []
    L.append("FINDINGS  %d" % len(findings))
    for f in findings:
        L.extend(_wrap("[%s] %s" % (f.get("kind"), f.get("text")), 2))
        ev = f.get("evidence") or []
        if not ev:
            L.append("      evidence  (none cited)")
        for e in ev:
            ok, line = guards.resolve_evidence(e, pack)
            L.extend(_wrap("%s %s" % ("evidence " if ok else "UNCITED  ", line), 6))

    L.append("-" * W)
    recs = debrief.get("recommendations") or []
    L.append("RECOMMENDATIONS  %d" % len(recs))
    for r in recs:
        mark = "  [ENGINEER TO REVIEW]" if r.get("review_only") else ""
        L.extend(_wrap("[%s]%s %s" % (r.get("kind"), mark, r.get("text")), 2))
        nb = r.get("n_basis")
        if isinstance(nb, dict):
            L.append("      basis  %s  N=%s of %s   confidence %s" % (
                nb.get("arm"), nb.get("n"), nb.get("n_needed"), r.get("confidence")))
        else:
            L.append("      confidence %s" % r.get("confidence"))
        for ch in (r.get("config_changes") or []):
            L.append("      stage  %-40s -> %s" % (
                str(ch.get("yaml_key")).strip()[:40], ch.get("new_value")))
        if r.get("driver_dial"):
            L.extend(_wrap("dial   %s" % r["driver_dial"], 6))
        if r.get("review_only"):
            L.append("      engineer to review - never staged by LOAD FIX")

    rs = debrief.get("run_sheet")
    if rs:
        L.append("-" * W)
        L.append("PROPOSED RUN SHEET  stack %s  res %s" % (rs.get("stack"),
                                                           rs.get("res")))
        L.extend(_wrap(rs.get("hypothesis") or "-", 2))
        for a in rs.get("arms") or []:
            L.append("  arm %-3s %-28s clk %-5s pwr %-6s n_target %s" % (
                a.get("label"), str(a.get("tune"))[:28], a.get("clk"), a.get("pwr"),
                a.get("n_target")))
        if rs.get("next"):
            L.extend(_wrap("NEXT: " + rs["next"], 2))

    L.append("-" * W)
    g = debrief.get("guards") or {}
    dropped = g.get("dropped") or []
    L.append("GUARDS    %s   %d dropped" % (
        "passed" if g.get("passed") else "CORRECTED", len(dropped)))
    for d in dropped:
        L.extend(_wrap("- %s: %s" % (d.get("kind"), d.get("reason")), 2))
    if not dropped:
        L.append("  nothing was taken away; the eleven found nothing to correct")
    L.append("=" * W)
    return "\n".join(_ascii_line(line)[:W].rstrip() for line in L)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            _pack = json.load(fh)
        _d = build(_pack)
        print(render(_d, _pack))
    else:
        sys.exit("usage: rules_only.py <pack.json>   (library; see tools/radio.py)")
