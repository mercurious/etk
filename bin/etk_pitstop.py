#!/usr/bin/env python3
# ==========================================================
# ETK PITSTOP // SCHEMATIC DRIVEN TUNER INTERFACE ENGINE
# Version: 12.1.1 - PATH + GAMEPAD HARDENING
# ==========================================================
# FIX 1: CONFIG_PATH and FIELDS_JSON normalized to /storage/games-internal/roms
#         (canonical ETK_ROOT per AI_MANIFEST.md and install.sh deployment target)
# FIX 2: Gamepad open failure no longer hard-exits via sys.exit() inside curses.
#         Falls back to keyboard-only mode gracefully — no more Error 230 on
#         InputPlumber late-init or missing virtual Xbox node.
# FIX 3: EVENT_FORMAT corrected from 'llHHI' (unsigned) to 'llHHi' (signed).
#         D-PAD axes send val=-1 for Up/Left; unsigned format silently swallowed
#         these as 4294967295, breaking all navigation. Matches input_d.py reference.
# ==========================================================
import os
import sys
import json
import struct
import curses

# --- SHARED TRUTH ENVIRONMENT RESOLUTION ---
# Safely handle empty strings or whitespace leaking from the shell
raw_id = os.environ.get('TARGET_ID', '').strip()
TARGET_ID = raw_id if raw_id else 'NPUA80075'

# Inherit ETK_ROOT from env.sh, falling back to the canonical single-card path
ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')

CONFIG_PATH = f"/storage/games-internal/roms/bios/rpcs3/custom_configs/config_{TARGET_ID}.yml"
FIELDS_JSON = f"{ETK_ROOT}/config/pitstop_fields.json"

# FIX 3: SIGNED int ('i') to correctly receive val=-1 from D-PAD axes.
EVENT_FORMAT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


def find_gamepad():
    """Dynamically captures the exact InputPlumber Virtual Xbox target."""
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir)):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        if "xbox" in f.read().strip().lower():
                            return f"/dev/input/{entry}"
    except Exception:
        pass
    return '/dev/input/event9'

def load_menu_matrix():
    """Initializes schema definition and parses real values live from the target YAML file."""
    if not os.path.exists(FIELDS_JSON):
        sys.exit(f"[-] Missing schema definitions: {FIELDS_JSON}")

    with open(FIELDS_JSON, 'r') as f:
        matrix = json.load(f)

    yaml_lines = []
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            yaml_lines = f.readlines()

    for item in matrix:
        item["current_val"] = None
        item["enum_idx"] = 0

        for line in yaml_lines:
            # yaml_key carries its own indentation to disambiguate duplicate
            # keys (e.g. the Audio vs Video "Renderer"), so match the raw line.
            if line.rstrip("\n").startswith(item["yaml_key"] + ":"):
                raw_val = line.split(":", 1)[1].strip().replace('"', '')

                if item["type"] == "int":
                    item["current_val"] = int(raw_val)
                elif item["type"] == "bool":
                    item["current_val"] = True if raw_val.lower() == "true" else False
                elif item["type"] == "enum":
                    item["current_val"] = raw_val
                    # The UI and save path are driven entirely by enum_idx, so
                    # a value outside the curated options list must still be
                    # representable — otherwise it silently coerces to
                    # options[0] on the next save. Adopt the live value so it
                    # round-trips and remains selectable via the D-pad.
                    if raw_val not in item["options"]:
                        item["options"] = [raw_val] + item["options"]
                    item["enum_idx"] = item["options"].index(raw_val)
                break

        # Bulletproof fallback
        if item["current_val"] is None:
            if item["type"] == "int":
                item["current_val"] = item["min"]
            elif item["type"] == "bool":
                item["current_val"] = False
            elif item["type"] == "enum":
                item["current_val"] = item["options"][0]
                item["enum_idx"] = 0

    return matrix


def save_menu_matrix(matrix):
    """Line-by-line structural injector loop avoiding pyyaml dependencies."""
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        lines = []
    else:
        with open(CONFIG_PATH, 'r') as f:
            lines = f.readlines()

    updated_keys = set()

    for i in range(len(lines)):
        for item in matrix:
            # Mirror load's first-match semantics: skip keys already written so
            # an ambiguous key (e.g. Audio vs Video "Renderer") only rewrites
            # its first occurrence instead of clobbering the other section.
            if item["yaml_key"] in updated_keys:
                continue
            # yaml_key already includes its indentation; match the raw line.
            if lines[i].rstrip("\n").startswith(item["yaml_key"] + ":"):
                if item["type"] == "enum":
                    val_str = item['options'][item['enum_idx']]
                elif item["type"] == "bool":
                    val_str = 'true' if item['current_val'] else 'false'
                else:
                    val_str = str(item['current_val'])

                lines[i] = f"{item['yaml_key']}: {val_str}\n"
                updated_keys.add(item["yaml_key"])
                break

    # Appender Layer (Fallback for entirely missing keys)
    for item in matrix:
        if item["yaml_key"] not in updated_keys:
            if item["type"] == "enum":
                val_str = item['options'][item['enum_idx']]
            elif item["type"] == "bool":
                val_str = 'true' if item['current_val'] else 'false'
            else:
                val_str = str(item['current_val'])
            lines.append(f"{item['yaml_key']}: {val_str}\n")

    with open(CONFIG_PATH, 'w') as f:
        f.writelines(lines)


