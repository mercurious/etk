#!/usr/bin/env bash
# ==========================================================
# ETK ONE-LINER BOOTSTRAP (macOS / Linux)
# ==========================================================
# Fetches the ETK repo as a tarball from GitHub (no git needed), lays it
# down at ~/etk, and hands off to the real installer. Run from any shell:
#
#   curl -fsSL https://raw.githubusercontent.com/mercurious/etk/main/get-etk.sh | bash
#
# Re-running updates the checkout in place: repo files are overwritten;
# machine-local files that are not in the archive (etk.conf, vault/,
# state/, drivers/, emulators/) are untouched. Safe to repeat — this is
# also the update path.
#
# Windows parity: windows_installer/get-etk.ps1 (irm | iex).
#
# Piped-into-bash safe: this script never reads stdin, and install.sh's
# interactive wizard + ssh pairing read from /dev/tty (by design), which
# we reattach explicitly at the handoff.
# ==========================================================
set -euo pipefail

REPO="mercurious/etk"
BRANCH="main"
DEST="$HOME/etk"
TARURL="https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo ""
echo "=========================================================="
echo "  ETK BOOTSTRAP  (fetch -> $DEST -> installer)"
echo "=========================================================="
echo ">>> Downloading $TARURL ..."
curl -fL --progress-bar "$TARURL" | tar -xz -C "$TMP"

SRC="$TMP/etk-$BRANCH"
if [ ! -f "$SRC/install.sh" ]; then
    echo "!!! Unexpected archive layout - install.sh not found. Aborting." >&2
    exit 1
fi

echo ">>> Installing repo files into $DEST ..."
mkdir -p "$DEST"
if command -v rsync >/dev/null 2>&1; then
    rsync -a "$SRC/" "$DEST/"
else
    cp -R "$SRC/." "$DEST/"
fi
cd "$DEST"
chmod +x install.sh uninstall.sh get-etk.sh 2>/dev/null || true

echo ">>> Handing off to the ETK installer (repo root: $DEST)"
# Reattach the terminal: under `curl | bash` stdin is the exhausted pipe,
# and the installer's wizard/pairing expect a controlling tty.
if [ -r /dev/tty ]; then
    exec ./install.sh </dev/tty
else
    echo ">>> No controlling terminal - running non-interactive (first-run"
    echo ">>> wizard will bail with instructions if etk.conf is missing)."
    exec ./install.sh
fi
