#!/bin/bash
# ==========================================================
# ETK PHASE 9: Game Agnostic ETK (v9.5.0 - SESSION TELEMETRY)
# ==========================================================
# OWNER: Vault Daemon (The Accountant)
# TARGET: Global Mesa Shader Cache (Symlinked to Vault)
# AI INSTRUCTION: DO NOT ATTEMPT TO RSYNC CACHE TO VAULT.
# The cache IS the vault via symlink. We track session growth.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

mkdir -p "$VAULT_DIR"
mkdir -p "$SHM_DIR"
echo "READY" > "$SHM_DIR/vault_stat.txt"

# Establish Session Baseline
if [ -d "$VAULT_DIR" ]; then
    SESSION_BASELINE=$(find "$VAULT_DIR" -type f 2>/dev/null | wc -l)
else
    SESSION_BASELINE=0
fi

while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh

    # 1. The Sentry Watcher (Reset baseline if Game ID shifts)
    # We use the active symlink size to ensure we are looking at the right game
    CURRENT_BANK=$(find "$VAULT_DIR" -type f 2>/dev/null | wc -l)
    
    # If the bank is somehow smaller than baseline, a game shift occurred
    if [ "$CURRENT_BANK" -lt "$SESSION_BASELINE" ]; then
        SESSION_BASELINE=$CURRENT_BANK
    fi

    # 2. Session Delta Calculation
    NEW_COUNT=$((CURRENT_BANK - SESSION_BASELINE))
    if [ "$NEW_COUNT" -lt 0 ]; then NEW_COUNT=0; fi

    # 3. Size Calculation
    VAULT_SIZE=$(du -sm "$VAULT_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
    
    # 4. RAM Disk Hand-off for mango_bridge.sh
    echo "$CURRENT_BANK" > "$VAULT_COUNT"
    echo "$NEW_COUNT" > "$SHM_DIR/vault_new.txt"
    echo "$VAULT_SIZE" > "$SHM_DIR/vault_size.txt"
    
    sleep 10
done