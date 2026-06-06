#!/usr/bin/env bash
# ==========================================================
# ETK PRO TUNING — bundle builder + publisher  (PRODUCER / host-side)
# ==========================================================
# Packages a settled tune (config + clean-room shader vault) into a
# self-describing bundle, publishes the bytes to a GitHub Release,
# updates the curated index, and prints the one-liner to share.
#
# Runs on the HOST (your Mac), from the repo root, where ./config and
# ./vault live. Uses `gh` (already authed) for the Release upload.
#
#   ./pro-tuning/export.sh NPUA80075 --publish --write-index
#
# Status: SCAFFOLD v0.1.0 — fill the bundle from a CLEAN-ROOM vault only
#   (single-driver recompile, no nightly blend; ProTuningExportDossier §5.2).
# ==========================================================
set -euo pipefail

REPO="${PROTUNE_REPO:-mercurious/etk}"
CHIPSET="SM8250"; TURNIP="26.1.0"; ROCKNIX="20260601"
TUNER="dave"; NOTE="clean-room vault on final 2026 official"
RIG="${RIG:-SM8250.local}"; MESA_HASH=""; TAG=""
DO_VAULT=1; DO_PUBLISH=0; DO_WRITE_INDEX=0

GAME_ID="${1:-}"; shift || true
[ -n "$GAME_ID" ] || { echo "usage: export.sh <GAME_ID> [--chipset X] [--turnip V] [--rocknix B] [--tuner H] [--note '..'] [--tag T] [--mesa-hash H] [--rig host] [--no-vault] [--publish] [--write-index]"; exit 1; }
while [ $# -gt 0 ]; do case "$1" in
  --chipset) CHIPSET="$2"; shift 2;;  --turnip) TURNIP="$2"; shift 2;;
  --rocknix) ROCKNIX="$2"; shift 2;;  --tuner) TUNER="$2"; shift 2;;
  --note) NOTE="$2"; shift 2;;        --tag) TAG="$2"; shift 2;;
  --mesa-hash) MESA_HASH="$2"; shift 2;; --rig) RIG="$2"; shift 2;;
  --no-vault) DO_VAULT=0; shift;;     --publish) DO_PUBLISH=1; shift;;
  --write-index) DO_WRITE_INDEX=1; shift;;
  *) echo "unknown flag: $1" >&2; exit 1;;
esac; done

TAG="${TAG:-vault-${CHIPSET}-turnip${TURNIP}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_SRC="$ROOT/config/config_${GAME_ID}.yml"
VAULT_SRC="$ROOT/vault/${CHIPSET}/${GAME_ID}/shaders"
ASSET="protune_${GAME_ID}_${CHIPSET}.zip"
INDEX="$ROOT/vault-index/manifest.json"

[ -f "$CONFIG_SRC" ] || { echo "[FAIL] missing $CONFIG_SRC"; exit 1; }

# --- homologation hash: from flag, else probe the rig's driver
if [ -z "$MESA_HASH" ]; then
  echo ">>> probing $RIG for Mesa/Turnip driver hash..."
  MESA_HASH="$(ssh "root@$RIG" 'head -c 65536 /usr/lib/libvulkan_freedreno.so 2>/dev/null | sha256sum | cut -d" " -f1')" \
    || { echo "[FAIL] could not read driver hash from $RIG (pass --mesa-hash)"; exit 1; }
fi
[ -n "$MESA_HASH" ] || { echo "[FAIL] empty mesa_hash"; exit 1; }

# --- assemble the bundle ---
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
cp -a "$CONFIG_SRC" "$STAGE/config_${GAME_ID}.yml"
SHADER_COUNT=0; HAVE_VAULT=false
if [ "$DO_VAULT" -eq 1 ] && [ -d "$VAULT_SRC" ]; then
  mkdir -p "$STAGE/shaders"
  cp -a "$VAULT_SRC/." "$STAGE/shaders/"
  SHADER_COUNT=$(find "$STAGE/shaders" -type f | wc -l | tr -d ' ')
  HAVE_VAULT=true
  echo ">>> vault: $SHADER_COUNT shader files from $VAULT_SRC"
