#!/bin/bash
# ==========================================================
# ETK PHASE 9: AGNOSTIFIER (v9.0.2 - THE GREAT MIGRATION)
# ==========================================================
# TARGET: 800MB+ GT5P Vault -> Agnostic Hierarchy
# ==========================================================

#!/bin/bash
# Move to the project root (/storage/roms/etk)
cd "$(dirname "$0")/.." 

# Source environment once from the scripts folder
if [ -f "./scripts/env.sh" ]; then
    source ./scripts/env.sh
else
    echo "ERROR: env.sh not found at ./scripts/env.sh"
    exit 1
fi

CHIPSET="SM8250"
# Point to where the shaders actually live now before migration
LEGACY_DATA="/storage/.cache/mesa_shader_cache" 
NEW_VAULT_BASE="$ETK_ROOT/vault/$CHIPSET"
TARGET_ID="NPUA80075" # GT5P

echo -e "\033[33m>>> [1/3] PREPARING AGNOSTIC HIERARCHY...\033[0m"
mkdir -p "$NEW_VAULT_BASE/$TARGET_ID/shaders"
mkdir -p "$NEW_VAULT_BASE/$TARGET_ID/config"

echo -e "\033[36m>>> [2/3] MIGRATING 815MB+ SHADER DATA...\033[0m"
# Using rsync with progress2 as planned
rsync -a --info=progress2 "$LEGACY_DATA/" "$NEW_VAULT_BASE/$TARGET_ID/shaders/"

echo -e "\033[32m>>> [3/3] ARCHIVING TUNING PROFILE...\033[0m"
if [ -f "$RPCS3_CONFIG" ]; then
    cp "$RPCS3_CONFIG" "$NEW_VAULT_BASE/$TARGET_ID/config/config_custom.yml"
fi

echo -e "\n\033[32m[!] MIGRATION COMPLETE. Ready for Phase 9.\033[0m"

# Verify the new vault size using the BusyBox 'stat' logic
NEW_VAL_SIZE=$(du -sh "$NEW_VAULT_BASE/$TARGET_ID/shaders" | awk '{print $1}')
echo -e "\033[32m>>> VERIFIED: $NEW_VAL_SIZE migrated to Agnostic Vault.\033[0m"