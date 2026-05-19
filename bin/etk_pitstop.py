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

    # Read live config array only if the target file actually exists
    yaml_lines = []
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            yaml_lines = f.readlines()

    # Loop globally over matrix outside the file existence block
    for item in matrix:
        item["current_val"] = None
        item["enum_idx"] = 0

        for line in yaml_lines:
            if line.startswith(item["yaml_key"] + ":"):
                raw_val = line.split(":", 1)[1].strip().replace('"', '')

                if item["type"] == "int":
                    item["current_val"] = int(raw_val)
                elif item["type"] == "bool":
                    item["current_val"] = True if raw_val.lower() == "true" else False
                elif item["type"] == "enum":
                    item["current_val"] = raw_val
                    if raw_val in item["options"]:
                        item["enum_idx"] = item["options"].index(raw_val)
                break

        # Bulletproof fallback: assign defaults if key missed or file wasn't present
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
            if lines[i].startswith(item["yaml_key"] + ":"):
                if item["type"] == "enum":
                    lines[i] = f"{item['yaml_key']}: {item['options'][item['enum_idx']]}\n"
                elif item["type"] == "bool":
                    lines[i] = f"{item['yaml_key']}: {'true' if item['current_val'] else 'false'}\n"
                else:
                    lines[i] = f"{item['yaml_key']}: {item['current_val']}\n"
                updated_keys.add(item["yaml_key"])

    # Appender Layer: append keys entirely missing from the target YAML
    for item in matrix:
        if item["yaml_key"] not in updated_keys:
            if item["type"] == "enum":
                lines.append(f"{item['yaml_key']}: {item['options'][item['enum_idx']]}\n")
            elif item["type"] == "bool":
                lines.append(f"{item['yaml_key']}: {'true' if item['current_val'] else 'false'}\n")
            else:
                lines.append(f"{item['yaml_key']}: {item['current_val']}\n")

    with open(CONFIG_PATH, 'w') as f:
        f.writelines(lines)


def draw_interface(stdscr, matrix, active_idx, gamepad_status):
    """Draws custom curses viewport based entirely on the parsed schema framework."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    # Render Application Frame
    stdscr.attron(curses.color_pair(1))
    stdscr.addstr(0, 2, " ETK PITSTOP // SCHEMATIC DRIVEN EMULATOR TUNER ", curses.A_BOLD)
    stdscr.attroff(curses.color_pair(1))
    stdscr.addstr(1, 2, f"TARGET: config_{TARGET_ID}.yml  |  PAD: {gamepad_status}", curses.A_DIM)
    stdscr.addstr(2, 2, "-" * (w - 4), curses.A_DIM)

    start_y = 4
    for idx, item in enumerate(matrix):
        if start_y + idx >= h - 4:
            break
        is_selected = (idx == active_idx)
        prefix = "> " if is_selected else "  "
        attr = curses.A_REVERSE if is_selected else curses.A_NORMAL

        stdscr.addstr(start_y + idx, 4, prefix, curses.color_pair(1) if is_selected else curses.A_NORMAL)
        stdscr.addstr(start_y + idx, 8, f"{item['label']:<30}")

        if item["type"] == "enum":
            val_str = f"[ {item['options'][item['enum_idx']]} ]"
        elif item["type"] == "bool":
            val_str = f"[ {'ON' if item['current_val'] else 'OFF'} ]"
        else:
            val_str = f"[ {item['current_val']} ]"

        stdscr.addstr(start_y + idx, 40, val_str, attr)

    # Control footer
    stdscr.addstr(h - 3, 2, "-" * (w - 4), curses.A_DIM)
    footer = "DPAD UP/DN: Move  DPAD LT/RT: Change  A: Save  B: Drop  Q: Quit"
    stdscr.addstr(h - 2, 4, footer[:w - 6], curses.A_BOLD)
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

    running = True
    while running:
        draw_interface(stdscr, menu_data, cursor_idx, gamepad_status)

        # Keyboard input
        try:
            ch = stdscr.getch()
            if ch == ord('q') or ch == ord('Q'):
                running = False
            elif ch == curses.KEY_UP:
                cursor_idx = (cursor_idx - 1) % len(menu_data)
            elif ch == curses.KEY_DOWN:
                cursor_idx = (cursor_idx + 1) % len(menu_data)
            elif ch == curses.KEY_LEFT:
                _adjust_item(menu_data[cursor_idx], -1)
            elif ch == curses.KEY_RIGHT:
                _adjust_item(menu_data[cursor_idx], 1)
            elif ch == ord('\n') or ch == ord('s'):
                save_menu_matrix(menu_data)
                running = False
        except Exception:
            pass

        # Gamepad input — only if fd was successfully opened
        if fd is not None:
            try:
                data = os.read(fd, EVENT_SIZE)
                if data:
                    _, _, etype, code, val = struct.unpack(EVENT_FORMAT, data)

                    # A button — save and exit
                    if etype == 1 and code == 304 and val == 1:
                        save_menu_matrix(menu_data)
                        running = False

                    # B button — exit without save
                    elif etype == 1 and code == 305 and val == 1:
                        running = False

                    # D-PAD Up/Down (ABS_HAT0Y)
                    elif etype == 3 and code == 17:
                        if val == -1:
                            cursor_idx = (cursor_idx - 1) % len(menu_data)
                        elif val == 1:
                            cursor_idx = (cursor_idx + 1) % len(menu_data)

                    # D-PAD Left/Right (ABS_HAT0X)
                    elif etype == 3 and code == 16 and val != 0:
                        _adjust_item(menu_data[cursor_idx], val)

            except BlockingIOError:
                pass

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
