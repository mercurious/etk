#!/usr/bin/env python3
"""ETK RADIO -- `etk-radio.service`, the node-side HTTP service (spec 9).

The rig POSTs a PACK v1 bundle and gets a job id back; one worker thread builds the
briefing, calls Ollama on 127.0.0.1 with the DEBRIEF v1 schema as `format:`, computes
the tags, runs the guards, validates, and stores the result. The rig polls with short
requests (spec 5: a held connection over handheld WiFi through a five-minute inference
is the wrong shape). Nothing here reaches the rig; the rig only ever POSTs and GETs.

Routes (bearer on EVERY one; an unauthenticated request gets a bare 401):

    POST /v1/debrief     PACK v1 -> 202 {"job": id, "status": "queued"}
    GET  /v1/jobs/<id>   -> {"status": queued|deferred|running|done|failed, ...}
    POST /v1/ask         {"pack_epoch", "question_id"|"question"} -> a short answer,
                         synchronous on the fast model, <= 180 s
    GET  /v1/health      models, corpus commit, ollama reachable, forge state, job counts

Laws this file is accountable for:

  * NEVER TRUNCATE SILENTLY (spec 6, 10.1) -- the prompt is counted BEFORE the call and
    a job that would not fit is failed `over budget`. Ollama's own truncation is never
    allowed to decide what the model saw. Three of four pre-prototype reviews were cut
    at 4,098 tokens and the models judged fragments as whole files.
  * DATA, NEVER COMMANDS (spec 6, 12) -- the model's answer is JSON, validated against
    the schema, stored as bytes. NO SHELL IS EVER SPAWNED FROM A REQUEST. The only
    subprocesses this file starts are `git` in the WORKER (pull + rev-parse), never in a
    handler: /v1/health reads the corpus commit the worker cached.
  * THE FORGE OUTRANKS THE RADIO (spec 1.8) -- a job waits while any
    `~/forge-runs/active_*` marker is live.
  * THE TOKEN IS NEVER LOGGED. Nothing from a pack is logged beyond epoch and game_id.

Degradation (spec 9 is a Phase-0 spec and its neighbours land in waves): briefing.py,
tags.py, guards.py and rules_only.py are imported LAZILY, inside the worker, and a
missing one degrades with a logged line rather than a crash --

    briefing.py  -> a built-in minimal briefing (pack JSON + the pack's own crash text),
                    tokens estimated at 4 chars/token. Honest, small, testable; it exists
                    so the never-truncate guard has something to count before C's real
                    briefing lands, not as a second implementation of it.
    tags.py      -> tools/radio/eval.py's compute_tags(), which is the same spec 3.2 rule
                    the eval already pins.
    guards.py    -> NOTHING is dropped, and the debrief is stamped
                    guards.passed = false with a `guards unavailable` entry, so a
                    guard-less answer is visibly guard-less rather than quietly trusted.
    rules_only.py-> a rules-only request fails the job with that reason.

Usage:
    service.py --config /etc/etk-radio/config.json          # the unit's ExecStart
    service.py --config PATH --once                         # drain the queue and exit
    service.py --selftest                                   # config + schemas, no network

Config is JSON; $RADIO_CONFIG overrides the default /etc/etk-radio/config.json.
Python 3.12 stdlib only. All logging to stderr, which is journald under the unit.
"""
import argparse
import glob
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_CONFIG = os.environ.get("RADIO_CONFIG", "/etc/etk-radio/config.json")

# Every key the service understands, with the spec 9 default. An unknown key in the
# config file is a FAILURE, not a shrug: a typo'd `retention_days` that silently kept
# the default would delete nothing and nobody would find out for thirty days.
DEFAULTS = {
    "bind": "127.0.0.1",
    "port": 8737,
    "token_file": "/etc/etk-radio/token",
    "ollama_url": "http://127.0.0.1:11434",
    "model_debrief": "etk-radio:9b",
    "model_fast": "etk-radio:4b",
    "jobs_db": "~/etk-radio/jobs.db",
    "results_dir": "~/etk-radio/results",
    "packs_dir": "~/etk-radio/packs",       # the submitted pack, kept for /v1/ask
    "corpus_root": "~/etk",
    "forge_runs_dir": "~/forge-runs",
    "retention_days": 30,
    "keep_alive": "30m",
    "max_body": 65536,
    "think": False,
    # --- knobs the spec implies but does not name; defaults are the spec's numbers ---
    "num_ctx": 8192,                # Modelfile value; the budget is computed from it
    "num_predict": 700,             # spec 4.1 / 10.2: generation is the other half
    "temperature": 0.2,
    "budget_slack": 64,             # tokens kept back so nothing rides the edge
    "chat_timeout_s": 900,          # a cold 9b load + a long prefill (spec 5)
    "ask_timeout_s": 180,           # spec 9: /v1/ask is synchronous and bounded
    "forge_poll_s": 30,             # spec 9: poll the forge lock every 30 s
    "git_pull": True,               # off in tests: the corpus is the checkout as-is
    "git_timeout_s": 20,
    "rules_only": False,            # service-wide switch; a request may also ask
}
PATH_KEYS = ("token_file", "jobs_db", "results_dir", "packs_dir", "corpus_root",
             "forge_runs_dir")

