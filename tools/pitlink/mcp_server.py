#!/usr/bin/env python3
"""mcp_server — "pitlink": Claude's eyes and hands on the car, as an MCP server (stdio).

  claude mcp add pitlink -e PITLINK_ADDR=169.254.170.2:47500 -- python3 tools/pitlink/mcp_server.py

Hand-rolled JSON-RPC 2.0 over newline-delimited stdio (initialize ·
notifications/initialized · tools/list · tools/call · ping) — no SDK, stdlib +
the pitlink client only. stdout carries protocol and nothing else; logs go to
stderr.

ONE PitlinkClient per server process, connected lazily on the first tool call
(the server starts fine while the car is down) and re-dialled if the link drops.
Being the long-lived client, this process is normally the CONTROLLER; one-shot
`pitlink.py` runs alongside it are observers. A hold() is heartbeated by this
process, so if it dies the car's dead-man releases the pad within ttl.

wait_for takes a deliberately tiny condition language — no eval:
    clause [and clause ...]      clause = TERM OP NUMBER, OP in > >= < <= == !=
    TERM: flip · dflip (flips since the wait began) · fps · vblank · w · h
          watch[i] (unsigned BE) · watchs[i] (signed) · watchf[i] (float, size 4/8)
          event[k] (EVENTs of kind k pushed since the wait began; 7 = step complete)
    e.g.  watch[0] > 100 and flip >= 5000      dflip >= 60      event[7] >= 1
"""
import base64
import json
import operator
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plnk as P  # noqa: E402
from client import PitlinkClient, PitlinkError, default_addr  # noqa: E402

SERVER = {"name": "pitlink", "version": "0.1.0"}
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
WAIT_MAX_S = 120.0

# ---- the condition language -------------------------------------------------------------
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
       "==": operator.eq, "!=": operator.ne}
INDEXED, PLAIN = ("watch", "watchs", "watchf", "event"), ("flip", "dflip", "fps", "vblank", "w", "h")
CLAUSE = re.compile(r"^\s*([a-z]+)\s*(?:\[\s*(\d+)\s*\])?\s*(>=|<=|==|!=|>|<)\s*"
                    r"([-+]?(?:0x[0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+))\s*$")


def parse_cond(s):
    """'watch[0] > 100 and flip >= 5' -> [(term, idx, op, number)]. ValueError on anything else."""
    out = []
    for part in re.split(r"\s+and\s+|\s*&&\s*", (s or "").strip()):
        m = CLAUSE.match(part)
        if not m:
            raise ValueError(f"bad clause {part!r} (want TERM OP NUMBER, e.g. watch[0] > 100)")
        term, idx, op, num = m.groups()
        if term in INDEXED and idx is None:
            raise ValueError(f"{term} needs an index: {term}[0]")
        if term in PLAIN and idx is not None:
            raise ValueError(f"{term} takes no index")
        if term not in INDEXED + PLAIN:
            raise ValueError(f"unknown term {term!r} (have {', '.join(PLAIN + INDEXED)})")
        val = int(num, 0) if re.fullmatch(r"[-+]?(0x[0-9a-fA-F]+|\d+)", num) else float(num)
        out.append((term, None if idx is None else int(idx), op, val))
    return out


def term_value(term, idx, frame, watch_items, flip0, events):
    """One term's current value; None = not available yet (the clause is then false)."""
    if term == "event":
        return sum(1 for ev in events if ev.kind == idx)
    if frame is None:
        return None
    if term == "dflip":
        return frame.flip - flip0
    if term in ("flip", "fps", "vblank", "w", "h"):
        return getattr(frame, term)
    parts = P.split_watch(frame.watch, watch_items)
    if parts is None or idx >= len(parts):
        return None
    kind = {"watch": "u", "watchs": "s", "watchf": "f"}[term]
    try:
        return P.be_value(parts[idx], kind)
    except ValueError:
        return None


