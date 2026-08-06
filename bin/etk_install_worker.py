#!/usr/bin/env python3
"""ETK INSTALL WORKER — drains the background install queue.

Installs used to hold the Pitstop hostage: the whole operation ran inside one
main-loop iteration, so the UI could not be used and the app could not be left
until RPCS3 was done. EmulationStation does not work that way — a scrape or a
content install runs in the background behind its own progress card while the
frontend stays live — and this is the piece that lets ETK behave the same.

Pitstop now does the fast pre-flight, drops a job in the queue, and returns to
the UI. This worker runs OUT OF PROCESS (start_new_session), so the operator
can leave the Pitstop, browse ES, or close the terminal and the install keeps
going. It reuses `_run_install` / `_run_install_fw` from etk_pitstop.py rather
than reimplementing them: those two carry the whole hard-won verdict-from-the-
log contract, and a second copy would drift from it.

THE ONE THING THAT MAKES THIS DANGEROUS is a game launched mid-install. RPCS3
is single-instance in practice, and ROCKNIX's own start_rpcs3.sh does an
`rm -rf` + relink of the RPCS3 config directories at EVERY game launch —
exactly the tree an install is writing into. So the worker watches for a real
game (by cmdline, not by process name — see _rpcs3_pids) and, the moment one
appears, terminates its OWN emulator by PID, puts the job back at the head of
the queue, and says so. The game wins; the install resumes when the rig is
idle again. A part-installed PKG is re-runnable — RPCS3 overwrites — which is
why requeue is safe and abandoning the job would not be.

Queue layout (all volatile, under $SHM_DIR — a queue does not survive a reboot
by design; installs are operator-initiated and a cold boot is a clean slate):
    install_queue/<seq>.job      one job per file: kind \\t path \\t rap
    install_queue/<seq>.running  claimed; renamed back to .job on yield
    install_worker.pid           liveness, so Pitstop only ever spawns one
    etk_install_stat             one line for the UI: state \\t name \\t pct \\t msg
"""
import os
import sys
import time
import signal
import threading
import importlib.util

ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')
SHM_DIR = os.environ.get('SHM_DIR', '/dev/shm/etk_shm')
QUEUE_DIR = os.path.join(SHM_DIR, 'install_queue')
PIDFILE = os.path.join(SHM_DIR, 'install_worker.pid')
STATFILE = os.path.join(SHM_DIR, 'etk_install_stat')
LOGFILE = os.path.join(ETK_ROOT, 'etk_telemetry', 'install_worker.log')

