#!/usr/bin/env python3
# ==========================================================
# ETK PHASE 10: PYTHON SHIFTER (v10.1.0 - L3 HUD POSITION)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. R3 (BTN_THUMBR, code 318) = RECOVERY PANIC BUTTON.
#    Literal single press. Invokes bin/recovery.sh DIRECTLY
#    and detached so the rig is fully untethered. Do NOT add
#    a long-press/double-tap guard and do NOT route this
#    through CMD_QUEUE (queue needs a tethered consumer).
# 2. L3 (BTN_THUMBL, code 317) = HUD POSITION TOGGLE.
#    Flips MangoHud top-left <-> bottom-left in $RIG_MANGO_CONF,
#    persists per-game preference to $HUD_POSITIONS_FILE, and
#    signals MangoHud reload_cfg so the swap takes effect live.
#    The Sentry re-applies the per-game pref at IDLE->RUNNING.
#    PIT/RACE thermal mode is now thermal_d.sh-internal only
#    (auto-PIT at RACE_THRESHOLD; auto-recovers to RACE at
#    RECOVER_THRESHOLD — no reboot, since thermal_d.sh v14).
# 3. SELF-HEAL: The InputPlumber virtual controller may not
#    exist when the Sentry spawns this at boot. The connect
#    loop MUST keep re-finding the device; do not collapse it
#    back into a one-shot open.
# ==========================================================
import struct, os, time, re, glob, socket

# Absolute fallbacks point to the global tmpfs
CMD_QUEUE = os.environ.get('CMD_QUEUE', '/dev/shm/etk_shm/etk_cmd_queue')
ID_FILE   = os.environ.get('ID_FILE',   '/dev/shm/etk_shm/active_id.txt')
RECOVERY  = os.environ.get('ETK_RECOVERY',
                           '/storage/games-internal/roms/etk/bin/recovery.sh')
RIG_MANGO_CONF = os.environ.get('RIG_MANGO_CONF',
                                '/storage/.config/MangoHud/MangoHud.conf')
HUD_POSITIONS_FILE = os.environ.get(
    'HUD_POSITIONS_FILE',
    '/storage/games-internal/roms/etk/etk_telemetry/hud_positions.tsv')
SCREENSHOT_MODE_FILE = os.environ.get(
    'SCREENSHOT_MODE_FILE',
    '/storage/games-internal/roms/etk/etk_telemetry/screenshot_mode.txt')

# L1-screenshot gating. 'always' fires on any L1 press, 'in-game' fires only
# while a PS3 game is resolved, 'disabled' never fires (frees L1 for the game).
# in-game is the default whenever the mode file is absent / unreadable /
# unrecognized -- the conservative choice that keeps L1 out of the frontend.
SCREENSHOT_MODES = ('always', 'in-game', 'disabled')
SCREENSHOT_MODE_DEFAULT = 'in-game'

# HUD toggle pair. bottom-left is the dashboard default — sits down by
# the physical controls on the Flip 2, below the typical "rear view mirror"
# game-element band. top-left is the alternative for games whose HUD
# elements crowd the bottom edge (e.g. GT5P).
HUD_POSITIONS = ('top-left', 'bottom-left')
HUD_DEFAULT = 'bottom-left'

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

def fire_screenshot():
    """Silent grim capture (incl. MangoHUD overlay), detached so the input
    loop never blocks on grim latency."""
    os.system("nohup bash /storage/games-internal/roms/etk/bin/screenshot.sh >/dev/null 2>&1 &")

def _read_screenshot_mode():
    """Return the L1-screenshot mode, one of SCREENSHOT_MODES. Re-read on
    every press (a single small file read, human-paced) so a Pitstop toggle
    takes effect with no daemon restart. Any error / unrecognized value
    falls back to SCREENSHOT_MODE_DEFAULT -- never raises into the loop."""
    try:
        with open(SCREENSHOT_MODE_FILE) as f:
            mode = f.read().strip().lower()
        return mode if mode in SCREENSHOT_MODES else SCREENSHOT_MODE_DEFAULT
    except Exception:
        return SCREENSHOT_MODE_DEFAULT

def _l1_screenshot_allowed():
    """Gate the L1 trigger by the current mode. 'disabled' never fires;
    'in-game' fires only when a real game id is resolved; 'always' fires
    unconditionally. (The SELECT+DPAD-Up chord is NOT gated -- see loop.)"""
    mode = _read_screenshot_mode()
    if mode == 'disabled':
        return False
    if mode == 'in-game':
        return _read_active_game_id() is not None
    return True  # 'always'

def _read_active_game_id():
    """Returns the active game ID, or None if the rig is idle / unresolved.
    Sentry writes 'IDLE' to ID_FILE when no game is running and 'UNKNOWN_ID'
    when the resolver couldn't identify the game; both are sentinels, not
    real IDs to persist HUD prefs against."""
    try:
        with open(ID_FILE) as f:
            gid = f.read().strip()
        return gid if gid and gid not in ('IDLE', 'UNKNOWN_ID') else None
    except Exception:
        return None


