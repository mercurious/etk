#!/usr/bin/env python3
"""ETK RADIO -- the eleven guards (spec 6): discipline in code, downstream of the model.

The model can be wrong; the SURFACE cannot show a wrong crown. Everything a model (or
the rules-only builder) says passes through here before anything is written, rendered
or toasted. Each guard names the law it enforces, and each one is a pure function of
(debrief, pack, config) -- no logging, no I/O beyond loading the two config files once,
no network, no clock.

    apply(debrief, pack, *, falsified=None, fields=None, num_ctx=None) -> new debrief
    GUARDS                      -> [(id, law, callable)] in spec 6 table order
    resolve_evidence(ev, pack)  -> (resolved: bool, rendered_line: str)
    pit_note(pack)              -> the computed <= 60 char fallback headline

`apply` NEVER raises. A debrief that is structurally hopeless (not an object, no
findings, a schema violation nothing can repair) comes back as a MINIMAL VALID debrief
for the pack with the reason recorded under `guards.dropped` -- because a service that
crashes on a bad answer is a service that has no answer, and the rig is waiting.

The returned debrief always carries:

    "guards": {"passed": <no drop was recorded>, "dropped": [{"kind", "reason"}, ...]}

`kind` is the guard's id, so the service can log which guard fired and the renderer can
print what was taken away and why. A drop is recorded, never silent (spec 10.1).

The eleven, in the spec 6 table's order:

  1 no_crown_below_n                       manual B.3: N>=3 before any crown
  2 never_repropose_falsified              manual F: the disproof is the asset
  3 schema_vocabulary_only                 TUNING refuses foreign keys
  4 resolution_is_not_a_kpi_lever          manual 2.1: resolution-lowering = cheating
  5 bake_and_aborted_are_not_feel_evidence manual B.3: bake sessions lie
  6 attribution_before_narrative           manual B.3: rule out our own code first
  7 data_never_commands                    spec 12: the debrief is data, never a command
  8 ascii_surfaces                         manual A.3: glyph law, notification law
  9 evidence_beside_every_claim            manual B.3 + spec 10.1 (semantic flips)
 10 never_truncate_silently                spec 10.1: cut prompts judged as whole files
 11 diagnosis_is_not_prescription          spec 10.2: right diagnosis, illegal fix

Config: `config/falsified.json` and `config/pitstop_fields.json` are read from the repo
this file lives in, or from $ETK_RADIO_CORPUS when the node's checkout sits elsewhere.
Both can be passed in instead, which is what the tests do.

Stdlib only; python 3.12+. ASCII on every surface string it writes.
"""
import copy
import json
import os
import re
import sys
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                # runnable from the repo root, the node, the rig
    sys.path.insert(0, _HERE)

import schemas                                                          # noqa: E402
import tags as tagmod                                                   # noqa: E402

__all__ = ["apply", "GUARDS", "resolve_evidence", "pit_note", "arm_label",
           "INVESTIGATE_TEXT", "load_falsified", "load_fields", "load_signatures",
           "repo_root", "to_ascii", "trim"]

RADIO_MAX, HEADLINE_MAX, TEXT_MAX, REASON_MAX = 280, 60, 400, 240

# A finding "compares arms" when it says so the way an engineer says it. Kept
# byte-identical to tools/radio/eval.py's COMPARISON_RE; test_guards.py pins the
# two patterns together so a divergence is a failing test, not a silent crown.
COMPARISON_RE = re.compile(
    r"(?i)(\bvs\.?\b|\bversus\b|\bcompared (?:to|with)\b|\bagainst the (?:default|baseline|other)\b"
    r"|\b(?:better|worse|faster|slower|cleaner|higher|lower|longer|shorter) than\b"
    r"|\bbeats\b|\boutperform\w*\b|\bwins? over\b|\bahead of\b|\bimproves? on\b"
    r"|\b\d+(?:\.\d+)?\s*(?:x|times)\s+(?:better|worse|fewer|more|longer|the)\b"
    r"|\bcuts? (?:the )?\w+ (?:rate )?(?:by|to)\b)")

# One evidence entry cites an arm's N when its `field` is n / *_n / arm n.
N_FIELD_RE = re.compile(r"(?i)^(?:n|arm[_ ]?n|n[_ ]?arm|[a-z0-9_ .\[\]-]*[_. ]n)$")

# A feel claim: what a bake or ABORTED row may not say (manual B.3).
FEEL_CLAIM_RE = re.compile(r"(?i)(\bfps\b|fps_med|fps_1low|frame ?rate|framerate|"
                           r"\bperfect\b|perfect_pct|perfect windows|lock_pct|"
                           r"\bframe times?\b|\bframetime)")

# Outside the kit's config vocabulary: the model may DIAGNOSE these, never prescribe
# them (spec 10.2 -- both models diagnosed the env bomb, then prescribed a Law #2 break).
OUTSIDE_KIT_RE = re.compile(
    r"(?i)(\binstall\.sh\b|\buninstall\.sh\b|\benv\.sh\b|\bforge\.sh\b|profile\.d|"
    r"systemd|systemctl|modprobe|sysctl|/etc/|/storage/|crontab|\bdaemon\b|"
    r"\bkernel\b|initrd|\budev\b|LD_PRELOAD|\bexport [A-Z_]{3,}=|\brecompile\b|"
    r"\b[A-Za-z0-9_-]+\.(?:sh|py|service|conf|rules)\b)")

