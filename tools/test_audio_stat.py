#!/usr/bin/env python3
"""ETK — host-side regression tests for the fork's audio telemetry line.

Run from the repo root:   python3 tools/test_audio_stat.py

The GTK Edition's cellAudio writes ONE line to /dev/shm/rpcs3_audio_stat every
~2 s (and the same line, `ep=`-prefixed, to rpcs3_audio_log). Two ETK readers
depend on its shape, and the next GTK Edition line grows a key — `drop=`,
whole 5.33 ms blocks the ring could not fit (ARMSX3 14e740513's counter, kept
per guest boot). Every reader has to take BOTH lines, because the ledger holds
hundreds of rows from builds that never wrote it.

  [FORMAT]     the new line is the old line with `drop=` APPENDED: same keys,
               same order. Optionally pinned to the producer itself: point
               ETK_AUDIO_PRODUCER at a cellAudio.cpp or a GTK Edition patch and
               its snprintf format must yield exactly NEW_KEYS.

  [POSTMORTEM] the REAL aud block from bin/session_postmortem.sh (not a copy
               that can drift), retargeted at a temp file: both lines fold into
               one ledger token with every byte kept, and the up_s stale-guard
               still reads up_s — not some other key — on both.

  [DYNO]       tools/etk_dyno.py: parse_kv keeps every old key's value; a cell
               without `drop=` is UNKNOWN (None), a new-build cell with drop=0
               is ZERO — conflating the two would credit every pre-drop-counter
               session as click-free. The --audio table gains DROP/min p50 and
               DROP N while its existing columns and ranking stay identical to
               the same ledger with every `drop=` stripped.

No rig, no network, no root. The shell block runs under the host's /bin/sh;
it is unchanged by this suite's feature and uses only POSIX-basic constructs.
Point ETK_REPO_ROOT at a copy of the kit to check a deliberately broken version.
"""
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
POSTMORTEM = os.path.join(ROOT, "bin", "session_postmortem.sh")
DYNO = os.path.join(ROOT, "tools", "etk_dyno.py")

spec = importlib.util.spec_from_file_location("dyno", DYNO)
dyno = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dyno)

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


# A real 0.9.0.x line (host ledger mirror, a 30-minute NPEA00050 session),
# and the same session as a drop-counter build writes it.
OLD_LINE = ("up_s=1804.1 ur=0 urb=0 skip=43 sil=252 ratio=1.000 rmin=1.000 "
            "enq_ms=74.7 buf_ms=34")
NEW_LINE = OLD_LINE + " drop=3"
OLD_KEYS = ["up_s", "ur", "urb", "skip", "sil", "ratio", "rmin", "enq_ms", "buf_ms"]
NEW_KEYS = OLD_KEYS + ["drop"]


def keys(line):
    return [tok.partition("=")[0] for tok in line.split()]


# ---------------------------------------------------------------------------
print("\n[FORMAT] drop= is appended; every earlier key keeps its place")
check("old line carries the nine aud1 keys in order", keys(OLD_LINE), OLD_KEYS)
check("new line = old keys, then drop", keys(NEW_LINE), NEW_KEYS)
check("new line's first nine tokens are the old line verbatim",
      NEW_LINE.split()[:9], OLD_LINE.split())

producer = os.environ.get("ETK_AUDIO_PRODUCER")
if producer:
    # a diff's removed lines are the OLD producer — only what the file ships counts
    src = "\n".join(l for l in open(producer).read().splitlines()
                    if not l.startswith("-"))
    m = re.search(r'"(up_s=[^"]*)\\n"', src)
    check_true("producer carries the rpcs3_audio_stat format string", m)
    if m:
        check("producer format keys == NEW_KEYS",
              [t.partition("=")[0] for t in m.group(1).split()], NEW_KEYS)
else:
    print("    skip producer pin (set ETK_AUDIO_PRODUCER=<cellAudio.cpp|patch>)")


# ---------------------------------------------------------------------------
print("\n[POSTMORTEM] real aud block, temp stat file")


def _aud_block(stat_path):
    """The REAL aud-attribution block from session_postmortem.sh, retargeted
    from /dev/shm at a temp file. Extracted, not restated: a drift between
    this test and the shipped ledger writer would defeat the test."""
    src = open(POSTMORTEM).read()
    m = re.search(r'(AUD_STAT="-"\nAUD_FILE="/dev/shm/rpcs3_audio_stat"\n.*?\nfi\n)'
                  r'\n# aud2 timeline archive', src, re.S)
    assert m, "could not find the aud block in session_postmortem.sh"
    return m.group(1).replace("/dev/shm/rpcs3_audio_stat", stat_path)


def postmortem_aud(line, duration):
    with tempfile.TemporaryDirectory() as d:
        stat_path = os.path.join(d, "rpcs3_audio_stat")
        with open(stat_path, "w") as fh:
            fh.write(line + "\n")           # the producer's own trailing \n
        now = int(time.time())
        script = (f"START_EPOCH={now - duration}\nDURATION={duration}\n"
                  + _aud_block(stat_path) + 'printf "%s" "$AUD_STAT"\n')
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout


for label, line in (("old", OLD_LINE), ("new", NEW_LINE)):
    folded = line.replace(" ", ",")
    check(f"{label} line folds into one ledger token, every byte kept",
          postmortem_aud(line, 1810), folded)
    # up_s=1804 vs a 60 s session: the stale-guard must still find up_s
    check(f"{label} line: up_s stale-guard still fires",
          postmortem_aud(line, 60), "-")
    # boundary: 1804 <= 1774 + 30 passes, 1804 > 1773 + 30 does not
    check(f"{label} line: stale-guard boundary is up_s, not a later key",
          (postmortem_aud(line, 1774), postmortem_aud(line, 1773)), (folded, "-"))


# ---------------------------------------------------------------------------
print("\n[DYNO] tools/etk_dyno.py reads both cells")
OLD_CELL, NEW_CELL = OLD_LINE.replace(" ", ","), NEW_LINE.replace(" ", ",")
old_kv, new_kv = dyno.parse_kv(OLD_CELL), dyno.parse_kv(NEW_CELL)
check("old cell parses to the nine keys", sorted(old_kv), sorted(OLD_KEYS))
check("new cell = old cell's values + drop",
      {k: v for k, v in new_kv.items() if k != "drop"}, old_kv)
check("new cell's drop value", new_kv.get("drop"), 3.0)

rate = getattr(dyno, "aud_drop_per_min", None)
check_true("dyno exposes aud_drop_per_min", rate is not None)
if rate:
    check("old cell -> None (unknown, never 0)", rate(old_kv), None)
    check("new cell -> drops per minute of AUDIO uptime",
          round(rate(new_kv), 4), round(3 / 1804.1 * 60, 4))
    check("new-build clean session -> 0.0, not None",
          rate(dyno.parse_kv(OLD_CELL + ",drop=0")), 0.0)
    check("up_s=0 -> None (no denominator)",
          rate({"up_s": 0.0, "drop": 5.0}), None)


def row(epoch, dur, game, aud):
    r = ["0"] * 31
    r[0], r[1], r[3], r[4], r[11], r[25] = str(epoch), str(dur), game, "CLEAN", "0", aud
    return "\t".join(r)


def cell(up, skip, drop=None):
    c = f"up_s={up},ur=1,urb=4096,skip={skip},sil=10,ratio=1.000,rmin=0.900,enq_ms=40.0,buf_ms=34"
    return c if drop is None else f"{c},drop={drop}"


# GT5P: two old cells + two new (6 drops / 600 s = 0.60/min; 0 / 300 s = 0.00)
# OLDONE: old cells only. NEWONE: one new cell + one STALE new cell (up_s far
# past the session) that the stale-guard must drop before drop= is counted.
LEDGER = [
    row(1, 610, "NPEA00050", cell(600, 1200)),
    row(2, 310, "NPEA00050", cell(300, 900)),
    row(3, 610, "NPEA00050", cell(600, 1500, drop=6)),
    row(4, 310, "NPEA00050", cell(300, 600, drop=0)),
    row(5, 400, "OLDONE0001", cell(400, 40)),
    row(6, 200, "OLDONE0001", cell(200, 10)),
    row(7, 120, "NEWONE0001", cell(120, 12, drop=2)),
    row(8, 100, "NEWONE0001", cell(900, 5, drop=40)),
]


def audio_table(lines):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sessions.tsv")
        with open(p, "w") as fh:
            fh.write("header\n" + "\n".join(lines) + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dyno.audio_report(dyno.Path(p), types.SimpleNamespace(game=None))
    text = out.getvalue()
    table = {l.split()[0]: l.split() for l in text.splitlines()[1:] if l[:1].isalpha()
             and not l.startswith("skip/s")}
    return text, text.splitlines()[0].split(), table


text, header, table = audio_table(LEDGER)
check("header gains DROP/min p50 and DROP N at the END",
      header, ["GAME", "N", "SKIP/s", "p50", "SKIP/s", "max", "UR", "p50",
               "AUDIO", "s", "DROP/min", "p50", "DROP", "N"])
gt = table.get("NPEA00050", [])
check("mixed title: N counts every audio cell", gt[1:2], ["4"])
check("mixed title: DROP/min p50 over new cells only", gt[6:7], ["0.30"])
check("mixed title: DROP N = cells carrying drop=", gt[7:8], ["2"])
check("old-only title: DROP/min is '-' and DROP N is 0",
      table.get("OLDONE0001", [])[6:8], ["-", "0"])
check("stale new cell is dropped before drop= is scored",
      table.get("NEWONE0001", [])[1:8], ["1", "0.10", "0.10", "1.0", "120", "1.00", "1"])
check_true("footer counts scored cells carrying drop=",
           "3 scored cells carry drop=" in text)

# Existing columns + ranking: identical to the same ledger with drop= stripped
_, _, bare = audio_table([re.sub(r",drop=\d+", "", l) for l in LEDGER])
check("existing six columns are unchanged by drop=",
      {g: r[:6] for g, r in table.items()}, {g: r[:6] for g, r in bare.items()})
check("ranking (skip/s p50) is unchanged by drop=",
      [l.split()[0] for l in text.splitlines()[1:4]],
      ["NPEA00050", "NEWONE0001", "OLDONE0001"])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL AUDIO-STAT CHECKS PASSED")