# How often to look for a game appearing under us. RPCS3 takes several seconds
# to come up, so 2s catches a launch well before it touches the config tree.
GAME_POLL_S = 2.0
# Grace after an emulator exits, before starting a job. The Sentry's
# RUNNING->IDLE rollup runs the session postmortem, which reads RPCS3.log
# whole; our installer rewrites that log at launch. Its budget is <2s, so this
# is deliberately generous — a late install costs nothing, a shredded ledger
# row cannot be recovered.
SETTLE_S = 20.0
# Consecutive yields tolerated for one job before the worker stands down. A
# genuine yield happens once (the operator started a race); repeated yields for
# the same job mean the game test is firing on something that is not a game,
# and retrying forever thrashes the emulator instead of failing visibly.
MAX_YIELDS = 3


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        with open(LOGFILE, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _load_pitstop():
    """Import etk_pitstop.py by path — it is a script, not an installed module.
    Importing it is safe (it starts no curses; the host test suites do the
    same), and it brings the installers, the notifier and the ES surfaces."""
    path = os.path.join(ETK_ROOT, 'bin', 'etk_pitstop.py')
    spec = importlib.util.spec_from_file_location('etk_pitstop', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_stat(state, name="", pct="", msg=""):
    """Publish one line for the Pitstop TOOLS tab. Atomic tmp+rename so a
    half-written line is never read."""
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        tmp = STATFILE + '.tmp'
        with open(tmp, 'w') as f:
            f.write(f"{state}\t{name}\t{pct}\t{msg}\n")
        os.replace(tmp, STATFILE)
    except Exception:
        pass


def _clear_stat():
    try:
        os.remove(STATFILE)
    except OSError:
        pass


def _claim_pidfile():
    """Become THE worker, or exit. O_EXCL makes the claim atomic; a pidfile
    whose process is gone (a crash, or the SHM surviving a Pitstop restart) is
    stale and gets taken over."""
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        fd = os.open(PIDFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            with open(PIDFILE) as f:
                other = int(f.read().strip() or 0)
        except Exception:
            other = 0
        if other and other != os.getpid():
            try:
                os.kill(other, 0)
                return False              # a live worker already owns the queue
            except OSError:
                pass                      # stale — fall through and take it
        try:
            os.remove(PIDFILE)
            return _claim_pidfile()
        except OSError:
            return False
    except Exception as e:
        # No SHM means no queue and no worker. Fail soft, but say so — a
        # silent exit here looks identical to "nothing was queued".
        _log(f"cannot claim the queue: {e.__class__.__name__}: {e}")
        return False


def _jobs():
    """Queued jobs, oldest first. The sequence prefix is the ordering."""
    try:
        return sorted(f for f in os.listdir(QUEUE_DIR) if f.endswith('.job'))
    except OSError:
        return []


def _read_job(path):
    try:
        with open(path) as f:
            parts = f.read().rstrip('\n').split('\t')
    except OSError:
        return None
    if len(parts) < 2 or not parts[1]:
        return None
    # Everything after the path is a licence. _run_install takes a list.
    return {'kind': parts[0], 'path': parts[1],
            'rap': [p for p in parts[2:] if p]}


def _run_one(pit, job, jobfile):
    """Run one job, yielding to a game if one appears. Returns
    'done' | 'yielded' | 'failed'."""
    name = os.path.basename(job['path'])
    aborted = threading.Event()
    stop = threading.Event()

    def watch():
        # The whole point of the worker: a game launch outranks an install.
        # Keep watching after the first kill rather than returning — the
        # install path can still be between attempts, and an emulator that
        # survived one signal must not run on beside the game.
        while not stop.wait(GAME_POLL_S):
            if pit._game_running():
                if not aborted.is_set():
                    _log(f"game launched during {name} — yielding")
                    _write_stat('YIELDING', name, '', 'game launched')
                    # We are about to kill this install on purpose, so the
                    # INSTALL FAILED it is about to report is our doing, not a
                    # real verdict. Mute it or the operator sees FAILED flash
                    # immediately before PAUSED — two answers to one event.
                    notifier.mute()
                aborted.set()
                pit._kill_installer_rpcs3()

    notifier = pit._Notifier()
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        _write_stat('INSTALLING', name, '', '')
        if job['kind'] == 'fw':
            ok, lines = pit._run_install_fw(job['path'], notifier)
        else:
            ok, lines = pit._run_install(job['path'], job['rap'], notifier)
    except Exception as e:
        _log(f"{name}: worker error {e.__class__.__name__}: {e}")
        ok, lines = False, [f"worker error: {e.__class__.__name__}"]
    finally:
        stop.set()
        watcher.join(timeout=5)

    if aborted.is_set() and not ok:
        # Put it back at the head of the queue and let the race happen. The
        # verdict toast _run_install already posted is a failure it did not
        # really have, so correct the record with an honest one.
        try:
            os.rename(jobfile + '.running', jobfile)
        except OSError:
            pass
        notifier.unmute()
        notifier.post("INSTALL PAUSED", "resumes when you finish playing",
                      timeout=10000)
        _log(f"{name}: yielded to a game session, requeued")
        return 'yielded'

    if not ok:
        # The installers' own failure toasts cover the RPCS3-verdict branches,
        # but the early returns (storage split, timeout, no title id, worker
        # error) predate the background path and were only ever reported on
        # the result screen — which nobody is looking at now. Always leave a
        # verdict on screen; a background job that fails in silence is worse
        # than one that fails loudly.
        notifier.post(
            "FIRMWARE INSTALL FAILED" if job['kind'] == 'fw' else "INSTALL FAILED",
            (lines[0] if lines else "see install_worker.log")[:40],
            timeout=12000)
    _log(f"{name}: {'OK' if ok else 'FAILED'} — " + " | ".join(lines[:3]))
    try:
        os.remove(jobfile + '.running')
    except OSError:
        pass
    return 'done' if ok else 'failed'


def main():
    if not _claim_pidfile():
        return 0
    # Holding the pidfile proves no other worker is alive, so any job still
    # marked .running belongs to a predecessor that died without its finally
    # (SIGKILL, or the operator pulling power). Put it back at the head of the
    # queue rather than leaving it stranded, and drop the status line it left
    # advertising an install that is no longer happening.
    try:
        for f in sorted(os.listdir(QUEUE_DIR)):
            if f.endswith('.job.running'):
                os.rename(os.path.join(QUEUE_DIR, f),
                          os.path.join(QUEUE_DIR, f[:-len('.running')]))
                _log(f"reclaimed orphaned job {f}")
    except OSError:
        pass
    _clear_stat()
    pit = None
    try:
        pit = _load_pitstop()
        _log(f"worker up (pid {os.getpid()})")
        idle_since = None
        yields = {}
        while True:
            jobs = _jobs()
            if not jobs:
                # Linger briefly so a burst of enqueues reuses this worker
                # instead of paying process startup for each one.
                if idle_since is None:
                    idle_since = time.time()
                if time.time() - idle_since > 10:
                    break
                time.sleep(1)
                continue
            idle_since = None

            # Never start a job on top of a live game.
            waited = 0
            saw_emulator = False
            while pit._rpcs3_running():
                # Wait for ANY emulator to clear, not just a game: the
                # previous job's RPCS3 may still be unwinding, and
                # _run_install refuses outright if one is up.
                saw_emulator = True
                _write_stat('WAITING', '', '', 'emulator busy')
                time.sleep(GAME_POLL_S)
                waited += GAME_POLL_S
                if waited > 6 * 3600:
                    # Six hours is a wedged emulator, not a race. Leave the
                    # queue intact and stand down; the next enqueue re-arms.
                    _log("waited 6h for an idle rig — standing down")
                    return 0
            if saw_emulator:
                # A session just ended, which means the Sentry is doing its
                # RUNNING->IDLE rollup: the postmortem reads RPCS3.log WHOLE
                # to write the ledger row. Our installer rewrites that same
                # log the moment it launches, so starting now would corrupt
                # the row for the race the operator just finished. The rollup
                # has a <2s budget; give it a wide margin.
                _write_stat('WAITING', '', '', 'finishing your session')
                time.sleep(SETTLE_S)

            jobfile = os.path.join(QUEUE_DIR, jobs[0])
            job = _read_job(jobfile)
            if job is None:
                try:
                    os.remove(jobfile)
                except OSError:
                    pass
                continue
            try:
                os.rename(jobfile, jobfile + '.running')
            except OSError:
                continue                   # someone else took it; re-scan
            result = _run_one(pit, job, jobfile)
            if result == 'yielded':
                # A yield is normal ONCE — the operator started a race. Yielding
                # over and over for the SAME job is not: it means something is
                # being read as a game that is not one, and retrying forever
                # just thrashes the emulator and flickers toasts at the
                # operator (live on the rig 2026-08-06, when the AppImage's own
                # dwarfs mount helper matched the game test). Stand down instead
                # and leave the job queued for a deliberate retry.
                yields[job['path']] = yields.get(job['path'], 0) + 1
                if yields[job['path']] >= MAX_YIELDS:
                    _log(f"{os.path.basename(job['path'])}: yielded "
                         f"{MAX_YIELDS}x — standing down, job left queued")
                    _write_stat('DEFERRED', os.path.basename(job['path']),
                                '', 'start it again when you are not playing')
                    try:
                        pit._Notifier().post(
                            "INSTALL DEFERRED",
                            "start it again when you are not playing",
                            timeout=12000)
                    except Exception:
                        pass
                    return 0
                time.sleep(GAME_POLL_S)    # let the game settle before re-checking
            else:
                yields.pop(job['path'], None)
    except Exception as e:
        _log(f"worker fatal: {e.__class__.__name__}: {e}")
    finally:
        _clear_stat()
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        _log("worker down")
    return 0


if __name__ == '__main__':
    # A worker must never take the rig down with it.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    sys.exit(main())
