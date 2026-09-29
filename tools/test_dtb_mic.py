#!/usr/bin/env python3
"""test_dtb_mic.py — discrimination suite for bin/etk_dtb_mic.py (Flip 2 internal-mic DTB).

Builds synthetic FDT blobs (no dtc needed) and asserts the patcher's behaviour on good AND
broken input — a suite that only sees the happy path proves nothing. Runs on the host and on
the rig (python3 is on ROCKNIX):
    host: python3 tools/test_dtb_mic.py [real-stock.dtb]
    rig:  scp bin/etk_dtb_mic.py tools/test_dtb_mic.py to /tmp;
          ssh 'python3 /tmp/test_dtb_mic.py /flash/boot/grub/sm8250-retroidpocket-flip2.dtb'
The optional real DTB is only READ (derive output goes to a private tmp dir).
Exit 0 = all pass; nonzero = number of failures.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.environ.get("ETK_DTB_MIC", os.path.join(HERE, "..", "bin", "etk_dtb_mic.py"))
if not os.path.exists(TOOL):
    TOOL = os.path.join(HERE, "etk_dtb_mic.py")          # rig: both files side by side in /tmp
sys.path.insert(0, os.path.dirname(TOOL))
import etk_dtb_mic as m  # noqa: E402

FAIL = PASS = 0


def check(desc, cond):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"  PASS: {desc}")
    else:
        FAIL += 1
        print(f"  FAIL: {desc}")


def fdt(tree, extra_strings=(), pad_after_struct=0):
    """tree = (name, [(prop, bytes)], [children]) -> tight FDT v17 blob."""
    strings, offs = b"", {}

    def soff(name):
        nonlocal strings
        if name not in offs:
            offs[name] = len(strings)
            strings += name.encode() + b"\0"
        return offs[name]

    for s in extra_strings:
        soff(s)

    def node(n):
        name, props, kids = n
        nb = name.encode() + b"\0"
        out = struct.pack(">I", 1) + nb + b"\0" * (m._align4(len(nb)) - len(nb))
        for p, v in props:
            blob = struct.pack(">III", 3, len(v), soff(p)) + v
            out += blob + b"\0" * (m._align4(len(blob)) - len(blob))
        for k in kids:
            out += node(k)
        return out + struct.pack(">I", 2)

    sb = node(tree) + struct.pack(">I", 9)
    rsv = b"\0" * 16
    off_struct = 40 + len(rsv)
    off_strings = off_struct + len(sb) + pad_after_struct
    total = off_strings + len(strings)
    hdr = struct.pack(">10I", 0xD00DFEED, total, off_struct, off_strings, 40, 17, 16, 0,
                      len(strings), len(sb))
    return hdr + rsv + sb + b"\0" * pad_after_struct + strings


def sl(*xs):
    return b"".join(x.encode() + b"\0" for x in xs)


def flip2(root_compat=("retroidpocket,rpflip2", "qcom,sm8250"), sound=True, widgets=None,
          routing=("SpkrLeft IN", "WSA_SPK1 OUT", "AMIC2", "MIC BIAS2"), card="qcom,sm8250-sndcard"):
    sprops = [("compatible", sl(card)), ("model", sl("RetroidPocket"))]
    if widgets is not None:
        sprops.append(("widgets", sl(*widgets)))
    if routing is not None:
        sprops.append(("audio-routing", sl(*routing)))
    sprops.append(("phandle", struct.pack(">I", 0x266)))
    kids = [("soc@0", [("#address-cells", struct.pack(">I", 2))],
             [("codec@3370000", [("compatible", sl("qcom,sm8250-lpass-va-macro"))], [])])]
    if sound:
        kids.append(("sound", sprops, [("mm1-dai-link", [("link-name", sl("MultiMedia1"))], [])]))
    kids.append(("audio-codec", [("compatible", sl("qcom,wcd9385-codec"))], []))
    return fdt(("", [("compatible", sl(*root_compat)), ("model", sl("Retroid Pocket Flip2"))], kids))


def run(argv):
    r = subprocess.run([sys.executable, TOOL] + argv, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def derive_file(blob, tmp, name):
    src, out = os.path.join(tmp, name + ".dtb"), os.path.join(tmp, name + ".out.dtb")
    open(src, "wb").write(blob)
    rc, txt = run(["derive", src, out])
    return rc, txt, src, out


def main():
    tmp = tempfile.mkdtemp(prefix="dtbmic_test.")
    print("== synthetic stock Flip 2")
    stock = flip2()
    rc, txt, src, out = derive_file(stock, tmp, "stock")
    check("derive exits 0 with DTB_MIC_OK", rc == 0 and txt.startswith("DTB_MIC_OK"))
    got = open(out, "rb").read() if os.path.exists(out) else b""
    check("output is a parseable FDT", got[:4] == b"\xd0\x0d\xfe\xed")
    fs, fo = m.flatten(m.parse(stock)[3]), m.flatten(m.parse(got)[3]) if got else []
    extra = [x for x in fo if x not in fs]
    check("exactly two /sound props differ (widgets added, audio-routing extended)",
          sorted(x[1] for x in extra) == ["audio-routing", "widgets"] and all(x[0] == "/sound" for x in extra))
    check("every other node/prop unchanged, in order", [x for x in fs if x[1] != "audio-routing" or x[0] != "/sound"]
          == [x for x in fo if x[0] != "/sound" or x[1] not in ("audio-routing", "widgets")])
    r = dict((p, v) for path, p, v in fo if path == "/sound")
    check("routing = stock + DMIC4<-Internal Mic, DMIC4<-MIC BIAS3",
          r.get("audio-routing") == sl("SpkrLeft IN", "WSA_SPK1 OUT", "AMIC2", "MIC BIAS2",
                                       "DMIC4", "Internal Mic", "DMIC4", "MIC BIAS3"))
    check("widgets = Microphone / Internal Mic", r.get("widgets") == sl("Microphone", "Internal Mic"))
    check("sha in the verdict matches the written file", f"sha={m.sha(got)}" in txt)
    check("base sha in the verdict is the input's", f"base={m.sha(stock)}" in txt)
    check("check: stock -> DTB_MIC_STOCK (3)", run(["check", src]) == (3, "DTB_MIC_STOCK"))
    check("check: output -> DTB_MIC_PATCHED (0)", run(["check", out]) == (0, "DTB_MIC_PATCHED"))
    rc2, txt2 = run(["derive", out, os.path.join(tmp, "again.dtb")])
    check("re-derive on patched output stands down (already-patched, 3, nothing written)",
          rc2 == 3 and "already-patched" in txt2 and not os.path.exists(os.path.join(tmp, "again.dtb")))

    print("== strings table already carries 'widgets' (another node uses it)")
    s2 = fdt(("", [("compatible", sl("retroidpocket,rpflip2"))],
              [("other", [("widgets", sl("x"))], []),
               ("sound", [("compatible", sl("qcom,sm8250-sndcard")), ("audio-routing", sl("A", "B"))], [])]))
    rc, txt, _src, out = derive_file(s2, tmp, "reuse")
    got = open(out, "rb").read() if rc == 0 else b""
    check("derive OK and the strings block is not grown (offset reused)",
          rc == 0 and m.parse(got)[0][8] == m.parse(s2)[0][8])

    print("== refusals (never write, exit 3)")
    cases = [
        ("not a Flip 2 (RP5 compat)", flip2(root_compat=("retroidpocket,rp5", "qcom,sm8250")), "not-a-flip2-dtb"),
        ("no /sound node", flip2(sound=False), "no-sound-node"),
        ("unexpected card compatible", flip2(card="qcom,sm8550-sndcard"), "unexpected-sound-card"),
        ("no audio-routing", flip2(routing=None), "no-audio-routing"),
        ("upstream already added widgets", flip2(widgets=("Microphone", "Int Mic")), "stock-dt-already-has-widgets"),
        ("routing already mentions Internal Mic", flip2(routing=("DMIC4", "Internal Mic")), "stock-dt-already-routes-internal-mic"),
        ("odd-length routing list", flip2(routing=("A", "B", "C")), "malformed-audio-routing"),
        ("padding between struct and strings", fdt(("", [("compatible", sl("retroidpocket,rpflip2"))], []), pad_after_struct=8), "unsupported-layout"),
        ("bad magic", b"\0" * 64, "not-an-fdt"),
        ("truncated blob", flip2()[:30], "too-small"),
    ]
    for desc, blob, reason in cases:
        rc, txt, _src, out = derive_file(blob, tmp, desc.replace(" ", "_").replace("/", "_"))
        check(f"{desc} -> SKIP {reason}", rc == 3 and f"DTB_MIC_SKIP {reason}" in txt and not os.path.exists(out))

    if len(sys.argv) > 1:
        real = sys.argv[1]
        print(f"== real DTB {real}")
        data = open(real, "rb").read()
        h, sb, strings, toks = m.parse(data)
        check("real DTB re-serializes byte-identical (splicer proven on it)",
              m.build(h, data[40:h[2]], sb, strings) == data)
        rc, txt, _src, out = derive_file(data, tmp, "real")
        if rc == 0:
            got = open(out, "rb").read()
            diff = [x for x in m.flatten(m.parse(got)[3]) if x not in m.flatten(toks)]
            check("real DTB: only /sound widgets + audio-routing differ",
                  sorted(x[1] for x in diff) == ["audio-routing", "widgets"])
        else:
            check(f"real DTB derive ({txt})", False)

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
