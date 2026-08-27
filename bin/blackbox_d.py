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
import os, sys, time, shutil, glob, threading

TELEMETRY_DIR = os.environ.get(
    'TELEMETRY_DIR', '/storage/games-internal/roms/etk/etk_telemetry')
TRIPWIRE_LOG = os.environ.get('TRIPWIRE_LOG', '/storage/etk_tripwire.log')
PSTORE_DIR = '/sys/fs/pstore'
BB_DIR = os.path.join(TELEMETRY_DIR, 'blackbox')
KMSG_MAX_BYTES = 8 * 1024 * 1024   # rotate current log past this
KMSG_KEEP_FILES = 12               # prune old kmsg logs beyond this many
FSYNC_INTERVAL_S = 1.0
SHM_DIR = os.environ.get('ETK_SHM', '/dev/shm/etk_shm')
FLIGHTREC_MAX_BYTES = 4 * 1024 * 1024   # rotate the resource log past this
FLIGHTREC_KEEP = 12                     # prune old flightrec logs beyond this
FLIGHTREC_LIVE_S = 1.0                  # sample cadence with a game live
FLIGHTREC_IDLE_S = 5.0                  # sample cadence at idle


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


def prune_flightrec_logs():
    try:
        logs = sorted(glob.glob(os.path.join(BB_DIR, 'flightrec-*.tsv*')),
                      key=lambda p: os.path.getmtime(p))
        for p in logs[:-FLIGHTREC_KEEP] if len(logs) > FLIGHTREC_KEEP else []:
            os.unlink(p)
    except Exception:
        pass


# --- FLIGHT RECORDER (resource curve; the silent-death witness) -----------
# 2026-08-27 census: on the fast PANIC class the kmsg log ends AT ignition —
# no OOM line, no GPU fault, no panic text. pstore is empty (ramoops
# falsified) and the PMIC PON reason is not logged, so the ONLY forensics a
# silent instant death can leave is a resource curve written continuously
# and fsync'd as it goes. These helpers are PURE (tools/test_flightrec.py
# feeds them fixtures); the sampler thread below stays fail-silent and
# touches nothing but its own file under blackbox/.

def parse_meminfo(text):
    """{'MemAvailable': kB, 'SwapFree': kB} from /proc/meminfo content.
    Missing keys stay 0 — a short read must not raise."""
    out = {'MemAvailable': 0, 'SwapFree': 0}
    for ln in text.splitlines():
        key, _, rest = ln.partition(':')
        if key in out:
            try:
                out[key] = int(rest.split()[0])
            except (ValueError, IndexError):
                pass
    return out


def parse_psi(text, kind):
    """avg10 for the 'some'/'full' line of a /proc/pressure file; -1.0 when
    the line or field is absent (old kernel, CONFIG_PSI off)."""
    for ln in text.splitlines():
        if ln.startswith(kind + ' '):
            for tok in ln.split():
                if tok.startswith('avg10='):
                    try:
                        return float(tok[6:])
                    except ValueError:
                        return -1.0
    return -1.0


def flightrec_row(epoch, game, mem, psi_mem_some, psi_mem_full,
                  psi_cpu_some, psi_io_some, load1, tmax_c, gpu_mhz):
    """One TSV line. Column order is the file header's — append-only, same
    contract as the ledger (readers index positionally)."""
    return "\t".join([
        str(int(epoch)), game or '-',
        str(mem.get('MemAvailable', 0)), str(mem.get('SwapFree', 0)),
        f"{psi_mem_some:.2f}", f"{psi_mem_full:.2f}",
        f"{psi_cpu_some:.2f}", f"{psi_io_some:.2f}",
        f"{load1:.2f}", str(int(tmax_c)), str(int(gpu_mhz)),
    ])


FLIGHTREC_HEADER = ("# epoch\tgame\tmem_avail_kb\tswap_free_kb\t"
                    "psi_mem_some10\tpsi_mem_full10\tpsi_cpu_some10\t"
                    "psi_io_some10\tload1\ttmax_c\tgpu_mhz\n")


