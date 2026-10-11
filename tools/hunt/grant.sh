#!/bin/bash
# grant.sh -- the operator's signature on a hunt grant (docs/AUTONOMY_SPEC.md §2).
# Run it as yourself at a terminal, NOT under sudo: it probes read-only as you, prints the
# envelope, asks you to type the grant id, then sudo writes /etc/etk/grants/hunt.json.
# Root never runs repo code; the sudo password is the signature.
#   tools/hunt/grant.sh issue --game BCUS98296 --hours 10 [--lanes rpcs3,turnip] [--supervised]
#   tools/hunt/grant.sh show | revoke
if [ "$(id -u)" = 0 ]; then
    echo "grant.sh: run it as yourself, not under sudo -- it asks for sudo only to sign" >&2
    exit 1
fi
exec python3 -I "$(dirname "$(readlink -f "$0")")/grantctl.py" "$@"
