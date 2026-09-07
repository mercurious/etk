#!/usr/bin/env python3
"""ETK RADIO -- the tags the NODE computes from a pack (spec 3.2).

The debrief's `tags` are two things merged: what the model says, and what the
pipeline COMPUTES from the pack itself. Only the computed ones are asserted by the
eval, because only they are derivable from the row -- a model that forgets to say
`bake` has not made the shader storm go away.

This module is the computing half, and it is deliberately a twin of
`tools/radio/eval.py`'s `compute_tags`: the eval GRADES with its copy, the service
and the rules-only debrief BUILD with this one, and `tools/radio/test_guards.py`
asserts they agree on all twelve fixture packs. If you change a rule here, change it
there in the same commit, and let the parity test decide whether you got it right.

    compute(pack)       -> list[str], in a fixed order
    session_arms(pack)  -> the dyno arms that describe THIS session's condition

The tags, and what each one is FOR (spec 3.2, manual B.3):

    bake             shaders_harvested > 5 -- the speed columns measured the shader
                     compiler, not the game ("bake sessions lie": a 7,423-shader run
                     logged fps 30.8 where the real warm run was 20.0)
    aborted          the row never became a race
    attract          the operator marked it: attract-mode runs are invalid for
                     crash-class work (attract survived 1200 s+ where racing wedged
                     in about two minutes)
    low_n            no dyno arm at N >= 3 describes this session's condition, so
                     nothing on this row can be crowned (N>=3 before any crown)
    keepalive_absent a GPU fault status is present and rescues is exactly 0 -- our
                     own safety net did not show up, and that gets ruled out first
    panic_silent     a PANIC whose kept kmsg tail carries no lead-up at all

`stack_change` is NOT computed: the pack carries no per-history-row stack tag, so
there is nothing to compare against. The node may add it; no case asserts it.

Stdlib only, python 3.12+; pure functions, no I/O, ASCII surfaces.
"""
import re

__all__ = ["compute", "session_arms", "TAGS"]

# Every tag this module can emit, in the order compute() emits them.
TAGS = ("bake", "aborted", "attract", "low_n", "keepalive_absent", "panic_silent")

# The kmsg lead-up a panic must show before it is anything but silent.
_PANIC_LEAD = re.compile(r"(?i)(kernel panic|Oops|BUG:|Call trace|hung task|watchdog|"
                         r"rcu_sched|Unable to handle|smmu|page fault|gpu fault|"
                         r"fence timeout|a6xx|kgsl|adreno)")


def _sess(pack):
    return (pack.get("session") or {}) if isinstance(pack, dict) else {}


def _num(v):
    """A ledger cell as a float, or None. '1.2%' and ' 3 ' both read."""
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _dial(pack):
    """The session's TU_DEBUG dial; an absent dial is the 'default' arm, not None."""
    d = (pack.get("rig") or {}).get("dial") if isinstance(pack, dict) else None
    return (d or "default").strip() or "default"


def _arm_dial(arm):
    m = re.search(r"tu_debug=([^;]+)", arm.get("tune") or "")
    return m.group(1).strip() if m else "default"


def session_arms(pack):
    """The dyno arms describing THIS session's condition: same clock, same power rung,
    same dial. N comes from the dyno table -- never from a model, never guessed here.
    An empty list means the ledger has nothing that describes this row yet."""
    s, out = _sess(pack), []
    dial = _dial(pack)
    clk, pwr = s.get("gpu_mhz"), (s.get("pwr") or "").strip()
    if clk in (None, "") or not pwr:
        return out
    for arm in ((pack.get("dyno") or {}).get("arms") or []):
        if (arm.get("clk") == clk and (arm.get("pwr") or "") == pwr
                and _arm_dial(arm) == dial):
            out.append(arm)
    return out


def compute(pack):
    """The computed tags for a PACK v1 object. Same rules as eval.compute_tags."""
    s = _sess(pack)
    tags = []
    status = (s.get("status") or "").upper()
    sigs = [str(x).upper() for x in (s.get("crash_sig") or [])]

    if (s.get("shaders_harvested") or 0) > 5:
        tags.append("bake")
    if status.startswith("ABORTED"):
        tags.append("aborted")

    op = (pack.get("operator") or {}) if isinstance(pack, dict) else {}
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
        tail = ((pack.get("crash") or {}).get("blackbox_tail") or []) \
            if isinstance(pack, dict) else []
        if not any(_PANIC_LEAD.search(str(ln)) for ln in tail):
            tags.append("panic_silent")

    return tags
