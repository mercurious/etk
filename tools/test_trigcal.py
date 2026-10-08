#!/usr/bin/env python3
"""ETK — host-side regression tests for PITSTOP TRIGGER CALIBRATION (the drag guard).
Run from the repo root:   python3 tools/test_trigcal.py

WHY (car12, 2026-10-08): the DualSense's released triggers rest NONZERO and WANDER
(L2 seen 11..20/255), evdev reports changes only, and the screen's live value
started at 0 — so an operator who pulled, released and saved got a threshold of 0
three times running, and the brake dragged every launch. Copying another unit's
numbers was the wrong fix (calibration is per-stick); the right one is a screen
that cannot save a dragging threshold:

  [SEED]    the rest floor is read from the KERNEL at screen open (EVIOCGABS value),
            so AUTO before any event already clears the real floor.
  [REST]    the model keeps the HIGHEST resting value seen, not the last event.
  [HELD]    AUTO refuses while a trigger is above the rest band (a pull is not a floor).
  [ROW-A]   A on a threshold row = AUTO for that trigger alone.
  [GUARD]   SAVE refuses any threshold <= its trigger's rest floor, file untouched.
  [SAVE]    a good AUTO saves, verified by re-read, in the handler's config units.
  [WIRED]   the shipped source passes the pad fd into the model and tags DRAG on screen.

DISCRIMINATION: against 6794a24 (before the fix) [SEED]/[REST]/[HELD]/[GUARD]/[WIRED] fail.
  ETK_PITSTOP=<(git show 6794a24:bin/etk_pitstop.py) python3 tools/test_trigcal.py
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.environ.get("ETK_REPO_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PITSTOP = os.environ.get("ETK_PITSTOP", os.path.join(ROOT, "bin", "etk_pitstop.py"))

TD = tempfile.mkdtemp(prefix="etk_trigcal_")
PAD = os.path.join(TD, "Default.yml")
FIXTURE = """Player 1 Input:
  Handler: SDL
  Device: DualSense Wireless Controller 1
  Config:
    Left Stick Deadzone: 8000
    Left Trigger Threshold: 0
    Right Trigger Threshold: 0
    Left Pad Squircling Factor: 4000
Player 2 Input:
  Handler: "Null"
  Config:
    Left Trigger Threshold: 0
    Right Trigger Threshold: 0
