#!/usr/bin/env python3
# ==========================================================
# ETK MODULES INJECTOR — Tools-menu app registration
# ==========================================================
# Re-injects the ETK Tools-menu presences into the boot-volatile
# /storage/.config/modules/ directory. Per tool:
#   1. the launcher  <tool>.sh
#   2. the icon      <tool>.svg
#   3. the enriched  <game>  entry in gamelist.xml
#
# Registered tools: ETK Pitstop (always) and Chiaki Remote Play
# (unless ETK_CHIAKI=0 — the etk.conf knob, exported by env.sh).
#
# Rocknix wipes /storage/.config/modules and REGENERATES
# gamelist.xml on every boot (verified: the regenerated list
# never contains etk_pitstop.sh), so this must run post-boot —
# install.sh runs it once, and the Sentry tripwire re-runs it
# whenever any piece goes missing.
#
# Idempotent. Operates ONLY on the ETK <game> blocks — every
# other tool's entry is left byte-for-byte intact (dossier
# addendum invariant: upsert, never overwrite the whole file).
# ==========================================================
import os
import re
import shutil
import sys

ETK_ROOT = os.environ.get('ETK_ROOT', '/storage/games-internal/roms/etk')
MODULES_DIR = os.environ.get('MODULES_DIR', '/storage/.config/modules')
GAMELIST = os.environ.get('MODULES_GAMELIST', f"{MODULES_DIR}/gamelist.xml")

# The enriched Tools-menu entries. ASCII-only — Rocknix ES has no Unicode
# text rendering (dossier addendum invariant). All three artwork fields are
# relative to the modules system path, as ES expects for game images.
#
# WHY THREE ARTWORK TAGS: the default Rocknix theme (es-theme-art-book-next)
# HIDES the standard <image> mapping (md_image) and renders Tools art only
# through a subset-gated element bound to {game:thumbnail} (boxart) /
# {game:marquee} (logo) / {game:image} (image). Stock tool entries set only
# <image>, so under the default subset they show NO icon at all. We emit
# <thumbnail> + <marquee> + <image> (all -> the same SVG) so each tile
# renders whichever artwork subset is active, independent of the platform-wide
# Rocknix bug. Proven on-rig 2026-05-29; see dossiers/ToolsMenuArtworkDiagnosis.md
# and dossiers/RocknixToolsArtworkBugReport.md.
PITSTOP_BLOCK = """    <game>
        <path>./etk_pitstop.sh</path>
        <name>ETK Pitstop</name>
        <desc>The Emulation Tuning Kit makes PS3 emulation possible on Rocknix handhelds with a suite of on-board instrumentation, telemetry, configuration tuning, shader cache protection, simple package installation, and deep system performance optimizations.</desc>
        <developer>ETK</developer>
        <publisher>ETK</publisher>
        <rating>5.0</rating>
        <releasedate>2026</releasedate>
        <genre>Tool</genre>
        <players>1</players>
        <image>./etk_pitstop.svg</image>
        <thumbnail>./etk_pitstop.svg</thumbnail>
        <marquee>./etk_pitstop.svg</marquee>
    </game>
"""

CHIAKI_BLOCK = """    <game>
        <path>./etk_chiaki.sh</path>
        <name>Chiaki Remote Play</name>
        <desc>Stream your PlayStation 4 or PlayStation 5 to the handheld over WiFi (PS4/PS5 Remote Play). Pair once with the console PIN, then launch here to play. GT7 on the go: the streaming lane of the ETK garage.</desc>
        <developer>ETK</developer>
        <publisher>ETK</publisher>
        <rating>5.0</rating>
        <releasedate>2026</releasedate>
        <genre>Tool</genre>
        <players>1</players>
        <image>./etk_chiaki.svg</image>
        <thumbnail>./etk_chiaki.svg</thumbnail>
        <marquee>./etk_chiaki.svg</marquee>
    </game>
"""


