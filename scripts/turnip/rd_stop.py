#!/usr/bin/env python3
"""rd_stop.py DEC.txt FAULTINFO -- where in a cffdump --once decode did the a6xx hang detector stop the CP?

The hang line gives each IB's BASE and REMAINING dwords. Find IB2's total size from the CP_INDIRECT_BUFFER
that calls it, compute the stop address (base + (size - remaining) * 4), find the draws around it, and summarize
the last few draws before it (the GPU runs behind the CP's prefetch): primitive, index count, VS/FS code address,
MRT0 format and the render scissor.
"""
import re
import sys

dec, fi = sys.argv[1], sys.argv[2]
info = open(fi).read()
ib1, ib1rem, ib2, ib2rem = (int(x, 16) for x in
                            re.search(r"ib1=([0-9A-Fa-f]+) ib1_size=([0-9a-f]+) ib2=([0-9A-Fa-f]+) ib2_size=([0-9a-f]+)", info).groups())
fence = re.search(r"fence=([0-9a-f]+)", info).group(1)
lines = open(dec, errors="replace").read().split("\n")
ADDR = re.compile(r"^\t*([0-9a-f]{16}):")

ib2size = None
for i, l in enumerate(lines):
    if "IB_BASE = 0x%x " % ib2 in l + " " or l.strip() == "{ IB_BASE = 0x%x }" % ib2:
        m = re.search(r"IB_SIZE = 0x([0-9a-f]+)", lines[i + 1])
        if m:
            ib2size = int(m.group(1), 16)
            break
out = {"fence": fence, "ib1rem": "%04x" % ib1rem, "ib2": "%x" % ib2, "ib2rem": "%04x" % ib2rem}
if ib2size is None or ib2 == 0:
    out["note"] = "no IB2 call found (ib2=0?)"
    stop, lo, hi = ib1, ib1, ib1          # fall back to IB1 position below
else:
    stop = ib2 + (ib2size - ib2rem) * 4
    lo, hi = ib2, ib2 + ib2size * 4
    out["ib2size"] = "%04x" % ib2size
out["stop"] = "%x" % stop

# the decode line of the last packet address <= stop inside the IB2 range
stop_line = None
for i, l in enumerate(lines):
    m = ADDR.match(l)
    if m:
        a = int(m.group(1), 16)
        if lo <= a <= stop:
            stop_line = i
out["stop_line"] = stop_line

draw_marks = [(i, int(m.group(1))) for i, l in enumerate(lines) for m in [re.search(r"draw\[(\d+)\] register values", l)] if m]
before = [d for d in draw_marks if stop_line is not None and d[0] <= stop_line]
after = [d for d in draw_marks if stop_line is not None and d[0] > stop_line]
out["draws_total"] = len(draw_marks)
out["last_draw_before_stop"] = before[-1][1] if before else None
out["next_draw_after_stop"] = after[0][1] if after else None


def summarize(mark_line):
    """the CP_DRAW packet just above a draw marker, plus the shader loads and RT state that apply to it"""
    s = {}
    for j in range(mark_line, max(mark_line - 40, 0), -1):
        if "opcode: CP_DRAW" in lines[j]:
            s["draw"] = lines[j].split("opcode: ")[1].split(" (")[0]
            blk = " ".join(lines[j:j + 4])
            for k in ("PRIM_TYPE", "SOURCE_SELECT", "INDEX_SIZE", "NUM_INDICES", "NUM_INSTANCES"):
                m = re.search(k + r" = ([\w]+)", blk)
                if m:
                    s[k] = m.group(1)
            break
    for sb in ("SB6_VS_SHADER", "SB6_FS_SHADER"):
        for j in range(mark_line, 0, -1):
            if "ST6_SHADER" in lines[j] and sb in lines[j]:
                m = re.search(r"EXT_SRC_ADDR = (0x[0-9a-f]+)", lines[j + 1])
                n = re.search(r"NUM_UNIT = (\d+)", lines[j])
                s[sb[4:6] + "_code"] = (m.group(1) if m else "?") + "/" + (n.group(1) if n else "?")
                body = []
                for t in lines[j + 1:j + 6000]:
                    tt = t.strip()
                    if tt.startswith(":"):
                        body.append(tt.split("] ", 1)[-1])     # the instruction text, no address/encoding
                    elif body and (tt.startswith("opcode:") or ADDR.match(t)):
                        break
                import hashlib
                s[sb[4:6] + "_hash"] = hashlib.sha1("\n".join(body).encode()).hexdigest()[:10] + ":%d" % len(body)
                break
    regs = "\n".join(lines[mark_line:mark_line + 4000])
    for k, pat in (("MRT0", r"RB_MRT\[0\]\.BUF_INFO: \{ COLOR_FORMAT = (\w+)"),
                   ("scissor", r"GRAS_SC_SCREEN_SCISSOR\[0\]\.BR: \{ X = (\d+) \| Y = (\d+)"),
                   ("depth", r"RB_DEPTH_BUFFER_INFO: \{ DEPTH_FORMAT = (\w+)"),
                   ("vs_instr", r"SP_VS_INSTR_SIZE: (\d+)"),
                   ("fs_instr", r"SP_FS_INSTR_SIZE: (\d+)")):
        m = re.search(pat, regs)
        if m:
            s[k] = "x".join(m.groups())
    return s


out["recent"] = []
for i, n in before[-3:]:
    d = summarize(i)
    d["n"] = n
    out["recent"].append(d)
import json
print(json.dumps(out, indent=1))
