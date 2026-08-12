#!/usr/bin/env python3
# =============================================================================
# etk_drift.py  -  ETK OS-migration drift detector for Rocknix
# -----------------------------------------------------------------------------
# WHAT IT DOES
#   Captures a structured, build_id-keyed profile of the Rocknix OS surfaces
#   ETK depends on (thermal-zone layout, input enumeration, GPU/Turnip driver,
#   CPU clusters) so you can answer one question per nightly:
#       "what drifted, and will ETK core break if I adopt this?"
#
#   Subcommands:
#     --save-baseline [--pin]  Bank the live OS as a known-good profile. The
#                              first baseline (or any saved with --pin) becomes
#                              the pin that --diff compares against.
#     --diff [BUILD_ID|pin]    Diff the live OS against a banked baseline
#                              (default: the pin). Reports temporal drift AND
#                              profile-assumption validation, then a verdict.
#     --check                  No baseline needed. Validate the live OS against
#                              the device profile's pinned assumptions
#                              (scripts/profiles/<SOC>.sh). Run it the moment
#                              you flash a nightly.
#     --list                   List banked baselines (pin marked).
#     --thermal SECONDS        Append a live thermal-load sample to any report.
#     (no args)                Print the live profile, then run --check.
#
# RUNS ON THE RIG. Reads /sys (read-only for system state). The ONLY thing it
# writes is JSON profiles under $ETK_ROOT/vault/os_profiles/. It changes no
# system settings and makes no network connections.
#
# Stdlib only. Every probe is wrapped so a missing file never aborts the run;
# unsupported fields become null.
# =============================================================================

import os, sys, glob, time, json, re, argparse
from collections import namedtuple

SCHEMA = "ETK-DRIFT v1"
ETK_ROOT = os.environ.get("ETK_ROOT", "/storage/games-internal/roms/etk")
PROFILES_DIR = os.path.join(ETK_ROOT, "vault", "os_profiles")
PIN_PATH = os.path.join(PROFILES_DIR, "pin.json")
PROFILES_SRC = os.path.join(ETK_ROOT, "scripts", "profiles")

Finding = namedtuple("Finding", "sev field old new affects action")
SEV_ORDER = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
SEV_COLOR = {"CRITICAL": "\033[31m", "WARN": "\033[33m", "INFO": "\033[36m"}
RESET = "\033[0m"

# ---- tiny safe helpers -------------------------------------------------------

def read_text(path, limit=4096):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(limit).strip()
    except Exception:
        return None

def read_dt(name):
    # device-tree props are NUL-terminated / NUL-separated byte strings
    try:
        with open("/proc/device-tree/" + name, "rb") as f:
            raw = f.read(4096)
        parts = [p.decode("ascii", "replace") for p in raw.split(b"\x00") if p]
        return ", ".join(parts) if parts else None
    except Exception:
        return None

def first_existing(paths):
    for p in paths:
        v = read_text(p)
        if v:
            return v
    return None

# ---- ARM core part-number lookup (best effort; freq grouping is the backup) --

ARM_PARTS = {
    0xd03: "Cortex-A53", 0xd05: "Cortex-A55", 0xd07: "Cortex-A57",
    0xd08: "Cortex-A72", 0xd09: "Cortex-A73", 0xd0a: "Cortex-A75",
    0xd0b: "Cortex-A76", 0xd0d: "Cortex-A77", 0xd41: "Cortex-A78",
    0xd44: "Cortex-X1",  0xd46: "Cortex-A510", 0xd47: "Cortex-A710",
    0xd48: "Cortex-X2",  0xd4d: "Cortex-A715", 0xd4e: "Cortex-X3",
}
IMPL = {0x41: "ARM", 0x51: "Qualcomm"}

def cpu_parts():
    txt = read_text("/proc/cpuinfo", limit=65536)
    if not txt:
        return None
    impls, parts = [], []
    for line in txt.splitlines():
        if line.startswith("CPU implementer"):
            impls.append(line.split(":")[-1].strip())
        elif line.startswith("CPU part"):
            parts.append(line.split(":")[-1].strip())
    if not parts:
        return None
    seen, out = set(), []
    for i, p in enumerate(parts):
        try:
            impl = int(impls[i], 16) if i < len(impls) else None
            pn = int(p, 16)
        except Exception:
            continue
        key = (impl, pn)
        if key in seen:
            continue
        seen.add(key)
        name = ARM_PARTS.get(pn) if impl == 0x41 else None
        label = name or ("Kryo/%s" % p if impl == 0x51 else p)
        out.append("%s:%s=%s" % (IMPL.get(impl, hex(impl) if impl else "?"),
                                 p, label))
    return "  ".join(out) if out else None

