#!/usr/bin/env python3
"""grantlib -- the hunt grant: validation, the etk-cloud always-free check, the audit chain.

docs/AUTONOMY_SPEC.md. A hunt grant is /etc/etk/grants/hunt.json, root-owned, written only
through `tools/hunt/grant.sh issue` (the sudo password is the operator's signature). Every
hunt layer -- hunt.py, the PreToolUse guard, later forge.sh --hunt and the car daemon --
reads it through load_grant(); nothing here can create or widen one.

The grant path is a constant on purpose: an env override would let a dave-owned file pass
as a grant. Tests call the functions with explicit paths and owner uids.
"""
import datetime as dt
import hashlib
import json
import os
import re
import stat

GRANT_PATH = "/etc/etk/grants/hunt.json"
SCHEMA = "etk-hunt-grant/1"
MAX_HOURS = 12.0
LANES = ("rpcs3", "turnip")              # grantable mint lanes; kernel/image never are
INJECT = ("emulators/hunt/", "drivers/hunt/", "debug_env")
NEVER = ("publish", "tags", "garage remote", "rig reboot", "CERT pins",
         "kernel/DTB/firmware", "/flash", "install/uninstall", "kernel lane", "image lane")
GAME_RE = re.compile(r"^[A-Z]{4}\d{5}$")
ID_RE = re.compile(r"^hunt-\d{8}-[a-z0-9][a-z0-9-]{0,31}$")
CAR_RE = re.compile(r"^car\d+$")
SKEW = 300                               # seconds of clock skew tolerated on issued_at
STATE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "state", "hunt"))

# Oracle Cloud always-free Ampere A1 allowance (per tenancy): 4 OCPU, 24 GB, 200 GB block.
FREE_SHAPE = "VM.Standard.A1.Flex"
FREE_OCPUS = 4.0
FREE_MEM_GB = 24.0
FREE_DISK_BYTES = 200 * 1024 ** 3
FINGERPRINT_KEYS = ("shape", "ocpus", "memory_gb", "image", "arch", "disk_bytes")


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


# ---- the node: etk-cloud must stay always-free sized (spec §1.1) -------------------------

def node_fingerprint(meta, sysinfo):
    """meta = OCI instance metadata (/opc/v2/instance/), sysinfo = {'arch', 'disk_bytes'}."""
    sc = meta.get("shapeConfig") or {}
    return {
        "shape": meta.get("shape"),
        "ocpus": float(sc.get("ocpus") or 0),
        "memory_gb": float(sc.get("memoryInGBs") or 0),
        "image": meta.get("image"),
        "arch": sysinfo.get("arch"),
        "disk_bytes": int(sysinfo.get("disk_bytes") or 0),
    }


def fingerprint_sha(fp):
    return sha256(canonical({k: fp.get(k) for k in FINGERPRINT_KEYS}))


def always_free(fp):
    """Problems that put the node outside the always-free allowance ([] = inside)."""
    p = []
    if fp.get("shape") != FREE_SHAPE:
        p.append(f"shape {fp.get('shape')} is not the always-free {FREE_SHAPE}")
    if not 0 < fp.get("ocpus", 0) <= FREE_OCPUS:
        p.append(f"{fp.get('ocpus')} OCPU exceeds the free {FREE_OCPUS:g}")
    if not 0 < fp.get("memory_gb", 0) <= FREE_MEM_GB:
        p.append(f"{fp.get('memory_gb')} GB memory exceeds the free {FREE_MEM_GB:g} GB")
    if not 0 < fp.get("disk_bytes", 0) <= FREE_DISK_BYTES:
        p.append(f"boot disk {fp.get('disk_bytes', 0) / 1024 ** 3:.0f} GiB exceeds the free 200 GB")
    return p


# ---- the grant ----------------------------------------------------------------------------