# The provenance half of DEBRIEF v1: the SERVICE stamps these, the model never sees
# them. Handing the model the full schema as `format:` would force constrained decoding
# to invent a 64-hex prompt_sha256 and a corpus commit -- the eval fixture's
# `model_output` (tools/radio/eval_cases/hallucinated_key.json) is exactly this reduced
# shape, which is the evidence for the reading.
SERVICE_FIELDS = ("model", "prompt_sha256", "corpus_commit", "tokens", "latency_s")
MODEL_REQUIRED = ["schema", "epoch", "game_id", "radio", "headline", "tags",
                  "findings", "recommendations"]

# spec 7.3 RADIO CHECK: a pad-friendly menu, so the couch needs no keyboard.
QUESTIONS = {
    "why_crash": "Why did this session crash or wedge? Name the mechanism and the "
                 "evidence line it comes from.",
    "arm_ahead": "Is the second arm ahead of the first yet? Say yes only if both arms "
                 "carry N >= 3 in the pack's dyno table.",
    "next_run": "What should I run next on this rig, and what would it settle?",
    "explain_row": "Explain this ledger row in plain language: what happened, and what "
                   "in the pack says so.",
    "cpu_or_gpu": "Am I CPU-bound or GPU-bound in this session? Name the numbers that "
                  "decide it.",
}

_ASCII_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-", "\u2026": "...", "\u00b7": "-",
    "\u2022": "*", "\u00a0": " ", "\u2007": " ", "\u202f": " ",
    "\u2192": "->", "\u00b0": " deg", "\u2265": ">=", "\u2264": "<=",
    "\u00d7": "x",
}


