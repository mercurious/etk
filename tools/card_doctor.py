#!/usr/bin/env python3
"""ETK Card Doctor — TREADWEAR: objective SD-card wear, host-side, card in a USB reader.

SD cards are the tyres of a racing emulation rig: the one consumable, worn by
every session, and replaced on evidence rather than on feel. Metaphor: TREADWEAR.
Mechanism: this tool — measure, never guess; a table per tyre over time.

WHY IT EXISTS (2026-09-04): the operator suspected the rig's 256 GB card was at
the end of its life and wanted a measurement instead of a feeling. Over a USB
card reader the card is a plain SCSI disk — no SMART, no SD registers
(CID/CSD/health extension), no eMMC-style life_time counters — so "health" has
to be MEASURED from behaviour: does every sector read back? twice, identically?
how long do reads take, and where? does it still take writes at the class it
was sold at? This tool does exactly that and nothing clever.

TIERS (each writes state/card_doctor/<runid>/{run.json,report.md,kernel.log}):

  survey  (no root, seconds)   identity, capacity, partitions, USB link speed,
                               kernel-log history for this device since boot.
  files   (no root, minutes)   read EVERY readable file on the mounted card
                               through the filesystem, per-file timing + errors;
                               verifies ROCKNIX's own KERNEL.md5 / SYSTEM.md5 —
                               a known-good reference for 1.5 GB of the card.
  scan    (ROOT, ~75 min/256 GB) raw full-surface O_DIRECT read: per-chunk
                               latency + sha256 map, EIO localized to 4 KiB,
                               second-pass re-read of the outliers (+1 in 32)
                               compared by hash = silent-corruption check, 4 KiB
                               random-read latency percentiles, read-only fsck
                               when the partitions can be unmounted. NEVER
                               writes the device (no fd is ever opened for
                               writing). `--range A-B` (GiB) re-checks one
                               region (is a slow region persistent?) in minutes.
                               Every stage checkpoints run.json; a late failure
                               never loses the pass.
  quick   (ROOT, ~3 min)       4 KiB random-read percentiles + read-only fsck:
                               the two things a crashed scan loses, and a sanity
                               check before committing to a long pass.
  rebuild OUTDIR [--note ..]   reconstruct a crashed scan's run.json/report from
                               its chunks.csv (later stages are not recovered;
                               --note records what the terminal showed, labelled
                               as such).
  write   (ROOT, ~10 min)      writes N GiB of test files into FREE SPACE of the
                               data partition (f3write/f3read when installed,
                               Python fallback), verifies them, measures
                               sequential write speed, then probes 4 KiB random
                               I/O on the first (fully written) sample file:
                               O_DIRECT random write (spec-comparable: A1 = 500
                               IOPS), random write + fdatasync each (save-file
                               style), random read (fio when installed). Then
                               deletes the files. Existing data is not touched.
                               `--gib 1` is the 4-minute random-write re-test.
  report  RUN_JSON [--baseline RUN_JSON]   re-render / compare two runs.
  treadwear [--vs LABEL|card_id]           the wear table: every run of every
                               card, oldest first, plus each card's latest
                               metrics beside a baseline card's. Card identity
                               = hash of the filesystem UUIDs (changes on a
                               reflash; the label carries the name across).

VERDICT LADDER (thresholds are named constants below, each with its basis):
  FAIL      any uncorrectable read error · any hash mismatch between passes ·
            md5 mismatch against the shipped reference · USB reset/timeout/
            offline during the run · write error or read-only lockout.
  DEGRADED  >SLOW_PCT_DEGRADED % of chunks slower than SLOW_X× median, or
            STALLS_DEGRADED+ chunks slower than STALL_X× median (ECC-retry
            storms) · random-read p99 > RAND_P99_MS_DEGRADED · sequential
            write < SEQ_WRITE_MBPS_DEGRADED (the Class-10/U1 floor) ·
            random-write IOPS < RAND_WRITE_IOPS_DEGRADED or p99 > 1 s.
  HEALTHY   none of the above — WITH the honest caveat that a read-only tier
            cannot see write-endurance exhaustion; run `write` for that.
  INCONCLUSIVE  interrupted / partial run with nothing bad seen so far.

NOT DONE, DELIBERATELY: `smartctl`. On 2026-09-04 `smartctl -a -d scsi` stalled
the Norelsys NS1081 bridge, the kernel reset the reader (`reset SuperSpeed USB
device`) and the probe's timeout killed the run — a self-inflicted "fault" the
classifier would have pinned on the card. USB card readers expose no SMART.

WHAT IT CANNOT TELL YOU: the card's own model/serial (the reader's strings are
all USB exposes) · remaining spare blocks (vendor-private) · reader-vs-card —
a flaky reader or dongle produces the same symptoms; run the same tier on a
known-good card in the same reader and pass it as --baseline to discriminate.

RECORDING A BASELINE CARD (a new, known-good card in the same reader): its
WRITE numbers are a fair baseline as-is. Its READ numbers are not until it has
been written — a virgin card's controller answers unmapped blocks without
touching the flash. Fill it first (`write --gib <free-2>`; that pass is also
f3's fake-capacity check), then `scan`. Pass the baseline's run.json to later
runs with --baseline; the comparison table lands in report_vs_baseline.md.

DESTRUCTIVE OPTION (not automated here — the card holds the rig's data): when
the card is about to be reflashed anyway, the strongest test is a whole-surface
write+verify:  badblocks -wsv -t random -b 4096 /dev/sdX   (~3–5 h / 256 GB)
or f3probe --destructive --time-ops /dev/sdX (fast, capacity-fraud oriented).

Usage:
  tools/card_doctor.py survey
  tools/card_doctor.py files  [--limit-gib N]
  sudo tools/card_doctor.py scan  [--verify outliers|full|none] [--keep-mounted]
  sudo tools/card_doctor.py write [--gib 8]
  tools/card_doctor.py report state/card_doctor/<runid>/run.json [--baseline ...]
Device auto-detects (the removable USB disk carrying the kit's labels); pass
--device /dev/sdX to override. Non-removable devices are refused.
"""
import argparse
import csv
import errno
import hashlib
import json
import math
import mmap
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

VERSION = "0.1.0"

# ----------------------------------------------------------------------------
# Thresholds — every number carries its basis. Tune here, nowhere else.
# ----------------------------------------------------------------------------
CHUNK_MIB_DEFAULT = 16       # latency resolution vs per-call overhead; a 200 ms
                             # ECC-retry stall doubles a ~190 ms chunk at 90 MB/s
LOCALIZE_STEP = 4096         # SD/flash ECC page granularity for bad-range lists
LOCALIZE_MAX_BAD = 256       # per chunk; beyond this a region is "extensive"
LOCALIZE_BUDGET_S = 180.0    # per chunk; USB timeouts are 30 s each
SLOW_X = 3.0                 # chunk time > SLOW_X * median  -> "slow"
STALL_X = 10.0               # chunk time > STALL_X * median -> "stall"
SLOW_PCT_DEGRADED = 1.0      # % slow chunks that flips DEGRADED (a healthy card
                             # is near-uniform: <0.2 % in practice)
STALLS_DEGRADED = 3          # isolated host hiccups happen; three multi-x stalls
                             # across the surface do not
RAND_READS_DEFAULT = 2000    # 4 KiB random reads for the latency percentiles
RAND_P99_MS_DEGRADED = 30.0  # healthy UHS-I over USB-BOT QD1: p99 ~2–6 ms
RAND_MAX_MS_DEGRADED = 500.0 # a half-second single read = retry storm
SEQ_READ_MBPS_WARN = 20.0    # UHS-I on a USB3 reader: 60–95 MB/s; below 20 is
                             # a reader/link or card problem — WARN, not verdict
SEQ_WRITE_MBPS_DEGRADED = 10.0   # Class 10 / U1 sustained-write floor
RAND_WRITE_IOPS_DEGRADED = 50    # A1 spec = 500 write IOPS; 10 % of spec
RAND_WRITE_P99_MS_DEGRADED = 1000.0  # >1 s 4 KiB write = GC starved of spares
VERIFY_EVERY_N = 32          # 'outliers' verify: every Nth chunk + the outliers
BIG_FILE_MIB = 8             # files >= this size count toward throughput stats
KIT_LABELS = ("ROCKNIX-GTK", "GTKSTOR", "ROCKNIX", "STORAGE")
WRITE_FSTYPES = ("ext4", "ext3", "ext2", "exfat", "vfat", "btrfs", "xfs", "f2fs")   # write-tier hosts

# Kernel-log line classes. FATAL = a fault this device produced during the run.
KLOG_FATAL = re.compile(
    r"I/O error|Buffer I/O|blk_update_request|critical (?:medium|target)|"
    r"Medium Error|Unrecovered read|Hardware Error|Sense Key|FAILED Result|"
    r"reset (?:SuperSpeed|high-speed|full-speed|low-speed) USB|"
    r"Device offlined|timing out command|rejecting I/O|Write Protect is on|"
    r"device descriptor read/64|not accepting address|"
    r"failed to (?:read|write)|Unable to enumerate|transfer canceled", re.I)
KLOG_NOISE = re.compile(r"clearing M3 reset", re.I)   # apple-dcp chatter

STOP = {"flag": False}
CHILD = {"proc": None}       # the helper currently running (f3, fio), so an interrupt can stop it


