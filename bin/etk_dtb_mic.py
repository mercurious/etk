#!/usr/bin/env python3
"""ETK Flip 2 internal-mic DTB derivation (install.sh STEP 6.4, bin/osguard.sh Phase B).

The Flip 2's built-in microphone is a WCD938x digital mic: data slot 3 on the codec's
DMIC3/DMIC4 clock pair, powered from MIC BIAS3 (the AYN Thor's wiring for the same
slot). Mainline wcd938x gives its DMIC ADC widgets no DAPM source, and the stock ROCKNIX
Flip 2 DT routes no bias to them, so the capture path can never power up and the mic is
dead on ROCKNIX (it works on Android). Two DT additions fix it, no kernel change:

    /sound  widgets       += "Microphone", "Internal Mic"      (card-level mic = DAPM source)
    /sound  audio-routing += "DMIC4" <- "Internal Mic", "DMIC4" <- "MIC BIAS3"

Validated on the rig 2026-09-29 (ollamadreno mic hunt): 1 kHz loopback 86 dB above the
neighbouring bins, and speech transcribed word-exact by Qwen3-ASR; the same slot with no
bias is dead (-57 dBFS noise), which is what pins MIC BIAS3.

This tool derives the ETK DTB from whatever stock DTB the OS ships, by splicing the
binary FDT directly (no dtc: decompiling the stock Flip 2 DTB turns vreg_l11c's
<0x324b00> into "\\02K", which recompiles wrong). It refuses anything it is not sure of:
  * the input must re-serialize BYTE-IDENTICAL with no edits (proves the splicer), and
  * the output must equal the input node-for-node except the two /sound properties.

    etk_dtb_mic.py derive <stock.dtb> <out.dtb>
        DTB_MIC_OK sha=<out> base=<in>      exit 0  (out written)
        DTB_MIC_SKIP <reason> base=<in>     exit 3  (not applicable: use the stock DTB)
        DTB_MIC_FAIL <reason>               exit 1
    etk_dtb_mic.py check <dtb>
        DTB_MIC_PATCHED | DTB_MIC_STOCK     exit 0 / 3
"""
import hashlib
import struct
import sys

FDT_MAGIC = 0xD00DFEED
BEGIN, END, PROP, NOP, FEND = 1, 2, 3, 4, 9
HDR = struct.Struct(">10I")

DEVICE_COMPAT = b"retroidpocket,rpflip2"
CARD_COMPAT = b"qcom,sm8250-sndcard"
MIC = "Internal Mic"
WIDGETS = ["Microphone", MIC]
ROUTES = [("DMIC4", MIC), ("DMIC4", "MIC BIAS3")]


class Skip(Exception):
    pass


def _align4(n):
    return (n + 3) & ~3


def _cstr(buf, off):
    return buf[off:buf.index(b"\0", off)].decode()


def _strlist(val):
    return [s.decode() for s in val.split(b"\0")[:-1]] if val.endswith(b"\0") else None


def parse(data):
    """-> (hdr tuple, struct block, strings block, tokens). Tight standard layout only."""
    if len(data) < HDR.size:
        raise Skip("too-small")
    h = HDR.unpack_from(data)
    magic, total, off_struct, off_strings, off_rsv, version, _last, _cpu, sz_strings, sz_struct = h
    if magic != FDT_MAGIC:
        raise Skip("not-an-fdt")
    if version < 17 or total != len(data):
        raise Skip("unsupported-header")
    if not (off_rsv <= off_struct and off_strings == off_struct + sz_struct
            and total == off_strings + sz_strings):
        raise Skip("unsupported-layout")
    sb = data[off_struct:off_strings]
    strings = data[off_strings:total]
    toks, i = [], 0
    while True:
        tok = struct.unpack_from(">I", sb, i)[0]
        if tok == BEGIN:
            name_end = sb.index(b"\0", i + 4)
            j = _align4(name_end + 1)
            toks.append(("begin", i, j, sb[i + 4:name_end].decode()))
        elif tok == PROP:
            ln, nameoff = struct.unpack_from(">II", sb, i + 4)
            j = _align4(i + 12 + ln)
            toks.append(("prop", i, j, (_cstr(strings, nameoff), sb[i + 12:i + 12 + ln])))
        elif tok == END:
            j = i + 4
            toks.append(("end", i, j, None))
        elif tok == NOP:
            j = i + 4
            toks.append(("nop", i, j, None))
        elif tok == FEND:
            toks.append(("fend", i, i + 4, None))
            break
        else:
            raise Skip(f"bad-token@{i}")
        i = j
    return h, sb, strings, toks


def build(h, rsv_and_pad, sb, strings):
    off_struct = HDR.size + len(rsv_and_pad)
    off_strings = off_struct + len(sb)
    total = off_strings + len(strings)
    fields = list(h)
    fields[1], fields[2], fields[3] = total, off_struct, off_strings
    fields[8], fields[9] = len(strings), len(sb)
    return HDR.pack(*fields) + rsv_and_pad + sb + strings


