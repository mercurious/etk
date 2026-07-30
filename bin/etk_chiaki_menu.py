#!/usr/bin/env python3
# ==========================================================
# ETK CHIAKI MENU — on-device console chooser + pairing wizard
# ==========================================================
# The title screen, console chooser and gamepad pairing flow for
# chiaki-rocknix ("tuned by ETK"). Runs inside the scaled foot window
# that config/etk_chiaki.sh spawns; visual language and the direct-evdev
# gamepad reader are inherited from bin/etk_pitstop.py.
#
# Contract with the launcher:
#   exit 0 -> stream the config whose path is in $CHOICE_FILE
#   exit 1 -> user chose EXIT (or fatal) -> back to EmulationStation
#
# Console registry: one config per console in /storage/.config/chiaki/
# consoles/*.conf (the chiaki binary's own key=value format, written by
# `chiaki regist --config`). A legacy single chiaki.conf is migrated in
# on first run. ASCII-only UI text (rig font law).
# ==========================================================
import base64
import curses
import os
import re
import struct
import subprocess
import sys
import time

ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')
CHIAKI_BIN = os.environ.get('ETK_CHIAKI_BIN', f"{ETK_ROOT}/tools/chiaki")
CONF_DIR = os.environ.get('ETK_CHIAKI_CONF_DIR', '/storage/.config/chiaki')
CONSOLES_DIR = f"{CONF_DIR}/consoles"
LEGACY_CONF = f"{CONF_DIR}/chiaki.conf"
CHOICE_FILE = os.environ.get('ETK_CHIAKI_CHOICE', '/dev/shm/etk_shm/chiaki_menu_choice')

TITLE = "CHIAKI-ROCKNIX"
SUBTITLE = "tuned by ETK"
SPINNER = "|/-\\"

# --- gamepad (etk_pitstop.py pattern: InputPlumber virtual pad, direct evdev) ---
PAD_HINTS = ("xbox", "dualsense", "dual sense", "playstation",
             "sony", "ds5", "wireless controller", "inputplumber")
PAD_EXCLUDE = ("touchpad", "motion sensor", "headset", "battery", "keyboard")
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY, EV_ABS = 1, 3
ABS_HAT0X, ABS_HAT0Y = 16, 17
BTN_CONFIRM, BTN_BACK = 304, 305   # Cross / Circle
BTN_SELECT, BTN_START = 314, 315
BTN_TL = 310                        # L1: case toggle on the OSK

PAIR_TITLE, PAIR_OK, PAIR_ERR, PAIR_WARN = 1, 2, 3, 4


def _event_num(entry):
    try:
        return int(entry[len('event'):])
    except (ValueError, TypeError):
        return 1 << 30


def find_gamepad():
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir), key=_event_num):
            if not entry.startswith('event'):
                continue
            name_path = os.path.join(input_dir, entry, 'device/name')
            if os.path.exists(name_path):
                with open(name_path) as f:
                    nl = f.read().strip().lower()
                if any(h in nl for h in PAD_HINTS) and not any(x in nl for x in PAD_EXCLUDE):
                    return f"/dev/input/{entry}"
    except Exception:
        pass
    return '/dev/input/event9'


class Input:
    """Merged gamepad + keyboard events -> logical actions."""

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.fd = None
        try:
            self.fd = os.open(find_gamepad(), os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            pass  # keyboard-only degrade, same as Pitstop

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass

    def poll(self):
        """Return one of up/down/left/right/confirm/back/select/start/case or None."""
        if self.fd is not None:
            try:
                while True:
                    data = os.read(self.fd, EVENT_SIZE)
                    if not data or len(data) != EVENT_SIZE:
                        break
                    _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)
                    if etype == EV_ABS and code == ABS_HAT0Y and val == -1:
                        return 'up'
                    if etype == EV_ABS and code == ABS_HAT0Y and val == 1:
                        return 'down'
                    if etype == EV_ABS and code == ABS_HAT0X and val == -1:
                        return 'left'
                    if etype == EV_ABS and code == ABS_HAT0X and val == 1:
                        return 'right'
                    if etype == EV_KEY and val == 1:
                        if code == BTN_CONFIRM:
                            return 'confirm'
                        if code == BTN_BACK:
                            return 'back'
                        if code == BTN_START:
                            return 'start'
                        if code == BTN_SELECT:
                            return 'select'
                        if code == BTN_TL:
                            return 'case'
            except OSError:
                pass
        ch = self.stdscr.getch()
        if ch == curses.KEY_UP:
            return 'up'
        if ch == curses.KEY_DOWN:
            return 'down'
        if ch == curses.KEY_LEFT:
            return 'left'
        if ch == curses.KEY_RIGHT:
            return 'right'
        if ch in (curses.KEY_ENTER, 10, 13):
            return 'confirm'
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            return 'back'
        if ch == 27:
            return 'start'
        if ch == 9:
            return 'case'
        return None


