#!/usr/bin/env python3
# ==========================================================
# ETK PANIC BLACK BOX (v1.0.0 - PSTORE HARVEST + KMSG FLIGHT RECORDER)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. PURPOSE: kernel panics leave NO trace on this rig by default
#    (dmesg volatile, journald Storage=volatile/RuntimeMaxUse=2M,
#    pstore mounted but ramoops unarmed). This daemon is the read
#    side of the Panic Black Box: (a) at every boot it HARVESTS
#    /sys/fs/pstore/* (the previous panic's ramoops dumps) into
#    persistent telemetry BEFORE anything can clear them, then
#    frees the pstore slots; (b) it then tails /dev/kmsg to disk
#    with frequent fsync as the lead-up flight recorder (GPU
#    faults, OOM pressure, I/O errors preceding a death that
#    ramoops itself can't attribute).
# 2. The WRITE side (reserve_mem= cmdline on both grub twins +
#    modules-load.d/modprobe.d confs) is armed ONCE by the operator
#    via scripts/arm_blackbox.sh — NEVER auto-applied by install.sh
#    (a bad grub edit can brick boot; the armer is operator-run and
#    cold-boot validated per doctrine). This daemon must stay
#    harmless when ramoops is NOT armed: harvest finds nothing,
#    the kmsg recorder still runs.
# 3. FAIL-SILENT + SELF-HEALING: any I/O error sleeps and retries;
#    the daemon must never crash-loop hard nor wedge the boot.
#    It writes ONLY under $TELEMETRY_DIR/blackbox/ and the tripwire
#    log. Ledger schema is NOT touched — panic rows are already
#    synthesized by the Sentry's orphan-detect (SESSION_ANCHOR);
#    dumps correlate to rows by epoch.
# ==========================================================
import os, sys, time, shutil, glob

TELEMETRY_DIR = os.environ.get(
    'TELEMETRY_DIR', '/storage/games-internal/roms/etk/etk_telemetry')
TRIPWIRE_LOG = os.environ.get('TRIPWIRE_LOG', '/storage/etk_tripwire.log')
PSTORE_DIR = '/sys/fs/pstore'
BB_DIR = os.path.join(TELEMETRY_DIR, 'blackbox')
KMSG_MAX_BYTES = 8 * 1024 * 1024   # rotate current log past this
KMSG_KEEP_FILES = 12               # prune old kmsg logs beyond this many
FSYNC_INTERVAL_S = 1.0


def log(msg):
    print(f"[*] ETK BLACKBOX: {msg}", flush=True)


def tripwire(msg):
    """One line into the persistent tripwire log (best-effort)."""
    try:
        with open(TRIPWIRE_LOG, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} BLACKBOX: {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def harvest_pstore():
    """Move any pstore records (previous panic/oops dumps) to persistent
    storage, then free the pstore slots so the next panic has room.
    Runs once per boot, before the kmsg loop."""
    try:
        records = sorted(glob.glob(os.path.join(PSTORE_DIR, '*')))
    except Exception:
        records = []
    if not records:
        log("pstore empty (no prior panic captured, or ramoops not armed)")
        return
    stamp = time.strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BB_DIR, 'pstore', stamp)
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception as e:
        log(f"cannot create {dest}: {e} — leaving pstore records in place")
        return
    copied = []
    for rec in records:
        try:
            shutil.copy2(rec, dest)
            copied.append(os.path.basename(rec))
        except Exception as e:
            log(f"copy failed for {rec}: {e}")
    if not copied:
        return
    os.sync()
    # Free the slots only for records that were successfully copied.
    for name in copied:
        try:
            os.unlink(os.path.join(PSTORE_DIR, name))
        except Exception:
            pass
    try:
        with open(os.path.join(BB_DIR, 'last_panic_dump.txt'), 'w') as f:
            f.write(f"{int(time.time())}\t{dest}\t{','.join(copied)}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass
    log(f"HARVESTED {len(copied)} pstore record(s) -> {dest}")
    tripwire(f"harvested {len(copied)} panic record(s) -> {dest}")


def prune_kmsg_logs():
    try:
        logs = sorted(glob.glob(os.path.join(BB_DIR, 'kmsg-*.log*')),
                      key=lambda p: os.path.getmtime(p))
        for p in logs[:-KMSG_KEEP_FILES] if len(logs) > KMSG_KEEP_FILES else []:
            os.unlink(p)
    except Exception:
        pass


def kmsg_recorder():
    """Tail /dev/kmsg to a per-boot persistent log with ~1s fsync cadence.
    The first read of /dev/kmsg replays the whole ring buffer, so the boot
    lead-in is captured too. EPIPE (ring overrun) is expected under message
    storms — reopen-free continue per kernel semantics."""
    boot_epoch = int(time.time())
    path = os.path.join(BB_DIR, f'kmsg-{boot_epoch}.log')
    out = open(path, 'a', buffering=1)
    log(f"flight recorder -> {path}")
    last_fsync = 0.0
    written = out.tell()
    with open('/dev/kmsg', 'r', errors='replace') as kmsg:
        while True:
            try:
                line = kmsg.readline()
            except OSError:
                # EPIPE on ring-buffer overrun: kernel auto-advances; go on.
                time.sleep(0.05)
                continue
            if not line:
                time.sleep(0.2)
                continue
            out.write(line)
            written += len(line)
            now = time.monotonic()
            if now - last_fsync >= FSYNC_INTERVAL_S:
                try:
                    os.fsync(out.fileno())
                except Exception:
                    pass
                last_fsync = now
            if written >= KMSG_MAX_BYTES:
                # Rotate: keep exactly one previous chunk per boot file.
                out.close()
                try:
                    os.replace(path, path + '.old')
                except Exception:
                    pass
                out = open(path, 'a', buffering=1)
                written = 0


if __name__ == '__main__':
    while True:
        try:
            os.makedirs(BB_DIR, exist_ok=True)
            harvest_pstore()
            prune_kmsg_logs()
            kmsg_recorder()   # blocks forever in normal operation
        except Exception as e:
            print(f"[!] ETK BLACKBOX: {e}. Re-arming in 5s...", flush=True)
            time.sleep(5)
