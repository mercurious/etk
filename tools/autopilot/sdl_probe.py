#!/usr/bin/env python3
"""See the autopilot pad the way RPCS3 does: through RPCS3's own libSDL3.

Read-only diagnostic, streamed by `gtpilot.py sdlprobe`. Loads the libSDL3
that the running RPCS3 has mapped (its AppImage mount), opens every gamepad
SDL enumerates, prints the mapping SDL generated for each, then prints every
button/axis change for SECS seconds. If the agent's events show up here with
the right SDL names, RPCS3's SDL handler receives them too.
"""
import ctypes
import os
import sys
import time

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
BTN = ["south", "east", "west", "north", "back", "guide", "start", "lstick", "rstick",
       "lshoulder", "rshoulder", "dpup", "dpdown", "dpleft", "dpright", "misc1",
       "rpaddle1", "lpaddle1", "rpaddle2", "lpaddle2", "touchpad", "misc2"]
AX = ["leftx", "lefty", "rightx", "righty", "ltrig", "rtrig"]


def find_sdl():
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/maps") as f:
                for ln in f:
                    if "libSDL3.so" in ln:
                        return ln.split()[-1]
        except OSError:
            pass
    return None


path = find_sdl()
if not path:
    sys.exit("no process has libSDL3 mapped (is RPCS3 running?)")
os.environ["LD_LIBRARY_PATH"] = os.path.dirname(path)
sdl = ctypes.CDLL(path)
sdl.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
sdl.SDL_Init.restype = ctypes.c_bool
if not sdl.SDL_Init(0x00002000):  # SDL_INIT_GAMEPAD
    sdl.SDL_GetError.restype = ctypes.c_char_p
    sys.exit("SDL_Init failed: " + sdl.SDL_GetError().decode())
sdl.SDL_GetGamepads.restype = ctypes.POINTER(ctypes.c_uint32)
sdl.SDL_GetGamepadNameForID.restype = ctypes.c_char_p
sdl.SDL_OpenGamepad.restype = ctypes.c_void_p
sdl.SDL_GetGamepadMapping.restype = ctypes.c_char_p
sdl.SDL_GetGamepadMapping.argtypes = [ctypes.c_void_p]
sdl.SDL_GetGamepadButton.argtypes = [ctypes.c_void_p, ctypes.c_int]
sdl.SDL_GetGamepadButton.restype = ctypes.c_bool
sdl.SDL_GetGamepadAxis.argtypes = [ctypes.c_void_p, ctypes.c_int]
sdl.SDL_GetGamepadAxis.restype = ctypes.c_int16

n = ctypes.c_int()
ids = sdl.SDL_GetGamepads(ctypes.byref(n))
pads = []
for i in range(n.value):
    name = sdl.SDL_GetGamepadNameForID(ids[i]).decode()
    gp = sdl.SDL_OpenGamepad(ids[i])
    m = sdl.SDL_GetGamepadMapping(gp)
    print(f"pad {ids[i]}: {name!r}\n  mapping: {m.decode() if m else None}", flush=True)
    pads.append((name, gp))

last = {}
t0 = time.time()
while time.time() - t0 < SECS:
    sdl.SDL_UpdateGamepads()
    for name, gp in pads:
        for b, bn in enumerate(BTN):
            v = int(sdl.SDL_GetGamepadButton(gp, b))
            if last.get((name, bn), 0) != v:
                print(f"  {time.time() - t0:6.3f} {name[:20]:20} {bn}={v}", flush=True)
                last[(name, bn)] = v
        for a, an in enumerate(AX):
            v = sdl.SDL_GetGamepadAxis(gp, a)
            if abs(last.get((name, an), 0) - v) > 2000:
                print(f"  {time.time() - t0:6.3f} {name[:20]:20} {an}={v}", flush=True)
                last[(name, an)] = v
    time.sleep(0.005)
print("probe done", flush=True)
