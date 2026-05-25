#!/usr/bin/env python3
# ==========================================================
# ETK GAMEPAD PROBE
# ==========================================================
# Empirical evdev capture for pad migrations. Lists input device
# names, then prints (type, code, val) for each press on the chosen
# node. Reuses the ETK's 'llHHi' evdev wire format.
#
# Usage:
#   python3 /storage/games-internal/roms/etk/bin/gamepad_probe.py [/dev/input/eventN]
# Defaults to /dev/input/event9 (the historical InputPlumber node).
# Ctrl-C to quit.
#
# Reference (Linux input-event-codes.h — confirm these on-rig):
#   304 BTN_SOUTH (Cross/A)   305 BTN_EAST (Circle/B)
#   310 BTN_TL (L1)           311 BTN_TR (R1)
#   314 BTN_SELECT            317 BTN_THUMBL (L3)  318 BTN_THUMBR (R3)
#   ABS_HAT0X=16 ABS_HAT0Y=17 (val +/-1)
# ==========================================================
import os
import struct
import sys

FMT = 'llHHi'
SZ = struct.calcsize(FMT)
TYPES = {1: 'EV_KEY', 3: 'EV_ABS', 0: 'EV_SYN'}


def list_names():
    base = '/sys/class/input/'
    for entry in sorted(os.listdir(base)):
        if entry.startswith('event'):
            name_path = os.path.join(base, entry, 'device/name')
            try:
                with open(name_path) as f:
                    print(f"  {entry}: {f.read().strip()}")
            except Exception:
                pass


print("=== input devices ===")
list_names()
dev = sys.argv[1] if len(sys.argv) > 1 else '/dev/input/event9'
print(f"\nReading {dev} - press each button. Ctrl-C to quit.\n")
try:
    with open(dev, 'rb') as f:
        while True:
            data = f.read(SZ)
            if len(data) != SZ:
                continue
            _, _, etype, code, val = struct.unpack(FMT, data)
            if etype in (1, 3):
                print(f"{TYPES.get(etype, etype):7} code={code:<4} val={val}")
except KeyboardInterrupt:
    print("\nbye")
except (FileNotFoundError, PermissionError) as e:
    print(f"open failed: {e}  (try a different eventN from the list above)")