def holds(clauses, frame, watch_items, flip0, events):
    vals = []
    ok = True
    for term, idx, op, num in clauses:
        v = term_value(term, idx, frame, watch_items, flip0, events)
        vals.append(v)
        ok = ok and v is not None and OPS[op](v, num)
    return ok, vals


# ---- the link ---------------------------------------------------------------------------
class Link:
    def __init__(self):
        self.addr = default_addr()
        self.token = os.environ.get("PITLINK_TOKEN", "")
        self.c = None

    def get(self):
        if self.c is None or self.c.closed:
            if self.c is not None:
                log(f"link lost ({self.c.closed}); re-dialling {self.addr}")
            self.c = PitlinkClient(self.addr)
            info = self.c.hello(self.token)
            log(f"connected {self.addr}: role={'controller' if self.c.controller else 'observer'} "
                f"{info['title_id']} {info['title']!r} ({info['version']})")
            v = os.environ.get("PITLINK_VIDEO")  # optional WxH@HZ applied at connect
            if v and self.c.controller:
                size, _, hz = v.partition("@")
                w, h = (int(x) for x in size.split("x"))
                self.c.video(w, h, int(hz or 30))
        return self.c


def log(msg):
    print(f"[pitlink-mcp] {msg}", file=sys.stderr, flush=True)


def parse_items(items):
    out = []
    for s in items if isinstance(items, list) else [items]:
        if isinstance(s, dict):
            out.append((int(str(s["addr"]), 0), int(s.get("size", 4))))
        else:
            a, _, n = str(s).partition(":")
            out.append((int(a, 0), int(n or 4, 0)))
    return out


def text(s):
    return {"type": "text", "text": s if isinstance(s, str) else json.dumps(s, indent=1, default=str)}


def frame_text(c, f):
    m = f.meta()
    vals = c.watch_values(f)
    if vals is not None and c.watch_items:
        m["watch_values"] = {f"{a:#x}:{s}": v for (a, s), v in zip(c.watch_items, vals)}
    m["age_ms"] = round((time.monotonic() - f.recv_t) * 1e3, 1)
    return m


def need_controller(c):
    if not c.controller:
        raise PitlinkError("observer: another client holds the wheel (control ops are refused)")


# ---- tools ------------------------------------------------------------------------------
def t_frame(L, fresh=False, snap=False, max_width=640, timeout=3.0):
    c = L.get()
    if snap:
        need_controller(c)
        f = c.snap(P.CODEC_ZSTD if P.ZSTD else P.CODEC_RAW, timeout=timeout)
    else:
        f = c.latest_frame()
        if fresh or f is None:
            f = c.wait_frame(timeout=timeout)
    png = f.png(max_w=int(max_width) if max_width else None)
    return [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            text(frame_text(c, f))]


def t_status(L):
    c = L.get()
    st = c.status()
    flip, _ = c.ping()
    f = c.latest_frame()
    return [text({**st, "addr": c.addr, "role": "controller" if c.controller else "observer",
                  "state": {0: "running", 1: "paused"}.get(st["state"], "other"),
                  "ping_flip": flip, "rtt_ms": round(c.rtt_ms, 2), "hello": c.info,
                  "watch": [f"{a:#x}:{s}" for a, s in c.watch_items],
                  "held": {p: repr(s) for p, s in c.held.items()},
                  "frame": frame_text(c, f) if f else None,
                  "recent_events": [(e.kind, P.EVENT_NAMES.get(e.kind), e.text) for e in c.events_since(c.event_mark - 10)]})]


def t_press(L, buttons, ms=120, port=0):
    c = L.get()
    need_controller(c)
    if isinstance(buttons, str):
        buttons = [b for b in re.split(r"[\s,+]+", buttons) if b]
    r = c.press(buttons, int(ms), int(port))
    return [text({"pressed": buttons, "fps": round(c.fps(), 2), **r})]


def t_hold(L, controls, port=0, ttl_ms=0):
    c = L.get()
    need_controller(c)
    st = P.PadState(port=int(port), ttl_ms=int(ttl_ms))
    for k, v in (controls or {}).items():
        st.set(k, v)
    c.hold(st)
    return [text({"holding": repr(st), "keepalive": "heartbeat from this server; release() or my exit ends it"})]


