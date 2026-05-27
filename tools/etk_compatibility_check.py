#!/usr/bin/env python3
# =============================================================================
# etk_recon.py  -  ETK hardware-compatibility recon for Retroid Pocket / Rocknix
# -----------------------------------------------------------------------------
# WHAT IT DOES
#   Prints a single, paste-able hardware profile so the ETK project can map
#   what varies across Retroid Pocket devices (SoC, GPU, thermal-zone layout,
#   input enumeration) and figure out the per-chipset shader-vault namespaces.
#
# WHAT IT WILL *NOT* DO  (read before you run it on your device)
#   * It is READ-ONLY. It writes nothing to your device and changes no settings.
#   * It makes NO network connections. Nothing leaves your machine except the
#     text it prints to your terminal, which you choose to paste.
#   * It deliberately SKIPS anything identifying: no serial number, no MAC, no
#     IP address, no /proc/cmdline (which can contain a serial). If you spot
#     anything personal in the output, don't post it.
#
# HOW TO USE
#   1. SSH into your device (Rocknix).
#   2. Run:   python3 etk_recon.py
#   3. Copy everything between the ``` fences and paste it into a Reddit reply.
#   Optional dynamic thermal pass (run it DURING a heavy game for best data):
#             python3 etk_recon.py --thermal 30
#
# Stdlib only. Every probe is wrapped so a missing file never aborts the run;
# unsupported fields just print "n/a". Safe to run on non-Retroid hardware too.
# =============================================================================

import os, sys, glob, time, struct, subprocess

SCHEMA = "ETK-RECON v1"

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

def run(args, timeout=5):
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout
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

# ---- thermal -----------------------------------------------------------------

def thermal_zones():
    rows = []
    zones = glob.glob("/sys/class/thermal/thermal_zone*")
    def zidx(p):
        s = p.rsplit("thermal_zone", 1)[-1]
        return int(s) if s.isdigit() else 999
    for z in sorted(zones, key=zidx):
        t = read_text(os.path.join(z, "type")) or "?"
        raw = read_text(os.path.join(z, "temp"))
        c = "n/a"
        if raw:
            try:
                c = "%.1fC" % (int(raw) / 1000.0)
            except Exception:
                c = raw
        rows.append("  %s  %-22s %s" % (os.path.basename(z), t, c))
    return rows or None

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
    log = "/storage/.cache/rpcs3/RPCS3.log"
    txt = read_text(log, limit=200000)
    if not txt:
        return None
    for line in txt.splitlines():
        low = line.lower()
        if "turnip" in low or ("mesa" in low and "adreno" in low):
            return line.strip()[:160]
    return None

# ---- input devices -----------------------------------------------------------

def input_devices():
    rows = []
    base = "/sys/class/input/"
    try:
        entries = sorted([e for e in os.listdir(base) if e.startswith("event")],
                         key=lambda e: int(e[5:]) if e[5:].isdigit() else 999)
    except Exception:
        return None
    for e in entries:
        name = read_text(os.path.join(base, e, "device/name")) or "?"
        rows.append("  %s: %s" % (e, name))
    return rows or None

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
    root = "/storage/games-internal/roms/etk"
    if not os.path.isdir(root):
        return "not detected", None
    vault = os.path.join(root, "vault")
    ids = None
    try:
        for chip in os.listdir(vault):
            cpath = os.path.join(vault, chip)
            if os.path.isdir(cpath):
                ids = (ids or 0) + sum(1 for x in os.listdir(cpath)
                                       if os.path.isdir(os.path.join(cpath, x)))
    except Exception:
        pass
    return "installed", ids

def chipset_short(compatible):
    # "qcom,sm8250 qcom,..."-> "sm8250"
    if not compatible:
        return "unknown"
    for tok in compatible.replace(",", " ").split():
        if tok.lower().startswith("sm") and any(ch.isdigit() for ch in tok):
            return tok.lower()
    return compatible.split(",")[-1].split()[0]

# ---- assemble ----------------------------------------------------------------

def main():
    thermal_secs = 0
    if "--thermal" in sys.argv:
        try:
            thermal_secs = int(sys.argv[sys.argv.index("--thermal") + 1])
        except Exception:
            thermal_secs = 30

    osr = os_release()
    compatible = read_dt("compatible")
    chip = chipset_short(compatible)
    gpu = gpu_model() or "n/a"
    tver = turnip_version()
    vault_key = "%s / %s / %s" % (
        chip,
        (gpu.replace(" ", "") if gpu != "n/a" else "unknown-gpu"),
        ("turnip-known" if tver else "turnip-unknown(run RPCS3 once)"),
    )
    etk_state, vault_ids = etk_status()

    L = []
    w = L.append
    w("[%s]" % SCHEMA)
    w("generated_utc: %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    w("note: read-only probe, no PII (serial/MAC/IP intentionally omitted)")
    w("")
    w("-- DEVICE --")
    w("model:           %s" % (read_dt("model") or "n/a"))
    w("soc.compatible:  %s" % (compatible or "n/a"))
    w("soc.machine:     %s" % (read_text("/sys/devices/soc0/machine") or "n/a"))
    w("soc.id:          %s" % (read_text("/sys/devices/soc0/soc_id") or "n/a"))
    w("")
    w("-- OS / KERNEL --")
    w("os:              %s %s" % (osr.get("NAME", "n/a"), osr.get("VERSION", "")))
    w("build_id:        %s" % osr.get("BUILD_ID", osr.get("VERSION_ID", "n/a")))
    try:
        u = os.uname()
        w("kernel:          %s %s" % (u.release, u.machine))
    except Exception:
        w("kernel:          n/a")
    w("")
    w("-- CPU --")
    w("topology:        %s" % (cpu_topology() or "n/a"))
    w("parts:           %s" % (cpu_parts() or "n/a"))
    w("")
    w("-- GPU / DRIVER --")
    w("gpu.model:       %s" % gpu)
    w("gpu.max_clk:     %s" % (gpu_maxclk() or "n/a"))
    w("turnip.driver:   %s" % (turnip_driver() or "n/a"))
    w("turnip.version:  %s" % (tver or "run RPCS3 once to populate"))
    w("")
    w("-- THERMAL ZONES (static) --")
    tz = thermal_zones()
    if tz:
        L.extend(tz)
    else:
        w("  n/a")
    if thermal_secs:
        w("")
        w("-- THERMAL ZONES (%ds load sample) --" % thermal_secs)
        ts = thermal_sample(thermal_secs)
        L.extend(ts if ts else ["  n/a"])
    w("")
    w("-- INPUT DEVICES --")
    idv = input_devices()
    L.extend(idv if idv else ["  n/a"])
    w("")
    w("-- MEMORY / STORAGE --")
    w("mem.total:       %s" % (mem_total() or "n/a"))
    w("storage:         %s" % (storage_info() or "n/a"))
    w("")
    w("-- ETK --")
    w("etk.installed:   %s" % etk_state)
    w("vault.gameIDs:   %s" % ("n/a" if vault_ids is None else vault_ids))
    w("")
    w("-- DERIVED --")
    w("vault_key:       %s" % vault_key)
    w("[END %s]" % SCHEMA)

    print("Copy everything between the ``` lines below into your Reddit reply:\n")
    print("```")
    print("\n".join(L))
    print("```")

if __name__ == "__main__":
    main()