"""
with open(PAD, "w") as f:
    f.write(FIXTURE)
os.environ["RPCS3_PAD_CONFIG"] = PAD
os.environ.setdefault("ETK_NO_TARGET", "1")

spec = importlib.util.spec_from_file_location("etk_pitstop", PITSTOP)
ps = importlib.util.module_from_spec(spec)
sys.argv = ["etk_pitstop.py"]
try:
    spec.loader.exec_module(ps)
except SystemExit:
    pass

PASS = FAIL = 0
def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1; print(f"  PASS: {name}")
    else:
        FAIL += 1; print(f"  FAIL: {name}  got={got!r} want={want!r}")

def model_with_rest(l2=20, r2=11):
    """A model seeded as if the kernel reported these resting values."""
    if not hasattr(ps, "_read_abs_value"):
        return ps._trigcal_new_model()
    saved = ps._read_abs_value
    ps._read_abs_value = lambda fd, axis: l2 if axis == ps.ABS_Z else r2
    try:
        return ps._trigcal_new_model(fd=99)
    finally:
        ps._read_abs_value = saved

src = open(PITSTOP).read()
with open(PAD, "w") as f:
    f.write(FIXTURE)

print("[SEED] rest floor comes from the kernel at screen open")
m = model_with_rest(20, 11)
check("live L2 seeded 20", m.get("l2"), 20)
check("floor L2 seeded 20", ps._trigcal_floor(m, "l2") if hasattr(ps, "_trigcal_floor") else None, 20)
check("floor R2 seeded 11", ps._trigcal_floor(m, "r2") if hasattr(ps, "_trigcal_floor") else None, 11)
state = {"trigcal_model": m, "tools_cursor": ps._TRIGCAL_ROWS.index("auto"), "tools_mode": "trigcal"}
ok = ps._trigcal_auto(m) if hasattr(ps, "_trigcal_auto") else None
check("AUTO with no events clears the floor: L2 thr = 20+margin", m.get("l2_thr"), 20 + ps._TRIGCAL_MARGIN)
check("AUTO: R2 thr = 11+margin", m.get("r2_thr"), 11 + ps._TRIGCAL_MARGIN)

print("[REST] the floor is the SETTLED rest, max across releases; ramp transients never count")
m = model_with_rest(13, 11)
for v in (13, 200, 255, 120, 30, 20, 14, 11, 12):      # a pull, then a release ramp settling at 12
    ps._trigcal_axis({"trigcal_model": m}, ps.ABS_Z, v)
check("floor after one pull = max(seeded 13, settled 12), not the ramp's 30", ps._trigcal_floor(m, "l2") if hasattr(ps, "_trigcal_floor") else None, 13)
check("L2 envelope max 255 seen", m.get("l2_max"), 255)
for v in (15, 20):                                      # the rest CREEPS up while released
    ps._trigcal_axis({"trigcal_model": m}, ps.ABS_Z, v)
check("creeping rest raises the floor to 20", ps._trigcal_floor(m, "l2") if hasattr(ps, "_trigcal_floor") else None, 20)
for v in (200, 255, 40, 11):                            # second pull, settles lower
    ps._trigcal_axis({"trigcal_model": m}, ps.ABS_Z, v)
check("a lower settle after a higher one keeps the HIGHER floor (20)", ps._trigcal_floor(m, "l2") if hasattr(ps, "_trigcal_floor") else None, 20)
ps._trigcal_auto(m) if hasattr(ps, "_trigcal_auto") else None
check("AUTO uses the settled floor: thr = 20+margin", m.get("l2_thr"), 20 + ps._TRIGCAL_MARGIN)
check("AUTO: full pull to 255 = no top-end cal (0)", m.get("l2_top"), 0)
m2 = model_with_rest(13, 11)
for v in (13, 251, 180, 15):
    ps._trigcal_axis({"trigcal_model": m2}, ps.ABS_RZ, v)
ps._trigcal_auto(m2) if hasattr(ps, "_trigcal_auto") else None
check("AUTO: R2 saturating at 251 -> top-end 251-margin", m2.get("r2_top"), 251 - ps._TRIGCAL_TOP_MARGIN)

print("[HELD] AUTO refuses while a trigger is held")
m = model_with_rest(20, 11)
ps._trigcal_axis({"trigcal_model": m}, ps.ABS_Z, 200)   # L2 held
before = (m.get("l2_thr"), m.get("r2_thr"))
rc = ps._trigcal_auto(m) if hasattr(ps, "_trigcal_auto") else "missing"
check("AUTO returns False while L2 is held", rc, False)
check("thresholds unchanged by a refused AUTO", (m.get("l2_thr"), m.get("r2_thr")), before)
check("note names the held trigger", "RELEASE L2" in (m.get("note") or ""), True)
ps._trigcal_axis({"trigcal_model": m}, ps.ABS_Z, 20)    # released
check("AUTO succeeds once released", ps._trigcal_auto(m) if hasattr(ps, "_trigcal_auto") else None, True)

print("[ROW-A] A on a threshold row = per-trigger AUTO")
m = model_with_rest(20, 11); m["r2_thr"] = 77
if hasattr(ps, "_trigcal_auto"):
    ps._trigcal_auto(m, ("l2",))
check("row-A on L2 sets only L2", (m.get("l2_thr"), m.get("r2_thr")), (20 + ps._TRIGCAL_MARGIN, 77))

print("[GUARD] SAVE refuses a dragging threshold")
m = model_with_rest(20, 11)                 # loaded thresholds are 0 (the fixture) = the car12 state
state = {"trigcal_model": m}
ok, lines = ps._trigcal_save(state)
check("save of threshold 0 under rest 20 is REFUSED", ok, False)
check("refusal names the drag", any("DRAG" in ln for ln in lines), True)
check("file untouched by the refusal", open(PAD).read(), FIXTURE)
m["l2_thr"] = 20                            # equal to the floor is still a drag
ok, _ = ps._trigcal_save(state)
check("threshold == rest is refused too", ok, False)

print("[SAVE] a good AUTO saves in config units and verifies")
m = model_with_rest(20, 11); state = {"trigcal_model": m}
ps._trigcal_auto(m) if hasattr(ps, "_trigcal_auto") else None
ok, lines = ps._trigcal_save(state)
check("save ok", ok, True)
want_l = int(round((20 + ps._TRIGCAL_MARGIN) * 32767 / 255.0))
l, r, lmax, rmax, handler, err = ps._trigcal_read_thresholds()
check("L2 threshold in SDL units (0-32767)", l, want_l)
check("R2 threshold in SDL units", r, int(round((11 + ps._TRIGCAL_MARGIN) * 32767 / 255.0)))
check("Max keys inserted (0 = off, no pull seen)", (lmax, rmax), (0, 0))
check("Player 2 block untouched", "Handler: \"Null\"" in open(PAD).read() and open(PAD).read().count("Left Trigger Threshold: 0") == 1, True)
m = ps._trigcal_new_model() if not hasattr(ps, "_read_abs_value") else model_with_rest(20, 11)
check("re-opened screen loads the saved L2 threshold back in raw units", m.get("l2_thr"), 20 + ps._TRIGCAL_MARGIN)

print("[WIRED] the shipped source carries the fd and the DRAG tag")
check("pad fd stored in state", '"pad_fd": fd' in src, True)
check("model seeded from the pad fd", '_trigcal_new_model(state.get("pad_fd"))' in src, True)
check("screen tags DRAG", '"DRAG"' in src, True)
check("AUTO row routes through _trigcal_auto", "_trigcal_auto(m)" in src, True)

print(f"\ntest_trigcal: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
