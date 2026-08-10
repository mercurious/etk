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
# The Sentry, systemd units, rig-side step bodies (Turnip catalog, RPCS3
# GTK Edition bind, watchdog TEARDOWN, POWER applier, Panic Black Box,
# DP-mirror, Stage III, SD rebind), PKG drop README, and mako style are
# read verbatim from install.sh at runtime (Get-Heredoc), so install.sh
# stays the single source of truth for everything that runs on the rig.
# Synced to install.sh as of v0.7.x (2026-07-22). NOT ported by design:
# STEP 6.4 (custom kernel + grub entries incl. the SD-boot pair) — it is
# gated on a locally built KERNEL_IMAGE artifact that Windows hosts don't
# have; the GTK kernel reaches a rig via the flashable card image instead.
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
# Resolve the .local rig name ONCE and pin the session to its IP — every
# later ssh/scp then skips Windows mDNS (whose mid-run stalls froze installs).
Resolve-RigHost
# Establish passwordless SSH first (test-first + idempotent; <=1 password on a
# fresh rig, zero thereafter). Without this every ssh/scp below would prompt.
Invoke-EtkPair
Assert-RigConnection

# LIVE-SESSION GUARD (install.sh parity, guard 49a0aa1): NEVER deploy over an
# active race — Step 0 kills the telemetry daemons and Step 6 restarts the
# Sentry mid-game; the running session's ledger baseline (and the driver's
# race) are the casualties. The bracketed pgrep pattern defeats the
# ssh-cmdline self-match.
$liveCheck = Invoke-Rig 'pgrep -f "AppRun.wrappe[d]|rpcs3-s[a]" >/dev/null 2>&1 && echo ETK_LIVE || echo ETK_IDLE'
if ("$liveCheck" -match "ETK_LIVE") {
    throw "A game session is RUNNING on the rig - install refused. Deploying now would kill live telemetry and could cost the current race. Finish or exit the game, then re-run."
}

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
    $PkgStaging, $FirmwareDrop,
    "/storage/.config/custom_scripts", "/storage/.config/system.d",
    "/storage/.config/modules", "/storage/.config/MangoHud",
    "/storage/turnip/drivers", "/storage/rpcs3", "/storage/etk-power",
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

# Firmware drop-folder README (verbatim from install.sh FWREADME heredoc);
# the folder feeds Pitstop TOOLS > Install PS3 Firmware (headless --installfw).
$fwReadme = Get-Heredoc -Path $InstallSh -Marker "FWREADME"
Send-Text -Content $fwReadme -RemotePath "$FirmwareDrop/README.txt"
Write-Ok "Firmware drop-folder README written."

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

# tools/ entries that run ON the rig (install.sh pushes these selectively —
# the rest of tools/ is host-side; uninstall.sh rm -rf's $ETK_ROOT/tools, so
# each rig-runtime dependency must be deployed here, never one-off scp'd):
#   etk_drift.py   : OS-drift detector
#   vault_sweep.sh : Manage Shaders engine (missing => "no boundary" screen)
#   wl-mirror      : aarch64 mirror binary used by bin/dpmirror_d.sh
$driftLocal = Join-Path $RepoRoot "tools\etk_drift.py"
if (Test-Path -LiteralPath $driftLocal) {
    Send-File -LocalPath $driftLocal -RemotePath "$EtkRoot/tools/etk_drift.py"
    Write-Ok "OS-drift detector (etk_drift.py) deployed."
} else {
    Write-Warn "tools\etk_drift.py not found locally - skipping (verify the path)."
}
$sweepLocal = Join-Path $RepoRoot "tools\vault_sweep.sh"
if (Test-Path -LiteralPath $sweepLocal) {
    Send-File -LocalPath $sweepLocal -RemotePath "$EtkRoot/tools/vault_sweep.sh"
    Write-Ok "Manage Shaders engine (vault_sweep.sh) deployed."
} else {
    Write-Warn "tools\vault_sweep.sh not found locally - the Manage Shaders screen will show 'no boundary'."
}
$wlLocal = Join-Path $RepoRoot "tools\rocknix-bin\wl-mirror"
if (Test-Path -LiteralPath $wlLocal) {
    Send-File -LocalPath $wlLocal -RemotePath "$EtkRoot/tools/wl-mirror"
    Write-Ok "DP capture-mirror binary (wl-mirror) deployed."
} else {
    Write-Warn "tools\rocknix-bin\wl-mirror not found locally - DP mirror will be unavailable."
}

