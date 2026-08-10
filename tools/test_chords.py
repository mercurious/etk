#!/usr/bin/env python3
"""ETK — host-side regression tests for the input_d CHORD MAP (0.8.5).

Run from the repo root:   python3 tools/test_chords.py

input_d is locked-down core: it carries the R3 panic path, and it is the one
daemon whose bugs land in the operator's hands mid-race rather than in a log.
0.8.5 changed two chords because supporting games outside the GT series
showed the kit stealing controls those titles actually bind:

  [SHOT]   ETK screenshot moved from BARE L1 to L1+L2. L2 is the ANALOG axis
           on this pad and it RESTS NONZERO after first actuation (~12/255 —
           the H7 trigger-cal finding), so the modifier is gated on a
           hysteresis pair. The naive `val > 0` reading is the failure this
           suite exists to catch: after one brake application the modifier
           would latch on and bare L1 would fire screenshots forever, which
           is precisely the interference the change was meant to remove.

  [HOLD]   The R1+L3 HUD punchbox must now be HELD before it fires. A tapped
           L3 with R1 down is ordinary play; a 0.4 s hold is not.

  [GATE]   Policy (screenshot mode, the new bog-sampler switch, the Chiaki
           stand-down) is applied at FIRE time in the dispatcher, so the
           chord matcher stays a pure shape-matcher — this suite drives it
           with synthetic evdev frames, no pad and no rig.

  [PANIC]  And the one that outranks all of the above: L1+R3 still fires
           IMMEDIATELY, with no hold, no queue and no policy gate. Every
           check here that touches recovery is guarding immutable rule 1.

DISCRIMINATION: run against the pre-0.8.5 logic these fail — bare L1 fired a
screenshot (SHOT/bare-L1 fails), the punchbox fired on the press (HOLD fails),
and there was no bog gate at all (GATE fails).

No rig, no pad, no root.
"""
import importlib.util
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
INPUT_D = os.path.join(ROOT, "bin", "input_d.py")

# Point every state file at a scratch dir BEFORE import: input_d resolves its
# paths at module level from the environment, exactly as env.sh feeds it.
TMP = tempfile.mkdtemp(prefix="etk-chords-")
os.environ["SCREENSHOT_MODE_FILE"] = os.path.join(TMP, "screenshot_mode.txt")
os.environ["BOG_CHORD_FILE"] = os.path.join(TMP, "bog_chord.txt")
os.environ["ID_FILE"] = os.path.join(TMP, "active_id.txt")
os.environ["ETK_CHIAKI_LOCK"] = os.path.join(TMP, "chiaki_active")

spec = importlib.util.spec_from_file_location("input_d", INPUT_D)
ind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ind)

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


# evdev frame helpers. EV_KEY=1, EV_ABS=3.
def key(c, sm, v, t=0.0):
    return sm.feed(1, c, v, t)


def axis(c, sm, v, t=0.0):
    return sm.feed(3, c, v, t)


BTN_L1, BTN_R1, BTN_SELECT, BTN_L3, BTN_R3 = 310, 311, 314, 317, 318
HAT_X, HAT_Y = 16, 17


def fresh():
    return ind.Chords()


# ==========================================================================
print("[SHOT] screenshot is L1+L2, and L2 is analog")
# ==========================================================================
sm = fresh()
check("bare L1 press fires nothing", key(BTN_L1, sm, 1), [])
check("bare L1 release fires nothing", key(BTN_L1, sm, 0), [])

sm = fresh()
axis(ind.ABS_Z, sm, 255)          # squeeze L2 first
check("L2 alone fires nothing", axis(ind.ABS_Z, sm, 255), [])
check("then L1 completes the chord", key(BTN_L1, sm, 1), ["screenshot"])

sm = fresh()
key(BTN_L1, sm, 1)                # hold L1 first
check("L1 held, then L2 completes the chord",
      axis(ind.ABS_Z, sm, 200), ["screenshot"])

# THE LATCH BUG. The DS5 target rests around 12/255 once the driver has
# touched the brake. A `val > 0` modifier would stay armed for the rest of
# the session and turn every subsequent L1 into a shutter press.
sm = fresh()
axis(ind.ABS_Z, sm, 12)
check("resting trigger residual does NOT arm the modifier",
      key(BTN_L1, sm, 1), [])
check_true("...and the residual is genuinely below the ON threshold",
           12 < ind.L2_ON)

# Hysteresis: a trigger parked ON the threshold must not chatter the
# modifier and machine-gun the shutter while L1 is held.
sm = fresh()
key(BTN_L1, sm, 1)
check("first crossing fires once", axis(ind.ABS_Z, sm, ind.L2_ON), ["screenshot"])
check("wobble down into the hysteresis band is inert",
      axis(ind.ABS_Z, sm, ind.L2_ON - 5), [])
