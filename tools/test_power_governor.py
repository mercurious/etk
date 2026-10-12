#!/usr/bin/env python3
"""ETK -- the POWER CPU governor must reach the GAME (GT5 hunt, 2026-10-11).

  python3 tools/test_power_governor.py

The bug: POWER race wrote `performance` to every cpufreq policy at boot and on APPLY, but
ROCKNIX's runemu.sh applies `cpugovernor` for the platform at EVERY launch (falling back to
system.cpugovernor=ondemand), after the kit -- so every RACE game ran ondemand. On SM8250 each
silver-cluster DVFS step casts an RPMh bandwidth vote; GT5's mid-race CPU0 lockup died on exactly
that path, and vanished once the governor really pinned (Hunt_GT5 §15-16). The fix hands the
profile's governor to ROCKNIX's own ps3.cpugovernor through ROCKNIX's own set_setting -- which is
bash-only ([[ =~ ]]), and silently no-ops under BusyBox sh (the operator's first attempt did).

The fake ROCKNIX functions below keep that bash-only test, so a `sh -c` call fails here too.

  [PITSTOP] POWER APPLY sets ps3.cpugovernor to the profile's cpu_gov (race -> performance,
            balanced -> ondemand) and says so in the footer; no ROCKNIX functions -> no-op, no raise
  [BOOT]    the install.sh applier hands the saved profile's governor to ROCKNIX at boot (existing
            RACE profiles get the fix without a re-APPLY); a profile without cpu_gov leaves it alone

DISCRIMINATION: against the pre-fix etk_pitstop.py + install.sh 6 of 8 checks fail (the applier body
exists and a no-cpu_gov profile leaves ROCKNIX alone on both).
"""
import curses
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, os.pardir))
PITSTOP = os.environ.get("ETK_PITSTOP", os.path.join(ROOT, "bin", "etk_pitstop.py"))
INSTALL = os.environ.get("ETK_INSTALL", os.path.join(ROOT, "install.sh"))
FIX = tempfile.mkdtemp(prefix="etk_power_gov_")
CFG = os.path.join(FIX, "system.cfg")
FUNCS = os.path.join(FIX, "001-functions")
with open(FUNCS, "w") as f:
    f.write(f'''J_CONF="{CFG}"
set_setting() {{
  if [[ "${{1}}" =~ ^[[:alnum:]] ]]; then
    touch "$J_CONF"; grep -v "^${{1}}=" "$J_CONF" > "$J_CONF.t"; mv "$J_CONF.t" "$J_CONF"
    echo "${{1}}=${{2}}" >> "$J_CONF"
  fi
}}
''')
os.environ.update(ETK_ROOT=FIX, TELEMETRY_DIR=os.path.join(FIX, "tel"), SHM_DIR=os.path.join(FIX, "shm"),
                  ETK_POWER_DIR=os.path.join(FIX, "etk-power"), ETK_ROCKNIX_FUNCS=FUNCS,
                  POWER_PROFILES_JSON=os.path.join(ROOT, "config", "power_profiles.json"),
                  TARGET_ID="NPEA00050", ETK_NO_TARGET="0")
spec = importlib.util.spec_from_file_location("pit", PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)
curses.color_pair = lambda n: 0
FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def cfg():
    try:
        return dict(ln.strip().split("=", 1) for ln in open(CFG) if "=" in ln)
    except FileNotFoundError:
        return {}


def apply_preset(preset):
    st = {}
    pit._POWER_SCHEMA_CACHE = None
    pit._power_load(st)
    m = st["power_model"]
    m["values"].update(pit._power_preset_values(m["schema"], preset))
    return pit._power_apply(m)


print("[PITSTOP]")
ok, msg = apply_preset("RACE")
check("RACE -> ps3.cpugovernor=performance", cfg().get("ps3.cpugovernor"), "performance")
check("footer says the games get it", "+ games: performance" in msg, True)
ok, msg = apply_preset("BALANCED")
check("BALANCED -> ps3.cpugovernor=ondemand (one line, replaced)", (cfg().get("ps3.cpugovernor"),
      (open(CFG).read() if os.path.exists(CFG) else "").count("ps3.cpugovernor=")), ("ondemand", 1))
real = getattr(pit, "ROCKNIX_FUNCS", None)
pit.ROCKNIX_FUNCS = os.path.join(FIX, "absent")
ok, msg = apply_preset("RACE")
check("no ROCKNIX functions: apply still ok, setting untouched, no footer claim",
      (ok, cfg().get("ps3.cpugovernor"), "games:" in msg), (True, "ondemand", False))
pit.ROCKNIX_FUNCS = real

print("[BOOT]")
src = open(INSTALL).read()
m = re.search(r"cat << 'APPLY' > /storage/.config/etk-power-apply.sh\n(.*?)\nAPPLY\n", src, re.S)
body = m.group(1) if m else ""
check("install.sh carries the applier body", bool(body), True)


def boot(profile_lines):
    with open(CFG, "w") as f:
        f.write("system.cpugovernor=ondemand\n")
    prof = os.path.join(FIX, "boot-profile")
    with open(prof, "w") as f:
        f.write("\n".join(profile_lines) + "\n")
    script = os.path.join(FIX, "applier.sh")
    with open(script, "w") as f:
        f.write(body + "\n")
    p = subprocess.run(["/bin/sh", script], env=dict(os.environ, ETK_POWER_PROFILE=prof),
                       capture_output=True, text=True, timeout=20)
    return p.returncode, p.stdout


rc, out = boot(["# pwr=race", "# knob cpu_gov performance"])
check("a saved RACE profile hands performance to ROCKNIX at boot", cfg().get("ps3.cpugovernor"), "performance")
check("the applier says so", "ps3.cpugovernor=performance" in out, True)
rc, out = boot(["# pwr=custom", "# knob gpu_gov performance"])
check("no cpu_gov knob: ROCKNIX untouched", cfg().get("ps3.cpugovernor"), None)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL POWER GOVERNOR CHECKS PASSED")
