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

PLAYABLE-ONLY BY DEFAULT (operator-directed 2026-08-10), and this is not
cosmetic. The ledger cannot see whether a session was a race or a main menu,
so an unplayable title charts exactly like a playable one — and a title that
never leaves its menus can look STABLE, because a menu does not stress the
GPU. Reading stability across all titles therefore implies a library you can
play, which is false. `config/game_status.tsv` carries the operator's own
playability call (mirrored from the wiki) and is the only source of that
intel. It also cleans up the exit-hang scan: of seven candidates found by
telemetry alone, FALLOUT 3 is `menus` and Need for Speed: Most Wanted is
`none` — their R3s are a human giving up on a title that never started, not
one refusing to close. Pass --status to widen, or --status any for everything.
"""
import argparse
import os
import statistics as st
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
    ap.add_argument("--status-file", default="config/game_status.tsv")
    ap.add_argument("--status", default="playable",
                    help="comma-separated statuses to chart, or 'any'")
    ap.add_argument("--out", default="docs/charts/titles.png")
    ap.add_argument("--top", type=int, default=22)
    a = ap.parse_args()

    status, wiki_name, flags = {}, {}, {}
    if os.path.exists(a.status_file):
        for line in open(a.status_file):
            if line.startswith("#") or not line.strip():
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2:
                status[p[0].strip()] = p[1].strip()
                if len(p) >= 3:
                    wiki_name[p[0].strip()] = p[2].strip()
                if len(p) >= 4 and p[3].strip():
                    flags[p[0].strip()] = set(f.strip() for f in p[3].split(","))
    elif a.status != "any":
        sys.exit(f"no {a.status_file} — playability is operator intel, not "
                 "derivable from the ledger; pass --status any to override")
    want = None if a.status == "any" else set(s.strip() for s in a.status.split(","))

    # The wiki's naming is the operator's own and wins over a launcher filename.
    names = {}
    if os.path.exists(a.names):
        for line in open(a.names):
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2:
                names.setdefault(p[0].strip(), p[1].strip())
    names.update(wiki_name)

    per = defaultdict(lambda: defaultdict(int))
    perf = defaultdict(lambda: defaultdict(list))
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
        # fps_med (col 17) and ft_jitter_ms (col 23) for the in-bar readout.
        for k, i in (("fps", 16), ("jit", 22)):
            if i < len(p) and p[i] not in ("", "-"):
                try:
                    v = float(p[i])
                except ValueError:
                    continue
                if v > 0:
                    perf[p[3]][k].append(v)

    # Drop what the operator has not called playable. Report the drop rather
    # than performing it silently — a title vanishing from a stability chart
    # should be a stated decision, not an absence nobody notices.
    if want is not None:
        dropped = sorted(((s, tot[s]) for s in tot if status.get(s) not in want),
                         key=lambda kv: -kv[1])
        for s, _ in dropped:
            del tot[s]
        if dropped:
            print(f"excluded {len(dropped)} title(s) not in status={a.status}:")
            for s, n in dropped[:12]:
                print(f"    {names.get(s, s):<42} {status.get(s, 'NO STATUS'):<11} N={n}")
    if not tot:
        sys.exit("nothing left to chart after the status filter")

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
    any_hatched = False
    for i, sid in enumerate(top):
        left = 0.0
        n = tot[sid]
        # A `hangs-on-quit` title gets its R3 segment STRIPED. Only that
        # segment: rows that also carried a GPU fault stay solid, so the
        # operator's intel annotates the exit hang without erasing the
        # evidence that something else went wrong during play.
        quit_hang = "hangs-on-quit" in flags.get(sid, ())
        for key, colour, _ in SEG:
            v = per[sid].get(key, 0)
            if not v:
                continue
            hatch = "///" if (quit_hang and key == "R3") else None
            if hatch:
                any_hatched = True
            ax.barh(i, 100.0 * v / n, left=left, color=colour, height=0.68,
                    hatch=hatch, edgecolor="#ffffff" if hatch else "none",
                    linewidth=0, zorder=3)
            left += 100.0 * v / n

        # fps / frame-time jitter, white and left-aligned INSIDE the bar.
        # Medians, per manual §6.4 — labelled `med` so it never reads as a
        # mean. Placed on the clean (blue) segment where there is one; when a
        # title has almost no clean share there is no dark ground to sit on,
        # so it moves outside in grey rather than becoming white-on-orange.
        fv, jv = perf[sid].get("fps"), perf[sid].get("jit")
        if fv:
            txt = f"{st.median(fv):.1f} fps"
            if jv:
                txt += f"  ±{st.median(jv):.1f} ms"
            clean_pct = 100.0 * per[sid].get("CLEAN", 0) / n
            if clean_pct >= 16:
                ax.text(1.4, i, txt, va="center", ha="left", fontsize=9,
                        color="#ffffff", fontweight="bold", zorder=5)
            else:
                ax.text(101.2, i, txt, va="center", ha="left", fontsize=9,
                        color="#555555", zorder=5)
        nm = names.get(sid, sid)
        if len(nm) > 34:
            nm = nm[:33] + "…"
        labels.append(f"{nm}  ({n})")

    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlim(0, 100)
    ax.margins(x=0)
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
             "PLAYABLE titles only (config/game_status.tsv)  ·  scored sessions in brackets  ·  "
             "aborted and sub-60 s runs excluded  ·  white figures are median fps and frame-time jitter",
             fontsize=11, color="#666")

    handles = [Patch(facecolor=c, label=l) for _, c, l in SEG]
    if any_hatched:
        handles.append(Patch(facecolor="#f4a03d", hatch="///", edgecolor="#ffffff",
                             label="hangs on QUIT — plays, will not exit"))
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.205),
              ncol=3 if any_hatched else 5, frameon=False, fontsize=10)

    fig.subplots_adjust(left=0.28, right=0.90, top=0.87, bottom=0.20)
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