def _read_first(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ''


def flight_recorder_sampler():
    """Sample the resource curve to a per-boot TSV, fsync'd per write:
    1 Hz while the Sentry marks a game live (SHM active_id.txt non-empty),
    slow tick at idle. A PANIC row joins it by the row's
    [epoch - duration_s, epoch] interval, exactly like bog metas.
    Kill-switch: ETK_FLIGHTREC=0. Fail-silent; never touches the Sentry,
    the ledger schema, or the R3 path."""
    boot_epoch = int(time.time())
    path = os.path.join(BB_DIR, f'flightrec-{boot_epoch}.tsv')
    zones = sorted(glob.glob('/sys/class/thermal/thermal_zone*/temp'))
    gpu_freq_paths = sorted(glob.glob('/sys/class/devfreq/*gpu*/cur_freq'))
    gpu_freq_path = gpu_freq_paths[0] if gpu_freq_paths else None
    active_id = os.path.join(SHM_DIR, 'active_id.txt')
    while True:
        try:
            out = open(path, 'a', buffering=1)
            if out.tell() == 0:
                out.write(FLIGHTREC_HEADER)
            written = out.tell()
            log(f"flight recorder (resources) -> {path}")
            while True:
                game = _read_first(active_id).strip()
                mem = parse_meminfo(_read_first('/proc/meminfo'))
                psi_mem = _read_first('/proc/pressure/memory')
                tmax = 0
                for z in zones:
                    try:
                        tmax = max(tmax, int(_read_first(z)) // 1000)
                    except (ValueError, TypeError):
                        pass
                try:
                    load1 = float(_read_first('/proc/loadavg').split()[0])
                except (ValueError, IndexError):
                    load1 = -1.0
                try:
                    gpu_mhz = int(_read_first(gpu_freq_path)) // 1000000 \
                        if gpu_freq_path else 0
                except (ValueError, TypeError):
                    gpu_mhz = 0
                line = flightrec_row(
                    time.time(), game, mem,
                    parse_psi(psi_mem, 'some'), parse_psi(psi_mem, 'full'),
                    parse_psi(_read_first('/proc/pressure/cpu'), 'some'),
                    parse_psi(_read_first('/proc/pressure/io'), 'some'),
                    load1, tmax, gpu_mhz) + "\n"
                out.write(line)
                written += len(line)
                try:
                    os.fsync(out.fileno())
                except Exception:
                    pass
                if written >= FLIGHTREC_MAX_BYTES:
                    out.close()
                    try:
                        os.replace(path, path + '.old')
                    except Exception:
                        pass
                    out = open(path, 'a', buffering=1)
                    out.write(FLIGHTREC_HEADER)
                    written = out.tell()
                time.sleep(FLIGHTREC_LIVE_S if game else FLIGHTREC_IDLE_S)
        except Exception as e:
            log(f"flight recorder error: {e}. Re-arming in 5s...")
            time.sleep(5)


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

    def ensure_linked(cur):
        # SELF-RELINK GUARD (live incident 2026-08-01, 20260801 stack): the
        # announced log was found UNLINKED while this daemon still held it —
        # every write was going to an orphaned inode, which a panic-reboot
        # cannot recover (the whole point of this recorder is surviving one).
        # Culprit unknown (no kit code deletes the live file; hunt banked).
        # Defense in depth: if the path vanishes, salvage the orphan's bytes
        # through /proc/self/fd and re-open the path so the recording is
        # back on disk. Fail-soft: any error keeps the current handle.
        try:
            if os.path.exists(path):
                return cur
            content = ''
            try:
                with open(f'/proc/self/fd/{cur.fileno()}', 'r',
                          errors='replace') as orphan:
                    content = orphan.read()
            except Exception:
                pass
            try:
                cur.close()
            except Exception:
                pass
            fresh = open(path, 'a', buffering=1)
            if content:
                fresh.write(content)
            log(f"kmsg log was unlinked externally — relinked {path} "
                f"({len(content)}B salvaged)")
            tripwire(f"blackbox kmsg log relinked after external unlink "
                     f"({len(content)}B salvaged)")
            return fresh
        except Exception:
            return cur

    idle_ticks = 0
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
                idle_ticks += 1
                if idle_ticks >= 50:      # ~10s quiet: still verify the link
                    idle_ticks = 0
                    out = ensure_linked(out)
                continue
            idle_ticks = 0
            out.write(line)
            written += len(line)
            now = time.monotonic()
            if now - last_fsync >= FSYNC_INTERVAL_S:
                out = ensure_linked(out)
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
    # The resource flight recorder rides shotgun in its own thread (its own
    # forever-loop; started once, not per re-arm). Default-ON, ETK_FLIGHTREC=0
    # kills it without touching the kmsg recorder.
    if os.environ.get('ETK_FLIGHTREC', '1') != '0':
        try:
            os.makedirs(BB_DIR, exist_ok=True)
            prune_flightrec_logs()
            threading.Thread(target=flight_recorder_sampler,
                             daemon=True).start()
        except Exception as e:
            print(f"[!] ETK BLACKBOX: flightrec start failed: {e}", flush=True)
    while True:
        try:
            os.makedirs(BB_DIR, exist_ok=True)
            harvest_pstore()
            prune_kmsg_logs()
            kmsg_recorder()   # blocks forever in normal operation
        except Exception as e:
            print(f"[!] ETK BLACKBOX: {e}. Re-arming in 5s...", flush=True)
            time.sleep(5)
