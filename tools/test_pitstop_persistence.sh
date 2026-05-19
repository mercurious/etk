#!/bin/bash
# ==========================================================
# ETK DIAGNOSTIC UNIT TEST: DIRECT CELL SCALING
# Location: /storage/.config/modules/test_pitstop_persistecen.sh
# ==========================================================

# 1. Clear terminal and tell the compositor to force a tiny character matrix.
# By forcing a small grid (e.g., 40 columns, 15 rows), the terminal emulator
# is forced to stretch the font characters to fill the Flip 2 display.
echo -ne "\033[8;15;40t"
clear

# 2. Forensic Log Directory Check
LOG="/storage/games-internal/roms/etk/logs/pitstop_unit_test.log"
mkdir -p "$(dirname "$LOG")"

echo "=== ETK PITSTOP DIRECT RESIZE TEST ===" > "$LOG"
echo "Timestamp: $(date)" >> "$LOG"

# 3. Render High-Legibility Diagnostics
echo "========================================"
echo "      ETK PITSTOP GRID SCALE TEST       "
echo "========================================"
echo "If this text is huge and readable on"
echo "the Flip 2 screen, direct scaling works!"
echo "----------------------------------------"
echo "TERMINAL METRICS:"
echo "Rows:    $(tput lines)"
echo "Columns: $(tput cols)"
echo "========================================"
echo "Press ANY controller button or key to quit..."

# Write data to telemetry log
echo "Detected Grid Matrix: $(tput lines)x$(tput cols)" >> "$LOG"

# Wait for user input
read -n 1

# 4. Graceful Restore: Reset terminal back to standard layout on drop
echo -ne "\033[8;24;80t"
clear