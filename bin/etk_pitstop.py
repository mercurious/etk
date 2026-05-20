#!/usr/bin/env python3
# ==========================================================
# ETK PITSTOP // SCHEMATIC DRIVEN TUNER INTERFACE ENGINE
# Version: 12.3.0 - SECTION-AWARE KEY DISAMBIGUATION
# ==========================================================
# FIX 1: CONFIG_PATH and FIELDS_JSON normalized to /storage/games-internal/roms
#         (canonical ETK_ROOT per AI_MANIFEST.md and install.sh deployment target)
# FIX 2: Gamepad open failure no longer hard-exits via sys.exit() inside curses.
#         Falls back to keyboard-only mode gracefully — no more Error 230 on
#         InputPlumber late-init or missing virtual Xbox node.
# FIX 3: EVENT_FORMAT corrected from 'llHHI' (unsigned) to 'llHHi' (signed).
#         D-PAD axes send val=-1 for Up/Left; unsigned format silently swallowed
#         these as 4294967295, breaking all navigation. Matches input_d.py reference.
# FIX 4: Visible failure path — any init/runtime crash is logged to a persistent
#         file AND held on screen with a "press ENTER" prompt so the foot
#         terminal cannot tear down before the message is read. Rocknix tool
#         launchers exit the moment python does, so a bare traceback is
#         invisible. JSON schema parse errors now report line/column. Per-field
#         YAML value parse errors degrade to the bulletproof fallback instead
#         of killing the whole tuner — required to survive a complete RPCS3
#         config (~280 lines, nested mappings, unmanaged keys) as input.
# FIX 5: Section-aware matching — a schema entry may pin itself to a parent
#         section ("Audio", "Video", etc.) via an optional "section" field.
#         Without it, the bare yaml_key "  Renderer" collided between Audio
#         and Video because both are indented 2 spaces; a config missing
#         the Audio block silently corrupted Video Renderer on save, and the
#         appender spilled every unmatched key into whichever block ended
#         the file. Section-bound rows now only attach inside their named
#         block, and the appender inserts AT THE END of that block (or
#         logs + refuses if the block is absent).
# ==========================================================
import os
import sys
import json
import struct
import curses
import time
import traceback

# --- SHARED TRUTH ENVIRONMENT RESOLUTION ---
# Safely handle empty strings or whitespace leaking from the shell
raw_id = os.environ.get('TARGET_ID', '').strip()
TARGET_ID = raw_id if raw_id else 'NPUA80075'

# Inherit ETK_ROOT from env.sh, falling back to the canonical single-card path
ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')

CONFIG_PATH = f"/storage/games-internal/roms/bios/rpcs3/custom_configs/config_{TARGET_ID}.yml"
FIELDS_JSON = f"{ETK_ROOT}/config/pitstop_fields.json"
LOG_PATH = f"{ETK_ROOT}/log/etk_pitstop.log"


