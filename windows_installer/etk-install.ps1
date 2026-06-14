#requires -Version 5.1
# ==========================================================
# ETK WINDOWS HOST INSTALLER  (no-vault port of install.sh)
# ==========================================================
# Mirrors install.sh step-for-step EXCEPT:
#   - Step 2 (vault PULL rig->host)  : SKIPPED (no host vault on Windows)
#   - Step 4 (vault PUSH host->rig)  : SKIPPED
# The rig still vaults shaders locally; only the host-side backup is gone.
# To protect a vault on Windows, copy <ETK_ROOT>/vault off the device over
# SMB before reflashing - see WINDOWS_HOST_README.md.
#
# The Sentry, systemd unit, PKG drop README, and mako style are read
# verbatim from install.sh at runtime (Get-Heredoc), so install.sh stays
# the single source of truth for everything that runs on the rig.
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\etk-install.ps1
# ==========================================================

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\etk-env.ps1"
. "$PSScriptRoot\etk-common.ps1"

# Scripts live in windows_installer/; the bash source of truth (install.sh,
# bin/, scripts/, config/, tools/) is one level up at the repo root.
$RepoRoot    = Split-Path $PSScriptRoot -Parent
$InstallSh   = Join-Path $RepoRoot "install.sh"
$TOTAL       = 8
# GitHub API on Windows PowerShell 5.1 needs TLS 1.2 opted in explicitly.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  ETK WINDOWS FLASHER  (no-vault)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

Assert-Tooling
# Establish passwordless SSH first (test-first + idempotent; <=1 password on a
# fresh rig, zero thereafter). Without this every ssh/scp below would prompt.
Invoke-EtkPair
Assert-RigConnection

# ==========================================================
# STEP 0: PROBE & QUIESCE  (install.sh Step 0)
# Kill ETK workers before file ops. Resolve the rig game ID purely to
# provision that game's vault dir; the Sentry creates per-game dirs on
# demand anyway, so the NPUA80075 fallback is fine when idle.
# ==========================================================
Write-Step 0 $TOTAL "PROBING & QUIESCING REMOTE RIG..."
Invoke-Rig "pkill -f vault_d.sh; pkill -f thermal_d.sh; pkill -f mango_bridge.sh; pkill -f input_d.py" 2>$null | Out-Null
Start-Sleep -Seconds 1

$RigId = (Invoke-Rig "cat $IdFile 2>/dev/null || pgrep -f rpcs3 | xargs -I{} cat /proc/{}/cmdline /proc/{}/environ 2>/dev/null | tr '\0' '\n' | grep -oE '[A-Z]{4}[0-9]{5}' | head -n 1")
$RigId = ("$RigId").Trim()
switch ($RigId) {
    ""           { $TargetId = "NPUA80075" }
    "IDLE"       { $TargetId = "NPUA80075" }
    "UNKNOWN_ID" { $TargetId = "NPUA80075" }
    default      { $TargetId = $RigId }
}
$VaultDir = "$EtkRoot/vault/$Chipset/$TargetId/shaders"
Write-Note "RIG ID   : $TargetId"
Write-Note "VAULT DIR: $VaultDir"

# ==========================================================
# STEP 1: PROVISION RIG DIRECTORIES  (install.sh Step 1)
# ==========================================================
Write-Step 1 $TOTAL "PROVISIONING RIG DIRECTORIES..."
$dirs = @(
    $VaultDir, "$EtkRoot/bin", "$EtkRoot/scripts", "$EtkRoot/tools",
    "$EtkRoot/vault", "$EtkRoot/logs", "$EtkRoot/config", "$EtkRoot/screenshots",
    "$EtkRoot/pro-tuning",
    $PkgStaging,
    "/storage/.config/custom_scripts", "/storage/.config/system.d",
    "/storage/.config/modules", "/storage/.config/MangoHud",
    $Rpcs3CustomConfigs, $Rpcs3GameDir, $Rpcs3ExdataDir, $Rpcs3HomeDir,
    $Ps3LauncherDir, $TelemetryDir
)
$mkdirCmd = "mkdir -p " + (($dirs | ForEach-Object { "'$_'" }) -join " ")
Invoke-Rig $mkdirCmd | Out-Null
Write-Ok "Directories provisioned."

# PKG drop-folder README (verbatim from install.sh PKGREADME heredoc)
$pkgReadme = Get-Heredoc -Path $InstallSh -Marker "PKGREADME"
Send-Text -Content $pkgReadme -RemotePath "$PkgStaging/README.txt"
Write-Ok "PKG drop-folder README written."

# Screenshots-folder README (verbatim from install.sh SHOTREADME heredoc)
$shotReadme = Get-Heredoc -Path $InstallSh -Marker "SHOTREADME"
Send-Text -Content $shotReadme -RemotePath "$EtkRoot/screenshots/README.txt"
Write-Ok "Screenshots-folder README written."

