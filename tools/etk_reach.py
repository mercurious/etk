#!/usr/bin/env python3
"""ETK Reach — release-asset download tracker (host-side, read-only).

The ledger measures the rig. This measures the *audience*: how many people
actually took a build. GitHub only exposes a running total per asset, with no
history and no time series — so a single `gh release view` tells you a number
but never a trend. This snapshots the counters and diffs them against the last
snapshot, which is the part GitHub throws away.

The headline metric is the **install image** (`*.img.gz`): a download there is
someone flashing a whole ROCKNIX-GTK rig, i.e. a new user. Component assets
(the RPCS3 AppImage, a Turnip `.so`, a kernel) are mostly *existing* users
updating one layer, so they're counted separately rather than pooled — a jump
in components is retention, a jump in images is growth.

Usage:
  tools/etk_reach.py                 # snapshot + report deltas since last run
  tools/etk_reach.py --no-save       # report only, don't append to history
  tools/etk_reach.py --history       # print the image-download time series
  tools/etk_reach.py --repo owner/x  # default: mercurious/etk

Auth comes from the `gh` CLI keyring — no token is read from etk.conf or the
environment, so this never touches PADDOCK_TOKEN.
History: state/downloads.tsv (gitignored, same precedent as the vault).
"""
import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_DEFAULT = "mercurious/etk"
HIST = Path(__file__).resolve().parent.parent / "state" / "downloads.tsv"
IMAGE_SUFFIXES = (".img.gz", ".img.xz", ".img")


def gh_releases(repo):
    """All releases with their assets, via the authenticated gh CLI."""
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/releases?per_page=100", "--paginate"],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        sys.exit("gh CLI not found — install it, or run `gh auth login`.")
    if out.returncode != 0:
        sys.exit(f"gh api failed: {out.stderr.strip()[:200]}")
    # --paginate can concatenate multiple JSON arrays; normalise to one list.
    txt = out.stdout.strip().replace("][", ",")
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        sys.exit(f"could not parse gh output: {e}")


def is_image(name):
    return name.endswith(IMAGE_SUFFIXES)


def load_history():
    """Previous snapshots as {(tag, asset): [(ts, count), ...]}."""
    hist = defaultdict(list)
    if not HIST.exists():
        return hist
    for line in HIST.read_text().splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 4:
            continue
        try:
            hist[(f[1], f[2])].append((int(f[0]), int(f[3])))
        except ValueError:
            continue
    return hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    hist = load_history()

    if args.history:
        rows = [(k, v) for k, v in hist.items() if is_image(k[1])]
        if not rows:
            sys.exit("no image history yet — run without --history first.")
        for (tag, asset), pts in sorted(rows):
            print(f"\n{tag}  {asset}")
            for ts, c in pts:
                print(f"  {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}  {c}")
        return

    now = int(time.time())
    rels = gh_releases(args.repo)
    if not rels:
        sys.exit(f"no releases on {args.repo}")

    def last_count(tag, asset):
        pts = hist.get((tag, asset))
        return pts[-1][1] if pts else None

    img_tot = comp_tot = img_new = comp_new = 0
    lines = []
    for r in rels:
        tag = r["tag_name"]
        assets = r.get("assets", [])
        if not assets:
            continue
        body = []
        for a in sorted(assets, key=lambda x: (not is_image(x["name"]), -x["download_count"])):
            n, c = a["name"], a["download_count"]
            prev = last_count(tag, n)
            delta = "" if prev is None else (f"  (+{c - prev})" if c > prev else "")
            if is_image(n):
                img_tot += c
                img_new += 0 if prev is None else max(0, c - prev)
                body.append(f"    IMAGE   {c:>5}{delta}  {n}")
            else:
                comp_tot += c
                comp_new += 0 if prev is None else max(0, c - prev)
                body.append(f"            {c:>5}{delta}  {n}")
        lines.append(f"  {tag:<10} {r['published_at'][:10]}\n" + "\n".join(body))

    prior = max((p[-1][0] for p in hist.values() if p), default=None)
    print(f"ETK REACH — {args.repo}")
    if prior:
        age = (now - prior) / 3600.0
        print(f"deltas vs snapshot {time.strftime('%Y-%m-%d %H:%M', time.localtime(prior))} "
              f"({age:.1f}h ago)")
    else:
        print("first snapshot — no deltas yet; run again later to see movement")
    print()
    print("\n".join(lines))
    print()
    print(f"  INSTALL IMAGES : {img_tot:>5} total" + (f"   +{img_new} since last snapshot" if prior else ""))
    print(f"  components     : {comp_tot:>5} total" + (f"   +{comp_new} since last snapshot" if prior else ""))
    print("\n  Images = new rigs flashed. Components = existing users updating a layer.")

    if not args.no_save:
        HIST.parent.mkdir(parents=True, exist_ok=True)
        new = not HIST.exists()
        with HIST.open("a") as fh:
            if new:
                fh.write("epoch\ttag\tasset\tdownload_count\tpublished\n")
            for r in rels:
                for a in r.get("assets", []):
                    fh.write(f"{now}\t{r['tag_name']}\t{a['name']}\t"
                             f"{a['download_count']}\t{r['published_at'][:10]}\n")
        print(f"\n  snapshot appended -> {HIST}")


if __name__ == "__main__":
    main()