def _sigint(_sig, _frm):
    STOP["flag"] = True
    p = CHILD.get("proc")
    if p is not None and p.poll() is None:
        try:
            p.terminate()
        except OSError:
            pass
    eprint("\n[card_doctor] interrupt — stopping the current step, then writing a PARTIAL report")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def human_bytes(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def pct(sorted_vals, p):
    """Nearest-rank percentile of an ascending list (p in 0..100)."""
    if not sorted_vals:
        return None
    k = max(1, math.ceil(p / 100.0 * len(sorted_vals))) - 1
    return sorted_vals[min(k, len(sorted_vals) - 1)]


def run_cmd(argv, timeout=None, check=False, capture=True):
    """Run a helper; None when it is missing or hangs. A probe's hang must never
    take the run down with it (2026-09-04: smartctl stalled the USB bridge and
    its TimeoutExpired killed an 80-minute scan before anything was written)."""
    try:
        cp = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        eprint(f"[card_doctor] {argv[0]} timed out after {timeout}s — skipped")
        return None
    if check and cp.returncode != 0:
        raise RuntimeError(f"{argv[0]} rc={cp.returncode}: {cp.stderr.strip()}")
    return cp


# ----------------------------------------------------------------------------
# Device discovery
# ----------------------------------------------------------------------------
def lsblk_tree():
    cp = run_cmd(["lsblk", "-J", "-b", "-o",
                  "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,VENDOR,TRAN,RM,SERIAL,PTTYPE"])
    if cp is None or cp.returncode != 0:
        return []
    return json.loads(cp.stdout).get("blockdevices", [])


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _truthy(v):
    return v in (True, 1, "1", "true", "True")


def pick_device(tree, explicit=None, force=False):
    """Choose the card. Refuses non-removable devices unless force=True.
    Returns the lsblk disk node. Pure function over the lsblk tree (testable)."""
    disks = [d for d in tree if d.get("type") == "disk"]
    if explicit:
        for d in disks:
            if d.get("path") == explicit or f"/dev/{d.get('name')}" == explicit:
                if not _truthy(d.get("rm")) and not force:
                    raise SystemExit(f"{explicit} is not a removable device; refusing without --force-device")
                return d
        raise SystemExit(f"{explicit} is not a disk known to lsblk")
    cands = [d for d in disks if _truthy(d.get("rm")) and _int(d.get("size")) > 0
             and (d.get("tran") in ("usb", None) or force)]
    if not cands:
        raise SystemExit("no removable disk with media found; pass --device /dev/sdX")

    def kit_score(d):
        labels = {c.get("label") for c in d.get("children", []) or []}
        return len(labels & set(KIT_LABELS))
    cands.sort(key=kit_score, reverse=True)
    if len(cands) > 1 and kit_score(cands[0]) == kit_score(cands[1]):
        names = ", ".join(c.get("path") or c.get("name") for c in cands)
        raise SystemExit(f"several removable disks present ({names}); pass --device")
    return cands[0]


def pick_write_partition(partitions):
    """The data partition the write tier fills: the kit's GTKSTOR when present,
    otherwise the largest partition carrying a filesystem we can mount (a retail
    card ships one exFAT/FAT32 partition — a baseline card is exactly that).
    Pure function (testable). None when the card has no usable filesystem."""
    cands = [p for p in partitions if (p.get("fstype") or "") in WRITE_FSTYPES]
    if not cands:
        return None
    cands.sort(key=lambda p: (p.get("label") == "GTKSTOR", p.get("size") or 0), reverse=True)
    return cands[0]


def sysfs_block(devname):
    base = Path("/sys/block") / devname
    out = {}
    if not base.exists():
        return out
    keys = {"size_sectors": "size", "removable": "removable", "ro": "ro",
            "logical_block": "queue/logical_block_size",
            "physical_block": "queue/physical_block_size",
            "max_sectors_kb": "queue/max_sectors_kb", "scheduler": "queue/scheduler",
            "read_ahead_kb": "queue/read_ahead_kb", "nr_requests": "queue/nr_requests",
            "scsi_vendor": "device/vendor", "scsi_model": "device/model",
            "scsi_state": "device/state", "scsi_timeout_s": "device/timeout",
            "scsi_queue_depth": "device/queue_depth"}
    for k, rel in keys.items():
        try:
            out[k] = (base / rel).read_text().strip()
        except OSError:
            pass
    try:
        out["scsi_id"] = os.path.basename(os.path.realpath(base / "device"))
    except OSError:
        pass
    return out


def sysfs_stat(devname):
    """/sys/block/<dev>/stat -> dict (cumulative since device attach)."""
    try:
        f = (Path("/sys/block") / devname / "stat").read_text().split()
        names = ["rd_ios", "rd_merges", "rd_sectors", "rd_ticks_ms", "wr_ios", "wr_merges",
                 "wr_sectors", "wr_ticks_ms", "in_flight", "io_ticks_ms", "time_in_queue_ms"]
        return {n: int(v) for n, v in zip(names, f)}
    except (OSError, ValueError):
        return {}


def udev_props(devpath):
    cp = run_cmd(["udevadm", "info", "-q", "property", devpath])
    props = {}
    if cp and cp.returncode == 0:
        for line in cp.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
    return props


def usb_info(props):
    """Follow DEVPATH up to the USB device node: speed, product, port."""
    info = {}
    devpath = props.get("DEVPATH", "")
    m = re.search(r"/(usb\d+)/((?:\d+-[\d.]+)/)*?(\d+-[\d.]+)/\3:\d+\.\d+/", devpath)
    if not m:
        return info
    usb_id = m.group(3)
    base = Path("/sys/bus/usb/devices") / usb_id
    info["usb_id"] = usb_id
    for a in ("speed", "version", "manufacturer", "product", "serial", "bMaxPower", "idVendor", "idProduct"):
        try:
            info[a] = (base / a).read_text().strip()
        except OSError:
            pass
    sp = info.get("speed")
    info["link"] = {"480": "USB 2.0 High-Speed (480 Mb/s) — caps a UHS-I card at ~35–40 MB/s",
                    "5000": "USB 3.x SuperSpeed (5 Gb/s) — not the bottleneck for an SD card",
                    "10000": "USB 3.x SuperSpeed+ (10 Gb/s)"}.get(sp, f"{sp} Mb/s" if sp else "unknown")
    return info


# ----------------------------------------------------------------------------
# Kernel log
# ----------------------------------------------------------------------------
def klog_lines(since_epoch=None, tokens=(), until_epoch=None):
    """journalctl -k lines (unprivileged OK for wheel), filtered to this device's
    tokens (sd id, usb port, /dev name). Returns (lines, source_note)."""
    argv = ["journalctl", "-k", "-o", "short-iso", "--no-pager", "-q"]
    if since_epoch is not None:
        argv += ["--since", f"@{int(since_epoch)}"]
    if until_epoch is not None:
        argv += ["--until", f"@{int(until_epoch)}"]
    cp = run_cmd(argv, timeout=60)
    if cp is None or cp.returncode != 0:
        cp2 = run_cmd(["dmesg", "-T"], timeout=30)
        if cp2 is None or cp2.returncode != 0:
            return [], "kernel log unavailable (journalctl and dmesg both refused)"
        text, note = cp2.stdout, "dmesg -T (no timestamps filter)"
    else:
        text, note = cp.stdout, "journalctl -k"
    pats = [re.escape(t) for t in tokens if t]
    generic = r"usb-storage|I/O error|Buffer I/O|blk_update_request|Medium Error|Sense Key|Write Protect|offlined|timing out"
    rx = re.compile("|".join(pats + [generic]), re.I)
    return [ln for ln in text.splitlines() if rx.search(ln) and not KLOG_NOISE.search(ln)], note


def klog_classify(lines):
    fatal = [ln for ln in lines if KLOG_FATAL.search(ln)]
    return {"total": len(lines), "fatal": fatal}


# ----------------------------------------------------------------------------
# Raw reads (scan tier)
# ----------------------------------------------------------------------------
class DirectReader:
    """O_DIRECT reader over a block device (or a regular file for tests)."""

    def __init__(self, path, chunk):
        self.path = path
        self.chunk = chunk
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
        self.buf = mmap.mmap(-1, chunk)
        self.mv = memoryview(self.buf)

    def read(self, off, n):
        """Read n bytes at off into the aligned buffer; returns bytes read.
        Raises OSError on failure (errno.EIO for media errors)."""
        got = os.preadv(self.fd, [self.mv[:n]], off)
        return got

    def view(self, n):
        return self.mv[:n]

    def close(self):
        try:
            self.mv.release()
        except Exception:
            pass
        self.buf.close()
        os.close(self.fd)


def device_size(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        return os.lseek(fd, 0, os.SEEK_END)
    finally:
        os.close(fd)


def localize_bad(read_fn, off, n, step=LOCALIZE_STEP, mid=1 << 20, budget_s=LOCALIZE_BUDGET_S,
                 max_bad=LOCALIZE_MAX_BAD, clock=time.monotonic):
    """Narrow an EIO inside [off, off+n) to `step`-sized bad ranges.
    read_fn(off, n) must raise OSError(EIO) on a media error. Coarse pass at
    `mid`, fine pass at `step`. Bounded by time and count; returns
    (bad_ranges[(start,len)], status) with status in ok|capped|timeout."""
    t0 = clock()
    bad = []
    status = "ok"
    for o in range(off, off + n, mid):
        m = min(mid, off + n - o)
        try:
            read_fn(o, m)
            continue
        except OSError as e:
            if e.errno != errno.EIO:
                raise
        for so in range(o, o + m, step):
            s = min(step, o + m - so)
            try:
                read_fn(so, s)
            except OSError as e:
                if e.errno != errno.EIO:
                    raise
                bad.append((so, s))
                if len(bad) >= max_bad:
                    return merge_ranges(bad), "capped"
            if clock() - t0 > budget_s:
                return merge_ranges(bad), "timeout"
        if clock() - t0 > budget_s:
            status = "timeout"
            break
    return merge_ranges(bad), status


def merge_ranges(ranges):
    out = []
    for s, l in sorted(ranges):
        if out and out[-1][0] + out[-1][1] == s:
            out[-1] = (out[-1][0], out[-1][1] + l)
        else:
            out.append((s, l))
    return out


class Progress:
    def __init__(self, label, total, unit="B"):
        self.label, self.total, self.unit = label, total, unit
        self.t0 = time.monotonic()
        self.last = 0.0
        self.tty = sys.stderr.isatty()
        self.last_pct_print = -5

    def update(self, done, extra=""):
        now = time.monotonic()
        if now - self.last < 0.5 and done < self.total:
            return
        self.last = now
        el = now - self.t0
        rate = done / el if el > 0 else 0
        p = 100.0 * done / self.total if self.total else 100.0
        eta = (self.total - done) / rate if rate > 0 else 0
        rate_s = f"{rate / 1e6:6.1f} MB/s" if self.unit == "B" else f"{rate:6.1f}/s"
        line = f"{self.label} {p:5.1f}%  {rate_s}  elapsed {int(el)//60:02d}:{int(el)%60:02d}  eta {int(eta)//60:02d}:{int(eta)%60:02d}  {extra}"
        if self.tty:
            eprint("\r" + line + " " * 8, end="")
        elif p - self.last_pct_print >= 5 or done >= self.total:
            self.last_pct_print = p
            eprint(line)

    def done(self):
        if self.tty:
            eprint("")


def scan_surface(path, size, chunk, on_row=None, label="scan", start=0, end=None):
    """Sequential O_DIRECT pass over [start, end) (default: the whole device).
    Returns list of row dicts: idx (global chunk index), off, n, ms, sha (or ''),
    err (0/1), bad (list of [start,len]), loc_status."""
    end = size if end is None else end
    rd = DirectReader(path, chunk)
    rows = []
    prog = Progress(label, end - start)
    errs = 0
    try:
        for off in range(start, end, chunk):
            if STOP["flag"]:
                break
            idx = off // chunk
            n = min(chunk, end - off)
            t0 = time.perf_counter()
            try:
                got = rd.read(off, n)
                ms = (time.perf_counter() - t0) * 1000.0
                if got != n:
                    row = {"idx": idx, "off": off, "n": n, "ms": ms, "sha": "", "err": 1,
                           "bad": [], "loc_status": f"short read {got}/{n}"}
                    errs += 1
                else:
                    row = {"idx": idx, "off": off, "n": n, "ms": ms,
                           "sha": hashlib.sha256(rd.view(n)).hexdigest(), "err": 0, "bad": [], "loc_status": ""}
            except OSError as e:
                ms = (time.perf_counter() - t0) * 1000.0
                if e.errno != errno.EIO:
                    raise
                errs += 1
                bad, st = localize_bad(rd.read, off, n)
                row = {"idx": idx, "off": off, "n": n, "ms": ms, "sha": "", "err": 1,
                       "bad": [list(b) for b in bad], "loc_status": st}
                eprint(f"\n[card_doctor] EIO at chunk {idx} (offset {off}): {len(bad)} bad {LOCALIZE_STEP}-byte ranges ({st})")
            rows.append(row)
            if on_row:
                on_row(row)
            prog.update(off + n - start, extra=f"err {errs}")
    finally:
        prog.done()
        rd.close()
    return rows


def chunk_stats(rows):
    ok = [r for r in rows if not r["err"]]
    times = sorted(r["ms"] for r in ok)
    if not times:
        return {"n_ok": 0}
    med = statistics.median(times)
    slow = [r for r in ok if r["ms"] > SLOW_X * med]
    stalls = [r for r in ok if r["ms"] > STALL_X * med]
    total_bytes = sum(r["n"] for r in ok)
    total_ms = sum(r["ms"] for r in ok)
    # an all-zero chunk was never written (or was trimmed): the controller answers it
    # without touching flash, so it says nothing about the cells. Count them.
    zero_sha = {n: hashlib.sha256(bytes(n)).hexdigest() for n in {r["n"] for r in ok}}
    return {
        "n_ok": len(ok), "n_err": sum(1 for r in rows if r["err"]),
        "zero_chunks": sum(1 for r in ok if r["sha"] == zero_sha[r["n"]]),
        "median_ms": med, "p95_ms": pct(times, 95), "p99_ms": pct(times, 99), "max_ms": times[-1],
        "min_ms": times[0],
        "slow_count": len(slow), "slow_pct": 100.0 * len(slow) / len(ok),
        "stall_count": len(stalls),
        "stall_idx": [r["idx"] for r in stalls][:50],
        "seq_mbps_median": (ok[0]["n"] / 1e6) / (med / 1000.0) if med > 0 else None,
        "seq_mbps_overall": (total_bytes / 1e6) / (total_ms / 1000.0) if total_ms > 0 else None,
        "bytes_ok": total_bytes,
    }


def pick_verify_indices(rows, mode):
    ok = [r for r in rows if not r["err"]]
    if mode == "none" or not ok:
        return []
    if mode == "full":
        return [r["idx"] for r in ok]
    med = statistics.median(r["ms"] for r in ok)
    sel = {r["idx"] for r in ok if r["ms"] > SLOW_X * med}
    # slowest 2 % even if under SLOW_X — the suspects for ECC retries
    ranked = sorted(ok, key=lambda r: r["ms"], reverse=True)
    sel.update(r["idx"] for r in ranked[:max(1, len(ok) // 50)])
    sel.update(r["idx"] for r in ok if r["idx"] % VERIFY_EVERY_N == 0)
    return sorted(sel)


def verify_pass(path, rows, indices, chunk):
    by_idx = {r["idx"]: r for r in rows}
    rd = DirectReader(path, chunk)
    out = {"n": 0, "mismatch": [], "err": [], "persistent_slow": 0, "second_ms": {}}
    med = statistics.median(r["ms"] for r in rows if not r["err"]) if rows else 0
    prog = Progress("verify", len(indices), unit="chunks")
    try:
        for k, idx in enumerate(indices):
            if STOP["flag"]:
                break
            r = by_idx[idx]
            t0 = time.perf_counter()
            try:
                got = rd.read(r["off"], r["n"])
                ms = (time.perf_counter() - t0) * 1000.0
                sha = hashlib.sha256(rd.view(got)).hexdigest() if got == r["n"] else ""
            except OSError as e:
                if e.errno != errno.EIO:
                    raise
                out["err"].append(idx)
                continue
            out["n"] += 1
            out["second_ms"][idx] = ms
            if sha != r["sha"]:
                out["mismatch"].append({"idx": idx, "off": r["off"], "first": r["sha"], "second": sha})
                eprint(f"\n[card_doctor] HASH MISMATCH chunk {idx} offset {r['off']}: {r['sha'][:16]} vs {sha[:16]}")
            if med and r["ms"] > SLOW_X * med and ms > SLOW_X * med:
                out["persistent_slow"] += 1
            prog.update(k + 1, extra=f"mismatch {len(out['mismatch'])}")
    finally:
        prog.done()
        rd.close()
    return out


def random_read_probe(path, size, count, seed=1234, block=4096, start=0, end=None):
    end = size if end is None else end
    rd = DirectReader(path, block)
    rng = random.Random(seed)
    lat = []
    errs = 0
    prog = Progress("random-read", count, unit="reads")
    try:
        for i in range(count):
            if STOP["flag"]:
                break
            off = start + rng.randrange(0, (end - start - block) // block) * block
            t0 = time.perf_counter()
            try:
                rd.read(off, block)
            except OSError as e:
                if e.errno != errno.EIO:
                    raise
                errs += 1
                continue
            lat.append((time.perf_counter() - t0) * 1000.0)
            if i % 50 == 0:
                prog.update(i + 1)
        prog.update(count)
    finally:
        prog.done()
        rd.close()
    lat.sort()
    if not lat:
        return {"count": 0, "errors": errs}
    return {"count": len(lat), "errors": errs, "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95),
            "p99_ms": pct(lat, 99), "max_ms": lat[-1], "mean_ms": statistics.fmean(lat),
            "iops_qd1": 1000.0 / statistics.fmean(lat)}


# ----------------------------------------------------------------------------
# Filesystem-level read (files tier)
# ----------------------------------------------------------------------------
def read_file_timed(path, want_md5=False, bufsize=4 << 20):
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)   # measure the card, not the cache
        except OSError:
            pass
        h = hashlib.md5() if want_md5 else None
        total = 0
        t0 = time.perf_counter()
        while True:
            b = os.read(fd, bufsize)
            if not b:
                break
            total += len(b)
            if h:
                h.update(b)
        ms = (time.perf_counter() - t0) * 1000.0
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass
        return total, ms, (h.hexdigest() if h else None)
    finally:
        os.close(fd)


def load_md5_refs(mountpoint):
    """ROCKNIX ships X.md5 beside X ('<md5>  target/X'). Map basename -> md5."""
    refs = {}
    for p in Path(mountpoint).glob("*.md5"):
        try:
            for line in p.read_text(errors="replace").splitlines():
                m = re.match(r"^([0-9a-fA-F]{32})\s+\*?(.+)$", line.strip())
                if m:
                    refs[os.path.basename(m.group(2))] = (m.group(1).lower(), p.name)
        except OSError:
            pass
    return refs


def files_tier(mountpoints, limit_bytes=None):
    files = []
    walk_errors = []
    for mp in mountpoints:
        for root, dirs, names in os.walk(mp, onerror=lambda e: walk_errors.append(f"{e.filename}: {e.strerror}")):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for nm in names:
                p = os.path.join(root, nm)
                if os.path.islink(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError as e:
                    walk_errors.append(f"{p}: {e.strerror}")
                    continue
                files.append((p, st.st_size))
    files.sort()
    total = sum(s for _, s in files)
    refs = {}
    for mp in mountpoints:
        for k, v in load_md5_refs(mp).items():
            refs[os.path.join(mp, k)] = v
    prog = Progress("files", total)
    results, errs, md5_checks = [], [], []
    done = 0
    for p, sz in files:
        if STOP["flag"]:
            break
        if limit_bytes is not None and done >= limit_bytes:
            break
        want_md5 = p in refs
        try:
            n, ms, dig = read_file_timed(p, want_md5=want_md5)
        except OSError as e:
            errs.append({"path": p, "errno": e.errno, "error": e.strerror})
            if e.errno == errno.EIO:
                eprint(f"\n[card_doctor] EIO reading {p}")
            continue
        results.append({"path": p, "bytes": n, "ms": ms})
        if want_md5:
            ref, src = refs[p]
            md5_checks.append({"path": p, "ref_file": src, "expected": ref, "actual": dig, "match": dig == ref})
        done += n
        prog.update(done, extra=f"files {len(results)}/{len(files)} err {len(errs)}")
    prog.done()
    big = [r for r in results if r["bytes"] >= BIG_FILE_MIB << 20 and r["ms"] > 0]
    small = [r for r in results if r["bytes"] < 64 << 10]
    big_mbps = sorted((r["bytes"] / 1e6) / (r["ms"] / 1000.0) for r in big)
    small_ms = sorted(r["ms"] for r in small)
    sum_bytes = sum(r["bytes"] for r in results)
    sum_ms = sum(r["ms"] for r in results)
    for r in results:
        r["mbps"] = (r["bytes"] / 1e6) / (r["ms"] / 1000.0) if r["ms"] > 0 else None
    slowest_big = sorted(big, key=lambda r: r["mbps"])[:10]
    return {
        "n_files_seen": len(files), "n_files_read": len(results), "bytes_read": sum_bytes,
        "overall_mbps": (sum_bytes / 1e6) / (sum_ms / 1000.0) if sum_ms > 0 else None,
        "big_files": {"n": len(big), "mbps_median": pct(big_mbps, 50), "mbps_p10": pct(big_mbps, 10),
                      "mbps_min": big_mbps[0] if big_mbps else None, "mbps_max": big_mbps[-1] if big_mbps else None},
        "small_files": {"n": len(small), "ms_p50": pct(small_ms, 50), "ms_p99": pct(small_ms, 99),
                        "ms_max": small_ms[-1] if small_ms else None},
        "slowest_big": [{"path": r["path"], "mbps": r["mbps"], "bytes": r["bytes"]} for r in slowest_big],
        "read_errors": errs, "walk_errors": walk_errors[:200], "n_walk_errors": len(walk_errors),
        "md5_checks": md5_checks, "partial": STOP["flag"] or (limit_bytes is not None and done >= limit_bytes),
        "_results": results,
    }


# ----------------------------------------------------------------------------
# Write tier helpers (free-space, filesystem-level)
# ----------------------------------------------------------------------------
def parse_f3write(text):
    m = re.search(r"Average writing speed:\s*([\d.]+)\s*(\w+)/s", text)
    return {"mbps": _to_mbps(m.group(1), m.group(2)) if m else None}


def parse_f3read(text):
    def sectors(label):
        m = re.search(label + r".*?\((\d+) sectors\)", text)
        return int(m.group(1)) if m else None
    m = re.search(r"Average reading speed:\s*([\d.]+)\s*(\w+)/s", text)
    return {"ok_sectors": sectors(r"Data OK"), "lost_sectors": sectors(r"Data LOST"),
            "corrupted_sectors": sectors(r"Corrupted"), "changed_sectors": sectors(r"Slightly changed"),
            "overwritten_sectors": sectors(r"Overwritten"),
            "mbps": _to_mbps(m.group(1), m.group(2)) if m else None}


def _to_mbps(val, unit):
    v = float(val)
    u = unit.upper()
    return v * {"B": 1e-6, "KB": 1e-3, "MB": 1.0, "GB": 1e3}.get(u, 1.0)


def parse_fio_json(text, kind):
    j = json.loads(text)
    job = j["jobs"][0][kind]
    clat = job.get("clat_ns") or job.get("lat_ns") or {}
    pc = clat.get("percentile", {})

    def p(key):
        for k, v in pc.items():
            if abs(float(k) - key) < 1e-3:
                return v / 1e6
        return None
    return {"iops": job.get("iops"), "bw_mbps": (job.get("bw_bytes", 0) or job.get("bw", 0) * 1024) / 1e6,
            "p50_ms": p(50.0), "p99_ms": p(99.0), "max_ms": (clat.get("max") or 0) / 1e6,
            "mean_ms": (clat.get("mean") or 0) / 1e6}


def py_write_sample(dirpath, gib, block=4 << 20):
    """Fallback for f3: write `gib` files of 1 GiB seeded pseudo-random data with
    O_DIRECT, fsync, read back with O_DIRECT and compare. Returns dict."""
    base = os.urandom(block)
    written = 0
    t_write = 0.0
    bad_files = []
    buf = mmap.mmap(-1, block)
    mv = memoryview(buf)
    prog = Progress("write-sample", gib << 30)     # silence for 10+ minutes reads as a hang (2026-09-04)
    try:
        for i in range(gib):
            if STOP["flag"]:
                break
            p = os.path.join(dirpath, f"{i + 1}.cdw")
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o600)
            try:
                t0 = time.perf_counter()
                for k in range((1 << 30) // block):
                    mv[:] = base
                    mv[0:8] = (i * 1_000_003 + k).to_bytes(8, "little")
                    os.write(fd, mv)
                    if k % 16 == 0:
                        prog.update(written + (k + 1) * block, extra=f"file {i + 1}/{gib}")
                os.fsync(fd)
                t_write += time.perf_counter() - t0
                written += 1 << 30
                prog.update(written, extra=f"file {i + 1}/{gib}")
            finally:
                os.close(fd)
        prog.done()
        # verify
        t_read = 0.0
        read_bytes = 0
        rbuf = mmap.mmap(-1, block)
        rmv = memoryview(rbuf)
        prog = Progress("read-back", written)
        try:
            for i in range(gib):
                if STOP["flag"]:
                    break
                p = os.path.join(dirpath, f"{i + 1}.cdw")
                if not os.path.exists(p):
                    continue
                fd = os.open(p, os.O_RDONLY | os.O_DIRECT)
                try:
                    t0 = time.perf_counter()
                    for k in range((1 << 30) // block):
                        got = os.preadv(fd, [rmv], k * block)
                        expect_hdr = (i * 1_000_003 + k).to_bytes(8, "little")
                        if got != block or rmv[0:8] != expect_hdr or rmv[8:] != memoryview(base)[8:]:
                            bad_files.append({"file": p, "block": k})
                            break
                        if k % 16 == 0:
                            prog.update(read_bytes + (k + 1) * block, extra=f"file {i + 1}/{gib} bad {len(bad_files)}")
                    t_read += time.perf_counter() - t0
                    read_bytes += 1 << 30
                finally:
                    os.close(fd)
            prog.done()
        finally:
            rmv.release()
            rbuf.close()
    finally:
        mv.release()
        buf.close()
    return {"runner": "python", "gib": gib, "bytes_written": written,
            "write_mbps": (written / 1e6) / t_write if t_write else None,
            "read_mbps": (read_bytes / 1e6) / t_read if t_read else None,
            "bad_blocks": bad_files, "ok": not bad_files and written == gib << 30}


def py_random_write(filepath, seconds, block=4096, seed=99, dsync=True):
    """4 KiB random O_DIRECT writes inside an existing, fully written file; dsync adds
    O_DSYNC (each write durable before the next — the save-file case)."""
    size = os.path.getsize(filepath)
    fd = os.open(filepath, os.O_WRONLY | os.O_DIRECT | (os.O_DSYNC if dsync else 0))
    buf = mmap.mmap(-1, block)
    mv = memoryview(buf)
    rng = random.Random(seed)
    lat = []
    errs = 0
    t_begin = time.monotonic()
    t_end = t_begin + seconds
    prog = Progress("random-write", seconds, unit="s")
    try:
        while time.monotonic() < t_end and not STOP["flag"]:
            off = rng.randrange(0, size // block) * block
            mv[0:8] = off.to_bytes(8, "little")
            t0 = time.perf_counter()
            try:
                os.pwrite(fd, mv, off)
            except OSError as e:
                errs += 1
                if e.errno in (errno.EIO, errno.EROFS):
                    break
                raise
            lat.append((time.perf_counter() - t0) * 1000.0)
            if len(lat) % 50 == 0:
                prog.update(min(seconds, time.monotonic() - t_begin), extra=f"{len(lat)} writes")
        prog.done()
    finally:
        mv.release()
        buf.close()
        os.close(fd)
    lat.sort()
    if not lat:
        return {"runner": "python", "iops": 0, "errors": errs}
    return {"runner": "python", "iops": len(lat) / (sum(lat) / 1000.0), "p50_ms": pct(lat, 50),
            "p99_ms": pct(lat, 99), "max_ms": lat[-1], "mean_ms": statistics.fmean(lat), "errors": errs}


# ----------------------------------------------------------------------------
# Verdict
# ----------------------------------------------------------------------------
def compute_verdict(run):
    """Pure function over run.json content -> {level, reasons, warnings, notes}."""
    fails, degr, warns, notes = [], [], [], []
    tier = run.get("tier")
    k = run.get("kernel_log", {})
    if k.get("fatal"):
        fails.append(f"kernel reported {len(k['fatal'])} fault line(s) for this device during the run")
    sv = run.get("survey", {})
    if sv.get("write_protect_on"):
        fails.append("card reports Write Protect ON — the read-only lockout of an exhausted controller (or the physical switch on an adapter)")

    sc = run.get("scan")
    if sc:
        st = sc.get("stats", {})
        if st.get("n_err"):
            nbad = sum(len(r.get("bad", [])) for r in sc.get("error_rows", []))
            fails.append(f"{st['n_err']} chunk(s) returned uncorrectable read errors ({nbad} bad {LOCALIZE_STEP}-byte range(s) localized)")
        vf = sc.get("verify", {})
        if vf.get("mismatch"):
            fails.append(f"{len(vf['mismatch'])} chunk(s) read back DIFFERENT data on the second pass (silent corruption)")
        if vf.get("err"):
            fails.append(f"{len(vf['err'])} chunk(s) failed on the second pass after reading once")
        if st.get("n_ok"):
            if st["slow_pct"] > SLOW_PCT_DEGRADED:
                degr.append(f"{st['slow_pct']:.2f}% of chunks read slower than {SLOW_X:g}x the median ({st['slow_count']} chunks) — ECC-retry signature")
            if st["stall_count"] >= STALLS_DEGRADED:
                degr.append(f"{st['stall_count']} chunk(s) stalled beyond {STALL_X:g}x the median (max {st['max_ms']:.0f} ms vs median {st['median_ms']:.0f} ms)")
            if vf.get("persistent_slow", 0) >= STALLS_DEGRADED:
                degr.append(f"{vf['persistent_slow']} slow chunk(s) were slow again on re-read — persistent weak regions, not host noise")
            if st.get("seq_mbps_median") is not None and st["seq_mbps_median"] < SEQ_READ_MBPS_WARN:
                warns.append(f"sequential read {st['seq_mbps_median']:.1f} MB/s is below {SEQ_READ_MBPS_WARN:g} MB/s — check the reader/link before blaming the card")
        rr = sc.get("random_read", {})
        if rr.get("count"):
            if rr["p99_ms"] > RAND_P99_MS_DEGRADED:
                degr.append(f"4 KiB random-read p99 {rr['p99_ms']:.1f} ms (threshold {RAND_P99_MS_DEGRADED:g} ms)")
            elif rr["max_ms"] > RAND_MAX_MS_DEGRADED:
                degr.append(f"4 KiB random-read worst case {rr['max_ms']:.0f} ms (threshold {RAND_MAX_MS_DEGRADED:g} ms)")
            if rr.get("errors"):
                fails.append(f"{rr['errors']} random 4 KiB read(s) returned errors")
        for fs in sc.get("fsck", []):
            # A filesystem finding is never attributed to the medium by itself: the rig
            # powers off uncleanly all the time (dirty bit, orphan inodes). Only the raw
            # read evidence above can convict the card; fsck is a WARN with its output.
            if fs.get("rc") not in (None, 0):
                warns.append(f"fsck {fs['device']} ({fs['fstype']}) rc={fs['rc']} [{fs.get('severity')}]: "
                             f"{fs.get('summary', '')[:160]} — filesystem-level; may be an unclean shutdown, not media")
        fio = sc.get("foreign_io_mib")
        if fio is not None and fio > 64:
            notes.append(f"{fio:.0f} MiB of I/O not issued by this tool hit the device during the run — figures carry that noise")

    fl = run.get("files")
    if fl:
        if fl.get("read_errors"):
            eio = [e for e in fl["read_errors"] if e.get("errno") == errno.EIO]
            if eio:
                fails.append(f"{len(eio)} file(s) returned I/O errors when read")
            other = len(fl["read_errors"]) - len(eio)
            if other:
                notes.append(f"{other} file(s) unreadable for non-media reasons (permissions) — expected for root-only dirs")
        bad_md5 = [c for c in fl.get("md5_checks", []) if not c["match"]]
        for c in bad_md5:
            fails.append(f"{os.path.basename(c['path'])} does not match the shipped {c['ref_file']} reference — corrupted on card, or replaced after flashing (check mtimes)")
        bf = fl.get("big_files", {})
        if bf.get("n") and bf.get("mbps_median") is not None and bf["mbps_median"] < SEQ_READ_MBPS_WARN:
            warns.append(f"large-file read median {bf['mbps_median']:.1f} MB/s is below {SEQ_READ_MBPS_WARN:g} MB/s")
        if bf.get("n") and bf.get("mbps_min") is not None and bf.get("mbps_median") and bf["mbps_min"] < bf["mbps_median"] / STALL_X:
            degr.append(f"slowest large file read at {bf['mbps_min']:.1f} MB/s vs median {bf['mbps_median']:.1f} MB/s — a >{STALL_X:g}x stall region")
        ratio = fl.get("device_read_ratio")
        if ratio is not None and ratio < 0.8:
            warns.append(f"the device moved only {ratio:.0%} of the bytes read — part of this run was served from the page cache; throughput is overstated")
        elif ratio is not None and ratio > 1.3:
            notes.append(f"the device moved {ratio:.2f}x the bytes this tool read — other I/O (indexer, file manager) shared the card during the run")

    wr = run.get("write")
    if wr:
        s = wr.get("sample", {})
        if s.get("ok") is False or (s.get("lost_sectors") or 0) > 0 or (s.get("corrupted_sectors") or 0) > 0 \
                or (s.get("changed_sectors") or 0) > 0 or s.get("bad_blocks"):
            fails.append("test files did not read back as written (write failure / data loss / capacity fraud)")
        if s.get("write_error"):
            fails.append(f"write error: {s['write_error']}")
        if s.get("write_mbps") is not None and s["write_mbps"] < SEQ_WRITE_MBPS_DEGRADED:
            degr.append(f"sequential write {s['write_mbps']:.1f} MB/s is below the Class-10/U1 floor of {SEQ_WRITE_MBPS_DEGRADED:g} MB/s")
        rw = wr.get("random_write", {})
        if rw.get("invalid"):
            notes.append(f"random-write figure set aside as invalid: {rw['invalid']}")
            rw = {}
        if rw:
            if rw.get("errors"):
                fails.append(f"{rw['errors']} random 4 KiB write(s) failed")
            if rw.get("iops") is not None and rw["iops"] < RAND_WRITE_IOPS_DEGRADED:
                degr.append(f"4 KiB random-write {rw['iops']:.0f} IOPS (threshold {RAND_WRITE_IOPS_DEGRADED}; A1 spec 500)")
            if rw.get("p99_ms") is not None and rw["p99_ms"] > RAND_WRITE_P99_MS_DEGRADED:
                degr.append(f"4 KiB random-write p99 {rw['p99_ms']:.0f} ms — garbage collection starved of spare blocks")
        rws = wr.get("random_write_sync", {})
        if rws.get("errors"):
            fails.append(f"{rws['errors']} random 4 KiB write+fdatasync(s) failed")
        rrf = wr.get("random_read_file", {})
        if rrf.get("errors"):
            fails.append(f"{rrf['errors']} random 4 KiB read(s) of the written sample failed")
        if rrf.get("iops") and rrf["iops"] > 20000:
            notes.append("the random-read-on-file figure was answered by the filesystem or page cache, not the card (unwritten extents or a cached file) — ignore it")
        if wr.get("random_write_note"):
            notes.append(wr["random_write_note"])
        for key in ("random_write", "random_write_sync", "random_read_file"):
            if (wr.get(key) or {}).get("interrupted"):
                notes.append(f"{key.replace('_', ' ')} probe was interrupted by the operator — not measured")

    for n in run.get("notes") or []:
        notes.append(f"operator note (terminal, not machine-recorded): {n}")
    if run.get("error"):
        notes.append(f"the run ended with an internal error after stage '{run.get('checkpoint_stage')}' — results are what was measured before it")
    if run.get("rebuilt"):
        notes.append("rebuilt from chunks.csv: the verify / random-read / fsck stages were not recovered")
    partial = bool(run.get("partial"))
    if fails:
        level = "FAIL"
    elif degr:
        level = "DEGRADED"
    elif partial or tier == "survey":      # a survey measures nothing: it can only FAIL or stay open
        level = "INCONCLUSIVE"
    else:
        level = "HEALTHY"
    if level == "HEALTHY" and tier in ("scan", "files"):
        notes.append("read-only evidence: write-endurance exhaustion only shows under writes — run the `write` tier to close that gap")
    if tier == "survey":
        notes.append("survey is identity + history only; it measures nothing — run `files` (no root) or `scan` (root)")
    return {"level": level, "fails": fails, "degraded": degr, "reasons": fails + degr, "warnings": warns,
            "notes": notes, "partial": partial}


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
def surface_map(rows, buckets=64):
    """ASCII map of the device: one glyph per 1/64 of the surface.
    . normal  - >1.5x median  + >3x  # >10x  X error"""
    ok = [r for r in rows if not r["err"]]
    if not ok:
        return ""
    med = statistics.median(r["ms"] for r in ok)
    n = len(rows)
    out = []
    for b in range(buckets):
        seg = rows[b * n // buckets:(b + 1) * n // buckets] or rows[-1:]
        if any(r["err"] for r in seg):
            out.append("X")
            continue
        mx = max(r["ms"] for r in seg)
        out.append("#" if mx > STALL_X * med else "+" if mx > SLOW_X * med else "-" if mx > 1.5 * med else ".")
    return "".join(out)


def render_svg(rows, stats, path_out):
    ok = [r for r in rows if not r["err"]]
    if not ok:
        return
    med = stats["median_ms"]
    W, H, L, B = 1100, 300, 60, 40
    ymax = max(med * 12, stats["max_ms"] * 1.05)
    n = len(rows)

    def X(i):
        return L + (W - L - 10) * i / max(1, n - 1)

    def Y(ms):
        return H - B - (H - B - 10) * min(ms, ymax) / ymax
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="monospace" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<line x1="{L}" y1="{Y(0)}" x2="{W-10}" y2="{Y(0)}" stroke="#888"/>',
             f'<line x1="{L}" y1="{Y(med)}" x2="{W-10}" y2="{Y(med)}" stroke="#2a7" stroke-dasharray="4 3"/>',
             f'<text x="{L+4}" y="{Y(med)-3}" fill="#2a7">median {med:.0f} ms</text>',
             f'<line x1="{L}" y1="{Y(SLOW_X*med)}" x2="{W-10}" y2="{Y(SLOW_X*med)}" stroke="#e90" stroke-dasharray="4 3"/>',
             f'<text x="{L+4}" y="{Y(SLOW_X*med)-3}" fill="#e90">{SLOW_X:g}x slow</text>',
             f'<line x1="{L}" y1="{Y(STALL_X*med)}" x2="{W-10}" y2="{Y(STALL_X*med)}" stroke="#c22" stroke-dasharray="4 3"/>',
             f'<text x="{L+4}" y="{Y(STALL_X*med)-3}" fill="#c22">{STALL_X:g}x stall</text>',
             f'<text x="{L}" y="{H-8}" fill="#333">offset 0</text>',
             f'<text x="{W-120}" y="{H-8}" fill="#333">end of device</text>',
             f'<text x="4" y="14" fill="#333">ms/chunk</text>']
    pts = " ".join(f"{X(r['idx']):.1f},{Y(r['ms']):.1f}" for r in ok)
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#69c" stroke-width="0.7"/>')
    for r in ok:
        if r["ms"] > SLOW_X * med:
            col = "#c22" if r["ms"] > STALL_X * med else "#e90"
            parts.append(f'<circle cx="{X(r["idx"]):.1f}" cy="{Y(r["ms"]):.1f}" r="2.2" fill="{col}"/>')
    for r in rows:
        if r["err"]:
            parts.append(f'<line x1="{X(r["idx"]):.1f}" y1="{Y(0)}" x2="{X(r["idx"]):.1f}" y2="10" stroke="#000" stroke-width="1.5"/>')
    parts.append("</svg>")
    Path(path_out).write_text("\n".join(parts))


def fmt(v, spec=".1f", unit=""):
    if v is None:
        return "—"
    return f"{v:{spec}}{unit}"


def render_report(run, baseline=None):
    v = run["verdict"]
    L = []
    ident = run.get("identity", {})
    L.append(f"# Card Doctor — {run['tier']} — {ident.get('device', '?')}")
    L.append("")
    L.append(f"**Verdict: {v['level']}**" + ("  *(partial run)*" if v.get("partial") else ""))
    for r in v.get("fails", []):
        L.append(f"- FAIL: {r}")
    for r in v.get("degraded", []):
        L.append(f"- DEGRADED: {r}")
    for w in v["warnings"]:
        L.append(f"- WARN: {w}")
    for n in v["notes"]:
        L.append(f"- note: {n}")
    L.append("")
    L.append(f"Run `{run['runid']}` · started {run['started']} · finished {run.get('finished', '—')} · card_doctor {VERSION}")
    if run.get("card_label"):
        L.append(f"Card (operator-supplied label): **{run['card_label']}**")
    L.append("")
    L.append("## Identity (what USB exposes — the READER's strings, not the card's)")
    L.append("")
    L.append("| field | value |")
    L.append("|---|---|")
    for k in ("device", "size_bytes", "size_human", "reader", "usb_link", "usb_port", "scsi_id",
              "partition_table", "logical_block", "max_sectors_kb", "scheduler", "scsi_queue_depth"):
        if k in ident:
            L.append(f"| {k} | {ident[k]} |")
    for p in ident.get("partitions", []):
        L.append(f"| partition | {p['path']} {p.get('fstype', '')} `{p.get('label', '')}` {human_bytes(p['size'])} {p.get('mountpoint') or '(not mounted)'} |")
    L.append("")
    sc = run.get("scan")
    if sc:
        st = sc.get("stats", {})
        L.append("## Quick probe (no surface pass)" if sc.get("quick") else "## Full-surface read (raw, O_DIRECT)")
        L.append("")
        if sc.get("range"):
            L.append(f"Region scanned: **{sc['range']['gib']} GiB** of the device (a `--range` re-check, not a full pass).")
            L.append("")
        if sc.get("rebuilt_from"):
            L.append(f"Rebuilt from `{sc['rebuilt_from']}` after the original run crashed; verify / random-read / fsck results were not recovered.")
            L.append("")
        L.append("| metric | value |")
        L.append("|---|---|")
        if st.get("n_ok"):
            L.append(f"| chunks read OK / errored | {st.get('n_ok', 0)} / {st.get('n_err', 0)} (chunk {sc['chunk_bytes'] >> 20} MiB) |")
            L.append(f"| bytes read | {human_bytes(st.get('bytes_ok', 0))} of {human_bytes(ident.get('size_bytes', 0))} |")
            L.append(f"| sequential read, median chunk | {fmt(st.get('seq_mbps_median'), '.1f', ' MB/s')} |")
            L.append(f"| sequential read, whole pass | {fmt(st.get('seq_mbps_overall'), '.1f', ' MB/s')} |")
            L.append(f"| chunk time median / p95 / p99 / max | {fmt(st.get('median_ms'), '.0f')} / {fmt(st.get('p95_ms'), '.0f')} / {fmt(st.get('p99_ms'), '.0f')} / {fmt(st.get('max_ms'), '.0f')} ms |")
            L.append(f"| slow chunks (>{SLOW_X:g}x median) | {st.get('slow_count', 0)} ({fmt(st.get('slow_pct'), '.2f', '%')}) — DEGRADED above {SLOW_PCT_DEGRADED:g}% |")
            L.append(f"| stalls (>{STALL_X:g}x median) | {st.get('stall_count', 0)} — DEGRADED at {STALLS_DEGRADED}+ |")
            if st.get("zero_chunks") is not None:
                L.append(f"| all-zero chunks (never written / trimmed) | {st['zero_chunks']} of {st['n_ok']} — the rest hold data, current or stale, so every read was a real flash read |")
        vf = sc.get("verify")
        if vf is not None:
            L.append(f"| second-pass re-reads | {vf.get('n', 0)} chunks ({sc.get('verify_mode')}), {len(vf.get('mismatch', []))} hash mismatch, {len(vf.get('err', []))} errors, {vf.get('persistent_slow', 0)} persistently slow |")
        rr = sc.get("random_read", {})
        if rr.get("count"):
            L.append(f"| 4 KiB random read (QD1, n={rr['count']}) | p50 {rr['p50_ms']:.2f} · p95 {rr['p95_ms']:.2f} · p99 {rr['p99_ms']:.2f} · max {rr['max_ms']:.1f} ms · {rr['iops_qd1']:.0f} IOPS — DEGRADED at p99 > {RAND_P99_MS_DEGRADED:g} ms |")
        if sc.get("foreign_io_mib") is not None:
            L.append(f"| I/O on the device not issued by this tool | {sc['foreign_io_mib']:.0f} MiB |")
        L.append("")
        if st.get("n_ok"):
            L.append("Surface map (64 buckets, offset 0 → end): `.` normal · `-` >1.5x · `+` >3x · `#` >10x · `X` error")
            L.append("")
            L.append("```")
            L.append(sc.get("surface_map", ""))
            L.append("```")
        if sc.get("error_rows"):
            L.append("")
            L.append("### Unreadable ranges")
            for r in sc["error_rows"][:100]:
                rng = ", ".join(f"{s}+{l}" for s, l in r.get("bad", [])[:20]) or "(not localized)"
                L.append(f"- chunk {r['idx']} @ {r['off']}: {rng} [{r.get('loc_status')}]")
        for fs in sc.get("fsck", []):
            L.append("")
            L.append(f"### fsck -n {fs['device']} ({fs['fstype']}) → rc={fs['rc']}")
            L.append("```")
            L.append((fs.get("output") or fs.get("summary") or "").strip()[:3000])
            L.append("```")
    if run.get("notes") or run.get("error"):
        L.append("## Run notes")
        L.append("")
        for n in run.get("notes") or []:
            L.append(f"- operator note (terminal, not machine-recorded): {n}")
        if run.get("error"):
            L.append(f"- the run ended with an internal error after stage `{run.get('checkpoint_stage')}`; everything above was measured before it:")
            L.append("```")
            L.append(run["error"].strip()[-1500:])
            L.append("```")
        L.append("")
    fl = run.get("files")
    if fl:
        L.append("## Filesystem read of every readable file")
        L.append("")
        L.append("| metric | value |")
        L.append("|---|---|")
        L.append(f"| files seen / read | {fl['n_files_seen']} / {fl['n_files_read']} |")
        L.append(f"| bytes read | {human_bytes(fl['bytes_read'])} |")
        L.append(f"| overall throughput | {fmt(fl.get('overall_mbps'), '.1f', ' MB/s')} |")
        bf = fl["big_files"]
        L.append(f"| large files (>= {BIG_FILE_MIB} MiB): n / median / p10 / min / max | {bf['n']} / {fmt(bf['mbps_median'])} / {fmt(bf['mbps_p10'])} / {fmt(bf['mbps_min'])} / {fmt(bf['mbps_max'])} MB/s |")
        sf = fl["small_files"]
        L.append(f"| small files (< 64 KiB) open+read: n / p50 / p99 / max | {sf['n']} / {fmt(sf['ms_p50'], '.2f')} / {fmt(sf['ms_p99'], '.2f')} / {fmt(sf['ms_max'], '.1f')} ms |")
        L.append(f"| read errors (I/O) | {len([e for e in fl['read_errors'] if e.get('errno') == errno.EIO])} |")
        L.append(f"| unreadable (permissions etc.) | {len([e for e in fl['read_errors'] if e.get('errno') != errno.EIO])} + {fl.get('n_walk_errors', 0)} dirs |")
        for c in fl.get("md5_checks", []):
            L.append(f"| reference md5 {os.path.basename(c['path'])} ({c['ref_file']}) | {'MATCH' if c['match'] else 'MISMATCH'} {c['actual']} |")
        if fl.get("device_read_ratio") is not None:
            L.append(f"| device bytes moved / bytes read | {fl['device_read_ratio']:.2f} (≈1.0 = measured the card, not the cache) |")
        if fl.get("slowest_big"):
            L.append("")
            L.append("Slowest large files:")
            for r in fl["slowest_big"]:
                L.append(f"- {r['mbps']:.1f} MB/s  {human_bytes(r['bytes'])}  {r['path']}")
        for e in [e for e in fl["read_errors"] if e.get("errno") == errno.EIO][:50]:
            L.append(f"- EIO: {e['path']}")
        L.append("")
    wr = run.get("write")
    if wr:
        s = wr.get("sample", {})
        L.append("## Write sample (free space of the ext4 partition; files deleted afterwards)")
        L.append("")
        L.append("| metric | value |")
        L.append("|---|---|")
        L.append(f"| runner | {s.get('runner')} · {s.get('gib')} GiB in {wr.get('dir')} |")
        L.append(f"| sequential write | {fmt(s.get('write_mbps'), '.1f', ' MB/s')} — DEGRADED below {SEQ_WRITE_MBPS_DEGRADED:g} |")
        L.append(f"| read-back | {fmt(s.get('read_mbps'), '.1f', ' MB/s')} |")
        if "ok_sectors" in s:
            L.append(f"| f3read sectors ok / lost / corrupted / changed / overwritten | {s.get('ok_sectors')} / {s.get('lost_sectors')} / {s.get('corrupted_sectors')} / {s.get('changed_sectors')} / {s.get('overwritten_sectors')} |")
        elif "ok" in s:
            L.append(f"| verify | {'all blocks match' if s.get('ok') else 'MISMATCH ' + str(s.get('bad_blocks'))[:200]} |")
        rw = wr.get("random_write", {})
        if rw.get("invalid"):
            L.append(f"| 4 KiB random write | SET ASIDE — {rw['invalid']} (measured {fmt(rw.get('iops'), '.0f')} IOPS, not a card figure) |")
        elif rw.get("interrupted"):
            L.append("| 4 KiB random write, O_DIRECT, spec-comparable | interrupted by the operator — not measured |")
        elif rw:
            L.append(f"| 4 KiB random write, O_DIRECT, spec-comparable ({rw.get('runner')}) | {fmt(rw.get('iops'), '.0f')} IOPS · p50 {fmt(rw.get('p50_ms'), '.2f')} · p99 {fmt(rw.get('p99_ms'), '.1f')} · max {fmt(rw.get('max_ms'), '.0f')} ms — DEGRADED below {RAND_WRITE_IOPS_DEGRADED} IOPS or p99 > 1 s; A1 spec 500, A2 spec 2000 |")
        rws = wr.get("random_write_sync", {})
        if rws:
            L.append(f"| 4 KiB random write + fdatasync each, save-file style ({rws.get('runner')}) | {fmt(rws.get('iops'), '.0f')} IOPS · p50 {fmt(rws.get('p50_ms'), '.2f')} · p99 {fmt(rws.get('p99_ms'), '.1f')} · max {fmt(rws.get('max_ms'), '.0f')} ms — informational |")
        rrd = wr.get("random_read_file", {})
        if rrd:
            art = " — filesystem/cache artefact, not the card" if (rrd.get("iops") or 0) > 20000 else ""
            L.append(f"| 4 KiB random read on the written sample ({rrd.get('runner')}) | {fmt(rrd.get('iops'), '.0f')} IOPS · p99 {fmt(rrd.get('p99_ms'), '.2f')} ms{art} |")
        if wr.get("random_write_note"):
            L.append(f"| random probes | {wr['random_write_note']} |")
        L.append("")
    k = run.get("kernel_log", {})
    L.append("## Kernel log for this device")
    L.append("")
    L.append(f"{k.get('total', 0)} line(s) matched ({k.get('source', '')}); {len(k.get('fatal', []))} fault line(s).")
    for ln in k.get("fatal", [])[:40]:
        L.append(f"- `{ln}`")
    L.append("")
    if baseline:
        L.append("## Comparison with baseline run")
        L.append("")
        L.append(f"Baseline: `{baseline.get('runid')}` ({baseline.get('tier')}, verdict {baseline.get('verdict', {}).get('level')})")
        L.append("")
        L.append("| metric | this run | baseline |")
        L.append("|---|---|---|")
        for label, getter in (
                ("seq read median MB/s", lambda r: (r.get("scan") or {}).get("stats", {}).get("seq_mbps_median")),
                ("chunk p99 ms", lambda r: (r.get("scan") or {}).get("stats", {}).get("p99_ms")),
                ("slow chunk %", lambda r: (r.get("scan") or {}).get("stats", {}).get("slow_pct")),
                ("stalls", lambda r: (r.get("scan") or {}).get("stats", {}).get("stall_count")),
                ("random read p99 ms", lambda r: (r.get("scan") or {}).get("random_read", {}).get("p99_ms")),
                ("large-file read median MB/s", lambda r: (r.get("files") or {}).get("big_files", {}).get("mbps_median")),
                ("small-file p99 ms", lambda r: (r.get("files") or {}).get("small_files", {}).get("ms_p99")),
                ("seq write MB/s", lambda r: (r.get("write") or {}).get("sample", {}).get("write_mbps")),
                ("random write IOPS", lambda r: (r.get("write") or {}).get("random_write", {}).get("iops"))):
            a, b = getter(run), getter(baseline)
            if a is not None or b is not None:
                L.append(f"| {label} | {fmt(a, '.2f')} | {fmt(b, '.2f')} |")
        L.append("")
    L.append("## Reading this report")
    L.append("")
    L.append("- FAIL evidence (errors, mismatches, USB faults, write failures) is unambiguous: the card does not do its one job.")
    L.append("- DEGRADED evidence is statistical: healthy flash reads at a near-uniform rate; a widening tail of slow chunks is the controller retrying ECC on weak cells — the leading indicator before uncorrectable errors appear.")
    L.append("- A reader or dongle fault mimics a card fault. Discriminate with a known-good card in the same reader: run the same tier and pass `--baseline` to this report.")
    L.append("- Read-only tiers cannot see write-endurance exhaustion. A card can read perfectly and refuse writes tomorrow; the `write` tier is the check for that.")
    L.append("- A never-written (new) card answers reads of unmapped blocks from its controller without touching the flash, so its read speed and latency flatter it. For a like-for-like READ baseline, fill the card first (`write --gib <free-2>`, which is also f3's capacity-fraud check), then `scan`. Its WRITE figures are a fair baseline as they are.")
    L.append(f"- Destructive whole-surface write test (only when the card will be reflashed anyway): `badblocks -wsv -t random -b 4096 {ident.get('device', '/dev/sdX')}`.")
    L.append("")
    return "\n".join(L)


def print_card(run):
    v = run["verdict"]
    ident = run.get("identity", {})
    bar = "=" * 72
    print(bar)
    print(f"  CARD DOCTOR  ·  {run['tier']}  ·  {ident.get('device', '?')}  {ident.get('size_human', '')}  ·  {ident.get('reader', '')}")
    print(bar)
    tag = {"HEALTHY": "[PASS]", "DEGRADED": "[WARN]", "FAIL": "[FAIL]", "INCONCLUSIVE": "[----]"}[v["level"]]
    print(f"  {tag} VERDICT: {v['level']}" + ("  (partial run)" if v.get("partial") else ""))
    for r in v.get("fails", []):
        print(f"  [FAIL] {r}")
    for r in v.get("degraded", []):
        print(f"  [WARN] {r}")
    for w in v["warnings"]:
        print(f"  [WARN] {w}")
    for n in v["notes"]:
        print(f"  [NOTE] {n}")
    sc = run.get("scan")
    if sc:
        st = sc.get("stats", {})
        if st.get("n_ok"):
            print(f"  seq read {fmt(st.get('seq_mbps_median'), '.1f')} MB/s · chunk med/p99/max {fmt(st['median_ms'], '.0f')}/{fmt(st['p99_ms'], '.0f')}/{fmt(st['max_ms'], '.0f')} ms · slow {st['slow_count']} ({st['slow_pct']:.2f}%) · stalls {st['stall_count']} · errors {st['n_err']}")
            vf = sc.get("verify") or {}
            print(f"  verify {vf.get('n', 0)} chunks re-read, {len(vf.get('mismatch', []))} mismatch, {vf.get('persistent_slow', 0)} persistently slow" + (f" · {sc['range']['gib']} GiB range" if sc.get("range") else ""))
            print(f"  map  {sc.get('surface_map', '')}")
        rr = sc.get("random_read", {})
        if rr.get("count"):
            print(f"  4K random read p50/p99/max {rr['p50_ms']:.2f}/{rr['p99_ms']:.2f}/{rr['max_ms']:.1f} ms · {rr['iops_qd1']:.0f} IOPS")
        for fs in sc.get("fsck", []):
            print(f"  fsck {fs['device']} ({fs.get('fstype')}): rc={fs.get('rc')} {fs.get('summary', '')[:90]}")
    fl = run.get("files")
    if fl:
        bf = fl["big_files"]
        print(f"  files {fl['n_files_read']}/{fl['n_files_seen']} · {human_bytes(fl['bytes_read'])} · overall {fmt(fl.get('overall_mbps'), '.1f')} MB/s · large-file median {fmt(bf['mbps_median'])} min {fmt(bf['mbps_min'])} MB/s · EIO {len([e for e in fl['read_errors'] if e.get('errno') == errno.EIO])}")
        for c in fl.get("md5_checks", []):
            print(f"  reference md5 {os.path.basename(c['path']):<18} {'MATCH' if c['match'] else 'MISMATCH'}")
    wr = run.get("write")
    if wr:
        s = wr.get("sample", {})
        rw = wr.get("random_write", {})
        rws = wr.get("random_write_sync", {})
        print(f"  write {fmt(s.get('write_mbps'))} MB/s · read-back {fmt(s.get('read_mbps'))} MB/s · 4K randwrite {fmt(rw.get('iops'), '.0f')} IOPS p99 {fmt(rw.get('p99_ms'))} ms"
              + (f" · +fdatasync {fmt(rws.get('iops'), '.0f')} IOPS p50 {fmt(rws.get('p50_ms'))} ms" if rws else ""))
    k = run.get("kernel_log", {})
    print(f"  kernel log: {k.get('total', 0)} device lines, {len(k.get('fatal', []))} faults")
    print(bar)
    print(f"  report: {run['outdir']}/report.md")
    print(bar)


# ----------------------------------------------------------------------------
# Run plumbing
# ----------------------------------------------------------------------------
def repo_root():
    return Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------
# TREADWEAR — the wear table across every run of every card (SD cards are the
# tyres of a racing emulation rig: a consumable, measured, never guessed)
# ----------------------------------------------------------------------------
TREADWEAR_COLS = ["finished", "card_id", "card_label", "tier", "verdict", "seq_read_mbps", "slow_pct", "stalls",
                  "rand_read_p99_ms", "seq_write_mbps", "rand_write_iops", "rand_write_sync_p99_ms", "read_errors",
                  "mismatches", "runid"]


def card_id(ident):
    """A stable-enough identity for a card USB cannot name: the hash of its
    filesystem UUIDs and size. It changes when the card is reflashed — the
    operator label carries the human identity across that. (A native SD host,
    i.e. the rig itself, exposes the real CID/serial; that is the next piece.)"""
    uuids = sorted(p.get("uuid") or "" for p in ident.get("partitions", []) if p.get("uuid"))
    if not uuids:
        return "unknown"
    return hashlib.sha256("|".join(uuids + [str(ident.get("size_bytes", 0))]).encode()).hexdigest()[:12]


def treadwear_row(run):
    """One ledger row per run. Empty cell = that tier does not measure it."""
    sc = run.get("scan") or {}
    st = sc.get("stats") or {}
    rr = sc.get("random_read") or {}
    wr = run.get("write") or {}
    s = wr.get("sample") or {}
    rw = wr.get("random_write") or {}
    if rw.get("invalid"):
        rw = {}                                  # a set-aside figure never enters the trend
    rws = wr.get("random_write_sync") or {}
    fl = run.get("files") or {}

    def f(v, spec=".2f"):
        return "" if v is None else format(v, spec)
    seq_read = st.get("seq_mbps_median") if st.get("n_ok") else (fl.get("big_files") or {}).get("mbps_median")
    read_errors = st.get("n_err") if st.get("n_ok") else (
        len([e for e in fl.get("read_errors", []) if e.get("errno") == errno.EIO]) if fl else None)
    mism = len((sc.get("verify") or {}).get("mismatch", [])) if sc.get("verify") else None
    return [run.get("finished", ""), card_id(run.get("identity", {})), run.get("card_label", ""), run.get("tier", ""),
            (run.get("verdict") or {}).get("level", ""), f(seq_read, ".1f"), f(st.get("slow_pct")) if st.get("n_ok") else "",
            "" if not st.get("n_ok") else str(st.get("stall_count", "")), f(rr.get("p99_ms")), f(s.get("write_mbps"), ".1f"),
            f(rw.get("iops"), ".0f"), f(rws.get("p99_ms"), ".1f"), "" if read_errors is None else str(read_errors),
            "" if mism is None else str(mism), run.get("runid", "")]


def treadwear_table(base):
    """Every run.json under `base` (survey rows skipped — they measure nothing),
    oldest first. Returns (rows, skipped_dirs)."""
    rows, skipped = [], []
    for d in sorted(Path(base).glob("*/")):
        rj = d / "run.json"
        if not rj.exists():
            if (d / "chunks.csv").exists():
                skipped.append(f"{d.name} (crashed scan — run `rebuild {d}`)")
            continue
        try:
            run = json.loads(rj.read_text())
        except ValueError:
            skipped.append(f"{d.name} (unreadable run.json)")
            continue
        if run.get("tier") == "survey" or "verdict" not in run:
            continue
        rows.append(treadwear_row(run))
    rows.sort(key=lambda r: r[0])
    return rows, skipped


def treadwear_print(rows, skipped, vs=None):
    if not rows:
        print("TREADWEAR: no runs yet")
        return
    cards = {}
    for r in rows:
        cards.setdefault(r[1], []).append(r)
    print("=" * 100)
    print(f"  TREADWEAR — {len(rows)} run(s) over {len(cards)} card(s); a row per run, the tyre's wear over time")
    print("=" * 100)
    head = f"  {'finished':<20} {'tier':<6} {'verdict':<12} {'seqR':>6} {'slow%':>6} {'stl':>4} {'rrP99':>6} {'seqW':>6} {'rwIOPS':>7} {'rwsP99':>7} {'err':>4} {'mism':>4}"
    for cid, rs in cards.items():
        label = next((r[2] for r in reversed(rs) if r[2]), "") or "(no label)"
        print(f"  card {cid}  {label}")
        print(head)
        for r in rs:
            print(f"  {r[0][:19]:<20} {r[3]:<6} {r[4]:<12} {r[5]:>6} {r[6]:>6} {r[7]:>4} {r[8]:>6} {r[9]:>6} {r[10]:>7} {r[11]:>7} {r[12]:>4} {r[13]:>4}")
        print()
    if vs:
        base_rows = [r for r in rows if vs.lower() in (r[2] or "").lower() or r[1] == vs]
        if not base_rows:
            print(f"  --vs {vs!r}: no card matches that label or id")
        else:
            bid = base_rows[-1][1]

            def latest(cid_rows, idx):
                for r in reversed(cid_rows):
                    if r[idx] != "":
                        return r[idx]
                return ""
            print(f"  LATEST PER METRIC vs baseline card {bid} ({base_rows[-1][2]})")
            print(f"  {'card':<14} {'seqR':>6} {'slow%':>6} {'stl':>4} {'rrP99':>6} {'seqW':>6} {'rwIOPS':>7} {'rwsP99':>7}")
            for cid, rs in cards.items():
                print(f"  {cid:<14} " + " ".join(f"{latest(rs, i):>{w}}" for i, w in ((5, 6), (6, 6), (7, 4), (8, 6), (9, 6), (10, 7), (11, 7)))
                      + ("   <- baseline" if cid == bid else ""))
            print()
    for s in skipped:
        print(f"  skipped: {s}")
    print("  columns: seqR/seqW MB/s · slow% chunks >3x median · stl stalls >10x · rrP99 4 KiB random-read p99 ms · rwIOPS O_DIRECT 4 KiB random write · rwsP99 write+fdatasync p99 ms")


def treadwear_write_tsv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(TREADWEAR_COLS)
        w.writerows(rows)


def make_outdir(base, tier, devname):
    runid = f"{time.strftime('%Y%m%d-%H%M%S')}-{tier}-{devname}"
    d = Path(base) / runid
    d.mkdir(parents=True, exist_ok=True)
    return runid, d


def chown_to_operator(path):
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None or os.geteuid() != 0:
        return
    for p in [path] + [Path(r) / n for r, ds, fs in os.walk(path) for n in ds + fs]:
        try:
            os.chown(p, int(uid), int(gid))
        except OSError:
            pass
    # the parent state dir may have been created by root too
    try:
        os.chown(Path(path).parent, int(uid), int(gid))
    except OSError:
        pass


def gather_identity(disk, devpath):
    devname = disk.get("name") or os.path.basename(devpath)
    sb = sysfs_block(devname)
    props = udev_props(devpath)
    usb = usb_info(props)
    size = _int(disk.get("size")) or (int(sb.get("size_sectors", 0)) * 512)
    parts = []
    for c in disk.get("children", []) or []:
        mps = c.get("mountpoints") or ([c.get("mountpoint")] if c.get("mountpoint") else [])
        parts.append({"path": c.get("path") or f"/dev/{c.get('name')}", "fstype": c.get("fstype"),
                      "label": c.get("label"), "uuid": c.get("uuid"), "size": _int(c.get("size")),
                      "mountpoint": next((m for m in mps if m), None)})
    return {
        "device": devpath, "devname": devname, "size_bytes": size, "size_human": f"{size / 1e9:.2f} GB ({size / 2**30:.1f} GiB)",
        "reader": f"{(disk.get('vendor') or '').strip()} {(disk.get('model') or '').strip()} (USB {usb.get('idVendor', '?')}:{usb.get('idProduct', '?')} {usb.get('manufacturer', '')} {usb.get('product', '')})".strip(),
        "usb_link": usb.get("link", "unknown"), "usb_port": usb.get("usb_id"), "usb_serial": usb.get("serial"),
        "scsi_id": sb.get("scsi_id"), "partition_table": disk.get("pttype"),
        "logical_block": sb.get("logical_block"), "physical_block": sb.get("physical_block"),
        "max_sectors_kb": sb.get("max_sectors_kb"), "scheduler": sb.get("scheduler"),
        "scsi_queue_depth": sb.get("scsi_queue_depth"), "scsi_timeout_s": sb.get("scsi_timeout_s"),
        "partitions": parts, "udev_id_serial": props.get("ID_SERIAL"),
    }


def klog_tokens(ident):
    toks = [f"[{ident.get('devname')}]", f" {ident.get('devname')}:", f"sd {ident.get('scsi_id')}", f"scsi {ident.get('scsi_id')}"]
    if ident.get("usb_port"):
        toks.append(f"usb {ident['usb_port']}")
    return toks


def finish_run(run, outdir, baseline=None):
    run["finished"] = now_iso()
    run["partial"] = bool(run.get("partial") or STOP["flag"])
    run["verdict"] = compute_verdict(run)
    run["outdir"] = str(outdir)
    (outdir / "run.json").write_text(json.dumps(run, indent=1, default=str))
    (outdir / "report.md").write_text(render_report(run))
    if baseline:
        run["baseline_runid"] = baseline.get("runid")
        (outdir / "report_vs_baseline.md").write_text(render_report(run, baseline))
    chown_to_operator(outdir)
    print_card(run)
    if baseline:
        print(f"  comparison vs baseline {baseline.get('runid')}: {outdir}/report_vs_baseline.md")
    if run.get("tier") != "survey":
        print(f"  treadwear: this card is {card_id(run.get('identity', {}))} — `tools/card_doctor.py treadwear` for its wear over time")
    return run


def load_baseline(path):
    if not path:
        return None
    try:
        b = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"--baseline {path}: {e}")
    if b.get("tool") != "card_doctor":
        raise SystemExit(f"--baseline {path} is not a card_doctor run.json")
    return b


def need_root(tier, argv_hint):
    if os.geteuid() != 0:
        eprint(f"[card_doctor] `{tier}` opens the raw device and needs root. The operator runs it:\n\n    sudo {argv_hint}\n")
        sys.exit(2)


# ----------------------------------------------------------------------------
# Tiers
# ----------------------------------------------------------------------------
def tier_survey(args, disk, devpath, ident):
    survey = {}
    lines, note = klog_lines(None, klog_tokens(ident))
    survey["boot_history_lines"] = len(lines)
    survey["write_protect_on"] = any("Write Protect is on" in ln for ln in lines)
    survey["stat"] = sysfs_stat(ident["devname"])
    for p in ident["partitions"]:
        if p.get("mountpoint"):
            try:
                st = os.statvfs(p["mountpoint"])
                p["fs_size"] = st.f_frsize * st.f_blocks
                p["fs_free"] = st.f_frsize * st.f_bavail
                p["fs_used_pct"] = 100.0 * (1 - st.f_bavail / st.f_blocks) if st.f_blocks else None
            except OSError:
                pass
    tools = {t: bool(shutil.which(t)) for t in ("f3write", "f3read", "f3probe", "fio", "badblocks", "e2fsck", "fsck.fat", "fsck.exfat")}
    survey["tools_present"] = tools
    est_s = ident["size_bytes"] / 1e6 / 60.0   # 60 MB/s planning figure
    survey["scan_estimate"] = f"~{est_s / 60:.0f} min at 60 MB/s for one full read pass"
    return survey, lines, note


def unmount_partitions(ident):
    """Quiesce the card: unmount what the desktop auto-mounted. Returns the list
    of partition paths we unmounted (fsck may run on those). A busy mount is
    left alone and reported, never forced."""
    run_cmd(["sync"])
    unmounted = []
    for p in ident["partitions"]:
        if p.get("mountpoint"):
            cp = run_cmd(["umount", p["path"]], timeout=120)
            if cp is not None and cp.returncode == 0:
                unmounted.append(p["path"])
                p["unmounted_by_scan"] = True
            else:
                eprint(f"[card_doctor] could not unmount {p['path']} ({(cp.stderr if cp else '').strip()}) — continuing mounted; fsck skipped for it")
    return unmounted


def run_fsck(partitions, unmounted):
    """Read-only filesystem checks on partitions that are not mounted. Never
    repairs. e2fsck rc&4 / fsck.fat rc 1 = inconsistencies found (WARN-level in
    the verdict: an unclean rig power-off produces them without media damage)."""
    out = []
    for p in partitions:
        if p.get("mountpoint") and p["path"] not in unmounted:
            out.append({"device": p["path"], "fstype": p.get("fstype"), "rc": None, "summary": "skipped: mounted"})
            continue
        fst = p.get("fstype")
        if fst == "vfat" and shutil.which("fsck.fat"):
            cp = run_cmd(["fsck.fat", "-n", p["path"]], timeout=1800)
        elif fst in ("ext4", "ext3", "ext2") and shutil.which("e2fsck"):
            cp = run_cmd(["e2fsck", "-fn", p["path"]], timeout=3600)
        elif fst == "exfat" and shutil.which("fsck.exfat"):
            cp = run_cmd(["fsck.exfat", "-n", p["path"]], timeout=1800)
        else:
            out.append({"device": p["path"], "fstype": fst, "rc": None, "summary": "skipped: no checker"})
            continue
        if cp is None:
            out.append({"device": p["path"], "fstype": fst, "rc": None, "summary": "checker missing or timed out"})
            continue
        text = (cp.stdout or "") + (cp.stderr or "")
        rc = cp.returncode
        severity = "structural" if (fst and fst.startswith("ext") and rc & 4) or (fst == "vfat" and rc == 1) else "operational"
        out.append({"device": p["path"], "fstype": fst, "rc": rc, "severity": severity,
                    "summary": text.strip().splitlines()[-1] if text.strip() else "", "output": text[-6000:]})
    return out


def checkpoint(run, outdir, stage):
    """Persist what has been measured so far. Paid for on 2026-09-04: an 80-minute
    read+verify pass was lost when a later probe raised; nothing had been written."""
    run["checkpoint_stage"] = stage
    snap = dict(run, partial=True, finished=None)
    try:
        (outdir / "run.json").write_text(json.dumps(snap, indent=1, default=str))
    except OSError as e:
        eprint(f"[card_doctor] checkpoint {stage} not written: {e}")


def parse_range(spec, size, chunk):
    """'A-B' in GiB -> (start, end) byte offsets aligned to the chunk, clipped to
    the device. None -> the whole device."""
    if not spec:
        return 0, size
    m = re.match(r"^\s*([\d.]+)\s*-\s*([\d.]+)\s*$", spec)
    if not m:
        raise SystemExit(f"--range wants START-END in GiB, e.g. 175-222 (got {spec!r})")
    a, b = float(m.group(1)) * 2**30, float(m.group(2)) * 2**30
    start = int(a // chunk) * chunk
    end = min(size, int(math.ceil(b / chunk)) * chunk)
    if not (0 <= start < end):
        raise SystemExit(f"--range {spec} is empty or outside the device ({size / 2**30:.1f} GiB)")
    return start, end


def write_chunks_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "offset", "bytes", "ms", "sha256", "err", "bad_ranges", "loc_status"])
        for r in rows:
            w.writerow([r["idx"], r["off"], r["n"], f"{r['ms']:.3f}", r["sha"], r["err"],
                        ";".join(f"{s}+{l}" for s, l in r["bad"]), r["loc_status"]])


def load_chunks_csv(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"idx": int(r["idx"]), "off": int(r["offset"]), "n": int(r["bytes"]), "ms": float(r["ms"]),
                         "sha": r["sha256"], "err": int(r["err"]),
                         "bad": [tuple(int(x) for x in b.split("+")) for b in r["bad_ranges"].split(";") if b],
                         "loc_status": r["loc_status"]})
    return rows


def tier_scan(args, disk, devpath, ident, outdir, run):
    sc = {"chunk_bytes": args.chunk_mib << 20, "verify_mode": args.verify}
    run["scan"] = sc                       # partial results live in `run` from the first stage on
    size = ident["size_bytes"] if not args.allow_file else device_size(devpath)
    ident["size_bytes"] = size
    ident["size_human"] = f"{size / 1e9:.2f} GB ({size / 2**30:.1f} GiB)"
    start, end = parse_range(getattr(args, "range", None), size, sc["chunk_bytes"])
    sc["range"] = ({"start": start, "end": end, "gib": f"{start / 2**30:.1f}-{end / 2**30:.1f}"}
                   if (start, end) != (0, size) else None)
    unmounted = [] if (args.allow_file or args.keep_mounted) else unmount_partitions(ident)
    sc["unmounted"] = unmounted
    stat0 = sysfs_stat(ident["devname"]) if not args.allow_file else {}
    rows = scan_surface(devpath, size, sc["chunk_bytes"], start=start, end=end)
    sc["stats"] = chunk_stats(rows)
    sc["error_rows"] = [r for r in rows if r["err"]]
    sc["surface_map"] = surface_map(rows)
    write_chunks_csv(rows, outdir / "chunks.csv")
    if sc["stats"].get("n_ok"):
        render_svg(rows, sc["stats"], outdir / "latency.svg")
    checkpoint(run, outdir, "read-pass")
    if not STOP["flag"]:
        idx = pick_verify_indices(rows, args.verify)
        sc["verify"] = verify_pass(devpath, rows, idx, sc["chunk_bytes"]) if idx else {"n": 0, "mismatch": [], "err": [], "persistent_slow": 0}
        sc["verify"].pop("second_ms", None)
        checkpoint(run, outdir, "verify")
    if not STOP["flag"] and args.random_reads > 0 and end - start > 1 << 20:
        sc["random_read"] = random_read_probe(devpath, size, args.random_reads, start=start, end=end)
        checkpoint(run, outdir, "random-read")
    if not args.allow_file:
        stat1 = sysfs_stat(ident["devname"])
        if stat0 and stat1:
            dev_bytes = (stat1["rd_sectors"] - stat0["rd_sectors"]) * 512
            ours = sc["stats"].get("bytes_ok", 0) + sum(r["n"] for r in sc["error_rows"]) \
                + sc.get("verify", {}).get("n", 0) * sc["chunk_bytes"] + sc.get("random_read", {}).get("count", 0) * 4096
            sc["foreign_io_mib"] = max(0.0, (dev_bytes - ours) / 2**20)
        # NO smartctl here, deliberately: on 2026-09-04 `smartctl -d scsi` stalled the
        # Norelsys NS1081 bridge, the kernel reset the reader, and the probe's timeout
        # killed the run after 80 minutes. USB card readers expose no SMART anyway.
        if not args.skip_fsck:
            sc["fsck"] = run_fsck(ident["partitions"], unmounted)
            checkpoint(run, outdir, "fsck")
    return sc


def tier_quick(args, disk, devpath, ident, outdir, run):
    """Minutes, root: 4 KiB random-read percentiles + read-only fsck. Fills the
    two holes a crashed scan leaves, or sanity-checks a card before a long pass."""
    sc = {"quick": True, "chunk_bytes": 0, "verify_mode": "none", "stats": {"n_ok": 0}, "error_rows": [], "surface_map": ""}
    run["scan"] = sc
    size = ident["size_bytes"]
    unmounted = [] if args.keep_mounted else unmount_partitions(ident)
    sc["unmounted"] = unmounted
    sc["random_read"] = random_read_probe(devpath, size, args.random_reads)
    checkpoint(run, outdir, "random-read")
    if not args.skip_fsck:
        sc["fsck"] = run_fsck(ident["partitions"], unmounted)
        checkpoint(run, outdir, "fsck")
    return sc


RUNID_RX = re.compile(r"^(\d{8})-(\d{6})-scan(?:-r[\d.]+-[\d.]+)?-([A-Za-z0-9_.]+)$")


def tier_rebuild(args, baseline):
    """Reconstruct a scan run's run.json/report from its chunks.csv after a crash.
    What the later stages measured (verify, random-read, fsck) is NOT recovered;
    --note records what the operator saw on the terminal, labelled as such."""
    src = Path(args.outdir)
    csvp = src / "chunks.csv"
    if not csvp.exists():
        raise SystemExit(f"{csvp} not found")
    rows = load_chunks_csv(csvp)
    if not rows:
        raise SystemExit(f"{csvp} is empty")
    m = RUNID_RX.match(src.name)
    devname = m.group(3) if m else "unknown"
    started_epoch = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else None
    csv_end = csvp.stat().st_mtime
    try:
        disk = pick_device(lsblk_tree(), args.device or (f"/dev/{devname}" if devname != "unknown" else None), args.force_device)
        ident = gather_identity(disk, disk.get("path") or f"/dev/{disk['name']}")
    except SystemExit:
        ident = {"device": f"/dev/{devname}", "devname": devname, "size_bytes": sum(r["n"] for r in rows),
                 "size_human": "", "reader": "(device not present at rebuild time)", "partitions": []}
    ident["card_label"] = args.card_label or ""
    chunk = max(r["n"] for r in rows)
    sc = {"chunk_bytes": chunk, "verify_mode": "not recovered (rebuilt from chunks.csv)", "stats": chunk_stats(rows),
          "error_rows": [r for r in rows if r["err"]], "surface_map": surface_map(rows),
          "verify": {"n": 0, "mismatch": [], "err": [], "persistent_slow": 0}, "rebuilt_from": str(csvp)}
    run = {"tool": "card_doctor", "version": VERSION, "tier": "scan", "runid": src.name + "-rebuilt",
           "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_epoch)) if started_epoch else "?",
           "host": os.uname().nodename, "kernel": os.uname().release, "identity": ident, "card_label": args.card_label or "",
           "notes": list(args.note or []), "rebuilt": True, "partial": True, "scan": sc}
    lines, note = klog_lines(started_epoch - 2 if started_epoch else None, klog_tokens(ident), until_epoch=csv_end + 1)
    run["kernel_log"] = dict(klog_classify(lines), source=f"{note}, read-pass window (start to chunks.csv mtime)")
    outdir = src if os.access(src, os.W_OK) else src.with_name(src.name + "-rebuilt")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "kernel.log").write_text("\n".join(lines))
    if sc["stats"].get("n_ok"):
        render_svg(rows, sc["stats"], outdir / "latency.svg")
    finish_run(run, outdir, baseline)
    return run


def run_child(argv, echo=False, timeout=None):
    """Run a long helper (f3, fio) in its OWN session, registered in CHILD so the
    tool's SIGINT handler stops it deliberately — the terminal's Ctrl-C no longer
    reaches the child directly, which used to make an interrupted fio job read as
    a failed write (2026-09-04). echo=True streams stdout+stderr live to stderr
    (f3's \\r progress) while collecting it; echo=False keeps stdout and stderr
    apart (fio's JSON). Returns (rc, out, err); rc None = binary missing, -9 = hung
    past `timeout`."""
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT if echo else subprocess.PIPE, start_new_session=True)
    except FileNotFoundError:
        return None, "", ""
    CHILD["proc"] = proc
    out, err = "", ""
    try:
        if echo:
            chunks = []
            while True:
                b = os.read(proc.stdout.fileno(), 4096)
                if not b:
                    break
                chunks.append(b)
                try:
                    sys.stderr.buffer.write(b)
                except AttributeError:            # stderr redirected to a text buffer (tests)
                    sys.stderr.write(b.decode("utf-8", "replace"))
                sys.stderr.flush()
            rc = proc.wait(timeout=timeout)
            out = b"".join(chunks).decode("utf-8", "replace")
        else:
            o, e = proc.communicate(timeout=timeout)
            rc = proc.returncode
            out, err = o.decode("utf-8", "replace"), e.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = -9
    finally:
        CHILD["proc"] = None
    return rc, out, err


def run_streaming(argv, timeout=None):
    rc, out, _ = run_child(argv, echo=True, timeout=timeout)
    return rc, out


def fio_target(tdir):
    """A FULLY WRITTEN 1 GiB file for the random-I/O probes: the first sample file
    (f3's 1.h2w or the Python 1.cdw). fio's own fallocate'd scratch file has
    unwritten extents: the first write to each costs an extent conversion plus a
    journal commit, and reads of them are answered by the filesystem, never the
    card (2026-09-04: 3.7 write IOPS and 366k read IOPS — both artefacts).
    None when no sample file exists."""
    for name in ("1.h2w", "1.cdw"):
        p = os.path.join(tdir, name)
        if os.path.exists(p) and os.path.getsize(p) >= 1 << 30:
            return p
    return None


def fio_job(target, rw, seconds, extra=()):
    argv = ["fio", "--name=cd", f"--filename={target}", "--size=1G", "--bs=4k", "--direct=1", "--ioengine=psync",
            "--iodepth=1", "--time_based", f"--runtime={seconds}", "--output-format=json", "--randrepeat=1",
            "--allow_file_create=0", f"--rw={rw}", *extra]
    rc, out, err = run_child(argv, timeout=seconds * 4 + 120)
    if rc is None or rc == -9:
        return {"runner": "fio", "errors": 1, "raw": "fio missing or timed out"}
    if STOP["flag"] and rc != 0:
        return {"runner": "fio", "interrupted": True}          # stopped by the operator, not a failed write
    try:
        return dict(parse_fio_json(out, "write" if "write" in rw else "read"), runner="fio",
                    errors=0 if rc == 0 else 1)
    except (ValueError, KeyError):
        return {"runner": "fio", "errors": 1, "raw": (out + err)[-1000:]}


def tier_write(args, disk, devpath, ident, outdir, run):
    part = pick_write_partition(ident["partitions"])
    if part is None:
        raise SystemExit(f"write tier needs a mountable data partition ({', '.join(WRITE_FSTYPES)}); "
                         f"the card shows {[p.get('fstype') for p in ident['partitions']] or 'no partitions'}")
    mounted_here = False
    mp = part.get("mountpoint")
    if not mp:
        mp = f"/run/card_doctor/{part.get('label') or os.path.basename(part['path'])}"
        os.makedirs(mp, exist_ok=True)
        run_cmd(["mount", "-o", "rw,noatime", part["path"], mp], check=True)   # fstype auto-detected
        mounted_here = True
    wr = {"partition": part["path"], "fstype": part.get("fstype"), "mountpoint": mp, "mounted_by_tool": mounted_here}
    run["write"] = wr
    tdir = os.path.join(mp, ".card_doctor")
    os.makedirs(tdir, exist_ok=True)
    wr["dir"] = tdir
    st = os.statvfs(mp)
    free = st.f_frsize * st.f_bavail
    need = (args.gib + 2) << 30
    if free < need:
        raise SystemExit(f"only {human_bytes(free)} free on {mp}; need {human_bytes(need)} for a {args.gib} GiB sample (--gib smaller)")
    use_f3 = bool(shutil.which("f3write") and shutil.which("f3read")) and not args.python_only
    use_fio = bool(shutil.which("fio")) and not args.python_only
    try:
        eprint(f"[card_doctor] write sample: {args.gib} GiB into {tdir} ({'f3write/f3read' if use_f3 else 'python'}) — "
               f"~{args.gib * 1024 / 10 / 60:.0f} min to write at the Class-10 floor of 10 MB/s, ~{args.gib * 1024 / 30 / 60:.0f} min at V30, then the read-back")
        if use_f3:
            rc, text = run_streaming(["f3write", f"--end-at={args.gib}", tdir])
            sample = {"runner": "f3", "gib": args.gib, "f3write_rc": rc, "write_mbps": parse_f3write(text)["mbps"]}
            if rc != 0:
                sample["write_error"] = text.strip()[-300:]
            eprint("\n[card_doctor] f3read (read back and verify)")
            rc, text = run_streaming(["f3read", tdir])
            r = parse_f3read(text)
            sample.update({"f3read_rc": rc, "read_mbps": r.pop("mbps")})
            sample.update(r)
            sample["ok"] = rc == 0 and all((r.get(k) or 0) == 0 for k in ("lost_sectors", "corrupted_sectors", "changed_sectors", "overwritten_sectors"))
            sample["f3_output"] = text[-3000:]
            eprint("")
        else:
            sample = py_write_sample(tdir, args.gib)
        wr["sample"] = sample
        checkpoint(run, outdir, "write-sample")
        target = fio_target(tdir)
        if STOP["flag"]:
            pass
        elif target is None:
            wr["random_write_note"] = "no fully written 1 GiB sample file survived the write phase — random probes skipped"
        else:
            s = args.fio_seconds
            half = max(10, s // 2)
            eprint(f"[card_doctor] random 4 KiB probes on {os.path.basename(target)} ({'fio' if use_fio else 'python'}): "
                   f"{s} s write (O_DIRECT, spec-comparable) + {half} s write+fdatasync (save-file style) + {half} s read")

            def py_read():
                rr = random_read_probe(target, os.path.getsize(target), 2000)
                return {"runner": "python", "iops": rr.get("iops_qd1"), "p50_ms": rr.get("p50_ms"),
                        "p99_ms": rr.get("p99_ms"), "max_ms": rr.get("max_ms"), "errors": rr.get("errors", 0)}
            jobs = [("random_write", (lambda: fio_job(target, "randwrite", s)) if use_fio else (lambda: py_random_write(target, s, dsync=False))),
                    ("random_write_sync", (lambda: fio_job(target, "randwrite", half, ["--fdatasync=1"])) if use_fio else (lambda: py_random_write(target, half, dsync=True))),
                    ("random_read_file", (lambda: fio_job(target, "randread", half)) if use_fio else py_read)]
            for key, job in jobs:
                if STOP["flag"]:
                    wr[key] = {"interrupted": True}
                    continue
                eprint(f"[card_doctor]   {key.replace('_', ' ')} ...")
                wr[key] = job()
                checkpoint(run, outdir, key.replace("_", "-"))
    finally:
        if not args.keep_files:
            shutil.rmtree(tdir, ignore_errors=True)
            run_cmd(["sync"])
        if mounted_here:
            run_cmd(["umount", mp], timeout=300)
    return wr


def main(argv=None):
    ap = argparse.ArgumentParser(prog="card_doctor.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"card_doctor {VERSION}")
    sub = ap.add_subparsers(dest="tier", required=True)

    def common(p):
        p.add_argument("--device", help="/dev/sdX (default: auto — the removable USB disk carrying the kit's labels)")
        p.add_argument("--force-device", action="store_true", help="allow a non-removable device (dangerous: never point this at the system disk without reason)")
        p.add_argument("--out", default=str(repo_root() / "state" / "card_doctor"), help="output base dir (default state/card_doctor)")
        p.add_argument("--card-label", default="", help="the label printed on the card (SanDisk Extreme 256GB A2 V30 ...) — recorded in the report")
        p.add_argument("--baseline", help="run.json of the same tier on a known-good card in the same reader; renders report_vs_baseline.md at the end")
    for name in ("survey", "files", "scan", "quick", "write"):
        p = sub.add_parser(name)
        common(p)
        if name == "files":
            p.add_argument("--limit-gib", type=float, default=None, help="stop after this many GiB (default: all readable files)")
        if name == "scan":
            p.add_argument("--chunk-mib", type=int, default=CHUNK_MIB_DEFAULT)
            p.add_argument("--verify", choices=("outliers", "full", "none"), default="outliers")
            p.add_argument("--range", help="only this region, START-END in GiB (e.g. 175-222) — re-check a slow region without a full pass")
            p.add_argument("--allow-file", action="store_true", help=argparse.SUPPRESS)   # tests: scan a regular file
        if name in ("scan", "quick"):
            p.add_argument("--random-reads", type=int, default=RAND_READS_DEFAULT)
            p.add_argument("--keep-mounted", action="store_true", help="do not unmount the card's partitions (fsck is then skipped)")
            p.add_argument("--skip-fsck", action="store_true")
        if name == "write":
            p.add_argument("--gib", type=int, default=8, help="GiB of test files to write into free space (default 8)")
            p.add_argument("--fio-seconds", type=int, default=60)
            p.add_argument("--keep-files", action="store_true")
            p.add_argument("--python-only", action="store_true", help="ignore f3/fio even if installed")
    rp = sub.add_parser("report")
    rp.add_argument("run_json")
    rp.add_argument("--baseline", help="another run.json to compare against (a known-good card in the same reader)")
    rp.add_argument("--card-label", help="set/correct the card label recorded in this run (rewrites run.json)")
    tw = sub.add_parser("treadwear", help="the wear table: one row per run per card, oldest first (TSV alongside)")
    tw.add_argument("--out", default=str(repo_root() / "state" / "card_doctor"))
    tw.add_argument("--vs", help="baseline card: a label substring or card_id; prints every card's latest metrics beside it")
    rb = sub.add_parser("rebuild", help="reconstruct a crashed scan's run.json/report from its chunks.csv")
    rb.add_argument("outdir")
    rb.add_argument("--device", help="the card's device if it is still present (default: from the run dir name)")
    rb.add_argument("--force-device", action="store_true")
    rb.add_argument("--card-label", default="")
    rb.add_argument("--baseline")
    rb.add_argument("--note", action="append", help="what the operator saw on the terminal for the lost stages (repeatable)")
    args = ap.parse_args(argv)

    if args.tier == "treadwear":
        rows, skipped = treadwear_table(args.out)
        treadwear_print(rows, skipped, vs=args.vs)
        if rows:
            tsv = Path(args.out) / "treadwear.tsv"
            treadwear_write_tsv(rows, tsv)
            print(f"  table: {tsv}")
        return 0

    if args.tier == "report":
        run = json.loads(Path(args.run_json).read_text())
        base = load_baseline(args.baseline)
        if args.card_label is not None:
            run["card_label"] = args.card_label
            run.setdefault("identity", {})["card_label"] = args.card_label
        run["verdict"] = compute_verdict(run)
        Path(args.run_json).write_text(json.dumps(run, indent=1, default=str))   # run.json is the record: keep its verdict current
        text = render_report(run, base)
        out = Path(args.run_json).with_name("report.md" if not base else "report_vs_baseline.md")
        out.write_text(text)
        print_card(run)
        print(f"  wrote {out}")
        return 0

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)
    baseline = load_baseline(getattr(args, "baseline", None))   # fail early on a bad path, not after an hour

    if args.tier == "rebuild":
        run = tier_rebuild(args, baseline)
        return 0 if run["verdict"]["level"] in ("HEALTHY", "INCONCLUSIVE") else 1

    if "<" in (args.card_label or ""):
        eprint(f"[card_doctor] --card-label still contains a placeholder ({args.card_label!r}); recorded as-is — fix later with: "
               f"tools/card_doctor.py report <run.json> --card-label \"...\"")

    if args.tier == "scan" and getattr(args, "allow_file", False):
        devpath = args.device
        disk = {"name": Path(devpath).name, "path": devpath, "size": device_size(devpath), "children": [], "model": "regular-file", "vendor": ""}
        ident = {"device": devpath, "devname": Path(devpath).name, "size_bytes": disk["size"], "size_human": "", "reader": "regular file (test mode)", "partitions": []}
    else:
        tree = lsblk_tree()
        disk = pick_device(tree, args.device, args.force_device)
        devpath = disk.get("path") or f"/dev/{disk['name']}"
        if args.tier in ("scan", "quick", "write"):
            need_root(args.tier, " ".join(["tools/card_doctor.py"] + (argv if argv is not None else sys.argv[1:])))
        ident = gather_identity(disk, devpath)
    ident["card_label"] = args.card_label
    tag = args.tier
    if args.tier == "scan" and getattr(args, "range", None):
        tag += "-r" + re.sub(r"[^0-9.]+", "-", args.range.strip()).strip("-")
    runid, outdir = make_outdir(args.out, tag, ident["devname"])
    t_start = time.time()
    run = {"tool": "card_doctor", "version": VERSION, "tier": args.tier, "runid": runid, "started": now_iso(),
           "host": os.uname().nodename, "kernel": os.uname().release, "identity": ident, "card_label": args.card_label,
           "args": {k: v for k, v in vars(args).items() if k not in ("tier",)}}
    eprint(f"[card_doctor] {args.tier} on {devpath} ({ident.get('size_human')}) -> {outdir}")

    if args.tier == "survey":
        survey, lines, note = tier_survey(args, disk, devpath, ident)
        run["survey"] = survey
        run["kernel_log"] = dict(klog_classify(lines), source=f"{note}, since boot")
        (outdir / "kernel.log").write_text("\n".join(lines))
        finish_run(run, outdir, baseline)
        return 0

    try:
        if args.tier == "files":
            mps = [p["mountpoint"] for p in ident["partitions"] if p.get("mountpoint")]
            if not mps:
                raise SystemExit("no partition of the card is mounted; mount it (file manager / udisksctl mount -b /dev/sdX2) and re-run")
            stat0 = sysfs_stat(ident["devname"])
            fl = files_tier(mps, int(args.limit_gib * 2**30) if args.limit_gib else None)
            results = fl.pop("_results")
            with open(outdir / "files.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["path", "bytes", "ms", "mbps"])
                for r in results:
                    w.writerow([r["path"], r["bytes"], f"{r['ms']:.3f}", f"{r['mbps']:.2f}" if r.get("mbps") else ""])
            stat1 = sysfs_stat(ident["devname"])
            if stat0 and stat1 and fl["bytes_read"]:
                # honesty check: the device should have moved about what we read. Much less =
                # the page cache answered (throughput overstated); much more = foreign I/O.
                fl["device_read_mib"] = (stat1["rd_sectors"] - stat0["rd_sectors"]) * 512 / 2**20
                fl["device_read_ratio"] = fl["device_read_mib"] / (fl["bytes_read"] / 2**20)
            run["files"] = fl
            run["partial"] = fl.get("partial", False)
        elif args.tier == "scan":
            tier_scan(args, disk, devpath, ident, outdir, run)
        elif args.tier == "quick":
            tier_quick(args, disk, devpath, ident, outdir, run)
        elif args.tier == "write":
            tier_write(args, disk, devpath, ident, outdir, run)
    except KeyboardInterrupt:
        run["partial"] = True
    except SystemExit:
        raise
    except Exception as e:                  # never lose an hour of measurements to a late failure
        run["error"] = traceback.format_exc()
        run["partial"] = True
        eprint(f"[card_doctor] stage failed: {e!r} — writing what was measured (see run.json 'error')")

    if not getattr(args, "allow_file", False):
        lines, note = klog_lines(t_start - 2, klog_tokens(ident))
        run["kernel_log"] = dict(klog_classify(lines), source=f"{note}, run window")
        (outdir / "kernel.log").write_text("\n".join(lines))
    else:
        run["kernel_log"] = {"total": 0, "fatal": [], "source": "skipped (file mode)"}
    finish_run(run, outdir, baseline)
    if args.tier in ("scan", "quick") and (run.get("scan") or {}).get("unmounted"):
        print(f"  the card's partitions were unmounted for the {args.tier}; re-seat the card or run (no sudo):  udisksctl mount -b {run['scan']['unmounted'][-1]}")
    return 0 if run["verdict"]["level"] in ("HEALTHY", "INCONCLUSIVE") else 1


if __name__ == "__main__":
    sys.exit(main())