$mangoLocal = Join-Path $RepoRoot "config\MangoHud.conf"
if (Test-Path -LiteralPath $mangoLocal) {
    Send-File -LocalPath $mangoLocal -RemotePath "/storage/.config/MangoHud/MangoHud.conf"
} else {
    Write-Warn "config\MangoHud.conf not found locally - skipping overlay push (verify the filename)."
}

# Rig-side operator config: the bash installer pushes the repo-root etk.conf
# (the rig's env.sh sources $ETK_ROOT/etk.conf for tier/HUD/DP toggles). The
# Windows port has no etk.conf, so generate a minimal one from etk-env.ps1.
$etkConf = @(
    "# Generated by the ETK Windows installer - edit windows_installer\etk-env.ps1, not this file.",
    "ETK_BUILD_TYPE=`"$EtkBuildType`"",
    "DEFAULT_MODE=`"$DefaultMode`"",
    "ETK_HUD_MODE=`"$EtkHudMode`"",
    "ETK_DP_MIRROR=`"$EtkDpMirror`"",
    "ETK_DP_AUDIO_S16=`"$EtkDpAudioS16`"",
    "HUD_HEADER_HOLD_S=`"$HudHeaderHold`""
) -join "`n"
Send-Text -Content ($etkConf + "`n") -RemotePath "$EtkRoot/etk.conf"
Write-Ok "Operator config (etk.conf) generated on the rig from etk-env.ps1 values."

Invoke-Rig "chmod +x $EtkRoot/bin/* $EtkRoot/scripts/* $EtkRoot/tools/etk_drift.py $EtkRoot/tools/vault_sweep.sh $EtkRoot/tools/wl-mirror 2>/dev/null; true" | Out-Null