# --- console registry --------------------------------------------------------

def _parse_conf(path):
    conf = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                conf[k.strip()] = v.strip()
    except OSError:
        return None
    return conf


def _registered(conf):
    return bool(conf and conf.get('host_addr') and conf.get('rp_regist_key') and conf.get('rp_key'))


def list_consoles():
    """[(path, nickname, host, is_ps5)] sorted by nickname."""
    out = []
    try:
        entries = sorted(os.listdir(CONSOLES_DIR))
    except OSError:
        entries = []
    for name in entries:
        if not name.endswith('.conf'):
            continue
        path = os.path.join(CONSOLES_DIR, name)
        conf = _parse_conf(path)
        if not _registered(conf):
            continue
        nick = conf.get('nickname') or name[:-5]
        ps5 = int(conf.get('target') or 0) >= 1000000
        out.append((path, nick, conf.get('host_addr', '?'), ps5))
    return out


def migrate_legacy():
    """One-time: fold a pre-menu chiaki.conf into the consoles registry."""
    os.makedirs(CONSOLES_DIR, exist_ok=True)
    if list_consoles():
        return
    conf = _parse_conf(LEGACY_CONF)
    if not _registered(conf):
        return
    nick = re.sub(r'[^A-Za-z0-9-]', '-', conf.get('nickname') or conf.get('host_addr', 'console'))
    dst = os.path.join(CONSOLES_DIR, f"{nick}.conf")
    if not os.path.exists(dst):
        try:
            with open(LEGACY_CONF, 'rb') as f:
                data = f.read()
            with open(dst, 'wb') as f:
                f.write(data)
            os.chmod(dst, 0o600)
        except OSError:
            pass


def known_account_ids():
    """[(b64, source-nickname)] across the registry, deduped."""
    seen, out = set(), []
    for path, nick, _host, _ps5 in list_consoles():
        conf = _parse_conf(path)
        aid = conf.get('psn_account_id') if conf else None
        if aid and aid not in seen:
            seen.add(aid)
            out.append((aid, nick))
    return out


def normalize_account_id(text):
    """Accept base64 (8 bytes) or the decimal account number; return b64 or None."""
    text = text.strip()
    if not text:
        return None
    if re.fullmatch(r'[0-9]{10,20}', text):
        try:
            return base64.b64encode(int(text).to_bytes(8, 'little')).decode()
        except (OverflowError, ValueError):
            return None
    try:
        if len(base64.b64decode(text, validate=True)) == 8:
            return text
    except Exception:
        pass
    return None


# --- drawing -----------------------------------------------------------------