# The 14 pack sections a piece of evidence may cite (spec 3.1), plus two spellings the
# eval fixtures use for the same places. NOT included, on purpose: `manual`. A doctrine
# quotation is not a citation of THIS row, and the law is that a claim resolves to the
# pack or is demoted -- so a manual-only finding becomes an observation tagged uncited.
EVIDENCE_SOURCES = ("ledger", "session", "history", "dyno", "crash", "rpcs3", "dmesg",
                    "blackbox", "timeline", "config", "aud", "perf", "career",
                    "operator", "rpcs3_log", "pack")

_TRANSLIT = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201C: '"', 0x201D: '"', 0x201E: '"',
    0x2013: "-", 0x2014: "-", 0x2212: "-", 0x2022: "-", 0x00B7: "-", 0x2026: "...",
    0x00D7: "x", 0x00B0: " deg", 0x00A0: " ", 0x00AB: '"', 0x00BB: '"',
    0x2192: "->", 0x2190: "<-", 0x2265: ">=", 0x2264: "<=", 0x00B1: "+/-",
}

_cache = {}


# ------------------------------------------------------------------ config loading
def repo_root():
    """The checkout this file belongs to; $ETK_RADIO_CORPUS wins (the node's copy)."""
    env = os.environ.get("ETK_RADIO_CORPUS")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def _load_json(relpath, default):
    key = ("json", relpath, repo_root())
    if key in _cache:
        return _cache[key]
    path = os.path.join(repo_root(), relpath)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = default
    _cache[key] = data
    return data


def load_falsified():
    """config/falsified.json -> the list of entries (the file may wrap them in a
    document with its own header; both shapes read)."""
    data = _load_json(os.path.join("config", "falsified.json"), [])
    if isinstance(data, dict):
        data = data.get("entries") or []
    return data if isinstance(data, list) else []


def load_fields():
    """config/pitstop_fields.json -> the ONLY config vocabulary a recommendation has."""
    data = _load_json(os.path.join("config", "pitstop_fields.json"), [])
    return data if isinstance(data, list) else []


def load_signatures():
    """config/crash_signatures.json -> which sigs may legitimately lower resolution."""
    data = _load_json(os.path.join("config", "crash_signatures.json"), [])
    return data if isinstance(data, list) else []


# ------------------------------------------------------------------- small helpers
def to_ascii(text):
    """Transliterate to printable ASCII. A glyph the HUD cannot draw is not a glyph."""
    s = str(text)
    s = "".join(_TRANSLIT.get(ord(c), c) for c in s)
    s = unicodedata.normalize("NFKD", s)
    out = []
    for ch in s:
        if ch in "\r\n\t":
            out.append(" ")
        elif 0x20 <= ord(ch) <= 0x7E:
            out.append(ch)
        elif unicodedata.combining(ch):
            continue
        else:
            out.append("?")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def trim(text, cap):
    """Truncate at a word boundary, never mid-word, never with a dangling separator."""
    s = str(text)
    if len(s) <= cap:
        return s
    cut = s[:cap]
    sp = cut.rfind(" ")
    if sp >= cap // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,;:-")


def _num(v):
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _short(v):
    if isinstance(v, float):
        v = ("%.3f" % v).rstrip("0").rstrip(".")
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        # a table (dyno arms, history rows, error lines): summarize, never truncate
        # mid-object -- a half-printed JSON list is not evidence anyone can read
        ns = [x.get("n") for x in v if isinstance(x.get("n"), (int, float))]
        if ns:
            return "%d arm(s), highest N %s" % (len(v), int(max(ns)))
        return "%d row(s); first: %s" % (
            len(v), to_ascii(json.dumps(v[0]))[:36] + ("..." if len(json.dumps(v[0])) > 36 else ""))
    if isinstance(v, list) and len(v) > 3:
        return "%d item(s): %s ..." % (len(v), to_ascii(json.dumps(v[:3]))[:40])
    s = to_ascii(v if not isinstance(v, (dict, list)) else json.dumps(v))
    return s if len(s) <= 60 else s[:57] + "..."


def _sess(pack):
    return (pack.get("session") or {}) if isinstance(pack, dict) else {}


def _arm_label(pack):
    dial = ((pack.get("rig") or {}).get("dial") or "default") if isinstance(pack, dict) else "default"
    clk = _sess(pack).get("gpu_mhz")
    return "%s@%s" % (to_ascii(dial), clk if clk not in (None, "") else "-")


def _arm_n(pack):
    return max([a.get("n") or 0 for a in tagmod.session_arms(pack)] or [0])


def pack_arm_ns(pack):
    """Every N the pack's own dyno table carries -- the only N a crown may cite."""
    return [a.get("n") for a in ((pack.get("dyno") or {}).get("arms") or [])
            if isinstance(a.get("n"), int)]


def pit_note(pack):
    """The computed <= 60 char pit note: what the rig can say with no model at all.
    Also the replacement when a guard has to take a poisoned headline away."""
    t = set(tagmod.compute(pack))
    label, n = _arm_label(pack), _arm_n(pack)
    if "panic_silent" in t:
        line = "PANIC - no lead-up in the tail; investigate first"
    elif "keepalive_absent" in t:
        line = "fault with no rescue - check the keepalive first"
    elif "low_n" in t:
        line = "%s N=%d of 3 - one more warm run" % (label, n)
    else:
        status = to_ascii(_sess(pack).get("status") or "-").split(":")[0] or "-"
        line = "%s - %s at N=%d" % (status, label, n)
    return trim(to_ascii(line), HEADLINE_MAX)