def _log(msg):
    """Append a timestamped line to LOG_PATH. Must never raise: a logging
    failure (read-only fs, missing parent, full disk) must not cascade into
    the tuner's own error handling and turn one bad field into a crash."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, 'a') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _fatal(msg, exc=None):
    """Final-error path. The Rocknix tool launcher exits the foot terminal
    the instant python exits, so a raw traceback is invisible. Log it, print
    it, and BLOCK on input so the operator can read it before the window
    disappears. Caller is responsible for ensuring curses has been torn down
    (curses.wrapper does this automatically on exception)."""
    _log(msg)
    if exc is not None:
        _log(traceback.format_exc())
    sys.stderr.write("\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(" ETK PITSTOP // FATAL\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(f" {msg}\n")
    if exc is not None:
        sys.stderr.write("\n")
        traceback.print_exc()
    sys.stderr.write("\n")
    sys.stderr.write(f" Log: {LOG_PATH}\n")
    sys.stderr.write("\n Press ENTER to close...")
    sys.stderr.flush()
    try:
        sys.stdin.readline()
    except Exception:
        time.sleep(15)
    sys.exit(1)

# Rocknix's PS3 launchers: each "<Game Name>.psn" file contains the title
# ID as text, so the directory is a static name<->ID map that resolves
# even when Pitstop runs idle from the tools menu (no rpcs3 process).
PS3_ROMS_DIR = "/storage/games-internal/roms/ps3"


def _section_of(line):
    """Detect a top-level YAML section header (e.g. 'Audio:', 'Video:').
    Returns the section name, or None if the line is not a section header.
    A section header is column-0, ends with ':' after stripping trailing
    whitespace, and is not a comment. Sub-mappings (4+ space indent like
    'Performance Overlay:' nested under Video) are intentionally NOT treated
    as sections — they stay inside their parent's scope, which is what
    section-pinned schema rows actually want."""
    s = line.rstrip()
    if not s or s[0] in (' ', '\t', '#'):
        return None
    if not s.endswith(":"):
        return None
    return s[:-1]


def _line_matches_item(line, current_section, item):
    """True iff the YAML line matches the schema item's key AND, if the item
    declares a parent section, the cursor is currently inside that section.

    The section gate is the whole point of FIX 5: without it the bare key
    '  Renderer' attached to whichever Renderer line appeared first in the
    file, and an Audio edit could silently rewrite Video (or vice versa)."""
    if not line.rstrip("\n").startswith(item["yaml_key"] + ":"):
        return False
    sect = item.get("section")
    if sect and sect != current_section:
        return False
    return True


def _find_section_range(lines, section):
    """Return (body_start_idx, body_end_idx) for the named top-level section.
    body_start is the line AFTER the section header; body_end is the index
    of the next top-level section header (or len(lines) if this is the last
    section). Returns None if the section header is not present.

    Used by the appender so a key bound to 'Audio' lands inside the Audio
    block instead of being dumped at EOF — which historically meant it
    ended up inside whichever block happened to be last in the file."""
    start = None
    for i, line in enumerate(lines):
        sec = _section_of(line)
        if sec == section and start is None:
            start = i + 1
            continue
        if start is not None and sec is not None:
            return (start, i)
    if start is not None:
        return (start, len(lines))
    return None


def resolve_game_name(target_id):
    """Map a title ID to its human display name via the .psn launchers.
    Falls back to the ID itself so the header is never blank."""
    try:
        for entry in os.listdir(PS3_ROMS_DIR):
            if not entry.endswith(".psn"):
                continue
            with open(os.path.join(PS3_ROMS_DIR, entry), 'r') as f:
                if f.read().strip() == target_id:
                    return entry[:-4]
    except Exception:
        pass
    return target_id


GAME_NAME = resolve_game_name(TARGET_ID)

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
        raise RuntimeError(f"Missing schema definitions: {FIELDS_JSON}")

    try:
        with open(FIELDS_JSON, 'r') as f:
            matrix = json.load(f)
    except json.JSONDecodeError as e:
        # Re-raise with file/line/column so the operator sees exactly which
        # row of the schema is malformed (e.g. trailing comma, stray period).
        # The default JSONDecodeError str() leaves out the path; without it
        # the user has to guess which of several config files broke.
        raise RuntimeError(
            f"Schema JSON parse error in {FIELDS_JSON} "
            f"at line {e.lineno}, col {e.colno}: {e.msg}"
        ) from e

    if not isinstance(matrix, list):
        raise RuntimeError(f"Schema JSON must be a list of field objects, got {type(matrix).__name__}")

    # Duplicate (section, yaml_key) pairs silently dedupe on save (first wins,
    # rest skipped), which makes "I edited it but it didn't stick" almost
    # impossible to debug by inspection. Log it now so the operator can correct
    # the schema rather than chase a phantom save bug. Keying by the
    # (section, yaml_key) pair is what lets Audio.Renderer and Video.Renderer
    # coexist as legitimate distinct rows.
    seen_keys = set()
    for item in matrix:
        sk = (item.get("section"), item.get("yaml_key"))
        if sk in seen_keys:
            label = f"section={sk[0]!r}, yaml_key={sk[1].strip() if sk[1] else sk[1]!r}"
            _log(f"Schema warning: duplicate ({label}) — second occurrence will be ignored on save")
        seen_keys.add(sk)

    yaml_lines = []
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                yaml_lines = f.readlines()
        except OSError as e:
            # A missing file is normal (handled above); a read failure on an
            # existing file is not — surface it instead of silently editing a
            # phantom empty config.
            raise RuntimeError(f"Cannot read config {CONFIG_PATH}: {e}")

    for item in matrix:
        item["current_val"] = None
        item["enum_idx"] = 0

        # Required-key sanity: a malformed field shouldn't kill the tuner —
        # log it, skip its YAML scan, and let the bulletproof fallback below
        # populate enough state for the row to render harmlessly.
        if "yaml_key" not in item or "type" not in item or "label" not in item:
            _log(f"Schema warning: field missing required keys, skipping: {item!r}")
            item.setdefault("label", "<malformed>")
            item.setdefault("type", "bool")
            item.setdefault("yaml_key", "")
            continue

        # Walk lines from the top each time so we can track which top-level
        # section the line lives in. An item with section="Audio" only
        # matches inside an Audio: block; without that gate the bare key
        # "  Renderer" attaches to whichever Renderer line came first.
        current_section = None
        for line in yaml_lines:
            sec = _section_of(line)
            if sec is not None:
                current_section = sec
                continue
            if _line_matches_item(line, current_section, item):
                raw_val = line.split(":", 1)[1].strip().replace('"', '')

                # Per-field parse failures (non-numeric int value, missing
                # options list on enum, etc.) must NOT abort the load — a
                # verbose RPCS3 config has ~280 lines of unmanaged keys around
                # the few we care about, and any one of them turning weird
                # would otherwise lock the whole tuner. Log and fall through
                # to the bulletproof fallback below.
                try:
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
                    else:
                        _log(f"Schema warning: unknown type '{item['type']}' for '{item['yaml_key'].strip()}'")
                except (ValueError, KeyError, TypeError) as e:
                    _log(f"Field '{item['yaml_key'].strip()}' raw={raw_val!r} parse failed ({e.__class__.__name__}: {e}); using fallback")
                    item["current_val"] = None
                break

        # Bulletproof fallback
        if item["current_val"] is None:
            if item["type"] == "int":
                item["current_val"] = item.get("min", 0)
            elif item["type"] == "bool":
                item["current_val"] = False
            elif item["type"] == "enum":
                opts = item.get("options") or ["<unset>"]
                item["options"] = opts
                item["current_val"] = opts[0]
                item["enum_idx"] = 0

    return matrix


def _render_value(item):
    if item["type"] == "enum":
        return item['options'][item['enum_idx']]
    if item["type"] == "bool":
        return 'true' if item['current_val'] else 'false'
    return str(item['current_val'])


def save_menu_matrix(matrix):
    """Line-by-line structural injector loop avoiding pyyaml dependencies.
    Section-aware: a row with section="Audio" only rewrites lines inside an
    Audio: block, and an absent key is inserted at the END of that block
    rather than appended to EOF (which historically landed it inside
    whichever block happened to be last)."""
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        lines = []
    else:
        with open(CONFIG_PATH, 'r') as f:
            lines = f.readlines()

    # readlines() preserves the missing-newline state of the final line if
    # the source file doesn't end in '\n' (several of the golden templates
    # don't). An insert at end-of-section / end-of-file then concatenates
    # onto that orphan line, producing malformed YAML like
    # "VRAM ... 65536  Read Color Buffers: false" on one line. Normalize.
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    # Track resolution by item identity, not just yaml_key, so two rows
    # sharing a key but pinned to different sections (Audio.Renderer vs
    # Video.Renderer) are each written exactly once.
    updated = set()
    current_section = None

    for i in range(len(lines)):
        sec = _section_of(lines[i])
        if sec is not None:
            current_section = sec
            continue
        for item in matrix:
            if id(item) in updated:
                continue
            if _line_matches_item(lines[i], current_section, item):
                lines[i] = f"{item['yaml_key']}: {_render_value(item)}\n"
                updated.add(id(item))
                break

    # Appender Layer — for keys that weren't present in the file.
    # Section-bound items insert into their named block; if the block is
    # absent we REFUSE to append (logging instead). Spilling an Audio key
    # into a sibling section is the exact corruption FIX 5 exists to stop.
    for item in matrix:
        if id(item) in updated:
            continue
        val_str = _render_value(item)
        sect = item.get("section")
        if sect:
            rng = _find_section_range(lines, sect)
            if rng is None:
                _log(
                    f"Cannot append '{item['yaml_key'].strip()}': section "
                    f"'{sect}' not present in {CONFIG_PATH} — skipping to "
                    f"avoid cross-section corruption"
                )
                continue
            lines.insert(rng[1], f"{item['yaml_key']}: {val_str}\n")
        else:
            lines.append(f"{item['yaml_key']}: {val_str}\n")
        updated.add(id(item))

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

    # Section-aware readback keyed by item identity, mirroring save's match
    # semantics so a value verified for Audio.Renderer cannot accidentally
    # confirm against the Video.Renderer line further down the file.
    seen = {}
    current_section = None
    for line in disk:
        sec = _section_of(line)
        if sec is not None:
            current_section = sec
            continue
        for item in matrix:
            if id(item) in seen:
                continue
            if _line_matches_item(line, current_section, item):
                seen[id(item)] = line.split(":", 1)[1].strip().replace('"', '')
                break

    bad = []
    for item in matrix:
        k = item["yaml_key"]
        if id(item) not in seen:
            bad.append(k.strip() + "(absent)")
            continue
        got = seen[id(item)]
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
    target = f"GAME: {GAME_NAME}  |  PAD: {gamepad_status}"
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
    # curses.wrapper restores the terminal on exception before re-raising —
    # so by the time _fatal runs, stderr is plain again and the operator can
    # actually read what we print. Without this catch the launcher exits the
    # foot terminal immediately on any traceback and the message is lost.
    try:
        curses.wrapper(main)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        _fatal(f"{e.__class__.__name__}: {e}", exc=e)
