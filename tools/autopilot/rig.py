#!/usr/bin/env python3
"""ETK autopilot — the HOST half: ssh bootstrap + JSON-lines client.

`Rig().agent()` streams rig_agent.py over ssh (nothing is written to the rig)
and returns a live RPC handle; closing it (or this process dying) EOFs the
agent's stdin, which releases every input and destroys the virtual pad.
"""
import json
import os
import shlex
import subprocess
import threading
import zlib
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_SRC = os.path.join(HERE, "rig_agent.py")
RIG_ENV = "XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1"
# python3 -c bootstrap: read N, exec the next N bytes of stdin; stdin then
# carries the RPC stream (agent mode).
BOOT = ("import sys;n=int(sys.stdin.buffer.readline());"
        "c=sys.stdin.buffer.read(n);sys.argv=['rig_agent']+sys.argv[1:];"
        "exec(compile(c,'rig_agent.py','exec'))")


def default_host():
    return os.environ.get("ETK_RIG", "etk-rig")


class RpcError(RuntimeError):
    pass


class Agent:
    def __init__(self, host, args=("agent",)):
        cmd = f"env {RIG_ENV} python3 -u -c {shlex.quote(BOOT)} " + " ".join(map(shlex.quote, args))
        self.p = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", host, cmd],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        src = open(AGENT_SRC, "rb").read()
        self.p.stdin.write(b"%d\n" % len(src) + src)
        self.p.stdin.flush()
        self._id = 0
        self._lock = threading.Lock()
        hello = self.p.stdout.readline()
        if not hello:
            raise RpcError("agent failed to start: " + self.p.stderr.read().decode(errors="replace"))
        self.hello = json.loads(hello)

    def call(self, op, **kw):
        with self._lock:
            self._id += 1
            kw.update(op=op, id=self._id)
            self.p.stdin.write((json.dumps(kw) + "\n").encode())
            self.p.stdin.flush()
            line = self.p.stdout.readline()
        if not line:
            raise RpcError("agent died: " + self.p.stderr.read().decode(errors="replace"))
        r = json.loads(line)
        if not r.get("ok"):
            raise RpcError(r.get("err"))
        return r

    def close(self):
        try:
            self.p.stdin.close()  # the dead-man's switch, pulled on purpose
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Rig:
    def __init__(self, host=None):
        self.host = host or default_host()

    def sh(self, script, timeout=30, check=False):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, "sh -s"],
                           input=script.encode(), capture_output=True, timeout=timeout)
        if check and r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace"))
        return r.stdout.decode(errors="replace")

    def agent(self):
        return Agent(self.host)

    def frame(self, path, fmt=None, region=None, timeout=10):
        """grim the live output (the composited game window) to a host file."""
        fmt = fmt or ("jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "png")
        geo = f"-g {shlex.quote(region)} " if region else ""
        q = "-q 85 " if fmt == "jpeg" else ""
        cmd = f"env {RIG_ENV} timeout 5 grim {geo}-t {fmt} {q}-"
        with open(path, "wb") as f:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, cmd],
                               stdout=f, stderr=subprocess.PIPE, timeout=timeout)
        if r.returncode:
            raise RuntimeError("grim failed: " + r.stderr.decode(errors="replace"))
        return path

    def snap(self, ranges="all", timeout=600):
        """Bulk guest-RAM snapshot -> (meta, {guest_start: bytes})."""
        src = open(AGENT_SRC, "rb").read()
        cmd = f"python3 -c {shlex.quote(BOOT)} snap {shlex.quote(ranges)}"
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, cmd],
                           input=b"%d\n" % len(src) + src, capture_output=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError("snap failed: " + r.stderr.decode(errors="replace"))
        n = struct.unpack_from("<I", r.stdout, 0)[0]
        meta = json.loads(r.stdout[4:4 + n])
        blob = zlib.decompress(r.stdout[4 + n:])
        out, off = {}, 0
        for g0, g1 in meta["ranges"]:
            out[g0] = blob[off:off + (g1 - g0)]
            off += g1 - g0
        return meta, out