def cpu_topology():
    freqs = []
    for d in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*"),
                    key=lambda x: int(x.rsplit("cpu", 1)[-1]) if x.rsplit("cpu",1)[-1].isdigit() else 0):
        v = read_text(os.path.join(d, "cpufreq/cpuinfo_max_freq"))
        if v and v.isdigit():
            freqs.append(int(v))
    if not freqs:
        return None
    # group consecutive identical max-freqs into clusters (reveals 1+3+4 etc.)
    groups = []
    for f in freqs:
        if groups and groups[-1][0] == f:
            groups[-1][1] += 1
        else:
            groups.append([f, 1])
    return " + ".join("%dx%.2fGHz" % (n, khz / 1e6) for khz, n in groups)

def cpu_policies():
    out = []
    for p in glob.glob("/sys/devices/system/cpu/cpufreq/policy*"):
        s = p.rsplit("policy", 1)[-1]
        if s.isdigit():
            out.append(int(s))
    return sorted(out)

# ---- thermal -----------------------------------------------------------------

def thermal_zone_map():
    # index -> type string. Temps are runtime-variable, so the structural
    # baseline records the index->sensor mapping only (that's what drifts).
    out = {}
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        idx = z.rsplit("thermal_zone", 1)[-1]
        if not idx.isdigit():
            continue
        out[idx] = read_text(os.path.join(z, "type")) or "?"
    return out

def thermal_sample(seconds):
    zones = sorted(glob.glob("/sys/class/thermal/thermal_zone*"),
                   key=lambda p: int(p.rsplit("thermal_zone",1)[-1]) if p.rsplit("thermal_zone",1)[-1].isdigit() else 999)
    labels = {z: (read_text(os.path.join(z, "type")) or "?") for z in zones}
    stats = {z: [None, None, None] for z in zones}  # min,max,last (C)
    end = time.time() + seconds
    while time.time() < end:
        for z in zones:
            raw = read_text(os.path.join(z, "temp"))
            if raw and raw.lstrip("-").isdigit():
                c = int(raw) / 1000.0
                mn, mx, _ = stats[z]
                stats[z] = [c if mn is None else min(mn, c),
                            c if mx is None else max(mx, c), c]
        time.sleep(1)
    rows = []
    for z in zones:
        mn, mx, last = stats[z]
        if mx is None:
            continue
        rows.append("  %s  %-22s min=%.1f max=%.1f last=%.1f"
                    % (os.path.basename(z), labels[z], mn, mx, last))
    return rows or None

# ---- gpu / driver ------------------------------------------------------------

def gpu_model():
    return first_existing([
        "/sys/class/kgsl/kgsl-3d0/gpu_model",
        "/sys/class/kgsl/kgsl-3d0/device/gpu_model",
    ])

def gpu_maxclk():
    v = first_existing([
        "/sys/class/kgsl/kgsl-3d0/max_gpuclk",
        "/sys/class/kgsl/kgsl-3d0/devfreq/max_freq",
    ])
    if v and v.isdigit():
        return "%.0f MHz" % (int(v) / 1e6)
    return v

def turnip_driver():
    hits = []
    for d in ("/usr/lib", "/usr/lib/aarch64-linux-gnu", "/usr/lib64"):
        hits += glob.glob(os.path.join(d, "libvulkan_freedreno*"))
    return os.path.basename(hits[0]) if hits else None

def turnip_version():
    # only available after RPCS3 has run once and written its log
    txt = read_text("/storage/.cache/rpcs3/RPCS3.log", limit=200000)
    if not txt:
        return None
    for line in txt.splitlines():
        low = line.lower()
        if "turnip" in low or ("mesa" in low and "adreno" in low):
            return line.strip()[:160]
    return None

# ---- input devices -----------------------------------------------------------