# ------------------------------------------------------------- evidence resolution
def _evidence_root(pack, source):
    ses = _sess(pack)
    crash = (pack.get("crash") or {}) if isinstance(pack, dict) else {}
    cfg = (pack.get("config") or {}) if isinstance(pack, dict) else {}
    return {
        "ledger": ses,
        "session": ses,
        "aud": ses.get("aud"),
        "perf": ses.get("perf"),
        "history": pack.get("history") if isinstance(pack, dict) else None,
        "career": ((pack.get("history") or {}).get("career")
                   if isinstance(pack, dict) else None),
        "dyno": pack.get("dyno") if isinstance(pack, dict) else None,
        "crash": crash,
        "rpcs3": crash.get("rpcs3_errors"),
        "rpcs3_log": crash.get("rpcs3_errors"),
        "pack": pack if isinstance(pack, dict) else None,
        "dmesg": crash.get("dmesg_window"),
        "blackbox": crash.get("blackbox_tail"),
        "timeline": pack.get("timeline") if isinstance(pack, dict) else None,
        "config": cfg.get("values") if isinstance(cfg.get("values"), dict) else cfg,
        "operator": pack.get("operator") if isinstance(pack, dict) else None,
    }.get(source)


def _walk(node, dotted):
    """A dotted path over the pack: 'arms.0.n', 'fault.status', 'Resolution Scale'."""
    cur, seen = node, False
    for part in str(dotted).split("."):
        if isinstance(cur, dict) and part in cur:
            cur, seen = cur[part], True
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur, seen = cur[int(part)], True
        else:
            return False, None
    return seen, cur


def _pack_lines(pack, source):
    """Every quotable line the pack carries, for a `line` citation."""
    out = []
    root = _evidence_root(pack, source)
    for holder in ([root] if source else []):
        if isinstance(holder, list):
            for item in holder:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and isinstance(item.get("line"), str):
                    out.append(item["line"])
    if isinstance(pack, dict):
        out += [str(x) for x in (pack.get("pack_notes") or [])]
    return out


def resolve_evidence(ev, pack):
    """-> (resolved, line). The renderer prints `line` VERBATIM under the claim, so a
    semantic flip (4 MB read as 288 MB, 'abort' for 'requeue') is visible at a glance."""
    if not isinstance(ev, dict):
        return False, "(unresolved: not an evidence object)"
    src = str(ev.get("source") or "")
    if src not in EVIDENCE_SOURCES:
        return False, "(unresolved: %s is not a pack section)" % (src or "no source")
    root = _evidence_root(pack, src)
    field = ev.get("field")
    if field not in (None, ""):
        ok, val = _walk(root, field)
        if ok:
            return True, "%s.%s = %s" % (src, to_ascii(field), _short(val))
        return False, "(unresolved: %s.%s is not in the pack)" % (src, to_ascii(field))
    quote = ev.get("line")
    if quote not in (None, ""):
        needle = str(quote).strip().lower()
        for line in _pack_lines(pack, src):
            if needle and needle in line.lower():
                return True, "%s: %s" % (src, _short(line))
        return False, "(unresolved: no %s line carries %s)" % (src, _short(quote))
    if root not in (None, "", [], {}):
        return True, "%s (section present in the pack)" % src
    return False, "(unresolved: the pack has no %s section)" % src


# ---------------------------------------------------------------------- the context
class _Ctx(object):
    """Everything the eleven share. Not part of the public API."""

    def __init__(self, pack, falsified, fields, num_ctx):
        self.pack = pack if isinstance(pack, dict) else {}
        self.falsified = falsified if falsified is not None else load_falsified()
        self.fields = fields if fields is not None else load_fields()
        self.sigs = load_signatures()
        self.num_ctx = num_ctx
        self.dropped = []
        self.tags = set(tagmod.compute(self.pack))

    def drop(self, kind, reason):
        self.dropped.append({"kind": to_ascii(kind)[:40],
                             "reason": trim(to_ascii(reason), REASON_MAX)})

    def tag(self, debrief, name):
        have = debrief.setdefault("tags", [])
        if name not in have and len(have) < 12:
            have.append(name)


# ------------------------------------------------------------------ prose scrubbing
_CLAUSE_SPLIT = re.compile(r"(\s*[.;]\s+|\s+-\s+|,\s+)")


def _clause_hits(clause, key, value):
    low = clause.lower()
    if key and key.lower() in low:
        return True
    last = key.split()[-1].lower() if key.split() else ""
    if last and value not in (None, "") and last in low and str(value).lower() in low:
        return True
    return False


def _scrub_text(text, key, value):
    """Take the clause that carries a DROPPED change out of the prose. A change the
    guard refused must not survive as a sentence either."""
    parts = _CLAUSE_SPLIT.split(str(text))
    keep, i = [], 0
    while i < len(parts):
        clause = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if clause.strip() and _clause_hits(clause, key, value):
            if keep:
                keep[-1] = ""            # drop the separator that led into it
        else:
            keep += [clause, sep]
        i += 2
    out = re.sub(r"\s+", " ", "".join(keep)).strip()
    return out.rstrip(" ,;-")


def _scrub_debrief(debrief, ctx, key, value):
    for fld, fallback in (("headline", pit_note(ctx.pack)), ("radio", None)):
        cur = debrief.get(fld)
        if not isinstance(cur, str):
            continue
        new = _scrub_text(cur, key, value)
        if new != cur:
            debrief[fld] = new or (fallback if fallback is not None
                                   else pit_note(ctx.pack))
            ctx.drop("schema_vocabulary_only",
                     "the dropped change was taken out of the %s" % fld)
    for holder in ("findings", "recommendations"):
        for item in (debrief.get(holder) or []):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                new = _scrub_text(item["text"], key, value)
                if new != item["text"]:
                    item["text"] = new or "(the proposal was dropped by a guard)"


