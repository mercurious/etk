#!/usr/bin/env python3
"""pitlink — the Engineer's one-shot CLI onto a Pitlink link (PLNK v1).

  pitlink.py [--addr A] status                     HELLO + STATUS + RTT + the latest frame's identity
  pitlink.py frame OUT.png [--snap] [--max-w W]    the next frame (--snap: one native-size frame)
  pitlink.py press cross [circle ...] [--ms 120]   tap, counted in flips ON THE CAR
  pitlink.py hold lx=-0.3 r2=0.6 cross=1           set the pad (sticks -1..1, buttons 0/1, pressure 0..1)
             [--secs S]                            ... and keep it S seconds (heartbeat), then release
             [--ttl MS]                            ... one-shot: the car's dead-man ends it after MS
  pitlink.py release [--port 0]                    agent pad -> neutral, schedule cleared
  pitlink.py watch 0xADDR:size ... [--secs 3]      set the per-flip WATCH list, print frame-aligned values
  pitlink.py ram 0xADDR size                       hexdump guest RAM
  pitlink.py pause | resume | step N [--wait] | exit [--savestate]
  pitlink.py garage OP [k=v ...] [--out F]         car control over USB, no game needed:
             status | running | games [filter=gt] | launch game=BCUS98296 | log [grep=RX tail=N]
             | dump_threads (ARMSX3 thread dump: every PPU/SPU/RSX thread, returns its log)
  pitlink.py serve                                 HOLD the wheel (run in the background): one
             long-lived controller; every other `pitlink.py` run on this host is relayed through
             it, so an observer window (./onboard) keeps its frames between the Engineer's taps

--addr: @name (abstract unix, same host) or host:port; default $PITLINK_ADDR else
usb (raw USB via usb_broker.py, spawned on first use); host:port = TCP fallback.
--token default $PITLINK_TOKEN.

Each run is its own client. Only the controller (the first client to HELLO) may
drive; while another client (e.g. the MCP server) holds the wheel, this CLI is
an observer: status/frame/ram work, control ops are refused by the car. Neither
the WATCH list nor a press dies with this process — they live on the car; a
one-shot hold lives until the car's dead-man (ttl).

THE HELD WHEEL (`serve`). PLNK v1 has no "observer please": the first HELLO while
the wheel is free gets it. A one-shot CLI therefore takes and drops the wheel at
every tap, and between taps nobody holds it -- so ./onboard (which refuses a wheel
its HELLO lands) sees nothing while a game runs. `serve` is the fix on the host
side: it HELLOs once, keeps the wheel idle, and listens on $PITLINK_WHEEL
(default @etk-pitlink-wheel, abstract unix, this host only). Every other run
whose command needs a car link first tries that socket and, when it answers,
sends its argv there and prints what the holder printed -- same output, same
exit status, no second HELLO. `--direct` skips the relay. When the holder is an
observer (something else has the wheel) it does not listen, and runs fall back
to their own link exactly as before. Ctrl-C / SIGTERM releases every held pad.
"""
import argparse
import contextlib
import io
import json
import os
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plnk as P  # noqa: E402
from client import PitlinkClient, garage, PitlinkError, default_addr  # noqa: E402

CONTROL = {"press", "hold", "release", "watch", "pause", "resume", "step", "exit"}
WHEEL_ADDR = os.environ.get("PITLINK_WHEEL", "@etk-pitlink-wheel")  # the held wheel's relay socket
WHEEL_RETRY_S = 5.0  # serve: while another client holds the wheel, re-HELLO this often
WHEEL_PING_S = 2.0   # serve: idle link check


def _wheel_sockaddr(addr=WHEEL_ADDR):
    return "\0" + addr[1:] if addr.startswith("@") else addr