def flatten(toks):
    """[(path, prop, value)] in tree order — the node-for-node comparison basis."""
    out, path = [], []
    for kind, _i, _j, x in toks:
        if kind == "begin":
            path.append(x)
        elif kind == "end":
            path.pop()
        elif kind == "prop":
            out.append(("/".join(path) or "/", x[0], x[1]))
    return out


def sound_props(toks):
    """Props directly in the top-level /sound node -> {name: (start, end, value)}."""
    depth, in_sound, props = 0, False, {}
    for kind, i, j, x in toks:
        if kind == "begin":
            depth += 1
            if depth == 2 and x == "sound":
                in_sound = True
        elif kind == "end":
            if in_sound and depth == 2:
                return props
            depth -= 1
        elif kind == "prop" and in_sound and depth == 2:
            props[x[0]] = (i, j, x[1])
    return props if in_sound else None


def root_compat(toks):
    depth = 0
    for kind, _i, _j, x in toks:
        if kind == "begin":
            depth += 1
        elif kind == "end":
            depth -= 1
        elif kind == "prop" and depth == 1 and x[0] == "compatible":
            return x[1].split(b"\0")
    return []


def is_patched(props):
    w = _strlist(props.get("widgets", (0, 0, b""))[2]) or []
    r = _strlist(props.get("audio-routing", (0, 0, b""))[2]) or []
    pairs = list(zip(r[0::2], r[1::2]))
    return MIC in w and all(p in pairs for p in ROUTES)


def derive(data):
    h, sb, strings, toks = parse(data)
    off_rsv, off_struct = h[4], h[2]
    head_gap = data[HDR.size:off_struct]
    if off_rsv != HDR.size:
        raise Skip("unsupported-layout")
    if build(h, head_gap, sb, strings) != data:
        raise Skip("roundtrip-not-identical")
    if DEVICE_COMPAT not in root_compat(toks):
        raise Skip("not-a-flip2-dtb")
    props = sound_props(toks)
    if props is None:
        raise Skip("no-sound-node")
    if props.get("compatible", (0, 0, b""))[2].split(b"\0")[0] != CARD_COMPAT:
        raise Skip("unexpected-sound-card")
    if "audio-routing" not in props:
        raise Skip("no-audio-routing")
    if is_patched(props):
        raise Skip("already-patched")
    if "widgets" in props:
        raise Skip("stock-dt-already-has-widgets")          # upstream took over: stand down
    routing = _strlist(props["audio-routing"][2])
    if routing is None or len(routing) % 2:
        raise Skip("malformed-audio-routing")
    if MIC in routing:
        raise Skip("stock-dt-already-routes-internal-mic")

    # strings block: reuse "widgets" if the table already carries it, else append
    idx, strings2 = strings.find(b"widgets\0"), strings
    while idx > 0 and strings[idx - 1] != 0:
        idx = strings.find(b"widgets\0", idx + 1)
    if idx < 0:
        idx, strings2 = len(strings), strings + b"widgets\0"

    def prop(nameoff, val):
        blob = struct.pack(">III", PROP, len(val), nameoff) + val
        return blob + b"\0" * (_align4(len(blob)) - len(blob))

    r_start, r_end, r_val = props["audio-routing"]
    r_nameoff = struct.unpack_from(">I", sb, r_start + 8)[0]
    w_val = b"".join(s.encode() + b"\0" for s in WIDGETS)
    new_r = r_val + b"".join(s.encode() + b"\0" for p in ROUTES for s in p)
    sb2 = sb[:r_start] + prop(idx, w_val) + prop(r_nameoff, new_r) + sb[r_end:]
    out = build(h, head_gap, sb2, strings2)

    # post-verify: identical tree except the two /sound properties
    _h2, _sb, _st, toks2 = parse(out)
    exp, got = flatten(toks), flatten(toks2)
    exp_edit = []
    for path, name, val in exp:
        if path == "/sound" and name == "audio-routing":
            exp_edit.append((path, "widgets", w_val))
            val = new_r
        exp_edit.append((path, name, val))
    if got != exp_edit:
        raise RuntimeError("post-verify: output tree differs beyond the mic delta")
    if not is_patched(sound_props(toks2)):
        raise RuntimeError("post-verify: mic delta not present in output")
    return out


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main(argv):
    if len(argv) == 4 and argv[1] == "derive":
        data = open(argv[2], "rb").read()
        try:
            out = derive(data)
        except Skip as e:
            print(f"DTB_MIC_SKIP {e} base={sha(data)}")
            return 3
        except Exception as e:  # noqa: BLE001 — any surprise = refuse, never write
            print(f"DTB_MIC_FAIL {type(e).__name__}: {e}")
            return 1
        tmp = argv[3] + ".tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        import os
        os.replace(tmp, argv[3])
        print(f"DTB_MIC_OK sha={sha(out)} base={sha(data)}")
        return 0
    if len(argv) == 3 and argv[1] == "check":
        try:
            _h, _sb, _st, toks = parse(open(argv[2], "rb").read())
            props = sound_props(toks)
        except Skip as e:
            print(f"DTB_MIC_FAIL {e}")
            return 1
        if props is not None and is_patched(props):
            print("DTB_MIC_PATCHED")
            return 0
        print("DTB_MIC_STOCK")
        return 3
    print(__doc__.split("\n\n")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
