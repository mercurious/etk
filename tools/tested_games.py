#!/usr/bin/env python3
"""ETK — generate the Tested-Games wiki table from the ledger + config/.

    python3 tools/tested_games.py            > /tmp/tested-games.md
    python3 tools/tested_games.py --names names.tsv

Feeds https://github.com/mercurious/etk/wiki/Tested-Games: one row per PS3
title, its scored telemetry, and a link to the committed reference tune. The
tune links resolve because `tools/sync_game_configs.sh` carries the rig's live
`custom_configs/` into `config/` and the release gate fails a stale notebook —
before that, a wiki link would have pointed at a tune nobody was running.

LEDGER METHOD (manual §B.2), applied so the wiki cannot overstate:
  * ABORTED rows and sub-60 s runs dropped
  * impossible durations quarantined and REPORTED (one 2026-08 PANIC row
    carries 39.7 years from a broken session anchor)
  * medians, never means — a single 4 h idle row drags any average
  * `SURVIVED:*` counts as clean (the keepalive absorbed the hang and the
    race went on); `RECOVERY:*` does not
  * N is carried on every row, and a title below MIN_N is marked
    provisional rather than scored — no verdicts at N<3
  * a title with a config but no sessions is reported as SEEDED, not as 0%

TITLE NAMES are not derivable here: the ledger and the configs are keyed by
serial only, and the names live in the rig's ES gamelist. Pass them with
--names (a TSV of `SERIAL<TAB>Title Name`) or the serial stands alone. Do NOT
hand-guess them — a wrong title name on a public compatibility table is worse
than a bare serial.
"""
import argparse
import math
import os
import re
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone

COLS = dict(epoch=0, dur=1, game=3, status=4, shd=11, fps=16, res=19,
            perfect=28, rescues=29)
