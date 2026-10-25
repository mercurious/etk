#!/usr/bin/env python3
"""rd_tailwrites.py DEC.txt FAULTINFO -- what memory-writing packets were still ahead of the CP when the a6xx
hang detector fired (the rest of IB2 + the rest of IB1). Recovery drops those: if RPCS3's FIFO depends on
any of them (labels / semaphores / fences), it reads stale values afterwards."""
import re
import sys

dec, fi = sys.argv[1], sys.argv[2]
info = open(fi).read()
ib1, ib1rem, ib2, ib2rem = (int(x, 16) for x in
                            re.search(r"ib1=([0-9A-Fa-f]+) ib1_size=([0-9a-f]+) ib2=([0-9A-Fa-f]+) ib2_size=([0-9a-f]+)", info).groups())
lines = open(dec, errors="replace").read().split("\n")
ADDR = re.compile(r"^([0-9a-f]{16}):(\t*)")
WRITES = ("CP_MEM_WRITE", "CP_EVENT_WRITE", "CP_REG_TO_MEM", "CP_MEM_TO_MEM", "CP_EVENT_WRITE7",
          "CP_REG_TO_MEM_OFFSET_MEM", "CP_REG_TO_MEM_OFFSET_REG", "CP_SMMU_TABLE_UPDATE", "CP_WAIT_REG_MEM",
          "CP_WAIT_MEM_GTE", "CP_COND_WRITE5", "CP_MEM_TO_REG", "CP_WAIT_FOR_ME", "CP_INDIRECT_BUFFER")

ib1size = None
for l in lines:
    m = re.match(r"cmdstream\[\d+\]: (\d+) dwords", l)
    if m:
        ib1size = int(m.group(1))
        break
ib2size = None
for i, l in enumerate(lines):
    if l.strip() == "{ IB_BASE = 0x%x }" % ib2:
        m = re.search(r"IB_SIZE = 0x([0-9a-f]+)", lines[i + 1])
        if m:
            ib2size = int(m.group(1), 16)
        break


def tail(base, size, rem, depth_tabs):
    """packets at the given nesting depth whose address is in [stop, end) of this IB"""
    if not size:
        return []
    stop, end = base + (size - rem) * 4, base + size * 4
    out, pending = [], None
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("opcode: "):
            pending = (len(l) - len(l.lstrip("\t")), s.split("opcode: ")[1].split(" (")[0], i)
            continue
        m = ADDR.match(l)
        if m and pending and len(m.group(2)) == pending[0]:
            a = int(m.group(1), 16)
            if stop <= a < end:
                ev = ""
                for t in lines[pending[2] + 1:pending[2] + 4]:
                    e = re.search(r"EVENT = (\w+)", t)
                    if e:
                        ev = e.group(1)
                        break
                out.append((pending[1], ev))
            pending = None
    return out


ib2_tail = tail(ib2, ib2size, ib2rem, None) if ib2 else []
ib1_tail = tail(ib1, ib1size, ib1rem, None)
from collections import Counter
for name, t in (("IB2 rest", ib2_tail), ("IB1 rest", ib1_tail)):
    c = Counter(f"{op}{'(' + ev + ')' if ev else ''}" for op, ev in t if op in WRITES)
    print(f"  {name}: {len(t)} packets ahead; memory/sync: " + (", ".join(f"{k}x{v}" for k, v in c.most_common()) or "none"))
