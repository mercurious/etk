#!/bin/bash
# ==========================================================
# ETK PHASE 9: Game Agnostic ETK (v9.5.2 - ANTI-RACE / PATH-LOCKED)
# ==========================================================
# OWNER: Vault Daemon (The Accountant)
# TARGET: Global Mesa Shader Cache (Symlinked to Vault)
# GEMINI IMMUTABLE RULE: Polling loop interval must run at 2s for precise 
# real-time telemetry. Session lifetimes are completely anchored to Sentry 
# invocation; do not attempt automated baseline resets inside this loop.
# To eliminate path-shifting race conditions, the target directory profile 
# must be resolved and locked at startup before establishing the baseline.
# All state outputs must implement atomic 'echo > tmp && mv tmp output' patterns.
# ==========================================================

# 1. STARTUP IDENTITY SYNC
# Loop smoothly until the Sentry establishes a definitive, non-IDLE Game ID profile
while true; do
    source /storage/games-internal/roms/etk/scripts/env.sh
    if [ ! -z "$TARGET_ID" ] && [ "$TARGET_ID" != "IDLE" ] && [[ "$VAULT_DIR" != *"IDLE"* ]]; then
        break
    fi
    sleep 0.5
done

# 2. IMMUTABLE PATH LOCK
# Freeze environment variables to protect the session from downstream environment updates
LOCKED_VAULT_DIR="$VAULT_DIR"
LOCKED_SHM_DIR="$SHM_DIR"
LOCKED_VAULT_COUNT="$VAULT_COUNT"

mkdir -p "$LOCKED_VAULT_DIR"
mkdir -p "$LOCKED_SHM_DIR"
echo "READY" > "$LOCKED_SHM_DIR/vault_stat.txt"

# 3. ESTABLISH CLEAN SESSION BASELINE
# Capture the absolute baseline count once against the locked directory tree
if [ -d "$LOCKED_VAULT_DIR" ]; then
    SESSION_BASELINE=$(find "$LOCKED_VAULT_DIR" -type f 2>/dev/null | wc -l)
else
    SESSION_BASELINE=0
fi

# 4. ACCOUNTING ACCOUNTANT LOOP
while true; do
    # Capture absolute real-time bank count
    CURRENT_BANK=$(find "$LOCKED_VAULT_DIR" -type f 2>/dev/null | wc -l)
    
    # Session Delta Calculation (Directly tracked from the immutable startup snapshot)
    NEW_COUNT=$((CURRENT_BANK - SESSION_BASELINE))
    if [ "$NEW_COUNT" -lt 0 ]; then 
        NEW_COUNT=0 
    fi

    # Size Calculation (BusyBox compliance using awk decimal interpretation)
    VAULT_SIZE=$(du -sm "$LOCKED_VAULT_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
    
    # Atomic Hand-off to prevent line collisions with mango_bridge.sh
    echo "$CURRENT_BANK" > "$LOCKED_VAULT_COUNT.tmp" && mv "$LOCKED_VAULT_COUNT.tmp" "$LOCKED_VAULT_COUNT"
    echo "$NEW_COUNT" > "$LOCKED_SHM_DIR/vault_new.txt.tmp" && mv "$LOCKED_SHM_DIR/vault_new.txt.tmp" "$LOCKED_SHM_DIR/vault_new.txt"
    echo "$VAULT_SIZE" > "$LOCKED_SHM_DIR/vault_size.txt.tmp" && mv "$LOCKED_SHM_DIR/vault_size.txt.tmp" "$LOCKED_SHM_DIR/vault_size.txt"
    
    sleep 2
done