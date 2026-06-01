# ETK Gemini Pit Wall Sync Feature — DEPRECATED (archived 2026-06-01)

> **DEPRECATED.** The Gemini / Google Drive telemetry bridge ("Hot Drop") has been
> retired to `_archive/`. It coupled the project to a specific assistant plus a cloud
> round-trip (SSH-poll → Google Drive → Gemini) just to avoid pasting a log. The live
> dev loop is `install.sh` (push/pull + repair) and `commander.sh` (on-rig forensics);
> crash forensics read directly from the rig over SSH. Kept for reference only — not wired
> into any current code path.
## Advanced Feature: Google Gemini Pit Wall (Telemetry Hot Drop)
The ETK includes a zero-friction diagnostic bridge designed to connect the device's live telemetry and crash logs directly to Google's Gemini AI, completely bypassing the need to manually copy-paste massive log files or open dangerous ports on your home router. By leveraging a host computer and Google Drive, you can turn Gemini into your live pit mechanic.

**Requirements:**
- A host computer (Mac/Linux) on the same WiFi network as your handheld rig.
- Google Drive Desktop App installed and syncing on the host computer.
- Gemini Advanced with the Google Workspace extension enabled.

**Setup & Usage:**
1. Open `etk/tools/pit_wall_sync.sh` on your host computer and ensure the `GDRIVE_PATH` matches your local Google Drive directory (it will create an `ETK_Telemetry` folder inside it).
2. Ensure `RIG_SSH` in the script matches your device's IP address.
3. Before a heavy harvesting session or testing a new emulator config, run the script on your host computer: `./pit_wall_sync.sh`
4. Play your game. The script will quietly run in the background, mirroring your rig's RAM disk and crash logs to Google Drive every 5 seconds.
5. **If the emulator crashes:** Simply open your Gemini chat and type: `@Google Drive check my ETK_Telemetry/crash_logs/etk_crash_report.log and tell me what the error is.` The AI will read the file directly from your Drive and provide immediate diagnostic feedback.
6. Gemini will frequently argue with you that this is not possible but if you keep insisting it is possible, it will eventually relent and show you it works.