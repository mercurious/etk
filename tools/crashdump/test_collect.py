#!/usr/bin/env python3
"""Host tests for tools/crashdump/collect.py: a fake sysfs (05c6:900e present or not) and a fake
`qdl` that writes Sahara segments, so watch -> ramdump -> report runs end to end without a car.

  python3 tools/crashdump/test_collect.py
"""
import os
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "collect.py")
FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


def usb(root, name, vid, pid):
    d = os.path.join(root, name)
    os.makedirs(d)
    for k, v in (("idVendor", vid), ("idProduct", pid)):
        with open(os.path.join(d, k), "w") as f:
            f.write(v + "\n")


FAKE_QDL = r'''#!/usr/bin/env python3
import os, sys
assert sys.argv[1] == "ramdump" and sys.argv[2] == "-o", sys.argv
out = sys.argv[3]
os.makedirs(out, exist_ok=True)
noise = bytes(range(256)) * 4096
with open(os.path.join(out, "DDRCS0_0.BIN"), "wb") as f:
    f.write(noise)
    f.write(b"\x00\x00msm_dpu ae01000.display-controller: [drm:recover_worker] 06050002: context_keepalive: surviving hang\x00")
    f.write(noise)
    f.write(b"\x01arm-smmu 3da0000.iommu: Unhandled context fault: fsr=0x402, iova=0xdead0000\x00")
    f.write(noise)
    f.write(b"\x01arm-smmu 3da0000.iommu: Unhandled context fault: fsr=0x402, iova=0xdead0000\x00")   # dup
with open(os.path.join(out, "OCIMEM.BIN"), "wb") as f:
    f.write(noise[:4096] + b"TZ  : NOC error: GEM_NOC slave GPU addr 0x0\x00plain firmware words here\x00")
print("ramdump: 2 segments")
sys.exit(int(os.environ.get("FAKE_QDL_RC", "0")))
'''

with tempfile.TemporaryDirectory() as tmp:
    sysfs = os.path.join(tmp, "usb")
    os.makedirs(sysfs)
    usb(sysfs, "1-1", "1d6b", "0104")          # a ROCKNIX gadget: not a dump
    qdl = os.path.join(tmp, "qdl")
    with open(qdl, "w") as f:
        f.write(FAKE_QDL)
    os.chmod(qdl, os.stat(qdl).st_mode | stat.S_IEXEC)
    env = dict(os.environ, ETK_USB_SYSFS=sysfs, ETK_QDL=qdl)

    def run(*argv, **extra):
        p = subprocess.run([sys.executable, TOOL] + list(argv), env=dict(env, **extra),
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    print("[1] no crash-dump device")
    rc, out = run("watch", "--secs", "1", "--out", os.path.join(tmp, "none"))
    check("times out, exit 1, nothing written", (rc, os.path.exists(os.path.join(tmp, "none"))), (1, False))

    print("[2] 05c6:900e present -> qdl ramdump -> report")
    usb(sysfs, "1-2", "05c6", "900e")
    d = os.path.join(tmp, "dump")
    rc, out = run("watch", "--secs", "5", "--out", d)
    check("exit 0", rc, 0)
    rep = open(os.path.join(d, "report.md")).read()
    check("segment table lists both segments", ("| DDRCS0_0.BIN |" in rep, "| OCIMEM.BIN |" in rep), (True, True))
    check("the kernel line in DDR is found", "context_keepalive: surviving hang" in rep, True)
    check("the SMMU fault is found once (deduped)", rep.count("Unhandled context fault"), 1)
    check("the firmware NoC line in OCIMEM is found", "NOC error: GEM_NOC" in rep, True)
    check("noise and plain words are not keyword hits", "plain firmware words" in rep, False)
    check("small segment gets a full strings dump",
          "plain firmware words here" in open(os.path.join(d, "OCIMEM.BIN.strings.txt")).read(), True)
    check("qdl's own output kept", "2 segments" in open(os.path.join(d, "qdl.log")).read(), True)

    print("[3] qdl fails mid-dump")
    rc, out = run("watch", "--secs", "5", "--out", os.path.join(tmp, "bad"), FAKE_QDL_RC="3")
    check("exit 2, but whatever arrived is still reported",
          (rc, os.path.exists(os.path.join(tmp, "bad", "report.md"))), (2, True))

    print("[4] qdl missing")
    rc, out = run("watch", "--secs", "1", ETK_QDL=os.path.join(tmp, "nope"))
    check("exit 1 naming the dnf package", (rc, "dnf install qdl" in out), (1, True))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CRASHDUMP CHECKS PASSED")
