#!/usr/bin/env python3
# ==========================================================
# ETK PHASE 10: PYTHON SHIFTER (v10.5.0 - GAME-SAFE CHORDS)
# ==========================================================
# GEMINI IMMUTABLE RULE:
# 1. L1 + R3 (BTN_TL 310 held + BTN_THUMBR 318) = RECOVERY PANIC.
#    Chord-gated: a bare R3 stick-click is left to the game so it
#    can't trip recovery mid-race. When L1 is held, R3 invokes
#    bin/recovery.sh DIRECTLY and detached so the rig stays fully
#    untethered. Do NOT add a long-press/double-tap guard and do
#    NOT route this through CMD_QUEUE (queue needs a tethered
#    consumer). The chord is the only guard. THE R3 PATH IS NEVER
#    DELAYED, NEVER GATED, AND NEVER QUEUED — rules 5 and 6 below
#    add a hold-delay and a disable switch to OTHER chords only.
# 2. R1 + L3 (BTN_TR 311 held + BTN_THUMBL 317) = HUD PUNCHBOX CYCLE.
#    Chord-gated: bare L3 stays with the game. When R1 is held, L3
#    advances the HUD one stop: top -> bottom -> default -> off ->
#    repeat. The state is persisted per-game to $HUD_STATE_FILE; the
#    conf swap + live reload is done by bin/hud_apply.sh (shared with
#    the Sentry, which re-applies the per-game state at IDLE->RUNNING).
#    PIT/RACE thermal mode is now thermal_d.sh-internal only
#    (auto-PIT at RACE_THRESHOLD; auto-recovers to RACE at
#    RECOVER_THRESHOLD — no reboot, since thermal_d.sh v14).
#    HELD, NOT TAPPED — see rule 5.
# 3. SELF-HEAL: The InputPlumber virtual controller may not
#    exist when the Sentry spawns this at boot. The connect
#    loop MUST keep re-finding the device; do not collapse it
#    back into a one-shot open.
# 4. R1 + DPAD-Up = RSX FRAME CAPTURE (alternating resume);
#    R1 + DPAD-Down = BOG PROFILER (operator-assigned 2026-07-10;
#    RSX capture relocated Down->Up the same day, same mechanics).
#    Both deliberately NOT SELECT chords: we never EVIOCGRAB, so the
#    modifier always passes through to the game, and SELECT is
#    the in-game camera-view toggle — a SELECT chord knocks the
#    camera as collateral (the #11912 repro had to HOLD bumper
#    cam; a mid-race bog mark must not flip the view either).
#    R1 is the mid-race chord modifier; SELECT-clutch chords are
#    for rare tool actions only (operator call 2026-07-02,
#    generalized 2026-07-10). RPCS3's capture hotkey is Alt+C on the game
#    window and the Flip 2 has no keyboard, so the chord INJECTS
#    key events by writing raw input_event structs into the
#    existing "InputPlumber Keyboard" node (no uinput device
#    creation — a new device appearing mid-game can retrigger
#    pad re-enumeration). A SUCCESSFUL capture makes RPCS3 pause
#    itself (visible freeze = capture banked), so the chord
#    ALTERNATES: odd press = Alt+C (capture), even press =
#    Ctrl+P (resume). If a capture ever fails (no pause), the
#    operator presses twice more to get back in phase — never
#    stuck, no state to clean up. All I/O fail-silent so this
#    can never break the R3 panic path.
# 5. GAME-SAFE CHORDS (0.8.5, operator-directed): broadening the
#    game library past the GT series showed the ETK chords stealing
#    controls that other titles actually bind. Two changes, both
#    scoped to leave rule 1 alone:
#    (a) ETK SCREENSHOT moved from BARE L1 to L1 + L2. A bare
#        shoulder button is a real game control (handbrake, look-back,
#        shift-down); a shoulder + trigger chord is not. L2 is the
#        ANALOG axis on this pad (ABS_Z, 0-255) and it RESTS NONZERO
#        after first actuation (~12/255 — the H7 trigger-cal finding),
#        so the modifier is gated on a hysteresis pair (L2_ON/L2_OFF),
#        never on `val > 0` — that would latch the modifier on for the
#        rest of the session. BTN_TL2 (312) arms it too where the
#        target emits the digital twin.
#    (b) The R1+L3 PUNCHBOX must now be HELD for PUNCHBOX_HOLD_S
#        before it fires. An L3 stick-click while R1 happens to be
#        held is ordinary play in a lot of games; holding both for
#        ~0.4 s is not. Released early = cancelled, nothing fires.
#        The hold needs the read to be able to TIME OUT, which is why
#        the loop selects on the fd instead of blocking in read().
#        The timeout is armed ONLY while a punchbox press is pending;
#        with nothing pending the select blocks exactly as the old
#        read() did.
# 6. Chord POLICY (is this chord allowed right now?) is applied at
#    FIRE time in the dispatcher, never inside the chord state
#    machine — so the machine stays a pure, testable shape-matcher.
#    tools/test_chords.py drives it with synthetic evdev frames.
#    Gates: SCREENSHOT_MODE_FILE (L1+L2), BOG_CHORD_FILE
#    (R1+DPAD-Down), and the Chiaki in-stream stand-down.
# ==========================================================
import struct, os, time, re, glob, select, socket

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
# PUNCHBOX (4-state) per-game HUD memory + the shared applier that writes the
# live conf. Supersede the position-only file above.
HUD_STATE_FILE = os.environ.get(
    'HUD_STATE_FILE',
    '/storage/games-internal/roms/etk/etk_telemetry/hud_state.tsv')
