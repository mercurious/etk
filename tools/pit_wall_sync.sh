#!/bin/bash
# ==========================================================
# ETK: PIT WALL SYNC (Google Drive Hot Drop Daemon)
# Runs on the Mac. Pulls telemetry from rig -> Google Drive.
# ==========================================================
# TO DO:
# Simplify code mirror sync below
# Next up:
# Integrate with commander.sh
# Update this path to your actual Google Drive location!
# Move this to scripts/env.sh and use rig SSH from there
GDRIVE_PATH="$HOME/Google Drive/My Drive/ETK_Telemetry"
# LOCAL_ETK_PATH="~/etk"
RIG_SSH="root@192.168.1.53" # Your rig's IP from env.sh

echo "🏁 ETK PIT WALL SYNC INITIATED"
echo "📡 Target Rig: $RIG_SSH"
echo "📂 Drop Zone: $GDRIVE_PATH"
echo "----------------------------------------------------"

mkdir -p "$GDRIVE_PATH/crash_logs"
mkdir -p "$GDRIVE_PATH/live_shm"
echo "🧹 SEEDING COLD START TELEMETRY STRUCTS..."
mkdir -p "$GDRIVE_PATH/crash_logs"
mkdir -p "$GDRIVE_PATH/live_shm"
mkdir -p "$GDRIVE_PATH/etk_code_mirror"
mkdir -p "$GDRIVE_PATH/etk_code_mirror/local"
mkdir -p "$GDRIVE_PATH/etk_code_mirror/rig"

# Seed explicit offline markers so the data state is never ambiguous
echo "ETK:STATUS|OFFLINE - AWAITING RIG BOOT" > "$GDRIVE_PATH/live_shm/live_stat.txt"
if [ ! -f "$GDRIVE_PATH/crash_logs/etk_crash_report.log" ]; then
    echo "ETK: FORENSICS ENGINE ONLINE - NO ACTIVE CRASH REPORTED" > "$GDRIVE_PATH/crash_logs/etk_crash_report.log"
fi
echo "🏁 STATUS SEEDED. LOGGING ENGINE STARTED."
# =======================================================================

while true; do
    # 1. Pull the Crash Log (Only if updated)
    rsync -az --update "$RIG_SSH:/storage/etk_crash_report.log" "$GDRIVE_PATH/crash_logs/" 2>/dev/null

    # 2. Pull the live SHM Telemetry (For live analytics)
    rsync -az --update "$RIG_SSH:/dev/shm/etk_shm/" "$GDRIVE_PATH/live_shm/" 2>/dev/null

	# 3. Pull the code snapshot into a mirror for review

    # =======================================================================
    # STREAMLINED DIAGNOSTIC SOURCE CODE SYNC
    # =======================================================================
    # Syncs everything in ~/etk into the mirror directory, explicitly include 
    # our code/config file types, and drops the heavy shader vault completely.
    # First the local copy and then the copy on the device
	rsync -az --update --delete \
        --exclude="vault/" \
        --include="*.py" \
        --include="*.sh" \
        --include="*.conf" \
        --include="*.config" \
        --include="*.yml" \
        --include="*.md" \
        --include="*.json" \
        --include="*/" \
        --exclude="*" \
      	"$HOME/etk/" "$GDRIVE_PATH/etk_code_mirror/local" 2>/dev/null  
    # =======================================================================
	rsync -az --update --delete \
        --exclude="vault/" \
        --include="*.py" \
        --include="*.sh" \
        --include="*.conf" \
        --include="*.config" \
        --include="*.yml" \
        --include="*.md" \
        --include="*.json" \
        --include="*/" \
        --exclude="*" \
        "$RIG_SSH:/storage/roms/etk/" "$GDRIVE_PATH/etk_code_mirror/rig" 2>/dev/null    
    
    # Quietly wait 5 seconds before checking again
    sleep 5
done