def input_map():
    # event node -> device name. The enumeration that broke find_gamepad() on
    # the DS5 migration; keying by node makes a remap show up as a diff.
    out = {}
    base = "/sys/class/input/"
    try:
        for e in os.listdir(base):
            if not e.startswith("event"):
                continue
            out[e] = read_text(os.path.join(base, e, "device/name")) or "?"
    except Exception:
        pass
    return out

# ---- os / misc ---------------------------------------------------------------

def os_release():
    txt = read_text("/etc/os-release")
    if not txt:
        return {}
    d = {}
    for line in txt.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"')
    return d

def mem_total():
    txt = read_text("/proc/meminfo")
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("MemTotal"):
            kb = int(line.split()[1])
            return "%.1f GB" % (kb / 1048576.0)
    return None

def storage_info():
    for path in ("/storage", "/"):
        try:
            s = os.statvfs(path)
            total = s.f_blocks * s.f_frsize / 1e9
            free = s.f_bavail * s.f_frsize / 1e9
            return "%s: %.0f GB free / %.0f GB" % (path, free, total)
        except Exception:
            continue
    return None

def etk_status():
    if not os.path.isdir(ETK_ROOT):
        return {"installed": False, "vault_game_ids": None}
    vault = os.path.join(ETK_ROOT, "vault")
    ids = None
    try:
        for chip in os.listdir(vault):
            if chip == "os_profiles":  # our own baselines, not a chipset
                continue
            cpath = os.path.join(vault, chip)
            if os.path.isdir(cpath):
                ids = (ids or 0) + sum(1 for x in os.listdir(cpath)
                                       if os.path.isdir(os.path.join(cpath, x)))
    except Exception:
        pass
    return {"installed": True, "vault_game_ids": ids}

def chipset_short(compatible):
    # "qcom,sm8250 qcom,..."-> "sm8250"
    if not compatible:
        return "unknown"
    for tok in compatible.replace(",", " ").split():
        if tok.lower().startswith("sm") and any(ch.isdigit() for ch in tok):
            return tok.lower()
    return compatible.split(",")[-1].split()[0]

# ---- profile capture ---------------------------------------------------------

def capture_profile():
    osr = os_release()
    compatible = read_dt("compatible")
    try:
        u = os.uname()
        kr, km = u.release, u.machine
    except Exception:
        kr, km = None, None
    return {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # ROCKNIX os-release uses OS_VERSION (the nightly date, e.g. 20260525)
        # and BUILD_ID (the git SHA). Key human-facing on the date; keep the SHA
        # as a precise secondary id.
        "version": osr.get("OS_VERSION") or osr.get("VERSION_ID") or "unknown",
        "build_id": osr.get("BUILD_ID") or "unknown",
        "device": {
            "model": read_dt("model"),
            "soc_compatible": compatible,
            "soc_machine": read_text("/sys/devices/soc0/machine"),
            "soc_id": read_text("/sys/devices/soc0/soc_id"),
            "chipset": chipset_short(compatible),
        },
        "os": {
            "name": osr.get("OS_NAME") or osr.get("NAME"),
            "version": osr.get("OS_VERSION") or osr.get("VERSION"),
            "channel": osr.get("OS_BUILD"),
            "build_id": osr.get("BUILD_ID") or osr.get("VERSION_ID"),
            "build_date": osr.get("BUILD_DATE"),
            "kernel_release": kr,
            "kernel_machine": km,
        },
        "cpu": {
            "topology": cpu_topology(),
            "parts": cpu_parts(),
            "policies": cpu_policies(),
        },
        "gpu": {
            "model": gpu_model(),
            "max_clk": gpu_maxclk(),
            "turnip_driver": turnip_driver(),
            "turnip_version": turnip_version(),
        },
        "thermal_zones": thermal_zone_map(),
        "input": input_map(),
        "mem_total": mem_total(),
        "storage": storage_info(),
        "etk": etk_status(),
    }

# ---- persistence -------------------------------------------------------------

def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "unknown")

def write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception as e:
        sys.stderr.write("ERROR: cannot write %s: %s\n" % (path, e))
        return False

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def load_pin():
    return read_json(PIN_PATH)

def profile_key(p):
    # Filename / lookup key: the nightly date when known, else the SHA.
    v = p.get("version")
    return v if (v and v != "unknown") else (p.get("build_id") or "unknown")

