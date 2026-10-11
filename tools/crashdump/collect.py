#!/usr/bin/env python3
"""collect.py -- the host end of a firmware crash dump (GT5 op, 2026-10-11).

GT5 kills car8 with a FIRMWARE warm reset: the PMIC reads warm_reset=PS_HOLD and the new kernel
starts 10-12 s after a healthy sample, under panic=30 (Hunt_GT5_20261011.md). Linux never panics,
so kmsg, netconsole and pstore can't see it. Qualcomm firmware can park the SoC in Sahara
crash-dump mode instead of resetting: with `qcom_scm.download_mode` armed on the car
(/sys/module/qcom_scm/parameters/download_mode = full), the boot ROM re-enumerates over USB as
05c6:900e and offers its memory segments. This collects them with Fedora's `qdl ramdump`
(linux-msm/qdl 2.8) and reads them for what never reached the card: the kernel's last log lines
(the printk ring lives in DDR), and the firmware's own strings in the small on-chip segments
(OCIMEM / IMEM / CODERAM).

  collect.py status                         qdl, the udev rule, a 900e device present?
  collect.py watch [--secs S] [--out DIR]   wait for 05c6:900e, `qdl ramdump -o DIR`, then report
  collect.py report DIR                     segments + keyword hits -> DIR/report.md

The M1 needs `qdl` (dnf) and tools/crashdump/72-etk-qcom-crashdump.rules (once, sudo). Nothing
here touches the car: arming download_mode is the car side, and it is the operator's call.
Exit: 0 ok · 1 nothing collected / tool missing · 2 qdl failed.
"""
import argparse
import datetime as dt
import glob
import mmap
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
USB_SYSFS = os.environ.get("ETK_USB_SYSFS", "/sys/bus/usb/devices")
QDL = os.environ.get("ETK_QDL", "qdl")
RULE = "72-etk-qcom-crashdump.rules"
VID, PID = "05c6", "900e"
# What a dead boot would have said: Linux's own crash text, the GPU/SMMU/interconnect lines this
# rig's deaths cluster around, and firmware-side words (TZ, XPU, NoC, watchdog bite/bark).
KEYS = re.compile(rb"Kernel panic|Internal error|Unable to handle|SError|Call trace|BUG:|Oops|"
                  rb"hard LOCKUP|soft lockup|rcu_.{0,20}stall|watchdog|[Bb]ark|[Bb]ite|"
                  rb"context_keepalive|hangcheck|gpu fault|a6xx|arm-smmu|smmu|SMMU|[Nn]o[Cc] err|"
                  rb"XPU|xpu|TZ |tz_|QSEE|qsee|[Rr]eset reason|[Rr]estart reason|"
                  rb"BCUS98114|rsx::thread|AppRun")
PRINTABLE = re.compile(rb"[\x20-\x7e\t]{12,}")
SMALL = 16 << 20          # segments below this get a full strings dump (on-chip RAM, not DDR)


def dump_devices(root=USB_SYSFS):
    """USB device dirs currently enumerated as Qualcomm crash-dump mode (05c6:900e)."""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        try:
            v = open(os.path.join(d, "idVendor")).read().strip()
            p = open(os.path.join(d, "idProduct")).read().strip()
        except OSError:
            continue
        if v == VID and p == PID:
            out.append(d)
    return out


def scan_segment(path, ctx=160, cap=400):
    """[(offset, text)] for printable runs that hit KEYS, deduped by text, at most `cap`.
    mmap + one C-speed regex pass: an 8 GB DDR segment is a minute, not an hour."""
    hits, seen = [], set()
    size = os.path.getsize(path)
    if size == 0:
        return hits
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        for mo in PRINTABLE.finditer(m):
            run = mo.group(0)
            if not KEYS.search(run):
                continue
            text = run[:ctx * 2].decode("ascii", "replace")
            if text in seen:
                continue
            seen.add(text)
            hits.append((mo.start(), text))
            if len(hits) >= cap:
                break
    return hits


def report(d, out=print):
    """Write DIR/report.md: the segment table, keyword hits per segment, and a full strings
    dump of each small (on-chip) segment beside it. Returns the number of hits."""
    segs = sorted(p for p in glob.glob(os.path.join(d, "*"))
                  if os.path.isfile(p) and not p.endswith((".md", ".log", ".strings.txt")))
    lines = [f"# Firmware crash dump -- {os.path.basename(os.path.abspath(d))}", "",
             "| segment | bytes |", "|---|---|"]
    lines += [f"| {os.path.basename(p)} | {os.path.getsize(p):,} |" for p in segs]
    total = 0
    for p in segs:
        hits = scan_segment(p)
        total += len(hits)
        lines += ["", f"## {os.path.basename(p)}: {len(hits)} keyword hit(s)", ""]
        lines += [f"- `0x{off:x}` {text}" for off, text in hits]
        if 0 < os.path.getsize(p) < SMALL:
            with open(p, "rb") as f:
                strs = [m.group(0).decode("ascii", "replace") for m in PRINTABLE.finditer(f.read())]
            with open(p + ".strings.txt", "w") as f:
                f.write("\n".join(strs) + "\n")
    with open(os.path.join(d, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    out(f"crashdump: {len(segs)} segment(s), {total} keyword hit(s) -> {os.path.join(d, 'report.md')}")
    return total


def cmd_status(a):
    q = shutil.which(QDL)
    rule = [p for p in ("/etc/udev/rules.d/" + RULE, "/usr/lib/udev/rules.d/" + RULE) if os.path.exists(p)]
    devs = dump_devices()
    print(f"qdl        {q or 'MISSING (sudo dnf install qdl)'}")
    print(f"udev rule  {rule[0] if rule else 'MISSING (tools/crashdump/' + RULE + ', once, sudo)'}")
    print(f"900e now   {', '.join(os.path.basename(x) for x in devs) or 'none'}")
    return 0 if q and rule else 1


def cmd_watch(a):
    if not shutil.which(QDL):
        print("crashdump: qdl is not installed (sudo dnf install qdl)", file=sys.stderr)
        return 1
    out = a.out or os.path.join(ETK, "state", "crashdump", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    t0 = time.time()
    print(f"crashdump: waiting up to {a.secs:.0f} s for {VID}:{PID} (Sahara crash-dump mode)", flush=True)
    while not dump_devices():
        if time.time() - t0 > a.secs:
            print("crashdump: no crash-dump device appeared")
            return 1
        time.sleep(1)
    print(f"crashdump: {VID}:{PID} after {time.time() - t0:.0f} s -> qdl ramdump -o {out}", flush=True)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "qdl.log"), "w") as log:
        rc = subprocess.run([QDL, "ramdump", "-o", out] + ([a.filter] if a.filter else []),
                            stdout=log, stderr=subprocess.STDOUT, timeout=a.qdl_timeout).returncode
    if rc != 0:
        print(f"crashdump: qdl ramdump exited {rc} (see {out}/qdl.log)")
        report(out)
        return 2
    report(out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    w = sub.add_parser("watch")
    w.add_argument("--secs", type=float, default=3600)
    w.add_argument("--out")
    w.add_argument("--filter", help="qdl segment filter, e.g. OCIMEM,CODERAM (default: every segment)")
    w.add_argument("--qdl-timeout", type=float, default=3600)
    r = sub.add_parser("report")
    r.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "watch":
        return cmd_watch(a)
    return 0 if report(a.dir) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