# --------------------------------------------------------------------------- helpers
def log(msg):
    """One line to stderr = one line in journald. Never a token, never pack content."""
    sys.stderr.write("%s radio: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    sys.stderr.flush()


def to_ascii(text, limit=None):
    """spec 6 ASCII surfaces: the toast and pit_note.txt are ASCII or they are broken."""
    out = []
    for ch in str(text):
        if ch in _ASCII_MAP:
            out.append(_ASCII_MAP[ch])
        elif ch == "\n" or ch == "\t":
            out.append(" ")
        elif 0x20 <= ord(ch) <= 0x7E:
            out.append(ch)
        # anything else is dropped: a mojibake glyph in a toast is a broken surface
    s = "".join(out)
    s = re.sub(r"[ ]{2,}", " ", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit]
        if " " in cut[limit // 2:]:
            cut = cut[:cut.rstrip().rfind(" ")]      # truncate at a word boundary
        s = cut.rstrip()
    return s


def est_tokens(text):
    """A 4-chars-per-token estimate. Only used by the fallback briefing; the real
    briefing (tools/radio/briefing.py) reports its own count."""
    return max(1, (len(text) + 3) // 4)


def load_config(path=None):
    """Read the JSON config, fill the spec defaults, expand ~ on every path key."""
    path = path or DEFAULT_CONFIG
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
        if not isinstance(user, dict):
            raise ValueError("config is not a JSON object: %s" % path)
        # A key starting with "_" is a COMMENT: JSON has none, and a config a human
        # has to edit at 2 a.m. needs somewhere to say why. Everything else must be
        # a key the service knows.
        user = {k: v for k, v in user.items() if not k.startswith("_")}
        unknown = sorted(k for k in user if k not in DEFAULTS)
        if unknown:
            raise ValueError("unknown config key(s) in %s: %s"
                             % (path, ", ".join(unknown)))
        cfg.update(user)
    elif path and path != DEFAULT_CONFIG:
        raise ValueError("config not found: %s" % path)
    for k in PATH_KEYS:
        cfg[k] = os.path.expanduser(str(cfg[k]))
    cfg["_path"] = path
    return cfg


def _json_request(url, payload=None, timeout=30, method=None):
    """A bounded loopback JSON call. urllib, no shell, no redirects followed by design
    (Ollama is on 127.0.0.1 and answers directly)."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def model_format(schema):
    """The MODEL VIEW of DEBRIEF v1 (see SERVICE_FIELDS): the provenance properties are
    removed outright, so `additionalProperties: false` stops the model emitting them at
    all, and `required` is the analyst half. The finished object is validated against
    the FULL schema after the service stamps its own fields back on."""
    view = json.loads(json.dumps(schema))       # deep copy; the cached schema is shared
    props = view.get("properties", {})
    for key in SERVICE_FIELDS:
        props.pop(key, None)
    view["required"] = [k for k in MODEL_REQUIRED if k in props]
    view.pop("$schema", None)
    view.pop("$id", None)
    return view


# ------------------------------------------------------------------------------ jobs
class Jobs:
    """The job table. sqlite because a job has to survive a service restart -- the rig
    is holding a job id and will come back for it (spec 5)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS jobs (
        id          TEXT PRIMARY KEY,
        epoch       INTEGER,
        game_id     TEXT,
        status      TEXT NOT NULL,
        created     REAL NOT NULL,
        updated     REAL NOT NULL,
        error       TEXT,
        result_path TEXT,
        mode        TEXT NOT NULL DEFAULT 'model'
    );
    CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
    """

    def __init__(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(self.SCHEMA)
            self.db.commit()

    def add(self, epoch, game_id, mode="model"):
        jid = os.urandom(8).hex()               # not guessable, and filename-safe:
        now = time.time()                       # the rig writes pending/<job>.json
        with self._lock:
            self.db.execute(
                "INSERT INTO jobs (id, epoch, game_id, status, created, updated, mode)"
                " VALUES (?,?,?,'queued',?,?,?)", (jid, epoch, game_id, now, now, mode))
            self.db.commit()
        return jid

    def get(self, jid):
        with self._lock:
            cur = self.db.execute("SELECT * FROM jobs WHERE id = ?", (jid,))
            row = cur.fetchone()
        return dict(row) if row else None

    def update(self, jid, status, error=None, result_path=None):
        with self._lock:
            self.db.execute(
                "UPDATE jobs SET status=?, updated=?, error=COALESCE(?, error),"
                " result_path=COALESCE(?, result_path) WHERE id=?",
                (status, time.time(), error, result_path, jid))
            self.db.commit()

    def next_runnable(self):
        """queued first, then anything the forge deferred -- a deferred job is not a
        dead job, it is a job the forge outranked."""
        with self._lock:
            cur = self.db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','deferred')"
                " ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, created LIMIT 1")
            row = cur.fetchone()
        return dict(row) if row else None

    def reclaim_running(self):
        """A service restart mid-inference leaves a `running` row nobody owns."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE jobs SET status='queued', updated=? WHERE status='running'",
                (time.time(),))
            self.db.commit()
        return cur.rowcount

    def counts(self):
        with self._lock:
            cur = self.db.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")
            return {r["status"]: r["c"] for r in cur.fetchall()}

    def purge(self, days, results_dir):
        """spec 9: results retained 30 days. The row and its result go together."""
        cutoff = time.time() - days * 86400
        removed = 0
        with self._lock:
            cur = self.db.execute("SELECT id, result_path FROM jobs WHERE created < ?",
                                  (cutoff,))
            rows = cur.fetchall()
            for r in rows:
                p = r["result_path"]
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError as exc:
                        log("retention: cannot remove %s (%s)" % (os.path.basename(p),
                                                                  type(exc).__name__))
            self.db.execute("DELETE FROM jobs WHERE created < ?", (cutoff,))
            self.db.commit()
            removed = len(rows)
        # An orphan result (its row already gone) still ages out on file mtime.
        for p in glob.glob(os.path.join(results_dir, "*.json")):
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
        if removed:
            log("retention: dropped %d item(s) older than %d day(s)" % (removed, days))
        return removed


# --------------------------------------------------------------------------- service
class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        for key in ("results_dir", "packs_dir"):
            os.makedirs(cfg[key], exist_ok=True)
        self.jobs = Jobs(cfg["jobs_db"])
        n = self.jobs.reclaim_running()
        if n:
            log("startup: requeued %d job(s) left running by a previous process" % n)
        self._tok = (None, None)                 # (mtime, value)
        self._degraded = set()                   # log each degradation exactly once
        self.corpus_commit = self._git_commit()
        self.stop = threading.Event()
        self.jobs.purge(cfg["retention_days"], cfg["results_dir"])
        self._last_purge = time.time()

    # -- credentials ------------------------------------------------------------
    def token(self):
        """The bearer, re-read when the file changes so a re-mint needs no restart.
        The VALUE never reaches a log line, here or anywhere."""
        path = self.cfg["token_file"]
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if "token" not in self._degraded:
                self._degraded.add("token")
                log("auth: token file is not readable at %s -- every route will 401"
                    % path)
            return None
        if self._tok[0] != mtime:
            try:
                with open(path, encoding="utf-8") as fh:
                    self._tok = (mtime, fh.read().strip())
            except OSError:
                return None
        return self._tok[1] or None

    def check_bearer(self, header):
        tok = self.token()
        if not tok or not header:
            return False
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return hmac.compare_digest(parts[1].strip(), tok)

    # -- lazy neighbours --------------------------------------------------------
    def _mod(self, name):
        """Import a wave-mate lazily. A missing module degrades with ONE logged line."""
        try:
            return __import__(name)
        except Exception as exc:                                   # noqa: BLE001
            if name not in self._degraded:
                self._degraded.add(name)
                log("degraded: %s unavailable (%s: %s)"
                    % (name, type(exc).__name__, exc))
            return None

    # -- corpus -----------------------------------------------------------------
    def _git(self, args, timeout):
        """git, in the WORKER only, argv form, never a shell. Returns stdout or None."""
        try:
            r = subprocess.run(["git", "-C", self.cfg["corpus_root"]] + args,
                               capture_output=True, text=True, timeout=timeout)
        except Exception as exc:                                   # noqa: BLE001
            log("corpus: git %s failed (%s)" % (args[0], type(exc).__name__))
            return None
        if r.returncode != 0:
            log("corpus: git %s returned %d (%s)"
                % (args[0], r.returncode, (r.stderr or "").strip().split("\n")[0][:120]))
            return None
        return r.stdout.strip()

    def _git_commit(self):
        out = self._git(["rev-parse", "--short", "HEAD"], 10)
        if out and re.match(r"\A[0-9a-f]{7,40}\Z", out):
            return out
        # DEBRIEF v1 wants 7-40 hex; an unknown corpus says so in a shape the schema
        # accepts rather than failing every job on a node that is not a git checkout.
        return "0000000"

    def refresh_corpus(self):
        if self.cfg["git_pull"]:
            self._git(["pull", "--ff-only"], self.cfg["git_timeout_s"])
        self.corpus_commit = self._git_commit()
        return self.corpus_commit

    # -- forge lock -------------------------------------------------------------
    def forge_active(self):
        """spec 1.8: the forge outranks the radio. forge.sh writes one marker per lane."""
        return bool(glob.glob(os.path.join(self.cfg["forge_runs_dir"], "active_*")))

    # -- ollama -----------------------------------------------------------------
    def ollama(self, path, payload=None, timeout=30, method=None):
        return _json_request(self.cfg["ollama_url"].rstrip("/") + path, payload,
                             timeout=timeout, method=method)

    def ollama_up(self):
        try:
            self.ollama("/api/tags", timeout=5)
            return True
        except Exception:                                          # noqa: BLE001
            return False

    def _chat(self, model, messages, fmt=None, num_predict=None, timeout=None,
              num_ctx=None):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.cfg["keep_alive"],
            "think": bool(self.cfg["think"]),
            "options": {
                # The context the BUDGET was computed against is the context the model
                # gets. briefing.build raises num_ctx for a long pack; sending 8192
                # anyway is exactly the silent truncation spec 6 forbids.
                "num_ctx": int(num_ctx or self.cfg["num_ctx"]),
                "temperature": self.cfg["temperature"],
                "num_predict": num_predict if num_predict is not None
                else self.cfg["num_predict"],
            },
        }
        if fmt is not None:
            payload["format"] = fmt
        t0 = time.time()
        out = self.ollama("/api/chat", payload,
                          timeout=timeout or self.cfg["chat_timeout_s"])
        out["_latency_s"] = round(time.time() - t0, 1)
        return out

    # -- briefing ---------------------------------------------------------------
    def briefing(self, pack, run_sheet=None):
        """C's deterministic briefing when it is there; a bounded built-in when it is
        not. Either way the answer carries `tokens` and `num_ctx`, because the caller's
        next act is to REFUSE the job if it will not fit."""
        mod = self._mod("briefing")
        if mod is not None and hasattr(mod, "build"):
            try:
                b = mod.build(pack, repo_root=self.cfg["corpus_root"],
                              run_sheet=run_sheet)
                b.setdefault("num_ctx", self.cfg["num_ctx"])
                return b
            except Exception as exc:                               # noqa: BLE001
                log("degraded: briefing.build raised (%s: %s) -- using the built-in"
                    % (type(exc).__name__, exc))
        return self._fallback_briefing(pack)

    def _fallback_briefing(self, pack):
        """Minimal, bounded, honest. It is NOT spec 4.2 retrieval: no manual, no field
        help, no signature explanations -- exactly the pack, plus the output contract in
        one line. A debrief built on it says less; it never says more than it was told."""
        system = (
            "You are the ETK race engineer on the pit radio. Short, calm, useful.\n"
            "Every claim names the pack field it came from. N >= 3 before any "
            "comparison verdict. Do not invent numbers; the pack carries the computed "
            "ones. Answer as one JSON object in the given schema and nothing else."
        )
        body = json.dumps(pack, indent=1, sort_keys=True)
        user = "SESSION PACK (ETK-RADIO-PACK v1):\n" + body
        sha = hashlib.sha256(system.encode("utf-8")).hexdigest()
        return {
            "system": system,
            "briefing": "",
            "user": user,
            "sources": ["pack"],
            "tokens": {"system": est_tokens(system), "briefing": 0,
                       "pack": est_tokens(body),
                       "total": est_tokens(system) + est_tokens(user)},
            "prompt_sha256": sha,
            "corpus_commit": self.corpus_commit,
            "num_ctx": self.cfg["num_ctx"],
        }

    # -- tags / guards ----------------------------------------------------------
    def tags_for(self, pack):
        mod = self._mod("tags")
        if mod is not None and hasattr(mod, "compute"):
            try:
                return list(mod.compute(pack))
            except Exception as exc:                               # noqa: BLE001
                log("degraded: tags.compute raised (%s)" % type(exc).__name__)
        ev = self._mod("eval")          # the eval already pins the spec 3.2 rule
        if ev is not None and hasattr(ev, "compute_tags"):
            try:
                return list(ev.compute_tags(pack))
            except Exception as exc:                               # noqa: BLE001
                log("degraded: eval.compute_tags raised (%s)" % type(exc).__name__)
        return []

    def guard(self, debrief, pack):
        mod = self._mod("guards")
        if mod is not None and hasattr(mod, "apply"):
            # falsified= and fields= are LOADED entries, not paths: left as None,
            # guards.py reads config/falsified.json and config/pitstop_fields.json
            # from its own repo root, which on the node IS corpus_root.
            try:
                return mod.apply(debrief, pack)
            except Exception as exc:                               # noqa: BLE001
                log("degraded: guards.apply raised (%s: %s)"
                    % (type(exc).__name__, exc))
        # An unguarded debrief must LOOK unguarded. passed=false is what the rig and
        # the RADIO tab read; nothing here is silently blessed.
        debrief["guards"] = {
            "passed": False,
            "dropped": [{"kind": "guards",
                         "reason": "guards unavailable on this node; nothing was "
                                   "checked and nothing may be staged"}],
        }
        return debrief

    # -- the worker -------------------------------------------------------------
    def pack_path(self, epoch):
        return os.path.join(self.cfg["packs_dir"], "%s.json" % epoch)

    def result_path(self, epoch):
        return os.path.join(self.cfg["results_dir"], "%s.json" % epoch)

    def store_pack(self, pack):
        self._atomic(self.pack_path(pack.get("epoch")), pack)

    @staticmethod
    def _atomic(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1)
        os.replace(tmp, path)
        return path

    def run_once(self):
        """Process every runnable job, then return. `--once` and the tests use this;
        the forge lock DEFERS rather than blocks here, so a locked node still returns."""
        done = 0
        seen = set()
        while True:
            job = self.jobs.next_runnable()
            if not job or job["id"] in seen:
                break
            seen.add(job["id"])
            if self.forge_active():
                self.jobs.update(job["id"], "deferred")
                log("job %s deferred: forge active" % job["id"])
                continue
            self.run_job(job)
            done += 1
        return done

    def worker_loop(self):
        log("worker: started")
        while not self.stop.is_set():
            job = self.jobs.next_runnable()
            if job is None:
                self._maybe_purge()
                self.stop.wait(2.0)
                continue
            if self.forge_active():
                if job["status"] != "deferred":
                    self.jobs.update(job["id"], "deferred")
                    log("job %s deferred: forge active" % job["id"])
                self.stop.wait(self.cfg["forge_poll_s"])
                continue
            self.run_job(job)
        log("worker: stopped")

    def _maybe_purge(self):
        if time.time() - self._last_purge >= 86400:
            self._last_purge = time.time()
            self.jobs.purge(self.cfg["retention_days"], self.cfg["results_dir"])

    def run_job(self, job):
        jid = job["id"]
        self.jobs.update(jid, "running")
        try:
            pack = json.load(open(self.pack_path(job["epoch"]), encoding="utf-8"))
        except Exception as exc:                                   # noqa: BLE001
            return self._fail(jid, "pack missing: %s" % type(exc).__name__)
        mode = job["mode"] if "mode" in job.keys() else "model"
        log("job %s: start epoch=%s game=%s mode=%s"
            % (jid, job["epoch"], job["game_id"], mode))
        try:
            if mode == "rules_only" or self.cfg["rules_only"]:
                debrief = self._rules_only(pack)
            else:
                debrief = self._model_debrief(jid, pack)
        except _JobFailed as exc:
            return self._fail(jid, str(exc))
        except Exception as exc:                                   # noqa: BLE001
            return self._fail(jid, "%s: %s" % (type(exc).__name__, exc))
        if debrief is None:
            return self._fail(jid, "no debrief produced")

        errs = self._validate(debrief)
        if errs:
            return self._fail(jid, "debrief invalid after guards: " + "; ".join(errs[:4]))
        path = self._atomic(self.result_path(pack.get("epoch")), debrief)
        self.jobs.update(jid, "done", result_path=path)
        log("job %s: done epoch=%s guards_passed=%s latency=%ss"
            % (jid, pack.get("epoch"), debrief["guards"]["passed"],
               debrief.get("latency_s")))
        return True

    def _fail(self, jid, why):
        self.jobs.update(jid, "failed", error=why[:400])
        log("job %s: failed -- %s" % (jid, why[:200]))
        return False

    def _validate(self, debrief):
        import schemas
        return schemas.validate(debrief, schemas.load("debrief.v1"))

    def _rules_only(self, pack):
        mod = self._mod("rules_only")
        if mod is None or not hasattr(mod, "build"):
            raise _JobFailed("rules_only unavailable on this node")
        d = mod.build(pack, corpus_commit=self.corpus_commit)
        d.setdefault("model", "rules-only")
        d.setdefault("latency_s", 0)
        d.setdefault("tokens", {"prompt": 0, "completion": 0})
        d.setdefault("corpus_commit", self.corpus_commit)
        d.setdefault("prompt_sha256", "0" * 64)
        d.setdefault("run_sheet", None)
        d["tags"] = self._merge_tags(self.tags_for(pack), d.get("tags"))
        return self.guard(d, pack)

    def _model_debrief(self, jid, pack):
        import schemas
        self.refresh_corpus()
        brief = self.briefing(pack)
        num_ctx = int(brief.get("num_ctx") or self.cfg["num_ctx"])
        budget = num_ctx - self.cfg["num_predict"] - self.cfg["budget_slack"]
        total = int((brief.get("tokens") or {}).get("total") or 0)

        # NEVER TRUNCATE SILENTLY (spec 6). The refusal happens BEFORE any call, so a
        # pack that would not fit costs no prefill and, more importantly, is never
        # judged as if it were whole.
        if total > budget:
            raise _JobFailed("over budget: prompt %d tokens > %d (num_ctx %d - "
                             "num_predict %d - %d)"
                             % (total, budget, num_ctx, self.cfg["num_predict"],
                                self.cfg["budget_slack"]))

        system = brief["system"]
        user = brief["user"] if not brief.get("briefing") else \
            brief["briefing"] + "\n\n" + brief["user"]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        fmt = model_format(schemas.load("debrief.v1"))

        out = self._chat(self.cfg["model_debrief"], messages, fmt=fmt, num_ctx=num_ctx)
        raw = ((out.get("message") or {}).get("content") or "").strip()
        obj = _loads(raw)
        if obj is None:
            log("job %s: model answer was not JSON -- one corrective retry" % jid)
            messages = messages + [
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content":
                    "That was not valid JSON. Answer again with ONE JSON object in the "
                    "given schema, no prose, no code fence, no commentary."},
            ]
            out = self._chat(self.cfg["model_debrief"], messages, fmt=fmt,
                             num_ctx=num_ctx)
            raw = ((out.get("message") or {}).get("content") or "").strip()
            obj = _loads(raw)
            if obj is None:
                raise _JobFailed("model answer was not JSON after one retry")

        debrief = obj if isinstance(obj, dict) else {}
        debrief["schema"] = "ETK-RADIO-DEBRIEF v1"
        debrief["epoch"] = int(pack.get("epoch"))
        debrief["game_id"] = str(pack.get("game_id") or "UNKNOWN")
        debrief["model"] = out.get("model") or self.cfg["model_debrief"]
        debrief["prompt_sha256"] = brief.get("prompt_sha256") or ("0" * 64)
        debrief["corpus_commit"] = brief.get("corpus_commit") or self.corpus_commit
        debrief["tokens"] = {"prompt": int(out.get("prompt_eval_count") or 0),
                             "completion": int(out.get("eval_count") or 0)}
        debrief["latency_s"] = out.get("_latency_s", 0)
        debrief.setdefault("run_sheet", None)
        debrief.setdefault("findings", [])
        debrief.setdefault("recommendations", [])
        debrief["radio"] = to_ascii(debrief.get("radio", ""), 280)
        debrief["headline"] = to_ascii(debrief.get("headline", ""), 60)
        debrief["tags"] = self._merge_tags(self.tags_for(pack), debrief.get("tags"))
        return self.guard(debrief, pack)

    @staticmethod
    def _merge_tags(computed, from_model):
        """spec 3.2: computed on the node, merged with the model's. The computed ones
        come FIRST and the tests assert on them; the model's are additive, never a
        substitute (a model cannot un-tag its own bake row)."""
        out = list(computed or [])
        for t in (from_model or []):
            t = str(t).strip().lower()
            if re.match(r"\A[a-z0-9_]{1,32}\Z", t) and t not in out:
                out.append(t)
        return out[:12]

    # -- ask --------------------------------------------------------------------
    def ask(self, epoch, question_id=None, question=None):
        path = self.pack_path(epoch)
        if not os.path.exists(path):
            return None, "unknown pack epoch"
        with open(path, encoding="utf-8") as fh:
            pack = json.load(fh)
        if question_id:
            q = QUESTIONS.get(str(question_id))
            if not q:
                return None, "unknown question_id"
        else:
            q = to_ascii(question or "", 500)
            if not q:
                return None, "empty question"
        brief = self.briefing(pack)
        messages = [
            {"role": "system", "content": brief["system"]},
            {"role": "user", "content":
                (brief.get("briefing") or "") + "\n\n" + brief["user"] +
                "\n\nQUESTION: " + q +
                "\n\nAnswer in plain ASCII prose, at most four sentences. Name the pack "
                "field behind each number. If the pack does not say, say it does not."},
        ]
        out = self._chat(self.cfg["model_fast"], messages, num_predict=300,
                         timeout=self.cfg["ask_timeout_s"],
                         num_ctx=brief.get("num_ctx"))
        answer = to_ascii((out.get("message") or {}).get("content") or "", 1200)
        return {"answer": answer,
                "model": out.get("model") or self.cfg["model_fast"],
                "latency_s": out.get("_latency_s", 0)}, None

    # -- health -----------------------------------------------------------------
    def health(self):
        models, resident, up = [], [], False
        try:
            tags = self.ollama("/api/tags", timeout=5)
            models = sorted(m.get("name", "") for m in (tags.get("models") or []))
            up = True
        except Exception:                                          # noqa: BLE001
            pass
        if up:
            try:
                ps = self.ollama("/api/ps", timeout=5)
                resident = sorted(m.get("name", "") for m in (ps.get("models") or []))
            except Exception:                                      # noqa: BLE001
                pass
        return {
            "service": "etk-radio",
            "models": models,
            "model_debrief": self.cfg["model_debrief"],
            "model_fast": self.cfg["model_fast"],
            "corpus_commit": self.corpus_commit,
            "ollama": up,
            "forge_active": self.forge_active(),
            "resident": resident,
            "jobs": self.jobs.counts(),
            "rules_only": bool(self.cfg["rules_only"]),
        }


