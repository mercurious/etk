#!/usr/bin/env python3
"""etk_pitlink_usbd -- the car's end of Pitlink over raw USB (PLUSB v1, tools/pitlink/plusb.py).

Adds a vendor-class FunctionFS function ("ETK Pitlink", two bulk endpoints) to ROCKNIX's live
"cdc" composite gadget, NEXT TO the NCM network function (ssh keeps working), and relays every
USB channel into RPCS3's own Pitlink socket @etk-pitlink. No IP, no TCP on the Engineer's path.

  etk_pitlink_usbd.py serve    attach the function and relay until SIGTERM (systemd)
  etk_pitlink_usbd.py detach   remove the function, rebind NCM alone (idempotent)
  etk_pitlink_usbd.py status   print the gadget / function state

Garage (the link's second target, JSON lines): launch / running / games / notify / emukill,
through EmulationStation's own local API (127.0.0.1:1234) -- a launch is exactly a menu launch
(runemu.sh, the per-title core wrapper, the human's pad as Player 1); log / dump_threads (RPCS3's
log and ARMSX3's on-request thread dump); debug_env (allow-listed ARMSX3_* env for the next
launch, profile.d 099); hunt_status / put / pin / unpin / hunt_end -- the car's side of a hunt
grant (docs/AUTONOMY_SPEC.md §3.3, §4).

Safety: configfs is volatile (a reboot restores stock); functionfs is mounted no_disconnect=1
so a dead daemon cannot unbind the gadget under NCM; the attach rebinds the UDC and VERIFIES
NCM kept its address, else rolls back to NCM-only. Log: journal (stderr).
"""
import base64
import collections
import errno
import hashlib
import json
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (os.path.join(HERE, "..", "tools", "pitlink"), HERE):
    if os.path.exists(os.path.join(d, "plusb.py")):
        sys.path.insert(0, d)
        break
import plusb as L  # noqa: E402

VERSION = "etk_pitlink_usbd 1"
G = "/sys/kernel/config/usb_gadget/cdc"
FUNC = G + "/functions/ffs.pitlink"
LINK = G + "/configs/c.1/ffs.pitlink"
MNT = "/dev/ffs-pitlink"
TARGET = os.environ.get("PITLINK_USB_TARGET", L.CAR_TARGET)
ES_API = os.environ.get("ETK_ES_API", "http://127.0.0.1:1234")
# RPCS3's own dirs on the car: fs::get_config_dir() (dump_threads trigger) and its log.
RPCS3_CONFIG = os.environ.get("ETK_RPCS3_CONFIG", "/storage/.config/rpcs3/")
RPCS3_LOG = os.environ.get("ETK_RPCS3_LOG", "/storage/.cache/rpcs3/RPCS3.log")
LOG_REPLY_MAX = 512 * 1024
LOG_CHUNK = 1024 * 1024
# Diagnostic env for the NEXT game launch (start_rpcs3.sh sources /etc/profile -> profile.d).
# 099 sorts after the install-time 096 flags, so a debug run overrides them. Allow-listed and
# character-checked: this file is sourced by a shell at every launch.
DEBUG_ENV_FILE = os.environ.get("ETK_DEBUG_ENV_FILE", "/storage/.config/profile.d/099-etk-debug-env")
DEBUG_ENV_KEY = re.compile(r"^ARMSX3_[A-Z0-9_]+$")
DEBUG_ENV_VAL = re.compile(r"^[A-Za-z0-9_.,:-]*$")
SERIAL = re.compile(r"\b([A-Z]{4}\d{5})\b")
# Hunt grants (docs/AUTONOMY_SPEC.md §3.3, §4): the car's own check, independent of the host.
# put / pin write only emulators/hunt/ and drivers/hunt/, and only while the rig grant that
# grant.sh mirrored here is unexpired by the car's clock, names THIS car's USB serial, and the
# operator's Pitstop TOOLS -> Autonomy switch is on. Undoing (unpin, hunt_end) needs neither.
HUNT_GRANT_FILE = os.environ.get("ETK_HUNT_GRANT_FILE", "/storage/.config/etk-hunt.grant")
AUTONOMY_FILE = os.environ.get("ETK_AUTONOMY_FILE", "/storage/.config/etk-autonomy")
ETK_ROOT = os.environ.get("ETK_ROOT", "/storage/games-internal/roms/etk")
GADGET_SERIAL = G + "/strings/0x409/serialnumber"
HUNT_KINDS = {  # kind -> (dir under ETK_ROOT, name rule, size cap)
    "core": ("emulators/hunt", re.compile(r"^rpcs3-etk_hunt-[A-Za-z0-9._-]+\.AppImage$"), 512 << 20),
    "driver": ("drivers/hunt", re.compile(r"^etk_turnip_hunt-[A-Za-z0-9._-]+\.so$"), 128 << 20),
}
PUT_CHUNK_MAX = 1 << 20
CLOCK_SKEW = 300

# ---- FunctionFS ABI (include/uapi/linux/usb/functionfs.h) ---------------------------------
DESC_MAGIC_V2, STRINGS_MAGIC = 3, 2
HAS_FS, HAS_HS, HAS_SS = 1, 2, 4
EV_BIND, EV_UNBIND, EV_ENABLE, EV_DISABLE, EV_SETUP, EV_SUSPEND, EV_RESUME = range(7)
EV_NAMES = ("BIND", "UNBIND", "ENABLE", "DISABLE", "SETUP", "SUSPEND", "RESUME")
EVENT = struct.Struct("<BBHHHB3x")  # usb_ctrlrequest (8) + type + pad = 12


def descriptors():
    def iface():
        return struct.pack("<9B", 9, 4, 0, 0, 2, L.IF_CLASS, L.IF_SUBCLASS, L.IF_PROTOCOL, 1)

    def ep(addr, mps):
        return struct.pack("<BBBBHB", 7, 5, addr, 0x02, mps, 0)  # bulk

    ss_comp = struct.pack("<BBBBH", 6, 0x30, 0, 0, 0)
    fs = iface() + ep(0x81, 64) + ep(0x02, 64)                       # ep1 = IN, ep2 = OUT
    hs = iface() + ep(0x81, 512) + ep(0x02, 512)
    ss = iface() + ep(0x81, 1024) + ss_comp + ep(0x02, 1024) + ss_comp
    body = struct.pack("<III", 3, 3, 5) + fs + hs + ss
    return struct.pack("<III", DESC_MAGIC_V2, 12 + len(body), HAS_FS | HAS_HS | HAS_SS) + body


