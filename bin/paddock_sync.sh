#!/bin/bash
# ==========================================================
# PRIVATE PADDOCK SYNC ENGINE (0.3.0) — rig-side
# ==========================================================
# Push/pull YOUR vaults, tunes, and saves against YOUR private GitHub
# repo. ETK never shares these bytes: the repo is the user's own,
# credential lives at /storage/roms/etk/config/paddock.json (chmod 600,
# written by install.sh's PADDOCK LINK step; absent = feature off).
#
# Commands:
#   status            TSV rows for the Pitstop PADDOCK tab:
#                     GAME_ID<TAB>local_n<TAB>local_kb<TAB>remote_kb<TAB>epoch<TAB>STATE
#   push <GAME_ID>    bundle vault+config+saves -> epoch release asset
#   pull <GAME_ID>    download, sha256-verify, homologation-gate, inject
#                     (--force overrides the mesa_hash gate: config still
#                      installs, shaders skipped — mirrors install-protune)
#
# API loop validated end-to-end by tools/paddock_probe.sh (10/10 2026-06-12).
# GEMINI IMMUTABLE RULE: token must NEVER appear in argv or logs — it
# travels only via the header file in /dev/shm (umask 077).
# ==========================================================
# env.sh is not `set -u`-clean — source it FIRST, tighten after.
source /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null || true
set -u

API="https://api.github.com"
CHIPSET="${PROFILE_SOC:-SM8250}"
CRED="/storage/roms/etk/config/paddock.json"
VAULT_BASE="/storage/roms/etk/vault/$CHIPSET"
CFG_DIR="/storage/roms/bios/rpcs3/custom_configs"
SAVES="/storage/roms/bios/rpcs3/dev_hdd0/home/00000001/savedata"
WORK="/tmp/paddock_sync.$$"

die() { echo "ERROR: $1" >&2; rm -rf "$WORK"; exit 1; }

[ -f "$CRED" ] || die "paddock not configured (no $CRED)"
REPO=$(jq -r .repo "$CRED"); TOKEN=$(jq -r .token "$CRED")
[ -n "$REPO" ] && [ "$REPO" != "null" ] || die "bad credential file"

mkdir -p "$WORK"; umask 077
HDR="/dev/shm/paddock_hdr.$$"
printf 'Authorization: Bearer %s\n' "$TOKEN" > "$HDR"
trap 'rm -rf "$WORK" "$HDR"' EXIT
gh_api() { curl -fsS -H "@$HDR" -H "Accept: application/vnd.github+json" "$@"; }

# --- epoch: read the driver library itself (no log dependency) ---
MESA_VER=$(strings /usr/lib/libvulkan_freedreno.so | grep -m1 -oE "Mesa [0-9]+\.[0-9]+\.[0-9]+" | awk '{print $2}')
MESA_HASH=$(head -c 65536 /usr/lib/libvulkan_freedreno.so | sha256sum | cut -d' ' -f1)
TAG="vault-${CHIPSET}-turnip${MESA_VER}"

ensure_release() {
    REL=$(gh_api "$API/repos/$REPO/releases/tags/$TAG" 2>/dev/null | jq -r .id)
    if [ -z "$REL" ] || [ "$REL" = "null" ]; then
        REL=$(curl -fsS -X POST -H "@$HDR" -H "Accept: application/vnd.github+json" \
            -d "{\"tag_name\":\"$TAG\",\"name\":\"$CHIPSET vault - Turnip $MESA_VER (private paddock)\",\"prerelease\":true}" \
            "$API/repos/$REPO/releases" | jq -r .id)
    fi
    [ -n "$REL" ] && [ "$REL" != "null" ] || die "cannot ensure release $TAG (empty repo? token scope?)"
}