def wheel_relay(argv, cwd, addr=WHEEL_ADDR):
    """Send this run's argv to a `serve` holder on this host. -> (stdout, stderr, exit code),
    or None when no holder listens (the caller then opens its own link)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(2.0)
        try:
            s.connect(_wheel_sockaddr(addr))
        except OSError:
            return None
        s.settimeout(None)  # a `hold --secs S` or a long `watch` runs on the holder: wait for it
        s.sendall((json.dumps({"argv": list(argv), "cwd": cwd}) + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf:
        return None
    rep = json.loads(buf.decode())
    return rep.get("out", ""), rep.get("err", ""), int(rep.get("code", 0))


def wheel_serve(ap, a):
    """Hold the wheel; relay other runs' argv over WHEEL_ADDR while we are the controller."""
    addr = a.addr or default_addr()
    say = lambda m: print(f"wheel: {m}", flush=True)  # noqa: E731
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(_wheel_sockaddr(a.wheel_addr))
        say(f"a holder already listens on {a.wheel_addr}: one wheel per host, not starting")
        return 2
    except OSError:
        pass
    finally:
        probe.close()
    stop = {"now": False}

    def on_signal(signum, _frame):
        stop["now"] = True
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, on_signal)

    backoff = 1.0
    while not stop["now"]:
        try:
            c = PitlinkClient(addr, timeout=a.timeout)
        except OSError as e:
            say(f"car unreachable at {addr}: {e}; retry in {backoff:g} s")
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                return 0
            backoff = min(backoff * 2, 10.0)
            continue
        backoff = 1.0
        lst = None
        try:
            info = c.hello(a.token)
            if not c.controller:
                say(f"observer on {info.get('title_id')} {info.get('title')}: another client holds the "
                    f"wheel; not relaying, re-HELLO in {WHEEL_RETRY_S:g} s")
                c.close()
                time.sleep(WHEEL_RETRY_S)
                continue
            lst = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            lst.bind(_wheel_sockaddr(a.wheel_addr))
            lst.listen(8)
            lst.settimeout(WHEEL_PING_S)
            say(f"holding the wheel on {info.get('title_id')} {info.get('title')} via {addr}; "
                f"relaying {a.wheel_addr} (./onboard lands as an observer and keeps its frames)")
            while not c.closed:
                try:
                    conn, _ = lst.accept()
                except socket.timeout:
                    c.ping()  # a dead link raises; loop ends, we re-HELLO
                    continue
                _wheel_handle(ap, c, info, conn, say)
            say(f"link died: {c.closed}; re-HELLO")
        except KeyboardInterrupt:
            say("stopping: releasing every held pad")
            for port in list(c.held):
                try:
                    c.release(port)
                except PitlinkError:
                    pass
            return 0
        except (PitlinkError, OSError) as e:
            say(f"link: {e}; re-HELLO")
        finally:
            if lst is not None:
                lst.close()
            c.close()
        time.sleep(1.0)
    return 0