check("wobble back up does not re-fire",
      axis(ind.ABS_Z, sm, ind.L2_ON + 5), [])
check("a real release clears the modifier", axis(ind.ABS_Z, sm, 0), [])
check("and a deliberate re-pull fires again",
      axis(ind.ABS_Z, sm, 255), ["screenshot"])
check_true("OFF threshold sits below ON", ind.L2_OFF < ind.L2_ON)

# The digital twin, where the target emits it.
sm = fresh()
key(BTN_L1, sm, 1)
check("BTN_TL2 arms the modifier too", key(ind.BTN_TL2, sm, 1), ["screenshot"])

# The SELECT+DPAD-Up fallback is deliberately a SEPARATE, ungated action.
sm = fresh()
key(BTN_SELECT, sm, 1)
check("SELECT+DPAD-Up is the forced-shot fallback",
      axis(HAT_Y, sm, -1), ["screenshot_forced"])

# ==========================================================================
print("[HOLD] the punchbox must be held, not tapped")
# ==========================================================================
sm = fresh()
key(BTN_R1, sm, 1, 0.0)
check("L3 press only arms — nothing fires yet", key(BTN_L3, sm, 1, 0.0), [])
check("still nothing before the hold elapses", sm.tick(0.1), [])
check("fires once the hold is served", sm.tick(ind.PUNCHBOX_HOLD_S), ["punchbox"])
check("and only once", sm.tick(10.0), [])

sm = fresh()
key(BTN_R1, sm, 1, 0.0)
key(BTN_L3, sm, 1, 0.0)
key(BTN_L3, sm, 0, 0.1)           # a tap: released early
check("a tapped L3 is cancelled, not deferred", sm.tick(10.0), [])

sm = fresh()
key(BTN_R1, sm, 1, 0.0)
key(BTN_L3, sm, 1, 0.0)
key(BTN_R1, sm, 0, 0.1)           # let go of the modifier instead
check("releasing R1 mid-hold cancels too", sm.tick(10.0), [])

sm = fresh()
check("bare L3 with no R1 never arms", key(BTN_L3, sm, 1, 0.0), [])
check_true("...and stays unarmed", sm.next_timeout(0.0) is None)

sm = fresh()
key(BTN_R1, sm, 1, 0.0)
key(BTN_L3, sm, 1, 0.0)
check("a pending hold asks the loop for a timeout",
      round(sm.next_timeout(0.0), 3), round(ind.PUNCHBOX_HOLD_S, 3))
check_true("an idle matcher asks for none (loop blocks, as before)",
           fresh().next_timeout(0.0) is None)

# ==========================================================================
print("[PANIC] immutable rule 1: R3 recovery is never delayed or gated")
# ==========================================================================
sm = fresh()
key(BTN_L1, sm, 1, 0.0)
check("L1+R3 fires on the PRESS, no hold", key(BTN_R3, sm, 1, 0.0), ["recovery"])
sm = fresh()
check("bare R3 stays with the game", key(BTN_R3, sm, 1, 0.0), [])
# L2 must not have become an accidental precondition for the panic chord.
sm = fresh()
key(BTN_L1, sm, 1, 0.0)
axis(ind.ABS_Z, sm, 0, 0.0)
check("R3 recovery does not depend on trigger state",
      key(BTN_R3, sm, 1, 0.0), ["recovery"])
src = open(INPUT_D).read()
check_true("recovery is dispatched with no mode/state file gate",
           "if a == 'recovery':" in src and "_screenshot_chord_allowed" not in
           src.split("if a == 'recovery':")[1].split("elif a ==")[0],
           "rule 1")

# ==========================================================================
print("[GATE] policy is applied at fire time, not in the matcher")
# ==========================================================================
sm = fresh()
key(BTN_R1, sm, 1, 0.0)
check("R1+DPAD-Down is the bog sampler", axis(HAT_Y, sm, 1, 0.0), ["bog"])
check("R1+DPAD-Up is the RSX capture", axis(HAT_Y, sm, -1, 0.0), ["rsx"])
check("DPAD release fires nothing", axis(HAT_Y, sm, 0, 0.0), [])


def write(path, text):
    with open(path, "w") as f:
        f.write(text + "\n")


check("bog chord defaults to enabled with no file",
      ind._read_bog_chord_state(), "enabled")
