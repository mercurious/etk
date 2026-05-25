#!/usr/bin/env python3
# ==========================================================
# ETK PHASE 10: PYTHON SHIFTER (v10.0.0 - HEADLESS PANIC)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. R3 (BTN_THUMBR, code 318) = RECOVERY PANIC BUTTON.
#    Literal single press. Invokes bin/recovery.sh DIRECTLY
#    and detached so the rig is fully untethered. Do NOT add
#    a long-press/double-tap guard and do NOT route this
#    through CMD_QUEUE (queue needs a tethered consumer).
# 2. L3 (BTN_THUMBL, code 317) = SHIFT (PIT/RACE mode toggle).
# 3. SELF-HEAL: The InputPlumber virtual controller may not
#    exist when the Sentry spawns this at boot. The connect
#    loop MUST keep re-finding the device; do not collapse it
#    back into a one-shot open.
# ==========================================================
import struct, os, time

# Absolute fallbacks point to the global tmpfs
MODE_FILE = os.environ.get('MODE_FILE', '/dev/shm/etk_shm/etk_mode.txt')
CMD_QUEUE = os.environ.get('CMD_QUEUE', '/dev/shm/etk_shm/etk_cmd_queue')
RECOVERY  = os.environ.get('ETK_RECOVERY',
                           '/storage/games-internal/roms/etk/bin/recovery.sh')

# Pad-model-agnostic substrings. Rocknix has flipped the InputPlumber
# virtual target between models across nightlies (Xbox -> DS5 in 20260520);
# matching any known virtual-pad signature means a future target swap won't
# strand the R3 panic button on the event9 fallback. Keep in sync with the
# PAD_HINTS / PAD_EXCLUDE lists in bin/etk_pitstop.py.
PAD_HINTS = ("xbox", "dualsense", "dual sense", "playstation",
             "sony", "ds5", "wireless controller", "inputplumber")
# DS5 presents as a cluster of sibling nodes (Touchpad, Motion Sensors,
# Headset Jack, Battery) sharing the "... Wireless Controller" parent name.
# PAD_EXCLUDE filters those out so we land on the buttons device.
# "keyboard" excludes the separate "InputPlumber Keyboard" node.
PAD_EXCLUDE = ("touchpad", "motion sensor", "headset", "battery", "keyboard")


def _event_num(entry):
    """Sort key: numeric suffix of 'eventN' (lexical sort puts event10
    before event2 / event8, which landed us on the DS5 Touchpad node)."""
    try:
        return int(entry[len('event'):])
    except (ValueError, TypeError):
        return 1 << 30


def find_gamepad():
    """Locate the InputPlumber virtual controller, pad-model-agnostic.
    Logs the chosen device + name so a silent wrong-node fallback is one
    grep away from diagnosis."""
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir), key=_event_num):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        name = f.read().strip()
                    nl = name.lower()
                    if any(h in nl for h in PAD_HINTS) and \
                       not any(x in nl for x in PAD_EXCLUDE):
                        print(f"[*] ETK SHIFTER: matched /dev/input/{entry} name='{name}'", flush=True)
                        return f"/dev/input/{entry}"
    except Exception:
        pass

    print("[!] ETK SHIFTER: no PAD_HINTS match, falling back to /dev/input/event9", flush=True)
    return '/dev/input/event9'

# Event format: Long, Long, Short, Short, Int
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

def send_cmd(cmd):
    try:
        with open(CMD_QUEUE, 'a') as f:
            f.write(f"{cmd}\n")
    except: pass

def fire_recovery():
    """R3 PANIC: invoke the shared headless recovery, detached."""
    os.system(f"bash {RECOVERY} >/dev/null 2>&1 &")

def toggle_mode():
    """Reads current mode and flips it (L3 SHIFT logic)."""
    try:
        with open(MODE_FILE, 'r') as f:
            current = f.read().strip()
    except:
        current = "PIT"
        
    new_mode = "RACE" if current == "PIT" else "PIT"
    
    try:
        with open(MODE_FILE, 'w') as f:
            f.write(new_mode) 
    except: pass

def event_loop(device):
    clutch = False
    with open(device, 'rb') as f:
        while True:
            data = f.read(EVENT_SIZE)
            if not data: break
            _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)

            # --- BUTTON MAPPINGS (EV_KEY) ---
            if etype == 1:
                # 318 = BTN_THUMBR (R3): RECOVERY PANIC BUTTON
                if code == 318 and val == 1:
                    fire_recovery()

                # 317 = BTN_THUMBL (L3): SHIFT (PIT/RACE toggle)
                if code == 317 and val == 1:
                    toggle_mode()

                # 314 = BTN_SELECT (clutch modifier for DPAD)
                if code == 314: clutch = (val == 1)

            # --- DPAD MAPPINGS (EV_ABS) ---
            elif etype == 3 and clutch:
                # InputPlumber maps DPAD to ABS_HAT0X (16) and ABS_HAT0Y (17)
                if code == 16: # Horizontal Axis
                    if val == 1: send_cmd("VAULT") # Right
                    elif val == -1: os.system("pkill -USR1 mangohud") # Left

if __name__ == "__main__":
    # SELF-HEAL: the virtual controller may not exist yet when the
    # Sentry spawns us at boot, and InputPlumber re-creates the node
    # on resume. Keep re-finding the device instead of dying once.
    while True:
        device = find_gamepad()
        try:
            print(f"[*] ETK SHIFTER ONLINE. BINDING TO: {device}", flush=True)
            event_loop(device)
            print("[!] ETK SHIFTER: device stream closed. Re-binding...", flush=True)
        except Exception as e:
            print(f"[!] ETK SHIFTER: {e}. Re-binding...", flush=True)
        time.sleep(2)