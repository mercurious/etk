#!/usr/bin/env python3
"""grantctl -- issue, show and revoke the hunt grant (run it as `tools/hunt/grant.sh`).

docs/AUTONOMY_SPEC.md §2. The operator runs this at a terminal as themself, never under
sudo: everything it runs is read-only probing as the operator, and root runs no repo code.
The single privileged step is `sudo install`ing the grant the operator just read, so the
sudo password is the signature.

  grant.sh issue --game BCUS98296 --hours 10 [--lanes rpcs3,turnip] [--name gt6]
                 [--rig car8] [--reserve car12] [--supervised]
  grant.sh show
  grant.sh revoke

issue checks, before it asks for the signature:
  guard    tools/hunt/guard.py is registered as a Claude Code PreToolUse hook
  node     etk-cloud's shape (OCI instance metadata) is inside the always-free allowance;
           its fingerprint goes into the grant and `hunt.py mint` re-checks it (spec §1.1)
  rig      the hunt car passes scripts/etk_car.sh verify, and the serial its USB gadget
           reports is one the host sees on 1d6b:0104 (the Pitlink link the hunt drives)
  reserve  the other car passes its car check, booted, and carries Pitstop. Without that the
           grant can only be --supervised (spec §1.1: with one car in service, no overnight)
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import grantlib as gl  # noqa: E402

RIG_GRANT = "/storage/.config/etk-hunt.grant"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class Refused(Exception):
    pass


def read_conf(path):
    """etk.conf KEY="value" lines, parsed, never executed (it is the Engineer's to edit)."""
    out = {}
    try:
        with open(path) as f:
            for ln in f:
                m = re.match(r'^\s*([A-Z][A-Z0-9_]*)=("([^"]*)"|\'([^\']*)\'|(\S*))', ln)
                if m:
                    out[m.group(1)] = next(g for g in m.group(3, 4, 5) if g is not None)
    except OSError:
        pass
    return out


def run(cmd, timeout=40, stdin=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 255, "", str(e)


# ---- probes (read-only, as the operator) --------------------------------------------------

NODE_PROBE = ('curl -s -m 5 -H "Authorization: Bearer Oracle" http://169.254.169.254/opc/v2/instance/;'
              ' echo; echo @@ETK@@; echo "arch=$(uname -m)";'
              ' echo "disk=$(lsblk -bdno SIZE,TYPE | awk \'$2=="disk"{s+=$1} END{print s+0}\')"')


def probe_node(host):
    rc, out, err = run(SSH + [host, NODE_PROBE])
    if rc != 0:
        raise Refused(f"node {host} unreachable (read-only ssh): {err.strip()[:200]}")
    meta_text, _, tail = out.partition("@@ETK@@")
    try:
        meta = json.loads(meta_text)
    except ValueError:
        raise Refused(f"node {host}: no OCI instance metadata, so its tier cannot be judged")
    kv = dict(ln.split("=", 1) for ln in tail.splitlines() if "=" in ln)
    return gl.node_fingerprint(meta, {"arch": kv.get("arch"), "disk_bytes": kv.get("disk")})


CAR_PROBE = ('cat /sys/kernel/config/usb_gadget/*/strings/0x409/serialnumber 2>/dev/null | head -n1;'
             ' cut -d" " -f1 /proc/uptime;'
             ' ls /storage/*/roms/etk/bin/etk_pitstop.py /storage/roms/etk/bin/etk_pitstop.py'
             ' 2>/dev/null | head -n1')


def probe_car(target, car):
    """-> dict(ok, message, usb_serial, booted_at, pitstop)."""
    rc, out, err = run(["bash", os.path.join(ETK, "scripts", "etk_car.sh"), "verify", target, car], timeout=60)
    msg = (out.strip() or err.strip()).splitlines()[-1:] or [""]
    res = {"ok": rc == 0 and "unassigned" not in out, "message": msg[0], "usb_serial": None,
           "booted_at": None, "pitstop": None}
    if rc != 0:
        return res
    rc2, out2, _ = run(SSH + [target, CAR_PROBE])
    if rc2 == 0:
        ln = out2.splitlines() + ["", "", ""]
        res["usb_serial"] = ln[0].strip() or None
        try:
            res["booted_at"] = gl.iso(dt.datetime.now(dt.timezone.utc).timestamp() - float(ln[1]))
        except ValueError:
            pass
        res["pitstop"] = ln[2].strip() or None
    return res


def host_usb_serials(root="/sys/bus/usb/devices"):
    """Serials of the ROCKNIX gadgets on the host's USB (Pitlink's 1d6b:0104)."""
    out = []
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, d)

        def rd(name):
            try:
                with open(os.path.join(p, name)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        if rd("idVendor") == "1d6b" and rd("idProduct") == "0104" and rd("manufacturer") == "ROCKNIX":
            out.append(rd("serial"))
    return out


def guard_registered():
    """Is tools/hunt/guard.py a PreToolUse hook in a settings file Claude Code loads here?"""
    for f in (os.path.join(ETK, ".claude", "settings.local.json"), os.path.join(ETK, ".claude", "settings.json"),
              os.path.expanduser("~/.claude/settings.json")):
        try:
            with open(f) as fh:
                pre = (json.load(fh).get("hooks") or {}).get("PreToolUse") or []
        except (OSError, ValueError):
            continue
        if any("tools/hunt/guard.py" in (h.get("command") or "") for m in pre for h in m.get("hooks", [])):
            return f
    return None


def tools_state():
    """What the operator is about to trust: the commit and whether tools/hunt is clean."""
    rc, head, _ = run(["git", "-C", ETK, "log", "-1", "--format=%h %cs %s", "--", "tools/hunt"])
    rc2, dirty, _ = run(["git", "-C", ETK, "status", "--porcelain", "--", "tools/hunt", "scripts/etk_car.sh"])
    return {"tools_hunt": head.strip() if rc == 0 else "?", "clean": rc2 == 0 and not dirty.strip()}


# ---- the privileged step: the operator's signature ----------------------------------------

def sudo_install(staged):
    d = os.path.dirname(gl.GRANT_PATH)
    script = (f'umask 022 && mkdir -p {d} && install -o root -g root -m 0644 "$1" {d}/.hunt.json.new'
              f' && mv -f {d}/.hunt.json.new {gl.GRANT_PATH}')
    return subprocess.run(["sudo", "--", "sh", "-c", script, "sh", staged]).returncode == 0


def sudo_remove():
    return subprocess.run(["sudo", "--", "rm", "-f", gl.GRANT_PATH]).returncode == 0


def rig_write(target, body):
    cmd = (f"umask 022; mkdir -p {os.path.dirname(RIG_GRANT)} && cat > {RIG_GRANT}.tmp"
           f" && mv -f {RIG_GRANT}.tmp {RIG_GRANT} && cat {RIG_GRANT}")
    rc, out, _ = run(SSH + [target, cmd], stdin=body)
    return rc == 0 and out == body


def rig_remove(target):
    return run(SSH + [target, f"rm -f {RIG_GRANT} {RIG_GRANT}.tmp"])[0] == 0


# ---- issue / show / revoke ----------------------------------------------------------------

def compose(a, now, node_host, fp, rig, rig_probe, reserve, res_probe, tools):
    gid = f"hunt-{dt.datetime.fromtimestamp(now):%Y%m%d}-{a.name or a.game.lower()}"  # the operator's date
    return {
        "schema": gl.SCHEMA,
        "id": gid,
        "issued_at": gl.iso(now),
        "expires_at": gl.iso(now + a.hours * 3600),
        "hours": a.hours,
        "game": a.game,
        "mode": "overnight" if res_probe["ok"] and res_probe["pitstop"] else "supervised",
        "rig": {"car": a.rig, "ssh": rig, "usb_serial": rig_probe["usb_serial"],
                "car_check": rig_probe["message"]},
        "reserve": {"car": a.reserve, "ssh": reserve,
                    "verified": bool(res_probe["ok"] and res_probe["pitstop"]),
                    "booted_at": res_probe["booted_at"], "pitstop": res_probe["pitstop"],
                    "car_check": res_probe["message"]},
        "node": {"host": node_host, "fingerprint": fp, "sha": gl.fingerprint_sha(fp)},
        "mint": {"lanes": a.lanes, "one_at_a_time": True, "budget": "hours"},
        "inject": list(gl.INJECT),
        "never": list(gl.NEVER),
        "issuer": {"user": getpass.getuser(), "host": socket.gethostname()},
        "tools": tools,
    }


def envelope(g, raw):
    r, res, n = g["rig"], g["reserve"], g["node"]["fingerprint"]
    lines = [
        "", "  HUNT GRANT  (docs/AUTONOMY_SPEC.md)", "",
        f"  id        {g['id']}",
        f"  game      {g['game']}",
        f"  window    {g['issued_at']} -> {g['expires_at']}  ({g['hours']:g} h)",
        f"  mode      {g['mode'].upper()}" + ("" if g["mode"] == "overnight" else
                                                "  (no verified reserve: run it awake, not overnight)"),
        f"  hunt car  {r['car']} at {r['ssh']}, USB serial {r['usb_serial']}",
        f"  reserve   {res['car']}: {'VERIFIED' if res['verified'] else 'NOT verified'} -- {res['car_check']}"
        + (f"; booted {res['booted_at']}" if res["booted_at"] else "")
        + (f"; Pitstop {res['pitstop']}" if res["pitstop"] else "; no Pitstop found"),
        f"  node      {g['node']['host']}: {n['shape']} {n['ocpus']:g} OCPU / {n['memory_gb']:g} GB /"
        f" {n['disk_bytes'] / 1024 ** 3:.0f} GiB, {n['arch']} -- inside always-free",
        f"  mint      lanes {', '.join(g['mint']['lanes'])}; one at a time; no count budget (compute is free)",
        f"  inject    {', '.join(g['inject'])}",
        f"  never     {', '.join(g['never'])}",
        f"  tools     tools/hunt at {g['tools']['tools_hunt']}"
        + ("" if g["tools"]["clean"] else "   ** UNCOMMITTED CHANGES in tools/hunt or etk_car.sh **"),
        f"  sha256    {gl.sha256(raw)}", "",
    ]
    return "\n".join(lines)


def issue(a, probes, confirm, signer, out=print, now=None, grant_path=gl.GRANT_PATH, owner_uid=0):
    """Testable core. probes: node(host), car(target, car), usb() -> serials, tools(), guard(), conf.
    confirm(prompt) -> str. signer: install(staged) -> bool, remove() -> bool,
    rig_write(target, body) -> bool. Returns the grant dict; raises Refused."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    if os.geteuid() == 0:
        raise Refused("run grant.sh as yourself, not under sudo: it asks for sudo only to sign")
    if not gl.GAME_RE.match(a.game):
        raise Refused(f"--game {a.game!r} is not a PS3 serial (e.g. BCUS98296)")
    if not 0 < a.hours <= gl.MAX_HOURS:
        raise Refused(f"--hours must be in (0, {gl.MAX_HOURS:g}]")
    if any(l not in gl.LANES for l in a.lanes) or not a.lanes:
        raise Refused(f"--lanes must be a subset of {','.join(gl.LANES)} (kernel and image are never grantable)")
    if a.name and not re.match(r"^[a-z0-9][a-z0-9-]{0,31}$", a.name):
        raise Refused("--name: lowercase letters, digits and dashes")
    if a.rig == a.reserve:
        raise Refused("the reserve car cannot be the hunt car")
    g0, _, p0 = gl.load_grant(grant_path, owner_uid, now)
    if g0 is not None and not p0:
        raise Refused(f"grant {g0['id']} is valid until {g0['expires_at']}: revoke it first (one hunt at a time)")

    if not probes["guard"]():
        raise Refused("the hunt guard (tools/hunt/guard.py) is not a PreToolUse hook in any Claude Code settings file")
    conf = probes["conf"]
    node_host = conf.get("FORGE_HOST", "etk-cloud")
    fp = probes["node"](node_host)
    nf = gl.always_free(fp)
    if nf:
        raise Refused(f"etk-cloud is outside the always-free allowance, so a mint may cost money: {'; '.join(nf)}")

    def target(car):
        t = conf.get(f"CAR{car[3:]}_SSH") if car.startswith("car") else None
        if not t:
            raise Refused(f"etk.conf has no CAR{car[3:]}_SSH for {car}")
        return t
    rig, reserve = target(a.rig), target(a.reserve)
    rp = probes["car"](rig, a.rig)
    if not rp["ok"]:
        raise Refused(f"hunt car {a.rig}: car check failed -- {rp['message']}")
    usb = probes["usb"]()
    if not rp["usb_serial"] or rp["usb_serial"] not in usb:
        raise Refused(f"hunt car {a.rig} reports USB serial {rp['usb_serial']!r}, but the host's ROCKNIX "
                      f"gadgets are {usb or 'none'}: plug {a.rig} into the host's USB")
    sp = probes["car"](reserve, a.reserve)
    verified = bool(sp["ok"] and sp["pitstop"])
    if not verified and not a.supervised:
        why = sp["message"] if not sp["ok"] else "no Pitstop (ETK) on the reserve"
        raise Refused(f"reserve {a.reserve} not verified ({why}). An overnight grant needs it; "
                      f"for a hunt run awake, add --supervised")

    g = compose(a, now, node_host, fp, rig, rp, reserve, sp, probes["tools"]())
    raw = (json.dumps(g, indent=1) + "\n").encode()
    sha = gl.sha256(raw)
    out(envelope(g, raw))
    if confirm(f"Sign it: type the grant id ({g['id']}) to continue, anything else aborts: ").strip() != g["id"]:
        raise Refused("not signed")

    sdir = gl.hunt_dir(g["id"], grant_path)
    os.makedirs(sdir, exist_ok=True)
    staged = os.path.join(sdir, "grant.json")
    with open(staged, "wb") as f:
        f.write(raw)
    out("sudo writes the grant (your password is the signature):")
    if not signer["install"](staged):
        raise Refused("sudo did not write the grant: nothing was granted")
    g2, raw2, p2 = gl.load_grant(grant_path, owner_uid, now)
    if raw2 is None or gl.sha256(raw2) != sha or p2:
        signer["remove"]()
        raise Refused(f"the installed grant is not the one you read (sha or validation: {p2}); removed it")
    gl.audit_append(os.path.join(sdir, "audit.jsonl"), sha, "issued", [a.game, f"{a.hours:g}h", ",".join(a.lanes)],
                    {"mode": g["mode"], "sha": sha}, now)

    body = (f"id={g['id']}\ngame={g['game']}\nexpires_at={g['expires_at']}\n"
            f"expires_epoch={int(gl.parse_iso(g['expires_at']))}\nusb_serial={g['rig']['usb_serial']}\n"
            f"lanes={','.join(a.lanes)}\n")
    if signer["rig_write"](rig, body):
        out(f"rig grant mirrored to {a.rig}:{RIG_GRANT}")
    else:
        out(f"WARNING: could not mirror the rig grant to {a.rig}:{RIG_GRANT} -- rig-side hunt ops will refuse")
    out(f"GRANTED {g['id']} ({g['mode']}) until {g['expires_at']}. Revoke: tools/hunt/grant.sh revoke")
    return g


def show(out=print, grant_path=gl.GRANT_PATH, owner_uid=0):
    g, raw, p = gl.load_grant(grant_path, owner_uid)
    if g is None:
        out(f"no grant ({'; '.join(p)})")
        return 1
    out(envelope(g, raw))
    out("VALID" if not p else "NOT VALID: " + "; ".join(p))
    return 0 if not p else 1


def revoke(conf, signer, out=print, grant_path=gl.GRANT_PATH, owner_uid=0):
    g, raw, _ = gl.load_grant(grant_path, owner_uid)
    rc = 0
    if g is None and not os.path.lexists(grant_path):
        out("no host grant to revoke")
    elif signer["remove"]():
        out(f"host grant {g['id'] if g else ''} removed")
    else:
        out("sudo did not remove the host grant")
        rc = 1
    rig = (g or {}).get("rig", {}).get("ssh") or conf.get("CAR8_SSH")
    if rig:
        out(f"rig grant on {rig}: " + ("removed" if signer["rig_remove"](rig) else
                                       "NOT removed (unreachable?) -- Pitstop TOOLS -> Autonomy off stops it at the car"))
    if g is not None and raw is not None:
        ap = os.path.join(gl.hunt_dir(g["id"], grant_path), "audit.jsonl")
        if os.path.exists(ap):
            gl.audit_append(ap, gl.sha256(raw), "revoked", [], {"by": getpass.getuser()})
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grant.sh", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue")
    i.add_argument("--game", required=True)
    i.add_argument("--hours", type=float, default=10.0)
    i.add_argument("--lanes", default="rpcs3", type=lambda s: [x for x in s.split(",") if x])
    i.add_argument("--name", default="")
    i.add_argument("--rig", default="car8")
    i.add_argument("--reserve", default="car12")
    i.add_argument("--supervised", action="store_true")
    sub.add_parser("show")
    sub.add_parser("revoke")
    a = ap.parse_args(argv)
    conf = read_conf(os.path.join(ETK, "etk.conf"))
    signer = {"install": sudo_install, "remove": sudo_remove, "rig_write": rig_write, "rig_remove": rig_remove}
    try:
        if a.cmd == "show":
            return show()
        if a.cmd == "revoke":
            return revoke(conf, signer)
        if not sys.stdin.isatty():
            raise Refused("issue needs the operator at a terminal (the signature is typed, not piped)")
        probes = {"conf": conf, "node": probe_node, "car": probe_car, "usb": host_usb_serials, "tools": tools_state,
                  "guard": guard_registered}
        issue(a, probes, input, signer)
        return 0
    except Refused as e:
        print(f"grant.sh: REFUSED -- {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