HUD_APPLY = os.environ.get(
    'ETK_HUD_APPLY',
    '/storage/games-internal/roms/etk/bin/hud_apply.sh')
SCREENSHOT_MODE_FILE = os.environ.get(
    'SCREENSHOT_MODE_FILE',
    '/storage/games-internal/roms/etk/etk_telemetry/screenshot_mode.txt')
BOG_CHORD_FILE = os.environ.get(
    'BOG_CHORD_FILE',
    '/storage/games-internal/roms/etk/etk_telemetry/bog_chord.txt')

# Screenshot-chord gating (the chord itself is L1+L2 since 0.8.5 — rule 5a).
# 'always' fires whenever the chord is made, 'in-game' fires only while a PS3
# game is resolved, 'disabled' never fires (hands the chord back to the game).
# in-game is the default whenever the mode file is absent / unreadable /
# unrecognized -- the conservative choice that keeps it out of the frontend.
SCREENSHOT_MODES = ('always', 'in-game', 'disabled')
SCREENSHOT_MODE_DEFAULT = 'in-game'

# Bog-sampler chord gating (R1+DPAD-Down), toggled from Pitstop TOOLS. Same
# shape as the screenshot mode above, two states: the sampler is a forensic
# instrument, and on a title that binds R1+DPAD it is pure interference.
# Default enabled — the chord has been the perf lane's entry point since
# 2026-07-10 and disabling by default would silently retire it.
BOG_CHORD_STATES = ('enabled', 'disabled')
BOG_CHORD_DEFAULT = 'enabled'

# --- L2 as a chord modifier (rule 5a) ---
# L2 is ANALOG on the InputPlumber DS5 target: ABS_Z, 0-255. Trigger cal (H7)
# established that it rests NONZERO after first actuation (~12/255, kernel
# FLAT=0) and that the calibrated deadzone lands around 25 — so a `val > 0`
# test would arm the modifier permanently after the driver's first brake.
# L2_ON is a deliberate quarter-pull, well clear of that band; L2_OFF is lower
# so a trigger resting ON the threshold cannot chatter the modifier (and, with
# L1 held, machine-gun screenshots). Some targets also emit the digital twin
# BTN_TL2 — honoured directly, it needs no hysteresis.
BTN_TL2 = 312
ABS_Z = 2
L2_ON = 64
L2_OFF = 40

# --- Punchbox hold-delay (rule 5b) ---
# How long R1+L3 must be held before the HUD cycles. 0.4 s is above any
# incidental stick-click but still reads as "press", not "long-press".
PUNCHBOX_HOLD_S = float(os.environ.get('ETK_PUNCHBOX_HOLD_S', '0.4'))

# HUD toggle pair. bottom-left is the dashboard default — sits down by
# the physical controls on the Flip 2, below the typical "rear view mirror"
# game-element band. top-left is the alternative for games whose HUD
# elements crowd the bottom edge (e.g. GT5P).
HUD_POSITIONS = ('top-left', 'bottom-left')
HUD_DEFAULT = 'bottom-left'