def _read_current_position():
    """Parse the current position= line from RIG_MANGO_CONF. Returns the
    string ('top-left' / 'bottom-left' / other) or None if the conf is
    unreadable or has no position= line."""
    try:
        with open(RIG_MANGO_CONF) as f:
            for line in f:
                m = re.match(r'^\s*position\s*=\s*(\S+)', line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return None


def _write_position_to_conf(new_pos):
    """Atomic rewrite of the position= line. Returns True on success.
    Uses the ETK tmp+mv idiom (same as Pitstop's H2 save) so a power loss
    mid-write never leaves the conf half-written."""
    try:
        with open(RIG_MANGO_CONF) as f:
            lines = f.readlines()
    except Exception:
        return False
    found = False
    for i, line in enumerate(lines):
        if re.match(r'^\s*position\s*=', line):
            lines[i] = f'position={new_pos}\n'
            found = True
            break
    if not found:
        return False
    try:
        tmp = RIG_MANGO_CONF + '.tmp'
        with open(tmp, 'w') as f:
            f.writelines(lines)
        os.replace(tmp, RIG_MANGO_CONF)
        return True
    except Exception:
        return False


def _signal_mangohud_reload():
    """Send 'reload_cfg' to MangoHud's control socket. Fail silent — if
    MangoHud isn't running (no game launched) or the socket name didn't
    match our glob, the position change still applies on next launch via
    the Sentry's IDLE->RUNNING ignition block."""
    candidates = glob.glob('/tmp/MangoHud*') + glob.glob('/tmp/mangohud*')
    for path in candidates:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(path)
            s.send(b'reload_cfg\n')
            s.close()
            return True
        except Exception:
            continue
    return False


def _remember_position(game_id, pos):
    """Upsert game_id -> pos in HUD_POSITIONS_FILE. No-op for None game_id
    (rig idle / unresolved) so we never write a sentinel as a game key."""
    if not game_id:
        return
    rows = {}
    try:
        with open(HUD_POSITIONS_FILE) as f:
            for line in f:
                parts = line.rstrip('\n').split('\t')
                if len(parts) >= 2:
                    rows[parts[0]] = parts[1]
    except FileNotFoundError:
        pass
    except Exception:
        return
    rows[game_id] = pos
    try:
        os.makedirs(os.path.dirname(HUD_POSITIONS_FILE), exist_ok=True)
        tmp = HUD_POSITIONS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            for gid, p in sorted(rows.items()):
                f.write(f'{gid}\t{p}\n')
        os.replace(tmp, HUD_POSITIONS_FILE)
    except Exception:
        pass


def toggle_hud_position():
    """L3: flip MangoHud position top-left <-> bottom-left.
    1. Read current position from the live conf (default to HUD_DEFAULT).
    2. Pick the OTHER position from HUD_POSITIONS.
    3. Atomic write back to conf.
    4. Persist per-game pref (if a game is running).
    5. Signal MangoHud reload_cfg so it takes effect live.
    Each step fails silent — a broken HUD signal still gets the conf right
    for next launch."""
    cur = _read_current_position()
    # Any non-tracked value (or missing) flips us to the dashboard default.
    if cur == 'top-left':
        new = 'bottom-left'
    elif cur == 'bottom-left':
        new = 'top-left'
    else:
        new = HUD_DEFAULT
    if _write_position_to_conf(new):
        _remember_position(_read_active_game_id(), new)
        _signal_mangohud_reload()

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

                # 317 = BTN_THUMBL (L3): HUD position toggle
                if code == 317 and val == 1:
                    toggle_hud_position()

                # 310 = BTN_TL (L1): ETK SCREENSHOT (in-race recommended).
                # Single-button trigger so the chord can't pass-through any
                # in-game side effect (cf. SELECT-clutch chord which fires
                # GT6's camera toggle alongside the screenshot).
                #
                # GATED by SCREENSHOT_MODE_FILE (always / in-game / disabled,
                # default in-game), cycled from Pitstop's TOOLS tab. We never
                # EVIOCGRAB, so L1 always passes through to the game regardless
                # -- 'disabled' simply stops us ALSO firing a screenshot,
                # genuinely freeing L1 for a game that binds it (e.g. rear-view
                # camera; on GT5P/GT6 that view doesn't render on Turnip anyway,
                # see dossiers/etk_gametest_status_sheet.md). The default
                # 'in-game' suppresses accidental frontend/Pitstop captures.
                # Only this L1 trigger is gated; the SELECT+DPAD-Up chord below
                # is a deliberate fallback and always fires.
                if code == 310 and val == 1 and _l1_screenshot_allowed():
                    fire_screenshot()

                # 314 = BTN_SELECT (clutch modifier for DPAD)
                if code == 314: clutch = (val == 1)

            # --- DPAD MAPPINGS (EV_ABS) ---
            elif etype == 3 and clutch:
                # InputPlumber maps DPAD to ABS_HAT0X (16) and ABS_HAT0Y (17)
                if code == 16: # Horizontal Axis
                    if val == 1: send_cmd("VAULT") # Right
                    elif val == -1: os.system("pkill -USR1 mangohud") # Left
                elif code == 17: # Vertical Axis
                    # Up: ETK SCREENSHOT (silent grim capture incl. MangoHUD).
                    # Deliberate chord, NOT gated by SCREENSHOT_MODE_FILE -- the
                    # mode governs only the bare-L1 trigger above. Lets an
                    # operator who set L1 'disabled' still grab a shot on demand.
                    if val == -1: fire_screenshot()

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