def draw_frame(stdscr, subtitle=None):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    try:
        stdscr.addstr(1, max(2, (w - len(TITLE)) // 2), TITLE,
                      curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
        sub = subtitle if subtitle else SUBTITLE
        stdscr.addstr(2, max(2, (w - len(sub)) // 2), sub, curses.A_DIM)
        stdscr.hline(3, 2, curses.ACS_HLINE, max(1, w - 4))
    except curses.error:
        pass
    return h, w


def draw_hints(stdscr, text):
    h, w = stdscr.getmaxyx()
    try:
        stdscr.hline(h - 2, 2, curses.ACS_HLINE, max(1, w - 4))
        stdscr.addstr(h - 1, max(2, (w - len(text)) // 2), text[:w - 4], curses.A_DIM)
    except curses.error:
        pass


def busy(stdscr, inp, msg, work):
    """Spinner while `work` (a Popen) runs; returns (rc, stdout_text)."""
    i = 0
    while work.poll() is None:
        h, w = draw_frame(stdscr)
        try:
            stdscr.addstr(h // 2, max(2, (w - len(msg)) // 2), msg, curses.A_BOLD)
            stdscr.addstr(h // 2 + 2, w // 2, SPINNER[i % len(SPINNER)], curses.A_BOLD)
        except curses.error:
            pass
        stdscr.refresh()
        inp.poll()  # drain input so mashing buttons can't queue actions
        i += 1
        time.sleep(0.12)
    out = work.stdout.read() if work.stdout else ''
    return work.returncode, out


def menu_select(stdscr, inp, items, subtitle=None, hints="A: select   B: back", start=0):
    """Generic vertical chooser. items = [(label, dim)] -> index or None on back."""
    sel = max(0, min(start, len(items) - 1))
    while True:
        h, w = draw_frame(stdscr, subtitle)
        top = 5
        for i, (label, dim) in enumerate(items):
            attr = curses.A_REVERSE | curses.A_BOLD if i == sel else (curses.A_DIM if dim else curses.A_NORMAL)
            try:
                stdscr.addstr(top + i * 2, 4, ("> " if i == sel else "  ") + label[:w - 8], attr)
            except curses.error:
                pass
        draw_hints(stdscr, hints)
        stdscr.refresh()
        act = inp.poll()
        if act == 'up':
            sel = (sel - 1) % len(items)
        elif act == 'down':
            sel = (sel + 1) % len(items)
        elif act == 'confirm':
            return sel
        elif act == 'back':
            return None
        time.sleep(0.03)


# --- on-screen keyboard ------------------------------------------------------

OSK_TEXT = [
    "abcdefghij",
    "klmnopqrst",
    "uvwxyz-_.@",
    "0123456789",
    "+/=        ",
]
OSK_PIN = ["0123456789"]


def osk(stdscr, inp, prompt, rows, initial="", maxlen=64, upper_toggle=True):
    """Gamepad keyboard: dpad move, A type, B backspace, L1 case, Start done.
    Returns the string, or None if backed out with an empty field."""
    text = initial
    ry, rx = 0, 0
    upper = False
    while True:
        h, w = draw_frame(stdscr)
        try:
            stdscr.addstr(5, 4, prompt[:w - 8], curses.A_BOLD)
            field = text + "_"
            stdscr.addstr(7, 4, field[-(w - 8):], curses.color_pair(PAIR_OK) | curses.A_BOLD)
        except curses.error:
            pass
        top = 9
        for y, row in enumerate(rows):
            for x, chch in enumerate(row):
                shown = chch.upper() if upper and chch.isalpha() else chch
                attr = curses.A_REVERSE | curses.A_BOLD if (y == ry and x == rx) else curses.A_NORMAL
                try:
                    stdscr.addstr(top + y * 2, 4 + x * 4, f" {shown} ", attr)
                except curses.error:
                    pass
        hint = "A: type   B: delete   Start: done"
        if upper_toggle:
            hint += "   L1: case"
        draw_hints(stdscr, hint)
        stdscr.refresh()

        act = inp.poll()
        row = rows[ry]
        if act == 'up':
            ry = (ry - 1) % len(rows)
            rx = min(rx, len(rows[ry].rstrip()) - 1)
        elif act == 'down':
            ry = (ry + 1) % len(rows)
            rx = min(rx, len(rows[ry].rstrip()) - 1)
        elif act == 'left':
            rx = (rx - 1) % len(row.rstrip())
        elif act == 'right':
            rx = (rx + 1) % len(row.rstrip())
        elif act == 'confirm':
            ch = row[rx]
            if ch != ' ' and len(text) < maxlen:
                text += ch.upper() if upper and ch.isalpha() else ch
        elif act == 'back':
            if text:
                text = text[:-1]
            else:
                return None
        elif act == 'case' and upper_toggle:
            upper = not upper
        elif act == 'start':
            return text
        time.sleep(0.03)


# --- pairing wizard ----------------------------------------------------------

def scan_consoles(stdscr, inp):
    proc = subprocess.Popen([CHIAKI_BIN, 'scan', '-t', '4'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _rc, out = busy(stdscr, inp, "Searching for consoles...", proc)
    found = []
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) == 4:
            found.append({'addr': parts[0], 'state': parts[1], 'name': parts[2], 'ps5': parts[3] == '1'})
    return found


def message(stdscr, inp, lines, hints="A: continue"):
    while True:
        h, w = draw_frame(stdscr)
        for i, line in enumerate(lines):
            try:
                stdscr.addstr(5 + i, 4, line[:w - 8])
            except curses.error:
                pass
        draw_hints(stdscr, hints)
        stdscr.refresh()
        act = inp.poll()
        if act in ('confirm', 'back', 'start'):
            return act
        time.sleep(0.03)


def pick_account_id(stdscr, inp):
    """Reuse a known account id or enter one (b64 or decimal). None = backed out."""
    known = known_account_ids()
    items = [(f"Use PSN account from {nick}", False) for _aid, nick in known]
    items.append(("Enter PSN account id (base64 or number)", False))
    idx = menu_select(stdscr, inp, items, subtitle="PSN ACCOUNT")
    if idx is None:
        return None
    if idx < len(known):
        return known[idx][0]
    while True:
        raw = osk(stdscr, inp, "PSN account id (psntools.com shows it):", OSK_TEXT)
        if raw is None:
            return None
        aid = normalize_account_id(raw)
        if aid:
            return aid
        message(stdscr, inp, ["That is not a valid account id.",
                              "",
                              "Enter either the base64 form (ends with =)",
                              "or the long number form. Both are shown by",
                              "psntools.com/psn/checker for your PSN name."])


def pair_wizard(stdscr, inp):
    """Full pairing flow. Returns new config path or None."""
    found = scan_consoles(stdscr, inp)
    items = []
    for c in found:
        state = c['state']
        label = f"{c['name']}  ({c['addr']})  [{'PS5' if c['ps5'] else 'PS4'}, {state}]"
        items.append((label, state != 'ready'))
    items.append(("Enter IP address manually", False))
    idx = menu_select(stdscr, inp, items, subtitle="PAIR NEW CONSOLE",
                      hints="A: select   B: cancel")
    if idx is None:
        return None

    if idx < len(found):
        target = found[idx]
        if target['state'] != 'ready':
            message(stdscr, inp, ["The console is in rest mode.",
                                  "",
                                  "Pairing needs it fully ON: press its power",
                                  "button (or the PS button on its pad), then",
                                  "run pairing again."])
            return None
        addr, ps5, nick = target['addr'], target['ps5'], target['name']
    else:
        addr = osk(stdscr, inp, "Console IP address:", ["0123456789."], upper_toggle=False)
        if not addr:
            return None
        kind = menu_select(stdscr, inp, [("PlayStation 5", False), ("PlayStation 4", False)],
                           subtitle="CONSOLE TYPE")
        if kind is None:
            return None
        ps5, nick = kind == 0, addr

    aid = pick_account_id(stdscr, inp)
    if aid is None:
        return None

    message(stdscr, inp, ["On the console:",
                          "",
                          "  Settings > System > Remote Play",
                          "  > Pair Device",
                          "",
                          "It shows an 8-digit PIN. Enter it next."])
    pin = osk(stdscr, inp, "Pairing PIN from the console screen:", OSK_PIN,
              maxlen=8, upper_toggle=False)
    if not pin or len(pin) != 8:
        if pin is not None:
            message(stdscr, inp, ["The PIN is exactly 8 digits."])
        return None

    safe = re.sub(r'[^A-Za-z0-9-]', '-', nick) or addr.replace('.', '-')
    conf_path = os.path.join(CONSOLES_DIR, f"{safe}.conf")
    cmd = [CHIAKI_BIN, 'regist', '--host', addr, '--pin', pin,
           '--account-id', aid, '--config', conf_path]
    cmd.append('--ps5' if ps5 else '--ps4')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rc, out = busy(stdscr, inp, "Pairing with the console...", proc)
    if rc != 0 or not _registered(_parse_conf(conf_path)):
        tail = [l[:70] for l in out.splitlines()[-4:]]
        message(stdscr, inp, ["Pairing failed.", ""] + tail +
                ["", "Check the PIN (each one is single-use) and", "that Remote Play is enabled on the console."])
        return None
    message(stdscr, inp, ["Paired successfully!"])
    return conf_path


# --- main --------------------------------------------------------------------

def chooser(stdscr, inp):
    """Returns chosen config path (stream) or None (exit)."""
    last = 0
    while True:
        consoles = list_consoles()
        items = [(f"{nick}  ({host})  [{'PS5' if ps5 else 'PS4'}]", False)
                 for _p, nick, host, ps5 in consoles]
        items.append(("PAIR NEW CONSOLE", False))
        items.append(("EXIT", False))
        idx = menu_select(stdscr, inp, items, hints="A: select   B/Exit: back to ES",
                          start=last)
        if idx is None or idx == len(items) - 1:
            return None
        if idx == len(items) - 2:
            new = pair_wizard(stdscr, inp)
            if new:
                last = 0
            continue
        return consoles[idx][0]


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    # explicit black canvas — the bare foot default is the grey screen the
    # operator asked to never see again
    curses.init_pair(PAIR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_OK, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(PAIR_ERR, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(PAIR_WARN, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)
    stdscr.bkgd(' ', curses.color_pair(5))
    stdscr.nodelay(True)

    inp = Input(stdscr)
    try:
        # title splash with throbber while the registry warms up
        for i in range(10):
            h, w = draw_frame(stdscr)
            try:
                stdscr.addstr(h // 2, max(2, (w - 14) // 2), "Remote Play", curses.A_BOLD)
                stdscr.addstr(h // 2 + 2, w // 2, SPINNER[i % len(SPINNER)], curses.A_BOLD)
            except curses.error:
                pass
            stdscr.refresh()
            time.sleep(0.08)
        migrate_legacy()

        choice = chooser(stdscr, inp)
        if choice is None:
            return 1
        os.makedirs(os.path.dirname(CHOICE_FILE), exist_ok=True)
        tmp = CHOICE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            f.write(choice)
        os.replace(tmp, CHOICE_FILE)
        return 0
    finally:
        inp.close()


if __name__ == '__main__':
    try:
        sys.exit(curses.wrapper(main))
    except KeyboardInterrupt:
        sys.exit(1)