def profile_label(p):
    v = p.get("version") or "?"
    bid = (p.get("build_id") or "")[:12]
    return "%s (%s)" % (v, bid) if bid else v

# ---- device-profile constants (scripts/profiles/<SOC>.sh) --------------------

def parse_profile_consts(path):
    consts = {}
    txt = read_text(path, limit=65536)
    if not txt:
        return consts
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if not m:
            continue
        val = m.group(2).split("#", 1)[0].strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        consts[m.group(1)] = val
    return consts

def parse_zone_map(s):
    # "1:cpu0 5:cluster0 14:cpu7_bot ..." -> {1:"cpu0", 5:"cluster0", ...}
    out = {}
    for tok in (s or "").split():
        if ":" in tok:
            i, lbl = tok.split(":", 1)
            if i.isdigit():
                out[int(i)] = lbl
    return out

def label_matches(label, live_type):
    # Heuristic: does any >=3-char token of ETK's shorthand label appear in the
    # kernel's live type string? Used only for soft WARNs in --check; --diff is
    # the authoritative type-vs-type comparison.
    lt = (live_type or "").lower()
    for tok in re.split(r"[^a-z0-9]+", label.lower()):
        if len(tok) >= 3 and tok in lt:
            return True
    return False

def load_consts(profile):
    chip = (profile["device"].get("chipset") or "").upper()
    return parse_profile_consts(os.path.join(PROFILES_SRC, "%s.sh" % chip))

# ---- diff axes ---------------------------------------------------------------

def _diff_scalar(out, base, cur, path, sev, affects, action):
    keys = path.split(".")
    b = base
    c = cur
    for k in keys:
        b = (b or {}).get(k)
        c = (c or {}).get(k)
    if b != c:
        out.append(Finding(sev, path, b, c, affects, action))

def _diff_map(out, base, cur, label, change_sev, affects, action):
    keys = sorted(set(base) | set(cur),
                  key=lambda k: (len(k), k))
    for k in keys:
        b, c = base.get(k), cur.get(k)
        if b == c:
            continue
        if b is None:
            out.append(Finding("WARN", "%s.%s (added)" % (label, k), None, c, affects, action))
        elif c is None:
            out.append(Finding(change_sev, "%s.%s (removed)" % (label, k), b, None, affects, action))
        else:
            out.append(Finding(change_sev, "%s.%s" % (label, k), b, c, affects, action))

def diff_profiles(base, cur):
    out = []
    _diff_scalar(out, base, cur, "device.chipset", "CRITICAL",
                 "entire scripts/profiles/<SOC>.sh",
                 "baseline is from a different SoC — compare against the right pin")
    _diff_scalar(out, base, cur, "os.version", "INFO",
                 "version strings (README / TRACK_MANUAL / env.sh recal comment)",
                 "reconcile the pinned version strings to this nightly")
    _diff_scalar(out, base, cur, "os.build_id", "INFO",
                 "build identity (git SHA)",
                 "informational — confirms the build actually changed")
    _diff_scalar(out, base, cur, "os.kernel_release", "WARN",
                 "kernel-dependent /sys node paths",
                 "spot-check devfreq / thermal node paths still resolve")
    _diff_scalar(out, base, cur, "gpu.model", "CRITICAL",
                 "SM8250.sh GPU_ADAPTER_STRING, RPCS3 adapter pin",
                 "re-pin the GPU adapter string")
    _diff_scalar(out, base, cur, "gpu.turnip_driver", "CRITICAL",
                 "shader vault validity, install.sh Mesa fingerprint",
                 "run tools/vault_sweep.sh; confirm soname unchanged")
    _diff_scalar(out, base, cur, "gpu.turnip_version", "WARN",
                 "Turnip rendering behavior",
                 "re-test per-game RPCS3 settings on the new driver")
    _diff_scalar(out, base, cur, "gpu.max_clk", "WARN",
                 "GPU clock headroom",
                 "re-check thermal/perf assumptions")
    _diff_scalar(out, base, cur, "cpu.topology", "WARN",
                 "cluster assumptions",
                 "re-verify cluster layout")
    _diff_scalar(out, base, cur, "cpu.policies", "CRITICAL",
                 "SM8250.sh CPU_POLICY_*, thermal_d.sh PIT cap",
                 "re-verify cpufreq policy IDs")
    _diff_map(out, base.get("thermal_zones", {}), cur.get("thermal_zones", {}),
              "thermal_zone", "CRITICAL",
              "SM8250.sh THERMAL_ZONE_MAP/GOVERNING, bin/thermal_d.sh, scripts/etk_probe.sh",
              "re-verify zone indices; update THERMAL_ZONE_* and the probe map")
    _diff_map(out, base.get("input", {}), cur.get("input", {}),
              "input", "CRITICAL",
              "bin/input_d.py find_gamepad(), bin/etk_pitstop.py",
              "re-verify gamepad node; update PAD_HINTS / PAD_EXCLUDE")
    return out

