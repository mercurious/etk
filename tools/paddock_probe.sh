#!/bin/bash
# ==========================================================
# PADDOCK PROBE — disposable 0.3.0 validation harness (rig-side)
# ==========================================================
# Proves the full Private Paddock API loop from the rig with zero
# integration: detect epoch → ensure private repo + release → bundle a
# real game (vault + config + saves) → upload → download back → verify
# byte-identical. Run BEFORE any install.sh/Pitstop integration, per
# the validate-before-integrate house rule (cf. tools/boost_test.sh).
#
# Usage (token never persisted):
#   PADDOCK_TOKEN=<pat> bash paddock_probe.sh [GAME_ID]
# Defaults: GAME_ID=NPEA90002 (GT HD — small vault), repo <user>/etk-paddock.
# ==========================================================
set -u
GAME_ID="${1:-NPEA90002}"
API="https://api.github.com"
CHIPSET="SM8250"
VAULT_DIR="/storage/roms/etk/vault/$CHIPSET/$GAME_ID/shaders"
CFG="/storage/roms/bios/rpcs3/custom_configs/config_${GAME_ID}.yml"
SAVES="/storage/roms/bios/rpcs3/dev_hdd0/home/00000001/savedata"
WORK="/tmp/paddock_probe.$$"
PASS=0; FAIL=0
ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

[ -n "${PADDOCK_TOKEN:-}" ] || { echo "FAIL: PADDOCK_TOKEN not set"; exit 1; }
mkdir -p "$WORK"
# Token goes in a header file in tmpfs so it never appears in ps/argv.
HDR="$WORK/hdr"; umask 077
printf 'Authorization: Bearer %s\n' "$PADDOCK_TOKEN" > "$HDR"
gh_api() { curl -fsS -H "@$HDR" -H "Accept: application/vnd.github+json" "$@"; }

# --- 1. epoch detection (no log-grep: read the driver library itself) ---
MESA_VER=$(strings /usr/lib/libvulkan_freedreno.so | grep -m1 -oE "Mesa [0-9]+\.[0-9]+\.[0-9]+" | awk '{print $2}')
MESA_HASH=$(head -c 65536 /usr/lib/libvulkan_freedreno.so | sha256sum | cut -d' ' -f1)
TAG="vault-${CHIPSET}-turnip${MESA_VER}"
[ -n "$MESA_VER" ] && ok "epoch detect: $TAG (mesa_hash ${MESA_HASH:0:12}…)" || bad "mesa version detect"

# --- 2. identity + repo ensure ---
LOGIN=$(gh_api "$API/user" | jq -r .login)
[ -n "$LOGIN" ] && [ "$LOGIN" != "null" ] && ok "token identity: $LOGIN" || { bad "GET /user"; exit 1; }
REPO="${PADDOCK_REPO:-$LOGIN/etk-paddock}"
if gh_api "$API/repos/$REPO" -o "$WORK/repo.json" 2>/dev/null; then
  PRIV=$(jq -r .private "$WORK/repo.json")
  [ "$PRIV" = "true" ] && ok "repo exists + private: $REPO" || bad "repo $REPO is NOT private — refusing is correct behavior"
else
  CREATE=$(curl -fsS -X POST -H "@$HDR" -H "Accept: application/vnd.github+json" \
    -d "{\"name\":\"${REPO#*/}\",\"private\":true,\"description\":\"ETK Private Paddock — personal vault/save/config storage (not shared)\"}" \
    "$API/user/repos" 2>&1) && ok "repo auto-created private: $REPO" || bad "repo create (fine-grained token? create manually): $CREATE"
fi

# --- 2b. seed an initial commit if the repo is empty ---
# A release tag must point at a commit; a freshly auto-created repo has none.
# (Discovered live 2026-06-12: release create 422s on an empty repo.)
if ! gh_api "$API/repos/$REPO/contents/README.md" >/dev/null 2>&1; then
  SEED=$(printf '# ETK Private Paddock\n\nPersonal vault/save/config storage for [ETK](https://github.com/mercurious/etk). Private, not shared, managed by the ETK PADDOCK tab.\n' | base64 | tr -d '\n')
  curl -fsS -X PUT -H "@$HDR" -H "Accept: application/vnd.github+json" \
    -d "{\"message\":\"paddock: seed\",\"content\":\"$SEED\"}" \
    "$API/repos/$REPO/contents/README.md" >/dev/null \
    && ok "repo seeded (initial commit for release anchoring)" || bad "repo seed"
fi