_fetch_names() {
    # paddock_names.json: a tiny {GAME_ID: "Display Name"} asset maintained on
    # push, so REMOTE-ONLY rows (e.g. a cold card after disaster) still show
    # titles. Searches all releases, newest first. Leaves $WORK/names.json.
    echo '{}' > "$WORK/names.json"
    local nid
    nid=$(jq -r '[.[] | .assets[] | select(.name=="paddock_names.json")][0].id // empty' "$WORK/releases.json" 2>/dev/null)
    if [ -n "$nid" ]; then
        curl -fsSL -H "@$HDR" -H "Accept: application/octet-stream" \
            -o "$WORK/names.json" "$API/repos/$REPO/releases/assets/$nid" 2>/dev/null \
            && [ -s "$WORK/names.json" ] || echo '{}' > "$WORK/names.json"
    fi
}

cmd_status() {
    # Remote: every release asset across all epochs, newest epoch wins per game
    gh_api "$API/repos/$REPO/releases?per_page=20" > "$WORK/releases.json" 2>/dev/null || echo '[]' > "$WORK/releases.json"
    jq -r '.[] | .tag_name as $t | .assets[] | select(.name|endswith(".tar.gz")) | [$t, .name, (.size|tostring)] | @tsv' \
        "$WORK/releases.json" > "$WORK/remote.tsv" 2>/dev/null || : > "$WORK/remote.tsv"
    _fetch_names
    # Local: vault dirs with content
    : > "$WORK/seen"
    for d in "$VAULT_BASE"/*/shaders; do
        [ -d "$d" ] || continue
        ID=$(basename "$(dirname "$d")")
        N=$(find "$d" -type f | wc -l)
        KB=$(du -sk "$d" | cut -f1)
        R_LINE=$(grep "	paddock_${ID}_${CHIPSET}.tar.gz	" "$WORK/remote.tsv" | head -1)
        if [ -n "$R_LINE" ]; then
            R_TAG=$(printf '%s' "$R_LINE" | cut -f1)
            R_KB=$(( $(printf '%s' "$R_LINE" | cut -f3) / 1024 ))
            if [ "$R_TAG" = "$TAG" ]; then STATE="BOTH"; else STATE="EPOCH-OLD"; fi
        else
            R_TAG="-"; R_KB=0; STATE="LOCAL-ONLY"
        fi
        [ "$N" -eq 0 ] && [ "$R_KB" -gt 0 ] && STATE="REMOTE-ONLY"
        RNAME=$(jq -r --arg id "$ID" '.[$id] // empty' "$WORK/names.json" 2>/dev/null)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ID" "$N" "$KB" "$R_KB" "$R_TAG" "$STATE" "$RNAME"
        echo "$ID" >> "$WORK/seen"
    done
    # Remote-only games (no local vault dir at all)
    while IFS=$'\t' read -r r_tag r_name r_size; do
        ID=$(printf '%s' "$r_name" | sed -n "s/^paddock_\(.*\)_${CHIPSET}\.tar\.gz$/\1/p")
        [ -n "$ID" ] || continue
        grep -qx "$ID" "$WORK/seen" 2>/dev/null && continue
        ST="REMOTE-ONLY"; [ "$r_tag" != "$TAG" ] && ST="EPOCH-OLD"
        RNAME=$(jq -r --arg id "$ID" '.[$id] // empty' "$WORK/names.json" 2>/dev/null)
        printf '%s\t0\t0\t%s\t%s\t%s\t%s\n' "$ID" "$(( r_size / 1024 ))" "$r_tag" "$ST" "$RNAME"
        echo "$ID" >> "$WORK/seen"
    done < "$WORK/remote.tsv"
}

cmd_push() {
    GAME_ID="$1"
    GAME_NAME="${2:-}"   # optional display name from the caller (Pitstop)
    [ -d "$VAULT_BASE/$GAME_ID/shaders" ] || die "no local vault for $GAME_ID"
    # Trim hygiene: warn when the vault still carries pre-driver-bump
    # orphans (vault_sweep.sh removes them) so dead-epoch shaders don't
    # get banked under a live epoch tag.
    MESA_FP="${ETK_ROOT:-/storage/roms/etk}/vault/.last_mesa.hash"
    if [ -f "$MESA_FP" ]; then
        STALE=$(find "$VAULT_BASE/$GAME_ID/shaders" -mindepth 2 -maxdepth 2 \
            -type f ! -newer "$MESA_FP" 2>/dev/null | wc -l)
        [ "$STALE" -gt 0 ] && echo "WARNING: $STALE stale pre-bump shaders in this vault — run tools/vault_sweep.sh --apply first for a trim bundle"
    fi
    ensure_release
    B="$WORK/stage"; mkdir -p "$B/shaders" "$B/savedata"
    cp -a "$VAULT_BASE/$GAME_ID/shaders/." "$B/shaders/" 2>/dev/null
    [ -f "$CFG_DIR/config_${GAME_ID}.yml" ] && cp "$CFG_DIR/config_${GAME_ID}.yml" "$B/"
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
    SIZE=$(stat -c %s "$WORK/$ASSET")
    [ "$SIZE" -lt 1900000000 ] || die "bundle exceeds GitHub 2GB asset cap"
    SHA=$(sha256sum "$WORK/$ASSET" | cut -d' ' -f1)
    echo "$SHA  $ASSET" > "$WORK/$ASSET.sha256"
    # last-write-wins: drop same-name assets in this epoch first
    gh_api "$API/repos/$REPO/releases/$REL/assets" | \
        jq -r ".[] | select(.name==\"$ASSET\" or .name==\"$ASSET.sha256\") | .id" | \
        while read -r aid; do curl -fsS -X DELETE -H "@$HDR" "$API/repos/$REPO/releases/assets/$aid" >/dev/null; done
    curl -fsS -X POST -H "@$HDR" -H "Content-Type: application/gzip" \
        --data-binary "@$WORK/$ASSET" \
        "https://uploads.github.com/repos/$REPO/releases/$REL/assets?name=$ASSET" >/dev/null || die "upload failed"
    curl -fsS -X POST -H "@$HDR" -H "Content-Type: text/plain" \
        --data-binary "@$WORK/$ASSET.sha256" \
        "https://uploads.github.com/repos/$REPO/releases/$REL/assets?name=$ASSET.sha256" >/dev/null || die "sidecar upload failed"
    # Maintain paddock_names.json so cold rigs show titles, not IDs
    if [ -n "$GAME_NAME" ] && [ "$GAME_NAME" != "$GAME_ID" ]; then
        gh_api "$API/repos/$REPO/releases?per_page=20" > "$WORK/releases.json" 2>/dev/null || echo '[]' > "$WORK/releases.json"
        _fetch_names
        jq --arg id "$GAME_ID" --arg nm "$GAME_NAME" '. + {($id): $nm}' \
            "$WORK/names.json" > "$WORK/names.new" 2>/dev/null \
            || printf '{"%s":"%s"}\n' "$GAME_ID" "$GAME_NAME" > "$WORK/names.new"
        jq -r '[.[] | .assets[] | select(.name=="paddock_names.json") | .id] | .[]' "$WORK/releases.json" 2>/dev/null | \
            while read -r nid; do curl -fsS -X DELETE -H "@$HDR" "$API/repos/$REPO/releases/assets/$nid" >/dev/null; done
        curl -fsS -X POST -H "@$HDR" -H "Content-Type: application/json" \
            --data-binary "@$WORK/names.new" \
            "https://uploads.github.com/repos/$REPO/releases/$REL/assets?name=paddock_names.json" >/dev/null 2>&1 || true
    fi
    echo "PUSHED $GAME_ID: $SH_COUNT shaders, $((SIZE/1024)) KB -> $REPO @ $TAG"
}

cmd_pull() {
    GAME_ID="$1"; FORCE="${2:-}"
    ensure_release
    ASSET="paddock_${GAME_ID}_${CHIPSET}.tar.gz"
    AID=$(gh_api "$API/repos/$REPO/releases/$REL/assets" | jq -r ".[] | select(.name==\"$ASSET\") | .id")
    [ -n "$AID" ] && [ "$AID" != "null" ] || die "no $ASSET in epoch $TAG (older epoch? push from a rig on this driver first)"
    WANT_SHA=$(gh_api "$API/repos/$REPO/releases/$REL/assets" | jq -r ".[] | select(.name==\"$ASSET.sha256\") | .id")
    curl -fsSL -H "@$HDR" -H "Accept: application/octet-stream" -o "$WORK/pull.tar.gz" "$API/repos/$REPO/releases/assets/$AID" || die "download failed"
    if [ -n "$WANT_SHA" ] && [ "$WANT_SHA" != "null" ]; then
        curl -fsSL -H "@$HDR" -H "Accept: application/octet-stream" -o "$WORK/pull.sha" "$API/repos/$REPO/releases/assets/$WANT_SHA"
        GOT=$(sha256sum "$WORK/pull.tar.gz" | cut -d' ' -f1)
        EXP=$(cut -d' ' -f1 "$WORK/pull.sha")
        [ "$GOT" = "$EXP" ] || die "sha256 mismatch — refusing to inject"
    fi
    X="$WORK/x"; mkdir -p "$X"; tar -xzf "$WORK/pull.tar.gz" -C "$X" || die "extract failed"
    B_HASH=$(jq -r .mesa_hash "$X/manifest.json" 2>/dev/null)
    INJECT_SHADERS=1
    if [ "$B_HASH" != "$MESA_HASH" ]; then
        if [ "$FORCE" = "--force" ]; then INJECT_SHADERS=0
        else die "homologation: bundle driver != live driver (use --force for config-only)"; fi
    fi
    # config (with .bak, mirroring install-protune)
    if [ -f "$X/config_${GAME_ID}.yml" ]; then
        [ -f "$CFG_DIR/config_${GAME_ID}.yml" ] && cp "$CFG_DIR/config_${GAME_ID}.yml" "$CFG_DIR/config_${GAME_ID}.yml.paddock.bak"
        cp "$X/config_${GAME_ID}.yml" "$CFG_DIR/"
    fi
    # shaders: content-addressed merge, never clobber.
    # NOT cp -rn: BusyBox cp -rn with a `src/.` source is a SILENT NO-OP
    # (rc=0, zero files — discovered live 2026-06-12). tar -k is the
    # BusyBox-native no-clobber merge.
    if [ "$INJECT_SHADERS" = "1" ] && [ -d "$X/shaders" ]; then
        mkdir -p "$VAULT_BASE/$GAME_ID/shaders"
        (cd "$X/shaders" && tar -cf - .) | (cd "$VAULT_BASE/$GAME_ID/shaders" && tar -xkf -) 2>/dev/null
    fi
    # saves: no-clobber merge (existing local progress always wins)
    if [ -d "$X/savedata" ]; then
        for d in "$X/savedata"/*; do
            [ -d "$d" ] || continue
            T="$SAVES/$(basename "$d")"
            [ -d "$T" ] || cp -a "$d" "$T"
        done
    fi
    N=$(find "$VAULT_BASE/$GAME_ID/shaders" -type f 2>/dev/null | wc -l)
    echo "PULLED $GAME_ID: vault now $N shaders$([ "$INJECT_SHADERS" = "0" ] && echo ' (config-only: driver mismatch)')"
}

case "${1:-}" in
    status) cmd_status ;;
    push)   [ -n "${2:-}" ] || die "push needs GAME_ID"; cmd_push "$2" "${3:-}" ;;
    pull)   [ -n "${2:-}" ] || die "pull needs GAME_ID"; cmd_pull "$2" "${3:-}" ;;
    *)      echo "usage: paddock_sync.sh status | push <GAME_ID> [NAME] | pull <GAME_ID> [--force]"; exit 1 ;;
esac