def validate_assumptions(consts, cur):
    out = []
    if not consts:
        return [Finding("INFO", "device-profile", None, None,
                        "scripts/profiles/<SOC>.sh",
                        "no device profile for this chipset — cannot validate pinned assumptions")]
    live_zones = cur.get("thermal_zones", {})
    tzmap = parse_zone_map(consts.get("THERMAL_ZONE_MAP", ""))

    gov = consts.get("THERMAL_ZONE_GOVERNING")
    if gov and gov.isdigit():
        lt = live_zones.get(gov)
        label = tzmap.get(int(gov))
        if lt is None:
            out.append(Finding("CRITICAL", "THERMAL_ZONE_GOVERNING=%s" % gov, label, "MISSING",
                               "bin/thermal_d.sh", "governing zone gone — re-find the cpu7-bottom zone"))
        elif label and not label_matches(label, lt):
            out.append(Finding("WARN", "THERMAL_ZONE_GOVERNING=%s" % gov, label, lt,
                               "bin/thermal_d.sh, SM8250.sh",
                               "governing zone type no longer resembles '%s' — verify by hand" % label))

    for idx, label in sorted(tzmap.items()):
        lt = live_zones.get(str(idx))
        if lt is None:
            out.append(Finding("CRITICAL", "THERMAL_ZONE_MAP[%d]" % idx, label, "MISSING",
                               "SM8250.sh THERMAL_ZONE_MAP, scripts/etk_probe.sh",
                               "zone index no longer present — re-map"))
        elif not label_matches(label, lt):
            out.append(Finding("WARN", "THERMAL_ZONE_MAP[%d]" % idx, label, lt,
                               "SM8250.sh THERMAL_ZONE_MAP",
                               "index may now map to a different sensor — verify"))

    for key in ("GPU_DEVFREQ_NODE", "GPU_GOVERNOR_PATH"):
        p = consts.get(key)
        if p and not os.path.exists(p):
            out.append(Finding("CRITICAL", key, p, "MISSING",
                               "SM8250.sh, bin/thermal_d.sh GPU control",
                               "GPU control node relocated — find the new path"))

    adapter = consts.get("GPU_ADAPTER_STRING")
    if adapter:
        model = cur.get("gpu", {}).get("model")
        m = re.search(r"(\d{3,4})", adapter)
        if not model:
            # Missing data is not evidence of mismatch: the kgsl gpu_model sysfs
            # attribute is not exposed on every kernel. Report it, do not alarm.
            out.append(Finding("INFO", "GPU_ADAPTER_STRING", adapter, "gpu model unreadable",
                               "RPCS3 adapter pin",
                               "kgsl gpu_model sysfs not exposed on this kernel — confirm via RPCS3.log that it binds Adreno 650"))
        elif m and m.group(1) not in model:
            out.append(Finding("WARN", "GPU_ADAPTER_STRING", adapter, model,
                               "RPCS3 adapter pin",
                               "live GPU model differs from pinned adapter — verify RPCS3 binds the right GPU"))

    for key in ("CPU_POLICY_SILVER", "CPU_POLICY_GOLD", "CPU_POLICY_PRIME"):
        pid = consts.get(key)
        if pid and pid.isdigit() and not os.path.isdir("/sys/devices/system/cpu/cpufreq/policy%s" % pid):
            out.append(Finding("CRITICAL", key, "policy%s" % pid, "MISSING",
                               "SM8250.sh, bin/thermal_d.sh PIT cap",
                               "cpufreq policy id changed — re-verify cluster policies"))
    return out

# ---- reporting ---------------------------------------------------------------