# PUNCHBOX cycle (R1+L3): the four HUD stops in cycle order. top/bottom are the
# ETK DDU (position); default = minimal stock readout; off = hidden. The conf
# swap + reload for each stop is done by bin/hud_apply.sh (shared w/ the Sentry).
HUD_STATES = ('top', 'bottom', 'default', 'off')
HUD_STATE_DEFAULT = 'bottom'

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

def fire_bog_profile():
    """R1+DPAD-Down: BOG PROFILER — perf-sample the emulator through the
    section the driver just entered (bin/bog_profile.sh; default 30s, then
    symbolize + bank to telemetry). R1 chord, NOT SELECT (camera toggle —
    operator correction 2026-07-10). Detached + fail-silent by construction:
    the script itself debounces (double-press = no-op) and exits clean with
    no game/no perf, so this can never block the loop or the R3 path."""
    os.system("nohup sh /storage/games-internal/roms/etk/bin/bog_profile.sh >/dev/null 2>&1 &")

# --- RSX capture injection (R1+DPAD-Up; relocated from R1+Down 2026-07-10) ---
# Key codes from linux/input-event-codes.h. EV_KEY press/release pairs are
# written straight into the InputPlumber Keyboard event node; the kernel's
# input_inject_event() distributes them to sway exactly as if typed, and
# sway routes them to the focused game window (RPCS3's gs_frame handles the
# Alt+C "RSX Capture" and Ctrl+P "Toggle Pause" shortcuts itself).
KEY_LEFTCTRL, KEY_LEFTALT = 29, 56
KEY_C, KEY_P = 46, 25
_rsx_capture_phase = [0]   # even = next press captures, odd = next press resumes


def _find_keyboard():
    """Locate the InputPlumber virtual keyboard node (the sibling device the
    pad finder deliberately excludes). Same /sys walk as find_gamepad."""
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir), key=_event_num):
            if not entry.startswith('event'):
                continue
            name_path = os.path.join(input_dir, entry, 'device/name')
            if os.path.exists(name_path):
                with open(name_path, 'r') as f:
                    name = f.read().strip().lower()
                if 'keyboard' in name and 'inputplumber' in name:
                    return f"/dev/input/{entry}"
    except Exception:
        pass
    return None


def _inject_key_combo(modifier, key):
    """Write modifier+key press/release into the virtual keyboard node.
    Fail-silent: a missing node / EPERM must never disturb the input loop."""
    dev = _find_keyboard()
    if not dev:
        print("[!] ETK SHIFTER: no InputPlumber Keyboard node; RSX chord ignored", flush=True)
        return False
    try:
        with open(dev, 'wb', buffering=0) as f:
            def emit(code, val):
                # EV_KEY (1) then EV_SYN/SYN_REPORT (0,0) per event, kernel stamps time
                f.write(struct.pack(EVENT_FORMAT, 0, 0, 1, code, val))
                f.write(struct.pack(EVENT_FORMAT, 0, 0, 0, 0, 0))
            emit(modifier, 1)
            emit(key, 1)
            time.sleep(0.05)
            emit(key, 0)
            emit(modifier, 0)
        return True
    except Exception as e:
        print(f"[!] ETK SHIFTER: key injection failed: {e}", flush=True)
        return False


def fire_rsx_capture():
    """R1+DPAD-Down: alternate RSX frame capture (Alt+C) / resume (Ctrl+P).
    A successful capture pauses RPCS3 (that freeze IS the confirmation the
    .rrc banked); the next press resumes. See immutable rule 4."""
    if _rsx_capture_phase[0] % 2 == 0:
        ok = _inject_key_combo(KEY_LEFTALT, KEY_C)
        print(f"[*] ETK SHIFTER: RSX capture requested (Alt+C sent={ok})", flush=True)
    else:
        ok = _inject_key_combo(KEY_LEFTCTRL, KEY_P)
        print(f"[*] ETK SHIFTER: RSX capture resume (Ctrl+P sent={ok})", flush=True)
    if ok:
        _rsx_capture_phase[0] += 1

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

def _screenshot_chord_allowed():
    """Gate the L1+L2 chord by the current mode. 'disabled' never fires;
    'in-game' fires only when a real game id is resolved; 'always' fires
    unconditionally. (The SELECT+DPAD-Up chord is NOT gated -- see loop.)"""
    mode = _read_screenshot_mode()
    if mode == 'disabled':
        return False
    if mode == 'in-game':
        return _read_active_game_id() is not None
    return True  # 'always'