MAX_PLAUSIBLE_S = 86400
MIN_SESSION_S = 60
MIN_N = 3
BLOB = "https://github.com/mercurious/etk/blob/main/config"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="state/etk_telemetry/sessions.tsv")
    ap.add_argument("--configs", default="config")
    ap.add_argument("--names", help="TSV: SERIAL<TAB>Title Name")
    ap.add_argument("--vaults", default="state/meta/vault_sizes.tsv",
                    metavar="PATH",
                    help="TSV of SERIAL<TAB>kilobytes<TAB>files, measured on the "
                         "rig (du -k, BusyBox-safe). Refreshes the Vault Size "
                         "column; without it that column is left as-is.")
    ap.add_argument("--update-wiki", metavar="PATH",
                    help="rewrite an existing Tested-Games.md in place: relink "
                         "every title, refresh N/FPS, reorder by status then N. "
                         "Human columns (Audio/Notes/Vault Size) and all prose "
                         "are preserved verbatim.")
    a = ap.parse_args()

    names = {}
    if a.names:
        with open(a.names) as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 2 and p[0].strip():
                    names[p[0].strip()] = p[1].strip()

    rows, quarantined = [], []
    with open(a.ledger) as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or p[0] == "epoch":
                continue
            try:
                p[0] = int(p[0]); dur = float(p[1])
            except ValueError:
                continue
            if p[4].split(":")[0] == "ABORTED" or dur < MIN_SESSION_S:
                continue
            (quarantined if dur > MAX_PLAUSIBLE_S else rows).append(p)

    def g(r, k, d=None):
        i = COLS[k]
        return r[i] if i < len(r) and r[i] not in ("", "-") else d

    def gf(r, k, d=0.0):
        try:
            return float(g(r, k))
        except (TypeError, ValueError):
            return d

    per = defaultdict(list)
    for r in rows:
        per[g(r, "game", "?")].append(r)

    # A PS3 serial is four letters + five digits. Match it explicitly rather
    # than by filename length — `config.yml` and `etk_template.yml` also live
    # here, and a length test is one off-by-one away from an empty table.
    tunes = sorted(
        m.group(1)
        for m in (re.fullmatch(r"config_([A-Z]{4}\d{5})\.yml", f)
                  for f in os.listdir(a.configs))
        if m
    )

    if a.update_wiki:
        # mean of per-session medians — the page's OWN documented definition.
        stats = {}
        for sid, w in per.items():
            f = [gf(r, "fps") for r in w if gf(r, "fps") > 0]
            stats[sid] = (len(w), (sum(f) / len(f)) if f else None)
        vaults = {}
        if os.path.exists(a.vaults):
            for line in open(a.vaults):
                q = line.rstrip("\n").split("\t")
                if len(q) >= 2 and q[1].isdigit():
                    vaults[q[0].strip()] = int(q[1])
        new, nrows, nlinked, nvault = update_wiki(
            a.update_wiki, stats, names, set(tunes), vaults)
        with open(a.update_wiki, "w", encoding="utf-8") as fh:
            fh.write(new)
        print(f"rewrote {a.update_wiki}: {nrows} rows, {nlinked} linked to a tune, "
              f"{nvault} vault sizes refreshed")
        return

    print("<!-- generated by tools/tested_games.py — do not hand-edit; re-run it -->")
    print(f"<!-- ledger: {len(rows)} scored sessions"
          f"{', %d quarantined' % len(quarantined) if quarantined else ''} -->")
    print()
    print("| Title | Serial | Tune | Sessions | Median | Ceiling | Clean | Panics | FPS | Locked-60 |")
    print("|---|---|---|---|---|---|---|---|---|---|")

    def fmt_dur(s):
        return f"{int(s)//60}m {int(s)%60:02d}s" if s >= 60 else f"{int(s)}s"

    def ceiling(durs):
        """p90, NOT max — the manual's time-to-crash ceiling (§6.4). Max is the
        one stat a single idle row destroys: NPUA80075's longest 'session' is
        4h02m of a rig left running with the game up, and on a public
        compatibility table that reads as a promise of four-hour play.

        NEAREST-RANK (ceil), not a truncating index. Rounding DOWN put the
        ceiling BELOW the median on small samples — a 2-session title reported
        median 7m28s against a 'ceiling' of 4m53s, which is not a conservative
        estimate, it is a visibly broken one. ceil(0.9*N)-1 degrades to the max
        as N shrinks and never returns less than the median."""
        return durs[max(0, math.ceil(0.9 * len(durs)) - 1)]

    ordered = sorted(tunes, key=lambda s: -len(per.get(s, [])))
    for sid in ordered:
        w = per.get(sid, [])
        name = names.get(sid, "—")
        tune = f"[yml]({BLOB}/config_{sid}.yml)"
        if not w:
            print(f"| {name} | `{sid}` | {tune} | seeded, not yet raced | — | — | — | — | — | — |")
            continue
        durs = sorted(gf(r, "dur") for r in w)
        clean = sum(1 for r in w
                    if g(r, "status", "").split(":")[0] in ("CLEAN", "SURVIVED"))
        panics = sum(1 for r in w if g(r, "status", "").startswith("PANIC"))
        fps = [gf(r, "fps") for r in w if gf(r, "fps") > 0]
        pf = [gf(r, "perfect") for r in w if g(r, "perfect")]
        n = f"{len(w)}" + ("" if len(w) >= MIN_N else " ⚠︎")
        print(f"| {name} | `{sid}` | {tune} | {n} | {fmt_dur(st.median(durs))} "
              f"| {fmt_dur(ceiling(durs))} | {100.0*clean/len(w):.0f}% | {panics} "
              f"| {st.median(fps):.1f} | {st.median(pf):.1f}% |"
              if fps else
              f"| {name} | `{sid}` | {tune} | {n} | {fmt_dur(st.median(durs))} "
              f"| {fmt_dur(ceiling(durs))} | {100.0*clean/len(w):.0f}% | {panics} | — | — |")

    # Titles the ledger knows but no committed tune covers — the wiki should
    # not silently omit them; they are usually a rename or a seed that failed.
    orphans = sorted(set(per) - set(tunes))
    if orphans:
        print()
        print("<!-- raced but no committed tune: "
              + ", ".join(f"{o} (N={len(per[o])})" for o in orphans) + " -->")

    print()
    print(f"*⚠︎ = fewer than {MIN_N} scored sessions; treat as provisional "
          "(the GT5P clean-run noise floor alone spans 77–2886 s). "
          "Aborted runs and sub-60 s launches are excluded. `Clean` counts "
          "sessions that finished or were absorbed by the anti-lock net. "
          "`Locked-60` is the PERFECT-window share, the project KPI — it is "
          "judged at native 720p, so a low figure is honest rather than "
          "tuned around.*")

    if quarantined:
        print()
        for r in quarantined:
            d = datetime.fromtimestamp(r[0], timezone.utc).strftime("%Y-%m-%d")
            print(f"<!-- quarantined: {d} {g(r,'game')} duration_s={r[1]} "
                  "(impossible — broken session anchor) -->", file=sys.stderr)