def color(sev, s):
    if sys.stdout.isatty():
        return SEV_COLOR.get(sev, "") + s + RESET
    return s

def fmt(v):
    if v is None:
        return "n/a"
    s = str(v)
    return s if len(s) <= 88 else s[:85] + "..."

def print_findings(title, findings):
    print("== %s ==" % title)
    if not findings:
        print("  no drift detected")
        return 0, 0
    crit = warn = 0
    for x in sorted(findings, key=lambda y: SEV_ORDER.get(y.sev, 9)):
        if x.sev == "CRITICAL":
            crit += 1
        elif x.sev == "WARN":
            warn += 1
        print("  %s %s" % (color(x.sev, "[%-8s]" % x.sev), x.field))
        print("             %s  ->  %s" % (fmt(x.old), fmt(x.new)))
        print("             affects: %s" % x.affects)
        print("             action:  %s" % x.action)
    return crit, warn

def print_thermal(seconds):
    print("== THERMAL LOAD SAMPLE (%ds) ==" % seconds)
    rows = thermal_sample(seconds)
    print("\n".join(rows) if rows else "  n/a")
    print("")

def print_profile(p):
    d, o, g = p["device"], p["os"], p["gpu"]
    print("[%s]  nightly=%s  build_id=%s  generated=%s" % (p["schema"], p.get("version"), p["build_id"], p["generated_utc"]))
    print("-- DEVICE --")
    print("  model:          %s" % fmt(d.get("model")))
    print("  chipset:        %s" % fmt(d.get("chipset")))
    print("  soc.compatible: %s" % fmt(d.get("soc_compatible")))
    print("-- OS / KERNEL --")
    print("  os:             %s %s (%s)" % (fmt(o.get("name")), o.get("version") or "", o.get("channel") or ""))
    print("  build_id:       %s" % fmt(o.get("build_id")))
    print("  build_date:     %s" % fmt(o.get("build_date")))
    print("  kernel:         %s %s" % (fmt(o.get("kernel_release")), o.get("kernel_machine") or ""))
    print("-- CPU --")
    print("  topology:       %s" % fmt(p["cpu"].get("topology")))
    print("  policies:       %s" % fmt(p["cpu"].get("policies")))
    print("-- GPU / DRIVER --")
    print("  model:          %s" % fmt(g.get("model")))
    print("  max_clk:        %s" % fmt(g.get("max_clk")))
    print("  turnip.driver:  %s" % fmt(g.get("turnip_driver")))
    print("  turnip.version: %s" % fmt(g.get("turnip_version")))
    print("-- THERMAL ZONES (index -> type) --")
    tz = p.get("thermal_zones", {})
    for k in sorted(tz, key=lambda x: int(x) if x.isdigit() else 999):
        print("  zone%-3s %s" % (k, tz[k]))
    print("-- INPUT DEVICES (node -> name) --")
    iv = p.get("input", {})
    for k in sorted(iv, key=lambda x: int(x[5:]) if x[5:].isdigit() else 999):
        print("  %-9s %s" % (k, iv[k]))
    print("-- MEMORY / STORAGE --")
    print("  mem.total:      %s" % fmt(p.get("mem_total")))
    print("  storage:        %s" % fmt(p.get("storage")))

def verdict(crit, warn):
    if crit:
        print(color("CRITICAL", "VERDICT: %d CRITICAL / %d WARN  -  do NOT adopt without recal" % (crit, warn)))
    elif warn:
        print(color("WARN", "VERDICT: %d WARN  -  review before adopting" % warn))
    else:
        print(color("INFO", "VERDICT: no structural drift  -  safe to adopt"))

# ---- commands ----------------------------------------------------------------

def cmd_save_baseline(pin):
    prof = capture_profile()
    path = os.path.join(PROFILES_DIR, "%s.json" % safe_name(profile_key(prof)))
    if not write_json(path, prof):
        return 2
    msg = "saved baseline %s -> %s" % (profile_label(prof), path)
    if pin or load_pin() is None:
        if write_json(PIN_PATH, prof):
            msg += "   [PINNED]"
    print(msg)
    return 0

