#!/bin/sh
# =============================================================================
# contact_sheet.sh  --  Cockpit one-shot contact sheet of on-rig screenshots
# -----------------------------------------------------------------------------
# Pulls the N most-recent ETK screenshots off a ROCKNIX rig (ssh) and montages
# them into ONE labeled grid PNG (host-side, PIL) so the engineer can Read the
# whole run's frames in a single look — section/car-density/fps overview at a
# glance. Born from the HSL tunnel analysis (2026-06-22): doing it by hand was
# pull-each + montage + read; this is one shot.
#
# USAGE:  contact_sheet.sh [COUNT] [OUTPUT_PNG]
#   COUNT       number of most-recent screenshots        (default 24)
#   OUTPUT_PNG  sheet path to write                       (default /tmp/cockpit_contact_sheet.png)
# ENV:
#   COCKPIT_RIG_SSH   rig ssh target   (default: $RIG_SSH or root@<SOC>.local)
#   COCKPIT_SS_DIR    screenshots dir on rig (default: auto-detect ETK dir)
#   COCKPIT_SS_GLOB   filename glob    (default: *.png; e.g. '*20260622_2024*' for one run)
#   COCKPIT_COLS      grid columns     (default 4)
#
# Prints the output PNG path on success (Read it to analyze). Host needs python3
# + Pillow. Rig side is BusyBox-safe (ls -t, tar). ssh/ROCKNIX path only — on the
# adb/Android target, screenshots come from screencap, not this dir (TODO).
# =============================================================================
set -e
COUNT="${1:-24}"
OUT="${2:-/tmp/cockpit_contact_sheet.png}"
RIG="${COCKPIT_RIG_SSH:-${RIG_SSH:-root@SM8250.local}}"
GLOB="${COCKPIT_SS_GLOB:-*.png}"
COLS="${COCKPIT_COLS:-4}"
TMP="$(mktemp -d /tmp/cockpit_cs.XXXXXX)"

# 1. Resolve the screenshots dir on the rig (ETK convention; override via env).
SSDIR="${COCKPIT_SS_DIR:-}"
if [ -z "$SSDIR" ]; then
  SSDIR=$(ssh -o ConnectTimeout=8 "$RIG" '
    for d in /storage/games-internal/roms/etk/screenshots /storage/roms/etk/screenshots; do
      [ -d "$d" ] && { echo "$d"; break; }
    done')
fi
[ -z "$SSDIR" ] && { echo "ERROR: no screenshots dir on $RIG (set COCKPIT_SS_DIR)" >&2; exit 2; }

# 2. Pull the N most-recent matching screenshots in ONE round-trip (tar over ssh).
#    BusyBox ls -t orders newest-first; head -N takes the batch; tar streams them.
FILES=$(ssh -o ConnectTimeout=8 "$RIG" "cd '$SSDIR' 2>/dev/null && ls -1t $GLOB 2>/dev/null | head -n $COUNT")
[ -z "$FILES" ] && { echo "ERROR: no screenshots match '$GLOB' in $SSDIR" >&2; exit 2; }
echo "$FILES" | ssh -o ConnectTimeout=8 "$RIG" "cd '$SSDIR' && tar cf - -T -" | tar xf - -C "$TMP"
N=$(ls -1 "$TMP"/*.png 2>/dev/null | wc -l | tr -d ' ')
echo "pulled $N screenshots from $RIG:$SSDIR" >&2

# 3. Montage host-side (PIL): chronological grid, each cell labeled #idx + HH:MM:SS.
python3 - "$TMP" "$OUT" "$COLS" <<'PY'
import glob, os, sys
from PIL import Image, ImageDraw, ImageFont
src, out, cols = sys.argv[1], sys.argv[2], int(sys.argv[3])
files = sorted(glob.glob(os.path.join(src, "*.png")))
if not files:
    sys.stderr.write("no frames to montage\n"); sys.exit(2)
tw = 460
thumbs = []
for f in files:
    im = Image.open(f).convert("RGB")
    thumbs.append((os.path.basename(f).split("_")[-1].replace(".png",""),
                   im.resize((tw, int(tw*im.height/im.width)))))
th, lab = thumbs[0][1].height, 22
rows = (len(thumbs)+cols-1)//cols
sheet = Image.new("RGB", (cols*tw, rows*(th+lab)), (18,18,22))
d = ImageDraw.Draw(sheet)
try: font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
except Exception:
    try: font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: font = ImageFont.load_default()
for i,(ts,im) in enumerate(thumbs):
    x,y = (i%cols)*tw, (i//cols)*(th+lab)
    d.rectangle([x,y,x+tw,y+lab], fill=(30,30,38))
    stamp = "%s:%s:%s"%(ts[:2],ts[2:4],ts[4:6]) if len(ts)>=6 else ts
    d.text((x+6,y+3), "#%02d  %s"%(i+1, stamp), fill=(0,220,140), font=font)
    sheet.paste(im, (x, y+lab))
sheet.save(out)
print("%s  (%dx%d, %d frames)" % (out, sheet.size[0], sheet.size[1], len(thumbs)))
PY
rm -rf "$TMP"