# ------------------------------------------------------------------------ guard 1
def _g_no_crown_below_n(debrief, ctx):
    """No crown below N -- manual B.3 'N>=3 before any crown'; dyno's LOW-N.

    A finding that compares arms must carry evidence with BOTH arms' n >= 3, and those
    N must be in the pack's own dyno table, not in the model's imagination. Otherwise
    it is rewritten to an observation and the row is tagged low_n."""
    pack_ns = pack_arm_ns(ctx.pack)
    for i, f in enumerate(debrief.get("findings") or []):
        if not isinstance(f, dict):
            continue
        text = str(f.get("text") or "")
        if not COMPARISON_RE.search(text):
            continue
        cited = []
        for ev in (f.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            if N_FIELD_RE.match(str(ev.get("field") or "").strip()):
                n = _num(ev.get("value"))
                if n is not None:
                    cited.append(int(n))
        good = [n for n in cited if n >= 3 and n in pack_ns]
        if len(good) < 2:
            if f.get("kind") != "observation":
                f["kind"] = "observation"
            ctx.tag(debrief, "low_n")
            ctx.drop("no_crown_below_n",
                     "findings[%d] compares arms citing N %s; the pack's dyno arms "
                     "carry %s, so the verdict is demoted to an observation"
                     % (i, cited or "nowhere", pack_ns or "no arms"))


# ------------------------------------------------------------------------ guard 2
def _entry_hits(entry, rec, game_id):
    """Does one falsified entry match this RECOMMENDATION? Proposals only: a finding
    that names an item to REFUSE it is the disproof doing its job (manual F)."""
    scope = (entry.get("scope") or {}).get("game_id")
    if scope and game_id not in scope:
        return None
    m = entry.get("match") or {}
    keys = [str((ch or {}).get("yaml_key") or "").strip().lower()
            for ch in (rec.get("config_changes") or []) if isinstance(ch, dict)]
    for want in (m.get("yaml_key") or []):
        if str(want).strip().lower() in keys:
            return "config_changes names %r" % str(want).strip()
    dial = str(rec.get("driver_dial") or "")
    for tok in (m.get("tu_debug") or []):
        if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tok), dial, re.I):
            return "driver_dial names %r" % tok
    prose = "%s %s" % (rec.get("text") or "", dial)
    if rec.get("kind") not in ("config", "dial", "power"):
        # a next_run / investigate may MENTION a falsified token (the current arm's
        # name, a refutation) -- only the entry's own prose patterns can convict it
        # (e.g. "run an attract lap" for attract-mode trials as a crash-class probe)
        for rx in (m.get("text") or []):
            try:
                if re.search(rx, prose, re.I):
                    return "the %s recommendation re-opens it in prose" % rec.get("kind")
            except re.error:
                continue
        return None
    for group in ("tu_debug", "env", "power"):
        for tok in (m.get(group) or []):
            if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tok),
                         prose, re.I):
                return "the %s recommendation proposes %r" % (rec.get("kind"), tok)
    for rx in (m.get("text") or []):
        try:
            if re.search(rx, prose, re.I):
                return "the %s recommendation re-opens it in prose" % rec.get("kind")
        except re.error:
            continue
    return None


def _g_never_repropose_falsified(debrief, ctx):
    """Never re-propose section F -- 'never re-propose; the disproof is the asset'.

    Matches config_changes[].yaml_key, driver_dial and the prose of a config/dial/power
    recommendation against config/falsified.json. A match drops the whole
    recommendation and records the entry's disproof, so the operator sees WHY."""
    game_id = str(ctx.pack.get("game_id") or "")
    keep = []
    for rec in (debrief.get("recommendations") or []):
        if not isinstance(rec, dict):
            continue
        hit = None
        for entry in ctx.falsified:
            if not isinstance(entry, dict):
                continue
            why = _entry_hits(entry, rec, game_id)
            if why:
                hit = (entry, why)
                break
        if hit:
            entry, why = hit
            ctx.drop("never_repropose_falsified",
                     "%s is falsified (%s): %s -- %s"
                     % (entry.get("id"), entry.get("anchor") or "manual section F",
                        why, entry.get("disproof") or "never re-propose"))
        else:
            keep.append(rec)
    debrief["recommendations"] = keep


# ------------------------------------------------------------------------ guard 3
def _field_entries(fields, yaml_key):
    key = str(yaml_key).strip().lower()
    return [f for f in fields if str(f.get("yaml_key") or "").strip().lower() == key]


def _value_error(entry, value):
    """None when `value` is legal for this pitstop field, else why it is not."""
    kind = entry.get("type")
    if kind == "enum":
        opts = [str(o) for o in (entry.get("options") or [])]
        if str(value) in opts:
            return None
        return "is not one of the options %s" % "/".join(opts)
    if kind == "bool":
        if isinstance(value, bool) or str(value).lower() in ("true", "false"):
            return None
        return "is not a boolean"
    if kind == "int":
        n = _num(value)
        if n is None:
            return "is not a number"
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and n < lo or hi is not None and n > hi:
            return "is outside the range [%s, %s]" % (lo, hi)
        step = entry.get("step")
        if step and lo is not None and abs((n - lo) % step) > 1e-9:
            return "is not on the %s step from %s" % (step, lo)
        return None
    return None


