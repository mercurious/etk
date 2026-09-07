#!/usr/bin/env python3
"""ETK RADIO — the host-side console (RADIO spec 2.2 row 11).

The rig builds packs and the node writes debriefs; this is the laptop's window on
both. In Phase 0 there is no node, so only the two local verbs are real:

    tools/radio.py pack 1788491975 --inspect     # build it, then read it
    tools/radio.py pack 1788491975 --stdout      # the JSON, nothing else
    tools/radio.py inspect 1788491975            # a stored pack, or build one live
    tools/radio.py inspect state/etk_telemetry/radio/1788491975.pack.json

`inspect` is the surface for the Phase-0 exit ("a pack for row 1788491975 inspected
by the operator"): a bounded ASCII render, 80 columns, no colour, no curses, that
says what the engineer will be told and what it will NOT be told. Every number is
the pack's own - this renders, it never recomputes.

`debrief`, `ask` and `eval` are registered here as stubs so the later waves have a
door to fill in rather than an argument to have; they exit 2 and say so.

Stdlib only; python 3.12 and 3.14. Reads the ledger and its sibling archive dirs
read-only, and writes only through bin/radio_pack.py's own atomic output path.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
PACKER = os.path.join(ROOT, "bin", "radio_pack.py")
DEFAULT_LEDGER = os.path.join(ROOT, "state", "etk_telemetry", "sessions.tsv")
W = 80
NOT_BUILT = "not built yet (RADIO Phase 0 wave 2)"


# ------------------------------------------------------------------ small helpers
def _s(v, dash="-"):
    """Anything -> a short ASCII cell; None and '' both read as an honest dash."""
    if v is None or v == "":
        return dash
    if isinstance(v, float):
        return ("%.1f" % v).rstrip("0").rstrip(".") if abs(v) < 1e6 else str(v)
    return str(v)


def _dur(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "-"
    return "%dm%02ds" % (sec // 60, sec % 60) if sec >= 60 else "%ds" % sec


def _rule(ch="-"):
    return ch * W


def _series(label, vals, width=6):
    """One timeline series as a single row, clipped to 80 columns."""
    cells = "".join(("%*s" % (width, _s(v))) for v in (vals or []))
    return "  %-10s%s" % (label, cells[:W - 12])


def _wrap(text, indent):
    out, line = [], ""
    for word in str(text).split():
        if len(line) + len(word) + 1 > W - indent:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return [(" " * indent) + ln for ln in out] or [(" " * indent) + "-"]


# ------------------------------------------------------------------------- render
def render(pack):
    """A pack as an 80-column terminal read. Returns the text (no trailing NL)."""
    L = []
    rig = pack.get("rig") or {}
    pwr = rig.get("power") or {}
    ses = pack.get("session") or {}
    hist = pack.get("history") or {}
    crash = pack.get("crash") or {}
    tl = pack.get("timeline")
    dyno = pack.get("dyno")
    budget = (pack.get("budget") or {}).get("bytes")

    L.append(_rule("="))
    L.append("RADIO PACK  %s  %s%s" % (
        _s(pack.get("epoch")), _s(pack.get("game_id")),
        ("%*s" % (W - 24 - len(_s(pack.get("game_id"))),
                  "%s B / 65536" % _s(budget))) if budget else ""))
    L.append("rig    %s  os %s  kit %s   stack %s" % (
        _s(rig.get("soc")), _s(rig.get("os")), _s(rig.get("kit")),
        _s(rig.get("stack"))))
    L.append("core   %s" % _s(rig.get("core")))
    L.append("dial   %s   build %s   patches %s" % (
        _s(rig.get("dial")), _s(rig.get("build")), _s(rig.get("patches"))))
    L.append("power  %s/%s @ %s MHz   res %s" % (
        _s(pwr.get("profile")), _s(pwr.get("grid")), _s(pwr.get("gpu_mhz")),
        _s(ses.get("res_scale"))))
    L.append(_rule())

    L.append("SESSION  %-22s %8s   shd %-5s drain %-5s temp %s/%s C" % (
        _s(ses.get("status")), _dur(ses.get("duration_s")),
        _s(ses.get("shaders_harvested")), _s(ses.get("drain_pct")),
        _s(ses.get("avg_temp")), _s(ses.get("peak_temp"))))
    L.append("KPI      perfect %-6s lock %-6s fps_med %-6s ft_p99 %-7s jit %-5s" % (
        _s(ses.get("perfect_pct")) + "%", _s(ses.get("lock_pct")) + "%",
        _s(ses.get("fps_med")), _s(ses.get("ft_p99_ms")) + "ms",
        _s(ses.get("ft_jitter_ms"))))
    aud = ses.get("aud") or {}
    L.append("AUDIO    up %-7s skip %-6s ur %-5s buf %-5s snd %-6s rescues %s" % (
        _s(aud.get("up_s")), _s(aud.get("skip")), _s(aud.get("ur")),
        _s(aud.get("buf_ms")), _s(ses.get("snd")), _s(ses.get("rescues"))))
    feel = (pack.get("operator") or {}).get("feel") or ""
    if feel:
        L.append("FEEL     %s" % feel[:W - 9])

    # --- crash -----------------------------------------------------------------
    sigs = crash.get("sigs") or []
    fault = crash.get("fault")
    L.append(_rule())
    if sigs or fault or ses.get("crash_sig"):
        L.append("CRASH")
        for sig in sigs:
            L.append("  %-24s %-7s %s" % (
                _s(sig.get("id")), _s(sig.get("severity")),
                _s(sig.get("summary"))[:W - 36]))
        known = {s.get("id") for s in sigs}
        for sid in (ses.get("crash_sig") or []):
            if sid not in known:
                L.append("  %-24s %-7s %s" % (sid, "?", "not in the catalog"))
        if fault:
            L.append("  fault %s  fence %s  -> %s" % (
                _s(fault.get("status")), _s(fault.get("fence_hex")),
                _s(fault.get("class"))))
        else:
            L.append("  fault -  (no GPU fault status on this row)")
    else:
        L.append("CRASH    none on this row")
    L.append("  evidence: rpcs3 %d line(s)  dmesg %d  blackbox %d" % (
        len(crash.get("rpcs3_errors") or []), len(crash.get("dmesg_window") or []),
        len(crash.get("blackbox_tail") or [])))
    for e in (crash.get("rpcs3_errors") or [])[:5]:
        L.append("    x%-4s %s" % (_s(e.get("n")), _s(e.get("line"))[:W - 11]))

    # --- dyno ------------------------------------------------------------------
    L.append(_rule())
    if dyno and dyno.get("arms"):
        L.append("DYNO     %s @ res %s   (N from the ledger, never guessed)" % (
            _s(dyno.get("game")), _s(dyno.get("res"))))
        L.append("  %-30s %3s %7s %6s %6s %6s %7s" % (
            "ARM (stack|tune|clk|pwr)", "N", "PERF%", "LOCK%", "JIT", "RESC/h",
            "CRASH"))
        for a in dyno["arms"]:
            label = "%s|%s|%s|%s" % (_s(a.get("stack")), _s(a.get("tune")),
                                     _s(a.get("clk")), _s(a.get("pwr")))
            L.append("  %-30s %3s %7s %6s %6s %6s %7s%s" % (
                label[:30], _s(a.get("n")), _s(a.get("perfect_p50")),
                _s(a.get("lock_p50")), _s(a.get("jit_p50")), _s(a.get("resc_h")),
                _s(a.get("crash")), "  LOW-N" if a.get("low_n") else ""))
        L.append("  no verdicts below N=3 (manual B.3); res<100 arms are context.")
    else:
        L.append("DYNO     none (no arms table in this pack)")

    # --- timeline --------------------------------------------------------------
    L.append(_rule())
    if tl and not tl.get("trimmed"):
        win = tl.get("lock_window_ms") or []
        L.append("TIMELINE %s bins%s" % (
            _s(tl.get("bins")),
            ("   lock window %s-%s ms" % (_s(win[0]), _s(win[1]))) if len(win) == 2
            else ""))
        L.append(_series("fps_med", tl.get("fps_med")))
        L.append(_series("ft_p99 ms", tl.get("ft_p99_ms")))
        L.append(_series("temp C", tl.get("temp_c")))
        L.append(_series("locked %", tl.get("perfect_windows")))
    elif tl:
        L.append("TIMELINE trimmed to fit the byte cap")
    else:
        L.append("TIMELINE none (no mango csv for this row)")

    # --- history + changes -----------------------------------------------------
    L.append(_rule())
    rows = hist.get("rows") or []
    L.append("HISTORY  last %d row(s) of this game" % len(rows))
    if rows:
        L.append("  %-11s %6s %-18s %5s %6s %6s %5s" % (
            "EPOCH", "DUR", "STATUS", "SHD", "FPS", "LOCK%", "PERF%"))
    for r in rows:
        L.append("  %-11s %6s %-18s %5s %6s %6s %5s" % (
            _s(r.get("epoch")), _dur(r.get("duration_s")),
            _s(r.get("status"))[:18], _s(r.get("shaders_harvested")),
            _s(r.get("fps_med")), _s(r.get("lock_pct")), _s(r.get("perfect_pct"))))
    car = hist.get("career") or {}
    if car:
        L.append("  career: %s sessions, %s%% clean, streak %s, %s shaders" % (
            _s(car.get("total_sessions")), _s(car.get("clean_rate_pct")),
            _s(car.get("current_streak")), _s(car.get("total_shaders"))))
    changes = hist.get("changes_since_last_debrief") or []
    L.append("CHANGES  since the last debrief: %d" % len(changes))
    for c in changes:
        L.append("  %-11s %s: %s -> %s" % (
            _s(c.get("epoch")), _s(c.get("field")), _s(c.get("old")),
            _s(c.get("new")))[:W])

    # --- run sheet -------------------------------------------------------------
    rs = pack.get("run_sheet")
    L.append(_rule())
    if rs:
        L.append("RUN SHEET  accepted %s   stack %s res %s" % (
            _s(rs.get("accepted_epoch")), _s(rs.get("stack")), _s(rs.get("res"))))
        L.extend(_wrap(rs.get("hypothesis") or "-", 2))
        for a in rs.get("arms") or []:
            L.append("  arm %-3s %-28s clk %-5s pwr %-6s n_target %s" % (
                _s(a.get("label")), _s(a.get("tune"))[:28], _s(a.get("clk")),
                _s(a.get("pwr")), _s(a.get("n_target"))))
        if rs.get("next"):
            L.extend(_wrap("NEXT: " + rs["next"], 2))
    else:
        L.append("RUN SHEET  none accepted for this game")

    # --- notes + budget --------------------------------------------------------
    L.append(_rule())
    notes = pack.get("pack_notes") or []
    L.append("PACK NOTES  %d  (every degradation is in the pack, never an abort)"
             % len(notes))
    for n in notes:
        L.extend(_wrap("- " + str(n), 2))
    if budget:
        pct = int(budget / 65536 * 100)
        L.append("BUDGET   %s B of 65536 (%d%%)  %s" % (
            budget, pct, "OK" if budget <= 65536 else "OVER CAP"))
    L.append(_rule("="))
    return "\n".join(line[:W].rstrip() for line in L)


# --------------------------------------------------------------------- packer glue
def run_packer(epoch, ledger, extra):
    """Call bin/radio_pack.py. Returns (returncode, stdout, stderr)."""
    argv = [sys.executable, PACKER, str(epoch), "--ledger", ledger] + list(extra)
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def load_pack(target, ledger):
    """A path, or an epoch: a stored pack if there is one, otherwise built live."""
    if os.path.sep in str(target) or str(target).endswith(".json"):
        with open(target, encoding="utf-8") as fh:
            return json.load(fh), target
    stored = os.path.join(os.path.dirname(ledger), "radio",
                          "%s.pack.json" % target)
    if os.path.exists(stored):
        with open(stored, encoding="utf-8") as fh:
            return json.load(fh), stored
    rc, out, err = run_packer(target, ledger, ["--stdout"])
    if rc != 0:
        sys.exit(err.strip() or "pack failed for %s" % target)
    return json.loads(out), "(built in memory, not written)"


# ------------------------------------------------------------------- subcommands
def cmd_pack(args):
    extra = []
    if args.stdout:
        extra.append("--stdout")
    elif args.out:
        extra += ["--out", args.out]
    rc, out, err = run_packer(args.epoch, args.ledger, extra)
    if err.strip():
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    if rc != 0:
        return rc
    if args.stdout:
        sys.stdout.write(out)
        if args.inspect:
            print(render(json.loads(out)))
        return 0
    sys.stdout.write(out)
    if args.inspect:
        m = re.search(r"^wrote (\S+)", out, re.M)
        path = m.group(1) if m else os.path.join(
            os.path.dirname(args.ledger), "radio", "%s.pack.json" % args.epoch)
        with open(path, encoding="utf-8") as fh:
            print(render(json.load(fh)))
    return 0


def cmd_inspect(args):
    pack, src = load_pack(args.target, args.ledger)
    print(render(pack))
    print("source: %s" % src)
    return 0


def cmd_stub(args):
    sys.stderr.write("radio %s: %s\n" % (args.verb, NOT_BUILT))
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tools/radio.py", description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER,
                    help="session ledger; its parent holds the archive dirs")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("pack", help="build PACK v1 for one epoch")
    p.add_argument("epoch")
    p.add_argument("--ledger", default=argparse.SUPPRESS,
                   help="override the global --ledger")
    p.add_argument("--out", help="write here instead of radio/<epoch>.pack.json")
    p.add_argument("--stdout", action="store_true", help="write the JSON to stdout")
    p.add_argument("--inspect", action="store_true", help="render it after building")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("inspect", help="render a stored pack, a pack file, or an epoch")
    p.add_argument("target", help="an epoch or a path to a .pack.json")
    p.add_argument("--ledger", default=argparse.SUPPRESS,
                   help="override the global --ledger")
    p.set_defaults(func=cmd_inspect)

    for verb, helptext in (("debrief", "ask the node for a debrief"),
                           ("ask", "one question about the last pack"),
                           ("eval", "score models against the golden cases")):
        p = sub.add_parser(verb, help="%s (%s)" % (helptext, NOT_BUILT))
        p.add_argument("rest", nargs="*")
        p.set_defaults(func=cmd_stub)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
