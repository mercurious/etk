#!/usr/bin/env python3
"""ETK — host-side regression tests for the self-update UNIT RECONCILER.

Run from the repo root:   python3 tools/test_unit_reconcile.py

WHY THIS EXISTS
  install.sh writes ETK's /storage/.config/system.d units via heredocs. A
  couch-only update (Pitstop -> TOOLS -> Check for ETK Updates) copies the
  bin/scripts/config trees but NEVER re-runs install.sh — so a shipped UNIT
  fix (the motivating case: the osguard `After=` ordering fix, without which
  ConditionPathExists is evaluated before games-internal is mounted and the
  guard silently SKIPS — it missed the 2026-08-28 nightly frankenboot) would
  never reach couch-only users. `_reconcile_units` patches existing units to
  the update's own manifest (config/unit_reconcile.json).

WHAT IS SILENTLY BREAKABLE (a suite each)
  [PATCHER]   `_reconcile_unit_file` — the pure in-place directive patcher:
              replaces a WRONG value, is a no-op on a CORRECT one (the
              discrimination — a test that only runs on already-correct units
              proves nothing), collapses duplicate directives, inserts a
              missing key under an existing section, never invents a section,
              leaves an absent file alone, is idempotent, and preserves every
              other directive/comment/section byte-for-byte.
  [MANIFEST]  config/unit_reconcile.json parses and carries the osguard rule.
  [ANTI-DRIFT] the manifest's osguard After= is byte-identical to install.sh's
              osguard heredoc After= — the whole point is couch and host rigs
              CONVERGE; a drift here silently re-splits them.
  [END-TO-END] `_reconcile_units` against a temp unit dir (ETK_UNIT_DIR seam):
              a wrong-After osguard unit is fixed and summarized; a unit that
              is absent is skipped (install.sh owns first creation); an absent
              manifest is a clean no-op.

No rig, no root, no network — synthetic fixtures + the repo's own manifest.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# ETK_REPO_ROOT points the suite at a mutated COPY of the kit — a gate that has
# only ever run against working code proves nothing.
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.path.join(ROOT, "bin", "etk_pitstop.py")
INSTALL_SH = os.path.join(ROOT, "install.sh")
MANIFEST = os.path.join(ROOT, "config", "unit_reconcile.json")

# Redirect the module's log + unit dir to throwaway temp space BEFORE import
# (LOG_PATH is computed from ETK_ROOT at import time).
_TMP = tempfile.mkdtemp(prefix="etk_recon_test_")
os.environ["ETK_ROOT"] = os.path.join(_TMP, "etkroot")
os.environ["ETK_UNIT_DIR"] = os.path.join(_TMP, "system.d")
os.makedirs(os.environ["ETK_UNIT_DIR"], exist_ok=True)

spec = importlib.util.spec_from_file_location("pit", PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


def wunit(text):
    """Write a fixture unit into a fresh temp file, return its path."""
    fd, p = tempfile.mkstemp(suffix=".service", dir=_TMP)
    os.close(fd)
    with open(p, "w") as f:
        f.write(text)
    return p


def rd(p):
    with open(p) as f:
        return f.read()


# The correct osguard After= the whole thing must converge on.
GOOD_AFTER = "local-fs.target rocknix-automount.service etk-sd-rebind.service"
SET_OSGUARD = {"Unit": {"After": GOOD_AFTER}}

# A realistic pre-0.9.0 osguard unit with the WRONG (short) After= — the
# broken version the reconciler must FIX.
BROKEN_UNIT = f"""[Unit]
Description=ETK OS-update coherence guard (kernel/module-tree self-heal)
# a comment that must survive verbatim
After=local-fs.target
ConditionPathExists=/storage/games-internal/roms/etk/bin/osguard.sh

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /storage/games-internal/roms/etk/bin/osguard.sh

