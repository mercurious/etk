#!/bin/sh
# ==========================================================
# ETK PRO TUNING — one-command installer  (CONSUMER / rig-side)
# ==========================================================
# A hardcore tuner did the work to make your rig run amazing.
# This one command installs their settled tune: the RPCS3 per-game
# config + the saturated clean-room shader vault, homologation-gated
# to your exact GPU driver build so the shaders actually load.
#
# RUNS ON THE RIG. Invoked by the shared one-liner, e.g.:
#   ssh root@SM8250.local \
#     'curl -fsSL https://raw.githubusercontent.com/mercurious/etk/main/pro-tuning/install-protune.sh \
#      | sh -s -- NPUA80075'
#
# INVARIANT (the trust contract): THIS script is the only code that
# runs. The bundle it pulls is PURE DATA (yml + shader blobs +
# manifest.json). Worst case from a bad/hostile bundle is a config
# file at a known path — never arbitrary code execution.
#
# Status: SCAFFOLD v0.1.0 — pending the payload-test gate
#   (dossiers/ShaderDistributionFusionDossier.md §4.1: prove a
#    clean-room vault reproduces on a second rig before public use).
# ==========================================================
set -eu

REPO="${PROTUNE_REPO:-mercurious/etk}"
BRANCH="${PROTUNE_BRANCH:-main}"
INDEX_URL="${PROTUNE_INDEX_URL:-https://raw.githubusercontent.com/$REPO/$BRANCH/vault-index/manifest.json}"

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
say()  { printf "%b\n" "$*"; }
fail() { printf "%b\n" "${R}[FAIL] $*${N}" >&2; exit 1; }

# ---------- args ----------
ALLOW_MISMATCH=0; WITH_SAVEDATA=0; GAME_ID=""; CHIPSET=""
for a in "$@"; do
  case "$a" in
    --allow-turnip-mismatch) ALLOW_MISMATCH=1 ;;
    --with-savedata)         WITH_SAVEDATA=1 ;;
    --chipset=*)             CHIPSET="${a#*=}" ;;
    --*)                     say "${Y}[WARN] unknown flag: $a${N}" ;;
    *)                       [ -z "$GAME_ID" ] && GAME_ID="$a" ;;
  esac
done
[ -n "$GAME_ID" ] || fail "usage: install-protune.sh <GAME_ID> [--chipset=SM8250] [--allow-turnip-mismatch] [--with-savedata]"

for t in curl jq unzip sha256sum head; do
  command -v "$t" >/dev/null 2>&1 || fail "missing required tool on rig: $t"
done

# Hostname is the SoC on these rigs (e.g. SM8250) unless overridden.
[ -n "$CHIPSET" ] || CHIPSET="$(hostname 2>/dev/null || echo SM8250)"

say "${C}>>> ETK Pro Tuning — installing $GAME_ID on $CHIPSET${N}"

# ---------- resolve the bundle from the curated index ----------
INDEX="$(curl -fsSL "$INDEX_URL")" || fail "cannot fetch index: $INDEX_URL"
ENTRY="$(printf '%s' "$INDEX" | jq -c --arg id "$GAME_ID" --arg chip "$CHIPSET" \
  '.tunes[] | select(.game.id==$id and .chipset==$chip)' 2>/dev/null)" || true
[ -n "${ENTRY:-}" ] || fail "no Pro Tuning published for $GAME_ID on $CHIPSET"

NAME=$(printf '%s' "$ENTRY"     | jq -r '.game.name')
URL=$(printf '%s' "$ENTRY"      | jq -r '.bundle.url')
WANT_SHA=$(printf '%s' "$ENTRY" | jq -r '.bundle.sha256')
WANT_MESA=$(printf '%s' "$ENTRY"| jq -r '.homologation.mesa_hash')
TURNIP=$(printf '%s' "$ENTRY"   | jq -r '.homologation.turnip_version')
TUNER=$(printf '%s' "$ENTRY"    | jq -r '.tuner.handle // "anon"')
say "    ${G}$NAME${N} · tuned by $TUNER · Turnip $TURNIP"

# ---------- HOMOLOGATION GATE ----------
# The shader vault is keyed to the exact Mesa/Turnip driver build. We
# compare the first 64 KB of libvulkan_freedreno.so (same primitive
# install.sh uses). Exact match => shaders are guaranteed loadable.
LOCAL_MESA="$(head -c 65536 /usr/lib/libvulkan_freedreno.so 2>/dev/null | sha256sum | cut -d' ' -f1)"
VAULT_OK=1
if [ "$WANT_MESA" = "PENDING_CLEAN_ROOM" ] || [ -z "$WANT_MESA" ] || [ "$WANT_MESA" = "null" ]; then
  say "${Y}[WARN] index has no homologation hash yet (pre-clean-room) — config only.${N}"; VAULT_OK=0
elif [ "$LOCAL_MESA" != "$WANT_MESA" ]; then
  if [ "$ALLOW_MISMATCH" -eq 1 ]; then
    say "${Y}[WARN] GPU driver MISMATCH — shaders may be dead weight. Forcing (override).${N}"
  else
    say "${Y}[GATE] Your GPU driver build differs from the tuner's:${N}"
    say "       yours: $LOCAL_MESA"
    say "       tune : $WANT_MESA  (Turnip $TURNIP)"
    say "       Installing CONFIG ONLY. Match the tuner's ROCKNIX build for shaders,"
    say "       or re-run with --allow-turnip-mismatch to force the shader install."
    VAULT_OK=0
  fi
