#!/usr/bin/env python3
"""hunt.py -- the one command the Engineer runs for an autonomous crash hunt.

docs/AUTONOMY_SPEC.md §3. Every acting subcommand validates the grant (root-owned,
unexpired, in scope) and the audit chain first, and appends hash-chained lines to
state/hunt/<id>/audit.jsonl, whether it acts or refuses.

  hunt.py status [--probe]      the grant, time left, the audit chain; --probe re-reads
                                etk-cloud's shape and the host's USB (read-only)
  hunt.py audit [--tail N]      verify the chain, print the last N lines
  hunt.py check [--game G] [--lane L] [--id ID] [--node-host H]
                                exit 0 iff the grant is valid and in scope; --node-host
                                also re-reads that node's shape against the grant's
  hunt.py mint --base SHA [--patch patches/X.patch] [--marker SYM] [--label TEXT] [--dry-run]
                                one rpcs3 mint on etk-cloud: the base commit + the patch
                                committed on fork branch hunt/<id>, through forge.sh --hunt,
                                staged to emulators/hunt/ (P2). Long: run it in the background.

Not yet built (each validates, audits the attempt and refuses with its phase):
  put, pin, unpin, rollback, end (P3: car daemon + wrapper) · trial, recover, report (P4)
Exit: 0 ok · 1 no valid grant / refused / failed · 3 not built yet.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grantlib as gl  # noqa: E402

ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
FORGE = [os.path.join(ETK, "forge.sh")]
HUNT_STAGE = os.path.join(ETK, "emulators", "hunt")
READ_ONLY = ("status", "audit", "check")
LATER = {"put": "P3", "pin": "P3", "unpin": "P3", "rollback": "P3", "end": "P3",
         "trial": "P4", "recover": "P4", "report": "P4"}
FORK_FILES = ("scripts/package-appimage.sh", "scripts/verify-markers.sh")


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
    mints = [e for e in entries if e["action"] == "minted"]
    if mints:
        out(f"mints     {len(mints)}; last {mints[-1]['result'].get('artifact')}")
    rc = 0 if not problems and not ap else 1
    if a.probe:
        rc = max(rc, probe(g, out))
    return rc


def node_problems(g, host):
    """Read-only: is `host` the grant's node, still the shape it was granted on, still free?"""
    import grantctl
    if host != g["node"]["host"]:
        return [f"node {host} is not the grant's node ({g['node']['host']})"]
    try:
        fp = grantctl.probe_node(host)
    except grantctl.Refused as e:
        return [str(e)]
    p = [] if gl.fingerprint_sha(fp) == g["node"]["sha"] else ["node shape CHANGED since the grant"]
    return p + gl.always_free(fp)


def probe(g, out):
    """Read-only: does the world still match the grant? (node shape, hunt car on USB)"""
    import grantctl
    np = node_problems(g, g["node"]["host"])
    out("probe     node " + ("unchanged, always-free" if not np else "; ".join(np)))
    on = g["rig"]["usb_serial"] in grantctl.host_usb_serials()
    out(f"probe     hunt car on the host's USB: {'yes' if on else 'NO'}")
    return 0 if on and not np else 1


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
    if g and a.id and a.id != g.get("id"):
        p.append(f"the grant is {g.get('id')}, not {a.id}")
    if g and not p and a.node_host:
        p += node_problems(g, a.node_host)
    out("valid" if not p else "invalid: " + "; ".join(p))
    return 0 if not p else 1


# ---- mint (P2) ----------------------------------------------------------------------------

