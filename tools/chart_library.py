#!/usr/bin/env python3
"""ETK chart: the library outgrew the GT series.

    python3 tools/chart_library.py [--ledger PATH] [--out docs/charts/library.png]

Renders `docs/charts/library.png` — distinct PS3 titles raced per week, with
the share of sessions outside the GT family overlaid. This is the trend behind
the 0.8.5 chord changes: while the kit was a Gran Turismo rig, an ETK chord
sitting on a bare shoulder button cost nothing, because the GT titles do not
bind it. As the library widened, the same chords started taking controls other
games actually use.

WHY THE GENERATOR IS COMMITTED. The three charts already in docs/charts/ were
rendered by scripts that live in no repository — the same defect the 2026-08-05
build-fleet audit found in four build lanes (`AI_MANIFEST` / manual §8.5: a
recipe for a shipped artifact that exists on exactly one laptop). A README
chart IS a shipped artifact. This one can be re-run by anyone with the ledger.

LEDGER METHOD (manual §6.4) is applied here as everywhere: ABORTED rows and
sub-60 s runs are dropped, and rows whose duration is physically impossible
are quarantined and REPORTED, never silently included — one PANIC row in the
2026-08 ledger carries duration_s=1251432772 (39.7 years) from an unguarded
session anchor, and it is enough on its own to make any rate computed over the
ledger meaningless.

matplotlib is a host-side dev dependency and is deliberately not vendored:
    python3 -m venv /tmp/chartenv && /tmp/chartenv/bin/pip install matplotlib
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

COLS = dict(epoch=0, dur=1, game=3, status=4)
MAX_PLAUSIBLE_S = 86400        # a session longer than a day is a broken anchor
MIN_SESSION_S = 60             # matches TELEMETRY_MIN_SESSION_S

# The Gran Turismo family the kit was built for — every other serial is a
# title the kit was never specialised for and now has to behave in front of.
GT_FAMILY = {
    "NPEA00050",  # GT5P Spec III (EU)
    "NPUA80075",  # GT5P (US)
    "NPEA00502",  # GT5P Spec II
    "NPEA90002",  # GT HD Concept
    "BCUS98158",  # GT5P disc
    "BCUS98114",  # GT5P disc (US)
    "NPUA80105",  # GT HD Concept (US)
}

# Release markers, drawn as the existing charts do.
MARKERS = [
    ("2026-07-07", "v0.7 anti-lock"),
    ("2026-07-22", "v0.8.0"),
    ("2026-08-01", "v0.8.3"),
    ("2026-08-07", "v0.8.4 forge"),
]

BG = "#fafafa"
BLUE = "#2a7ae2"
ORANGE = "#f4622d"
GRID = "#d8d8d8"


def load(path):
    rows, bad = [], []
    with open(path) as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or p[0] == "epoch":
                continue
            try:
                p[0] = int(p[0])
                dur = float(p[1])
            except ValueError:
                continue
            status = p[4].split(":")[0]
            if status == "ABORTED" or dur < MIN_SESSION_S:
                continue
            (bad if dur > MAX_PLAUSIBLE_S else rows).append(p)
    rows.sort(key=lambda r: r[0])
    return rows, bad


def week(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%G-W%V")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="state/etk_telemetry/sessions.tsv")
    ap.add_argument("--out", default="docs/charts/library.png")
    a = ap.parse_args()

    rows, bad = load(a.ledger)
    if not rows:
        sys.exit(f"no scorable rows in {a.ledger}")
    for r in bad:
        print(f"  quarantined: {datetime.fromtimestamp(r[0], timezone.utc):%Y-%m-%d} "
              f"{r[3]} duration_s={r[1]} (impossible — broken session anchor)")

    titles, sessions, nongt = defaultdict(set), defaultdict(int), defaultdict(int)
    for r in rows:
        w = week(r[0])
        titles[w].add(r[3])
        sessions[w] += 1
        if r[3] not in GT_FAMILY:
            nongt[w] += 1
    weeks = sorted(titles)
    n_titles = [len(titles[w]) for w in weeks]
    pct = [100.0 * nongt[w] / sessions[w] for w in weeks]

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib not installed — see the module docstring")

    fig, ax = plt.subplots(figsize=(14.7, 7.36), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    ax.bar(range(len(weeks)), n_titles, color=BLUE, width=0.62,
           label="distinct titles raced", zorder=3)
    ax.set_ylabel("distinct PS3 titles in the week", color="#444")
    ax.set_ylim(0, max(n_titles) * 1.22)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#bbb")
    ax.tick_params(colors="#444")

    ax2 = ax.twinx()
    ax2.plot(range(len(weeks)), pct, "o-", color=ORANGE, linewidth=2.2,
             markersize=7, label="share of sessions outside the GT family",
             zorder=4)
    ax2.set_ylabel("% of sessions outside the GT family", color=ORANGE)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors=ORANGE)
    for s in ("top", "left", "right"):
        ax2.spines[s].set_visible(False)

    # Week labels: show the Monday date, every other week to stay readable.
    def monday(w):
        return datetime.strptime(w + "-1", "%G-W%V-%u").strftime("%b %d")
    ax.set_xticks(range(len(weeks)))
    ax.set_xticklabels([monday(w) if i % 2 == 0 else "" for i, w in enumerate(weeks)])

    for datestr, label in MARKERS:
        d = datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        w = week(d.timestamp())
        if w in titles:
            x = weeks.index(w)
            ax.axvline(x - 0.42, color="#c9c9c9", linestyle="--", linewidth=1, zorder=1)
            ax.text(x - 0.36, ax.get_ylim()[1] * 0.985, label, rotation=90,
                    va="top", ha="left", fontsize=9, color="#777")

    peak_w, peak_n = weeks[-1], n_titles[-1]
    fig.text(0.063, 0.945, "The library outgrew the series the kit was built for",
             fontsize=18, fontweight="bold", color="#111")
    fig.text(0.063, 0.898,
             f"racing sessions ≥60 s, aborted runs excluded  ·  n={len(rows)}  ·  "
             f"week of {monday(peak_w)}: {peak_n} distinct titles, and "
             f"{pct[-1]:.0f}% of that week's sessions were outside Gran Turismo",
             fontsize=11, color="#666")

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False,
              bbox_to_anchor=(0.005, 0.94), fontsize=11)

    fig.subplots_adjust(left=0.063, right=0.937, top=0.83, bottom=0.09)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    fig.savefig(a.out, facecolor=BG)
    print(f"wrote {a.out}  ({len(weeks)} weeks, n={len(rows)})")


if __name__ == "__main__":
    main()
