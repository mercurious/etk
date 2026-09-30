#!/usr/bin/env python3
"""ETK Flip 2 kit DTB derivation (install.sh STEP 6.4, bin/osguard.sh Phase B).

Two independent deltas, each of which applies or stands down on its own:
  mic   the built-in microphone (below; kill-switch ETK_INTERNAL_MIC=0 -> --no-mic)
  vbus  the USB-C connector's VBUS supply (below the mic section)

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

USB-C VBUS (2026-09-30): mainline 7.2 moved the PM8150B VBUS supply from the Type-C
block (`vdd-vbus-supply`, pm8150b.dtsi) to the connector (`vbus-supply`, b5817fa4026c)
and taught the driver to read the connector first (506927b6bf29) — but only in-tree
boards got the new property. The ROCKNIX 20260901 Flip 2 DT has NEITHER, so the driver
falls back to a DUMMY regulator: in source role it "enables" nothing, VBUS never
reaches 5 V ("vbus vsafe5v fail"), and a bus-powered DP/HDMI adapter (or any
bus-powered USB device) never powers up — no PD, no DP alt-mode, no picture. One
connector property fixes it, exactly as ROCKNIX 1dc63e1531 does upstream:

    <pm8150b typec>/connector  vbus-supply = <&pm8150b_vbus>   (after self-powered)

Validated on the rig 2026-09-30: cold boot on the spliced DTB -> usb_vbus enabled,
PD partner with the ff01 alt-mode, DP-1 connected, picture on the TV. It stands down
when the stock DT already wires VBUS either way (upstream took over).

This tool derives the ETK DTB from whatever stock DTB the OS ships, by splicing the
binary FDT directly (no dtc: decompiling the stock Flip 2 DTB turns vreg_l11c's
<0x324b00> into "\\02K", which recompiles wrong). It refuses anything it is not sure of:
  * the input must re-serialize BYTE-IDENTICAL with no edits (proves the splicer), and
  * after each delta, the output must equal its input node-for-node except that
    delta's properties (two /sound props; one connector prop).

    etk_dtb_mic.py derive [--no-mic] [--no-vbus] <stock.dtb> <out.dtb>
        DTB_MIC_OK sha=<out> base=<in> mic=<st> vbus=<st>          exit 0  (out written)
        DTB_MIC_SKIP <reason> base=<in> [mic=<st> vbus=<st>]       exit 3  (nothing applies:
                                                                     use the stock DTB)
        DTB_MIC_FAIL <reason>                                      exit 1
      <st> = applied | off | the delta's stand-down reason. A SKIP's <reason> is the DTB-
      level refusal, else the first requested delta's stand-down reason.
    etk_dtb_mic.py check <dtb>
        DTB_MIC_PATCHED | DTB_MIC_STOCK     exit 0 / 3   (the mic delta only)
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
TYPEC_COMPAT = b"qcom,pm8150b-typec"
CONN_COMPAT = b"usb-c-connector"
VBUS_COMPAT = b"qcom,pm8150b-vbus-reg"


class Skip(Exception):
    """The whole DTB is not ours to touch (or nothing applies): use the stock DTB."""
    def __init__(self, reason, status=None):
        super().__init__(reason)
        self.status = status


class Stand(Exception):
    """One delta stands down; the other may still apply."""


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


def index(toks):
    """path -> {"props": {name: (start, end, value)}, "after_props": offset past its last prop}."""
    nodes, path = {}, []
    for kind, _i, j, x in toks:
        if kind == "begin":
            path.append(x)
            nodes["/".join(path) or "/"] = {"props": {}, "after_props": j}
        elif kind == "end":
            path.pop()
        elif kind == "prop":
            node = nodes["/".join(path) or "/"]
            node["props"][x[0]] = (_i, j, x[1])
            node["after_props"] = j
    return nodes


def _string_off(strings, name):
    """(offset of name in the strings block, strings block) — reuse a whole-string hit, else append."""
    needle = name.encode() + b"\0"
    idx = strings.find(needle)
    while idx > 0 and strings[idx - 1] != 0:
        idx = strings.find(needle, idx + 1)
    if idx < 0:
        return len(strings), strings + needle
    return idx, strings


def _prop(nameoff, val):
    blob = struct.pack(">III", PROP, len(val), nameoff) + val
    return blob + b"\0" * (_align4(len(blob)) - len(blob))


def _mic_delta(data):
    """-> data + the two /sound properties (post-verified), or raise Stand(reason)."""
    h, sb, strings, toks = parse(data)
    head_gap = data[HDR.size:h[2]]
    props = sound_props(toks)
    if props is None:
        raise Stand("no-sound-node")
    if props.get("compatible", (0, 0, b""))[2].split(b"\0")[0] != CARD_COMPAT:
        raise Stand("unexpected-sound-card")
    if "audio-routing" not in props:
        raise Stand("no-audio-routing")
    if is_patched(props):
        raise Stand("already-patched")
    if "widgets" in props:
        raise Stand("stock-dt-already-has-widgets")          # upstream took over: stand down
    routing = _strlist(props["audio-routing"][2])
    if routing is None or len(routing) % 2:
        raise Stand("malformed-audio-routing")
    if MIC in routing:
        raise Stand("stock-dt-already-routes-internal-mic")

    idx, strings2 = _string_off(strings, "widgets")
    r_start, r_end, r_val = props["audio-routing"]
    r_nameoff = struct.unpack_from(">I", sb, r_start + 8)[0]
    w_val = b"".join(s.encode() + b"\0" for s in WIDGETS)
    new_r = r_val + b"".join(s.encode() + b"\0" for p in ROUTES for s in p)
    sb2 = sb[:r_start] + _prop(idx, w_val) + _prop(r_nameoff, new_r) + sb[r_end:]
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


def _vbus_delta(data):
    """-> data + connector vbus-supply = <&pm8150b_vbus> (post-verified), or raise Stand(reason)."""
    h, sb, strings, toks = parse(data)
    head_gap = data[HDR.size:h[2]]
    nodes = index(toks)

    def has_compat(path, compat):
        return compat in nodes[path]["props"].get("compatible", (0, 0, b""))[2].split(b"\0")

    def okay(path):
        st = nodes[path]["props"].get("status")
        return st is None or st[2] in (b"okay\0", b"ok\0")

    typec = [p for p in nodes if has_compat(p, TYPEC_COMPAT)]
    if len(typec) != 1:
        raise Stand("no-pm8150b-typec" if not typec else "multiple-pm8150b-typec")
    if not okay(typec[0]):
        raise Stand("typec-disabled")
    if "vdd-vbus-supply" in nodes[typec[0]]["props"]:
        raise Stand("typec-has-vdd-vbus-supply")             # pre-7.2 binding: the driver falls back to it
    conn = typec[0] + "/connector"
    if conn not in nodes or not has_compat(conn, CONN_COMPAT):
        raise Stand("no-usb-c-connector")
    cprops = nodes[conn]["props"]
    if "vbus-supply" in cprops:
        raise Stand("connector-has-vbus-supply")             # upstream took over (or already spliced)
    regs = [p for p in nodes if has_compat(p, VBUS_COMPAT)]
    if len(regs) != 1:
        raise Stand("no-vbus-regulator" if not regs else "multiple-vbus-regulators")
    # A disabled regulator never registers: the connector would then defer the Type-C
    # probe FOREVER (no charging negotiation either). Only ever point at a live one.
    if not okay(regs[0]):
        raise Stand("vbus-regulator-disabled")
    ph = nodes[regs[0]]["props"].get("phandle", (0, 0, b""))[2]
    if len(ph) != 4:
        raise Stand("vbus-regulator-no-phandle")
    if [p for p in nodes if nodes[p]["props"].get("phandle", (0, 0, b""))[2] == ph
            or nodes[p]["props"].get("linux,phandle", (0, 0, b""))[2] == ph] != regs:
        raise Stand("vbus-phandle-not-unique")

    idx, strings2 = _string_off(strings, "vbus-supply")
    anchor = cprops["self-powered"][1] if "self-powered" in cprops else nodes[conn]["after_props"]
    sb2 = sb[:anchor] + _prop(idx, ph) + sb[anchor:]
    out = build(h, head_gap, sb2, strings2)

    # post-verify: identical tree except the one connector property, at the anchor
    _h2, _sb, _st, toks2 = parse(out)
    exp, got = flatten(toks), flatten(toks2)
    exp_edit, last = [], max(i for i, x in enumerate(exp) if x[0] == conn)
    if "self-powered" in cprops:
        last = next(i for i, x in enumerate(exp) if x[0] == conn and x[1] == "self-powered")
    for i, x in enumerate(exp):
        exp_edit.append(x)
        if i == last:
            exp_edit.append((conn, "vbus-supply", ph))
    if got != exp_edit:
        raise RuntimeError("post-verify: output tree differs beyond the vbus delta")
    return out


DELTAS = (("mic", _mic_delta), ("vbus", _vbus_delta))


def derive(data, mic=True, vbus=True):
    """-> (out, {delta: applied|off|reason}). Skip = the DTB is not ours, or nothing applies."""
    h, sb, strings, toks = parse(data)
    off_rsv, off_struct = h[4], h[2]
    head_gap = data[HDR.size:off_struct]
    if off_rsv != HDR.size:
        raise Skip("unsupported-layout")
    if build(h, head_gap, sb, strings) != data:
        raise Skip("roundtrip-not-identical")
    if DEVICE_COMPAT not in root_compat(toks):
        raise Skip("not-a-flip2-dtb")
    want, status, out = {"mic": mic, "vbus": vbus}, {}, data
    for name, fn in DELTAS:
        if not want[name]:
            status[name] = "off"
            continue
        try:
            out = fn(out)
            status[name] = "applied"
        except Stand as e:
            status[name] = str(e)
    if "applied" not in status.values():
        reasons = [v for v in status.values() if v != "off"]
        raise Skip(reasons[0] if reasons else "all-deltas-off", status)
    return out, status


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _status_str(status):
    return " ".join(f"{k}={v}" for k, v in (status or {}).items())


def main(argv):
    flags = [a for a in argv[2:] if a.startswith("--")]
    args = argv[:2] + [a for a in argv[2:] if not a.startswith("--")]
    if len(args) == 4 and args[1] == "derive" and set(flags) <= {"--no-mic", "--no-vbus"}:
        data = open(args[2], "rb").read()
        try:
            out, status = derive(data, mic="--no-mic" not in flags, vbus="--no-vbus" not in flags)
        except Skip as e:
            print(f"DTB_MIC_SKIP {e} base={sha(data)} {_status_str(e.status)}".rstrip())
            return 3
        except Exception as e:  # noqa: BLE001 — any surprise = refuse, never write
            print(f"DTB_MIC_FAIL {type(e).__name__}: {e}")
            return 1
        tmp = args[3] + ".tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        import os
        os.replace(tmp, args[3])
        print(f"DTB_MIC_OK sha={sha(out)} base={sha(data)} {_status_str(status)}")
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
