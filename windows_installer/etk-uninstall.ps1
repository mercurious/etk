#requires -Version 5.1
# ==========================================================
# ETK WINDOWS HOST UNINSTALLER  (port of uninstall.sh)
# ==========================================================
# Mirrors uninstall.sh. The STOP / HW / CLEAN bash blocks are read
# verbatim from uninstall.sh at runtime, so uninstall.sh stays the single
# source of truth for what happens on the rig.
#
# Vault handling: the rig's local shader vault is PRESERVED by default
# (it survives a later reinstall). Because the Windows host keeps no
# backup, destroying it would be unrecoverable - so destruction is
# opt-in via -ZapVault, exactly like uninstall.sh's --zap-vault.
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\etk-uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File .\etk-uninstall.ps1 -ZapVault
# ==========================================================

param([switch]$ZapVault)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\etk-env.ps1"
. "$PSScriptRoot\etk-common.ps1"

# Scripts live in windows_installer/; uninstall.sh is one level up at repo root.
$UninstallSh = Join-Path (Split-Path $PSScriptRoot -Parent) "uninstall.sh"
$TOTAL = 4
$zap = if ($ZapVault) { "1" } else { "0" }

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Red
Write-Host "  ETK RETIREMENT - REMOTE CLEAN & ZAP (Windows host)" -ForegroundColor Red
Write-Host "==========================================================" -ForegroundColor Red
Write-Host ""

if ($zap -eq "1") {
    Write-Host ">>> WARNING: -ZapVault passed. The rig's banked shaders will be DESTROYED." -ForegroundColor Red
    Write-Host "    There is no host-side backup on Windows. This cannot be undone." -ForegroundColor Red
    $confirm = Read-Host "    Type ZAP to confirm vault destruction"
    if ($confirm -ne "ZAP") { Write-Host "    Aborted - vault left intact." -ForegroundColor Yellow; exit 1 }
} else {
    Write-Host ">>> Your rig's shader vault will be PRESERVED on this uninstall." -ForegroundColor Yellow
    Write-Host "    To destroy the vault too, re-run with:  -ZapVault" -ForegroundColor Yellow
}
Write-Host ""

Assert-Tooling
# Resolve-once-pin: same mDNS-stall guard as the installer.
Resolve-RigHost
# Establish passwordless SSH first (test-first + idempotent); without it the
# remote teardown below would prompt for the rig password repeatedly. Mirrors
# the installer's pairing gate.
Invoke-EtkPair
Assert-RigConnection

# ==========================================================
# STEP 1: QUIESCE ALL ETK PROCESSES  (uninstall.sh STOP block)
# ==========================================================
Write-Step 1 $TOTAL "STOPPING ETK SERVICES & PROCESSES..."
$stop = Get-Heredoc -Path $UninstallSh -Marker "STOP"
$out1 = Invoke-RigBash -Script $stop
if ($out1) { $out1 | ForEach-Object { Write-Note $_ } }

# ==========================================================
# STEP 2: RESTORE STOCK HARDWARE STATES  (uninstall.sh HW block)
# Must run while the rig is still up, before ETK_ROOT removal.
# ==========================================================
Write-Step 2 $TOTAL "RESTORING STOCK HARDWARE STATES..."
$hw = Get-Heredoc -Path $UninstallSh -Marker "HW"
$out2 = Invoke-RigBash -Script $hw
if ($out2) { $out2 | ForEach-Object { Write-Note $_ } }

# ==========================================================
# STEP 3: REMOVE ETK FILES  (uninstall.sh CLEAN block)
# CLEAN is an UNQUOTED heredoc in bash: it expands $ETK_ROOT and
# $ZAP_VAULT on the host. We pass those same two values as environment
# variables so the verbatim block resolves identically on the rig.
# ==========================================================
Write-Step 3 $TOTAL "REMOVING ETK FILES FROM RIG..."
$clean = Get-Heredoc -Path $UninstallSh -Marker "CLEAN"
$out3 = Invoke-RigBash -Script $clean -EnvVars @{ ETK_ROOT = $EtkRoot; ZAP_VAULT = $zap }
if ($out3) { $out3 | ForEach-Object { Write-Note $_ } }

# ==========================================================
# STEP 4: HOST-SIDE STATUS
# No host vault on Windows - nothing to report or delete locally.
# ==========================================================
Write-Step 4 $TOTAL "HOST-SIDE STATUS..."
if ($zap -eq "1") {
    Write-Note "Rig vault destroyed. No host backup exists on Windows."
} else {
    Write-Note "Rig vault preserved on the device. If you want an offline copy,"
    Write-Note "pull <ETK_ROOT>/vault off the rig over SMB before reflashing."
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  RETIREMENT COMPLETE. RIG RESTORED." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
if ($zap -ne "1") {
    Write-Host "    Reinstall anytime with .\etk-install.ps1 - the preserved rig vault is reused." -ForegroundColor Yellow
}
Write-Host ""
