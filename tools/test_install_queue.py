#!/usr/bin/env python3
"""ETK — host-side regression tests for BACKGROUND installs (0.8.4).

Run from the repo root:   python3 tools/test_install_queue.py

Installs no longer hold the Pitstop hostage: Pitstop queues a job and
bin/etk_install_worker.py drains it out of process, so the operator can keep
using the app, go back to ES, or close the terminal. Three things make that
safe, and each is a suite here.

  [SENTRY] An installer and a game are BOTH RPCS3. Once they can be live at
           the same moment, the Sentry has to tell them apart or it either
           invents a phantom telemetry session (igniting on the installer) or
           loses a real race (parking on the install lock, which is what
           0.8.3 did). The discrimination is EXTRACTED FROM install.sh and run
           as shipped text against a fixture /proc — a copy here could drift
           from the Sentry that actually runs.

  [PROC]   The same rule on the Python side, which the worker uses to decide
           when to yield and which emulator to kill. Both must agree; a
           disagreement is how an install ends up killing the operator's game.

  [QUEUE]  Enqueue, dedupe, ordering, worker liveness and the yield/requeue
           handoff — including that a queued job survives the Pitstop exiting,
           which is the entire point of the change.

No rig, no bus, no emulator, no root.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.path.join(ROOT, "bin", "etk_pitstop.py")
WORKER = os.path.join(ROOT, "bin", "etk_install_worker.py")
INSTALL_SH = os.path.join(ROOT, "install.sh")

spec = importlib.util.spec_from_file_location('pit', PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


# The cases that matter. Each is (fixture name, {pid: argv}, a game is live?).
# argv is stored NUL-separated exactly as /proc/<pid>/cmdline presents it.
CASES = [
    ("empty",                {},                                              False),
    ("installer_pkg",        {101: "/usr/bin/rpcs3-sa --headless --installpkg /d/x.pkg"},  False),
    ("installer_fw",         {102: "/usr/bin/rpcs3-sa --headless --installfw /d/PS3UPDAT.PUP"}, False),
    ("game",                 {103: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/GT5P.psn"},     True),
    # THE case the whole change exists for: a race started mid-install.
    ("game_during_install",  {104: "/usr/bin/rpcs3-sa --headless --installpkg /d/x.pkg",
                              105: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/GT5P.psn"},     True),
    ("apprun_game",          {106: "/tmp/.mount_x/AppRun.wrapped /roms/ps3/GT.iso"},     True),
    # The postmortem greps RPCS3.log; it must never read as an emulator.
    ("postmortem",           {107: "strings /storage/.cache/rpcs3/RPCS3.log"},           False),
    ("worker_argv",          {108: "python3 /roms/etk/bin/etk_install_worker.py"},       False),
    ("pitstop_argv",         {109: "python3 /roms/etk/bin/etk_pitstop.py"},              False),
    ("two_installers",       {110: "/usr/bin/rpcs3-sa --headless --installpkg /a.pkg",
                              111: "/usr/bin/rpcs3-sa --headless --installfw /b.pup"},   False),
    # Pathological but decisive: a substring test would call this an installer
    # and silently cost a real session its telemetry.
    ("rom_named_installfw",  {112: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/my--installfw.iso"}, True),
    # THE LIVE LOOP BUG (rig, 2026-08-06), argv verbatim. The AppImage spawns a
    # dwarfs FUSE helper carrying the image's own path but none of the install
    # flags. Matching "any argument" saw the installer's OWN mount helper as a
    # game: the worker killed its install 2s in, requeued, and toggled between
    # INSTALL FAILED and INSTALL PAUSED forever. Only argv[0] tells them apart.
    ("installer_with_dwarfs_helper",
     {113: "/storage/rpcs3/rpcs3-sa.custom --headless --installfw /d/PS3UPDAT.PUP",
      114: "dwarfs /storage/rpcs3/rpcs3-sa.custom /tmp/.mount_rqZmH1hm -f -o ro,nodev,noatime",
      115: "/tmp/.mount_rqZmH1hm/AppRun.wrapped --headless --installfw /d/PS3UPDAT.PUP"},
     False),
    # ...and the same helper alongside a real game must still read as a game.
    ("game_with_dwarfs_helper",
     {116: "/storage/rpcs3/rpcs3-sa.custom /roms/ps3/GT5P.psn",
      117: "dwarfs /storage/rpcs3/rpcs3-sa.custom /tmp/.mount_ab12 -f -o ro,nodev,noatime"},
     True),
]


def _build_procfs(base, procs):
    for pid, argv in procs.items():
        d = os.path.join(base, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(argv.replace(" ", "\0").encode())
    return base


# ==========================================================
print("\n[SENTRY] install.sh's etk_game_running, as shipped, vs a fixture /proc")
# ==========================================================
src = open(INSTALL_SH).read()
m = re.search(r"(etk_game_running\(\) \{.*?\n\})", src, re.S)
check_true("etk_game_running found in install.sh", bool(m))
if m:
    fn = m.group(1)
    check_true("Sentry matches the install flags as whole tokens (grep -x)",
               "-qxE" in fn,
               "a substring test misreads a ROM path containing --installfw")
    check_true("Sentry identifies the emulator by argv[0]",
               "head -n1" in fn,
               "else the AppImage's dwarfs helper reads as a game")
    check_true("Sentry's flag pattern has no backslash-escaped dashes",
               "\\-\\-install" not in fn,
               "BusyBox warns 'stray \\ before -' every 2s into the journal")
    tmp = tempfile.mkdtemp()
    try:
        for name, procs, want_game in CASES:
            proc_dir = _build_procfs(os.path.join(tmp, name), procs)
            # Two EXTERNAL references get retargeted at the fixture — the
            # pgrep candidate list and the cmdline path. The discrimination
            # itself (the whole-token grep) is the shipped text, untouched.
            stub = os.path.join(proc_dir, "stub")
            os.makedirs(stub, exist_ok=True)
            with open(os.path.join(stub, "pgrep"), "w") as f:
                f.write("#!/bin/sh\n" + "".join(
                    f"echo {pid}\n" for pid in sorted(procs)))
            os.chmod(os.path.join(stub, "pgrep"), 0o755)
            probe = fn.replace('"/proc/$_PID/cmdline"',
                               f'"{proc_dir}/$_PID/cmdline"')
            script = probe + "\netk_game_running && echo GAME || echo NOGAME\n"
            env = dict(os.environ,
                       PATH=stub + os.pathsep + "/bin" + os.pathsep + "/usr/bin")
            r = subprocess.run(["sh", "-c", script], capture_output=True,
                               text=True, env=env)
            got = r.stdout.strip()
            check(f"sentry: {name}", got, "GAME" if want_game else "NOGAME")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

check_true("Sentry keeps a shutdown edge even while the install lock is held",
           'PREV_STATE" != "RUNNING"' in src,
           "otherwise a game ending during an install loses its ledger row")
check_true("Sentry does not walk all of /proc every tick",
           "pgrep -f" in (m.group(1) if m else ""),
           "two forks per process every 2s is a real tax on this SoC")
check_true("recovery.sh preserves the install queue across an R3 recovery",
           "install_worker.pid|etk_install_stat|install_queue"
           in open(os.path.join(ROOT, "bin", "recovery.sh")).read(),
           "R3 recovers a wedged game and has no quarrel with a queued install")

# ==========================================================
print("\n[PROC] the Python rule must agree with the Sentry, case for case")
# ==========================================================
tmp = tempfile.mkdtemp()
try:
    for name, procs, want_game in CASES:
        proc_dir = _build_procfs(os.path.join(tmp, name), procs)
        game = bool(pit._rpcs3_pids(installer=False, procfs=proc_dir))
        check(f"python: {name}", game, want_game)
    # And the split itself: installer vs game vs all.
    d = _build_procfs(os.path.join(tmp, "game_during_install"), {})
    check("installer pids identified",
          sorted(pit._rpcs3_pids(installer=True, procfs=d)), [104])
    check("game pids identified",
          sorted(pit._rpcs3_pids(installer=False, procfs=d)), [105])
    check("all pids identified",
          sorted(pit._rpcs3_pids(procfs=d)), [104, 105])
    check("a torn-down /proc entry does not raise",
          pit._rpcs3_pids(procfs=os.path.join(tmp, "nope")), [])
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# The kill path is the one that can hurt the operator: prove it only ever
# targets installer PIDs.
killed = []
real_kill = pit.os.kill
tmpk = tempfile.mkdtemp()
try:
    d = _build_procfs(tmpk, {104: "/usr/bin/rpcs3-sa --headless --installpkg /d/x.pkg",
                             105: "/usr/bin/rpcs3-sa --no-gui /roms/ps3/GT5P.psn"})
    pit.os.kill = lambda pid, sig: killed.append((pid, sig))
    orig = pit._rpcs3_pids
    pit._rpcs3_pids = lambda installer=None, procfs=d: orig(installer, d)
    pit.time.sleep = lambda *_: None
    pit._kill_installer_rpcs3()
    check_true("installer kill targets the installer PID",
               any(p == 104 for p, _ in killed))
    check_true("installer kill NEVER touches the game PID",
               all(p != 105 for p, _ in killed),
               "a pattern pkill would have taken the operator's race down")
finally:
    pit.os.kill = real_kill
    pit._rpcs3_pids = orig
    import shutil, time as _t
    pit.time.sleep = _t.sleep
    shutil.rmtree(tmpk, ignore_errors=True)

# ==========================================================
print("\n[QUEUE] enqueue / dedupe / ordering / worker liveness")
# ==========================================================
tmp = tempfile.mkdtemp()
try:
    pit.SHM_DIR = tmp
    pit.INSTALL_QUEUE_DIR = os.path.join(tmp, "install_queue")
    pit.INSTALL_WORKER_PID = os.path.join(tmp, "install_worker.pid")
    pit.INSTALL_STAT_FILE = os.path.join(tmp, "etk_install_stat")
    spawned = []
    pit.subprocess.Popen = lambda cmd, **kw: spawned.append(list(cmd))

    pkg = os.path.join(tmp, "game.pkg")
    open(pkg, "w").write("x")
    ok, why = pit._enqueue_install("pkg", pkg, None)
    check("first enqueue accepted", (ok, why), (True, "queued"))
    check("worker spawned on first enqueue", len(spawned), 1)
    check_true("worker spawned detached from the Pitstop",
               any("etk_install_worker.py" in c for c in spawned[0]),
               "the install must outlive the app")

    ok2, why2 = pit._enqueue_install("pkg", pkg, None)
    check("duplicate refused", (ok2, why2), (False, "already queued"))
    check_true("a duplicate does not queue the job twice",
               len(pit._install_queue()) == 1)

    pup = os.path.join(tmp, "PS3UPDAT.PUP")
    open(pup, "w").write("x")
    ok3, _ = pit._enqueue_install("fw", pup, None)
    check("second distinct job accepted", ok3, True)
    q = pit._install_queue()
    check("both jobs queued", len(q), 2)
    check("queue is ordered oldest-first",
          [j["path"] for _, j in q], [pkg, pup])

    # A live worker must not be duplicated; a stale pidfile must be taken over.
    with open(pit.INSTALL_WORKER_PID, "w") as f:
        f.write(str(os.getpid()))
    check("live worker detected", pit._worker_alive(), True)
    before = len(spawned)
    pkg2 = os.path.join(tmp, "other.pkg")
    open(pkg2, "w").write("x")
    pit._enqueue_install("pkg", pkg2, None)
    check("no extra worker while one is alive", len(spawned), before)
    with open(pit.INSTALL_WORKER_PID, "w") as f:
        f.write("999999")          # a pid that cannot exist
    check("stale pidfile is not mistaken for a live worker",
          pit._worker_alive(), False)

    # Pre-flight: a missing file is refused BEFORE queueing; a running game is
    # explicitly NOT a refusal any more — that is what the queue is for.
    check_true("missing file refused up front",
               bool(pit._install_preflight("pkg", os.path.join(tmp, "gone.pkg"))))
    pit._storage_incoherent_msg = lambda: None
    real_game = pit._game_running
    pit._game_running = lambda: True
    check("a running game is NOT a pre-flight refusal",
          pit._install_preflight("pkg", pkg), None)
    pit._game_running = real_game

    # THE DEFECT THAT WOULD HAVE SHIPPED: licences are a LIST, and writing the
    # list into one TSV field serialised its repr — the installer then treated
    # that text as a single path, failed every copy, and still reported the
    # install complete. A DRM'd game would install and refuse to boot.
    wspec0 = importlib.util.spec_from_file_location('worker_rt', WORKER)
    w0 = importlib.util.module_from_spec(wspec0)
    wspec0.loader.exec_module(w0)
    rap_a = os.path.join(tmp, "a.rap")
    rap_b = os.path.join(tmp, "b.rap")
    multi = os.path.join(tmp, "multi.pkg")
    open(multi, "w").write("x")
    pit._enqueue_install("pkg", multi, [rap_a, rap_b])
    jobfile = [os.path.join(pit.INSTALL_QUEUE_DIR, n)
               for n, j in pit._install_queue() if j["path"] == multi][0]
    round_tripped = w0._read_job(jobfile)
    check("every licence survives the queue round-trip",
          round_tripped["rap"], [rap_a, rap_b])
    check_true("no Python repr leaked into the job file",
               "[" not in open(jobfile).read(),
               "a stringified list becomes one bogus licence path")

    # A queue orphaned in the worker's exit window must be re-armable. Pressing
    # install again is the obvious remedy, so it must not short-circuit.
    with open(pit.INSTALL_WORKER_PID, "w") as f:
        f.write("999999")                     # worker died; queue still full
    spawned.clear()
    ok_d, why_d = pit._enqueue_install("pkg", multi, [rap_a])
    check("re-queueing a duplicate still reports it as a duplicate",
          (ok_d, why_d), (False, "already queued"))
    check_true("...but it RE-ARMS the dead worker", len(spawned) == 1,
               "otherwise an orphaned queue needs a reboot to clear")

    # Two enqueues in the same millisecond must not overwrite one another.
    real_time = pit.time.time
    pit.time.time = lambda: 1770000000.0
    a1 = os.path.join(tmp, "same1.pkg"); open(a1, "w").write("x")
    a2 = os.path.join(tmp, "same2.pkg"); open(a2, "w").write("x")
    before_q = len(pit._install_queue())
    pit._enqueue_install("pkg", a1, None)
    pit._enqueue_install("pkg", a2, None)
    pit.time.time = real_time
    check("same-millisecond enqueues both survive",
          len(pit._install_queue()) - before_q, 2)

    # Status line round-trip (the worker writes it, the TOOLS tab reads it).
    with open(pit.INSTALL_STAT_FILE, "w") as f:
        f.write("INSTALLING\tGT5P.pkg\t42\t\n")
    st = pit._install_status()
    check("status parsed", (st["state"], st["name"], st["pct"]),
          ("INSTALLING", "GT5P.pkg", "42"))
    os.remove(pit.INSTALL_STAT_FILE)
    check("no status when idle", pit._install_status(), None)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ==========================================================
print("\n[WORKER] job file handling and the yield/requeue contract")
# ==========================================================
wspec = importlib.util.spec_from_file_location('worker', WORKER)
worker = importlib.util.module_from_spec(wspec)
wspec.loader.exec_module(worker)

tmp = tempfile.mkdtemp()
try:
    worker.SHM_DIR = tmp          # else it tries to mkdir /dev/shm on a host
    worker.QUEUE_DIR = os.path.join(tmp, "install_queue")
    worker.STATFILE = os.path.join(tmp, "stat")
    worker.PIDFILE = os.path.join(tmp, "pid")
    os.makedirs(worker.QUEUE_DIR)

    jf = os.path.join(worker.QUEUE_DIR, "100.job")
    with open(jf, "w") as f:
        f.write("pkg\t/d/x.pkg\t/d/x.rap\n")
    job = worker._read_job(jf)
    check("job parsed", (job["kind"], job["path"], job["rap"]),
          ("pkg", "/d/x.pkg", ["/d/x.rap"]))
    with open(os.path.join(worker.QUEUE_DIR, "101.job"), "w") as f:
        f.write("fw\t/d/f.pup\t\n")
    check("no licence reads back as an empty list",
          worker._read_job(os.path.join(worker.QUEUE_DIR, "101.job"))["rap"], [])
    check("jobs listed oldest-first", worker._jobs(), ["100.job", "101.job"])
    with open(os.path.join(worker.QUEUE_DIR, "102.job"), "w") as f:
        f.write("garbage\n")
    check("a malformed job is rejected, not crashed on",
          worker._read_job(os.path.join(worker.QUEUE_DIR, "102.job")), None)

    # A claimed job is invisible to the queue; requeueing restores its place.
    os.rename(jf, jf + ".running")
    check("claimed job leaves the pending list", "100.job" in worker._jobs(), False)
    os.rename(jf + ".running", jf)
    check("requeued job returns to the HEAD of the queue",
          worker._jobs()[0], "100.job")

    os.remove(worker.PIDFILE) if os.path.exists(worker.PIDFILE) else None
    check("pidfile claim succeeds when free", worker._claim_pidfile(), True)
    # A DIFFERENT live process owning the queue must turn a second worker away,
    # or two workers would drive RPCS3 at the same time.
    with open(worker.PIDFILE, "w") as f:
        f.write(str(os.getppid()))
    check("a live worker keeps the queue to itself",
          worker._claim_pidfile(), False)
    with open(worker.PIDFILE, "w") as f:
        f.write("999999")          # a pid that cannot exist
    check("stale pidfile is taken over", worker._claim_pidfile(), True)

    # A worker that died mid-job leaves a .running file _jobs() cannot see.
    # Winning the pidfile claim proves it is an orphan, so it must be reclaimed
    # or the job is stranded until the next reboot.
    orphan = os.path.join(worker.QUEUE_DIR, "200.job.running")
    with open(orphan, "w") as f:
        f.write("pkg\t/d/orphan.pkg\n")
    src_main = open(WORKER).read()
    check_true("worker reclaims an orphaned .running job on startup",
               ".job.running" in src_main and "reclaimed orphaned job" in src_main)
    check_true("worker settles before starting a job after a session",
               "SETTLE_S" in src_main,
               "the Sentry postmortem reads RPCS3.log, which our installer rewrites")
    check_true("worker toasts a verdict when a job fails",
               "INSTALL FAILED" in src_main,
               "a background failure with no screen would otherwise be silent")
    check_true("worker only requeues a job that actually failed",
               "aborted.is_set() and not ok" in src_main)
    check_true("game watcher keeps watching after the first kill",
               src_main.count("pit._kill_installer_rpcs3()") >= 1
               and "if not aborted.is_set():" in src_main)
    check_true("no fabricated percentage in the status line",
               "_write_stat('INSTALLING', name, '', '')" in src_main)
    check_true("repeated yields for one job stand the worker down",
               "MAX_YIELDS" in src_main and "INSTALL DEFERRED" in src_main,
               "a false game reading must not thrash the emulator forever")

    worker._write_stat("INSTALLING", "x.pkg", "10", "")
    with open(worker.STATFILE) as f:
        check("status written atomically and parseable",
              f.read().rstrip("\n").split("\t")[:3], ["INSTALLING", "x.pkg", "10"])
    worker._clear_stat()
    check("status cleared on exit", os.path.exists(worker.STATFILE), False)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL BACKGROUND-INSTALL CHECKS PASSED")