check("...and is allowed", ind._bog_chord_allowed(), True)
write(os.environ["BOG_CHORD_FILE"], "disabled")
check("TOOLS can disable the bog chord", ind._bog_chord_allowed(), False)
write(os.environ["BOG_CHORD_FILE"], "enabled")
check("TOOLS can re-enable it", ind._bog_chord_allowed(), True)
write(os.environ["BOG_CHORD_FILE"], "banana")
check("garbage in the file falls back to enabled, not off",
      ind._bog_chord_allowed(), True)
os.remove(os.environ["BOG_CHORD_FILE"])

# The screenshot gate keeps its three-state contract across the chord move.
check("screenshot defaults to in-game", ind._read_screenshot_mode(), "in-game")
check("in-game with an idle rig does not fire",
      ind._screenshot_chord_allowed(), False)
write(os.environ["ID_FILE"], "IDLE")
check("...and IDLE is a sentinel, not a game",
      ind._screenshot_chord_allowed(), False)
write(os.environ["ID_FILE"], "NPEA00050")
check("in-game with a resolved title fires",
      ind._screenshot_chord_allowed(), True)
write(os.environ["SCREENSHOT_MODE_FILE"], "disabled")
check("disabled hands the chord back to the game",
      ind._screenshot_chord_allowed(), False)
write(os.environ["SCREENSHOT_MODE_FILE"], "always")
check("always fires even at the frontend", ind._screenshot_chord_allowed(), True)

# ==========================================================================
print("[WIRED] the dispatcher actually consults those gates")
# ==========================================================================
# Testing the gate FUNCTION only proves the switch turns; it does not prove
# anything is attached to it. Deleting the gate from _dispatch left the
# function-level checks above entirely green — so drive the dispatcher and
# watch what fires.
FIRED = []


def _spy(name):
    return lambda *a, **k: FIRED.append(name)


for fn in ("fire_recovery", "fire_screenshot", "fire_bog_profile",
           "fire_rsx_capture", "cycle_hud_state", "send_cmd"):
    setattr(ind, fn, _spy(fn))
ind.os.system = lambda *a, **k: FIRED.append("os.system")


def dispatched(acts):
    del FIRED[:]
    ind._dispatch(acts)
    return list(FIRED)


write(os.environ["BOG_CHORD_FILE"], "disabled")
check("disabled bog chord reaches the dispatcher and is suppressed",
      dispatched(["bog"]), [])
write(os.environ["BOG_CHORD_FILE"], "enabled")
check("enabled bog chord fires the sampler",
      dispatched(["bog"]), ["fire_bog_profile"])

write(os.environ["SCREENSHOT_MODE_FILE"], "disabled")
check("disabled screenshot mode is honoured by the dispatcher",
      dispatched(["screenshot"]), [])
check("...but the SELECT fallback still fires",
      dispatched(["screenshot_forced"]), ["fire_screenshot"])
write(os.environ["SCREENSHOT_MODE_FILE"], "always")
check("enabled screenshot mode fires",
      dispatched(["screenshot"]), ["fire_screenshot"])

check("recovery fires", dispatched(["recovery"]), ["fire_recovery"])
check("punchbox fires", dispatched(["punchbox"]), ["cycle_hud_state"])
check("rsx fires", dispatched(["rsx"]), ["fire_rsx_capture"])
check("vault fires", dispatched(["vault"]), ["send_cmd"])
check("mango toggle fires", dispatched(["mango_toggle"]), ["os.system"])

# Chiaki owns R1+L3 and L1+R3 in-stream, so the colliding chords stand down —
# screenshots do not, because a shot of a stream collides with nothing.
open(os.environ["ETK_CHIAKI_LOCK"], "w").close()
check("Chiaki stand-down: recovery", dispatched(["recovery"]), [])
check("Chiaki stand-down: punchbox", dispatched(["punchbox"]), [])
check("Chiaki stand-down: bog", dispatched(["bog"]), [])
check("Chiaki stand-down: rsx", dispatched(["rsx"]), [])
check("screenshots survive a Chiaki stream",
      dispatched(["screenshot"]), ["fire_screenshot"])
os.remove(os.environ["ETK_CHIAKI_LOCK"])

# Pitstop and input_d must agree on every shared constant, or the TOOLS row
# writes a value the daemon does not recognize and silently defaults.
pit_src = open(os.path.join(ROOT, "bin", "etk_pitstop.py")).read()
for token in ('BOG_CHORD_STATES = ("enabled", "disabled")',
              'BOG_CHORD_DEFAULT = "enabled"',
              'bog_chord.txt'):
    check_true(f"Pitstop shares the bog contract: {token}", token in pit_src)
check_true("env.sh publishes BOG_CHORD_FILE",
           "BOG_CHORD_FILE" in open(os.path.join(ROOT, "scripts", "env.sh")).read(),
           "law #2 — env.sh is the only definer")

import shutil
shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL CHORD CHECKS PASSED")
