#!/usr/bin/env python3
"""ETK chart: how each title ends, not just whether it survived.

    python3 tools/chart_titles.py [--names state/meta/title_names.tsv]

Renders `docs/charts/titles.png` — one stacked bar per title, split by how its
sessions ACTUALLY ENDED. It exists because a single "clean %" column lies by
omission, and the operator caught it on a live title:

    SOULCALIBUR V plays fine. It hangs only when you try to quit the
    emulator. Every session therefore ends on an R3 recovery, so the ledger
    reads 10% clean and the game looks broken — when what it really needs is
    a note that says "you'll R3 to leave it".

The ledger row is CORRECT in every one of those cases: the operator pressed
R3, so RECOVERY:R3 is the truth. The deception is in the inference a reader
draws from a single aggregate. Splitting the bar puts the two failure modes
side by side, so "died during play" and "died on exit" stop looking identical:

  * a title whose losses are ALL R3 with no GPU fault and no panic is the
    exit-hang fingerprint — it played, then refused to quit
  * a title carrying Adreno faults or panics is failing DURING play

This chart cannot prove which is which on its own — R3 is a human pressing a
button, and the ledger does not record what they were trying to do at the
time. It narrows the candidates to the ones worth asking about, and that is
all it claims.
"""
import argparse
import os
import sys
from collections import defaultdict

MAX_PLAUSIBLE_S = 86400
MIN_SESSION_S = 60
BG = "#fafafa"
# clean -> absorbed -> exit-ish -> faulted -> panic, cool to hot.
SEG = [
    ("CLEAN",           "#2a7ae2", "finished cleanly"),
    ("SURVIVED",        "#7fb2ee", "hang absorbed by anti-lock"),
    ("R3",              "#f4a03d", "R3 recovery, no GPU fault"),
    ("RECOVERY_FAULT",  "#f4622d", "recovery with a GPU fault"),
    ("PANIC",           "#a02020", "kernel panic"),
]


def classify(status, sig):
    """Bucket a row. R3-without-a-fault is kept SEPARATE from R3-with-one:
    that distinction is the entire point of the chart."""
    head = status.split(":")[0]
    if head == "PANIC":
        return "PANIC"
    if head in ("CLEAN",):
        return "CLEAN"
    if head == "SURVIVED":
        return "SURVIVED"
    faulted = ("Adreno" in status or "00C5" in sig or "00E5" in sig
               or "VK_DEVICE_LOST" in sig or "FIFO" in sig)
    if "R3" in sig and not faulted:
        return "R3"
    return "RECOVERY_FAULT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="state/etk_telemetry/sessions.tsv")
    ap.add_argument("--names", default="state/meta/title_names.tsv")
    ap.add_argument("--out", default="docs/charts/titles.png")
    ap.add_argument("--top", type=int, default=22)
    a = ap.parse_args()

    names = {}
    if os.path.exists(a.names):
        for line in open(a.names):
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2:
                names.setdefault(p[0].strip(), p[1].strip())

    per = defaultdict(lambda: defaultdict(int))
    tot = defaultdict(int)
    for line in open(a.ledger):
        p = line.rstrip("\n").split("\t")
        if len(p) < 11 or p[0] == "epoch":
            continue
        try:
            dur = float(p[1])
        except ValueError:
            continue
        if p[4].split(":")[0] == "ABORTED" or dur < MIN_SESSION_S or dur > MAX_PLAUSIBLE_S:
            continue
        per[p[3]][classify(p[4], p[9] or "")] += 1
        tot[p[3]] += 1

    top = sorted(tot, key=lambda s: -tot[s])[:a.top]
    top.reverse()   # barh draws bottom-up

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except ImportError:
        sys.exit("matplotlib not installed (host-side dev dep) — see chart_library.py")

    fig, ax = plt.subplots(figsize=(14.7, 8.4), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    labels = []
    for i, sid in enumerate(top):
        left = 0.0
        n = tot[sid]
        for key, colour, _ in SEG:
            v = per[sid].get(key, 0)
            if v:
                ax.barh(i, 100.0 * v / n, left=left, color=colour, height=0.68, zorder=3)
                left += 100.0 * v / n
        nm = names.get(sid, sid)
        if len(nm) > 34:
            nm = nm[:33] + "…"
        labels.append(f"{nm}  ({n})")

    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of that title's scored sessions (%)", color="#444")
    ax.xaxis.grid(True, color="#d8d8d8", linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#bbb")
    ax.tick_params(colors="#444")

    fig.text(0.063, 0.955, "How each title ends — not just whether it survived",
             fontsize=18, fontweight="bold", color="#111")
    fig.text(0.063, 0.917,
             "scored sessions per title in brackets  ·  aborted runs and sub-60 s launches excluded  ·  "
             "an all-orange bar with no red is the 'plays fine, hangs on exit' fingerprint",
             fontsize=11, color="#666")

    ax.legend(handles=[Patch(facecolor=c, label=l) for _, c, l in SEG],
              loc="lower center", bbox_to_anchor=(0.5, -0.13), ncol=5,
              frameon=False, fontsize=10)

    fig.subplots_adjust(left=0.28, right=0.97, top=0.87, bottom=0.14)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    fig.savefig(a.out, facecolor=BG)
    print(f"wrote {a.out} ({len(top)} titles)")

    print("\nexit-hang candidates (all losses are R3, no GPU fault, no panic):")
    for sid in sorted(tot, key=lambda s: -tot[s]):
        d = per[sid]
        if tot[sid] >= 3 and d.get("R3", 0) and not d.get("RECOVERY_FAULT", 0) \
           and not d.get("PANIC", 0) and d.get("R3", 0) >= 0.5 * tot[sid]:
            print(f"  {names.get(sid, sid):<40} {sid}  "
                  f"{d.get('R3',0)}/{tot[sid]} sessions ended on R3")


if __name__ == "__main__":
    main()
