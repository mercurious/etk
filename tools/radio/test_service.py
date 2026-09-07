#!/usr/bin/env python3
"""ETK RADIO -- host-side regression tests for the NODE service and the RIG sender.

Run from the repo root:   python3 tools/radio/test_service.py

There is no node here: no 6 GB of weights, no Ollama, no 23 GB Ampere box, and a real
debrief is two minutes of inference. So every section below stands the service up
IN THIS PROCESS against tools/radio/fake_ollama.py on an ephemeral loopback port, and
drives it the way the rig will. No network beyond 127.0.0.1, no ssh, no rig, no root.

What each section is for (and what would make it fail for the right reason):

  [1] AUTH        every route is bearer-gated and an unauthenticated caller learns
                  nothing -- and the token never reaches a log line (paddock_sync law).
  [2] BODY CAP    64 KB, checked from Content-Length BEFORE the body is read.
  [3] CONTRACT    a pack that fails pack.v1 is 422 with the first errors, not a job.
  [4] SUBMIT      a good pack is 202 {"job": id, "status": "queued"}.
  [5] WORKER      fake Ollama serves a good debrief: done, valid, tags computed on the
                  NODE, prompt_sha256 stamped, token counts from Ollama's own reply.
  [6] GUARDS      the hallucinated_key fixture's model_output, served verbatim: both
                  invented config changes dropped, named in guards.dropped, and the
                  case's own eval assertions pass.
  [7] RETRY       malformed JSON buys exactly ONE corrective retry, then fails.
  [8] FORGE       spec 1.8: the forge outranks the radio. Locked -> deferred; the
                  marker removed -> the same job runs.
  [9] BUDGET      spec 6 never-truncate: an over-budget pack FAILS, and Ollama is
                  never called at all. This is the guard that the 2026-09-06 reviews
                  paid for -- three of four prompts were cut at 4,098 tokens and the
                  models judged fragments as whole files.
 [10] HEALTH      ollama false when the fake is down; the service still answers.
 [11] ASK         /v1/ask answers in ASCII inside the timeout on the fast model.
 [12] RETENTION   a result older than retention_days, and its row, are deleted.
 [13] RULES-ONLY  a debrief with no model running at all.
 [14] SENDER      bin/radio_send.sh, run with `sh` against the live loopback service.

BUSYBOX HONESTY: section 14 runs the sender under this host's `sh`, which is bash
(/bin/sh -> bash on this laptop; there is no busybox here and no checkbashisms in the
PATH). SO THIS TEST CANNOT DISCRIMINATE BUSYBOX BEHAVIOUR -- it proves the script's
LOGIC, not its portability. Portability is held by construction and by a source audit
in section 14: the constructs deliberately avoided are [[ ]], local, arrays,
${var,,}/${var^^}, $'...', echo -e/-n, +=, the `function` keyword, process
substitution, here-strings, set -o pipefail, trap ERR, read -a, mapfile, sed -i,
stat --format, find -printf, grep -P, seq, and GNU long options on busybox applets;
`sh -n` and checkbashisms run when they are available and are skipped, loudly, when
they are not. The one construct that MUST be verified on the rig is the subshell
source of scripts/env.sh, which is bash (${BASH_SOURCE[0]}) -- see the sender header.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ollama                                                  # noqa: E402
import schemas                                                      # noqa: E402
import service                                                      # noqa: E402

SENDER = os.path.join(ROOT, "bin", "radio_send.sh")
CASE_DIR = os.path.join(HERE, "eval_cases")
FAILS = []
SKIPS = []


# ----------------------------------------------------------------------- harness
def check(name, got, want):
    if got == want:
        print("    ok   %s" % name)
    else:
        print("    FAIL %s: got %r, want %r" % (name, got, want))
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else "%s (%s)" % (name, why), bool(cond), True)


def skip(name, why):
    print("    SKIP %s -- %s" % (name, why))
    SKIPS.append(name)


def have(module):
    """Is a wave-mate's module importable yet? The sections that need one say so."""
    try:
        __import__(module)
        return True
    except Exception:                                              # noqa: BLE001
        return False


class LogCapture:
    """service.log() writes to sys.stderr; hold it so a section can assert on what
    did -- and did NOT -- reach journald."""

    def __enter__(self):
        self.buf = io.StringIO()
        self._old = sys.stderr
        sys.stderr = self.buf
        return self

    def __exit__(self, *exc):
        sys.stderr = self._old
        return False

    def text(self):
        return self.buf.getvalue()


TOKEN = "ffb2b0a95f1c4e2d8a7b6c5d4e3f201122334455667788990011223344556677"