def strings():
    s = struct.pack("<H", 0x0409) + L.IF_STRING.encode() + b"\0"
    return struct.pack("<IIII", STRINGS_MAGIC, 16 + len(s), 1, 1) + s


def log(*a):
    print("[pitlink-usb]", *a, file=sys.stderr, flush=True)


def rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return ""


def wr(p, v):
    with open(p, "w") as f:
        f.write(v)


def ncm_iface():
    return rd(G + "/functions/ncm.usb0/ifname")


def iface_inet(ifname):
    if not ifname:
        return ""
    r = subprocess.run(["ip", "-4", "addr", "show", ifname], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            return line.split()[1]
    return ""


def is_mount(p):
    try:
        with open("/proc/mounts") as f:
            return any(line.split()[1] == p for line in f)
    except OSError:
        return False


# ---- gadget surgery ------------------------------------------------------------------------
class NotReady(RuntimeError):
    pass


def unbind():
    """Unbind the cdc gadget. It must be a NEWLINE: an empty write() is no syscall at all, the
    gadget stays bound, and configfs then refuses the config link with EINVAL (2026-10-10)."""
    wr(G + "/UDC", "\n")
    if rd(G + "/UDC"):
        raise OSError(f"cdc gadget did not unbind (UDC={rd(G + '/UDC')})")


def rebind(udc, with_us):
    """Unbind the cdc gadget, set our config link as asked, bind it again. True only if the
    link is as asked AND the gadget is bound again; it always tries to bind again."""
    unbind()
    linked = True
    try:
        if with_us and not os.path.islink(LINK):
            os.symlink(FUNC, LINK)
        if not with_us and os.path.islink(LINK):
            os.unlink(LINK)
    except OSError as e:
        log(f"config link ({'add' if with_us else 'remove'}): {e}")
        linked = False
    try:
        wr(G + "/UDC", udc)
    except OSError as e:
        log(f"bind {udc} failed: {e}")
    return linked and rd(G + "/UDC") == udc


def restore_ncm(addr, ifindex_before):
    """If the rebind cost NCM its address (a recreated netdev), re-apply exactly what ROCKNIX's
    usbgadget configure_iface does: the address, link up, and udhcpd on the new netdev."""
    ifname = ncm_iface()
    if not ifname or not addr:
        return False
    subprocess.run(["ip", "address", "add", addr, "dev", ifname], capture_output=True)
    subprocess.run(["ip", "link", "set", ifname, "up"], capture_output=True)
    if rd(f"/sys/class/net/{ifname}/ifindex") != ifindex_before and os.path.exists("/var/run/udhcpd.conf"):
        pid = rd("/var/run/udhcpd.pid")
        if pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
            time.sleep(0.3)
        subprocess.run(["/usr/sbin/udhcpd", "-S", "/var/run/udhcpd.conf"], capture_output=True)
        log("udhcpd restarted on the recreated netdev")
    return bool(iface_inet(ifname))


def attach():
    """Function instance + functionfs + descriptors, then the UDC rebind. Returns ep0 fd."""
    udc = rd(G + "/UDC")
    if not os.path.isdir(G) or not udc:
        raise NotReady("cdc gadget not bound (ROCKNIX USB mode is not 'network')")
    addr_before = iface_inet(ncm_iface())
    ifindex_before = rd(f"/sys/class/net/{ncm_iface()}/ifindex") if ncm_iface() else ""
    os.makedirs(FUNC, exist_ok=True)
    os.makedirs(MNT, exist_ok=True)
    if is_mount(MNT) and not os.path.islink(LINK):
        subprocess.run(["umount", MNT], check=False)  # a previous run's mount: start clean
    if not is_mount(MNT):
        subprocess.run(["mount", "-t", "functionfs", "-o", "no_disconnect=1", "pitlink", MNT], check=True)
    ep0 = os.open(MNT + "/ep0", os.O_RDWR)
    os.write(ep0, descriptors())
    os.write(ep0, strings())
    try:
        ok = rebind(udc, with_us=True)
    except OSError as e:
        log(f"rebind: {e}")
        ok = False
    addr_after = ""
    for _ in range(30):  # the netdev normally survives a rebind; give it a moment to report
        addr_after = iface_inet(ncm_iface())
        if addr_after or not addr_before:
            break
        time.sleep(0.1)
    if ok and addr_before and not addr_after:
        log(f"NCM lost {addr_before} in the rebind: re-applying it")
        if restore_ncm(addr_before, ifindex_before):
            addr_after = iface_inet(ncm_iface())
    if not ok or (addr_before and not addr_after):
        log(f"attach FAILED (bound={ok}, ncm {addr_before or '-'} -> {addr_after or '-'}): rolling back to NCM-only")
        os.close(ep0)
        try:
            rebind(udc, with_us=False)
        except OSError as e:
            log(f"rollback rebind: {e}")
        if addr_before and not iface_inet(ncm_iface()):
            restore_ncm(addr_before, ifindex_before)
        raise RuntimeError("attach rolled back")
    log(f"attached to {udc}: NCM {ncm_iface()} {addr_after or '(no inet before either)'} + ETK Pitlink")
    return ep0


def detach():
    udc = rd(G + "/UDC")
    if os.path.islink(LINK):
        if udc:
            ok = rebind(udc, with_us=False)
            log(f"detached; NCM rebound to {udc}: {'ok' if ok else 'FAILED'}")
        else:
            os.unlink(LINK)
    if is_mount(MNT):
        subprocess.run(["umount", MNT], check=False)
    if os.path.isdir(FUNC) and not os.path.islink(LINK):
        try:
            os.rmdir(FUNC)
        except OSError:
            pass


# ---- garage: car control over the link (EmulationStation's own API) -----------------------
def es(path, body=None, timeout=5.0):
    data = body.encode() if body is not None else None
    req = urllib.request.Request(ES_API + path, data=data, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def game_serial(g):
    """A .psn entry holds just its serial; disc entries carry it in the name: '(BLUS30019)'."""
    path = g.get("path") or ""
    if path.endswith(".psn"):
        try:
            with open(path, errors="replace") as f:
                t = f.read(64).strip()
            if SERIAL.fullmatch(t):
                return t
        except OSError:
            pass
    m = SERIAL.search(f"{g.get('name') or ''} {os.path.basename(path)}")
    return m.group(1) if m else ""


def games(system="ps3"):
    return [{"name": g.get("name") or "", "path": g.get("path") or "", "serial": game_serial(g),
             "lastplayed": g.get("lastplayed") or ""} for g in json.loads(es(f"/systems/{system}/games"))]


def resolve(what, system="ps3"):
    """path | serial (several entries: the most recently played wins, the rest are reported) |
    name substring (must be unambiguous)."""
    gs = games(system)
    if what.startswith("/"):
        m = [g for g in gs if g["path"] == what]
    elif SERIAL.fullmatch(what.upper()):
        m = [g for g in gs if g["serial"] == what.upper()]
    else:
        w = what.lower()
        m = [g for g in gs if w in g["name"].lower() or w in os.path.basename(g["path"]).lower()]
        if len(m) > 1:
            raise LookupError(f"{what!r} matches {len(m)} {system} games: " + "; ".join(g["name"] for g in m[:8]))
    if not m:
        raise LookupError(f"no {system} game matches {what!r}")
    m.sort(key=lambda g: g["lastplayed"], reverse=True)
    return m[0], m[1:]


def running():
    r = json.loads(es("/runningGame") or "{}")
    return None if r.get("msg") == "NO GAME RUNNING" or not r else {"name": r.get("name"), "path": r.get("path")}


def garage(req):
    op = req.get("op")
    if op == "running":
        return {"running": running()}
    if op == "games":
        gs = games(req.get("system", "ps3"))
        f = (req.get("filter") or "").lower()
        return {"games": [g for g in gs if not f or f in g["name"].lower() or f in g["serial"].lower()]}
    if op == "launch":
        cur = running()
        if cur:
            raise RuntimeError(f"a game is already running: {cur['name']} (exit it first: pitlink.py exit)")
        g, also = resolve(str(req.get("game", "")), req.get("system", "ps3"))
        reply = es("/launch", body=g["path"])
        log(f"garage: launch {g['name']} ({g['serial'] or '-'}) -> {reply.strip()[:80]}")
        return {"launched": g, "also": [a["name"] for a in also], "es": reply.strip()[:200]}
    if op == "notify":
        return {"es": es("/notify", body=str(req.get("text", ""))[:200]).strip()[:200]}
    if op == "emukill":
        if not req.get("force"):
            raise RuntimeError("emukill is a hard kill (no graceful RPCS3 exit, no cache banking): "
                               "use pitlink.py exit; pass force to insist")
        return {"es": es("/emukill").strip()[:200]}
    if op == "status":
        return {"daemon": VERSION, "running": running()}
    if op == "log":
        return read_log(req)
    if op == "dump_threads":
        return dump_threads(float(req.get("timeout", 6.0)))
    if op == "debug_env":
        return debug_env(req)
    if op in HUNT_OPS:
        return HUNT_OPS[op](req)
    raise ValueError(f"unknown garage op {op!r} (launch, running, games, notify, emukill, status, log, dump_threads, "
                     f"debug_env, {', '.join(HUNT_OPS)})")


def log_size():
    try:
        return os.path.getsize(RPCS3_LOG)
    except OSError:
        return 0


def read_log(req):
    """RPCS3.log by byte offset: {"from": off} (default: the last `tail` lines), optional
    {"grep": regex}. Replies capped at LOG_REPLY_MAX bytes of text, with the next offset."""
    size = log_size()
    start = req.get("from")
    tail = int(req.get("tail", 200))
    rx = re.compile(req["grep"]) if req.get("grep") else None
    # Streamed, never slurped: a flooding title (RR7's spup spin, ~1 MB/s) grows the log past
    # 400 MB, and one read from an old offset held it all three times over (bytes, str, lines)
    # -- the daemon was OOM-killed at 4 GB on 2026-10-20. Only the reply's tail is kept.
    kept, kept_len, count = collections.deque(), 0, 0
    with open(RPCS3_LOG, "rb") as f:
        if start is None:
            f.seek(max(0, size - 4 * 1024 * 1024))
        else:
            f.seek(min(int(start), size))
        for ln in _log_lines(f, size - f.tell()):
            if rx and not rx.search(ln):
                continue
            count += 1
            kept.append(ln)
            kept_len += len(ln) + 1
            while kept and (kept_len - len(kept[0]) - 1 > LOG_REPLY_MAX
                            or (start is None and len(kept) > tail)):
                kept_len -= len(kept.popleft()) + 1
    text = "\n".join(kept)
    if len(text) > LOG_REPLY_MAX:
        text = text[-LOG_REPLY_MAX:]
    return {"size": size, "lines": min(count, tail) if start is None else count, "text": text}


def _log_lines(f, limit):
    """The decoded lines of the next `limit` bytes of `f`, a chunk at a time."""
    carry = b""
    while limit > 0:
        chunk = f.read(min(LOG_CHUNK, limit))
        if not chunk:
            break
        limit -= len(chunk)
        *whole, carry = (carry + chunk).split(b"\n")
        for b in whole:
            yield from b.decode(errors="replace").splitlines() or [""]
    if carry:
        yield from carry.decode(errors="replace").splitlines()


def read_debug_env():
    env = {}
    try:
        with open(DEBUG_ENV_FILE) as f:
            for ln in f:
                m = re.match(r"^export ([A-Z0-9_]+)='(.*)'$", ln.strip())
                if m:
                    env[m.group(1)] = m.group(2)
    except FileNotFoundError:
        pass
    return env


def debug_env(req):
    """{"action": "show" | "set" | "clear", ...}. "set" REPLACES the whole file with the
    ARMSX3_* keys given (top-level fields or "env": {}); it applies at the next launch."""
    action = req.get("action", "show")
    if action == "show":
        return {"file": DEBUG_ENV_FILE, "env": read_debug_env()}
    if action == "clear":
        for p in (DEBUG_ENV_FILE, os.path.join(os.path.dirname(DEBUG_ENV_FILE),
                                               "." + os.path.basename(DEBUG_ENV_FILE) + ".tmp")):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        log("garage: debug_env cleared")
        return {"file": DEBUG_ENV_FILE, "env": {}}
    if action != "set":
        raise ValueError(f"debug_env action {action!r}: show | set | clear")
    env = dict(req.get("env") or {})
    env.update({k: v for k, v in req.items() if k.startswith("ARMSX3_")})
    if not env:
        raise ValueError("debug_env set: no ARMSX3_* keys given (use clear to remove all)")
    for k, v in env.items():
        v = str(v)
        if not DEBUG_ENV_KEY.match(k):
            raise ValueError(f"debug_env: key {k!r} is not an ARMSX3_* diagnostic")
        if not DEBUG_ENV_VAL.match(v):
            raise ValueError(f"debug_env: value for {k} has characters outside [A-Za-z0-9_.,:-]")
        env[k] = v
    body = "# ETK debug env (garage debug_env) -- applies at the next game launch; clear when done.\n"
    body += "".join(f"export {k}='{v}'\n" for k, v in sorted(env.items()))
    d = os.path.dirname(DEBUG_ENV_FILE)
    tmp = os.path.join(d, "." + os.path.basename(DEBUG_ENV_FILE) + ".tmp")  # profile.d skips dotfiles
    with open(tmp, "w") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DEBUG_ENV_FILE)
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    log(f"garage: debug_env set {' '.join(f'{k}={v}' for k, v in sorted(env.items()))}")
    return {"file": DEBUG_ENV_FILE, "env": read_debug_env()}


# ---- hunt ops (docs/AUTONOMY_SPEC.md §3.3) -------------------------------------------------

def hunt_dir(kind):
    return os.path.join(ETK_ROOT, HUNT_KINDS[kind][0])


def override_file():
    return os.path.join(hunt_dir("core"), "override.tsv")


def rig_grant():
    """grant.sh's key=value mirror, or None."""
    try:
        with open(HUNT_GRANT_FILE) as f:
            return dict(ln.rstrip("\n").split("=", 1) for ln in f if "=" in ln)
    except OSError:
        return None


def own_serial():
    return (rd(GADGET_SERIAL) or "").strip()


def autonomy():
    """Exactly "on" (trailing newlines aside): Pitstop's and the launch wrapper's test."""
    try:
        with open(AUTONOMY_FILE) as f:
            return "on" if f.read().rstrip("\n") == "on" else "off"
    except OSError:
        return "off"


def gate_problems(game=None, now=None):
    now = time.time() if now is None else now
    g = rig_grant()
    if g is None:
        return ["no rig grant (the operator issues one: tools/hunt/grant.sh issue)"]
    p = []
    try:
        exp, iss = int(g.get("expires_epoch", "0")), int(g.get("issued_epoch", "0"))
    except ValueError:
        return ["the rig grant is malformed"]
    if now >= exp:
        p.append(f"the rig grant expired at {g.get('expires_at')}")
    if now < iss - CLOCK_SKEW:
        p.append("the car's clock is behind the grant's issue time (unsynced?): expiry cannot be judged")
    if g.get("usb_serial") != own_serial():
        p.append("the rig grant names another car's USB serial")
    if autonomy() != "on":
        p.append("Pitstop TOOLS -> Autonomy is off")
    if game is not None and game != g.get("game"):
        p.append(f"game {game} is outside the grant ({g.get('game')})")
    return p


def gate(game=None):
    p = gate_problems(game)
    if p:
        raise PermissionError("hunt refused at the car: " + "; ".join(p))
    return rig_grant()


def read_override():
    rows = {}
    try:
        with open(override_file()) as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) == 3 and not ln.startswith("#"):
                    rows[parts[0]] = {"core": parts[1], "driver": parts[2]}
    except OSError:
        pass
    return rows


