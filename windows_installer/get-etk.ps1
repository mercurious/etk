#requires -Version 5.1
# ==========================================================
# ETK WINDOWS ONE-LINER BOOTSTRAP
# ==========================================================
# Fetches the ETK repo as a zip from GitHub (no git needed), lays it down
# at ~\etk, and hands off to the real installer. Run from any PowerShell:
#
#   irm https://raw.githubusercontent.com/mercurious/etk/main/windows_installer/get-etk.ps1 | iex
#
# Re-running updates the checkout in place: repo files are overwritten,
# but machine-local files that are not in the zip (etk-env.ps1 edits are
# in the zip and WILL be refreshed — put custom values back afterward,
# or keep them in a copy) are left alone. Safe to repeat.
#
# Designed to be piped into iex, so: no params, no `exit` (that would
# close the caller's console), fail loudly via throw.
# ==========================================================

$ErrorActionPreference = "Stop"
# GitHub requires TLS 1.2; Windows PowerShell 5.1 needs the opt-in.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo   = "mercurious/etk"
$Branch = "main"
$Dest   = Join-Path $HOME "etk"
$ZipUrl = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
$Tmp    = Join-Path $env:TEMP ("etk-fetch-" + [IO.Path]::GetRandomFileName())

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  ETK BOOTSTRAP  (fetch -> $Dest -> installer)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    $Zip = Join-Path $Tmp "etk.zip"
    Write-Host ">>> Downloading $ZipUrl ..."
    Invoke-WebRequest -Uri $ZipUrl -OutFile $Zip -UseBasicParsing

    Write-Host ">>> Extracting ..."
    Expand-Archive -Path $Zip -DestinationPath $Tmp
    $Src = Join-Path $Tmp "etk-$Branch"
    if (-not (Test-Path (Join-Path $Src "windows_installer\etk-install.ps1"))) {
        throw "Unexpected zip layout - windows_installer\etk-install.ps1 not found in the archive."
    }

    Write-Host ">>> Installing repo files into $Dest ..."
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Copy-Item -Path (Join-Path $Src "*") -Destination $Dest -Recurse -Force
}
finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ">>> Handing off to the ETK installer (repo root: $Dest)" -ForegroundColor Cyan
Set-Location $Dest
# Child powershell so the user's execution policy can't block the file-based
# installer, and so an installer failure can't kill this console.
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "windows_installer\etk-install.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host ">>> Installer reported errors (exit $LASTEXITCODE). Fix and re-run:" -ForegroundColor Red
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-install.ps1" -ForegroundColor Red
} else {
    Write-Host ">>> Done. Repo lives at $Dest - re-run this one-liner anytime to update." -ForegroundColor Green
}