else
  say "    ${G}homologation OK${N} — driver build matches"
fi

# ---------- download + verify (data only) ----------
TMP="$(mktemp -d /tmp/protune.XXXXXX)"; trap 'rm -rf "$TMP"' EXIT INT TERM
ZIP="$TMP/bundle.zip"
say "${C}>>> downloading bundle...${N}"
curl -fsSL -o "$ZIP" "$URL" || fail "download failed: $URL"
GOT_SHA="$(sha256sum "$ZIP" | cut -d' ' -f1)"
if [ "$WANT_SHA" != "PENDING_CLEAN_ROOM" ] && [ "$WANT_SHA" != "null" ] && [ -n "$WANT_SHA" ]; then
  [ "$GOT_SHA" = "$WANT_SHA" ] || fail "sha256 mismatch (got $GOT_SHA, want $WANT_SHA)"
  say "    ${G}sha256 verified${N}"
else
  say "${Y}[WARN] index carries no bundle sha256 yet — integrity check skipped (scaffold).${N}"
fi
unzip -q -o "$ZIP" -d "$TMP/bundle" || fail "unzip failed (corrupt bundle?)"

# ---------- inject: config ----------
CFG_DST=""
for d in /storage/.config/rpcs3/custom_configs /storage/games-internal/roms/bios/rpcs3/custom_configs; do
  [ -d "$d" ] && { CFG_DST="$d"; break; }
done
[ -n "$CFG_DST" ] || { CFG_DST=/storage/.config/rpcs3/custom_configs; mkdir -p "$CFG_DST"; }
CFG_SRC="$TMP/bundle/config_${GAME_ID}.yml"
if [ -f "$CFG_SRC" ]; then
  [ -f "$CFG_DST/config_${GAME_ID}.yml" ] && \
    cp -a "$CFG_DST/config_${GAME_ID}.yml" "$CFG_DST/config_${GAME_ID}.yml.protune.bak"
  cp -a "$CFG_SRC" "$CFG_DST/config_${GAME_ID}.yml"
  say "    ${G}config -> $CFG_DST${N}"
else
  say "${Y}[WARN] bundle missing config_${GAME_ID}.yml${N}"
fi

# ---------- inject: shaders (content-addressed merge, never destructive) ----------
if [ "$VAULT_OK" -eq 1 ] && [ -d "$TMP/bundle/shaders" ]; then
  MESA_CACHE=/storage/.cache/mesa_shader_cache
  mkdir -p "$MESA_CACHE"
  # -n: never clobber an existing blob (same hash name == same bytes).
  cp -rn "$TMP/bundle/shaders/." "$MESA_CACHE/" 2>/dev/null || \
    (cd "$TMP/bundle/shaders" && find . -type f -exec sh -c 'd="$1"; [ -e "/storage/.cache/mesa_shader_cache/$d" ] || { mkdir -p "/storage/.cache/mesa_shader_cache/$(dirname "$d")"; cp -a "$d" "/storage/.cache/mesa_shader_cache/$d"; }' _ {} \;)
  N_NEW=$(find "$TMP/bundle/shaders" -type f | wc -l | tr -d ' ')
  say "    ${G}shaders merged -> $MESA_CACHE ($N_NEW files)${N}"
elif [ "$VAULT_OK" -eq 0 ]; then
  say "${Y}    shaders skipped (homologation gate)${N}"
fi

# ---------- inject: savedata (TIER 3, opt-in, non-clobbering) ----------
if [ "$WITH_SAVEDATA" -eq 1 ] && [ -d "$TMP/bundle/savedata" ]; then
  # TODO(payload-test): finalize destination + back up recipient's existing
  # save BEFORE extract (dossiers/ProTuningExportDossier.md §6 TIER 3).
  say "${Y}[TODO] savedata tier present but install is stubbed in the scaffold.${N}"
fi

# ---------- swappiness one-shot (NOT a daemon; proven essential) ----------
mkdir -p /storage/.config/custom_scripts
SWAP_SH=/storage/.config/custom_scripts/99-protune-swappiness.sh
cat > "$SWAP_SH" <<'EOS'
#!/bin/sh
# ETK Pro Tuning — swappiness one-shot. Boot-time, fire-and-exit. Not a daemon.
sysctl -w vm.swappiness=10 >/dev/null 2>&1
EOS
chmod +x "$SWAP_SH"
sysctl -w vm.swappiness=10 >/dev/null 2>&1 || true
say "    ${G}swappiness one-shot installed${N}"

# ---------- soft check: is the game present? (shaders/config are inert without it) ----------
if ! ls -d /storage/*/roms/bios/rpcs3/dev_hdd0/game/"$GAME_ID" >/dev/null 2>&1; then
  say "${Y}[NOTE] $GAME_ID isn't in your RPCS3 library yet — install the game to play it.${N}"
fi

# ---------- setup sheet + breadcrumb ----------
say ""
say "${G}=== Pro Tuning installed: $NAME ===${N}"
say "One-time, set these per-game in ROCKNIX (Game Options):"
say "   Cooling Profile : Aggressive"
say "   CPU Governor    : Performance"
say "   GPU Performance : Performance"
say ""
say "${C}Made with the ETK (Emulation Tuning Kit) — get the full kit: https://github.com/$REPO${N}"