def t_release(L, port=0):
    c = L.get()
    need_controller(c)
    c.release(int(port))
    return [text(f"port {port} released (neutral, schedule cleared)")]


def t_ram_read(L, items):
    c = L.get()
    its = parse_items(items)
    out = []
    for (a, s), b in zip(its, c.ram_read(its)):
        row = {"addr": f"{a:#x}", "size": s, "hex": None if b is None else b.hex()}
        if b is not None and s in (1, 2, 4, 8):
            row["u"] = P.be_value(b, "u")
            if s in (4, 8):
                row["f"] = P.be_value(b, "f")
        out.append(row)
    return [text(out)]


def t_watch(L, items):
    c = L.get()
    need_controller(c)
    its = parse_items(items)
    c.watch(its)
    return [text({"watch": [f"{a:#x}:{s}" for a, s in its],
                  "note": "every FRAME now carries these bytes, sampled at its flip; wait_for can test watch[i]"})]


def t_pause(L):
    c = L.get()
    need_controller(c)
    c.pause()
    return [text("paused")]


def t_resume(L):
    c = L.get()
    need_controller(c)
    c.resume()
    return [text("running")]


def t_step(L, n=1, wait=True, timeout=10.0):
    c = L.get()
    need_controller(c)
    mark = c.event_mark
    c.step(int(n))
    if not wait:
        return [text(f"stepping {n} flips")]
    ev = c.wait_event(P.EV_STEP, timeout=min(float(timeout), WAIT_MAX_S), after=mark)
    f = c.latest_frame()
    return [text({"step": int(n), "event": ev.text, "frame": frame_text(c, f) if f else None})]


def t_wait_for(L, condition, timeout=10.0):
    clauses = parse_cond(condition)
    c = L.get()
    timeout = min(float(timeout), WAIT_MAX_S)
    t0, ev_mark = time.monotonic(), c.event_mark
    f = c.latest_frame()
    flip0 = f.flip if f is not None else c.ping()[0]
    mark = c.mark
    while True:
        f = c.latest_frame()
        ok, vals = holds(clauses, f, c.watch_items, flip0, c.events_since(ev_mark))
        el = time.monotonic() - t0
        if ok or el >= timeout or c.closed:
            return [text({"met": ok, "condition": condition, "elapsed_s": round(el, 3),
                          "values": vals, "frame": frame_text(c, f) if f else None,
                          **({"link": c.closed} if c.closed else {})})]
        mark = c.wait_any(mark, timeout - el)


S_ITEMS = {"type": "array", "items": {"type": "string"},
           "description": "guest addresses as 'ADDR:SIZE', e.g. '0x10000:4' (size 1/2/4/8 for watch)"}
