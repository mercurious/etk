#!/usr/bin/env python3
"""gt5_park_catch.py -- read GT5's guest state live the moment a guest park begins.

A park (car8 2026-10-25, Hunt_GT5_Lift_20261025 addenda 2-3): flips stop, the RSX FIFO goes
`spinning` (state=2: the puller sits on a jump-to-self), `gFKsE` spins on GT5's GPUSyncArray
waiting for a slot's progress word, everything else sleeps. This tool watches the RSX PUT/GET pair
(read through the session, so it never HELLOs Pitlink and never takes the wheel from a driver);
once neither has moved for --after seconds it reads, every --every seconds, --reads times:
  * the RSX DMA control block (PUT/GET/REF at dma_address + 0x40; dma_address from the session
    log's "sys_rsx_context_allocate(): Mapped address", default 0x70100000 on car8)
  * the command words around GET (io -> ea through GT5's boot map, ea = 0x40000000 + io)
  * the GPUSyncArray (0x1a13e80, 0xc0 bytes) and its two descriptor buffers' heads
Reads go through a held `gtpilot.py serve` session (/proc/<pid>/mem): start it first.
Each read is appended to OUT as text; the host keeps it if the car dies.

  tools/autopilot/gt5_park_catch.py OUT [--after 8] [--reads 6] [--every 2] [--secs 900]
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import time
import etkconf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GTPILOT = os.path.join(HERE, "gtpilot.py")
SYNC = 0x1a13e80


def block(host, addr, n):
    r = subprocess.run([sys.executable, GTPILOT, "--host", host, "call", "block",
                        json.dumps({"addr": hex(addr), "n": n})], capture_output=True, text=True, timeout=30)
    try:
        return bytes.fromhex(json.loads(r.stdout)["hex"])
    except (ValueError, KeyError):
        return None


def hexlines(base, b):
    return "".join(f"  {base + o:08x}: " + " ".join(b[o:o + 16][k:k + 4].hex() for k in range(0, 16, 4)) + "\n"
                   for o in range(0, len(b), 16))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--host", default=etkconf.rig_ssh(fallback=etkconf.FALLBACK_SSH))
    ap.add_argument("--dma", type=lambda s: int(s, 0), default=0x70100000)
    ap.add_argument("--after", type=float, default=8)
    ap.add_argument("--reads", type=int, default=6)
    ap.add_argument("--every", type=float, default=2)
    ap.add_argument("--secs", type=float, default=900)
    a = ap.parse_args()
    t0, last, last_t = time.time(), None, time.time()
    with open(a.out, "a") as fh:
        fh.write(f"# gt5_park_catch start {time.strftime('%F %T')}\n")
        while time.time() - t0 < a.secs:
            ctl = block(a.host, a.dma, 0x60)
            f = struct.unpack(">II", ctl[0x40:0x48]) if ctl and len(ctl) >= 0x48 else None
            now = time.time()
            if f is None or f != last:
                last, last_t = f, now
                time.sleep(1)
                continue
            if now - last_t < a.after:
                time.sleep(1)
                continue
            fh.write(f"## PARK: put/get {f[0]:#x}/{f[1]:#x} frozen {now - last_t:.1f} s at {time.strftime('%T')}\n")
            for i in range(a.reads):
                ctl = block(a.host, a.dma, 0x60)
                fh.write(f"== read {i} {time.strftime('%T')}\n")
                if ctl is None:
                    fh.write("  dma control unreadable (session down or game gone)\n")
                    break
                put, get, ref = struct.unpack(">III", ctl[0x40:0x4c])
                fh.write(f"  dma 0x{a.dma:x}: put=0x{put:x} get=0x{get:x} ref=0x{ref:x}\n")
                ea = 0x40000000 + get
                around = block(a.host, (ea - 0x40) & ~0xf, 0x80)
                if around is not None:
                    cmd = struct.unpack(">I", around[(ea - ((ea - 0x40) & ~0xf)):][:4])[0]
                    # gcm_enums.h: OLD jump (cmd & 0xe0000003) == 0x20000000, NEW jump == 0x00000001,
                    # CALL (cmd & 3) == 2; a jump whose target == GET is the spin placeholder
                    kind, tgt = "", None
                    if cmd & 0xe0000003 == 0x20000000:
                        kind, tgt = "old JUMP", cmd & 0x1ffffffc
                    elif cmd & 0xe0000003 == 0x00000001:
                        kind, tgt = "new JUMP", cmd & 0xfffffffc
                    elif cmd & 3 == 2:
                        kind, tgt = "CALL", cmd & 0x1ffffffc
                    note = f"  ({kind} to 0x{tgt:x}{' = SELF' if tgt == get else ''})" if kind else ""
                    fh.write(f"  cmd at ea 0x{ea:x} = 0x{cmd:08x}{note}\n")
                    fh.write(hexlines((ea - 0x40) & ~0xf, around))
                sync = block(a.host, SYNC, 0xc0)
                if sync is not None:
                    fh.write("  GPUSyncArray:\n" + hexlines(SYNC, sync))
                fh.flush()
                time.sleep(a.every)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