# --- 3. ensure epoch release ---
REL=$(gh_api "$API/repos/$REPO/releases/tags/$TAG" 2>/dev/null | jq -r .id)
if [ -z "$REL" ] || [ "$REL" = "null" ]; then
  REL_RESP=$(curl -sS -X POST -H "@$HDR" -H "Accept: application/vnd.github+json" \
    -d "{\"tag_name\":\"$TAG\",\"name\":\"$CHIPSET vault · Turnip $MESA_VER (private paddock)\",\"prerelease\":true}" \
    "$API/repos/$REPO/releases")
  REL=$(printf '%s' "$REL_RESP" | jq -r .id)
  [ "$REL" = "null" ] && echo "  release POST response: $(printf '%s' "$REL_RESP" | jq -c '{message, errors}' 2>/dev/null)"
fi
[ -n "$REL" ] && [ "$REL" != "null" ] && ok "epoch release ready: $TAG (id $REL)" || { bad "release ensure"; exit 1; }

# --- 4. bundle (vault + config + saves, excluding .protune.bak.*) ---
B="$WORK/stage"; mkdir -p "$B/shaders" "$B/savedata"
cp -a "$VAULT_DIR/." "$B/shaders/" 2>/dev/null
[ -f "$CFG" ] && cp "$CFG" "$B/"
for d in "$SAVES/${GAME_ID}"*; do
  case "$d" in *.protune.bak.*) continue;; esac
  [ -d "$d" ] && cp -a "$d" "$B/savedata/"
done
SH_COUNT=$(find "$B/shaders" -type f | wc -l)
cat > "$B/manifest.json" <<MANIFEST
{ "private_paddock": 1, "game_id": "$GAME_ID", "chipset": "$CHIPSET",
  "turnip_version": "$MESA_VER", "mesa_hash": "$MESA_HASH",
  "shader_count": $SH_COUNT, "pushed_epoch": $(date +%s) }
MANIFEST
ASSET="paddock_${GAME_ID}_${CHIPSET}.tar.gz"
tar -czf "$WORK/$ASSET" -C "$B" .
SHA=$(sha256sum "$WORK/$ASSET" | cut -d' ' -f1)
SIZE=$(stat -c %s "$WORK/$ASSET")
[ "$SH_COUNT" -gt 0 ] && ok "bundle: $SH_COUNT shaders, $((SIZE/1024)) KB, sha ${SHA:0:12}…" || bad "bundle empty"

# --- 5. upload (last-write-wins: delete same-name assets first) ---
gh_api "$API/repos/$REPO/releases/$REL/assets" | jq -r ".[] | select(.name==\"$ASSET\" or .name==\"$ASSET.sha256\") | .id" | \
  while read -r aid; do curl -fsS -X DELETE -H "@$HDR" "$API/repos/$REPO/releases/assets/$aid" >/dev/null; done
echo "$SHA  $ASSET" > "$WORK/$ASSET.sha256"
UP1=$(curl -fsS -X POST -H "@$HDR" -H "Content-Type: application/gzip" \
  --data-binary "@$WORK/$ASSET" \
  "https://uploads.github.com/repos/$REPO/releases/$REL/assets?name=$ASSET" | jq -r .id)
UP2=$(curl -fsS -X POST -H "@$HDR" -H "Content-Type: text/plain" \
  --data-binary "@$WORK/$ASSET.sha256" \
  "https://uploads.github.com/repos/$REPO/releases/$REL/assets?name=$ASSET.sha256" | jq -r .id)
[ -n "$UP1" ] && [ "$UP1" != "null" ] && ok "upload bundle (asset id $UP1)" || bad "upload bundle"
[ -n "$UP2" ] && [ "$UP2" != "null" ] && ok "upload sha sidecar (asset id $UP2)" || bad "upload sidecar"

# --- 6. pull back + byte-verify (the part that proves restore works) ---
AID=$(gh_api "$API/repos/$REPO/releases/$REL/assets" | jq -r ".[] | select(.name==\"$ASSET\") | .id")
curl -fsSL -H "@$HDR" -H "Accept: application/octet-stream" \
  -o "$WORK/pulled.tar.gz" "$API/repos/$REPO/releases/assets/$AID"
PULL_SHA=$(sha256sum "$WORK/pulled.tar.gz" | cut -d' ' -f1)
[ "$PULL_SHA" = "$SHA" ] && ok "pull byte-identical (sha256 match)" || bad "pull sha mismatch: $PULL_SHA"
# homologation gate simulation: manifest mesa_hash vs live driver
P_HASH=$(tar -xzOf "$WORK/pulled.tar.gz" ./manifest.json | jq -r .mesa_hash)
[ "$P_HASH" = "$MESA_HASH" ] && ok "homologation gate: mesa_hash verifies" || bad "homologation mismatch"

echo "=========================================="
echo "PROBE RESULT: $PASS pass / $FAIL fail"
rm -rf "$WORK"
exit $FAIL