# WINDOWS-SPECIFIC: strip any CRLF from shell scripts that a Windows clone
# may have introduced. install.sh relies on a Linux/Mac checkout being LF;
# here we normalise on the rig so the daemons actually run. (See the
# .gitattributes recommendation in WINDOWS_HOST_README.md.)
$crlfFix = @'
for f in __ETKROOT__/bin/*.sh __ETKROOT__/bin/*.py __ETKROOT__/scripts/*.sh __ETKROOT__/tools/*.py __ETKROOT__/tools/*.sh __ETKROOT__/etk.conf; do
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
# etk_template.yml, power_profiles.json, etk_pitstop.svg, launcher master, ...).
Push-Dir -LocalDir (Join-Path $RepoRoot "config") -RemoteParent $EtkRoot

# PADDOCK injector (0.5.0): the rig-side Pro Tuning installer the Pitstop
# PADDOCK tab shells out to. Mirrors install.sh Step 4 — deploy ONLY
# install-protune.sh (export.sh is the host-only producer, not deployed).
# CRLF-strip + chmod so a Windows checkout's script
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
# STEP 6.5: CUSTOM TURNIP DRIVER CATALOG  (install.sh Step 6.5)
# Host side: fetch the certified GTK build(s) from the latest ETK GitHub
# release, sha256-verify, stage into the on-rig catalog, prune stale
# non-certified .so leftovers. Rig side: the TURNIPREMOTE body (bind
# resolver + oneshot unit + default-pick seed) runs verbatim from
# install.sh. Reboot-gated: a bind-mount can't hot-swap a driver a
# running RPCS3 already mapped.
# ==========================================================
Write-Step 6 $TOTAL "STEP 6.5: CUSTOM TURNIP DRIVER CATALOG..."
# Keep these pins in lockstep with install.sh (CERTIFIED_BUILDS / CERT_SHA) — they
# drift when a release rotates and the fetch then 404s down to stock-only (caught
# in the field 2026-07-11: this script still pinned gtk_0.2 while the v0.7.0
# release ships gtk_0.4 and the rig's etk.conf already selected 0.4).
$certified = @(
    @{ Name = "etk_turnip_rocknix_26.2.0_gtk_0.7.so"
       Sha  = "8a16efa627e5c22fb155e16b4b8b7834cfef5b383c307f8cc23f668f7e3b8a14" }
    @{ Name = "etk_turnip_rocknix_26.3.0-devel-e40d93a_gtk_0.7.so"
       Sha  = "0041e22968e4c74157eae902138f0d158cf2089b196c4ee0821f1625f5b4a0ac" }
)
$driverBase = "https://github.com/mercurious/etk/releases/latest/download"
$turnipKeep = @("stock")
$staged = 0
$driversDir = Join-Path $RepoRoot "drivers"
if (-not (Test-Path -LiteralPath $driversDir)) { New-Item -ItemType Directory -Path $driversDir -Force | Out-Null }
foreach ($cb in $certified) {
    $local = Join-Path $driversDir $cb.Name
    if (-not (Test-Path -LiteralPath $local)) {
        # drivers/ is gitignored, so a fresh clone has no .so — fetch it.
        Write-Note "Fetching certified driver $($cb.Name) from the latest ETK release..."
        try {
            Invoke-WebRequest -Uri "$driverBase/$($cb.Name)" -OutFile $local -UseBasicParsing
        } catch {
            Remove-Item $local -Force -ErrorAction SilentlyContinue
            Write-Warn "Could not fetch $($cb.Name) (offline or asset missing) - skipping."
        }
    }
    if (Test-Path -LiteralPath $local) {
        $got = (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($got -ne $cb.Sha) {
            Remove-Item $local -Force
            Write-Warn "$($cb.Name) FAILED sha256 verification - refusing to stage (expected $($cb.Sha))."
        } else {
            Send-File -LocalPath $local -RemotePath "/storage/turnip/drivers/$($cb.Name)"
            $staged++
            $turnipKeep += $cb.Name
        }
    }
}
if ($staged -gt 0) { Write-Ok "Staged $staged certified Turnip build(s) -> catalog (stock + proven fork)" }
else { Write-Warn "No certified Turnip build available - catalog will be stock-only." }

# Optional out-of-tree default build (mirrors etk.conf TURNIP_SO).
$turnipDefaultSel = "stock"
if ($TurnipSo -and (Test-Path -LiteralPath $TurnipSo)) {
    $soName = Split-Path $TurnipSo -Leaf
    Send-File -LocalPath $TurnipSo -RemotePath "/storage/turnip/drivers/$soName"
    $turnipDefaultSel = $soName
    $turnipKeep += $soName
    Write-Ok "Default driver build: $soName"
}

# Prune non-certified .so left from prior dev installs (catalog = stock + certified).
$prune = @'
for f in /storage/turnip/drivers/*.so; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  case " $KEEP " in *" $b "*) : ;; *) rm -f "$f" ;; esac
done
true
'@
Invoke-RigBash -Script $prune -EnvVars @{ KEEP = ($turnipKeep -join " ") } | Out-Null

$turnipBody = Get-Heredoc -Path $InstallSh -Marker "TURNIPREMOTE"
$tOut = Invoke-RigBash -Script $turnipBody -EnvVars @{ TURNIP_DEFAULT_SEL = $turnipDefaultSel }
$tOk = ($tOut | Where-Object { $_ -match "TURNIP_OK" } | Select-Object -First 1)
if ($tOk) { Write-Ok ($tOk -replace "TURNIP_OK ", "") }
else {
    Write-Warn "Turnip selector deploy: no status marker."
    if ($tOut) { $tOut | ForEach-Object { Write-Note $_ } }
}

# ==========================================================
# STEP 6.55: RPCS3 GTK EDITION (default emulator)  (install.sh Step 6.55)
# As of 0.6.0 GA the certified GTK Edition is the DEFAULT — fetched from the
# ETK release + sha256-verified, zero config (same pattern as the driver in
# Step 6.5). $Rpcs3AppImage semantics: "" = AUTO (default); "stock" = opt out;
# a path = local dev build. Fail-soft: fetch/verify failure warns and falls
# back to stock. Bind-mount over read-only /usr/bin/rpcs3-sa — stock is never
# overwritten. RPCS3REMOTE body verbatim.
# ==========================================================
Write-Step 6 $TOTAL "STEP 6.55: RPCS3 GTK EDITION (default emulator)..."
$certRpcs3    = "rpcs3-etk_gtk-edition-0.8.5_v0.0.41-19638-a1deb2921_linux_aarch64.AppImage"  # lockstep with install.sh CERT_RPCS3
$certRpcs3Sha = "9a8a4fd7aec8937d964594cc1be635b88129e768cb97b2f24fc291b92ed3c7ac"
$rpcs3StageSrc = $null
if ($Rpcs3AppImage -eq "stock") {
    Write-Note "RPCS3: stock ROCKNIX build selected (etk-env.ps1 opt-out)."
} elseif ([string]::IsNullOrWhiteSpace($Rpcs3AppImage)) {
    # AUTO: certified release build, cached in emulators\ (gitignored) like drivers\.
    $emuDir = Join-Path $RepoRoot "emulators"
    if (-not (Test-Path -LiteralPath $emuDir)) { New-Item -ItemType Directory -Path $emuDir -Force | Out-Null }
    $emuLocal = Join-Path $emuDir $certRpcs3
    if (-not (Test-Path -LiteralPath $emuLocal)) {
        Write-Note "Fetching RPCS3 GTK Edition from the latest ETK release (~78MB, one-time)..."
        try {
            # PS 5.1's IWR progress rendering throttles large downloads
            # brutally (and shows nothing useful under iex) — silence it
            # for speed and print an explicit done-line instead.
            $pp = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
            try { Invoke-WebRequest -Uri "$driverBase/$certRpcs3" -OutFile $emuLocal -UseBasicParsing }
            finally { $ProgressPreference = $pp }
            Write-Note ("Downloaded {0:N1} MB." -f ((Get-Item $emuLocal).Length / 1MB))
        } catch {
            Remove-Item $emuLocal -Force -ErrorAction SilentlyContinue
            Write-Warn "Could not fetch the GTK Edition (offline or asset missing) - stock RPCS3 stays active."
        }
    }
    if (Test-Path -LiteralPath $emuLocal) {
        $got = (Get-FileHash -LiteralPath $emuLocal -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($got -ne $certRpcs3Sha) {
            Remove-Item $emuLocal -Force
            Write-Warn "GTK Edition FAILED sha256 verification - refusing to stage (stock stays active)."
        } else {
            $rpcs3StageSrc = $emuLocal
        }
    }
} elseif (Test-Path -LiteralPath $Rpcs3AppImage) {
    $rpcs3StageSrc = $Rpcs3AppImage
} else {
    Write-Warn "Rpcs3AppImage set but file not found: $Rpcs3AppImage - stock stays active."
}
if ($rpcs3StageSrc) {
    Send-File -LocalPath $rpcs3StageSrc -RemotePath "/storage/rpcs3/rpcs3-sa.custom"
    # A Windows file carries no Linux exec bit — set it on the rig copy
    # (an AppImage without +x fails as a silent exit-126 "quits on launch").
    Invoke-Rig "chmod 755 /storage/rpcs3/rpcs3-sa.custom" | Out-Null
    Write-Ok ("Staged RPCS3 build: " + (Split-Path $rpcs3StageSrc -Leaf))
} else {
    Invoke-Rig "rm -f /storage/rpcs3/rpcs3-sa.custom" | Out-Null
}
$rpcs3Body = Get-Heredoc -Path $InstallSh -Marker "RPCS3REMOTE"
$rOut = Invoke-RigBash -Script $rpcs3Body
$rOk = ($rOut | Where-Object { $_ -match "RPCS3_OK" } | Select-Object -First 1)
if ($rOk) { Write-Ok (($rOk -replace "RPCS3_OK ", "") + " (reboot to fully validate)") }
else {
    Write-Warn "RPCS3 custom-build deploy: no status marker."
    if ($rOut) { $rOut | ForEach-Object { Write-Note $_ } }
}

# --- STEP 6.56: RPCS3 runtime env flags  (install.sh Step 6.56) ---------
# profile.d vector; reaches the RPCS3 runtime at every game launch. For the
# GTK Edition set $Rpcs3EnvFlags = "GTK_REMAP0_ONE=1" (road-flicker fix).
if ($Rpcs3EnvFlags) {
    $exports = (($Rpcs3EnvFlags.Trim() -split '\s+') | ForEach-Object { "export $_" }) -join "`n"
    Send-Text -Content ($exports + "`n") -RemotePath "/storage/.config/profile.d/096-etk-rpcs3-flags"
    Write-Ok "RPCS3 env flags injected: $Rpcs3EnvFlags"
} else {
    Invoke-Rig "rm -f /storage/.config/profile.d/096-etk-rpcs3-flags" | Out-Null
}

# --- STEP 6.57: RETIRE the audio watchdog  (install.sh Step 6.57) -------
# The SM8250 silent-boot probe race is fixed at the ROOT in the ROCKNIX-GTK
# kernel (q6afe vote retry, 2026-07-06), so the userspace watchdog is
# RETIRED. AUDIOWDREMOTE now contains the TEARDOWN body (same marker name,
# content auto-synced) — it removes the unit/script from rigs that still
# carry them from an earlier install; a no-op once gone.
$wdBody = Get-Heredoc -Path $InstallSh -Marker "AUDIOWDREMOTE"
Invoke-RigBash -Script $wdBody | Out-Null
Write-Ok "Audio watchdog retired (silent-boot race fixed in the GTK kernel)."

# --- STEP 6.6: POWER PROFILE APPLIER  (install.sh Step 6.6) -------------
# Boot oneshot re-applying the Pitstop POWER tab's gov/clock profile
# (sysfs resets every boot). Self-skips until a profile is set.
$pwrBody = Get-Heredoc -Path $InstallSh -Marker "POWERREMOTE"
Invoke-RigBash -Script $pwrBody | Out-Null
Write-Ok "POWER applier deployed (etk-power.service - self-skips until a profile is set)."

# --- STEP 6.65: PANIC BLACK BOX (read side)  (install.sh Step 6.65) -----
# pstore harvester + kmsg flight recorder unit. The WRITE side (ramoops
# grub token) stays operator-armed via scripts/arm_blackbox.sh — never
# auto-applied. The body also reports grub-drift warnings; print them.
$bbBody = Get-Heredoc -Path $InstallSh -Marker "BLACKBOXREMOTE"
$bbOut = Invoke-RigBash -Script $bbBody
if ($bbOut) { $bbOut | ForEach-Object { Write-Note $_ } }
Write-Ok "Panic Black Box read-side deployed (write-side stays operator-armed)."

# --- STEP 6.7: DP-MIRROR DAEMON  (install.sh Step 6.7) ------------------
# Mirrors the internal panel to an external DisplayPort for capture/OBS.
# Idle until a DP sink links; toggled by ETK_DP_MIRROR in the generated
# etk.conf (Step 3). DPMIRRORREMOTE verbatim.
$dpBody = Get-Heredoc -Path $InstallSh -Marker "DPMIRRORREMOTE"
Invoke-RigBash -Script $dpBody | Out-Null
Write-Ok "DP-mirror daemon deployed (idle until an external DisplayPort links)."

# --- STEP 6.75: DP CAPTURE-AUDIO FORMAT PIN  (install.sh Step 6.75) -----
# WirePlumber rule pinning the HDMI/DP capture sink to S16LE — the DP port's
# S24 path loses ~25 dB (see config/wireplumber-dp-s16.conf for the record).
# Deploy-on-change so routine installs never blip the rig's audio.
$wpConfLocal = Join-Path $RepoRoot "config\wireplumber-dp-s16.conf"
$wpConfRig   = "/storage/.config/wireplumber/wireplumber.conf.d/50-etk-dp-audio-s16.conf"
if (($EtkDpAudioS16 -ne "0") -and (Test-Path -LiteralPath $wpConfLocal)) {
    $newConf = (Get-Content -LiteralPath $wpConfLocal -Raw)
    $oldConf = (Invoke-Rig "cat '$wpConfRig' 2>/dev/null") -join "`n"
    if ($oldConf.Trim() -ne $newConf.Trim()) {
        Invoke-Rig "mkdir -p /storage/.config/wireplumber/wireplumber.conf.d" | Out-Null
        Send-Text -Content $newConf -RemotePath $wpConfRig
        Invoke-Rig "systemctl restart wireplumber 2>/dev/null" | Out-Null
        Write-Ok "DP capture-audio S16 pin deployed (WirePlumber bounced)."
    } else {
        Write-Ok "DP capture-audio S16 pin already current."
    }
} else {
    Invoke-Rig "[ -f '$wpConfRig' ] && { rm -f '$wpConfRig'; systemctl restart wireplumber 2>/dev/null; } || true" | Out-Null
}

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
# Same acceptance as install.sh's STAGE3_OK check: the CORE body prefers the
# SD-backed $ETK_ROOT/cores and falls back to legacy /storage/cores.
if ($cpat -match '^/storage/(games-internal/roms/etk/)?cores/') { Write-Ok "Stage III harness armed (core_pattern -> $($cpat -replace '/%.*$',''))." }
else { Write-Warn "Stage III harness not confirmed (core_pattern '$cpat') - crash forensics degraded, install continues." }

# --- STEP 6.85: SD GAME-TREE REBIND  (install.sh Step 6.85) -------------
# Crash-card storage model: internal-boot rig, games on an SD labelled
# SDGAMES. RBND (label-based v3 script, exits 0 fast with no card — no UI
# stall) + RBSVC (unit) pulled verbatim from install.sh. Deliberately NOT
# run at install time (it bind-mounts over live game paths); it takes
# effect on the next cold boot. No-op on single-card rigs.
Write-Step 7 $TOTAL "STEP 6.85: SD GAME-TREE REBIND (crash-card storage model)..."
$rbnd  = Get-Heredoc -Path $InstallSh -Marker "RBND"
$rbsvc = Get-Heredoc -Path $InstallSh -Marker "RBSVC"
Send-Text -Content $rbnd  -RemotePath "/storage/.config/custom_scripts/etk-sd-rebind.sh" -Executable
Send-Text -Content $rbsvc -RemotePath "/storage/.config/system.d/etk-sd-rebind.service"
$rbOut = Invoke-Rig "systemctl daemon-reload; systemctl enable etk-sd-rebind.service >/dev/null 2>&1; systemctl reset-failed etk-sd-rebind.service >/dev/null 2>&1; [ -x /storage/.config/custom_scripts/etk-sd-rebind.sh ] && systemctl is-enabled etk-sd-rebind.service >/dev/null 2>&1 && echo REBIND_OK || echo REBIND_FAIL"
if ("$rbOut" -match "REBIND_OK") { Write-Ok "SD rebind deployed (label=SDGAMES; effective next cold boot)." }
else { Write-Warn "SD rebind service did not verify - crash-card rigs unaffected until fixed; install continues." }

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
Write-Note "(The reboot also loads any newly selected Turnip driver build and the"
Write-Note " custom RPCS3 GTK Edition bind - both are boot-gated by design.)"
Write-Note "Confirm sentry health: ssh $RigSsh 'systemctl status etk.service'"
Write-Host ""
