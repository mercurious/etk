#!/usr/bin/env python3
# ==========================================================
# ETK PHASE 4: PYTHON SHIFTER (v11.2.1 - INPUTPLUMBER MERGE)
# ==========================================================
# MERGE LOG: Tapped into the InputPlumber Virtual Xbox pipe.
# Bypassed hardware hiding. Fixed Headphone Jack false positive.
# ==========================================================
import struct, os, sys, time

# Absolute fallbacks point to the global tmpfs
MODE_FILE = os.environ.get('MODE_FILE', '/dev/shm/etk_shm/etk_mode.txt')
CMD_QUEUE = os.environ.get('CMD_QUEUE', '/dev/shm/etk_shm/etk_cmd_queue')

def find_gamepad():
    """Hunts strictly for the InputPlumber Virtual Controller."""
    input_dir = '/sys/class/input/'
    try:
        for entry in os.listdir(input_dir):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        name = f.read().strip().lower()
                        # Lock onto the Virtual Xbox Controller
                        if "xbox" in name:
                            return f"/dev/input/{entry}"
    except Exception as e:
        pass
    
    # Fallback to the known Virtual node
    return '/dev/input/event9' 

DEVICE = find_gamepad()

# Event format: Long, Long, Short, Short, Int
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

def send_cmd(cmd):
    try:
        with open(CMD_QUEUE, 'a') as f:
            f.write(f"{cmd}\n")
    except: pass

def toggle_mode():
    """Reads current mode and flips it (R3 Button Logic)."""
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

if __name__ == "__main__":
    print(f"[*] ETK SHIFTER ONLINE. BINDING TO: {DEVICE}", flush=True)

    clutch = False; r1 = False; l2 = False; r2 = False

    try:
        with open(DEVICE, 'rb') as f:
            while True:
                data = f.read(EVENT_SIZE)
                if not data: break
                _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)

                # --- BUTTON MAPPINGS (EV_KEY) ---
                if etype == 1:
                    # 318 is standard Linux EVDEV for BTN_THUMBR (R3)
                    if code == 318 and val == 1: 
                        toggle_mode()
                        
                    # Clutch & Modifiers (SELECT)
                    if code == 314: clutch = (val == 1) 
                    if code == 311: r1 = (val == 1)     
                    if code == 312: l2 = (val == 1)     
                    if code == 313: r2 = (val == 1)     

                    # NUCLEAR RECOVERY (SELECT + L2 + R2)
                    if clutch and l2 and r2: 
                        send_cmd("RECOVERY")

                # --- DPAD MAPPINGS (EV_ABS) ---
                elif etype == 3 and clutch:
                    # InputPlumber maps DPAD to ABS_HAT0X (16) and ABS_HAT0Y (17)
                    if code == 16: # Horizontal Axis
                        if val == 1: send_cmd("VAULT") # Right
                        elif val == -1: os.system("pkill -USR1 mangohud") # Left

    except Exception as e:
        print(f"[!] ETK CRASH: {e}")