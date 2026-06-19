#!/usr/bin/env python3
# ETK Turnip — freedreno redump (.rd) section inspector / truncation check.
# Prints the RD section-type histogram and whether the file parses to a clean
# section boundary (truncation detector) and whether it carries RD_CMDSTREAM_ADDR
# (the cmdstream-entry pointer cffdump needs). Pairs with rd_repair.py.
#
# Enum: src/freedreno/common/redump.h
import sys, struct, os
from collections import Counter, defaultdict

NAMES = {0: "RD_NONE", 1: "RD_TEST", 2: "RD_CMD", 3: "RD_GPUADDR", 4: "RD_CONTEXT",
         5: "RD_CMDSTREAM", 6: "RD_CMDSTREAM_ADDR", 7: "RD_PARAM", 8: "RD_FLUSH",
         9: "RD_PROGRAM", 10: "RD_VERT_SHADER", 11: "RD_FRAG_SHADER",
         12: "RD_BUFFER_CONTENTS", 13: "RD_GPU_ID", 14: "RD_CHIP_ID",
         15: "RD_SHADER_LOG_BUFFER", 16: "RD_CP_LOG_BUFFER", 17: "RD_WRBUFFER"}


def main():
    path = sys.argv[1]
    size = os.path.getsize(path)
    cnt = Counter()
    byt = defaultdict(int)
    pos = 0
    with open(path, "rb") as f:
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            t, sz = struct.unpack("<II", h)
            cnt[t] += 1
            byt[t] += sz
            pos += 8 + sz
            f.seek(sz, 1)
    print("file: %s (%d bytes)" % (path, size))
    for t in sorted(cnt):
        print("  type %2d %-20s count=%8d bytes=%d" % (t, NAMES.get(t, "?"), cnt[t], byt[t]))
    trunc = pos - size
    print("  parsed_to=%d  %s" % (pos, "CLEAN" if trunc == 0 else "TRUNCATED (%+d)" % trunc))
    print("  RD_CMDSTREAM_ADDR present: %s" % ("YES" if cnt.get(6) else "NO"))


if __name__ == "__main__":
    main()
