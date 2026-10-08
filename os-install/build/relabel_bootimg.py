#!/usr/bin/env python3
"""relabel_bootimg.py — swap the partition LABELs baked into an Android boot.img's cmdline.

Under ROCKNIX-ABL (SM8250, 20261001+) the kernel cmdline rides INSIDE the boot.img
header and the ABL appends nothing, so the flashable GTK card — which uses UNIQUE
labels (ROCKNIX-GTK / GTKSTOR, split-brain-safe beside an internal ROCKNIX) — needs
a boot.img whose `boot=LABEL=…` and `disk=LABEL=…` tokens name the card's labels.
The certified artifact is minted against the stock labels (parity-gated); this tool
rewrites ONLY the 512-byte cmdline field of the header. Nothing else moves: kernel,
ramdisk, sizes, page size, os_version, the id (a SHA1 over kernel+ramdisk, which
does not cover the cmdline). The output is byte-identical to the input outside
bytes 64..575, and the tool refuses anything it is not sure of.

    relabel_bootimg.py <in> <out> <from_boot> <from_stor> <to_boot> <to_stor>
        RELABEL_OK sha=<out-sha> cmdline=<new cmdline>            exit 0
        RELABEL_FAIL <reason>                                     exit 1
    relabel_bootimg.py show <img>        prints the cmdline            exit 0

Each token `boot=LABEL=<from_boot>` / `disk=LABEL=<from_stor>` must occur EXACTLY
once; the new cmdline must fit the 512-byte field (NUL-terminated, so <= 511); the
extra_cmdline field must be empty (we never write there). Same labels in and out is
a byte-identical copy (idempotent). Used by os-install/build/build_gtk_image_v2.sh
(the recipe) AND by tools/forge/lane_image.sh's independent verify, so both sides
derive the expected baked kernel the same way. Harness: tools/test_relabel_bootimg.sh.
"""
import hashlib
import os
import struct
import sys

HDR = struct.Struct('<8s10I16s512s32s1024s')   # boot.img v0..2 header (1632 bytes)
CMD_OFF, CMD_LEN = 64, 512


def die(msg):
    print(f'RELABEL_FAIL {msg}')
    sys.exit(1)


def read_img(path):
    try:
        d = open(path, 'rb').read()
    except OSError as e:
        die(f'cannot read {path}: {e}')
    if len(d) < HDR.size or d[:8] != b'ANDROID!':
        die(f'{path}: not an Android boot image')
    f = HDR.unpack_from(d, 0)
    if f[9] > 2:
        die(f'{path}: header version {f[9]} not supported (v0..2 carry the cmdline in-header)')
    cmd = f[12]
    extra = f[14].rstrip(b'\0')
    if extra:
        die(f'{path}: extra_cmdline is not empty ({len(extra)} B) — refusing to guess')
    return d, cmd.rstrip(b'\0').decode(errors='strict')


def relabel(cmdline, fb, fs, tb, ts):
    for key, old, new in (('boot', fb, tb), ('disk', fs, ts)):
        tok = f'{key}=LABEL={old}'
        n = cmdline.split(' ').count(tok)
        if n != 1:
            die(f"token '{tok}' occurs {n} times in the cmdline (need exactly 1): {cmdline}")
    parts = [f'boot=LABEL={tb}' if p == f'boot=LABEL={fb}' else
             f'disk=LABEL={ts}' if p == f'disk=LABEL={fs}' else p
             for p in cmdline.split(' ')]
    new = ' '.join(parts)
    if len(new.encode()) > CMD_LEN - 1:
        die(f'new cmdline is {len(new.encode())} B, the header field holds {CMD_LEN - 1}')
    return new


def main(argv):
    if len(argv) == 3 and argv[1] == 'show':
        _d, cmd = read_img(argv[2])
        print(cmd)
        return 0
    if len(argv) != 7:
        print(__doc__.strip())
        return 2
    src, dst, fb, fs, tb, ts = argv[1:7]
    for lbl in (tb, ts):
        if not lbl or len(lbl) > 16 or any(c in lbl for c in ' \t\0"\''):
            die(f'bad label {lbl!r}')
    d, cmd = read_img(src)
    new = relabel(cmd, fb, fs, tb, ts)
    field = new.encode().ljust(CMD_LEN, b'\0')
    out = d[:CMD_OFF] + field + d[CMD_OFF + CMD_LEN:]
    assert len(out) == len(d) and out[:CMD_OFF] == d[:CMD_OFF] and out[CMD_OFF + CMD_LEN:] == d[CMD_OFF + CMD_LEN:]
    chk, chk_cmd = None, None
    tmp = dst + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(out)
    os.replace(tmp, dst)
    _d2, chk_cmd = read_img(dst)            # read back through the same parser
    if chk_cmd != new:
        die(f'read-back mismatch: wrote {new!r}, read {chk_cmd!r}')
    print(f'RELABEL_OK sha={hashlib.sha256(out).hexdigest()} cmdline={new}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
