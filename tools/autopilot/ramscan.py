#!/usr/bin/env python3
"""ramscan — host-side guest-RAM search for the ETK autopilot (numpy).

The rig has no numpy, so the first pass runs here over a `gtpilot.py snap`
(.npz: one uint8 array per committed guest range, key "r<start hex>"); after
that, candidate sets are small enough to narrow LIVE through the session (a
few thousand preads on the rig take milliseconds). That is the autogamer
ramwatch loop, moved to a big-endian PS3 address space.

Candidates are an .npz with `addr` (uint32 guest addresses) and `kind`.
"""
import json

import numpy as np

DT = {"f32": ">f4", "u32": ">u4", "s32": ">i4", "u16": ">u2", "s16": ">i2", "u8": "u1", "f64": ">f8"}
WIDTH = {k: np.dtype(v).itemsize for k, v in DT.items()}


def load_snap(path):
    z = np.load(path)
    meta = json.loads(str(z["meta"]))
    regions = {int(k[1:], 16): z[k] for k in z.files if k.startswith("r")}
    return meta, regions


def values_at(regions, addr, kind):
    """Gather `kind` values at guest addresses from a snapshot (NaN/0 if outside)."""
    w = WIDTH[kind]
    starts = np.array(sorted(regions), dtype=np.int64)
    out = np.zeros(len(addr), dtype=np.dtype(DT[kind]).newbyteorder("="))
    if kind.startswith("f"):
        out[:] = np.nan
    idx = np.searchsorted(starts, addr, side="right") - 1
    for i, s in enumerate(starts):
        sel = np.nonzero(idx == i)[0]
        if not len(sel):
            continue
        buf = regions[int(s)]
        off = addr[sel].astype(np.int64) - s
        ok = (off >= 0) & (off + w <= len(buf))
        sel, off = sel[ok], off[ok]
        raw = np.stack([buf[off + j] for j in range(w)], axis=1).copy()
        out[sel] = raw.view(DT[kind]).reshape(-1).astype(out.dtype)
    return out


def first_pass(regions, kind, lo, hi, limit=None):
    """Every aligned `kind` value in [lo, hi] (finite only for floats)."""
    w = WIDTH[kind]
    hits = []
    for s, buf in sorted(regions.items()):
        n = len(buf) // w * w
        v = buf[:n].view(DT[kind])
        m = (v >= lo) & (v <= hi)
        if kind.startswith("f"):
            m &= np.isfinite(v)
        hits.append(np.nonzero(m)[0].astype(np.int64) * w + s)
    addr = np.concatenate(hits) if hits else np.zeros(0, dtype=np.int64)
    if limit and len(addr) > limit:
        raise SystemExit(f"{len(addr)} candidates > limit {limit}: tighten the range")
    return addr.astype(np.uint32)


def filt(addr, vals, op, a=None, b=None, ref=None):
    """Keep candidates whose value satisfies op. ref = values from an earlier snap."""
    v = vals.astype(np.float64)
    if op == "range":
        m = (v >= a) & (v <= b)
    elif op == "eq":
        m = np.abs(v - a) <= (b or 0)
    elif op == "gt":  # increased vs ref by more than a
        m = v > ref + (a or 0)
    elif op == "lt":  # decreased vs ref by more than a
        m = v < ref - (a or 0)
    elif op == "same":
        m = np.abs(v - ref) <= (a or 0)
    elif op == "changed":
        m = np.abs(v - ref) > (a or 0)
    else:
        raise ValueError(op)
    m &= np.isfinite(v)
    return addr[m]


def save_cand(path, addr, kind):
    np.savez(path, addr=np.asarray(addr, dtype=np.uint32), kind=kind)


def load_cand(path):
    z = np.load(path)
    return z["addr"].astype(np.int64), str(z["kind"])
