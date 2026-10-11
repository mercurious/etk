#!/usr/bin/env python3
"""hunt.py -- the one command the Engineer runs for an autonomous crash hunt.

docs/AUTONOMY_SPEC.md §3. Every subcommand except the read-only ones validates the grant
(root-owned, unexpired, in scope) first and appends a hash-chained line to
state/hunt/<id>/audit.jsonl, whether it acts or refuses.

  hunt.py status [--probe]      the grant, time left, the audit chain; --probe re-reads
                                etk-cloud's shape and the host's USB (read-only)
  hunt.py audit [--tail N]      verify the chain, print the last N lines
  hunt.py check [--game G] [--lane L]   exit 0 iff the grant is valid (and in scope)

Built in P1: status, audit, check, and the validation every later subcommand runs.
Not yet built (each validates, audits the attempt and refuses with its phase):
  mint (P2: forge.sh --hunt) · put, pin, unpin, rollback, end (P3: car daemon + wrapper)
  trial, recover, report (P4: the supervised hunt)
Exit: 0 ok · 1 no valid grant / refused · 3 not built yet.
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grantlib as gl  # noqa: E402

READ_ONLY = ("status", "audit", "check")
LATER = {"mint": "P2", "put": "P3", "pin": "P3", "unpin": "P3", "rollback": "P3", "end": "P3",
         "trial": "P4", "recover": "P4", "report": "P4"}


def left(g, now):
    s = max(0, gl.parse_iso(g["expires_at"]) - now)
    return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m"


def cmd_status(a, g, raw, problems, out, now, grant_path):
    if g is None:
        out(f"hunt: no grant ({'; '.join(problems)}). The operator issues one: tools/hunt/grant.sh issue ...")
        return 1
    n = g.get("node", {}).get("fingerprint", {})
    out(f"grant     {g.get('id')}  {'VALID' if not problems else 'NOT VALID: ' + '; '.join(problems)}")
    out(f"game      {g.get('game')}   mode {g.get('mode')}   lanes {','.join(g.get('mint', {}).get('lanes', []))}")
    out(f"window    {g.get('issued_at')} -> {g.get('expires_at')}" + (f"  ({left(g, now)} left)" if not problems else ""))
    out(f"hunt car  {g.get('rig', {}).get('car')}  USB {g.get('rig', {}).get('usb_serial')}")
    res = g.get("reserve", {})
    out(f"reserve   {res.get('car')}  {'verified' if res.get('verified') else 'NOT verified'}"
        + (f", booted {res['booted_at']}" if res.get("booted_at") else ""))
    out(f"node      {g.get('node', {}).get('host')}  {n.get('shape')} {n.get('ocpus')} OCPU / {n.get('memory_gb')} GB"
        f"  fp {g.get('node', {}).get('sha', '')[:12]}")
    entries, ap = gl.audit_verify(os.path.join(gl.hunt_dir(g.get("id", "?"), grant_path), "audit.jsonl"), gl.sha256(raw))
    out(f"audit     {len(entries)} lines, chain " + ("intact" if not ap else "BROKEN: " + "; ".join(ap)))
    rc = 0 if not problems and not ap else 1
    if a.probe:
        rc = max(rc, probe(g, out))
    return rc


def probe(g, out):
    """Read-only: does the world still match the grant? (node shape, hunt car on USB)"""
    import grantctl
    rc = 0
    try:
        fp = grantctl.probe_node(g["node"]["host"])
        same = gl.fingerprint_sha(fp) == g["node"]["sha"]
        free = gl.always_free(fp)
        out(f"probe     node {'unchanged' if same else 'CHANGED'}" + (f"; NOT FREE: {'; '.join(free)}" if free else ""))
        rc = 0 if same and not free else 1
    except grantctl.Refused as e:
        out(f"probe     node: {e}")
        rc = 1
    usb = grantctl.host_usb_serials()
    on = g["rig"]["usb_serial"] in usb
    out(f"probe     hunt car on the host's USB: {'yes' if on else 'NO'}")
    return rc if on else 1


def cmd_audit(a, g, raw, problems, out, now, grant_path):
    if g is None:
        out(f"hunt: no grant ({'; '.join(problems)})")
        return 1
    entries, ap = gl.audit_verify(os.path.join(gl.hunt_dir(g["id"], grant_path), "audit.jsonl"), gl.sha256(raw))
    for e in entries[-a.tail:]:
        out(json.dumps({k: e[k] for k in ("seq", "t", "action", "args", "result")}))
    out(f"chain: {len(entries)} lines, " + ("intact" if not ap else "BROKEN: " + "; ".join(ap)))
    return 0 if not ap else 1


def cmd_check(a, g, raw, problems, out, now, grant_path):
    p = problems + (gl.in_scope(g, a.game, a.lane) if g else [])
    out("valid" if not p else "invalid: " + "; ".join(p))
    return 0 if not p else 1


def main(argv=None, out=print, grant_path=gl.GRANT_PATH, owner_uid=0, now=None):
    ap = argparse.ArgumentParser(prog="hunt.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--probe", action="store_true")
    s = sub.add_parser("audit")
    s.add_argument("--tail", type=int, default=20)
    s = sub.add_parser("check")
    s.add_argument("--game")
    s.add_argument("--lane")
    for name in LATER:
        sub.add_parser(name, add_help=False)
    a, a.rest = ap.parse_known_args(argv)
    if a.cmd in READ_ONLY and a.rest:
        ap.error(f"unrecognized arguments: {' '.join(a.rest)}")
    now = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    g, raw, problems = gl.load_grant(grant_path, owner_uid, now)
    if a.cmd in READ_ONLY:
        return {"status": cmd_status, "audit": cmd_audit, "check": cmd_check}[a.cmd](a, g, raw, problems, out, now, grant_path)

    if g is None or problems:
        out(f"hunt {a.cmd}: REFUSED -- no valid grant ({'; '.join(problems)})")
        return 1
    audit = os.path.join(gl.hunt_dir(g["id"], grant_path), "audit.jsonl")
    entries, ap_ = gl.audit_verify(audit, gl.sha256(raw))
    if ap_:
        out(f"hunt {a.cmd}: REFUSED -- the audit chain is broken ({'; '.join(ap_)}); the operator must look first")
        return 1
    gl.audit_append(audit, gl.sha256(raw), a.cmd, a.rest, f"not built ({LATER[a.cmd]})", now)
    out(f"hunt {a.cmd}: grant {g['id']} is valid, but {a.cmd} lands in {LATER[a.cmd]} "
        f"(docs/AUTONOMY_SPEC.md §10); recorded in the audit")
    return 3


if __name__ == "__main__":
    sys.exit(main())
