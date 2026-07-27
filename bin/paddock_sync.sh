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

# Content fingerprint of a directory tree — used to tell "the save I already
# have IS the one in the bundle" from "they differ", so a restore only backs
# up and replaces when it would actually change something. Save dirs are a
# handful of small files, so hashing them is cheap.
dir_fingerprint() {
    ( cd "$1" 2>/dev/null || exit 0
      find . -type f | sort | while read -r f; do sha256sum "$f"; done \
        | sha256sum | cut -d' ' -f1 )
}

# merge_shaders <src> <dst>
# Content-addressed merge, plain overwrite. See the long note in cmd_pull for
# why NEITHER `cp -rn` NOR `tar -xkf` may be used here. Prints report lines;
# returns 1 if the destination ended up with fewer files than the source
# carried, which is the shape a cut-short merge takes.
merge_shaders() {
    _src="$1"; _dst="$2"; _err="${TMPDIR:-/tmp}/paddock_merge.$$.err"
    mkdir -p "$_dst"
    _before=$(find "$_dst" -type f 2>/dev/null | wc -l)
    _bundle=$(find "$_src" -type f 2>/dev/null | wc -l)
    (cd "$_src" && tar -cf - .) | (cd "$_dst" && tar -xf -) 2>"$_err"
    _after=$(find "$_dst" -type f 2>/dev/null | wc -l)
    [ -s "$_err" ] && echo "WARNING: shader merge reported: $(head -1 "$_err")"
    rm -f "$_err"
    echo "  shaders : +$((_after - _before)) new ($_bundle in bundle, $_after in vault)"
    if [ "$_after" -lt "$_bundle" ]; then
        echo "WARNING: shader merge INCOMPLETE — bundle had $_bundle, vault has $_after"
        return 1
    fi
    return 0
}

# restore_saves <src_savedata> <dst_savedata>
# Restore each save dir, backing up (never deleting) anything already there,
# and leaving byte-identical saves untouched. Prints one report line.
restore_saves() {
    _ssrc="$1"; _sdst="$2"
    _new=0; _repl=0; _same=0
    [ -d "$_ssrc" ] || { echo "  saves   : none in bundle"; return 0; }
    mkdir -p "$_sdst"
    _ts=$(date +%Y%m%d-%H%M%S 2>/dev/null || echo bak)
    for _d in "$_ssrc"/*; do
        [ -d "$_d" ] || continue
        _bn=$(basename "$_d")
        _t="$_sdst/$_bn"
        if [ ! -d "$_t" ]; then
            cp -a "$_d" "$_t" && _new=$((_new + 1))
        elif [ "$(dir_fingerprint "$_d")" = "$(dir_fingerprint "$_t")" ]; then
            _same=$((_same + 1))
        else
            mv "$_t" "$_sdst/$_bn.paddock.bak.$_ts" 2>/dev/null \
                && cp -a "$_d" "$_t" && _repl=$((_repl + 1))
        fi
    done
    echo "  saves   : $_new restored, $_repl replaced (old kept as .paddock.bak.*), $_same already current"
    return 0
}

# Tests source this file with PADDOCK_LIB=1 to exercise the merge/restore
# helpers above without a credential, a network round-trip, or a live rig.
# Everything below this line is the online engine.
[ "${PADDOCK_LIB:-0}" = "1" ] && return 0 2>/dev/null

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
        # Never bank a backup dir left behind by a restore (either installer's).
        case "$d" in *.protune.bak.*|*.paddock.bak.*) continue;; esac
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
    # shaders: content-addressed merge.
    #
    # DO NOT use `tar -xkf` here. BusyBox tar's -k does NOT mean "skip existing
    # files and carry on" — it ABORTS the whole extraction at the FIRST file
    # that already exists ("tar: can't open '...': File exists"). Proven on-rig
    # 2026-07-27: a 7,201-shader bundle pulled into a vault holding one colliding
    # file extracted 40 entries and stopped. That is exactly how a live GT HD
    # Concept pull silently landed 67 of 7,201 shaders — the vault already held
    # locally-compiled ones from the pad test minutes earlier, and 2>/dev/null
    # swallowed the error while the summary line reported the total as success.
    # (The previous idiom, `cp -rn src/. dst/`, was ALSO a BusyBox silent no-op;
    # this is the second trap on the same line. See TRACK_MANUAL BusyBox laws.)
    #
    # Plain overwrite is correct AND complete: these filenames are Mesa cache
    # keys, and the homologation gate above has already established that the
    # bundle was built on the byte-identical driver — so a name that exists on
    # both sides holds the same bytes, making "keep" and "overwrite" identical
    # in effect. Unlike -k, overwrite cannot abort the merge.
    SHADER_REPORT=""
    if [ "$INJECT_SHADERS" = "1" ] && [ -d "$X/shaders" ]; then
        SHADER_REPORT=$(merge_shaders "$X/shaders" "$VAULT_BASE/$GAME_ID/shaders")
    fi
    # saves: restore, backing up anything already there.
    #
    # This used to be a no-clobber skip ("existing local progress always wins"),
    # which is exactly backwards for a RESTORE. Launching the game even once —
    # which you must do to check the pad — makes RPCS3 write a fresh
    # <ID>-GAME- stub, and that brand-new stub then blocked the real backed-up
    # save from ever landing. Live 2026-07-27: the 10 REPLAY dirs restored
    # (absent locally) while the one save that mattered, the career GAME- dir,
    # was silently skipped. PULL is an explicit "make this rig match my
    # paddock" action, so it now behaves like the config restore directly
    # above: write it, but never destroy what was there — same
    # backup-then-write contract as pro-tuning/install-protune.sh.
    SAVE_REPORT=$(restore_saves "$X/savedata" "$SAVES")
    N=$(find "$VAULT_BASE/$GAME_ID/shaders" -type f 2>/dev/null | wc -l)
    echo "PULLED $GAME_ID: vault now $N shaders$([ "$INJECT_SHADERS" = "0" ] && echo ' (config-only: driver mismatch)')"
    [ -n "$SHADER_REPORT" ] && echo "$SHADER_REPORT"
    echo "  config  : $([ -f "$X/config_${GAME_ID}.yml" ] && echo 'restored (previous kept as .paddock.bak)' || echo 'none in bundle')"
    echo "$SAVE_REPORT"
}

case "${1:-}" in
    status) cmd_status ;;
    push)   [ -n "${2:-}" ] || die "push needs GAME_ID"; cmd_push "$2" "${3:-}" ;;
    pull)   [ -n "${2:-}" ] || die "pull needs GAME_ID"; cmd_pull "$2" "${3:-}" ;;
    *)      echo "usage: paddock_sync.sh status | push <GAME_ID> [NAME] | pull <GAME_ID> [--force]"; exit 1 ;;
esac
