#!/bin/busybox sh
# ==========================================================
# GEMINI IMMUTABLE RULE: POSIX COMPLIANCE
# Purpose: Static analysis to prevent regression of 
# Thermal Boundaries and SHM sanctity.
# ==========================================================

EXIT_CODE=0

# 1. ENFORCE SINGLE SOURCE OF TRUTH
# Checks if scripts are correctly sourcing env.sh rather than hardcoding variables.
for script in bin/*.sh scripts/*.sh; do
    if ! grep -q "source .*env.sh" "$script"; then
        echo "REGRESSION: $script does not source env.sh. Rule #2 Violation."
        EXIT_CODE=1
    fi
done

# 2. PROTECT THE HARD THERMAL CEILING
# Ensures the ALARM_TEMP and FAILSAFE logic in thermal_d.sh remain intact.
if ! grep -q "export ALARM_TEMP=" scripts/env.sh; then
    echo "CRITICAL: ALARM_TEMP definition lost in env.sh!"
    EXIT_CODE=1
fi

if ! grep -q "if \[ \"\$TEMP\" -ge \"\$RACE_THRESHOLD\" \]" bin/thermal_d.sh; then
    echo "CRITICAL: Thermal Failsafe logic missing from thermal_d.sh!"
    EXIT_CODE=1
fi

# 3. GUARD SHM SANCTITY
# Prevents any attempt to move the sacred /dev/shm path.
if grep -r "/tmp/etk" .; then
    echo "RULE VIOLATION: Unauthorized IPC path detected. Rule #1: NEVER TOUCH THE SHM."
    EXIT_CODE=1
fi

# 4. BUSYBOX COMPLIANCE CHECK
# Flagging common GNU-isms that break on Rocknix.
if grep -qrE "grep -P|stat --format|find -printf" .; then
    echo "COMPATIBILITY ERROR: Non-POSIX flags detected. Use BusyBox-safe syntax."
    EXIT_CODE=1
fi

exit $EXIT_CODE