class _JobFailed(Exception):
    """A job that failed for a REASON the rig should read, not a crash."""


def _loads(raw):
    """Parse the model's answer. A fenced block is the one dressing we undo, because
    it is a formatting slip rather than a different answer; anything else is a parse
    failure and buys exactly one corrective retry."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"\A```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*\Z", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


# ------------------------------------------------------------------------- HTTP face
class Handler(BaseHTTPRequestHandler):
    server_version = "etk-radio/1"
    # HTTP/1.0 on purpose: every exchange is short and self-closing (spec 12 "no held
    # connections"), and no keep-alive means no half-read body can poison the next one.
    protocol_version = "HTTP/1.0"

    # -- plumbing ---------------------------------------------------------------
    @property
    def svc(self):
        return self.server.svc

    def log_message(self, fmt, *args):
        # The default logs the full request line to stderr. Paths here carry a job id,
        # never a token, but route it through our logger so journald has one shape.
        log("http %s %s" % (self.address_string(), fmt % args))

    def _send(self, code, obj=None, extra=None):
        body = b"" if obj is None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _deny(self):
        """401 with NO body detail: an unauthenticated caller learns nothing about the
        route, the token, or whether the node has anything on it."""
        self._send(401, None, {"WWW-Authenticate": "Bearer"})

    def _auth(self):
        if self.svc.check_bearer(self.headers.get("Authorization")):
            return True
        log("401 %s %s" % (self.command, self.path.split("?")[0]))
        self._deny()
        return False

    def _read_body(self):
        """-> (obj, error_code). Bodies are capped at max_body BEFORE they are read."""
        cap = self.svc.cfg["max_body"]
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return None, 411
        try:
            n = int(raw_len)
        except ValueError:
            return None, 400
        if n > cap:
            log("413 body %d B > cap %d B" % (n, cap))
            return None, 413
        data = self.rfile.read(n)
        if len(data) > cap:
            return None, 413
        try:
            return json.loads(data.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError):
            return None, 400

    # -- routes -----------------------------------------------------------------
    def do_GET(self):                                              # noqa: N802
        if not self._auth():
            return
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/health":
            return self._send(200, self.svc.health())
        m = re.match(r"\A/v1/jobs/([0-9a-f]{4,64})\Z", path)
        if m:
            return self._job(m.group(1))
        self._send(404, {"error": "no such route"})

    def do_POST(self):                                             # noqa: N802
        if not self._auth():
            return
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/debrief":
            return self._debrief()
        if path == "/v1/ask":
            return self._ask()
        self._send(404, {"error": "no such route"})

    def _job(self, jid):
        job = self.svc.jobs.get(jid)
        if not job:
            return self._send(404, {"error": "no such job"})
        out = {"job": jid, "status": job["status"], "epoch": job["epoch"],
               "game_id": job["game_id"]}
        if job["status"] == "done" and job["result_path"]:
            try:
                with open(job["result_path"], encoding="utf-8") as fh:
                    out["debrief"] = json.load(fh)
            except Exception as exc:                               # noqa: BLE001
                out["status"] = "failed"
                out["error"] = "result unreadable: %s" % type(exc).__name__
        elif job["status"] == "failed":
            out["error"] = job["error"] or "failed"
        return self._send(200, out)

    def _debrief(self):
        import schemas
        obj, err = self._read_body()
        if err:
            return self._send(err, {"error": {400: "body is not JSON",
                                              411: "Content-Length required",
                                              413: "pack over 64 KB"}[err]})
        # `mode` is an out-of-band request knob, not part of PACK v1 -- whose root is
        # additionalProperties:false -- so it comes off BEFORE the contract is checked.
        mode = "model"
        pack = obj
        if isinstance(obj, dict):
            mode = "rules_only" if str(obj.get("mode") or "") == "rules_only" else "model"
            pack = {k: v for k, v in obj.items() if k != "mode"}
        errs = schemas.validate(pack, schemas.load("pack.v1"))
        if errs:
            log("422 pack rejected: %s" % errs[0])
            return self._send(422, {"error": "pack.v1", "errors": errs[:8]})
        self.svc.store_pack(pack)
        jid = self.svc.jobs.add(int(pack["epoch"]), str(pack.get("game_id") or ""), mode)
        log("202 job %s queued epoch=%s game=%s mode=%s"
            % (jid, pack.get("epoch"), pack.get("game_id"), mode))
        return self._send(202, {"job": jid, "status": "queued"})

    def _ask(self):
        obj, err = self._read_body()
        if err:
            return self._send(err, {"error": "bad request"})
        if not isinstance(obj, dict):
            return self._send(400, {"error": "body is not an object"})
        epoch = obj.get("pack_epoch")
        if epoch is None:
            return self._send(400, {"error": "pack_epoch required"})
        try:
            ans, why = self.svc.ask(epoch, obj.get("question_id"), obj.get("question"))
        except Exception as exc:                                   # noqa: BLE001
            log("ask: %s: %s" % (type(exc).__name__, exc))
            return self._send(503, {"error": "engineer unreachable"})
        if why:
            return self._send(404 if "unknown pack" in why else 400, {"error": why})
        return self._send(200, ans)


def make_server(svc):
    httpd = ThreadingHTTPServer((svc.cfg["bind"], int(svc.cfg["port"])), Handler)
    httpd.daemon_threads = True
    httpd.svc = svc
    return httpd


# --------------------------------------------------------------------------- selftest
def selftest(config_path):
    """Config parses, both schemas load, the model view is a strict subset, the
    question menu is ASCII and complete. No network, no Ollama, no sqlite file."""
    import schemas
    fails = []

    def check(label, ok, detail=""):
        print("  %s %-52s %s" % ("ok  " if ok else "FAIL", label, detail))
        if not ok:
            fails.append(label)

    try:
        cfg = load_config(config_path)
        check("config parses (%s)" % os.path.basename(config_path or "-"), True,
              "port %s, model %s" % (cfg["port"], cfg["model_debrief"]))
    except Exception as exc:                                       # noqa: BLE001
        check("config parses", False, "%s: %s" % (type(exc).__name__, exc))
        cfg = dict(DEFAULTS)

    check("bind is loopback", cfg["bind"] in ("127.0.0.1", "::1", "localhost"),
          cfg["bind"])
    check("ollama stays on loopback", "127.0.0.1" in cfg["ollama_url"],
          cfg["ollama_url"])
    check("body cap is 64 KB", cfg["max_body"] == 65536, str(cfg["max_body"]))

    for name in ("pack.v1", "debrief.v1"):
        try:
            schemas.load(name)
            check("schema %s loads" % name, True)
        except Exception as exc:                                   # noqa: BLE001
            check("schema %s loads" % name, False, str(exc))

    try:
        full = schemas.load("debrief.v1")
        view = model_format(full)
        vp, fp = set(view["properties"]), set(full["properties"])
        check("model view drops the provenance fields",
              vp == fp - set(SERVICE_FIELDS), ", ".join(sorted(fp - vp)))
        check("model view keeps additionalProperties:false",
              view.get("additionalProperties") is False)
    except Exception as exc:                                       # noqa: BLE001
        check("model view", False, str(exc))

    check("every question id maps to one ASCII line",
          all(q == to_ascii(q) and "\n" not in q for q in QUESTIONS.values()),
          " ".join(sorted(QUESTIONS)))
    budget = cfg["num_ctx"] - cfg["num_predict"] - cfg["budget_slack"]
    check("prompt budget leaves room to answer", budget > 0, "%d tokens" % budget)

    print()
    if fails:
        print("FAILED: %d check(s) -> %s" % (len(fails), fails))
        return 1
    print("SERVICE SELFTEST PASSED")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=None,
                    help="JSON config (default $RADIO_CONFIG or %s)" % DEFAULT_CONFIG)
    ap.add_argument("--once", action="store_true",
                    help="process the queued jobs and exit (tests, and hand cranking)")
    ap.add_argument("--rules-only", action="store_true",
                    help="skip the model stage entirely; rules_only.build answers")
    ap.add_argument("--selftest", action="store_true",
                    help="config + schemas, no network")
    args = ap.parse_args(argv)

    if args.selftest:
        path = args.config
        if not path:
            for cand in (os.environ.get("RADIO_CONFIG"), DEFAULT_CONFIG,
                         os.path.join(HERE, "config.example.json")):
                if cand and os.path.exists(cand):
                    path = cand
                    break
        return selftest(path)

    cfg = load_config(args.config)
    if args.rules_only:
        cfg["rules_only"] = True
    svc = Service(cfg)

    if args.once:
        n = svc.run_once()
        log("--once: processed %d job(s)" % n)
        return 0

    worker = threading.Thread(target=svc.worker_loop, name="radio-worker", daemon=True)
    worker.start()
    httpd = make_server(svc)
    log("listening on %s:%s (models %s / %s, corpus %s)"
        % (cfg["bind"], cfg["port"], cfg["model_debrief"], cfg["model_fast"],
           svc.corpus_commit))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        svc.stop.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