def cmd_diff(target, thermal_secs):
    cur = capture_profile()
    if target in (None, "pin"):
        base = load_pin()
        if base is None:
            sys.stderr.write("ERROR: no pinned baseline. Run --save-baseline --pin first.\n")
            return 2
    else:
        base = read_json(os.path.join(PROFILES_DIR, "%s.json" % safe_name(target)))
        if base is None:
            sys.stderr.write("ERROR: no baseline '%s' in %s\n" % (target, PROFILES_DIR))
            return 2
    print("[%s] diff" % SCHEMA)
    print("  baseline: %s   (%s)" % (profile_label(base), base.get("generated_utc")))
    print("  current:  %s   (%s)" % (profile_label(cur), cur.get("generated_utc")))
    print("")
    c1, w1 = print_findings("TEMPORAL DRIFT (baseline -> live)", diff_profiles(base, cur))
    print("")
    chip = (cur["device"].get("chipset") or "?").upper()
    c2, w2 = print_findings("PROFILE-ASSUMPTION VALIDATION (live vs %s.sh)" % chip,
                            validate_assumptions(load_consts(cur), cur))
    print("")
    if thermal_secs:
        print_thermal(thermal_secs)
    verdict(c1 + c2, w1 + w2)
    return 1 if (c1 + c2) else 0

def cmd_check(thermal_secs):
    cur = capture_profile()
    chip = (cur["device"].get("chipset") or "?").upper()
    print("[%s] check  (live OS vs device profile)" % SCHEMA)
    print("  chipset: %s   nightly: %s" % (cur["device"].get("chipset"), profile_label(cur)))
    print("")
    crit, warn = print_findings("PROFILE-ASSUMPTION VALIDATION (live vs %s.sh)" % chip,
                                validate_assumptions(load_consts(cur), cur))
    print("")
    if thermal_secs:
        print_thermal(thermal_secs)
    verdict(crit, warn)
    return 1 if crit else 0

def cmd_list():
    if not os.path.isdir(PROFILES_DIR):
        print("no baselines yet (%s does not exist)" % PROFILES_DIR)
        return 0
    pin = load_pin()
    pin_key = profile_key(pin) if pin else None
    rows = []
    for fn in sorted(os.listdir(PROFILES_DIR)):
        if not fn.endswith(".json") or fn == "pin.json":
            continue
        prof = read_json(os.path.join(PROFILES_DIR, fn))
        if not prof:
            continue
        mark = "  *PIN" if profile_key(prof) == pin_key else ""
        rows.append("  %-12s %-14s %s%s" % (prof.get("version") or "?",
                    (prof.get("build_id") or "")[:12], prof.get("generated_utc", ""), mark))
    print("baselines in %s:" % PROFILES_DIR)
    print("\n".join(rows) if rows else "  (none)")
    return 0

def cmd_default(thermal_secs):
    cur = capture_profile()
    print_profile(cur)
    print("")
    chip = (cur["device"].get("chipset") or "?").upper()
    crit, warn = print_findings("PROFILE-ASSUMPTION VALIDATION (live vs %s.sh)" % chip,
                                validate_assumptions(load_consts(cur), cur))
    print("")
    if thermal_secs:
        print_thermal(thermal_secs)
    verdict(crit, warn)
    return 1 if crit else 0

# ---- entry -------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        prog="etk_drift.py",
        description="ETK OS-migration drift detector (runs on the rig, reads /sys).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--save-baseline", action="store_true",
                   help="bank the live OS as a known-good profile")
    g.add_argument("--diff", nargs="?", const="pin", default=None, metavar="BUILD_ID",
                   help="diff live OS against a baseline (default: the pin)")
    g.add_argument("--check", action="store_true",
                   help="validate live OS against the device profile's pinned assumptions")
    g.add_argument("--list", action="store_true",
                   help="list banked baselines")
    p.add_argument("--pin", action="store_true",
                   help="with --save-baseline: mark this profile as the pin")
    p.add_argument("--thermal", nargs="?", const=30, type=int, default=0, metavar="SECONDS",
                   help="append a live thermal-load sample to the report")
    a = p.parse_args()

    if a.list:
        return cmd_list()
    if a.save_baseline:
        return cmd_save_baseline(a.pin)
    if a.check:
        return cmd_check(a.thermal)
    if a.diff is not None:
        return cmd_diff(a.diff, a.thermal)
    return cmd_default(a.thermal)

if __name__ == "__main__":
    sys.exit(main())