def git(fork, *args):
    r = subprocess.run(["git", "-C", fork, *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def fork_dir():
    conf = {}
    try:
        import grantctl
        conf = grantctl.read_conf(os.path.join(ETK, "etk.conf"))
    except Exception:
        pass
    return conf.get("FORGE_RPCS3_FORK") or os.path.expanduser("~/etk-rpcs3-gtk")


def prepare_mint(g, a, hdir, fork):
    """Extract the branch's committed inputs. -> dict(env, mdir, artifact, branch_sha, patch, patch_sha)."""
    branch = f"hunt/{g['id']}"
    branch_sha = git(fork, "rev-parse", "--verify", f"refs/heads/{branch}").decode().strip()
    patch = a.patch
    if not patch:
        found = [p for p in git(fork, "ls-tree", "--name-only", branch, "patches/").decode().split()
                 if p.endswith(".patch")]
        if len(found) != 1:
            raise RuntimeError(f"branch {branch} carries {len(found)} patches/*.patch ({found}); name one with --patch")
        patch = found[0]
    if not re.match(r"^patches/[A-Za-z0-9._-]+\.patch$", patch):   # it reaches node-side shell strings
        raise RuntimeError(f"patch path {patch!r} must be patches/<[A-Za-z0-9._-]>.patch")
    n = 1 + len([d for d in os.listdir(os.path.join(hdir, "mints"))]) if os.path.isdir(os.path.join(hdir, "mints")) else 1
    mdir = os.path.join(hdir, "mints", f"m{n:02d}")
    src = os.path.join(mdir, "src")
    for rel in (patch,) + FORK_FILES:
        data = git(fork, "show", f"{branch_sha}:{rel}")
        os.makedirs(os.path.dirname(os.path.join(src, rel)), exist_ok=True)
        with open(os.path.join(src, rel), "wb") as f:
            f.write(data)
    with open(os.path.join(src, patch), "rb") as f:
        patch_sha = gl.sha256(f.read())
    artifact = f"rpcs3-etk_{g['id']}-m{n:02d}_armsx3-{a.base[:9]}_linux_aarch64.AppImage"
    env = {"HUNT_BASE": a.base, "HUNT_FORK": src, "HUNT_PATCH": os.path.join(src, patch), "HUNT_ARTIFACT": artifact}
    if a.marker:
        env["HUNT_MARKER"] = a.marker
    return {"env": env, "mdir": mdir, "artifact": artifact, "branch": branch, "branch_sha": branch_sha,
            "patch": patch, "patch_sha": patch_sha, "n": n}


def cmd_mint(a, g, raw, out, now, grant_path, audit):
    genesis = gl.sha256(raw)
    if not re.match(r"^[0-9a-f]{7,40}$", a.base or ""):
        out("hunt mint: --base must be a commit sha (7-40 hex)")
        return 1
    if a.marker and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a.marker):
        out("hunt mint: --marker must be a symbol name")
        return 1
    p = gl.in_scope(g, lane="rpcs3")
    if p:
        gl.audit_append(audit, genesis, "mint", [a.base], {"refused": p})
        out(f"hunt mint: REFUSED -- {'; '.join(p)}")
        return 1
    hdir = gl.hunt_dir(g["id"], grant_path)
    os.makedirs(hdir, exist_ok=True)
    with open(os.path.join(hdir, "mint.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out("hunt mint: REFUSED -- a mint is already running for this hunt (one at a time)")
            return 1
        return mint_locked(a, g, hdir, out, audit, gl.sha256(raw))


def mint_locked(a, g, hdir, out, audit, genesis):
    try:
        m = prepare_mint(g, a, hdir, fork_dir())
    except (RuntimeError, OSError) as e:
        gl.audit_append(audit, genesis, "mint", [a.base, a.patch or ""], {"refused": str(e)})
        out(f"hunt mint: REFUSED -- {e}")
        return 1
    args = {"base": a.base, "branch": m["branch"], "branch_sha": m["branch_sha"], "patch": m["patch"],
            "patch_sha": m["patch_sha"], "artifact": m["artifact"], "label": a.label, "dry_run": a.dry_run}
    gl.audit_append(audit, genesis, "mint", args, "started")
    log = os.path.join(m["mdir"], "forge.log")
    cmd = FORGE + ["--hunt", g["id"], "rpcs3", "--verbose"] + (["--dry-run"] if a.dry_run else [])
    out(f"hunt mint m{m['n']:02d}: {a.base[:9]} + {m['patch']} ({m['branch']} @ {m['branch_sha'][:9]})"
        f"{' DRY RUN' if a.dry_run else ''}; log {log}")
    t0 = time.time()
    with open(log, "wb") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env={**os.environ, **m["env"]},
                            cwd=ETK).returncode
    minutes = round((time.time() - t0) / 60, 1)
    art = os.path.join(HUNT_STAGE, m["artifact"])
    result = {"rc": rc, "node_minutes": minutes, "log": os.path.relpath(log, ETK)}
    if rc == 0 and not a.dry_run and os.path.isfile(art):
        with open(art, "rb") as f:
            result.update(artifact=m["artifact"], sha256=gl.sha256(f.read()))
        action = "minted"
    else:
        with open(log, "rb") as f:
            result["tail"] = f.read()[-600:].decode(errors="replace")
        action = "mint dry-run" if a.dry_run and rc == 0 else "mint failed"
    gl.audit_append(audit, genesis, action, {"m": m["n"], "base": a.base}, result)
    out(f"hunt mint m{m['n']:02d}: {action.upper()} in {minutes} min" +
        (f" -- {os.path.relpath(art, ETK)} {result['sha256'][:12]}" if action == "minted" else f" (rc {rc}; {log})"))
    return 0 if action in ("minted", "mint dry-run") else 1


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
    s.add_argument("--id")
    s.add_argument("--node-host")
    s = sub.add_parser("mint")
    s.add_argument("--base", required=True)
    s.add_argument("--patch")
    s.add_argument("--marker")
    s.add_argument("--label", default="")
    s.add_argument("--dry-run", action="store_true")
    for name in LATER:
        sub.add_parser(name, add_help=False)
    a, a.rest = ap.parse_known_args(argv)
    if a.cmd not in LATER and a.rest:
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
    if a.cmd == "mint":
        return cmd_mint(a, g, raw, out, now, grant_path, audit)
    gl.audit_append(audit, gl.sha256(raw), a.cmd, a.rest, f"not built ({LATER[a.cmd]})", now)
    out(f"hunt {a.cmd}: grant {g['id']} is valid, but {a.cmd} lands in {LATER[a.cmd]} "
        f"(docs/AUTONOMY_SPEC.md §10); recorded in the audit")
    return 3


if __name__ == "__main__":
    sys.exit(main())