class Node:
    """One disposable etk-cloud in a temp dir: config, token, dirs, Service, and (on
    request) the HTTP face and the worker thread."""

    def __init__(self, tmp, ollama_url="http://127.0.0.1:1", **over):
        self.tmp = tmp
        self.token_file = os.path.join(tmp, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(TOKEN + "\n")
        cfg = {
            "bind": "127.0.0.1",
            "port": 0,                       # ephemeral: never collide with a real node
            "token_file": self.token_file,
            "ollama_url": ollama_url,
            "jobs_db": os.path.join(tmp, "jobs.db"),
            "results_dir": os.path.join(tmp, "results"),
            "packs_dir": os.path.join(tmp, "packs"),
            "corpus_root": ROOT,             # a real checkout: corpus_commit is honest
            "forge_runs_dir": os.path.join(tmp, "forge-runs"),
            "git_pull": False,               # the corpus is this checkout, as it is
            "forge_poll_s": 0.2,
            "chat_timeout_s": 10,
            "ask_timeout_s": 5,
        }
        cfg.update(over)
        self.cfg_path = os.path.join(tmp, "config.json")
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        self.cfg = service.load_config(self.cfg_path)
        os.makedirs(self.cfg["forge_runs_dir"], exist_ok=True)
        self.svc = service.Service(self.cfg)
        self.httpd = None
        self.worker = None

    def serve(self):
        self.httpd = service.make_server(self.svc)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.url

    def work(self):
        self.worker = threading.Thread(target=self.svc.worker_loop, daemon=True)
        self.worker.start()

    def close(self):
        self.svc.stop.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def call(url, path, token=TOKEN, body=None, raw=None, method=None, headers=None):
    """-> (status, obj_or_text). A 4xx/5xx is a RESULT here, not an exception."""
    data = raw
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if token is not None:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        code = exc.code
    try:
        return code, json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return code, payload.decode("utf-8", "replace")


def load_case(name):
    with open(os.path.join(CASE_DIR, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


CASE = load_case("hallucinated_key")
PACK = CASE["pack"]
EPOCH = PACK["epoch"]


def good_model_output():
    """The case's exemplar reduced to what a CONSTRAINED model actually emits: the
    provenance half is the service's to stamp (service.SERVICE_FIELDS)."""
    out = dict(CASE["exemplar"])
    for key in service.SERVICE_FIELDS:
        out.pop(key, None)
    return out


def script_entry(reply, **kw):
    e = {"match": "*", "reply": reply, "prompt_eval_count": 3410, "eval_count": 402}
    e.update(kw)
    return e


def wait_for(fn, timeout=20, tick=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(tick)
    return None


TMP = tempfile.mkdtemp(prefix="etk_radio_svc_")
print("ETK RADIO -- service + sender regression (%s)" % TMP)
print("fixture pack: eval_cases/hallucinated_key.json  epoch=%s  game=%s"
      % (EPOCH, PACK["game_id"]))

try:
    # =================================================================== [0] fixture
    print("\n[0] the fixture itself (a test on a bad fixture proves nothing)")
    check("fixture pack validates against pack.v1",
          schemas.validate(PACK, schemas.load("pack.v1")), [])
    check("good model output validates against the MODEL VIEW of debrief.v1",
          schemas.validate(good_model_output(),
                           service.model_format(schemas.load("debrief.v1"))), [])
    check_true("the case's own model_output is the guards' problem, not the schema's",
               not schemas.validate(CASE["model_output"],
                                    service.model_format(schemas.load("debrief.v1"))),
               "a foreign yaml_key is a VALID shape carrying a wrong answer")

    # ====================================================================== [1] auth
    print("\n[1] auth: every route is bearer-gated, and the token is never logged")
    d = os.path.join(TMP, "auth")
    os.makedirs(d)
    node = Node(d)
    url = node.serve()
    with LogCapture() as cap:
        for route, method, body in (("/v1/health", None, None),
                                    ("/v1/jobs/deadbeefdeadbeef", None, None),
                                    ("/v1/debrief", "POST", PACK),
                                    ("/v1/ask", "POST", {"pack_epoch": EPOCH})):
            code, obj = call(url, route, token=None, body=body, method=method)
            check("%s %s without a token -> 401" % (method or "GET", route), code, 401)
            check("...and carries no body detail", obj, "")
        code, _ = call(url, "/v1/health", token="not-the-token")
        check("a WRONG token -> 401", code, 401)
        code, _ = call(url, "/v1/health", token=TOKEN[:-1] + "0")
        check("a token wrong in ONE character -> 401", code, 401)
        code, obj = call(url, "/v1/health")
        check("the right token -> 200", code, 200)
    logged = cap.text()
    check_true("the token is nowhere in the log", TOKEN not in logged)
    check_true("a wrong token is not echoed either", "not-the-token" not in logged)
    check_true("the 401s were logged as 401s", logged.count("401 ") >= 4)
    check("an unknown route (authenticated) -> 404",
          call(url, "/v1/nope")[0], 404)
    check("an unknown job id -> 404",
          call(url, "/v1/jobs/aaaabbbbccccdddd")[0], 404)

    # ================================================================== [2] body cap
    print("\n[2] body cap: 64 KB, from Content-Length, before the body is read")
    big = json.dumps({"schema": "ETK-RADIO-PACK v1", "epoch": EPOCH,
                      "game_id": "NPEA00050", "session": {"epoch": str(EPOCH)},
                      "pack_notes": ["x" * 900] * 80}).encode("utf-8")
    check_true("the oversized body really is over 64 KB", len(big) > 65536,
               "%d B" % len(big))
    code, obj = call(url, "/v1/debrief", raw=big,
                     headers={"Content-Type": "application/json"})
    check("a body over 64 KB -> 413", code, 413)
    check_true("413 names the cap, not the content", "64 KB" in json.dumps(obj))
    code, _ = call(url, "/v1/debrief", raw=b"not json",
                   headers={"Content-Type": "application/json"})
    check("a body that is not JSON -> 400", code, 400)

    # ================================================================== [3] contract
    print("\n[3] contract: a pack that fails pack.v1 never becomes a job")
    bad = dict(PACK)
    bad.pop("session")
    code, obj = call(url, "/v1/debrief", body=bad)
    check("a pack missing a required section -> 422", code, 422)
    check("422 names the contract", obj.get("error"), "pack.v1")
    check_true("422 carries the first errors, not a shrug",
               any("session" in e for e in obj.get("errors", [])),
               obj.get("errors", ["<none>"])[0])
    code, obj = call(url, "/v1/debrief", body={"schema": "nope"})
    check("a foreign object -> 422", code, 422)
    check("no job was created by any rejected pack",
          node.svc.jobs.counts().get("queued", 0), 0)

    # ==================================================================== [4] submit
    print("\n[4] submit: a good pack -> 202 and a job id")
    code, obj = call(url, "/v1/debrief", body=PACK)
    check("a valid pack -> 202", code, 202)
    check("202 says queued", obj.get("status"), "queued")
    jid = obj.get("job")
    check_true("the job id is opaque and filename-safe (the rig writes pending/<job>)",
               bool(re.match(r"\A[0-9a-f]{8,64}\Z", str(jid))), str(jid))
    check("the pack was stored for /v1/ask",
          os.path.exists(node.svc.pack_path(EPOCH)), True)
    code, obj = call(url, "/v1/jobs/%s" % jid)
    check("GET the job -> 200 queued", (code, obj.get("status")), (200, "queued"))
    check("the job knows its row", obj.get("epoch"), EPOCH)
    node.close()

    # ==================================================================== [5] worker
    print("\n[5] worker: the fake Ollama serves a good debrief")
    d = os.path.join(TMP, "worker")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([script_entry(good_model_output())],
                                      resident=["etk-radio:9b"])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    with LogCapture() as cap:
        code, obj = call(url, "/v1/debrief", body=PACK)
        jid = obj["job"]
        node.svc.run_once()
        code, obj = call(url, "/v1/jobs/%s" % jid)
    check("the job finished", (code, obj.get("status")), (200, "done"))
    deb = obj.get("debrief") or {}
    check("the stored debrief validates against debrief.v1",
          schemas.validate(deb, schemas.load("debrief.v1")), [])
    check("the result landed under results_dir",
          os.path.exists(os.path.join(node.cfg["results_dir"], "%s.json" % EPOCH)), True)
    check("the service stamped the row's epoch", deb.get("epoch"), EPOCH)
    check("the service stamped the model", deb.get("model"), "etk-radio:9b")
    check_true("prompt_sha256 is a real 64-hex stamp",
               bool(re.match(r"\A[0-9a-f]{64}\Z", deb.get("prompt_sha256", ""))))
    check_true("corpus_commit is the checkout's, not a guess",
               bool(re.match(r"\A[0-9a-f]{7,40}\Z", deb.get("corpus_commit", ""))),
               deb.get("corpus_commit"))
    check("token counts come from Ollama's own reply, never from prose",
          deb.get("tokens"), {"prompt": 3410, "completion": 402})
    check_true("latency was measured", isinstance(deb.get("latency_s"), (int, float)))
    import eval as radio_eval                                       # noqa: E402
    computed = radio_eval.compute_tags(PACK)
    check_true("the NODE's computed tags survive into the debrief",
               set(computed) <= set(deb.get("tags") or []),
               "computed %s, debrief %s" % (computed, deb.get("tags")))
    check("exactly one chat call for a clean answer", fake_ollama.chat_calls(fake), 1)
    body = fake.requests[0]["body"]
    check("stream is off (the rig polls; nothing is held open)",
          body.get("stream"), False)
    check("keep_alive holds the weights between calls", body.get("keep_alive"), "30m")
    check("think is off by default (spec 4.1)", body.get("think"), False)
    # The context sent MUST be the one the budget was computed against: briefing.build
    # raises num_ctx for a long pack, and sending the Modelfile default anyway would be
    # the silent truncation spec 6 forbids.
    want_ctx = node.svc.briefing(PACK).get("num_ctx")
    check("num_ctx is set explicitly, and is the one the budget used",
          (body.get("options") or {}).get("num_ctx"), want_ctx)
    check("num_predict caps generation", (body.get("options") or {}).get("num_predict"),
          700)
    fmt = body.get("format") or {}
    check_true("format: is the debrief schema, so decoding is constrained",
               fmt.get("title") == "ETK RADIO DEBRIEF v1"
               and fmt.get("additionalProperties") is False)
    check_true("the model is never asked to invent its own provenance",
               all(k not in (fmt.get("properties") or {})
                   for k in service.SERVICE_FIELDS))
    log = cap.text()
    check_true("the log names the row, not the pack", "epoch=%s" % EPOCH in log)
    check_true("no pack content in the log",
               "GPU_FENCE_TIMEOUT" not in log and "dev_hdd0" not in log)
    node.close()
    fake_ollama.stop(fake)

    # ==================================================================== [6] guards
    print("\n[6] guards: the hallucinated_key model_output, served verbatim")
    if not have("guards"):
        skip("hallucinated_key end to end",
             "tools/radio/guards.py is not in the tree yet (agent B). The service "
             "degrades: guards.passed=false and nothing is dropped, which is the "
             "designed visible failure, not a silent pass.")
        d = os.path.join(TMP, "noguards")
        os.makedirs(d)
        fake, furl, _ = fake_ollama.start([script_entry(CASE["model_output"])])
        node = Node(d, ollama_url=furl)
        url = node.serve()
        code, obj = call(url, "/v1/debrief", body=PACK)
        node.svc.run_once()
        _, obj = call(url, "/v1/jobs/%s" % obj["job"])
        deb = obj.get("debrief") or {}
        check("an unguarded debrief is still schema-valid",
              schemas.validate(deb, schemas.load("debrief.v1")), [])
        check("an unguarded debrief says so: guards.passed is false",
              (deb.get("guards") or {}).get("passed"), False)
        check_true("and it says WHY, so nothing is quietly trusted",
                   any("guards unavailable" in (x.get("reason") or "")
                       for x in (deb.get("guards") or {}).get("dropped", [])))
        node.close()
        fake_ollama.stop(fake)
    else:
        d = os.path.join(TMP, "guards")
        os.makedirs(d)
        fake, furl, _ = fake_ollama.start([script_entry(CASE["model_output"])])
        node = Node(d, ollama_url=furl)
        url = node.serve()
        code, obj = call(url, "/v1/debrief", body=PACK)
        node.svc.run_once()
        _, obj = call(url, "/v1/jobs/%s" % obj["job"])
        check("the guarded job still finishes", obj.get("status"), "done")
        deb = obj.get("debrief") or {}
        check("the guarded debrief validates",
              schemas.validate(deb, schemas.load("debrief.v1")), [])
        keys = [c.get("yaml_key")
                for r in (deb.get("recommendations") or [])
                for c in (r.get("config_changes") or [])]
        check("the invented yaml_key is gone",
              any("Shader Cache Depth" in str(k) for k in keys), False)
        check("the out-of-range Resolution Scale change is gone (dropped, not clamped)",
              any("Resolution Scale" in str(k) for k in keys), False)
        dropped = json.dumps((deb.get("guards") or {}).get("dropped") or [])
        check_true("guards.dropped names each drop -- a drop is never silent",
                   re.search(r"(?i)(shader cache depth|resolution scale|"
                             r"pitstop_fields|out of range)", dropped), dropped[:120])
        results = radio_eval.grade(CASE, deb)
        for op, ok, detail in results:
            check_true("eval[%s] %s" % (CASE["id"], radio_eval.op_label(op)), ok, detail)
        node.close()
        fake_ollama.stop(fake)

    # ===================================================================== [7] retry
    print("\n[7] a malformed answer buys exactly one corrective retry")
    d = os.path.join(TMP, "retry")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([script_entry("here you go: {not, json")])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    code, obj = call(url, "/v1/debrief", body=PACK)
    jid = obj["job"]
    node.svc.run_once()
    _, obj = call(url, "/v1/jobs/%s" % jid)
    check("the job failed", obj.get("status"), "failed")
    check_true("the failure says what went wrong",
               "not JSON" in (obj.get("error") or ""), obj.get("error"))
    check("one answer plus ONE retry, never a loop", fake_ollama.chat_calls(fake), 2)
    retry = fake.requests[1]["body"]["messages"]
    check_true("the retry is corrective: the bad answer is shown back to the model",
               len(retry) == 4 and retry[-1]["role"] == "user"
               and "not valid JSON" in retry[-1]["content"])
    check("no result file was written for a failed job",
          os.path.exists(os.path.join(node.cfg["results_dir"], "%s.json" % EPOCH)),
          False)
    # A fenced answer is a formatting slip, not a different answer: it must survive.
    with fake.lock:
        fake.script[:] = [script_entry("```json\n"
                                       + json.dumps(good_model_output()) + "\n```")]
    code, obj = call(url, "/v1/debrief", body=PACK)
    node.svc.run_once()
    _, obj = call(url, "/v1/jobs/%s" % obj["job"])
    check("a code-fenced JSON answer is accepted without a retry",
          obj.get("status"), "done")
    node.close()
    fake_ollama.stop(fake)

    # ===================================================================== [8] forge
    print("\n[8] the forge outranks the radio (spec 1.8)")
    d = os.path.join(TMP, "forge")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([script_entry(good_model_output())])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    marker = os.path.join(node.cfg["forge_runs_dir"], "active_image")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("lane image, pid 1234\n")
    code, obj = call(url, "/v1/debrief", body=PACK)
    jid = obj["job"]
    with LogCapture() as cap:
        node.svc.run_once()
    _, obj = call(url, "/v1/jobs/%s" % jid)
    check("a job submitted while a lane is live -> deferred", obj.get("status"),
          "deferred")
    check_true("the reason is in the log", "deferred: forge active" in cap.text())
    check("the model was NOT called while the forge held the box",
          fake_ollama.chat_calls(fake), 0)
    os.remove(marker)
    node.svc.run_once()
    _, obj = call(url, "/v1/jobs/%s" % jid)
    check("the marker gone, the SAME job runs", obj.get("status"), "done")
    check("and it took exactly one call", fake_ollama.chat_calls(fake), 1)
    node.close()
    fake_ollama.stop(fake)

    # ==================================================================== [9] budget
    print("\n[9] never truncate silently: an over-budget pack is refused, unasked")
    d = os.path.join(TMP, "budget")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([script_entry(good_model_output())])
    # num_predict is raised so the refusal is pinned to the ARITHMETIC rather than to
    # one briefing's exact size: the budget is num_ctx - num_predict - slack whoever
    # built the prompt, and a pack padded to the 64 KB door has to lose against it.
    node = Node(d, ollama_url=furl, num_predict=3000)
    url = node.serve()
    fat = json.loads(json.dumps(PACK))
    fat["pack_notes"] = list(fat.get("pack_notes") or []) + ["padding " * 60] * 90
    blob = json.dumps(fat).encode("utf-8")
    check_true("the padded pack is legal (inside 64 KB) but too long to think about",
               len(blob) < 65536, "%d B" % len(blob))
    check("a padded pack is still a valid pack",
          schemas.validate(fat, schemas.load("pack.v1")), [])
    code, obj = call(url, "/v1/debrief", body=fat)
    check("it is accepted at the door", code, 202)
    with LogCapture() as cap:
        node.svc.run_once()
    _, obj = call(url, "/v1/jobs/%s" % obj["job"])
    check("the WORKER refuses it", obj.get("status"), "failed")
    check_true("the reason is the budget, in tokens",
               (obj.get("error") or "").startswith("over budget"), obj.get("error"))
    check("Ollama was never called -- nothing was truncated by anyone",
          fake_ollama.chat_calls(fake), 0)
    check_true("the refusal is in the log", "over budget" in cap.text())
    node.close()
    fake_ollama.stop(fake)

    # =================================================================== [10] health
    print("\n[10] health: honest when Ollama is not there")
    d = os.path.join(TMP, "health")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([], tags=["etk-radio:9b", "etk-radio:4b"],
                                      resident=["etk-radio:4b"])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    code, h = call(url, "/v1/health")
    check("health answers 200", code, 200)
    check("ollama up is reported up", h.get("ollama"), True)
    check("the installed models are listed",
          h.get("models"), ["etk-radio:4b", "etk-radio:9b"])
    check("the resident model is named (spec 12: cold weights)",
          h.get("resident"), ["etk-radio:4b"])
    check_true("the corpus commit is reported",
               bool(re.match(r"\A[0-9a-f]{7,40}\Z", h.get("corpus_commit", ""))))
    check("the forge state is reported", h.get("forge_active"), False)
    check_true("job counts are reported", isinstance(h.get("jobs"), dict))
    fake_ollama.stop(fake)
    code, h = call(url, "/v1/health")
    check("with Ollama down the service still answers", code, 200)
    check("...and says ollama is down rather than pretending", h.get("ollama"), False)
    check("...with no models to claim", h.get("models"), [])
    node.close()

    # ====================================================================== [11] ask
    print("\n[11] /v1/ask: one question, the fast model, ASCII out")
    d = os.path.join(TMP, "ask")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start(
        [script_entry("You were GPU\u2011bound: rsxload sat near zero while the "
                      "fence parked \u2014 see gpu_fault_status.")])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    call(url, "/v1/debrief", body=PACK)          # the pack /v1/ask will read
    t0 = time.time()
    code, ans = call(url, "/v1/ask", body={"pack_epoch": EPOCH,
                                           "question_id": "cpu_or_gpu"})
    took = time.time() - t0
    check("ask answers 200", code, 200)
    check_true("inside the 180 s bound", took < 180, "%.1fs" % took)
    check_true("the answer is ASCII (it lands on an ASCII surface)",
               all(0x20 <= ord(c) <= 0x7E for c in ans.get("answer", "")),
               ans.get("answer", "")[:60])
    check_true("the unicode dash and NBSP were transliterated, not dropped mid-word",
               "GPU-bound" in ans.get("answer", ""), ans.get("answer", "")[:60])
    check("the answer names its model", ans.get("model"), "etk-radio:4b")
    check_true("and its latency", isinstance(ans.get("latency_s"), (int, float)))
    check("the FAST model answered, not the debrief model",
          fake.requests[-1]["body"]["model"], "etk-radio:4b")
    check_true("the question text reached the model",
               "CPU-bound or GPU-bound" in
               "".join(m["content"] for m in fake.requests[-1]["body"]["messages"]))
    code, obj = call(url, "/v1/ask", body={"pack_epoch": 1, "question_id": "next_run"})
    check("a pack the node has never seen -> 404", code, 404)
    code, obj = call(url, "/v1/ask", body={"pack_epoch": EPOCH,
                                           "question_id": "drop tables"})
    check("an unknown question id -> 400, never a free-text passthrough", code, 400)
    code, obj = call(url, "/v1/ask", body={"pack_epoch": EPOCH})
    check("no question at all -> 400", code, 400)
    node.close()
    fake_ollama.stop(fake)

    # ================================================================ [12] retention
    print("\n[12] retention: results and rows age out together")
    d = os.path.join(TMP, "retain")
    os.makedirs(d)
    node = Node(d, retention_days=30)
    old_id = node.svc.jobs.add(1700000000, "NPEA00050")
    old_path = os.path.join(node.cfg["results_dir"], "1700000000.json")
    with open(old_path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "ETK-RADIO-DEBRIEF v1"}, fh)
    node.svc.jobs.update(old_id, "done", result_path=old_path)
    ancient = time.time() - 40 * 86400
    with node.svc.jobs._lock:
        node.svc.jobs.db.execute("UPDATE jobs SET created=? WHERE id=?",
                                 (ancient, old_id))
        node.svc.jobs.db.commit()
    os.utime(old_path, (ancient, ancient))
    fresh_id = node.svc.jobs.add(EPOCH, "NPEA00050")
    with LogCapture() as cap:
        removed = node.svc.jobs.purge(30, node.cfg["results_dir"])
    check_true("something was purged", removed >= 1, "%d item(s)" % removed)
    check("the 40-day-old result file is gone", os.path.exists(old_path), False)
    check("its row is gone too", node.svc.jobs.get(old_id), None)
    check_true("today's job is untouched", node.svc.jobs.get(fresh_id) is not None)
    check_true("the purge is logged", "retention:" in cap.text())
    node.close()

    # =============================================================== [13] rules-only
    print("\n[13] rules-only: a debrief with no model running at all")
    if not have("rules_only"):
        skip("rules_only mode",
             "tools/radio/rules_only.py is not in the tree yet (agent B). The service "
             "fails the job with that exact reason, which is the section below.")
        d = os.path.join(TMP, "norules")
        os.makedirs(d)
        node = Node(d)                     # ollama_url points at a dead port on purpose
        url = node.serve()
        code, obj = call(url, "/v1/debrief", body=dict(PACK, mode="rules_only"))
        check("a rules_only request is still accepted (the knob is out-of-band)",
              code, 202)
        node.svc.run_once()
        _, obj = call(url, "/v1/jobs/%s" % obj["job"])
        check("without the module the job fails, loudly", obj.get("status"), "failed")
        check_true("naming the missing piece",
                   "rules_only unavailable" in (obj.get("error") or ""),
                   obj.get("error"))
        node.close()
    else:
        d = os.path.join(TMP, "rules")
        os.makedirs(d)
        node = Node(d)                     # no Ollama anywhere: that is the point
        url = node.serve()
        code, obj = call(url, "/v1/debrief", body=dict(PACK, mode="rules_only"))
        check("rules_only submit -> 202", code, 202)
        node.svc.run_once()
        _, obj = call(url, "/v1/jobs/%s" % obj["job"])
        check("a rules-only debrief with Ollama unreachable", obj.get("status"), "done")
        deb = obj.get("debrief") or {}
        check("it validates like any other debrief",
              schemas.validate(deb, schemas.load("debrief.v1")), [])
        check_true("it names itself as rules-only, never as a model",
                   "rules" in str(deb.get("model", "")).lower(), deb.get("model"))
        node.close()

    # ================================================================== [14] sender
    print("\n[14] bin/radio_send.sh -- POSIX sender, run under this host's sh")
    src = open(SENDER, encoding="utf-8").read()

    # -- source audit first: the token law and the BusyBox constructs --------
    curl_lines = [ln for ln in src.split("\n")
                  if re.search(r"(?:^|[^-\w])curl\s+-", ln)
                  and not ln.lstrip().startswith("#")]
    check_true("the script really does call curl", len(curl_lines) >= 2)
    check("no curl line carries the token in argv",
          [ln.strip() for ln in curl_lines if "TOKEN" in ln], [])
    check("the token is written to the header file exactly once",
          len(re.findall(r'printf .Authorization: Bearer[^\n]*"\$TOKEN"', src)), 1)
    check_true("the header file lives in /dev/shm", '/dev/shm/radio_hdr' in src)
    check_true("umask 077 comes before the header file is written",
               src.index("umask 077") < src.index("printf 'Authorization"))
    check_true("the header file is removed on every exit path",
               re.search(r"trap '.*rm -f \"\$HDR\".*' EXIT", src) is not None)
    check_true("curl is bounded on both ends",
               all("--connect-timeout 10" in ln and "--max-time 30" in ln
                   for ln in curl_lines))
    banned = {
        "[[ ]]": r"\[\[",
        "local": r"^\s*local\s",
        "echo -e/-n": r"echo\s+-[en]",
        "+=": r"\w\+=",
        "function keyword": r"^\s*function\s+\w+",
        "process substitution": r"<\(",
        "here-string": r"<<<",
        "pipefail": r"set -o pipefail",
        "read -a": r"read\s+-a",
        "sed -i": r"sed -i",
        "stat --format": r"stat --",
        "find -printf": r"find .*-printf",
        "grep -P": r"grep\s+-[a-zA-Z]*P",
        "seq": r"\bseq\b",
        "${var,,}": r"\$\{[A-Za-z_]+,,",
    }
    for label, pattern in banned.items():
        hits = [ln for ln in src.split("\n")
                if re.search(pattern, ln) and not ln.lstrip().startswith("#")]
        check("BusyBox: no %s" % label, hits, [])

    rc = subprocess.run(["sh", "-n", SENDER], capture_output=True, text=True)
    check("sh -n parses the sender", (rc.returncode, rc.stderr.strip()), (0, ""))
    if shutil.which("checkbashisms"):
        rc = subprocess.run(["checkbashisms", "-f", SENDER],
                            capture_output=True, text=True)
        check("checkbashisms is clean", rc.returncode, 0)
    else:
        skip("checkbashisms", "not installed on this host")
    print("    NOTE this host's sh is %s and there is no busybox here, so the runs "
          "below\n         prove LOGIC, not BusyBox portability (see the module "
          "docstring)."
          % (os.path.realpath("/bin/sh")))

    # -- a temp rig -------------------------------------------------------
    rig = os.path.join(TMP, "rig")
    for sub in ("bin", "scripts", "config", "etk_telemetry/radio"):
        os.makedirs(os.path.join(rig, sub), exist_ok=True)
    tel = os.path.join(rig, "etk_telemetry")
    # A POSIX-safe env.sh stub. The REAL env.sh is bash and this is the one thing the
    # host cannot test (see the docstring); what is tested here is that the sender
    # takes its paths from env.sh when env.sh gives them.
    with open(os.path.join(rig, "scripts", "env.sh"), "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n"
                 "export ETK_ROOT=%s\n"
                 "export TELEMETRY_DIR=%s\n"
                 "export PIT_NOTE_FILE=%s/pit_note.txt\n" % (rig, tel, tel))
    toast_log = os.path.join(TMP, "toasts.tsv")
    notify = os.path.join(rig, "bin", "etk_notify.sh")
    with open(notify, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nprintf '%%s\\t%%s\\n' \"$1\" \"${2:-}\" >> %s\n"
                 % toast_log)
    os.chmod(notify, 0o755)
    # A packer stub: the sender must BUILD a pack when none is on the card. It records
    # its own argv so the call shape is pinned, and stamps the epoch it was asked for.
    packer_log = os.path.join(TMP, "packer_argv.txt")
    packer = os.path.join(rig, "bin", "radio_pack.py")
    with open(packer, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "open(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "pack = json.load(open(%r))['pack']\n"
            "pack['epoch'] = int(sys.argv[1])\n"
            "out = sys.argv[sys.argv.index('--out') + 1]\n"
            "json.dump(pack, open(out, 'w'))\n"
            % (packer_log, os.path.join(CASE_DIR, "hallucinated_key.json")))
    os.chmod(packer, 0o755)

    # A curl SHIM on PATH: it records the argv the kernel would show in `ps`, then
    # execs the real curl. This is how "the token is never in argv" is PROVEN rather
    # than read.
    real_curl = shutil.which("curl")
    argv_log = os.path.join(TMP, "curl_argv.txt")
    shim_dir = os.path.join(TMP, "shim")
    os.makedirs(shim_dir, exist_ok=True)
    with open(os.path.join(shim_dir, "curl"), "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %s\nexec %s \"$@\"\n"
                 % (argv_log, real_curl))
    os.chmod(os.path.join(shim_dir, "curl"), 0o755)

    d = os.path.join(TMP, "sender")
    os.makedirs(d)
    fake, furl, _ = fake_ollama.start([script_entry(good_model_output())])
    node = Node(d, ollama_url=furl)
    url = node.serve()
    node.work()
    cred = os.path.join(rig, "config", "radio.json")
    with open(cred, "w", encoding="utf-8") as fh:
        json.dump({"url": url, "token": TOKEN}, fh)
    os.chmod(cred, 0o600)

    env = dict(os.environ)
    env.update({"ETK_ROOT": rig, "RADIO_CRED": cred, "RADIO_POLL_S": "1",
                "RADIO_WAIT_S": "25", "RADIO_INTERACTIVE": "1",
                "PATH": shim_dir + os.pathsep + env["PATH"]})

    def run_sender(*args, **over):
        e = dict(env)
        e.update(over)
        return subprocess.run(["sh", SENDER] + list(args), env=e,
                              capture_output=True, text=True, timeout=120)

    # -- the gates come first: a gated-off rig must do NOTHING ---------------
    r = run_sender("debrief", str(EPOCH), ETK_RADIO="0")
    check("ETK_RADIO=0 exits 0 in silence", (r.returncode, r.stdout, r.stderr),
          (0, "", ""))
    r = run_sender("debrief", str(EPOCH), RADIO_CRED=os.path.join(rig, "nope.json"))
    check("no radio.json exits 0 in silence", (r.returncode, r.stdout, r.stderr),
          (0, "", ""))
    check("neither gate wrote anything to the card",
          os.listdir(os.path.join(tel, "radio")), [])

    # -- the real thing -----------------------------------------------------
    r = run_sender("debrief", str(EPOCH))
    check("debrief exits 0", r.returncode, 0)
    check("the packer was called for a row with no pack on the card",
          os.path.exists(packer_log), True)
    if os.path.exists(packer_log):
        argv = open(packer_log).read().strip().split("\n")[0]
        check_true("the packer was asked for THIS row, with an explicit --out",
                   argv.startswith(str(EPOCH)) and "--out" in argv, argv)
    deb_path = os.path.join(tel, "radio", "%s.debrief.json" % EPOCH)
    check("the debrief landed on the card", os.path.exists(deb_path), True)
    stored = json.load(open(deb_path, encoding="utf-8"))
    check("the stored file is the DEBRIEF, not the job envelope",
          stored.get("schema"), "ETK-RADIO-DEBRIEF v1")
    check("and it validates on the rig side too",
          schemas.validate(stored, schemas.load("debrief.v1")), [])
    note_path = os.path.join(tel, "pit_note.txt")
    check("the pit note was written", os.path.exists(note_path), True)
    note = open(note_path, encoding="utf-8").read()
    check_true("the pit note is ASCII", all(ord(c) < 128 for c in note), repr(note))
    check_true("the pit note is at most two lines",
               len(note.rstrip("\n").split("\n")) <= 2, repr(note))
    check("the pit note is the headline", note.strip(), stored["headline"])
    toasts = open(toast_log, encoding="utf-8").read() if os.path.exists(toast_log) \
        else ""
    check_true("a 'debrief ready' toast went out",
               "RADIO: debrief ready" in toasts, toasts[:120])
    check_true("the toast body is the headline",
               stored["headline"] in toasts, toasts[:200])
    check_true("toast copy is ASCII (mako renders text only)",
               all(ord(c) < 128 for c in toasts))
    check_true("an operator-pressed send says it was sent",
               "RADIO: sent, engineer thinking" in toasts)

    argv_seen = open(argv_log, encoding="utf-8").read() if os.path.exists(argv_log) \
        else ""
    check_true("curl actually ran through the shim", "/v1/" in argv_seen,
               argv_seen[:80])
    check("THE TOKEN NEVER APPEARS IN A curl ARGV (what ps would show)",
          TOKEN in argv_seen, False)
    check_true("the header file is how it travels", "@/dev/shm/radio_hdr" in argv_seen)
    check("no /dev/shm header file was left behind",
          [p for p in os.listdir("/dev/shm") if p.startswith("radio_hdr.")], [])

    log_path = os.path.join(tel, "radio", "radio.log")
    check("the sender keeps its own log", os.path.exists(log_path), True)
    check("the token is not in the sender's log either",
          TOKEN in open(log_path, encoding="utf-8").read(), False)

    # -- the pending path: a job slower than RADIO_WAIT_S --------------------
    slow_epoch = EPOCH + 1
    with fake.lock:
        fake.script[:] = [script_entry(good_model_output(), delay_s=4)]
    r = run_sender("debrief", str(slow_epoch), RADIO_WAIT_S="2")
    check("a slow job exits 1 (parked, not failed)", r.returncode, 1)
    pend = os.path.join(tel, "radio", "pending")
    parked = sorted(os.listdir(pend)) if os.path.isdir(pend) else []
    check_true("the job was parked in pending/", len(parked) == 1, str(parked))
    if parked:
        p = json.load(open(os.path.join(pend, parked[0]), encoding="utf-8"))
        check("the parked file names the job", parked[0], "%s.json" % p["job"])
        check("and the row it belongs to", str(p["epoch"]), str(slow_epoch))
    check("no debrief was invented for a job still running",
          os.path.exists(os.path.join(tel, "radio",
                                      "%s.debrief.json" % slow_epoch)), False)

    done = wait_for(lambda: os.path.exists(
        os.path.join(node.cfg["results_dir"], "%s.json" % slow_epoch)), timeout=30)
    check_true("the node finished the slow job in its own time", bool(done))
    r = run_sender("drain")
    check("drain exits 0", r.returncode, 0)
    check("drain collected the debrief",
          os.path.exists(os.path.join(tel, "radio",
                                      "%s.debrief.json" % slow_epoch)), True)
    check("and cleared the pending file",
          os.listdir(pend) if os.path.isdir(pend) else [], [])
    r = run_sender("drain")
    check("drain on an empty queue is a no-op that still exits 0", r.returncode, 0)

    # -- ask and health -----------------------------------------------------
    with fake.lock:
        fake.script[:] = [script_entry("Rsxload sat at 0 while ppu ran 5.1 - CPU side.")]
    r = run_sender("ask", "cpu_or_gpu")
    check("ask exits 0", r.returncode, 0)
    check_true("the answer is printed for the caller", "CPU side" in r.stdout,
               r.stdout[:80])
    asks = os.path.join(tel, "radio", "asks.log")
    check("the answer is kept in asks.log", os.path.exists(asks), True)
    check_true("asks.log is one tab-separated line per question",
               open(asks, encoding="utf-8").read().count("\t") >= 3)
    r = run_sender("health")
    check("health exits 0", r.returncode, 0)
    check_true("health prints the node's own answer", "etk-radio" in r.stdout,
               r.stdout[:80])
    check("the token never reached a curl argv in ANY command",
          TOKEN in open(argv_log, encoding="utf-8").read(), False)

    # -- failure is a log line, not a toast, unless the operator pressed it ---
    before = open(toast_log, encoding="utf-8").read()
    dead = os.path.join(rig, "config", "radio_dead.json")
    with open(dead, "w", encoding="utf-8") as fh:
        json.dump({"url": "http://127.0.0.1:1", "token": TOKEN}, fh)
    r = run_sender("debrief", str(EPOCH + 2), RADIO_CRED=dead, RADIO_INTERACTIVE="0")
    check_true("an unreachable node is a non-zero exit, not a crash",
               r.returncode != 0, str(r.returncode))
    check("an AUTOMATIC send that fails sends NO toast (spec 7.5)",
          open(toast_log, encoding="utf-8").read(), before)
    check_true("but it is in the log",
               "submit failed" in open(log_path, encoding="utf-8").read())
    r = run_sender("debrief", str(EPOCH + 3), RADIO_CRED=dead, RADIO_INTERACTIVE="1")
    check_true("an OPERATOR-PRESSED send that fails says 'no signal'",
               "RADIO: no signal" in open(toast_log, encoding="utf-8").read())
    r = run_sender("wibble")
    check("an unknown verb is a usage error", r.returncode, 64)

    node.close()
    fake_ollama.stop(fake)

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if SKIPS:
    print("SKIPPED: %d section(s) -> %s" % (len(SKIPS), SKIPS))
if FAILS:
    print("FAILED: %d check(s) -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL RADIO SERVICE + SENDER CHECKS PASSED")