# ==========================================================================
# WIKI UPDATE MODE — rewrite the operator's Tested-Games page in place.
# --------------------------------------------------------------------------
# The page is NOT regenerable from the ledger. Three of its columns are human
# intel that exists nowhere else: Audio ("Race stutter", "Good"), Notes ("Rear-
# view mirror does not render", "crashes into combat"), and Vault Size. Those
# are parsed out of the live page and carried across UNTOUCHED. Only what is
# derivable gets refreshed: the session count, the FPS figure, the config link,
# and the row order. Everything below the table — the whole methodology and
# "Reading this table" prose — is preserved byte-for-byte.
#
# FPS keeps the page's OWN documented definition (mean of per-session median
# FPS), not the median-of-medians the charts use. The footer explains that
# definition to readers; silently swapping the statistic under a label the page
# defines would be worse than the small inconsistency.
#
# VAULT SIZE is refreshed too (operator-directed 2026-08-10 — it was initially
# and wrongly treated as un-derivable human intel). It is measured on the rig
# with `du -k` per vault dir, BusyBox-safe (law #5: no `du -h`), into
# state/meta/vault_sizes.tsv. It matters that it stays current: the vault only
# grows, the page's footer sells it as the saturation proxy readers should
# judge stutter by, and the 2026-08-07 figures had already drifted far —
# NPEA00050 read 235 MB against a live 423 MB, Tekken Tag 73 MB against 135 MB.
# A stale saturation proxy tells a reader a title is less warmed-up than it is.
# Refresh it with:
#   ssh <rig> 'for d in $ETK_ROOT/vault/$CHIPSET/*/; do id=$(basename "$d");
#     kb=$(du -k "$d" | tail -1 | awk "{print \$1}");
#     n=$(find "$d" -type f | wc -l); printf "%s\t%s\t%s\n" "$id" "$kb" "$n";
#   done' > state/meta/vault_sizes.tsv
# ==========================================================================
STATUS_ORDER = ["playable", "in-game", "menus", "regression", "none"]


def _fmt_vault(kb):
    """Match the page's existing unit style. Rounded to whole MB under a GB and
    one decimal above, because a shader vault that crosses a gigabyte is a fact
    worth reading precisely — that is the boundary where Mesa's own 1 GB LRU
    cap used to silently evict the oldest entries."""
    mb = kb / 1024.0
    return f"{mb/1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def update_wiki(path, rows_by_id, names, cfg_ids, vaults=None):
    import re as _re
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    head_i = next(i for i, l in enumerate(lines) if l.startswith("| ID |"))
    sep_i = head_i + 1
    end_i = sep_i + 1
    while end_i < len(lines) and lines[end_i].startswith("|"):
        end_i += 1

    parsed = []
    for l in lines[sep_i + 1:end_i]:
        c = [x.strip() for x in l.split("|")[1:-1]]
        if len(c) < 8:
            continue
        sid = c[0].strip("`")
        # Strip any existing link so the title can be re-linked cleanly; keep
        # trailing annotations the operator added after it (e.g. "PKG").
        m = _re.match(r"\[([^\]]+)\]\([^)]*\)(.*)$", c[1])
        title, suffix = (m.group(1), m.group(2)) if m else (c[1], "")
        parsed.append(dict(sid=sid, title=title, suffix=suffix, status=c[2],
                           fps=c[3], n=c[4], audio=c[5], notes=c[6], vault=c[7]))

    def rank(r):
        st = r["status"].strip("`")
        return (STATUS_ORDER.index(st) if st in STATUS_ORDER else len(STATUS_ORDER),
                -rows_by_id.get(r["sid"], (0, None))[0])

    out, nvault = [], 0
    for r in sorted(parsed, key=rank):
        n, fps = rows_by_id.get(r["sid"], (None, None))
        cell = (f"[{r['title']}]({BLOB}/config_{r['sid']}.yml){r['suffix']}"
                if r["sid"] in cfg_ids else f"{r['title']}{r['suffix']}")
        vkb = (vaults or {}).get(r["sid"])
        if vkb:
            vault_cell = _fmt_vault(vkb)
            nvault += 1
        else:
            vault_cell = r["vault"]
        out.append("| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            r["sid"], cell, r["status"],
            f"{fps:.1f}" if fps else r["fps"],
            n if n is not None else r["n"],
            r["audio"], r["notes"], vault_cell))

    new = lines[:sep_i + 1] + out + lines[end_i:]
    return ("\n".join(new), len(out),
            sum(1 for r in parsed if r["sid"] in cfg_ids), nvault)


if __name__ == "__main__":
    main()
