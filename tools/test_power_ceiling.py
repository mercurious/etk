#!/usr/bin/env python3
"""ETK -- host tests for the POWER tab's GPU clock ceiling (2026-10-11, GT5 op).

  python3 tools/test_power_ceiling.py

Why the knob exists: config/power_profiles.json promised "GPU caps at 800 MHz (OPP cap)",
but ROCKNIX 20261001's device tree carries overclock OPPs to 925 MHz, and RACE's
`performance` governor rides to the table's top. Every GT5 death the flight recorder
caught ran at 925 MHz; Qualcomm's spec for the Adreno 650 is 587 MHz.

  [LOAD]   a profile saved before the knob existed loads UNCAPPED (the first option, the
           OPP top) and its preset and pwr= tag are unchanged (race stays race).
  [TAG]    a cap rides the tag like GRID does: race+c587, race+gA+c670.
  [APPLY]  the profile carries the max_freq line BEFORE min_freq (the boot applier writes
           in order), the '# knob gpu_ceiling' record round-trips, and RACE still matches.

DISCRIMINATION: against HEAD's schema + Pitstop every [TAG] and [APPLY] check fails
(no gpu_ceiling knob, no +c suffix).
"""
import curses
import importlib.util
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, os.pardir))
PITSTOP = os.environ.get("ETK_PITSTOP", os.path.join(ROOT, "bin", "etk_pitstop.py"))
FIX = tempfile.mkdtemp(prefix="etk_power_ceiling_")
os.environ.update(
    ETK_ROOT=FIX, TELEMETRY_DIR=os.path.join(FIX, "tel"), SHM_DIR=os.path.join(FIX, "shm"),
    ETK_POWER_DIR=os.path.join(FIX, "etk-power"),
    POWER_PROFILES_JSON=os.environ.get("POWER_PROFILES_JSON",
                                       os.path.join(ROOT, "config", "power_profiles.json")),
    TARGET_ID="NPEA00050", ETK_NO_TARGET="0",
)
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


def write_profile(lines):
    os.makedirs(pit.POWER_DIR, exist_ok=True)
    with open(pit.POWER_PROFILE_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


def load():
    state = {}
    pit._POWER_SCHEMA_CACHE = None
    pit._power_load(state)
    return state["power_model"]


# car8's profile as Pitstop wrote it before the knob (read live 2026-10-11)
RACE_OLD = ["# ETK POWER profile — written by Pitstop POWER tab.", "# pwr=race",
            "# knob gpu_gov performance", "# knob gpu_floor 800000000", "# knob cpu_gov performance",
            "# knob cpu_prime_floor 2841600", "# knob cpu_gold_floor 2419200", "# knob grid off",
            "/sys/class/devfreq/3d00000.gpu/governor\tperformance",
            "/sys/class/devfreq/3d00000.gpu/min_freq\t800000000"]

print("[LOAD]")
write_profile(RACE_OLD)
m = load()
check("an old RACE profile loads uncapped (the OPP top)", m["values"].get("gpu_ceiling"), "925000000")
check("its preset is still RACE", pit._power_cur_preset(m), "RACE")
check("its tag is still race", pit._power_tag(m), "race")

print("[TAG]")
m["values"]["gpu_ceiling"] = "587000000"
check("a 587 MHz cap tags race+c587", pit._power_tag(m), "race+c587")
check("the cap keeps the RACE preset identity", pit._power_cur_preset(m), "RACE")
m["values"]["grid"] = "A"
m["values"]["gpu_ceiling"] = "670000000"
check("with GRID: race+gA+c670", pit._power_tag(m), "race+gA+c670")
m["values"]["grid"] = "off"

print("[APPLY]")
m["values"]["gpu_ceiling"] = "587000000"
pit._power_apply(m)          # the live sysfs writes fail on a host: the profile is what counts
body = open(pit.POWER_PROFILE_FILE).read()
lines = [ln for ln in body.splitlines() if "\t" in ln]
paths = [ln.split("\t")[0] for ln in lines]
check("the profile carries the ceiling", "/sys/class/devfreq/3d00000.gpu/max_freq\t587000000" in lines, True)
mx = paths.index("/sys/class/devfreq/3d00000.gpu/max_freq") if "/sys/class/devfreq/3d00000.gpu/max_freq" in paths else 99
mn = paths.index("/sys/class/devfreq/3d00000.gpu/min_freq") if "/sys/class/devfreq/3d00000.gpu/min_freq" in paths else -1
check("max_freq is written before min_freq", mx < mn, True)
check("the header tag is race+c587", "# pwr=race+c587" in body, True)
m2 = load()
check("the knob record round-trips", m2["values"].get("gpu_ceiling"), "587000000")
check("reloaded tag", pit._power_tag(m2), "race+c587")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL POWER CEILING CHECKS PASSED")