[Install]
WantedBy=multi-user.target
"""

GOOD_UNIT = BROKEN_UNIT.replace("After=local-fs.target\n",
                                f"After={GOOD_AFTER}\n")

print("[PATCHER] _reconcile_unit_file")
# 1. the discrimination: a WRONG After= is rewritten to the desired value
p = wunit(BROKEN_UNIT)
changed, note = pit._reconcile_unit_file(p, SET_OSGUARD)
check("wrong After -> changed", (changed, note), (True, "patched"))
check("wrong After -> now correct", rd(p), GOOD_UNIT)
ok("comment survived the patch", "# a comment that must survive verbatim" in rd(p))
ok("other sections survived", "[Service]" in rd(p) and "[Install]" in rd(p))
ok("ConditionPathExists survived",
   "ConditionPathExists=/storage/games-internal/roms/etk/bin/osguard.sh" in rd(p))

# 2. a CORRECT unit is a no-op (idempotent; byte-identical)
p = wunit(GOOD_UNIT)
before = rd(p)
changed, note = pit._reconcile_unit_file(p, SET_OSGUARD)
check("correct After -> unchanged", (changed, note), (False, "current"))
check("correct After -> byte-identical", rd(p), before)

# 3. idempotence: patching the just-fixed file again is a no-op
p = wunit(BROKEN_UNIT)
pit._reconcile_unit_file(p, SET_OSGUARD)
changed2, note2 = pit._reconcile_unit_file(p, SET_OSGUARD)
check("second pass is a no-op", (changed2, note2), (False, "current"))

# 4. duplicate directives collapse to one desired line
dup = BROKEN_UNIT.replace("After=local-fs.target\n",
                          "After=local-fs.target\nAfter=stale.service\n")
p = wunit(dup)
changed, _ = pit._reconcile_unit_file(p, SET_OSGUARD)
ok("duplicate After -> changed", changed)
check("duplicate After -> exactly one After= line",
      rd(p).count("After="), 1)
ok("duplicate After -> the desired value", f"After={GOOD_AFTER}" in rd(p))

# 5. missing key in an existing section is inserted under the header
noafter = BROKEN_UNIT.replace("After=local-fs.target\n", "")
p = wunit(noafter)
changed, note = pit._reconcile_unit_file(p, SET_OSGUARD)
check("missing After -> changed", (changed, note), (True, "patched"))
ok("missing After -> inserted under [Unit]", f"After={GOOD_AFTER}" in rd(p))
# the inserted line must sit inside [Unit], before [Service]
body = rd(p)
ok("inserted After is inside [Unit]",
   body.index("After=") > body.index("[Unit]")
   and body.index("After=") < body.index("[Service]"))

# 6. a section that does not appear is NEVER invented
p = wunit(BROKEN_UNIT)
changed, _ = pit._reconcile_unit_file(p, {"Nonexist": {"Foo": "bar"}})
check("absent section -> no change", changed, False)
ok("absent section -> not created", "[Nonexist]" not in rd(p))

# 7. an absent file is left alone (no crash)
changed, note = pit._reconcile_unit_file(os.path.join(_TMP, "nope.service"),
                                         SET_OSGUARD)
check("absent file -> (False, absent)", (changed, note), (False, "absent"))

print("[MANIFEST] config/unit_reconcile.json")
man = json.load(open(MANIFEST))
ok("manifest parses + version present", man.get("version"))
units = {u["name"]: u for u in man.get("units", [])}
ok("osguard rule present", "etk-osguard.service" in units)
man_after = units["etk-osguard.service"]["set"]["Unit"]["After"]
ok("osguard rule sets After=", bool(man_after))

print("[ANTI-DRIFT] manifest After= == install.sh osguard heredoc After=")


def osguard_after_from_install_sh():
    inblk = False
    for ln in open(INSTALL_SH):
        ln = ln.rstrip("\n")
        if "system.d/etk-osguard.service" in ln and "cat " in ln:
            inblk = True
            continue
        if inblk:
            if ln.strip() == "SVC":
                break
            if ln.startswith("After="):
                return ln[len("After="):].strip()
    return None


inst_after = osguard_after_from_install_sh()
ok("found After= in install.sh osguard heredoc", inst_after is not None)
check("manifest After= matches install.sh (couch == host)", man_after, inst_after)

print("[END-TO-END] _reconcile_units against a temp unit dir")
# Stage a fake extracted tarball 'src' carrying the REAL repo manifest, and a
# unit dir (ETK_UNIT_DIR) holding a broken osguard unit + an unrelated unit.
src = os.path.join(_TMP, "src")
os.makedirs(os.path.join(src, "config"), exist_ok=True)
shutil.copy2(MANIFEST, os.path.join(src, "config", "unit_reconcile.json"))
udir = os.environ["ETK_UNIT_DIR"]
for f in os.listdir(udir):
    os.remove(os.path.join(udir, f))
with open(os.path.join(udir, "etk-osguard.service"), "w") as f:
    f.write(BROKEN_UNIT)
with open(os.path.join(udir, "etk-power.service"), "w") as f:
    f.write("[Unit]\nAfter=multi-user.target\n")
lines = pit._reconcile_units(src, os.path.join(_TMP, "base"))
ok("returns a 'Units: patched' summary line",
   any("etk-osguard.service" in ln for ln in lines))
check("osguard unit now carries the correct After=",
      f"After={GOOD_AFTER}" in rd(os.path.join(udir, "etk-osguard.service")),
      True)
ok("unrelated unit left untouched",
   rd(os.path.join(udir, "etk-power.service")) == "[Unit]\nAfter=multi-user.target\n")
# a second run is a clean no-op (nothing left to patch)
lines2 = pit._reconcile_units(src, os.path.join(_TMP, "base"))
check("second reconcile -> nothing patched", lines2, [])
# a manifest-less src is a clean no-op, not an error
empty_src = os.path.join(_TMP, "empty_src")
os.makedirs(os.path.join(empty_src, "config"), exist_ok=True)
check("absent manifest -> no-op", pit._reconcile_units(empty_src, _TMP), [])
# a unit the manifest names but the rig lacks is skipped (no crash, no create)
for f in os.listdir(udir):
    os.remove(os.path.join(udir, f))
check("no matching units on rig -> no-op", pit._reconcile_units(src, _TMP), [])
ok("skipped-unit case did not create the unit",
   not os.path.exists(os.path.join(udir, "etk-osguard.service")))

shutil.rmtree(_TMP, ignore_errors=True)
total = len(FAILS)
print(f"\n{'FAIL' if total else 'PASS'}: {total} failure(s)")
sys.exit(1 if total else 0)
