#!/bin/bash
# ==========================================================
# ETK: PIT WALL SYNC (Google Drive Hot Drop Daemon)
# Runs on the Mac. Pulls telemetry from rig -> Google Drive.
# ==========================================================
# Make sure to update this path to your actual Google Drive location!
GDRIVE_PATH="$HOME/Google Drive/My Drive/ETK_Telemetry"
RIG_SSH="root@192.168.1.53" # Your rig's IP from env.sh

echo "🏁 ETK PIT WALL SYNC INITIATED"
echo "📡 Target Rig: $RIG_SSH"
echo "📂 Drop Zone: $GDRIVE_PATH"
echo "----------------------------------------------------"

mkdir -p "$GDRIVE_PATH/crash_logs"
mkdir -p "$GDRIVE_PATH/live_shm"

while true; do
    # 1. Pull the Crash Log (Only if updated)
    rsync -az --update "$RIG_SSH:/storage/etk_crash_report.log" "$GDRIVE_PATH/crash_logs/" 2>/dev/null

    # 2. Pull the live SHM Telemetry (For live analytics)
    rsync -az --update "$RIG_SSH:/dev/shm/etk_shm/" "$GDRIVE_PATH/live_shm/" 2>/dev/null

    # Quietly wait 5 seconds before checking again
    sleep 5
done