# ETK mako notification style (verbatim ETKMAKO block; host-var-free)
$mako = Get-Heredoc -Path $InstallSh -Marker "ETKMAKO"
$makoOut = Invoke-RigBash -Script $mako
if ($makoOut) { $makoOut | ForEach-Object { Write-Note $_ } }

# ==========================================================
# STEP 2: VAULT PULL  ->  SKIPPED on Windows (no host vault)
# ==========================================================
Write-Step 2 $TOTAL "SAFEGUARD HARVEST (vault pull) - SKIPPED on Windows host."
Write-Note "No host-side vault backup on Windows. Copy <ETK_ROOT>/vault over SMB if you want to shield it."

# ==========================================================
# STEP 3: DEPLOY SCRIPTS, DAEMONS & OVERLAYS  (install.sh Step 3)
# --delete is replicated by Push-Dir -Mirror (remote tree removed first).
# ==========================================================
Write-Step 3 $TOTAL "DEPLOYING GUARDIAN DAEMONS & SCRIPTS..."
Push-Dir -LocalDir (Join-Path $RepoRoot "bin")     -RemoteParent $EtkRoot -Mirror
Push-Dir -LocalDir (Join-Path $RepoRoot "scripts") -RemoteParent $EtkRoot -Mirror

# tools/etk_drift.py is the only tools/ entry that runs ON the rig (the OS-drift
# detector). install.sh deploys just this one file (the rest of tools/ is
# host-side); mirror that with a single-file push, not the whole tree.
$driftLocal = Join-Path $RepoRoot "tools\etk_drift.py"
if (Test-Path -LiteralPath $driftLocal) {
    Send-File -LocalPath $driftLocal -RemotePath "$EtkRoot/tools/etk_drift.py"
    Write-Ok "OS-drift detector (etk_drift.py) deployed."
} else {
    Write-Warn "tools\etk_drift.py not found locally - skipping (verify the path)."
}

$mangoLocal = Join-Path $RepoRoot "config\MangoHud.conf"
if (Test-Path -LiteralPath $mangoLocal) {
    Send-File -LocalPath $mangoLocal -RemotePath "/storage/.config/MangoHud/MangoHud.conf"
} else {
    Write-Warn "config\MangoHud.conf not found locally - skipping overlay push (verify the filename)."
}
Invoke-Rig "chmod +x $EtkRoot/bin/* $EtkRoot/scripts/* $EtkRoot/tools/etk_drift.py 2>/dev/null; true" | Out-Null