def _g_schema_vocabulary_only(debrief, ctx):
    """Schema vocabulary only -- TUNING's section-aware injector refuses foreign keys.

    config_changes[].yaml_key must exist in pitstop_fields.json and new_value must be
    inside the field's own options or [min,max] on step. Anything else is dropped, and
    the prose that carried it is scrubbed with it."""
    for rec in (debrief.get("recommendations") or []):
        if not isinstance(rec, dict):
            continue
        keep = []
        for ch in (rec.get("config_changes") or []):
            if not isinstance(ch, dict):
                ctx.drop("schema_vocabulary_only", "a config change was not an object")
                continue
            key, val = ch.get("yaml_key"), ch.get("new_value")
            entries = _field_entries(ctx.fields, key)
            if not entries:
                ctx.drop("schema_vocabulary_only",
                         "yaml_key %r is not in pitstop_fields.json" % str(key))
                _scrub_debrief(debrief, ctx, str(key).strip(), val)
                continue
            errs = [_value_error(e, val) for e in entries]
            if all(errs):
                ctx.drop("schema_vocabulary_only",
                         "%r new_value %r %s" % (str(key), val, errs[0]))
                _scrub_debrief(debrief, ctx, str(key).strip(), val)
                continue
            keep.append(ch)
        rec["config_changes"] = keep
    _drop_empty_config_recs(debrief, ctx, "schema_vocabulary_only")


def _drop_empty_config_recs(debrief, ctx, kind):
    keep = []
    for rec in (debrief.get("recommendations") or []):
        if (isinstance(rec, dict) and rec.get("kind") == "config"
                and not rec.get("config_changes")):
            ctx.drop(kind, "a config recommendation lost every change it carried, so "
                           "the recommendation went with them")
            continue
        keep.append(rec)
    debrief["recommendations"] = keep


# ------------------------------------------------------------------------ guard 4
def _session_res(pack):
    cfg = (pack.get("config") or {}).get("values")
    if isinstance(cfg, dict):
        for k in ("  Resolution Scale", "Resolution Scale"):
            if k in cfg:
                return _num(cfg[k])
    return _num(_sess(pack).get("res_scale"))


def _crash_net_allows_resolution(ctx):
    """True when a crash signature ON THIS ROW lists Resolution Scale as its own fix."""
    have = {str(s).upper() for s in (_sess(ctx.pack).get("crash_sig") or [])}
    have |= {str((s or {}).get("id")).upper()
             for s in ((ctx.pack.get("crash") or {}).get("sigs") or [])}
    for sig in ctx.sigs:
        if str(sig.get("id") or "").upper() not in have:
            continue
        for ch in (sig.get("suggested_changes") or []):
            if str(ch.get("yaml_key") or "").strip() == "Resolution Scale":
                return str(sig.get("id"))
    return None


def _g_resolution_is_not_a_kpi_lever(debrief, ctx):
    """Resolution is not a KPI lever -- manual 2.1 'Resolution-lowering = cheating'.

    A Resolution Scale BELOW the session's own value is allowed only under a crash
    signature that lists it in suggested_changes, and then the debrief is tagged
    crash_net so the surface says what it bought: the crash net, not the KPI."""
    cur = _session_res(ctx.pack)
    if cur is None:
        cur = 100.0
    allowed_by = _crash_net_allows_resolution(ctx)
    for rec in (debrief.get("recommendations") or []):
        if not isinstance(rec, dict):
            continue
        keep = []
        for ch in (rec.get("config_changes") or []):
            key = str((ch or {}).get("yaml_key") or "").strip()
            new = _num((ch or {}).get("new_value"))
            if key != "Resolution Scale" or new is None or new >= cur:
                keep.append(ch)
                continue
            if allowed_by:
                ctx.tag(debrief, "crash_net")
                keep.append(ch)
                continue
            ctx.drop("resolution_is_not_a_kpi_lever",
                     "Resolution Scale %s -> %s is a KPI move, not a crash-net move; "
                     "no signature on this row lists it" % (cur, new))
            _scrub_debrief(debrief, ctx, key, (ch or {}).get("new_value"))
        rec["config_changes"] = keep
    _drop_empty_config_recs(debrief, ctx, "resolution_is_not_a_kpi_lever")


# ------------------------------------------------------------------------ guard 5
def _g_bake_and_aborted_are_not_feel_evidence(debrief, ctx):
    """Bake and ABORTED are not feel evidence -- manual B.3 'bake sessions lie'.

    A 7,423-shader run logged fps 30.8 where the real warm run was 20.0. On a bake or
    ABORTED pack the fps/perfect columns measured the compiler, so any finding that
    leans on them goes, and the only honest recommendation left is a warm run."""
    if not ({"bake", "aborted"} & ctx.tags):
        return
    keep = []
    for i, f in enumerate(debrief.get("findings") or []):
        text = str((f or {}).get("text") or "")
        if isinstance(f, dict) and FEEL_CLAIM_RE.search(text):
            ctx.drop("bake_and_aborted_are_not_feel_evidence",
                     "findings[%d] leans on a speed column on a %s row" % (
                         i, "bake" if "bake" in ctx.tags else "aborted"))
            continue
        keep.append(f)
    debrief["findings"] = keep
    kept = []
    for rec in (debrief.get("recommendations") or []):
        # `investigate` survives: attribution outranks narrative, and guard 6 would put
        # one back anyway. Everything else needs a warm run before it can mean anything.
        if isinstance(rec, dict) and rec.get("kind") not in ("next_run", "investigate"):
            ctx.drop("bake_and_aborted_are_not_feel_evidence",
                     "a %r recommendation cannot stand on a %s row; only a warm run "
                     "(or an investigation) can"
                     % (rec.get("kind"), "bake" if "bake" in ctx.tags else "aborted"))
            continue
        kept.append(rec)
    debrief["recommendations"] = kept


