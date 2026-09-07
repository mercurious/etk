#!/usr/bin/env python3
"""ETK RADIO -- a fake Ollama for tools/radio/test_service.py.

The service is the piece of RADIO that cannot be tested against the real thing on the
host: there is no node here, no 6 GB of weights, and a real debrief is two minutes of
inference. So the tests stand up THIS on an ephemeral loopback port and point
`ollama_url` at it. It answers the three endpoints service.py actually uses:

    POST /api/chat   the debrief / ask call
    GET  /api/tags   the installed models  (/v1/health)
    GET  /api/ps     the resident models   (/v1/health)

A SCRIPT decides what /api/chat says. It is a JSON list, tried in order, first match
wins:

    [{"match": "GPU_FENCE",            # substring of the concatenated user prompt,
                                       # or "*" for anything
      "reply": {...debrief object...}, # an object -> served as a JSON string in
                                       # message.content, which is what constrained
                                       # decoding produces; a STRING is served
                                       # verbatim, which is how malformed JSON,
                                       # a code fence, or prose is simulated
      "prompt_eval_count": 3410,
      "eval_count": 402,
      "delay_s": 0,                    # to blow the client's timeout on purpose
      "status": 200,                   # to simulate an Ollama-side error
      "once": true}]                   # consume this entry after one match, so a
                                       # retry can be answered differently

The five shapes the tests need are all expressible here: a good debrief, the
`hallucinated_key` fixture's model_output, malformed JSON, a reply too slow for the
timeout, and -- by simply not starting the server, or stopping it -- a connection
refusal.

Recording: every request is appended to `server.requests` as
{"path", "body", "at"}, so a test can assert that Ollama was NEVER CALLED (the
over-budget case, spec 6 "never truncate silently") or called exactly twice (one
malformed answer plus its single corrective retry).

Stdlib only, loopback only, no network. Usable standalone for hand-driving:

    python3 tools/radio/fake_ollama.py --port 11434 --script /tmp/script.json
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TAGS = ["etk-radio:9b", "etk-radio:4b"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-ollama/1"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("fake-ollama: " + (fmt % args) + "\n")

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, path, body):
        with self.server.lock:
            self.server.requests.append({"path": path, "body": body, "at": time.time()})

    def do_GET(self):                                              # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        self._record(path, None)
        if path == "/api/tags":
            return self._send(200, {"models": [{"name": n, "size": 1}
                                               for n in self.server.tags]})
        if path == "/api/ps":
            return self._send(200, {"models": [{"name": n, "size_vram": 1}
                                               for n in self.server.resident]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):                                             # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
        self._record(path, body)
        if path != "/api/chat":
            return self._send(404, {"error": "not found"})

        prompt = ""
        for msg in ((body or {}).get("messages") or []):
            if msg.get("role") in ("user", "system"):
                prompt += str(msg.get("content") or "")

        entry = self._pick(prompt)
        if entry is None:
            return self._send(200, self._chat_envelope(body, "{}", 0, 0))
        delay = float(entry.get("delay_s") or 0)
        if delay:
            time.sleep(delay)
        status = int(entry.get("status") or 200)
        if status != 200:
            return self._send(status, {"error": entry.get("reply") or "upstream error"})
        reply = entry.get("reply")
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return self._send(200, self._chat_envelope(
            body, content,
            int(entry.get("prompt_eval_count") or 0),
            int(entry.get("eval_count") or 0)))

    def _pick(self, prompt):
        with self.server.lock:
            for i, entry in enumerate(self.server.script):
                m = entry.get("match", "*")
                if m == "*" or str(m) in prompt:
                    if entry.get("once"):
                        self.server.script.pop(i)
                    return entry
        return None

    @staticmethod
    def _chat_envelope(body, content, prompt_eval, eval_count):
        return {
            "model": (body or {}).get("model") or "etk-radio:9b",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": prompt_eval,
            "eval_count": eval_count,
            "total_duration": 1,
        }


def start(script=None, tags=None, resident=None, port=0, verbose=False):
    """-> (httpd, url, thread). Bind on an ephemeral loopback port by default so
    several tests can run at once and nothing collides with a real Ollama."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.daemon_threads = True
    httpd.lock = threading.Lock()
    httpd.script = list(script or [])
    httpd.tags = list(tags if tags is not None else DEFAULT_TAGS)
    httpd.resident = list(resident or [])
    httpd.requests = []
    httpd.verbose = verbose
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1], thread


def chat_calls(httpd):
    """How many /api/chat calls this fake actually served."""
    with httpd.lock:
        return sum(1 for r in httpd.requests if r["path"] == "/api/chat")


def stop(httpd):
    httpd.shutdown()
    httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--script", help="JSON file: the list documented in __doc__")
    ap.add_argument("--tags", default=",".join(DEFAULT_TAGS))
    ap.add_argument("--resident", default="")
    args = ap.parse_args()
    script = []
    if args.script:
        with open(args.script, encoding="utf-8") as fh:
            script = json.load(fh)
    httpd, url, _ = start(script,
                          tags=[t for t in args.tags.split(",") if t],
                          resident=[t for t in args.resident.split(",") if t],
                          port=args.port, verbose=True)
    print("fake ollama on %s (ctrl-c to stop)" % url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop(httpd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