def commit_and_verify(matrix):
    """Save, then re-read CONFIG_PATH from disk and confirm every managed
    key actually holds the intended value. Returns (ok, status). A save
    that silently fails (read-only path, wrong target file, lost write)
    must never look like a success — that is the whole reported bug."""
    try:
        save_menu_matrix(matrix)
    except OSError as e:
        return False, f"WRITE FAIL: {e.__class__.__name__} {e}"

    try:
        with open(CONFIG_PATH, 'r') as f:
            disk = f.readlines()
    except OSError as e:
        return False, f"READBACK FAIL: {e.__class__.__name__} {e}"

    seen = {}
    for line in disk:
        for item in matrix:
            k = item["yaml_key"]
            if k not in seen and line.rstrip("\n").startswith(k + ":"):
                seen[k] = line.split(":", 1)[1].strip().replace('"', '')

    bad = []
    for item in matrix:
        k = item["yaml_key"]
        if k not in seen:
            bad.append(k.strip() + "(absent)")
            continue
        got = seen[k]
        if item["type"] == "enum":
            want = item["options"][item["enum_idx"]]
            ok = got == want
        elif item["type"] == "bool":
            want = "true" if item["current_val"] else "false"
            ok = got.lower() == want
        else:
            want = str(item["current_val"])
            ok = got == want
        if not ok:
            bad.append(f"{k.strip()}={got}!={want}")

    if bad:
        return False, "VERIFY MISMATCH: " + ", ".join(bad[:3])
    return True, "SAVED OK"