def _game_regex(launcher):
    """Byte-mode pattern matching an existing <game> block for ./<launcher> —
    whether ours or a bare auto-scanned entry — so a stale entry is REPLACED,
    not duplicated. Byte mode because Rocknix's stock list carries strict-
    invalid XML (a raw '&' in touchHLE's <desc>) — every other tool's entry is
    preserved byte-for-byte, sanitizing the whole file is Rocknix's job
    (reported upstream, see RocknixToolsArtworkBugReport.md)."""
    esc = re.escape(launcher).encode('ascii')
    return re.compile(
        rb'[ \t]*<game>(?:(?!</game>).)*?<path>\s*\./' + esc + rb'\s*</path>'
        rb'.*?</game>\n?',
        re.S)


def _chiaki_enabled():
    return os.environ.get('ETK_CHIAKI', '1') != '0'


def _tools():
    """The Tools-menu registration table. Each entry: launcher/icon filenames
    (master in $ETK_ROOT/config/, mirrored into modules/), the gamelist block,
    and the sentinel name used by the Sentry tripwire's grep."""
    tools = [{
        'launcher': 'etk_pitstop.sh',
        'svg': 'etk_pitstop.svg',
        'svg_master': os.environ.get('ETK_PITSTOP_SVG', f"{ETK_ROOT}/config/etk_pitstop.svg"),
        'block': PITSTOP_BLOCK,
    }]
    if _chiaki_enabled():
        tools.append({
            'launcher': 'etk_chiaki.sh',
            'svg': 'etk_chiaki.svg',
            'svg_master': os.environ.get('ETK_CHIAKI_SVG', f"{ETK_ROOT}/config/etk_chiaki.svg"),
            'block': CHIAKI_BLOCK,
        })
    return tools


def _log(msg):
    sys.stderr.write(f"etk_modules_inject: {msg}\n")


def _mirror(master, dest, executable=False):
    """Copy master -> dest if dest is missing or differs. Returns True on
    a (re)write. Best-effort — a missing master is logged, not fatal."""
    try:
        if not os.path.exists(master):
            _log(f"master missing: {master}")
            return False
        with open(master, 'rb') as f:
            src = f.read()
        cur = None
        if os.path.exists(dest):
            with open(dest, 'rb') as f:
                cur = f.read()
        if cur != src:
            tmp = dest + '.etk.tmp'
            with open(tmp, 'wb') as f:
                f.write(src)
            os.replace(tmp, dest)
            if executable:
                os.chmod(dest, 0o755)
            return True
    except Exception as e:
        _log(f"mirror {dest}: {e}")
    return False


def _upsert_gamelist(tools):
    """Ensure modules/gamelist.xml carries exactly one <game> block per ETK
    tool. Replace a stale/bare entry if present, else insert before
    </gameList>. Byte-mode + atomic write — every other entry is preserved
    byte-for-byte; only the ETK blocks are ever written."""
    try:
        if os.path.exists(GAMELIST):
            with open(GAMELIST, 'rb') as f:
                xml = f.read()
        else:
            xml = b'<?xml version="1.0"?>\n<gameList>\n</gameList>\n'
        orig = xml

        for tool in tools:
            block = tool['block'].encode('utf-8')  # blocks are pure ASCII
            pat = _game_regex(tool['launcher'])
            if pat.search(xml):
                xml = pat.sub(lambda _m: block, xml, count=1)
            elif b'</gameList>' in xml:
                xml = xml.replace(b'</gameList>', block + b'</gameList>', 1)
            else:
                xml = b'<?xml version="1.0"?>\n<gameList>\n' + block + b'</gameList>\n'

        if xml != orig:
            tmp = GAMELIST + '.etk.tmp'
            with open(tmp, 'wb') as f:
                f.write(xml)
            os.replace(tmp, GAMELIST)
            return True
    except Exception as e:
        _log(f"gamelist upsert: {e}")
    return False


def main():
    try:
        os.makedirs(MODULES_DIR, exist_ok=True)
    except Exception as e:
        _log(f"mkdir {MODULES_DIR}: {e}")
        return
    tools = _tools()
    for tool in tools:
        _mirror(f"{ETK_ROOT}/config/{tool['launcher']}", f"{MODULES_DIR}/{tool['launcher']}", executable=True)
        _mirror(tool['svg_master'], f"{MODULES_DIR}/{tool['svg']}")
    _upsert_gamelist(tools)


if __name__ == '__main__':
    main()