def _wheel_handle(ap, c, info, conn, say):
    out, err, code = io.StringIO(), io.StringIO(), 0
    try:
        conn.settimeout(5.0)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        req = json.loads(buf.decode() or "{}")
        argv = req.get("argv", [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                a2 = ap.parse_args(argv)
                if a2.cmd in ("serve", "garage"):
                    raise SystemExit(f"pitlink: {a2.cmd} is not relayed through the held wheel")
                if a2.cmd == "frame" and not os.path.isabs(a2.out):
                    a2.out = os.path.join(req.get("cwd") or os.getcwd(), a2.out)
                run(a2, c, info)
            except SystemExit as e:  # argparse errors and run()'s own sys.exit(msg)
                if isinstance(e.code, str):
                    print(e.code, file=sys.stderr)
                    code = 1
                else:
                    code = int(e.code or 0)
            except PitlinkError as e:
                print(f"pitlink: {e}", file=sys.stderr)
                code = 1
        say(f"relayed {' '.join(argv)!s} -> exit {code}")
        conn.settimeout(None)
        conn.sendall(json.dumps({"out": out.getvalue(), "err": err.getvalue(), "code": code}).encode())
    except (OSError, ValueError) as e:
        say(f"relay request dropped: {e}")
    finally:
        conn.close()


def parse_items(specs):
    out = []
    for s in specs:
        a, _, n = s.partition(":")
        out.append((int(a, 0), int(n or 4, 0)))
    return out


def parse_controls(specs, port=0):
    st = P.PadState(port=port)
    for s in specs:
        k, eq, v = s.partition("=")
        try:
            st.set(k, float(v) if eq else 1)
        except KeyError as e:
            sys.exit(f"{e.args[0]}; sticks {', '.join(P.STICKS)}")
    return st


def fresh_frame(c, timeout):
    """A frame from AFTER this call: the car's flip now (PING), then the first frame past it.
    The frame the car pushes at HELLO can be thousands of flips old (the tap only runs while
    someone watches) -- 2026-10-10 it made two presses look ignored. It is the answer only
    when no new flip comes (paused car)."""
    flip, _ = c.ping()
    try:
        return c.wait_frame(timeout=min(timeout, 2.0), after_flip=flip)
    except PitlinkError:
        return c.latest_frame()


def hexdump(base, b):
    for i in range(0, len(b), 16):
        row = b[i:i + 16]
        print(f"{base + i:08x}  {row.hex(' ', 4):<35}  " + "".join(chr(c) if 32 <= c < 127 else "." for c in row))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--addr", default=None)
    ap.add_argument("--token", default=os.environ.get("PITLINK_TOKEN", ""))
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--direct", action="store_true", help="own link even when a `serve` holder is up")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("status", "pause", "resume"):
        sub.add_parser(c)
    p = sub.add_parser("serve")
    p.add_argument("--wheel-addr", default=WHEEL_ADDR, help="relay socket (@name = abstract unix)")
    p = sub.add_parser("frame")
    p.add_argument("out")
    p.add_argument("--snap", action="store_true", help="one native-size frame (controller only)")
    p.add_argument("--codec", type=int, default=None)
    p.add_argument("--max-w", type=int, default=None)
    p = sub.add_parser("press")
    p.add_argument("buttons", nargs="+")
    p.add_argument("--ms", type=int, default=120)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--lead", type=int, default=2, help="flips before the down edge (0 = now)")
    p = sub.add_parser("hold")
    p.add_argument("ctl", nargs="+")
    p.add_argument("--secs", type=float, default=0)
    p.add_argument("--ttl", type=int, default=0, help="ms (max 65535; 0 = the car's default)")
    p.add_argument("--port", type=int, default=0)
    p = sub.add_parser("release")
    p.add_argument("--port", type=int, default=0)
    p = sub.add_parser("watch")
    p.add_argument("items", nargs="+", help="0xADDR:size (1/2/4/8)")
    p.add_argument("--secs", type=float, default=3.0)
    p.add_argument("--kinds", default="", help="per item u|s|f, comma-separated (default u)")
    p = sub.add_parser("ram")
    p.add_argument("address")  # not `addr`: that dest is --addr
    p.add_argument("size", nargs="?", default="256")
    p = sub.add_parser("step")
    p.add_argument("n", type=int)
    p.add_argument("--wait", action="store_true", help="block for EVENT 7 (step complete)")
    p = sub.add_parser("exit")
    p.add_argument("--savestate", action="store_true")
    p = sub.add_parser("garage")
    p.add_argument("op")
    p.add_argument("kv", nargs="*", help="k=v request fields")
    p.add_argument("--out", default=None, help="also write the reply's text to this file")
    a = ap.parse_args()

    if a.cmd == "garage":  # needs no running game: the car's daemon answers, not RPCS3
        import json
        req = {"op": a.op}
        for kv in a.kv:
            k, _, v = kv.partition("=")
            req[k] = int(v) if v.lstrip("-").isdigit() else v
        try:
            rep = garage(req, timeout=max(a.timeout, 30.0))
        except (OSError, PitlinkError) as e:
            sys.exit(f"pitlink: garage {a.op}: {e}")
        text = rep.pop("text", None)
        if a.out and text is not None:
            with open(a.out, "w") as f:
                f.write(text + "\n")
        print(json.dumps(rep, indent=1))
        if text and not a.out:
            print(text)
        return

    if a.cmd == "serve":
        sys.exit(wheel_serve(ap, a))

    if not a.direct:  # a `serve` holder on this host answers for us: one link, one wheel
        rep = wheel_relay(sys.argv[1:], os.getcwd())
        if rep is not None:
            out, err, code = rep
            sys.stdout.write(out)
            sys.stderr.write(err)
            sys.exit(code)

    try:
        c = PitlinkClient(a.addr or default_addr(), timeout=a.timeout)
    except OSError as e:
        sys.exit(f"pitlink: cannot reach {a.addr or default_addr()}: {e}")
    try:
        info = c.hello(a.token)
        if a.cmd in CONTROL and not c.controller:
            sys.exit("pitlink: observer — another client holds the wheel (control ops are refused)")
        run(a, c, info)
    except PitlinkError as e:
        sys.exit(f"pitlink: {e}")
    finally:
        c.close()


def run(a, c, info):
    if a.cmd == "status":
        flip, _ = c.ping()
        st = c.status()
        out = {**info, **st, "addr": c.addr, "role": "controller" if c.controller else "observer",
               "state": {0: "running", 1: "paused"}.get(st["state"], "other"),
               "ping_flip": flip, "rtt_ms": round(c.rtt_ms, 2)}
        f = fresh_frame(c, a.timeout)
        out["frame"] = f and f.meta()  # None: video off and nothing tapped yet
        print(json.dumps(out, indent=1))
    elif a.cmd == "frame":
        if a.snap:
            if not c.controller:
                sys.exit("pitlink: --snap is a control op; observer here")
            f = c.snap(P.CODEC_ZSTD if a.codec is None and P.ZSTD else (a.codec or 0), timeout=a.timeout)
        else:
            f = fresh_frame(c, a.timeout)
            if f is None:
                sys.exit("pitlink: no frame (video off? try --snap)")
        f.png(a.out, a.max_w)
        print(f"{a.out}: {f!r}")
    elif a.cmd == "press":
        r = c.press(a.buttons, a.ms, a.port, a.lead)
        print(f"press {'+'.join(a.buttons)}: {r['flips']} flips @ {c.fps():.1f} fps, "
              f"down at flip {r['down_at']}, up at {r['up_at']} (ttl {r['ttl_ms']} ms)")
    elif a.cmd == "hold":
        st = parse_controls(a.ctl, a.port)
        st.ttl_ms = a.ttl
        if a.secs:
            c.hold(st)
            print(f"holding {st} for {a.secs:g} s (Ctrl-C releases)", flush=True)
            try:
                time.sleep(a.secs)
            except KeyboardInterrupt:
                pass
            c.release(a.port)
            print("released")
        else:
            c.pad(st)
            print(f"sent {st}: the car's dead-man ends it after {a.ttl or 'its default'} ms")
    elif a.cmd == "release":
        c.release(a.port)
        print(f"port {a.port} released")
    elif a.cmd == "watch":
        items = parse_items(a.items)
        kinds = a.kinds.split(",") if a.kinds else ["u"] * len(items)
        c.watch(items)
        print("flip\t" + "\t".join(a.items))
        t_end = time.monotonic() + a.secs
        while time.monotonic() < t_end:
            try:
                f = c.wait_frame(timeout=max(0.05, t_end - time.monotonic()))
            except PitlinkError:
                break
            v = c.watch_values(f, kinds)
            if v is not None:
                print(f"{f.flip}\t" + "\t".join(f"{x:.6g}" if isinstance(x, float) else str(x) for x in v))
    elif a.cmd == "ram":
        addr, n = int(a.address, 0), int(a.size, 0)
        b = c.ram_read([(addr, n)])[0]
        if b is None:
            sys.exit(f"pitlink: {addr:#x}+{n} unreadable")
        hexdump(addr, b)
    elif a.cmd == "pause":
        c.pause()
        print("paused")
    elif a.cmd == "resume":
        c.resume()
        print("running")
    elif a.cmd == "step":
        mark = c.event_mark
        c.step(a.n)
        if a.wait:
            ev = c.wait_event(P.EV_STEP, timeout=max(a.timeout, a.n / 10.0), after=mark)
            print(f"step {a.n}: complete ({ev.text})")
        else:
            print(f"step {a.n}: started")
    elif a.cmd == "exit":
        c.exit(a.savestate)
        print("exit requested" + (" (savestate)" if a.savestate else ""))


if __name__ == "__main__":
    main()