else
  echo ">>> NOTE: no vault bundled (config-only). Source: $VAULT_SRC"
fi

# --- per-bundle manifest.json (pro_tuning/1, self-describing) ---
python3 - "$STAGE/manifest.json" <<PY
import json,sys
m={"pro_tuning":1,
   "game":{"id":"$GAME_ID","name":""},
   "tuner":{"handle":"$TUNER","note":"$NOTE","harvest":{"sessions":0,"hours":0,"best_streak":0}},
   "homologation":{"chipset":"$CHIPSET","turnip_version":"$TURNIP","rocknix_build":"$ROCKNIX","mesa_hash":"$MESA_HASH"},
   "config":{"file":"config_${GAME_ID}.yml","dest":"custom_configs"},
   "vault":{"present":$HAVE_VAULT,"shader_count":$SHADER_COUNT,"clean_room":True,"dest":"mesa_shader_cache"},
   "rocknix_settings":{"cooling.profile":"aggressive","cpugovernor":"performance","gpuperf":"performance"}}
json.dump(m,open(sys.argv[1],"w"),indent=2)
PY

# --- zip + hash ---
OUT="$ROOT/pro-tuning/dist"; mkdir -p "$OUT"
( cd "$STAGE" && zip -q -r -X "$OUT/$ASSET" . )
SHA="$(shasum -a 256 "$OUT/$ASSET" | cut -d' ' -f1)"
SIZE_MB=$(( ($(wc -c < "$OUT/$ASSET") + 524288) / 1048576 ))
echo ">>> built $OUT/$ASSET  ($SIZE_MB MB)  sha256=$SHA"

URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"

# --- publish bytes to a GitHub Release ---
if [ "$DO_PUBLISH" -eq 1 ]; then
  gh release view "$TAG" >/dev/null 2>&1 || \
    gh release create "$TAG" --title "$CHIPSET vault · Turnip $TURNIP" \
      --notes "Clean-room Pro Tuning bundles for ROCKNIX $ROCKNIX (Turnip $TURNIP)."
  gh release upload "$TAG" "$OUT/$ASSET" --clobber
  echo ">>> published asset to release $TAG"
fi

# --- patch the curated index in place ---
if [ "$DO_WRITE_INDEX" -eq 1 ]; then
  python3 - "$INDEX" <<PY
import json,sys
p=sys.argv[1]; d=json.load(open(p))
e=next((t for t in d["tunes"] if t["game"]["id"]=="$GAME_ID" and t["chipset"]=="$CHIPSET"),None)
if e is None:
    e={"game":{"id":"$GAME_ID","name":""},"chipset":"$CHIPSET","tiers":{},"tuner":{}}
    d["tunes"].append(e)
e["homologation"]={"turnip_version":"$TURNIP","rocknix_build":"$ROCKNIX","mesa_hash":"$MESA_HASH"}
e["release_tag"]="$TAG"
e["bundle"]={"asset":"$ASSET","url":"$URL","sha256":"$SHA","size_mb":$SIZE_MB}
e["tiers"]={"config":True,"vault":$HAVE_VAULT,"savedata":False,"game_pkg":False}
e["vault"]={"shader_count":$SHADER_COUNT,"clean_room":True}
e.setdefault("tuner",{}).update({"handle":"$TUNER","note":"$NOTE"})
d["updated"]="$(date +%Y-%m-%d)"
json.dump(d,open(p,"w"),indent=2); open(p,"a").write("\n")
print(">>> index updated:",p)
PY
fi

# --- the paste-ready share line ---
echo
echo "  Paste this to your friend ↓"
echo "  ──────────────────────────────────────────────"
echo "  # $GAME_ID · $CHIPSET · Turnip $TURNIP · ${SIZE_MB} MB · tuned by $TUNER"
echo "  ssh root@${CHIPSET}.local 'curl -fsSL https://raw.githubusercontent.com/${REPO}/main/pro-tuning/install-protune.sh | sh -s -- ${GAME_ID}'"
echo
[ "$DO_PUBLISH" -eq 1 ] || echo "  (dry build — re-run with --publish --write-index to go live)"