def _read_bog_chord_state():
    """Return the bog-sampler chord state, one of BOG_CHORD_STATES. Re-read
    on every press (one small file, human-paced) so a Pitstop TOOLS toggle
    takes effect with no daemon restart -- the screenshot-mode idiom. Any
    error / unrecognized value falls back to BOG_CHORD_DEFAULT."""
    try:
        with open(BOG_CHORD_FILE) as f:
            state = f.read().strip().lower()
        return state if state in BOG_CHORD_STATES else BOG_CHORD_DEFAULT
    except Exception:
        return BOG_CHORD_DEFAULT


def _bog_chord_allowed():
    """R1+DPAD-Down gate. Disabled hands the chord straight back to the game
    (we never EVIOCGRAB, so the buttons always passed through anyway -- this
    only stops us ALSO firing a 30 s perf record over the top of them)."""
    return _read_bog_chord_state() != 'disabled'

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


def _read_hud_state(game_id):
    """This game's remembered punchbox state, or the default. No game id
    (rig idle / unresolved) => the global default (never a sentinel key).
    An unrecognized stored token also collapses to the default."""
    if game_id:
        try:
            with open(HUD_STATE_FILE) as f:
                for line in f:
                    parts = line.rstrip('\n').split('\t')
                    if len(parts) >= 2 and parts[0] == game_id:
                        return parts[1] if parts[1] in HUD_STATES else HUD_STATE_DEFAULT
        except Exception:
            pass
    return HUD_STATE_DEFAULT


def _write_hud_state(game_id, state):
    """Upsert game_id -> state in HUD_STATE_FILE. No-op for a None game id
    (idle / unresolved) so a sentinel is never written as a game key. Same
    tmp+mv idiom as the position writer above."""
    if not game_id:
        return
    rows = {}
    try:
        with open(HUD_STATE_FILE) as f:
            for line in f:
                parts = line.rstrip('\n').split('\t')
                if len(parts) >= 2:
                    rows[parts[0]] = parts[1]
    except FileNotFoundError:
        pass
    except Exception:
        return
    rows[game_id] = state
    try:
        os.makedirs(os.path.dirname(HUD_STATE_FILE), exist_ok=True)
        tmp = HUD_STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            for gid, st in sorted(rows.items()):
                f.write(f'{gid}\t{st}\n')
        os.replace(tmp, HUD_STATE_FILE)
    except Exception:
        pass


def cycle_hud_state():
    """R1+L3 PUNCHBOX: advance the HUD one stop (top -> bottom -> default ->
    off -> repeat), persist it per-game, and hand the conf swap + live reload
    to bin/hud_apply.sh — detached and fail-silent so it can never stall the
    input loop or break the R3 panic path."""
    gid = _read_active_game_id()
    cur = _read_hud_state(gid)
    nxt = HUD_STATES[(HUD_STATES.index(cur) + 1) % len(HUD_STATES)]
    _write_hud_state(gid, nxt)
    os.system(f"bash {HUD_APPLY} {nxt} >/dev/null 2>&1 &")