def check_fields(g, now):
    """Problems with a grant's content ([] = valid). Ownership is load_grant's job."""
    p = []
    if not isinstance(g, dict):
        return ["grant is not a JSON object"]
    if g.get("schema") != SCHEMA:
        p.append(f"schema {g.get('schema')!r} is not {SCHEMA}")
    if not ID_RE.match(str(g.get("id", ""))):
        p.append(f"id {g.get('id')!r} is malformed")
    if not GAME_RE.match(str(g.get("game", ""))):
        p.append(f"game {g.get('game')!r} is not a PS3 serial")
    try:
        issued, expires = parse_iso(g["issued_at"]), parse_iso(g["expires_at"])
    except (KeyError, TypeError, ValueError):
        return p + ["issued_at/expires_at missing or malformed"]
    if expires - issued > MAX_HOURS * 3600 + 1:
        p.append(f"spans {(expires - issued) / 3600:.1f} h, over the {MAX_HOURS:g} h cap")
    if expires <= issued:
        p.append("expires before it was issued")
    if issued > now + SKEW:
        p.append(f"issued in the future ({g['issued_at']})")
    if now >= expires:
        p.append(f"expired at {g['expires_at']}")
    if g.get("mode") not in ("overnight", "supervised"):
        p.append(f"mode {g.get('mode')!r} is neither overnight nor supervised")
    rig = g.get("rig") or {}
    if not CAR_RE.match(str(rig.get("car", ""))) or not rig.get("usb_serial"):
        p.append("rig must name one car and its USB serial")
    res = g.get("reserve") or {}
    if g.get("mode") == "overnight" and not res.get("verified"):
        p.append("overnight grant without a verified reserve car")
    if res.get("car") and res.get("car") == rig.get("car"):
        p.append("the reserve is the hunt car")
    lanes = (g.get("mint") or {}).get("lanes")
    if not isinstance(lanes, list) or not lanes or any(l not in LANES for l in lanes):
        p.append(f"mint lanes {lanes!r} must be a non-empty subset of {list(LANES)}")
    node = g.get("node") or {}
    fp = node.get("fingerprint") or {}
    if not node.get("sha") or node.get("sha") != fingerprint_sha(fp):
        p.append("node fingerprint sha does not match its fields")
    elif always_free(fp):
        p.append("node recorded outside always-free: " + "; ".join(always_free(fp)))
    if sorted(g.get("inject") or []) != sorted(INJECT):
        p.append("inject paths differ from the fixed set")
    return p


def check_owner(path, owner_uid=0):
    """Problems with who could have written the grant: the file and every parent directory
    must belong to owner_uid and be writable by nobody else."""
    p = []
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ["no grant"]
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return ["grant is not a regular file"]
    if st.st_uid != owner_uid:
        p.append(f"grant is owned by uid {st.st_uid}, not {owner_uid}")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        p.append("grant is group/other-writable")
    d = os.path.dirname(os.path.abspath(path))
    while True:
        ds = os.stat(d)
        if ds.st_uid not in (owner_uid, 0) or ds.st_mode & (stat.S_IWGRP | stat.S_IWOTH) \
                and not ds.st_mode & stat.S_ISVTX:
            p.append(f"directory {d} is writable by someone other than its owner, or not root's")
            break
        if d == "/":
            break
        d = os.path.dirname(d)
    return p


def load_grant(path=GRANT_PATH, owner_uid=0, now=None):
    """-> (grant or None, raw bytes or None, problems). Valid iff problems == []."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    p = check_owner(path, owner_uid)
    if p == ["no grant"] or p == ["grant is not a regular file"]:
        return None, None, p
    try:
        with open(path, "rb") as f:
            raw = f.read(65536)
        g = json.loads(raw)
    except (OSError, ValueError) as e:
        return None, None, p + [f"unreadable grant: {e}"]
    return g, raw, p + check_fields(g, now)


def in_scope(g, game=None, lane=None):
    p = []
    if game is not None and game != g.get("game"):
        p.append(f"game {game} is outside the grant ({g.get('game')})")
    if lane is not None and lane not in (g.get("mint") or {}).get("lanes", []):
        p.append(f"lane {lane} is not granted ({(g.get('mint') or {}).get('lanes')})")
    return p


# ---- the audit: hash-chained, bound to the grant it runs under -----------------------------

def hunt_dir(gid, grant_path=GRANT_PATH):
    """state/hunt/<id>/ for the real grant; beside the grant file for a test grant."""
    return os.path.join(STATE if grant_path == GRANT_PATH else os.path.dirname(grant_path), gid)


def audit_entry(prev, seq, action, args, result, t):
    e = {"seq": seq, "t": iso(t), "action": action, "args": args, "result": result, "prev": prev}
    e["hash"] = sha256(canonical(e))
    return e


def audit_append(path, genesis, action, args=None, result=None, now=None):
    """Append one line. genesis = sha256 of the grant bytes: the first line chains from it."""
    t = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    prev, seq = genesis, 0
    if os.path.exists(path):
        with open(path) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if lines:
            last = json.loads(lines[-1])
            prev, seq = last["hash"], last["seq"] + 1
    e = audit_entry(prev, seq, action, args if args is not None else [], result, t)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(canonical(e) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return e


def audit_verify(path, genesis):
    """-> (entries, problems). Tamper-EVIDENT, not tamper-proof: the file is the Engineer's,
    so the chain catches an edited, dropped or reordered line, and its first link binds it to
    the root-owned grant it ran under."""
    try:
        with open(path) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return [], ["no audit log"]
    out, prev = [], genesis
    for i, ln in enumerate(lines):
        try:
            e = json.loads(ln)
            body = {k: v for k, v in e.items() if k != "hash"}
        except (ValueError, AttributeError):
            return out, [f"line {i}: not JSON"]
        if e.get("seq") != i:
            return out, [f"line {i}: seq {e.get('seq')} (a line was dropped or reordered)"]
        if e.get("prev") != prev:
            return out, [f"line {i}: chain broken (prev does not match line {i - 1})"]
        if sha256(canonical(body)) != e.get("hash"):
            return out, [f"line {i}: hash mismatch (the line was edited)"]
        out.append(e)
        prev = e["hash"]
    return out, []
