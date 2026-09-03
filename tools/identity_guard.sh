#!/usr/bin/env bash
# ==========================================================
# identity_guard.sh — the ONLY GitHub login allowed to touch this repo
# ==========================================================
# Public ETK artifacts ship under ONE pseudonym (TRACK_MANUAL §1.7). The gh
# CLI can hold more than one login and serves git-credential from whichever is
# ACTIVE — so a `gh auth switch` made anywhere else silently re-identifies
# every later push here. On 2026-09-03 a push to origin was refused (403) under the
# wrong account; it was caught only because that account lacked access. This
# guard makes the refusal deterministic and local instead of remote and lucky.
#
# Usage:  tools/identity_guard.sh          exit 0 iff the active gh login is allowed
#         installed as .git/hooks/pre-push by tools/identity_guard.sh --install
# release_sanity.sh runs it as a gate.
set -u
ALLOWED="${ETK_GH_LOGIN:-mercurious}"
if [ "${1:-}" = "--install" ]; then
    root=$(git rev-parse --show-toplevel) || exit 1
    hook="$root/.git/hooks/pre-push"
    printf '#!/bin/sh\nexec "%s/tools/identity_guard.sh"\n' "$root" > "$hook" && chmod +x "$hook"
    echo "installed $hook"; exit 0
fi
command -v gh >/dev/null 2>&1 || { echo "identity_guard: gh not found — cannot verify login; refusing" >&2; exit 1; }
login=$(gh api user --jq .login 2>/dev/null)
if [ "$login" = "$ALLOWED" ]; then
    echo "identity_guard: active GitHub login is $login — OK"; exit 0
fi
echo "identity_guard: REFUSED — active GitHub login is '${login:-none}', this repo is $ALLOWED-only." >&2
echo "                gh auth switch --user $ALLOWED   (then retry)" >&2
exit 1