TOOLS = {
    "frame": (t_frame, "The car's latest frame (PNG) plus its identity: flip, fps, and the WATCH values "
              "sampled at that same flip. fresh=true waits for the next frame; snap=true asks for one "
              "native-size frame (needs a running emulator: paused = no new flips).",
              {"fresh": {"type": "boolean"}, "snap": {"type": "boolean"},
               "max_width": {"type": "integer", "description": "downscale to this width (default 640; 0 = as sent)"},
               "timeout": {"type": "number"}}, []),
    "status": (t_status, "Link and emulator state: role, running/paused, flip, title, RTT, WATCH list, "
               "held pad, latest frame identity, recent events.", {}, []),
    "press": (t_press, "Tap buttons for `ms` (converted to flips at the car's fps, both edges stamped "
              "at_flip so the game sees exactly that many flips). Buttons: select l3 r3 start up right "
              "down left l2 r2 l1 r1 triangle circle cross square.",
              {"buttons": {"type": "array", "items": {"type": "string"}},
               "ms": {"type": "integer"}, "port": {"type": "integer"}}, ["buttons"]),
    "hold": (t_hold, "Set and KEEP pad controls until release: sticks lx ly rx ry in -1..1 (ly -1 = up), "
             "buttons 0/1, pressure buttons (incl. l2 r2) 0..1. Heartbeated; the car's dead-man "
             "releases it if this server dies.",
             {"controls": {"type": "object", "additionalProperties": {"type": "number"},
                           "description": "e.g. {\"lx\": -0.3, \"r2\": 0.6, \"cross\": 1}"},
              "port": {"type": "integer"}, "ttl_ms": {"type": "integer"}}, ["controls"]),
    "release": (t_release, "Agent pad back to neutral (and any scheduled PADs cleared).",
                {"port": {"type": "integer"}}, []),
    "ram_read": (t_ram_read, "Read guest RAM (big-endian PS3 memory): hex plus unsigned/float decodes.",
                 {"items": S_ITEMS}, ["items"]),
    "watch": (t_watch, "Set the per-flip WATCH list (<= 256 entries): the car samples these at every flip "
              "and attaches them to that flip's frame. [] clears it.", {"items": S_ITEMS}, ["items"]),
    "pause": (t_pause, "Pause the emulator (no flips until resume/step).", {}, []),
    "resume": (t_resume, "Resume the emulator.", {}, []),
    "step": (t_step, "Run exactly n flips then pause again (lockstep). wait=true returns after the "
             "step-complete event, with the stepped frame's identity.",
             {"n": {"type": "integer"}, "wait": {"type": "boolean"}, "timeout": {"type": "number"}}, []),
    "wait_for": (t_wait_for, "Block until a condition over the live frame stream holds, or timeout. "
                 "Language: clause [and clause...], clause = TERM OP NUMBER; TERM = flip | dflip "
                 "(flips since the wait began) | fps | vblank | w | h | watch[i] (unsigned) | watchs[i] "
                 "(signed) | watchf[i] (float) | event[k] (events of kind k since the wait began: 4 pad "
                 "dead-man, 6 error, 7 step complete); OP = > >= < <= == !=. "
                 "e.g. 'watch[0] > 100 and dflip >= 30'.",
                 {"condition": {"type": "string"}, "timeout": {"type": "number"}}, ["condition"]),
}


def tool_list():
    return [{"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": props, "required": req}}
            for n, (_, d, props, req) in TOOLS.items()]


def call_tool(L, name, args):
    if name not in TOOLS:
        return {"content": [text(f"unknown tool {name!r}")], "isError": True}
    fn, _, props, _ = TOOLS[name]
    args = {k: v for k, v in (args or {}).items() if k in props}
    try:
        return {"content": fn(L, **args), "isError": False}
    except (PitlinkError, OSError, ValueError, KeyError, TypeError) as e:
        if isinstance(e, OSError) and L.c is None:
            msg = f"cannot reach the car at {L.addr}: {e}"
        else:
            msg = f"{e.__class__.__name__}: {e}"
        return {"content": [text(msg)], "isError": True}


def handle(L, req):
    """One JSON-RPC message -> the response dict, or None for a notification."""
    mid, method = req.get("id"), req.get("method")
    if mid is None:
        return None  # notifications/initialized, notifications/cancelled, ...
    if method == "initialize":
        v = (req.get("params") or {}).get("protocolVersion")
        res = {"protocolVersion": v if v in PROTOCOLS else PROTOCOLS[1],
               "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
               "instructions": "Pitlink: the car's frames, pad, RAM and clock (PLNK v1). "
                               "frame = eyes, press/hold/release = hands, watch + wait_for = event-driven waits."}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": tool_list()}
    elif method == "tools/call":
        p = req.get("params") or {}
        res = call_tool(L, p.get("name"), p.get("arguments"))
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main():
    L = Link()
    log(f"stdio up; car at {L.addr} (dialled on first tool call)")
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}}
        else:
            if not isinstance(req, dict):
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch/invalid request"}}
            else:
                resp = handle(L, req)
        if resp is not None:
            out.write(json.dumps(resp) + "\n")
            out.flush()
    if L.c is not None:
        L.c.close()


if __name__ == "__main__":
    main()