def toggle_hud_position():
    """DEPRECATED (superseded by cycle_hud_state, the 4-state punchbox) — kept
    only until the next release-cut dead-code sweep. L3: flip MangoHud position
    top-left <-> bottom-left.
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

def _chiaki_active():
    """True while a Chiaki Remote Play stream runs (sentinel touched/removed
    by config/etk_chiaki.sh). Chiaki owns R1+L3 (resolution toggle) and
    L1+R3 (codec toggle) in-stream, so the ETK chords that share those
    shapes — punchbox, recovery, and the R1+DPAD RPCS3 tools — stand down.
    Screenshots (L1+L2 / SELECT+DPAD-Up) stay live: shots of a stream are
    useful and collide with nothing."""
    return os.path.exists(os.environ.get('ETK_CHIAKI_LOCK',
                                         '/dev/shm/etk_shm/chiaki_active'))


class Chords:
    """The chord SHAPE matcher — pure state, no I/O, no spawning (rule 6).

    feed() takes one decoded evdev frame and returns the list of action names
    it completed; tick() returns the actions a pending TIMED chord completed.
    The dispatcher in event_loop() applies policy (screenshot mode, bog
    enable, Chiaki stand-down) and does the firing. Splitting it this way is
    what lets tools/test_chords.py exercise every chord on the host without a
    pad — input_d is locked-down core and carries the R3 panic path.

    Actions: 'recovery' 'punchbox' 'screenshot' 'bog' 'rsx' 'vault'
             'mango_toggle'
    """

    def __init__(self, hold_s=PUNCHBOX_HOLD_S):
        self.hold_s = hold_s
        self.clutch = False    # BTN_SELECT (314) — DPAD clutch modifier
        self.l1 = False        # BTN_TL  (310) — R3-recovery / screenshot shoulder
        self.r1 = False        # BTN_TR  (311) — L3-punchbox / DPAD-tool shoulder
        self.l2 = False        # ABS_Z past L2_ON, or BTN_TL2 — screenshot modifier
        self.punchbox_at = None  # monotonic deadline while R1+L3 is held

    # -- the L2 modifier: analog with hysteresis, digital straight through --
    def _set_l2(self, held):
        """Returns the actions the transition completed. Rising edge with L1
        already down IS the screenshot chord — either order of the two
        presses fires it, which is the whole point of a chord."""
        if held == self.l2:
            return []
        self.l2 = held
        return ['screenshot'] if (held and self.l1) else []

    def feed(self, etype, code, val, now):
        acts = []

        # --- BUTTON MAPPINGS (EV_KEY) ---
        if etype == 1:
            # 310 = BTN_TL (L1) / 311 = BTN_TR (R1): shoulder modifiers.
            # Tracked as held-state so the stick-clicks (R3/L3) below only
            # fire ETK actions when chorded — bare R3/L3 stay free for the
            # game. Mirrors the SELECT `clutch` modifier used for the DPAD.
            if code == 310:
                self.l1 = (val == 1)
                # L1 + L2 = ETK SCREENSHOT (0.8.5, rule 5a). Fires on the
                # press that COMPLETES the chord; bare L1 no longer does
                # anything, which is the interference this change removes.
                if self.l1 and self.l2:
                    acts.append('screenshot')
            if code == 311:
                self.r1 = (val == 1)
                # R1 is the punchbox's held modifier: letting go of it
                # mid-hold cancels, same as letting go of L3.
                if not self.r1:
                    self.punchbox_at = None
            if code == BTN_TL2:
                acts += self._set_l2(val == 1)

            # L1 + R3 = RECOVERY PANIC. Chord-gated so a bare R3 stick-click
            # in-game never trips recovery. 318 = BTN_THUMBR (R3). Fires on
            # the press, undelayed and unqueued — immutable rule 1.
            if code == 318 and val == 1 and self.l1:
                acts.append('recovery')

            # R1 + L3 = HUD PUNCHBOX cycle. Chord-gated so a bare L3
            # stick-click stays with the game. 317 = BTN_THUMBL (L3).
            # Cycles top -> bottom -> default -> off -> repeat. HELD, not
            # tapped (rule 5b): the press only ARMS a deadline; tick() fires
            # it once the pair has survived hold_s. Release cancels.
            if code == 317:
                if val == 1 and self.r1:
                    self.punchbox_at = now + self.hold_s
                elif val == 0:
                    self.punchbox_at = None

            # 314 = BTN_SELECT (clutch modifier for DPAD)
            if code == 314:
                self.clutch = (val == 1)

        # --- AXIS MAPPINGS (EV_ABS) ---
        elif etype == 3:
            # L2 analog. Hysteresis pair, never `val > 0` — the DS5 target
            # rests nonzero after first actuation and would latch (rule 5a).
            if code == ABS_Z:
                if val >= L2_ON:
                    acts += self._set_l2(True)
                elif val <= L2_OFF:
                    acts += self._set_l2(False)
            # InputPlumber maps DPAD to ABS_HAT0X (16) and ABS_HAT0Y (17)
            elif self.clutch:
                if code == 16:      # Horizontal Axis
                    if val == 1:
                        acts.append('vault')          # Right
                    elif val == -1:
                        acts.append('mango_toggle')   # Left
                elif code == 17:    # Vertical Axis
                    # Up: ETK SCREENSHOT (silent grim capture incl. MangoHUD).
                    # Deliberate chord, NOT gated by SCREENSHOT_MODE_FILE -- the
                    # mode governs only the L1+L2 chord above. Lets an operator
                    # who set it 'disabled' still grab a shot on demand.
                    if val == -1:
                        acts.append('screenshot_forced')
                    # (SELECT+DPAD-Down stays FREE — and note for future
                    # chords: SELECT is the in-game CAMERA TOGGLE, so any
                    # SELECT chord flips the driver's view as collateral.
                    # Acceptable for rare tool actions (VAULT/mango/shot),
                    # WRONG for mid-race marks — those go on R1. Operator
                    # correction 2026-07-10, the rule-4 lesson generalized.)

            # R1 + DPAD chords (rule 4's constraint generalized: R1, never
            # SELECT, for anything fired mid-race — SELECT is the camera
            # toggle and would knock the view). In-game only by construction:
            # this daemon is Sentry-spawned in RUNNING state.
            #   R1+DPAD-Down = BOG PROFILER (operator-assigned 2026-07-10):
            #     mark the section start as the pack bogs; bin/bog_profile.sh
            #     samples 30s from HERE, DDU VAULT segment shows the s-bar,
            #     mako toast on banked. Debounced in the script. Disableable
            #     from Pitstop TOOLS since 0.8.5 (rule 5/6).
            #   R1+DPAD-Up = RSX FRAME CAPTURE / resume toggle (RELOCATED
            #     from R1+Down 2026-07-10 when the operator assigned Down to
            #     the bog profiler; same R1 mechanics, rule 4 intact).
            if self.r1 and code == 17:
                if val == 1:
                    acts.append('bog')
                elif val == -1:
                    acts.append('rsx')

        return acts

    def next_timeout(self, now):
        """Seconds until the pending punchbox fires, or None when nothing is
        pending. None means the caller blocks in select() exactly as the old
        read() did — the timeout exists only while a chord is being held."""
        if self.punchbox_at is None:
            return None
        return max(0.0, self.punchbox_at - now)

    def tick(self, now):
        """Fire any pending timed chord whose hold has elapsed."""
        if self.punchbox_at is not None and now >= self.punchbox_at:
            self.punchbox_at = None
            return ['punchbox']
        return []


def _dispatch(acts):
    """Apply chord POLICY, then fire. Every gate lives here (rule 6) so the
    state machine above stays a pure shape-matcher. Order is irrelevant —
    no two actions in one frame contend."""
    for a in acts:
        if a == 'recovery':
            # Rule 1: never gated by anything but the Chiaki stand-down,
            # which is a genuine chord COLLISION, not a policy.
            if not _chiaki_active():
                fire_recovery()
        elif a == 'punchbox':
            if not _chiaki_active():
                cycle_hud_state()
        elif a == 'screenshot':
            if _screenshot_chord_allowed():
                fire_screenshot()
        elif a == 'screenshot_forced':
            fire_screenshot()
        elif a == 'bog':
            if _bog_chord_allowed() and not _chiaki_active():
                fire_bog_profile()
        elif a == 'rsx':
            if not _chiaki_active():
                fire_rsx_capture()
        elif a == 'vault':
            send_cmd("VAULT")
        elif a == 'mango_toggle':
            os.system("pkill -USR1 mangohud")


def event_loop(device):
    """Read frames, hand them to the chord matcher, dispatch what it returns.

    buffering=0 is load-bearing: select() reports readiness of the FD, and a
    buffered reader can hold a whole event in Python's own buffer that select
    will never announce. Raw reads keep readiness and data 1:1. evdev always
    delivers whole events, so a 24-byte read is never short."""
    chords = Chords()
    with open(device, 'rb', buffering=0) as f:
        while True:
            timeout = chords.next_timeout(time.monotonic())
            ready, _, _ = select.select([f], [], [], timeout)
            if not ready:
                # Timed out: the only reason we ever ask for one is a held
                # chord coming due.
                _dispatch(chords.tick(time.monotonic()))
                continue
            data = f.read(EVENT_SIZE)
            if not data:
                break
            _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)
            now = time.monotonic()
            _dispatch(chords.feed(etype, code, val, now))
            # A frame can arrive after the hold elapsed but before select
            # timed out (pad traffic is continuous in-game) — check here too
            # or a held punchbox would wait on the next quiet moment.
            _dispatch(chords.tick(now))

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