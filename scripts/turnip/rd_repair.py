#!/usr/bin/env python3
# ETK Turnip — freedreno redump (.rd) validate + repair.
#
# WHY: the ROCKNIX hangrd debugfs node (cat /sys/kernel/debug/dri/0/hangrd) sometimes
# emits an INCOMPLETE redump even when the file is size-stable and the reader has closed:
#   - the trailing RD_BUFFER_CONTENTS section is truncated (declares more bytes than written), and/or
#   - the RD_CMDSTREAM_ADDR section (the cmdstream-entry pointer, written at the redump TAIL) is absent.
# cffdump then decodes 0 draws — it has the full GPU memory image but no entry point.
# Observed live 2026-06-18 (Save hang 235823) and 2026-06-19 (prefer_gmem 114642); the latter
# truncated BEFORE any R3, so it is the node's own dump, not an early-R3 race.
#
# FIX: drop the incomplete trailing section, then synthesize an RD_CMDSTREAM_ADDR from the
# dmesg faultinfo's ib1 gpuaddr (read from <file>.faultinfo), sized to the FULL containing
# RD_GPUADDR buffer (NOT the dmesg ib1_size, which is the *remaining* count and truncates the
# decode). Repairs IN PLACE (the dropped tail is incomplete/useless; in-place is SD-space neutral).
# Idempotent: a clean file (or an already-repaired one) is left untouched.
#
# Section layout (src/freedreno/common/redump.h + decode/rdutil.h parse_addr):
#   RD_GPUADDR(3)/RD_CMDSTREAM_ADDR(6) payload = [u32 gpuaddr_lo, u32 size/len, u32 gpuaddr_hi]
#
# Usage: rd_repair.py <file.rd>          # validate, repair in place if needed
#        rd_repair.py --check <file.rd>  # validate only, never write (exit 0 clean / 3 needs-repair)
# Exit:  0 clean-or-repaired · 2 cannot-repair (no ib1 / addr not mapped) · 3 needs-repair (--check)
import sys, struct, os, re

RD_GPUADDR = 3
RD_CMDSTREAM_ADDR = 6
RD_BUFFER_CONTENTS = 12


def read_sections(path):
    """Return (filesize, [(type,size,hdr_off,end)], truncation_or_None). Stops at a section
    whose declared length runs past EOF (truncation)."""
    size = os.path.getsize(path)
    secs = []
    trunc = None
    with open(path, "rb") as f:
        while True:
            off = f.tell()
            h = f.read(8)
            if len(h) < 8:
                break
            t, sz = struct.unpack("<II", h)
            end = off + 8 + sz
            if end > size:
                trunc = (t, sz, size - (off + 8))  # (type, declared_sz, bytes_present)
                break
            secs.append((t, sz, off, end))
            f.seek(sz, 1)
    return size, secs, trunc


def faultinfo_ib1(rd):
    fi = rd + ".faultinfo"
    if not os.path.exists(fi):
        return None
    m = re.search(r"ib1=([0-9A-Fa-f]+)", open(fi).read())
    return int(m.group(1), 16) if m else None


def ib1_full_size_dwords(rd, secs, ib1):
    """Find the RD_GPUADDR region containing ib1; return dwords from ib1 to that buffer's end."""
    with open(rd, "rb") as f:
        for t, sz, off, end in secs:
            if t == RD_GPUADDR and sz >= 8:
                f.seek(off + 8)
                d = f.read(sz)
                lo = struct.unpack("<I", d[0:4])[0]
                ln = struct.unpack("<I", d[4:8])[0]
                hi = struct.unpack("<I", d[8:12])[0] if sz >= 12 else 0
                base = lo | (hi << 32)
                if base <= ib1 < base + ln:
                    return (ln - (ib1 - base)) // 4
    return None


def main():
    args = sys.argv[1:]
    check_only = "--check" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        print("usage: rd_repair.py [--check] <file.rd>")
        return 2
    rd = paths[0]
    size, secs, trunc = read_sections(rd)
    has_cs = any(t == RD_CMDSTREAM_ADDR for t, _, _, _ in secs)

    if not trunc and has_cs:
        print("[rd_repair] OK: clean (%d sections, has RD_CMDSTREAM_ADDR)" % len(secs))
        return 0

    reasons = []
    if trunc:
        reasons.append("truncated (%d-byte tail of type-%d dropped)" % (trunc[2], trunc[0]))
    if not has_cs:
        reasons.append("missing RD_CMDSTREAM_ADDR")
    why = "; ".join(reasons)

    if check_only:
        print("[rd_repair] NEEDS REPAIR: %s" % why)
        return 3

    ib1 = faultinfo_ib1(rd)
    if ib1 is None:
        print("[rd_repair] CANNOT repair (%s): no ib1 in %s.faultinfo" % (why, os.path.basename(rd)))
        return 2
    sizedwords = ib1_full_size_dwords(rd, secs, ib1)
    if sizedwords is None:
        print("[rd_repair] CANNOT repair (%s): ib1 0x%X not in any RD_GPUADDR region" % (why, ib1))
        return 2

    last_good = secs[-1][3] if secs else 0
    lo = ib1 & 0xFFFFFFFF
    hi = (ib1 >> 32) & 0xFFFFFFFF
    section = struct.pack("<II", RD_CMDSTREAM_ADDR, 12) + struct.pack("<III", lo, sizedwords, hi)
    tmp = rd + ".repair.tmp"
    with open(rd, "rb") as f:
        body = f.read(last_good)
    with open(tmp, "wb") as o:
        o.write(body)
        o.write(section)
    os.replace(tmp, rd)
    print("[rd_repair] REPAIRED (%s): synth RD_CMDSTREAM_ADDR gpuaddr=0x%X sizedwords=0x%X"
          % (why, ib1, sizedwords))
    return 0


if __name__ == "__main__":
    sys.exit(main())