# ------------------------------------------------------------------------ guard 6
_INVESTIGATE_TEXT = {
    "keepalive_absent": "A GPU fault landed with rescues=0: the keepalive net did not "
                        "show up on this boot. Rule out our own code before any knob.",
    "panic_silent": "The kept message tail carries no lead-up to this reset. Arm the "
                    "flight recorder and reproduce before proposing anything.",
    "stack_change": "The stack tag moved under this comparison. Confirm what is "
                    "actually running before reading anything into the numbers.",
}


def _g_attribution_before_narrative(debrief, ctx):
    """Attribution before narrative -- manual B.3 'rule out our own code before blaming
    hardware'. keepalive_absent, panic_silent or stack_change force an `investigate`
    recommendation AHEAD of any tune advice."""
    forcing = [t for t in ("keepalive_absent", "panic_silent", "stack_change")
               if t in ctx.tags]
    if not forcing:
        return
    recs = [r for r in (debrief.get("recommendations") or []) if isinstance(r, dict)]
    first = next((i for i, r in enumerate(recs) if r.get("kind") == "investigate"), None)
    if first == 0:
        debrief["recommendations"] = recs
        return
    if first is not None:
        recs.insert(0, recs.pop(first))
        ctx.drop("attribution_before_narrative",
                 "an investigate recommendation was moved ahead of the tune advice "
                 "(%s)" % ", ".join(forcing))
    else:
        recs.insert(0, {"kind": "investigate",
                        "text": _INVESTIGATE_TEXT[forcing[0]],
                        "config_changes": [], "driver_dial": None,
                        "confidence": "high"})
        ctx.drop("attribution_before_narrative",
                 "no investigate recommendation was offered for %s; one was put first"
                 % ", ".join(forcing))
    debrief["recommendations"] = recs


# ------------------------------------------------------------------------ guard 7
def _prune(obj, schema, root, path, ctx):
    """Strip anything the contract does not name. A hallucinated field is a wrong
    answer wearing the right shape."""
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        ref = schema["$ref"]
        node = root
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        _prune(obj, node, root, path, ctx)
        return
    if isinstance(obj, dict):
        props = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for key in [k for k in obj if k not in props]:
                del obj[key]
                ctx.drop("data_never_commands",
                         "%s.%s is not in the contract and was stripped" % (path, key))
        for key, sub in props.items():
            if key in obj:
                _prune(obj[key], sub, root, "%s.%s" % (path, key), ctx)
    elif isinstance(obj, list):
        item = schema.get("items")
        if isinstance(item, dict):
            for i, val in enumerate(obj):
                _prune(val, item, root, "%s[%d]" % (path, i), ctx)


_FINDING_KINDS = ("observation", "mechanism", "anomaly")
_REC_KINDS = ("next_run", "config", "dial", "power", "no_change", "investigate")


