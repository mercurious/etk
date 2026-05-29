#!/usr/bin/env python3
# ==========================================================
# ETK MODULES INJECTOR — Tools-menu app registration
# ==========================================================
# Re-injects the "ETK Pitstop" presence into the boot-volatile
# /storage/.config/modules/ directory:
#   1. the launcher  etk_pitstop.sh
#   2. the icon      etk_pitstop.svg
#   3. the enriched  <game>  entry in gamelist.xml
#
# Rocknix wipes /storage/.config/modules and REGENERATES
# gamelist.xml on every boot (verified: the regenerated list
# never contains etk_pitstop.sh), so this must run post-boot —
# install.sh runs it once, and the Sentry tripwire re-runs it
# whenever any of the three pieces goes missing.
#
# Idempotent. Operates ONLY on the ETK <game> block — every
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

SH_MASTER = f"{ETK_ROOT}/config/etk_pitstop.sh"
SVG_MASTER = os.environ.get('ETK_PITSTOP_SVG', f"{ETK_ROOT}/config/etk_pitstop.svg")
SH_DEST = f"{MODULES_DIR}/etk_pitstop.sh"
SVG_DEST = f"{MODULES_DIR}/etk_pitstop.svg"

# The enriched Tools-menu entry. ASCII-only — Rocknix ES has no Unicode
# text rendering (dossier addendum invariant). All three artwork fields are
# relative to the modules system path, as ES expects for game images.
#
# WHY THREE ARTWORK TAGS: the default Rocknix theme (es-theme-art-book-next)
# HIDES the standard <image> mapping (md_image) and renders Tools art only
# through a subset-gated element bound to {game:thumbnail} (boxart) /
# {game:marquee} (logo) / {game:image} (image). Stock tool entries set only
# <image>, so under the default subset they show NO icon at all. We emit
# <thumbnail> + <marquee> + <image> (all -> the same SVG) so the Pitstop tile
# renders whichever artwork subset is active, independent of the platform-wide
# Rocknix bug. Proven on-rig 2026-05-29; see dossiers/ToolsMenuArtworkDiagnosis.md
# and dossiers/RocknixToolsArtworkBugReport.md.
GAME_BLOCK = """    <game>
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

# Matches an existing <game> block for ./etk_pitstop.sh — whether ours or a
# bare auto-scanned entry — so a stale entry is REPLACED, not duplicated.
# A BYTES pattern: the gamelist is edited in byte mode so every other
# tool's entry is preserved byte-for-byte, even if Rocknix's stock list
# carries a non-UTF-8 byte or strict-invalid XML (its touchHLE <desc> has
# a raw '&'). We still must NOT rewrite other entries — sanitizing the whole
# file is Rocknix's job (reported upstream, see RocknixToolsArtworkBugReport.md).
# NOTE: the ES `next` branch is stricter and rejects that raw '&' (older ES
# tolerated it); but the malformed '&' is NOT what blanks the Tools icons —
# that's the theme field-mismatch handled by GAME_BLOCK above. Confirmed
# on-rig 2026-05-29.
_ETK_GAME = re.compile(
    rb'[ \t]*<game>(?:(?!</game>).)*?<path>\s*\./etk_pitstop\.sh\s*</path>'
    rb'.*?</game>\n?',
    re.S)


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


def _upsert_gamelist():
    """Ensure modules/gamelist.xml carries exactly one ETK Pitstop <game>
    block. Replace a stale/bare entry if present, else insert before
    </gameList>. Byte-mode + atomic write — every other entry is preserved
    byte-for-byte; only the ETK block is ever written."""
    block = GAME_BLOCK.encode('utf-8')          # GAME_BLOCK is pure ASCII
    try:
        if os.path.exists(GAMELIST):
            with open(GAMELIST, 'rb') as f:
                xml = f.read()
        else:
            xml = b'<?xml version="1.0"?>\n<gameList>\n</gameList>\n'

        if _ETK_GAME.search(xml):
            new = _ETK_GAME.sub(lambda _m: block, xml, count=1)
        elif b'</gameList>' in xml:
            new = xml.replace(b'</gameList>', block + b'</gameList>', 1)
        else:
            new = b'<?xml version="1.0"?>\n<gameList>\n' + block + b'</gameList>\n'

        if new != xml:
            tmp = GAMELIST + '.etk.tmp'
            with open(tmp, 'wb') as f:
                f.write(new)
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
    _mirror(SH_MASTER, SH_DEST, executable=True)
    _mirror(SVG_MASTER, SVG_DEST)
    _upsert_gamelist()


if __name__ == '__main__':
    main()
