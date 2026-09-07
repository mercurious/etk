#!/usr/bin/env python3
"""ETK RADIO — PACK v1 builder. Reduce ONE session into a bounded, allowlisted JSON
bundle for the pit-radio analyst (docs/RADIO_SPEC.md §3.1, §8).

A pack is a reduction of what session_postmortem already wrote, keyed by the ledger row's
epoch ($NOW), which is the join key of every archive (rpcs3_logs/<epoch>.log,
mango_logs/<epoch>.csv, perf_logs, audio_logs, blackbox). It is built from an ALLOWLIST,
never from raw files: game serials, decoded ledger fields, and log lines with the argv
line dropped and paths reduced to dev_hdd0/game/<ID>. It is useful on its own as a
one-file forensic bundle, with or without a model, which is why it is the first thing
built.

Runs on the rig (paths from ETK_ROOT) AND host-side against the Tier-B mirror
(state/etk_telemetry/) — pass --ledger and the sibling archive dirs are found beside it.
Stdlib only. Every field is optional: a missing archive degrades to null/[] with a note in
`pack_notes`, never an abort. Calls tools/etk_dyno.py --json for the arms table (the N is
computed there, never guessed here — RADIO guard: no crown below N).

Usage:
  bin/radio_pack.py <epoch> [--ledger PATH] [--config-dir DIR] [--dyno PATH]
                    [--out PATH | --stdout] [--max-bytes 65536]
Default ledger on the rig: $TELEMETRY_DIR/sessions.tsv; host: state/etk_telemetry/sessions.tsv
Default out: <telemetry_dir>/radio/<epoch>.pack.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "ETK-RADIO-PACK v1"
HARD_CAP = 65536          # 64 KB; the service rejects larger (spec §3.1)
TAIL_BYTES = 4194304      # 4 MiB — the postmortem's bounded RPCS3.log read
MAX_RPCS3_ERRORS = 15
MAX_DMESG = 25
MAX_BLACKBOX = 40
MAX_HISTORY = 5
TIMELINE_BINS = 10

HEADER = ["epoch", "duration_s", "build", "game_id", "status", "peak_load", "peak_ram_mb",
          "peak_temp", "avg_temp", "crash_sig", "fence_at_crash", "shaders_harvested",
          "drain_pct", "thermal_overrides", "tune_tag", "crash_shot", "fps_med", "fps_1low",
          "ft_p99_ms", "res_scale", "gpu_mhz", "pwr", "ft_jitter_ms", "gpu_fault_status",
          "gpu_fault_fence_hex", "aud", "snd", "lock_pct", "perfect_pct", "rescues", "perf"]
NUMERIC = {"duration_s", "peak_load", "peak_ram_mb", "peak_temp", "avg_temp",
           "fence_at_crash", "shaders_harvested", "drain_pct", "thermal_overrides",
           "fps_med", "fps_1low", "ft_p99_ms", "res_scale", "gpu_mhz", "ft_jitter_ms",
           "lock_pct", "perfect_pct", "rescues"}


def _num(v):
    if v in ("", "-", None):
        return None
    try:
        n = float(v)
        return int(n) if n.is_integer() else round(n, 3)
    except ValueError:
        return v


def decode_row(fields):
    row = {}
    for i, name in enumerate(HEADER):
        v = fields[i] if i < len(fields) else ""
        row[name] = _num(v) if name in NUMERIC else v
    return row


def parse_kv(cell):
    """A comma-folded k=v telemetry cell (aud / perf / tune_tag pieces) to a dict."""
    out = {}
    if not cell or cell == "-":
        return out
    for tok in cell.split(","):
        k, sep, v = tok.partition("=")
        if not sep:
            continue
        out[k.strip()] = _num(v.strip())
    return out


def parse_tune_tag(tag):
    """tune_tag -> {build, stack, core, tu_debug, patches, ...} + any bare tokens."""
    out, bare = {}, []
    for tok in (tag or "").split(";"):
        if not tok:
            continue
        k, sep, v = tok.partition("=")
        if sep:
            out[k.strip()] = v.strip()
        else:
            bare.append(tok.strip())
    if bare:
        out["_bare"] = bare
    return out


def fault_class(status_hex):
    """A6XX_RBBM_STATUS fault code -> wedge class (manual §B.2)."""
    s = (status_hex or "").upper().lstrip("0X")
    if s.startswith("00C5") or s[:4].endswith("C5"):
        return "query park (#1)"
    if "E5" in s[:4] or s.startswith("00E5"):
        return "fence park (#2)"
    if s in ("", "-"):
        return None
    return f"unclassified ({status_hex})"


def redact(line, game_id):
    """Drop the argv line; reduce any absolute path to dev_hdd0/game/<ID> (spec §3.1)."""
    if "argv:" in line or "AppRun.wrapped" in line:
        return None
    line = re.sub(r"/[^\s'\"]*/(dev_hdd0/game/[A-Z0-9]+)", r"\1", line)
    line = re.sub(r"/tmp/\.mount_[^\s'\"]*", "<mount>", line)
    return line.strip()


def sib(telem, name):
    return telem / name


def load_rows(ledger):
    lines = ledger.read_text(errors="replace").splitlines()
    return [ln.split("\t") for ln in lines[1:] if ln and ln.split("\t")[0].isdigit()]


def rpcs3_errors(logdir, epoch, game_id, notes):
    p = sib(logdir, f"{epoch}.log")
    if not p.exists():
        notes.append(f"no rpcs3 log for {epoch}")
        return []
    with open(p, "rb") as fh:
        try:
            fh.seek(-TAIL_BYTES, os.SEEK_END)
        except OSError:
            fh.seek(0)
        blob = fh.read()
    text = blob.decode("latin-1", "replace")
    seen, out = {}, []
    for ln in text.splitlines():
        # postmortem reads the '·'-severity glyph as a bare E/F at column 0 after strings
        if re.match(r"^.?[EF] \d", ln) or re.match(r"^[EF] ", ln):
            r = redact(ln, game_id)
            if not r:
                continue
            key = re.sub(r"\d+", "#", r)[:80]
            if key in seen:
                seen[key]["n"] += 1
            else:
                seen[key] = {"n": 1, "line": r[:240]}
                out.append(seen[key])
        if len(out) >= MAX_RPCS3_ERRORS:
            break
    return out


def timeline(mangodir, epoch, notes):
    p = sib(mangodir, f"{epoch}.csv")
    if not p.exists():
        notes.append(f"no mango csv for {epoch}")
        return None
    lines = p.read_text(errors="replace").splitlines()
    hdr_i = next((i for i, ln in enumerate(lines) if ln.startswith("fps,frametime")), None)
    if hdr_i is None:
        notes.append("mango csv: no per-frame header")
        return None
    cols = lines[hdr_i].split(",")
    idx = {c: i for i, c in enumerate(cols)}
    rows = []
    for ln in lines[hdr_i + 1:]:
        parts = ln.split(",")
        if len(parts) < len(cols):
            continue
        try:
            rows.append([float(parts[i]) for i in range(len(cols))])
        except ValueError:
            continue
    if not rows:
        return None
    el = idx.get("elapsed", len(cols) - 1)
    t0, t1 = rows[0][el], rows[-1][el]
    span = (t1 - t0) or 1.0
    bins = [[] for _ in range(TIMELINE_BINS)]
    for r in rows:
        b = min(TIMELINE_BINS - 1, int((r[el] - t0) / span * TIMELINE_BINS))
        bins[b].append(r)

    def col(b, name, agg):
        i = idx.get(name)
        if i is None:
            return None
        vals = sorted(v[i] for v in b if v[i] > 0)
        if not vals:
            return None
        if agg == "med":
            return round(vals[len(vals) // 2], 1)
        if agg == "p99":
            return round(vals[min(len(vals) - 1, int(len(vals) * 0.99))], 1)
        if agg == "max":
            return round(vals[-1], 1)
        return None

    return {
        "bins": TIMELINE_BINS,
        "fps_med": [col(b, "fps", "med") for b in bins],
        "ft_p99_ms": [col(b, "frametime", "p99") for b in bins],
        "gpu_temp_c": [col(b, "gpu_temp", "max") for b in bins],
    }


def blackbox_tail(bbdir, epoch, notes):
    if not bbdir.exists():
        return []
    best, bestdiff = None, None
    for p in bbdir.glob("kmsg-*.log"):
        m = re.search(r"kmsg-(\d+)", p.name)
        if not m:
            continue
        d = abs(int(m.group(1)) - epoch)
        if bestdiff is None or d < bestdiff:
            best, bestdiff = p, d
    if best is None or bestdiff > 600:
        notes.append("no blackbox kmsg within 600 s of the row")
        return []
    lines = best.read_text(errors="replace").splitlines()
    return [ln[:240] for ln in lines[-MAX_BLACKBOX:]]


def read_config(config_dir, game_id, fields_json, notes):
    cfg = config_dir / f"config_{game_id}.yml"
    if not cfg.exists():
        notes.append(f"no config_{game_id}.yml in {config_dir}")
        return None
    want = {}
    if fields_json.exists():
        for fld in json.loads(fields_json.read_text()):
            key = (fld.get("yaml_key") or "").strip()
            if key:
                want[key] = fld.get("label", key)
    text = cfg.read_text(errors="replace")
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^(\s+[^:]+):\s*(.*?)\s*$", ln)
        if not m:
            continue
        k = m.group(1).strip()
        if not want or k in want:
            out[k] = m.group(2).strip('"')
    return {"source": str(cfg.name), "values": out}


def career(careerdir, game_id, notes):
    p = sib(careerdir, f"{game_id}.txt")
    if not p.exists():
        return None
    out = {}
    for ln in p.read_text(errors="replace").splitlines():
        k, sep, v = ln.partition("=")
        if sep:
            out[k.strip()] = _num(v.strip())
    return out


def recent_changes(telem, game_id, epoch, notes):
    p = sib(telem, "config_changes.tsv")
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(errors="replace").splitlines()[1:]:
        c = ln.split("\t")
        if len(c) < 5 or not c[0].isdigit():
            continue
        if c[1] == game_id and int(c[0]) <= epoch:
            out.append({"epoch": int(c[0]), "field": c[2], "old": c[3], "new": c[4]})
    return out[-MAX_HISTORY:]


def dyno_arms(dyno, ledger, game_id, res, notes):
    try:
        r = subprocess.run([sys.executable, str(dyno), "--json", "--ledger", str(ledger),
                            "--game", game_id, "--res", str(res)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            notes.append(f"dyno exit {r.returncode}: {r.stderr.strip()[:120]}")
            return None
        return json.loads(r.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        notes.append(f"dyno failed: {e}")
        return None


def build(epoch, ledger, config_dir, dyno, notes):
    telem = ledger.parent
    rows = load_rows(ledger)
    match = [r for r in rows if r and r[0] == str(epoch)]
    if not match:
        sys.exit(f"epoch {epoch} not found in {ledger}")
    row = decode_row(match[0])
    gid = row["game_id"]
    tune = parse_tune_tag(row.get("tune_tag", ""))
    pwr = row.get("pwr", "") or ""
    res = int(row.get("res_scale") or 0) or 100

    hist = [decode_row(r) for r in rows if r[3] == gid and r[0].isdigit()
            and int(r[0]) < int(epoch)][-MAX_HISTORY:]
    crash_ids = [c.strip() for c in (row.get("crash_sig") or "").split(",") if c.strip()]
    sigcat = {}
    scf = config_dir / "crash_signatures.json"
    if scf.exists():
        sigcat = {d["id"]: d for d in json.loads(scf.read_text())}
    sigs = [{"id": i, "label": sigcat[i].get("label"), "severity": sigcat[i].get("severity"),
             "summary": sigcat[i].get("summary")} for i in crash_ids if i in sigcat]
    unknown = [i for i in crash_ids if i not in sigcat]
    if unknown:
        notes.append("crash_sig not in catalog: " + ",".join(unknown))

    is_panic = row.get("status") == "PANIC"
    pack = {
        "schema": SCHEMA, "epoch": int(epoch), "game_id": gid,
        "rig": {
            "soc": os.environ.get("ETK_CHIPSET", "SM8250"),
            "build": tune.get("build"), "core": tune.get("core"),
            "stack": tune.get("stack"), "dial": tune.get("tu_debug"),
            "patches": tune.get("patches"),
            "kernel": (re.search(r"/k([0-9.]+)", tune.get("stack", "")) or [None, None])[1]
            if tune.get("stack") else None,
            "power": {"profile": pwr.split("+")[0] if pwr else None,
                      "grid": next((p for p in pwr.split("+")[1:]), None),
                      "gpu_mhz": row.get("gpu_mhz")},
        },
        "session": {
            **{k: row[k] for k in HEADER if k not in ("aud", "perf", "crash_sig", "tune_tag")},
            "crash_sig": crash_ids,
            "aud": parse_kv(row.get("aud", "")),
            "perf": parse_kv(row.get("perf", "")),
        },
        "history": {
            "rows": [{k: h[k] for k in ("epoch", "duration_s", "status", "shaders_harvested",
                                        "fps_med", "lock_pct", "perfect_pct", "rescues",
                                        "gpu_fault_status")} for h in hist],
            "career": career(sib(telem, "career"), gid, notes),
            "recent_changes": recent_changes(telem, gid, int(epoch), notes),
        },
        "dyno": dyno_arms(dyno, ledger, gid, res, notes),
        "crash": {
            "sigs": sigs,
            "fault": {"status": row.get("gpu_fault_status"),
                      "fence_hex": row.get("gpu_fault_fence_hex"),
                      "class": fault_class(row.get("gpu_fault_status"))}
            if row.get("gpu_fault_status") not in (None, "-", "") else None,
            "rpcs3_errors": rpcs3_errors(sib(telem, "rpcs3_logs"), epoch, gid, notes),
            "dmesg_window": [],   # host mirror carries no per-row dmesg; rig fills this
            "blackbox_tail": blackbox_tail(sib(telem, "blackbox"), int(epoch), notes) if is_panic else [],
        },
        "timeline": timeline(sib(telem, "mango_logs"), epoch, notes),
        "config": read_config(config_dir, gid, config_dir / "pitstop_fields.json", notes),
        "operator": {"feel": (sib(telem, "radio") / f"{epoch}.feel").read_text().strip()
                     if (sib(telem, "radio") / f"{epoch}.feel").exists() else "",
                     "note": ""},
        "pack_notes": notes,
    }
    if tune.get("_bare"):
        pack["rig"]["tune_bare"] = tune["_bare"]
    return pack


def trim_to_cap(pack, cap):
    """Shed the heaviest optional evidence until the pack fits, recording what went."""
    order = [("crash", "rpcs3_errors"), ("crash", "dmesg_window"),
             ("crash", "blackbox_tail")]
    while len(json.dumps(pack).encode()) > cap and order:
        sec, key = order.pop()
        if pack.get(sec, {}).get(key):
            n = len(pack[sec][key])
            pack[sec][key] = []
            pack["pack_notes"].append(f"trimmed {sec}.{key} ({n} items) to fit {cap} B")
    if len(json.dumps(pack).encode()) > cap and pack.get("timeline"):
        pack["timeline"] = {"bins": pack["timeline"]["bins"], "trimmed": True}
        pack["pack_notes"].append(f"trimmed timeline to fit {cap} B")
    return pack


def main():
    here = Path(__file__).resolve().parent
    default_ledger = (Path(os.environ["TELEMETRY_DIR"]) / "sessions.tsv"
                      if os.environ.get("TELEMETRY_DIR")
                      else here.parent / "state" / "etk_telemetry" / "sessions.tsv")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("epoch")
    ap.add_argument("--ledger", type=Path, default=default_ledger)
    ap.add_argument("--config-dir", type=Path, default=here.parent / "config")
    ap.add_argument("--dyno", type=Path, default=here.parent / "tools" / "etk_dyno.py")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=HARD_CAP)
    args = ap.parse_args()

    if not args.ledger.exists():
        sys.exit(f"ledger not found: {args.ledger}")
    notes = []
    pack = build(args.epoch, args.ledger, args.config_dir, args.dyno, notes)
    pack = trim_to_cap(pack, args.max_bytes)
    blob = json.dumps(pack, indent=1)
    size = len(blob.encode())
    pack["budget"] = {"bytes": size}
    blob = json.dumps(pack, indent=1)

    if args.stdout:
        sys.stdout.write(blob + "\n")
    else:
        out = args.out or (args.ledger.parent / "radio" / f"{args.epoch}.pack.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(blob)
        tmp.replace(out)
        print(f"wrote {out}  ({size} B, {'OK' if size <= args.max_bytes else 'OVER CAP'})")
    if notes:
        sys.stderr.write("notes: " + " · ".join(notes) + "\n")


if __name__ == "__main__":
    main()