def _g_data_never_commands(debrief, ctx):
    """Data, never commands -- spec 12 security.

    The debrief is JSON validated against DEBRIEF v1: unknown keys are stripped, prose
    is coerced to text, every missing contract field is filled from the pack. Nothing
    in it is ever executed, sourced, or written to a config by the service or the rig."""
    pack = ctx.pack
    debrief["schema"] = "ETK-RADIO-DEBRIEF v1"
    epoch = debrief.get("epoch", pack.get("epoch"))
    try:
        debrief["epoch"] = int(str(epoch))
    except (TypeError, ValueError):
        debrief["epoch"] = 0
    gid = str(debrief.get("game_id") or pack.get("game_id") or "unknown")
    debrief["game_id"] = re.sub(r"[^A-Za-z0-9_-]", "", gid)[:32] or "unknown"
    debrief["model"] = to_ascii(debrief.get("model") or "unknown")[:64]
    sha = str(debrief.get("prompt_sha256") or "")
    debrief["prompt_sha256"] = sha if re.match(r"^[0-9a-f]{64}$", sha) else "0" * 64
    com = str(debrief.get("corpus_commit") or "")
    debrief["corpus_commit"] = com if re.match(r"^[0-9a-f]{7,40}$", com) else "0" * 7
    tok = debrief.get("tokens")
    if not isinstance(tok, dict):
        tok = {}
    for k in ("prompt", "completion"):
        n = _num(tok.get(k))
        tok[k] = int(n) if n is not None and n >= 0 else 0
    debrief["tokens"] = {"prompt": tok["prompt"], "completion": tok["completion"]}
    lat = _num(debrief.get("latency_s"))
    debrief["latency_s"] = lat if lat is not None and lat >= 0 else 0.0
    for fld in ("radio", "headline"):
        val = debrief.get(fld)
        debrief[fld] = val if isinstance(val, str) else (
            "" if val is None else to_ascii(val))
    if not isinstance(debrief.get("tags"), list):
        debrief["tags"] = []
    debrief["tags"] = [re.sub(r"[^a-z0-9_]", "", str(t).lower())[:32]
                       for t in debrief["tags"]][:12]
    debrief["tags"] = [t for t in debrief["tags"] if t]

    keep = []
    for i, f in enumerate(debrief.get("findings") or []):
        if not isinstance(f, dict):
            ctx.drop("data_never_commands", "findings[%d] was not an object" % i)
            continue
        if f.get("kind") not in _FINDING_KINDS:
            f["kind"] = "observation"
        f["text"] = to_ascii(f.get("text") or "")
        ev = f.get("evidence")
        f["evidence"] = [e for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []
        keep.append(f)
    if len(keep) > 8:
        ctx.drop("data_never_commands",
                 "%d findings were offered; the contract caps them at 8" % len(keep))
    debrief["findings"] = keep[:8]

    kept = []
    for i, r in enumerate(debrief.get("recommendations") or []):
        if not isinstance(r, dict):
            ctx.drop("data_never_commands", "recommendations[%d] was not an object" % i)
            continue
        if r.get("kind") not in _REC_KINDS:
            ctx.drop("data_never_commands",
                     "recommendations[%d] kind %r is not in the contract"
                     % (i, r.get("kind")))
            continue
        r["text"] = to_ascii(r.get("text") or "")
        cc = r.get("config_changes")
        r["config_changes"] = [c for c in cc if isinstance(c, dict)][:5] \
            if isinstance(cc, list) else []
        dial = r.get("driver_dial")
        r["driver_dial"] = to_ascii(dial)[:120] if isinstance(dial, str) and dial else None
        if r.get("confidence") not in ("low", "medium", "high"):
            r["confidence"] = "medium"
        if "review_only" in r:
            r["review_only"] = bool(r["review_only"])
        nb = r.get("n_basis")
        if "n_basis" in r and (not isinstance(nb, dict)
                               or not {"arm", "n", "n_needed"} <= set(nb)):
            del r["n_basis"]
        kept.append(r)
    if len(kept) > 6:
        ctx.drop("data_never_commands",
                 "%d recommendations were offered; the contract caps them at 6"
                 % len(kept))
    debrief["recommendations"] = kept[:6]

    if not isinstance(debrief.get("run_sheet"), dict):
        debrief["run_sheet"] = None
    _prune(debrief, schemas.load("debrief.v1"), schemas.load("debrief.v1"), "$", ctx)


# ------------------------------------------------------------------------ guard 8
def _g_ascii_surfaces(debrief, ctx):
    """ASCII surfaces -- manual A.3 glyph law and the notification law.

    `radio` and `headline` land in a mako toast and in pit_note.txt, where a non-ASCII
    byte is a broken surface and a newline is a broken line. Transliterate, then
    truncate at a WORD boundary, never mid-word."""
    for fld, cap in (("radio", RADIO_MAX), ("headline", HEADLINE_MAX)):
        cur = debrief.get(fld) or ""
        new = trim(to_ascii(cur), cap)
        if new != cur:
            reason = ("%s was transliterated to ASCII" % fld if len(cur) <= cap
                      else "%s was %d chars and was truncated to %d at a word boundary"
                           % (fld, len(cur), cap))
            ctx.drop("ascii_surfaces", reason)
        debrief[fld] = new
    if not debrief.get("headline"):
        debrief["headline"] = pit_note(ctx.pack)
    for holder in ("findings", "recommendations"):
        for item in (debrief.get(holder) or []):
            item["text"] = trim(to_ascii(item.get("text") or ""), TEXT_MAX)
    rs = debrief.get("run_sheet")
    if isinstance(rs, dict):
        for fld in ("hypothesis", "stop_rule", "next"):
            if fld in rs:
                rs[fld] = trim(to_ascii(rs[fld]), RADIO_MAX)


# ------------------------------------------------------------------------ guard 9
def _g_evidence_beside_every_claim(debrief, ctx):
    """Evidence beside every claim -- manual B.3 'attribution outranks narrative' and
    the 2026-09-06 pre-prototype (spec 10.1), where models judged fragments as whole
    files. A finding whose evidence resolves to NOTHING in the pack is demoted to an
    observation and tagged `uncited`; the renderer prints what did resolve, verbatim."""
    for i, f in enumerate(debrief.get("findings") or []):
        ev = f.get("evidence") or []
        results = [resolve_evidence(e, ctx.pack) for e in ev]
        if any(ok for ok, _ in results):
            continue
        if f.get("kind") != "observation":
            f["kind"] = "observation"
        ctx.tag(debrief, "uncited")
        ctx.drop("evidence_beside_every_claim",
                 "findings[%d] cites nothing that resolves in the pack (%s)"
                 % (i, "; ".join(line for _, line in results) or "no evidence at all"))


# ----------------------------------------------------------------------- guard 10
def _g_never_truncate_silently(debrief, ctx):
    """Never truncate silently -- spec 10.1: three of four pre-prototype prompts were
    cut at 4,098 tokens and the models judged fragments as whole files. When the
    service tells us the context window it used, a prompt that did not fit is a FAILED
    debrief, not a shorter one. Ollama's truncation never decides what the model saw."""
    if ctx.num_ctx in (None, 0):
        return
    used = (debrief.get("tokens") or {}).get("prompt") or 0
    if used > ctx.num_ctx:
        ctx.drop("never_truncate_silently",
                 "over budget: the prompt was %s tokens against a num_ctx of %s, so "
                 "the model did not see the whole pack" % (used, ctx.num_ctx))


# ----------------------------------------------------------------------- guard 11
def _g_diagnosis_is_not_prescription(debrief, ctx):
    """Diagnosis is not prescription -- spec 10.2: both models diagnosed the env bomb
    precisely, then prescribed a fix that would break Law #2. A recommendation that
    reaches outside pitstop_fields.json -- a script, env.sh, a daemon, systemd, the
    kernel, install.sh, profile.d -- keeps its DIAGNOSIS and loses its hands: it is
    marked review_only, its config_changes are emptied, and it renders as
    'engineer to review'. It is never staged by LOAD FIX."""
    for i, rec in enumerate(debrief.get("recommendations") or []):
        blob = "%s %s %s" % (rec.get("text") or "", rec.get("driver_dial") or "",
                             json.dumps(rec.get("config_changes") or []))
        m = OUTSIDE_KIT_RE.search(blob)
        if not m:
            continue
        rec["review_only"] = True
        if rec.get("config_changes"):
            rec["config_changes"] = []
        ctx.drop("diagnosis_is_not_prescription",
                 "recommendations[%d] reaches outside the config vocabulary (%r); the "
                 "diagnosis is kept, the fix is for the engineer to review"
                 % (i, m.group(0)[:40]))


arm_label = _arm_label                 # the dial@clock label the surfaces all use
INVESTIGATE_TEXT = _INVESTIGATE_TEXT   # what "rule out our own code first" says


GUARDS = [
    ("no_crown_below_n",
     "manual B.3 'N>=3 before any crown'; dyno's LOW-N",
     _g_no_crown_below_n),
    ("never_repropose_falsified",
     "manual F 'never re-propose; the disproof is the asset'",
     _g_never_repropose_falsified),
    ("schema_vocabulary_only",
     "TUNING's section-aware injector refuses foreign keys",
     _g_schema_vocabulary_only),
    ("resolution_is_not_a_kpi_lever",
     "manual 2.1 'Resolution-lowering = cheating'",
     _g_resolution_is_not_a_kpi_lever),
    ("bake_and_aborted_are_not_feel_evidence",
     "manual B.3 'bake sessions lie'",
     _g_bake_and_aborted_are_not_feel_evidence),
    ("attribution_before_narrative",
     "manual B.3 'rule out our own code before blaming hardware'",
     _g_attribution_before_narrative),
    ("data_never_commands",
     "RADIO_SPEC 12 security: the debrief is data, never a command",
     _g_data_never_commands),
    ("ascii_surfaces",
     "manual A.3 glyph law, notification law",
     _g_ascii_surfaces),
    ("evidence_beside_every_claim",
     "manual B.3 'attribution outranks narrative'; RADIO_SPEC 10.1",
     _g_evidence_beside_every_claim),
    ("never_truncate_silently",
     "RADIO_SPEC 10.1: cut prompts were judged as whole files",
     _g_never_truncate_silently),
    ("diagnosis_is_not_prescription",
     "RADIO_SPEC 10.2: the right diagnosis, then an illegal fix",
     _g_diagnosis_is_not_prescription),
]


# --------------------------------------------------------------------------- apply
def minimal(pack, reason=None):
    """The debrief that is always safe to render: the pack's own computed facts, and
    nothing a model said. What a hopeless answer degrades to."""
    d = {
        "schema": "ETK-RADIO-DEBRIEF v1",
        "epoch": int(pack.get("epoch") or 0) if str(pack.get("epoch") or "0").isdigit() else 0,
        "game_id": re.sub(r"[^A-Za-z0-9_-]", "", str(pack.get("game_id") or "unknown"))[:32]
                   or "unknown",
        "model": "guards-minimal",
        "prompt_sha256": "0" * 64,
        "corpus_commit": "0" * 7,
        "tokens": {"prompt": 0, "completion": 0},
        "latency_s": 0.0,
        "radio": "No usable debrief for this row; the gauges below are the pack's own.",
        "headline": pit_note(pack),
        "tags": tagmod.compute(pack)[:12],
        "findings": [],
        "recommendations": [],
        "run_sheet": None,
        "guards": {"passed": False,
                   "dropped": [{"kind": "data_never_commands",
                                "reason": trim(to_ascii(reason or "the debrief could "
                                               "not be repaired into the contract"),
                                               REASON_MAX)}]},
    }
    return d


def apply(debrief, pack, *, falsified=None, fields=None, num_ctx=None):
    """Run the eleven over a debrief. Returns a NEW dict; the input is never mutated.

    `falsified` and `fields` default to the repo's config/falsified.json and
    config/pitstop_fields.json ($ETK_RADIO_CORPUS relocates the checkout).
    `num_ctx`, when the service passes the context window it actually used, arms the
    never-truncate-silently guard.

    Never raises: a guard that blows up records itself under guards.dropped, and a
    debrief that will not validate afterwards comes back as `minimal(pack)`."""
    pack = pack if isinstance(pack, dict) else {}
    ctx = _Ctx(pack, falsified, fields, num_ctx)
    try:
        d = copy.deepcopy(debrief) if isinstance(debrief, dict) else {}
    except (TypeError, ValueError):
        d = {}
    if not isinstance(debrief, dict):
        ctx.drop("data_never_commands", "the debrief was not a JSON object")

    for gid, _law, fn in GUARDS:
        try:
            fn(d, ctx)
        except Exception as exc:                                        # noqa: BLE001
            ctx.drop(gid, "the guard could not run: %s: %s"
                     % (type(exc).__name__, exc))

    d["guards"] = {"passed": not ctx.dropped, "dropped": ctx.dropped[:20]}
    errs = schemas.validate(d, schemas.load("debrief.v1"))
    if errs:
        out = minimal(pack, "the debrief did not validate: %s" % "; ".join(errs[:2]))
        out["guards"]["dropped"] = (ctx.dropped + out["guards"]["dropped"])[:20]
        return out
    return d