# WINDOWS-SPECIFIC: strip any CRLF from shell scripts that a Windows clone
# may have introduced. install.sh relies on a Linux/Mac checkout being LF;
# here we normalise on the rig so the daemons actually run. (See the
# .gitattributes recommendation in WINDOWS_HOST_README.md.)
$crlfFix = @'
for f in __ETKROOT__/bin/*.sh __ETKROOT__/bin/*.py __ETKROOT__/scripts/*.sh __ETKROOT__/tools/*.py; do
  [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
true
'@
Invoke-RigTemplate -Template $crlfFix | Out-Null
Write-Ok "Daemons + scripts deployed (CRLF normalised)."

# ==========================================================
# STEP 4: VAULT PUSH  ->  SKIPPED on Windows (no host vault)
# ==========================================================
Write-Step 4 $TOTAL "RESTORE BANKED SHADERS (vault push) - SKIPPED on Windows host."

# ==========================================================
# STEP 5: DEPLOY ETK PITSTOP ROCKNIX INTERFACE  (install.sh Step 5)
# ==========================================================
Write-Step 5 $TOTAL "DEPLOYING ETK PITSTOP ROCKNIX INTERFACE..."

# Push config payload (pitstop_fields.json, crash_signatures.json,
# etk_template.yml, etk_pitstop.svg, etk_pitstop.sh master copy, ...).
Push-Dir -LocalDir (Join-Path $RepoRoot "config") -RemoteParent $EtkRoot

# PADDOCK injector (0.5.0): the rig-side Pro Tuning installer the Pitstop
# PADDOCK tab shells out to. Mirrors install.sh Step 4 — deploy ONLY
# install-protune.sh (export.sh is the host-only producer; signature/ renders
# are tabled prototypes). CRLF-strip + chmod so a Windows checkout's script
# runs under the rig's /bin/sh.
$protuneLocal = Join-Path $RepoRoot "pro-tuning\install-protune.sh"
if (Test-Path -LiteralPath $protuneLocal) {
    Send-File -LocalPath $protuneLocal -RemotePath "$EtkRoot/pro-tuning/install-protune.sh"
    Invoke-Rig "sed -i 's/\r$//' $EtkRoot/pro-tuning/install-protune.sh && chmod +x $EtkRoot/pro-tuning/install-protune.sh" | Out-Null
    Write-Ok "PADDOCK injector (install-protune.sh) deployed."
} else {
    Write-Warn "pro-tuning\install-protune.sh not found locally - PADDOCK APPLY will be unavailable (verify the path)."
}

# Sanitise + arm the master launcher copy in config/
$fixMaster = @'
sed -i 's/\r$//' __ETKROOT__/config/etk_pitstop.sh
chmod +x __ETKROOT__/config/etk_pitstop.sh
'@
Invoke-RigTemplate -Template $fixMaster | Out-Null

# Deploy launcher to the volatile modules dir for immediate use
Send-File -LocalPath (Join-Path $RepoRoot "config\etk_pitstop.sh") `
          -RemotePath "/storage/.config/modules/etk_pitstop.sh"
Invoke-Rig "sed -i 's/\r$//' /storage/.config/modules/etk_pitstop.sh && chmod +x /storage/.config/modules/etk_pitstop.sh && sync" | Out-Null

# Verify the launcher landed and is executable
$check = (Invoke-Rig "[ -x /storage/.config/modules/etk_pitstop.sh ] && echo OK || echo MISSING").Trim()
if ($check -ne "OK") {
    Write-ErrLine "Launcher missing or lacks +x - aborting."
    exit 1
}
Write-Ok "Launcher verified and locked to disk."

# Register the Tools-menu app (idempotent injector)
Write-Note "Registering ETK Pitstop in the Tools menu..."
Invoke-Rig "python3 $EtkRoot/bin/etk_modules_inject.py" | Out-Null
$glCount = (Invoke-Rig "grep -c '>ETK Pitstop<' /storage/.config/modules/gamelist.xml 2>/dev/null || echo 0").Trim()
if (($glCount -as [int]) -ge 1) {
    Write-Ok "ETK Pitstop registered as a Tools-menu app."
} else {
    Write-Warn "Tools-menu entry not confirmed - the Sentry will re-inject it on boot."
}

# ==========================================================
# STEP 6: WRITE & RESTART THE SENTRY  (install.sh Step 6)
# SENTRY and SVC bodies are pulled verbatim from install.sh.
# ==========================================================
Write-Step 6 $TOTAL "DEPLOYING ROCKNIX-NATIVE SYSTEMD SENTRY..."
$sentry = Get-Heredoc -Path $InstallSh -Marker "SENTRY"
$svc    = Get-Heredoc -Path $InstallSh -Marker "SVC"

Invoke-Rig "mkdir -p /storage/.config/custom_scripts /storage/.config/system.d" | Out-Null
Send-Text -Content $sentry -RemotePath "/storage/.config/custom_scripts/01-etk-sentry.sh" -Executable
Send-Text -Content $svc    -RemotePath "/storage/.config/system.d/etk.service"

Invoke-Rig "systemctl daemon-reload; systemctl enable /storage/.config/system.d/etk.service 2>/dev/null; systemctl restart etk.service" | Out-Null
Start-Sleep -Seconds 2
$active = (Invoke-Rig "systemctl is-active etk.service 2>/dev/null || true").Trim()
if ($active -eq "active") { Write-Ok "Sentry service is active." }
else { Write-Warn "Sentry not confirmed active (reported '$active'). Check: ssh $RigSsh 'systemctl status etk.service'" }

# ==========================================================
# STEP 7: STAGE III STABILITY HARNESS  (install.sh Step 7)
# PROF / CORE / S3SVC bodies are pulled verbatim from install.sh —
# Mesa 10G cache cap + ulimit, boot-persistent coredump capture.
# ==========================================================
Write-Step 7 $TOTAL "ARMING STAGE III HARNESS (cache cap + coredump capture)..."
$prof  = Get-Heredoc -Path $InstallSh -Marker "PROF"
$core  = Get-Heredoc -Path $InstallSh -Marker "CORE"
$s3svc = Get-Heredoc -Path $InstallSh -Marker "S3SVC"
Invoke-Rig "mkdir -p /storage/.config/profile.d /storage/.config/custom_scripts /storage/.config/system.d /storage/cores" | Out-Null
Send-Text -Content $prof  -RemotePath "/storage/.config/profile.d/098-etk-stage3"
Send-Text -Content $core  -RemotePath "/storage/.config/custom_scripts/02-etk-coredump.sh" -Executable
Send-Text -Content $s3svc -RemotePath "/storage/.config/system.d/etk-stage3.service"
Invoke-Rig "systemctl daemon-reload; systemctl enable etk-stage3.service 2>/dev/null; systemctl restart etk-stage3.service" | Out-Null
$cpat = (Invoke-Rig "cat /proc/sys/kernel/core_pattern").Trim()
if ($cpat -like "/storage/cores/*") { Write-Ok "Stage III harness armed (core_pattern -> /storage/cores)." }
else { Write-Warn "Stage III harness not confirmed (core_pattern '$cpat') - crash forensics degraded, install continues." }

# ==========================================================
# STEP 8: PADDOCK LINK  (install.sh Step 8 — Private Paddock, 0.3.0)
# Conditional on $PaddockToken in etk-env.ps1. Mirrors the bash step:
# token -> identity -> verify-or-create PRIVATE repo -> seed -> wire rig.
# ==========================================================
Write-Step 8 $TOTAL "PADDOCK LINK (private paddock)..."
if (-not $PaddockToken) {
    Write-Note "No PaddockToken in etk-env.ps1 - private paddock skipped (tab stays hidden)."
} else {
    $ghHdr = @{ Authorization = "Bearer $PaddockToken"; Accept = "application/vnd.github+json"; "User-Agent" = "etk-installer" }
    try {
        $ghUser = (Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $ghHdr -Method Get).login
    } catch { $ghUser = $null }
    if (-not $ghUser) {
        Write-Warn "PADDOCK: token rejected by GitHub - check etk-env.ps1, re-run installer."
    } else {
        $prRepo = if ($PaddockRepo) { $PaddockRepo } else { "$ghUser/etk-paddock" }
        $repoInfo = $null
        try { $repoInfo = Invoke-RestMethod -Uri "https://api.github.com/repos/$prRepo" -Headers $ghHdr -Method Get } catch {}
        if (-not $repoInfo) {
            # Missing: try to create (classic PATs can; fine-grained cannot)
            $body = @{ name = ($prRepo -split '/')[1]; private = $true;
                       description = "ETK Private Paddock - personal vault/save/config storage (not shared)" } | ConvertTo-Json
            try {
                $repoInfo = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Headers $ghHdr -Method Post -Body $body -ContentType "application/json"
                Write-Ok "PADDOCK: created private repo $prRepo"
            } catch {
                Write-Warn "PADDOCK: repo $prRepo missing & token can't create it."
                Write-Warn "         Create it PRIVATE on github.com, then re-run the installer."
            }
        }
        if ($repoInfo) {
            if (-not $repoInfo.private) {
                # A PUBLIC paddock would publicly distribute the vault — refuse.
                Write-Warn "PADDOCK: $prRepo is PUBLIC - refusing. Make it private and re-run."
            } else {
                # Seed an initial commit if empty (release tags need a commit)
                $seeded = $true
                try { Invoke-RestMethod -Uri "https://api.github.com/repos/$prRepo/contents/README.md" -Headers $ghHdr -Method Get | Out-Null }
                catch {
                    $readme = "# ETK Private Paddock`n`nPersonal vault/save/config storage for ETK. Private, not shared, managed by the ETK PADDOCK tab.`n"
                    $seedBody = @{ message = "paddock: seed";
                                   content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($readme)) } | ConvertTo-Json
                    try {
                        Invoke-RestMethod -Uri "https://api.github.com/repos/$prRepo/contents/README.md" -Headers $ghHdr -Method Put -Body $seedBody -ContentType "application/json" | Out-Null
                        Write-Ok "PADDOCK: seeded $prRepo (initial commit)"
                    } catch { $seeded = $false; Write-Warn "PADDOCK: seed failed - first push will fail until the repo has a commit." }
                }
                # Wire the rig: credential file, root-only
                $cred = '{"repo":"' + $prRepo + '","token":"' + $PaddockToken + '"}'
                Invoke-Rig "mkdir -p $EtkRoot/config" | Out-Null
                Send-Text -Content $cred -RemotePath "$EtkRoot/config/paddock.json"
                Invoke-Rig "chmod 600 $EtkRoot/config/paddock.json" | Out-Null
                $wired = (Invoke-Rig "[ -s $EtkRoot/config/paddock.json ] && echo OK").Trim()
                if ($wired -eq "OK") { Write-Ok "PADDOCK connected: $prRepo - tab live on next Pitstop launch." }
                else { Write-Warn "PADDOCK: rig credential write failed - re-run the installer." }
            }
        }
    }
}

Write-Host ""
Write-Host ">>> DEPLOYMENT COMPLETE. REBOOT THE DEVICE TO ACTIVATE ETK PITSTOP IN ROCKNIX TOOLS." -ForegroundColor Green
Write-Note "(EmulationStation reads the Tools gamelist at startup, so the polished"
Write-Note " ETK Pitstop entry appears after a reboot - Update Gamelists does not refresh it.)"
Write-Note "Confirm sentry health: ssh $RigSsh 'systemctl status etk.service'"
Write-Host ""