def write_atomic(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "." + os.path.basename(path) + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_override(rows):
    body = "# ETK hunt override (garage pin) -- the launch wrapper honours it only under a valid rig grant\n"
    body += "".join(f"{g}\t{r['core']}\t{r['driver']}\n" for g, r in sorted(rows.items()))
    write_atomic(override_file(), body.encode())


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hunt_files(kind):
    out = []
    try:
        names = sorted(os.listdir(hunt_dir(kind)))
    except OSError:
        return out
    for n in names:
        if HUNT_KINDS[kind][1].match(n):
            sha = (rd(os.path.join(hunt_dir(kind), n + ".sha256")) or "").split(" ")[0]
            out.append({"name": n, "size": os.path.getsize(os.path.join(hunt_dir(kind), n)), "sha256": sha})
    return out


def hunt_status(req):
    return {"grant": rig_grant(), "refusals": gate_problems(), "autonomy": autonomy(), "serial": own_serial(),
            "now": int(time.time()), "override": read_override(),
            "files": {k: hunt_files(k) for k in HUNT_KINDS}}


def hunt_put(req):
    """{"kind": core|driver, "name", "total", "sha256", "offset", "data": base64} -- sequential
    chunks into .<name>.part; the last one verifies the sha256 and renames into place."""
    kind, name = req.get("kind"), str(req.get("name", ""))
    if kind not in HUNT_KINDS or not HUNT_KINDS[kind][1].match(name):
        raise ValueError(f"put: {kind}/{name!r} is not a hunt artifact name")
    gate()
    total, offset, want = int(req["total"]), int(req["offset"]), str(req["sha256"])
    data = base64.b64decode(req.get("data", ""), validate=True)
    if not 0 < total <= HUNT_KINDS[kind][2] or len(data) > PUT_CHUNK_MAX or offset + len(data) > total:
        raise ValueError("put: size out of bounds")
    d = hunt_dir(kind)
    final, part = os.path.join(d, name), os.path.join(d, "." + name + ".part")
    if os.path.exists(final):
        if file_sha256(final) == want:
            return {"received": total, "done": True, "sha256": want, "already": True}
        raise FileExistsError(f"put: {name} exists with different content (hunt artifacts are never replaced)")
    os.makedirs(d, exist_ok=True)
    if offset == 0:
        import shutil
        if shutil.disk_usage(d).free < total + (256 << 20):
            raise OSError(errno.ENOSPC, f"put: not enough space for {total} bytes in {d}")
        open(part, "wb").close()
    elif not os.path.exists(part) or os.path.getsize(part) != offset:
        raise ValueError(f"put: chunk at {offset} out of sequence (restart from 0)")
    with open(part, "ab") as f:
        f.write(data)
    got = offset + len(data)
    if got < total:
        return {"received": got, "done": False}
    sha = file_sha256(part)
    if sha != want:
        os.remove(part)
        raise ValueError(f"put: sha256 mismatch ({sha[:12]} != {want[:12]}): discarded")
    if kind == "core":
        os.chmod(part, 0o755)
    os.replace(part, final)
    write_atomic(final + ".sha256", f"{sha}  {name}\n".encode())
    log(f"garage: hunt put {kind} {name} {total} B sha {sha[:12]}")
    return {"received": got, "done": True, "sha256": sha}


def hunt_pin(req):
    """{"game", "core"?, "driver"?} -- the wrapper runs this game on these hunt artifacts."""
    g = gate(req.get("game"))
    row = {"core": "-", "driver": "-"}
    for kind in HUNT_KINDS:
        name = req.get(kind)
        if not name:
            continue
        path = os.path.join(hunt_dir(kind), str(name))
        if not HUNT_KINDS[kind][1].match(str(name)) or not os.path.isfile(path):
            raise FileNotFoundError(f"pin: no hunt {kind} {name!r} on the car (put it first)")
        side = (rd(path + ".sha256") or "").split(" ")[0]
        if not side or file_sha256(path) != side:
            raise ValueError(f"pin: {name} does not match its .sha256: refusing")
        row[kind] = str(name)
    if row == {"core": "-", "driver": "-"}:
        raise ValueError("pin: name a core and/or a driver")
    rows = read_override()
    rows[g["game"]] = row
    write_override(rows)
    log(f"garage: hunt pin {g['game']} core={row['core']} driver={row['driver']}")
    return {"override": read_override()}


def hunt_unpin(req):
    """{"game"} or {"all": true}. Undoing needs no grant."""
    rows = read_override()
    if req.get("all"):
        rows = {}
    else:
        rows.pop(str(req.get("game", "")), None)
    write_override(rows)
    log(f"garage: hunt unpin {'all' if req.get('all') else req.get('game')}")
    return {"override": read_override()}


def hunt_end(req):
    """Back to the certified car: no overrides, no hunt artifacts, no debug env, no rig grant."""
    removed = []
    for kind in HUNT_KINDS:
        d = hunt_dir(kind)
        for n in (os.listdir(d) if os.path.isdir(d) else []):
            try:
                os.remove(os.path.join(d, n))
                removed.append(f"{HUNT_KINDS[kind][0]}/{n}")
            except OSError:
                pass
    debug_env({"action": "clear"})
    try:
        os.remove(HUNT_GRANT_FILE)
        removed.append(HUNT_GRANT_FILE)
    except FileNotFoundError:
        pass
    log(f"garage: hunt_end removed {len(removed)} files")
    return {"removed": removed}


HUNT_OPS = {"hunt_status": hunt_status, "put": hunt_put, "pin": hunt_pin, "unpin": hunt_unpin, "hunt_end": hunt_end}


def dump_threads(timeout=6.0):
    """ARMSX3's on-request thread dump (lv2.cpp ppu_dump_threads_on_request): create
    <config>/dump_threads; RPCS3's syscall-usage thread consumes it within ~1 s while the
    game is NOT paused, pauses every PPU thread, logs registers + call stack + code around
    PC (and SPU / renderer threads), releases them. Returns the log written meanwhile."""
    trigger = os.path.join(RPCS3_CONFIG, "dump_threads")
    start = log_size()
    with open(trigger, "w"):
        pass
    deadline = time.time() + timeout
    while os.path.exists(trigger) and time.time() < deadline:
        time.sleep(0.1)
    consumed = not os.path.exists(trigger)
    if not consumed:
        try:
            os.remove(trigger)  # never leave a trigger armed for a later, unrelated boot
        except OSError:
            pass
        return {"consumed": False, "why": "RPCS3 did not take the trigger (no game running, or paused)"}
    last, stable_since = log_size(), time.time()
    while time.time() - stable_since < 1.0 and time.time() < deadline + 10:
        time.sleep(0.2)
        now = log_size()
        if now != last:
            last, stable_since = now, time.time()
    rep = read_log({"from": start, "tail": 10 ** 9})
    log(f"garage: dump_threads -> {rep['lines']} log lines")
    return {"consumed": True, "from": start, **rep}


# ---- onboard: the lobby cam (ONBD v1 over a PLUSB channel, target "onboard") -------------
# The car's half of ONBOARD (etk-dossiers/Onboard_LiveMirror_20261025.md section 8.3). A host OPENs
# a channel with target b"onboard"; everything after is ONBD v1 messages both ways:
#   header <4sBBBBHHIQ> magic b"ONBD", ver 1, kind, codec, flags, w, h, seq, car_ns
#   then <I> len, then len payload bytes (28 B fixed part).
#   kind 0 INFO  car->host  JSON: backend, encoder, mode, fps (effective), fps_req, quality,
#                            scale, transform, panel, output, policy_fps, pinned, daemon, seq
#   kind 1 FRAME car->host  codec 2 = JPEG; w,h as delivered (landscape); car_ns = CLOCK_MONOTONIC
#                            at capture start; flags bit1 = snap
#   kind 2 CTRL  host->car  JSON: fps / quality / scale / pin / snap; unknown keys ignored; every
#                            CTRL is answered by an INFO
#   kind 3 END   car->host  UTF-8 reason; the channel closes after it
# Backend "jpeg-grim": bin/onboard_cap.sh runs ONE `grim -o DSI-1 -t jpeg -q Q -s S -` per
# frame (exec'd, so the child IS grim). The next capture starts only after the previous frame's
# write returned AND the link drained it (our socket's unread bytes = 0 and the relay's IN
# queue empty): a slow link skips capture slots (seq jumps by the skipped slots) instead of
# queueing frames -- latest-wins, never a backlog. Policy: RUNNING (a game is up) caps the rate
# at ETK_ONBOARD_RUNNING_FPS (default 0 = INFO only, no capture process at all) unless the host
# pinned; the mode is re-read every second and every change is announced with an INFO. Nothing
# runs while no onboard channel is open: the capture lives inside the channel's own thread.
import fcntl  # noqa: E402
import queue  # noqa: E402
try:
    from termios import TIOCOUTQ as _ONBOARD_TIOCOUTQ  # noqa: E402
except ImportError:  # a python without termios must not take garage / hunts down with it
    _ONBOARD_TIOCOUTQ = 0x5411  # SIOCOUTQ == TIOCOUTQ on Linux (asm-generic/ioctls.h)

ONBD_MAGIC, ONBD_VER = b"ONBD", 1
ONBD_HDR = struct.Struct("<4sBBBBHHIQ")   # 24 B
ONBD_LEN = struct.Struct("<I")            # 4 B
ONBD_FIXED = ONBD_HDR.size + ONBD_LEN.size
ONBD_INFO, ONBD_FRAME, ONBD_CTRL, ONBD_END = range(4)
ONBD_RAW, ONBD_ZSTD, ONBD_JPEG, ONBD_H264 = range(4)
ONBD_FLAG_KEY, ONBD_FLAG_SNAP = 1, 2
ONBD_MAX = 16 << 20                       # a payload larger than this is a framing error
ONBOARD_TARGET = getattr(L, "TARGET_ONBOARD", b"onboard").decode()
ONBOARD_CAP = os.environ.get("ETK_ONBOARD_CAP", os.path.join(HERE, "onboard_cap.sh"))
ONBOARD_OUTPUT = os.environ.get("ETK_ONBOARD_OUTPUT", "DSI-1")
ONBOARD_PANEL, ONBOARD_TRANSFORM = (1080, 1920), 90   # DSI-1 as probed on car8; grim un-rotates
ONBOARD_SHM = os.environ.get("SHM_DIR", "/dev/shm/etk_shm")   # scripts/env.sh's SHM_DIR
ONBOARD_CONF = os.environ.get("ETK_CONF_FILE", os.path.join(ETK_ROOT, "etk.conf"))
ONBOARD_FPS_DEFAULT, ONBOARD_FPS_MAX = 5.0, 30.0
ONBOARD_SCALE_DEFAULT = 0.5
ONBOARD_SNAP_QUALITY = 100
ONBOARD_CAP_TIMEOUT = 5.0                 # a grim that hangs this long is a backend failure
ONBOARD_POLL = 0.1                        # capture / link-drain poll (CLOSE -> kill well < 1 s)
ONBOARD_MODE_EVERY = 1.0
_ONBOARD_CAP_LOCK = threading.Lock()      # two viewers share one grim at a time
_ONBOARD_LIVE = set()
_ONBOARD_LIVE_LOCK = threading.Lock()

OnbdMsg = collections.namedtuple("OnbdMsg", "kind codec flags w h seq car_ns payload")


def pack_msg(kind, payload=b"", codec=0, flags=0, w=0, h=0, seq=0, car_ns=0):
    """One ONBD v1 message. `payload` may be bytes, a str (UTF-8) or a dict (JSON)."""
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        payload = payload.encode()
    payload = bytes(payload)
    return (ONBD_HDR.pack(ONBD_MAGIC, ONBD_VER, kind, codec, flags, w & 0xFFFF, h & 0xFFFF,
                          seq & 0xFFFFFFFF, car_ns & 0xFFFFFFFFFFFFFFFF)
            + ONBD_LEN.pack(len(payload)) + payload)


def parse_msg(buf, offset=0):
    """(OnbdMsg, bytes consumed) for the message at buf[offset:], or (None, 0) while it is
    incomplete. Raises ValueError on a bad magic / version / length (the caller resyncs)."""
    if len(buf) - offset < ONBD_FIXED:
        return None, 0
    magic, ver, kind, codec, flags, w, h, seq, car_ns = ONBD_HDR.unpack_from(buf, offset)
    if magic != ONBD_MAGIC:
        raise ValueError(f"ONBD: bad magic {bytes(magic)!r}")
    if ver != ONBD_VER:
        raise ValueError(f"ONBD: version {ver} (this end speaks {ONBD_VER})")
    (n,) = ONBD_LEN.unpack_from(buf, offset + ONBD_HDR.size)
    if n > ONBD_MAX:
        raise ValueError(f"ONBD: payload length {n} > {ONBD_MAX}")
    if len(buf) - offset < ONBD_FIXED + n:
        return None, 0
    start = offset + ONBD_FIXED
    return OnbdMsg(kind, codec, flags, w, h, seq, car_ns, bytes(buf[start:start + n])), ONBD_FIXED + n


def jpeg_size(b):
    """(w, h) from a JPEG's SOF marker, or (0, 0)."""
    i = 2 if b[:2] == b"\xff\xd8" else len(b)
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            return 0, 0
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        n = struct.unpack_from(">H", b, i + 2)[0]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(b):
                return 0, 0
            h, w = struct.unpack_from(">HH", b, i + 5)
            return w, h
        i += 2 + n
    return 0, 0


def onboard_conf():
    """The ONBOARD knobs: the environment wins, else the kit's etk.conf (KEY=value lines, the
    shell-sourced file env.sh reads; the systemd unit does not source it), else defaults."""
    keys = ("ETK_ONBOARD", "ETK_ONBOARD_RUNNING_FPS", "ETK_ONBOARD_QUALITY")
    conf = {}
    try:
        with open(ONBOARD_CONF, errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("export "):
                    ln = ln[7:].lstrip()
                k, sep, v = ln.partition("=")
                if not sep or k not in keys:
                    continue
                v = v.strip()
                if v[:1] in ("'", '"'):
                    v = v[1:].split(v[0], 1)[0]
                else:
                    v = v.split("#", 1)[0].strip()
                conf[k] = v
    except OSError:
        pass
    conf.update({k: os.environ[k] for k in keys if k in os.environ})

    def num(k, default, lo, hi):
        try:
            return min(hi, max(lo, float(conf[k])))
        except (KeyError, ValueError):
            return default
    return {"enabled": (conf.get("ETK_ONBOARD") or "1").strip() == "1",   # install.sh: = "1"
            "running_fps": num("ETK_ONBOARD_RUNNING_FPS", 0.0, 0.0, ONBOARD_FPS_MAX),
            "quality": int(num("ETK_ONBOARD_QUALITY", 70, 1, 100))}


def _first_line(path):
    try:
        with open(path, errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return None


def onboard_mode():
    """RUNNING while a game is up, else IDLE (section 8.3 as amended): the Sentry's active_id.txt
    is the only signal -- missing / empty / IDLE = IDLE, anything else (a title ID, UNKNOWN_ID)
    = RUNNING. etk_mode.txt is the thermal governor's RACE/PIT file and is never read here."""
    gid = _first_line(os.path.join(ONBOARD_SHM, "active_id.txt"))
    return "IDLE" if gid in (None, "", "IDLE") else "RUNNING"


def onboard_sessions():
    """The live onboard channels (tests and status: no channel -> no capture)."""
    with _ONBOARD_LIVE_LOCK:
        return list(_ONBOARD_LIVE)


class _OnboardClosed(Exception):
    pass


class OnboardCapture:
    """One onboard channel: reader thread for CTRL, this thread for capture + every write."""

    def __init__(self, sock, relay=None):
        self.sock, self.relay = sock, relay
        self.closed = threading.Event()
        self.wake = threading.Event()
        self.ctrl = queue.Queue()
        self.proc = None
        self.seq = 0
        self.conf = onboard_conf()
        self.mode = onboard_mode()
        self.fps_req = ONBOARD_FPS_DEFAULT
        self.quality = self.conf["quality"]
        self.scale = ONBOARD_SCALE_DEFAULT
        self.pinned = False
        self.stats = collections.Counter()

    # -- policy ------------------------------------------------------------------
    def fps(self):
        if self.mode == "RUNNING" and not self.pinned:
            return min(self.fps_req, self.conf["running_fps"])
        return self.fps_req

    def info(self, **extra):
        d = {"backend": "jpeg-grim", "encoder": "grim", "mode": self.mode, "fps": self.fps(),
             "fps_req": self.fps_req, "quality": self.quality, "scale": self.scale,
             "transform": ONBOARD_TRANSFORM, "panel": list(ONBOARD_PANEL), "output": ONBOARD_OUTPUT,
             "policy_fps": self.conf["running_fps"], "pinned": self.pinned, "daemon": VERSION,
             "onbd": ONBD_VER, "seq": self.seq}
        d.update(extra)
        self.send(ONBD_INFO, d, seq=self.seq, car_ns=time.monotonic_ns())

    def tick_mode(self):
        m = onboard_mode()
        if m != self.mode:
            self.mode = m
            log(f"onboard: mode {m} -> {self.fps():g} fps")
            self.info(why="mode")

    # -- wire ----------------------------------------------------------------------
    def send(self, kind, payload, **hdr):
        try:
            self.sock.sendall(pack_msg(kind, payload, **hdr))
        except OSError:
            self.closed.set()
            raise _OnboardClosed()

    def _reader(self):
        buf = bytearray()
        try:
            while True:
                b = self.sock.recv(64 * 1024)
                if not b:
                    break
                buf += b
                while True:
                    try:
                        m, n = parse_msg(buf)
                    except ValueError:
                        i = buf.find(ONBD_MAGIC, 1)
                        del buf[:i if i > 0 else max(0, len(buf) - len(ONBD_MAGIC) + 1)]
                        self.stats["resync"] += 1
                        continue
                    if m is None:
                        break
                    del buf[:n]
                    if m.kind == ONBD_CTRL:
                        try:
                            req = json.loads(m.payload.decode("utf-8"))
                        except ValueError:
                            req = None
                        self.ctrl.put(req if isinstance(req, dict) else {"_bad": True})
                        self.wake.set()
        except OSError:
            pass
        self.closed.set()
        self.wake.set()

    def apply(self, req):
        """One CTRL: apply the keys it knows, ignore the rest, answer with an INFO."""
        extra, ignored = {"why": "ctrl"}, []
        if req.get("_bad"):
            extra["err"] = "CTRL payload is not a JSON object"
        for k, v in req.items():
            try:
                if k in ("fps", "quality", "scale") and isinstance(v, bool):
                    raise ValueError(k)
                if k == "fps":
                    self.fps_req = min(ONBOARD_FPS_MAX, max(0.0, float(v)))
                elif k == "quality":
                    self.quality = int(min(100, max(1, int(v))))
                elif k == "scale":
                    self.scale = min(1.0, max(0.05, float(v)))
                elif k == "pin":
                    if not isinstance(v, bool):
                        raise ValueError(k)
                    self.pinned = v
                elif k != "snap" and k != "_bad":
                    ignored.append(k)
            except (TypeError, ValueError):
                ignored.append(k)
        if ignored:
            extra["ignored"] = sorted(ignored)
        snap = req.get("snap") is True
        if snap and self.mode == "RUNNING" and not self.pinned and self.conf["running_fps"] <= 0:
            extra["snap"] = "refused: a game is running and ETK_ONBOARD_RUNNING_FPS=0 (pin to override)"
            snap = False
        self.info(**extra)
        if snap:
            self.frame(ONBOARD_SNAP_QUALITY, 1.0, ONBD_FLAG_SNAP, skipped=0)

    # -- capture -------------------------------------------------------------------
    def link_busy(self):
        """True while our last write has not left the car: unread bytes in our socket (the
        relay's pump has not taken them) or segments still queued for the IN endpoint."""
        try:
            outq = struct.unpack("i", fcntl.ioctl(self.sock.fileno(), _ONBOARD_TIOCOUTQ, b"\0\0\0\0"))[0]
        except (OSError, ValueError, TypeError, struct.error):
            outq = 0                                # cannot tell: the link counts as not busy
        q = getattr(getattr(self.relay, "out", None), "q", None)   # plusb.OutQueue's queue
        return outq > 0 or (q is not None and q.qsize() > 0)

    def _kill(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            try:
                p.kill()
            except OSError:
                pass
        try:
            p.communicate(timeout=2)
        except Exception:
            pass

    def capture(self, quality, scale, snap=False):
        """One JPEG from grim: (bytes, car_ns), or None when the channel closed / the policy
        went to 0 meanwhile. Raises RuntimeError on a backend failure."""
        while not _ONBOARD_CAP_LOCK.acquire(timeout=ONBOARD_POLL):
            if self.closed.is_set():
                return None
        try:
            car_ns = time.monotonic_ns()
            self.proc = subprocess.Popen(["/bin/sh", ONBOARD_CAP, str(int(quality)), f"{scale:g}", ONBOARD_OUTPUT],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
            self.stats["captures"] += 1
            deadline = time.monotonic() + ONBOARD_CAP_TIMEOUT
            next_mode = time.monotonic() + ONBOARD_MODE_EVERY
            while True:
                try:
                    out, err = self.proc.communicate(timeout=ONBOARD_POLL)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if self.closed.is_set():
                    return None
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"grim timed out ({ONBOARD_CAP_TIMEOUT:g} s)")
                if time.monotonic() >= next_mode:
                    next_mode += ONBOARD_MODE_EVERY
                    self.tick_mode()
                    if not snap and self.fps() <= 0:
                        return None
            rc, self.proc = self.proc.returncode, None
            if rc != 0:
                tail = (err or b"").decode(errors="replace").strip().splitlines()[-1:] or ["no stderr"]
                raise RuntimeError(f"grim failed (exit {rc}): {tail[0][:160]}")
            if not out:
                raise RuntimeError("grim produced no image")
            return out, car_ns
        finally:
            self._kill()
            _ONBOARD_CAP_LOCK.release()

    def frame(self, quality, scale, flags, skipped):
        got = self.capture(quality, scale, snap=bool(flags & ONBD_FLAG_SNAP))
        if got is None:
            return False
        img, car_ns = got
        w, h = jpeg_size(img)
        if not w:
            w, h = round(ONBOARD_PANEL[1] * scale), round(ONBOARD_PANEL[0] * scale)
        self.seq = (self.seq + 1 + skipped) & 0xFFFFFFFF
        self.send(ONBD_FRAME, img, codec=ONBD_JPEG, flags=flags, w=w, h=h, seq=self.seq, car_ns=car_ns)
        self.stats["frames"] += 1
        return True

    def run(self):
        if not self.conf["enabled"]:
            self.send(ONBD_END, "ONBOARD is off on this car (etk.conf ETK_ONBOARD=0)")
            return
        threading.Thread(target=self._reader, name="onboard-ctrl", daemon=True).start()
        log(f"onboard: open ({self.mode}, {self.fps():g} fps, q{self.quality}, x{self.scale:g})")
        self.info(why="open")
        now = time.monotonic()
        next_mode, due, busy_since = now + ONBOARD_MODE_EVERY, now, None
        while not self.closed.is_set():
            while True:
                try:
                    req = self.ctrl.get_nowait()
                except queue.Empty:
                    break
                self.apply(req)
            now = time.monotonic()
            if now >= next_mode:
                next_mode = now + ONBOARD_MODE_EVERY
                self.tick_mode()
            fps = self.fps()
            if fps > 0 and now >= due:
                if self.link_busy():
                    busy_since = busy_since or now
                    self.wake.wait(min(ONBOARD_POLL / 5, max(0.0, next_mode - now)))
                    self.wake.clear()
                    continue
                period = 1.0 / fps
                skipped = int((now - busy_since) / period) if busy_since else 0
                busy_since = None
                start = now
                if self.frame(self.quality, self.scale, 0, skipped):
                    due = start + period
                    if due < time.monotonic() - period:
                        due = time.monotonic()      # never burst to catch up
                continue
            waits = [next_mode - now] + ([due - now] if fps > 0 else [])
            self.wake.wait(max(0.0, min(waits)))
            self.wake.clear()


def onboard_serve(sock, relay=None):
    """The `onboard` channel service (CarRelay services entry): runs until CLOSE, link loss,
    or END. A backend failure is answered with END + its reason, never a daemon crash."""
    sess = OnboardCapture(sock, relay)
    with _ONBOARD_LIVE_LOCK:
        _ONBOARD_LIVE.add(sess)
    try:
        sess.run()
    except _OnboardClosed:
        pass
    except Exception as e:
        log(f"onboard: END ({e})")
        try:
            sess.send(ONBD_END, str(e) or e.__class__.__name__)
        except _OnboardClosed:
            pass
    finally:
        sess.closed.set()
        sess._kill()
        with _ONBOARD_LIVE_LOCK:
            _ONBOARD_LIVE.discard(sess)
        log(f"onboard: closed ({sess.stats['frames']} frames, {sess.stats['captures']} captures)")


# ---- the relay -----------------------------------------------------------------------------
class Daemon:
    def __init__(self, ep0):
        self.ep0 = ep0
        self.enabled = threading.Event()
        self.stop = threading.Event()
        self.relay = L.CarRelay(target=TARGET, status=self.status, log=log,
                                services={"garage": lambda sock: L.serve_json_lines(sock, garage)})
        self.relay.services[ONBOARD_TARGET] = lambda sock: onboard_serve(sock, self.relay)
        self.ep_in = os.open(MNT + "/ep1", os.O_RDWR)
        self.ep_out = os.open(MNT + "/ep2", os.O_RDWR)

    def status(self):
        try:
            L.connect_target(TARGET, timeout=0.2).close()
            car = "rpcs3 pitlink up"
        except OSError:
            car = "rpcs3 pitlink DOWN (no game running, or Pitlink off)"
        return f"{VERSION}; {car}"

    def run(self):
        for fn in (self._ep0, self._rx, self._tx):
            threading.Thread(target=fn, name=fn.__name__, daemon=True).start()
        while not self.stop.wait(1.0):
            pass

    def _ep0(self):
        while not self.stop.is_set():
            try:
                buf = os.read(self.ep0, EVENT.size * 8)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                log(f"ep0 read: {e}")
                time.sleep(0.5)
                continue
            for i in range(0, len(buf) - EVENT.size + 1, EVENT.size):
                rtype, req, value, index, length, kind = EVENT.unpack_from(buf, i)
                if kind == EV_SETUP:
                    self._setup(rtype, req, length)
                    continue
                log(f"event {EV_NAMES[kind] if kind < len(EV_NAMES) else kind}")
                if kind == EV_ENABLE:
                    self.enabled.set()
                elif kind in (EV_DISABLE, EV_UNBIND):
                    self.enabled.clear()
                    self.relay.link_down()

    def _setup(self, rtype, req, length):
        try:
            if (rtype & 0x60) == 0x40 and req == L.VREQ_RESET and not rtype & 0x80:
                self.relay.vendor_reset()
                os.read(self.ep0, length)       # OUT status stage: ACK
                log("VREQ_RESET: channels dropped, hunting for HELLO")
            elif rtype & 0x80:
                os.read(self.ep0, 0)            # IN request we don't serve: stall
            else:
                os.write(self.ep0, b"")         # OUT request we don't serve: stall
        except OSError as e:
            log(f"setup 0x{rtype:02x}/{req}: {e}")

    def _rx(self):
        """bulk OUT -> relay."""
        while not self.stop.is_set():
            self.enabled.wait()
            try:
                b = os.read(self.ep_out, L.IO_CHUNK)
            except OSError as e:
                if e.errno not in (errno.ESHUTDOWN, errno.EINTR, errno.ECONNRESET):
                    log(f"ep OUT: {e}")
                    time.sleep(0.05)
                continue
            if b:
                self.relay.feed(b)

    def _tx(self):
        """relay -> bulk IN (a ZLP closes any transfer that ends on a packet boundary)."""
        while not self.stop.is_set():
            try:
                b = self.relay.out.take(timeout=0.5)
            except Exception:  # queue.Empty
                continue
            self.enabled.wait()
            try:
                mv = memoryview(b)
                while mv:
                    n = os.write(self.ep_in, mv)
                    mv = mv[n:]
                if len(b) % 512 == 0:
                    os.write(self.ep_in, b"")
            except OSError as e:
                if e.errno not in (errno.ESHUTDOWN, errno.EINTR, errno.ECONNRESET):
                    log(f"ep IN: {e}")
                    time.sleep(0.05)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "status":
        print(f"cdc UDC={rd(G + '/UDC') or '-'} ncm={ncm_iface() or '-'} {iface_inet(ncm_iface()) or ''}")
        print(f"function={'yes' if os.path.isdir(FUNC) else 'no'} linked={'yes' if os.path.islink(LINK) else 'no'} "
              f"ffs mounted={'yes' if is_mount(MNT) else 'no'}")
        return 0
    if cmd == "detach":
        detach()
        return 0
    if cmd != "serve":
        print(__doc__)
        return 2
    try:
        ep0 = attach()
    except NotReady as e:
        log(f"not attaching: {e}")
        return 3
    d = Daemon(ep0)

    def bye(*_a):
        d.stop.set()
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    log(f"{VERSION} serving -> {TARGET}")
    try:
        d.run()
    finally:
        detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