def draw_interface(stdscr, matrix, active_idx, gamepad_status, status=""):
    """Draws custom curses viewport based entirely on the parsed schema framework."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    # Render Application Frame
    stdscr.attron(curses.color_pair(1))
    stdscr.addstr(0, 2, " ETK PITSTOP // SCHEMATIC DRIVEN EMULATOR TUNER ", curses.A_BOLD)
    stdscr.attroff(curses.color_pair(1))
    total = len(matrix)
    # Lap-counter style position telemetry, e.g. "LAP 06/18"
    lap = f"LAP {active_idx + 1:02d}/{total:02d}"
    # Show the FULL resolved path, not just the basename — if saves aren't
    # persisting, the first thing to confirm is which file is being written.
    target = f"FILE: {CONFIG_PATH}  |  PAD: {gamepad_status}"
    stdscr.addstr(1, 2, target[:w - 4], curses.A_DIM)
    if w > len(lap) + 4:
        stdscr.addstr(1, w - len(lap) - 2, lap, curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(2, 2, "-" * (w - 4), curses.A_DIM)

    start_y = 4
    # Content rows live between the header rule (row 2) and the footer rule
    # (row h-3). Defensive clamp keeps this sane on a tiny handheld TTY.
    capacity = max(1, (h - 3) - start_y)

    # Stateless scroll window: center the cursor when possible, clamped to
    # the ends so the list never scrolls past its first/last entry.
    if total <= capacity:
        offset = 0
    else:
        offset = min(max(0, active_idx - capacity // 2), total - capacity)

    for row, idx in enumerate(range(offset, min(offset + capacity, total))):
        item = matrix[idx]
        y = start_y + row
        is_selected = (idx == active_idx)
        prefix = "> " if is_selected else "  "
        attr = curses.A_REVERSE if is_selected else curses.A_NORMAL

        stdscr.addstr(y, 4, prefix, curses.color_pair(1) if is_selected else curses.A_NORMAL)
        stdscr.addstr(y, 8, f"{item['label']:<30}")

        if item["type"] == "enum":
            val_str = f"[ {item['options'][item['enum_idx']]} ]"
        elif item["type"] == "bool":
            val_str = f"[ {'ON' if item['current_val'] else 'OFF'} ]"
        else:
            val_str = f"[ {item['current_val']} ]"

        stdscr.addstr(y, 40, val_str, attr)

    # Control footer — replaced by the save status when one exists so a
    # failed write is impossible to mistake for a clean save+exit.
    stdscr.addstr(h - 3, 2, "-" * (w - 4), curses.A_DIM)
    if status:
        ok = status == "SAVED OK"
        stdscr.addstr(h - 2, 4, status[:w - 6],
                      curses.A_BOLD | (curses.A_NORMAL if ok else curses.A_REVERSE))
    else:
        footer = "DPAD UP/DN: Move  DPAD LT/RT: Change  A: Save  B: Quit "
        stdscr.addstr(h - 2, 4, footer[:w - 6], curses.A_BOLD)

    # Scroll telltales — race shift-light chevrons parked on the rules.
    # Drawn last so they sit on top of the header/footer separator lines.
    if w > 16:
        if offset > 0:
            stdscr.addstr(2, w - 12, " /\\ MORE ", curses.color_pair(1) | curses.A_BOLD)
        if offset + capacity < total:
            stdscr.addstr(h - 3, w - 12, " \\/ MORE ", curses.color_pair(1) | curses.A_BOLD)

    stdscr.refresh()


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    stdscr.timeout(50)

    menu_data = load_menu_matrix()
    cursor_idx = 0
    device_path = find_gamepad()

    # FIX 2: Graceful fallback — do NOT sys.exit() here.
    # If InputPlumber's virtual Xbox node isn't ready yet (common on nightly boot),
    # drop to keyboard-only rather than crashing curses and triggering Error 230.
    fd = None
    gamepad_status = "NO PAD - KB ONLY"
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
        gamepad_status = f"OK ({device_path})"
    except Exception as e:
        gamepad_status = f"NO PAD ({e})"

    status = ""
    running = True
    while running:
        draw_interface(stdscr, menu_data, cursor_idx, gamepad_status, status)

        # Keyboard input. getch can raise on resize/interrupt — that is the
        # only thing we ignore here. Save errors must NOT be swallowed.
        try:
            ch = stdscr.getch()
        except curses.error:
            ch = -1
        if ch == ord('q') or ch == ord('Q'):
            running = False
        elif ch == curses.KEY_UP:
            cursor_idx = (cursor_idx - 1) % len(menu_data)
            status = ""
        elif ch == curses.KEY_DOWN:
            cursor_idx = (cursor_idx + 1) % len(menu_data)
            status = ""
        elif ch == curses.KEY_LEFT:
            _adjust_item(menu_data[cursor_idx], -1)
            status = ""
        elif ch == curses.KEY_RIGHT:
            _adjust_item(menu_data[cursor_idx], 1)
            status = ""
        elif ch == ord('\n') or ch == ord('s'):
            ok, status = commit_and_verify(menu_data)
            running = not ok  # only leave the editor once the write is proven

        # Gamepad input — only if fd was successfully opened
        if fd is not None:
            try:
                data = os.read(fd, EVENT_SIZE)
            except BlockingIOError:
                data = None
            except OSError as e:
                data = None
                gamepad_status = f"PAD READ ERR ({e.errno})"
            if data and len(data) == EVENT_SIZE:
                _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)

                # Confirm button — save, verify, exit only if it stuck.
                # This pad's InputPlumber virtual-Xbox node swaps South/East:
                # the physical confirm button emits BTN_EAST (305), not
                # BTN_SOUTH (304). Binding 304 here is why every "save"
                # silently took the old quit path. Verified on-device.
                if etype == 1 and code == 305 and val == 1:
                    ok, status = commit_and_verify(menu_data)
                    running = not ok

                # Back button (BTN_SOUTH 304 on this swapped pad) — exit
                # without saving.
                elif etype == 1 and code == 304 and val == 1:
                    running = False

                # D-PAD Up/Down (ABS_HAT0Y)
                elif etype == 3 and code == 17:
                    if val == -1:
                        cursor_idx = (cursor_idx - 1) % len(menu_data)
                        status = ""
                    elif val == 1:
                        cursor_idx = (cursor_idx + 1) % len(menu_data)
                        status = ""

                # D-PAD Left/Right (ABS_HAT0X)
                elif etype == 3 and code == 16 and val != 0:
                    _adjust_item(menu_data[cursor_idx], val)
                    status = ""

    if fd is not None:
        os.close(fd)


def _adjust_item(item, direction):
    """Shared value adjustment logic for both keyboard and gamepad input."""
    if item["type"] == "int":
        delta = item["step"] * direction
        item["current_val"] = max(item["min"], min(item["max"], item["current_val"] + delta))
    elif item["type"] == "bool":
        item["current_val"] = not item["current_val"]
    elif item["type"] == "enum":
        item["enum_idx"] = (item["enum_idx"] + direction) % len(item["options"])


if __name__ == "__main__":
    curses.wrapper(main)
