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
# Also NOT ported, same reason: STEP 6.45 (osguard - it replays 6.4's heal
# bundle) and STEPS 6.552/6.553/6.554 (per-title core catalog, next-boot bind
# manifest, community patch fetch). The INSTALL BEACON below is synced to
# install.sh as of v0.8.7 (2026-08-29); the percent bands those unported
# steps own are simply absent from the card this script draws.
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
# RIG INSTALL BEACON  (install.sh parity, operator-decided 2026-08-29)
# ==========================================================
# An ETK install must be impossible to run without the person holding the
# handheld noticing. From here to the last step the rig's own screen carries
# an "ETK Progress" card: overall percent and the name of the running stage,
# and NOTHING else - no paths, no game ids, no host names.
#
# ALWAYS ON. There is no parameter, no etk-env.ps1 value and no kill-switch,
# by design: a switch is exactly what a silent installer would flip, so the
# only way to install without announcing it is to edit this file. Every other
# optional surface in this port ships with a toggle; this one is the
# deliberate exception.
#
# HONEST ABOUT WHAT IT IS: this is COOPERATIVE ANNOUNCEMENT, not foreign-
# installer detection. It proves THIS installer announced itself. It cannot
# see, and makes no claim about, an installer that chose not to.
#
# Fail-soft: a beacon may NEVER break or block an install. The body swallows
# its own failures (bounded, non-retrying transport calls inside a try/catch),
# so a rig with no bus, no mako, or a half-provisioned $EtkRoot installs
# exactly as it did before - just without the card.
#
# QUOTING: the label reaches the rig inside a single-quoted word in a remote
# shell command. Labels are therefore FIXED ASCII literals written at the call
# sites (<= 40 chars). NEVER interpolate a game id, a path, an etk-env.ps1
# value or anything else operator-supplied into this call -
# tools/release_sanity.sh enforces that statically.
#
# THE PERCENTS AND LABELS ARE install.sh's, VERBATIM, so both installers draw
# the same card on the same rig and an operator can compare them. The port
# skips the vault pull (8-13) and push (20-30) - no host vault on Windows -
# and does not port STEP 6.4/6.45 (44-50) or STEPS 6.552/6.553/6.554 (68-78),
# so those bands never appear. Monotonic non-decreasing to 100 is the
# contract, not a dense sequence.
# ==========================================================
$script:ToastId    = ""      # live notification id; replaces the card in place
$script:ToastSh    = ""      # resolved rig-side sender path ("-" once given up)
$script:ToastStage = ""      # last stage announced - the STOPPED verdict's body
$script:ToastFired = $false  # $true once a terminal verdict went out (guard)

# Backstop for a beacon that precedes a SINGLE bulk transfer with no seam to
# put another beacon in - the ~78 MB AppImage fetch, a driver push. The 45 s
# default would expire mid-transfer and blank the handheld for minutes, which
# is the failure this whole surface exists to prevent; an infinite expire
# would leave an IMMORTAL stale card after a kill. Ten minutes covers these on
# a slow link and still self-clears. Everywhere the code has a seam, the
# answer is another beacon, not a longer window.
$script:ToastBulkMs = 600000

function Invoke-RigToast {
    param(
        [Parameter(Mandatory)][int]$Pct,
        [Parameter(Mandatory)][string]$Stage,
        [int]$TimeoutMs = 45000
    )
    $script:ToastStage = $Stage
    try {
        if ($script:ToastSh -eq "") {
            # VIRGIN RIG: the first beacon fires before STEP 3 has pushed
            # bin/, so on a fresh/reflashed rig the sender does not exist yet.
            # Seed one copy in /tmp and keep using it for the whole run -
            # /tmp survives as long as the boot, which is longer than any
            # install. Such a rig has no [app-name="ETK Progress"] mako block
            # either (STEP 1 writes it), so the card falls back to stock
            # styling; it still shows, which is the point.
            #
            # The probe greps for the `--progress` case label rather than
            # testing for the file, because a rig carrying a sender that
            # PREDATES the progress form is the same problem as a rig carrying
            # none: the flag would fall through to the plain-toast path and
            # post a card summarised "--progress".
            Invoke-Bounded -Exe "ssh" -Arguments ($script:SshBase + @($RigSsh, "grep -q '^--progress)' $EtkRoot/bin/etk_notify.sh")) `
                           -TimeoutSec 20 -Quiet -What "beacon sender probe" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $script:ToastSh = "$EtkRoot/bin/etk_notify.sh"
            } else {
                $script:ToastSh = "-"
                $senderLocal = Join-Path $RepoRoot "bin\etk_notify.sh"
                if (Test-Path -LiteralPath $senderLocal) {
                    # LF-normalise on the way out. install.sh scp's the file
                    # straight across because a Unix checkout is LF; a Windows
                    # clone can carry CRLF, and the rig's sh chokes on it
                    # exactly as the STEP 3 fix-up below exists to prevent.
                    $senderTmp = New-LfTempFile -Content (Get-Content -LiteralPath $senderLocal -Raw)
                    try {
                        Invoke-Bounded -Exe "scp" -Arguments ($script:ScpBase + @($senderTmp, "$($RigSsh):/tmp/etk_notify.sh")) `
                                       -TimeoutSec 30 -Quiet -What "beacon sender seed" | Out-Null
                        if ($LASTEXITCODE -eq 0) { $script:ToastSh = "/tmp/etk_notify.sh" }
                    } finally {
                        Remove-Item $senderTmp -Force -ErrorAction SilentlyContinue
                    }
                }
            }
        }
        if ($script:ToastSh -eq "-") { return }
        # The timeout is a BACKSTOP so an abandoned card self-dismisses - it is
        # NOT the card's lifetime. mako honours expire_timeout (the "ETK
        # Progress" criteria sets no ignore-timeout), so the card survives only
        # as long as the NEXT beacon lands inside this window. That is why the
        # fat stages below are subdivided down to their seams and why a
        # seamless bulk transfer arms $script:ToastBulkMs instead. A stage that
        # outruns its window blanks the handheld, which is the bug this surface
        # exists to prevent.
        $sendId = "0"
        if ($script:ToastId) { $sendId = $script:ToastId }
        $reply = Invoke-Bounded -Exe "ssh" `
                     -Arguments ($script:SshBase + @($RigSsh, "ETK_NOTIFY_ID=$sendId sh $($script:ToastSh) --progress 'ETK INSTALL' '$Stage' $Pct $TimeoutMs")) `
                     -TimeoutSec 20 -Quiet -What "beacon"
        if ($LASTEXITCODE -eq 255) {
            # 255 is ssh's own "could not reach the host" - not the sender's
            # status (a remote command's own exit is 0..254; Windows OpenSSH
            # uses 255 for transport failure exactly as OpenSSH does anywhere).
            # A rig that fell off the network mid-install is the likeliest
            # install failure there is, and every later beacon AND the exit
            # verdict would each pay a full connect timeout: minutes of dead
            # terminal where the installer used to fail fast. Latch the
            # gave-up sentinel so the whole run stops trying after the first.
            #
            # Deliberately NOT latched on a nonzero from the sender itself (a
            # bus or mako hiccup): that costs one fast local round trip, and
            # killing the card for the rest of the install would trade a UX
            # cost for exactly the security property this surface holds.
            $script:ToastSh = "-"
            return
        }
        # ANCHORED digits-only: a rig echoing garbage (an ssh motd, a login
        # banner) must never reach a later command line. An unusable reply
        # leaves the previous id in place; if one was never latched at all the
        # id stays 0, which the sender reads as CREATE NEW - so those cards
        # STACK rather than replace. Acceptable (a visible pile beats a
        # missing card), but it is exactly what stdout pollution looks like.
        $reply = ($reply -join "`n").Trim()
        if ($reply -match '^[0-9]+$') { $script:ToastId = $reply }
    } catch {
        # Invoke-Bounded's wall-clock kill throws, and so would a local file
        # error in the bootstrap above. Both mean this transport is not going
        # to carry a card today, so latch rather than pay the same cost at
        # every remaining stage. Fail-soft: nothing propagates from here.
        $script:ToastSh = "-"
    }
}

# The beacon's other half: a card left behind by an install that died
# mid-flight would go on reporting work that is already over. Close it and say
# so. install.sh hangs this off an EXIT trap; PowerShell has none, so the port
# reaches the same guarantee from the try/finally armed below plus an explicit
# call at its one hard-exit fail site.
function Send-RigToastStopped {
    if ($script:ToastFired) { return }
    $script:ToastFired = $true
    # ""  = no beacon ever ran, so there is no card to close.
    # "-" = no sender, or the rig stopped answering. Either way an ssh here
    #       buys nothing and costs a full connect timeout on the way out.
    if ($script:ToastSh -eq "" -or $script:ToastSh -eq "-") { return }
    # ONE ssh, not two: on a rig that went away this is the difference between
    # one connect timeout and two. `--close 0` is a documented no-op in the
    # sender, so an id that never latched needs no branch here - the verdict
    # still goes out, which is the point (an operator with no end-state toast
    # cannot tell a finished install from an abandoned one).
    $closeId = "0"
    if ($script:ToastId) { $closeId = $script:ToastId }
    try {
        Invoke-Bounded -Exe "ssh" `
            -Arguments ($script:SshBase + @($RigSsh, "sh $($script:ToastSh) --close $closeId; sh $($script:ToastSh) 'ETK INSTALL STOPPED' '$($script:ToastStage)'")) `
            -TimeoutSec 20 -Quiet -What "beacon verdict" | Out-Null
    } catch { }
}

# ==========================================================
# THE VERDICT GUARD. install.sh arms `trap etk_toast_stopped EXIT`; PowerShell
# has no EXIT trap, so the port gets the same GUARANTEE from a try/finally
# spanning the whole install. Every way this script ends passes through it:
# a throw (the port's dominant failure - Invoke-Bounded, Send-Text, Send-File,
# Push-Dir and the Assert-* helpers all throw under $ErrorActionPreference
# 'Stop'), the one explicit `exit 1` fail site (which ALSO sends the verdict
# directly, mirroring install.sh's explicit tui_fail sites), and a normal
# finish (where the COMPLETE verdict has already set the double-fire guard,
# making the finally a no-op).
#
# NEVER masks the exit code: there is no catch, so the original error still
# propagates and powershell.exe still exits non-zero.
#
# The body inside is deliberately NOT re-indented. This port mirrors install.sh
# line for line and is diffed against it by hand at every release; re-indenting
# 500 lines to add a guard would cost more than it buys.
#
# KNOWN LIMIT, the same one install.sh documents for its own Ctrl-C path: a
# hard console kill can skip the finally, and the card then clears on its
# expire backstop instead of showing a verdict.
# ==========================================================
try {

# THE SECURITY MOMENT: the rig SSH probe above has answered, nothing has been
# mutated yet, and the handheld says so before the first pkill.
Invoke-RigToast 2 "ETK install starting"

# ==========================================================
# STEP 0: PROBE & QUIESCE  (install.sh Step 0)
# Kill ETK workers before file ops. Resolve the rig game ID purely to
# provision that game's vault dir; the Sentry creates per-game dirs on
# demand anyway, so the NPUA80075 fallback is fine when idle.
# ==========================================================
Invoke-RigToast 4 "Quiescing daemons"
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
Invoke-RigToast 14 "Deploying daemons"
Write-Step 3 $TOTAL "DEPLOYING GUARDIAN DAEMONS & SCRIPTS..."
Push-Dir -LocalDir (Join-Path $RepoRoot "bin")     -RemoteParent $EtkRoot -Mirror
Push-Dir -LocalDir (Join-Path $RepoRoot "scripts") -RemoteParent $EtkRoot -Mirror

# tools/ entries that run ON the rig (install.sh pushes these selectively —
# the rest of tools/ is host-side; uninstall.sh rm -rf's $ETK_ROOT/tools, so
# each rig-runtime dependency must be deployed here, never one-off scp'd):
#   etk_drift.py   : OS-drift detector
#   vault_sweep.sh : Manage Shaders & Caches engine (missing => "no boundary")
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
    Write-Ok "Manage Shaders & Caches engine (vault_sweep.sh) deployed."
} else {
    Write-Warn "tools\vault_sweep.sh not found locally - the Manage Shaders & Caches screen will show 'no boundary'."
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
Invoke-RigToast 34 "Pitstop interface"
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
    # install.sh sends the verdict at its tui_fail sites for the same
    # reason: a hard stop must not leave the card reporting work that
    # ended. Belt to the finally's braces - the guard makes it fire once.
    Send-RigToastStopped
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
Invoke-RigToast 38 "Arming sentry"
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
Invoke-RigToast 54 "Turnip driver catalog"
Write-Step 6 $TOTAL "STEP 6.5: CUSTOM TURNIP DRIVER CATALOG..."
# Keep these pins in lockstep with install.sh (CERTIFIED_BUILDS / CERT_SHA) — they
# drift when a release rotates and the fetch then 404s down to stock-only (caught
# in the field 2026-07-11: this script still pinned gtk_0.2 while the v0.7.0
# release ships gtk_0.4 and the rig's etk.conf already selected 0.4).
$certified = @(
    @{ Name = "etk_turnip_rocknix_26.2.0_gtk_0.7.so"
       Sha  = "7ed58c2fccafd114fc47aa11b2e2fa3ae676a8ee0242089c0faf1984a480fcb4" }
    @{ Name = "etk_turnip_rocknix_26.2.1_gtk_0.7.so"
       Sha  = "90be699eb62f13b8aea3cc390b2f158ab6b895fc1972468d27b00d8a81983606" }
    @{ Name = "etk_turnip_rocknix_26.3.0-devel-20260821-d2e56df_gtk_0.7.so"
       Sha  = "9e35ed234e5a8f361f91110763479ce42ade72f14d779e22705ed3927c601793" }
    @{ Name = "etk_turnip_rocknix_26.3.0-devel-e40d93a_gtk_0.7.so"
       Sha  = "6f02dec2e2c12d2dbbf6c92b6ea47909f10ec9a49e53d0a510ebd6a868787968" }
    @{ Name = "etk_turnip_rocknix_26.2.0-rc3_gtk_0.7.so"
       Sha  = "0041e22968e4c74157eae902138f0d158cf2089b196c4ee0821f1625f5b4a0ac" }
    @{ Name = "etk_turnip_rocknix_26.1.6_gtk_0.7.so"
       Sha  = "8a16efa627e5c22fb155e16b4b8b7834cfef5b383c307f8cc23f668f7e3b8a14" }
    @{ Name = "etk_turnip_rocknix_26.1.3_gtk_0.4.so"
       Sha  = "6b9c50bf993c10d32941177e7b15868714ef64da7a3bbf28022f8f2fb745045f" }
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
        # [B] heartbeat: a certified-build fetch is a seamless bulk
        # transfer, so it arms the long backstop rather than a beacon.
        Invoke-RigToast 55 "Turnip driver catalog" $script:ToastBulkMs
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
            # [B] heartbeat: the push of one verified .so to the catalog.
            Invoke-RigToast 56 "Turnip driver catalog" $script:ToastBulkMs
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
Invoke-RigToast 60 "RPCS3 core staging"
Write-Step 6 $TOTAL "STEP 6.55: RPCS3 GTK EDITION (default emulator)..."
$certRpcs3    = "rpcs3-etk_gtk-edition-0.9.0.3_armsx3-a74a0f3e0_linux_aarch64.AppImage"  # lockstep with install.sh CERT_RPCS3
$certRpcs3Sha = "8bf606d3740503c580b7fe81b569a7b37513223f6547fc99fff96ba09c0a6437"
$rpcs3StageSrc = $null
if ($Rpcs3AppImage -eq "stock") {
    Write-Note "RPCS3: stock ROCKNIX build selected (etk-env.ps1 opt-out)."
} elseif ([string]::IsNullOrWhiteSpace($Rpcs3AppImage)) {
    # AUTO: certified release build, cached in emulators\ (gitignored) like drivers\.
    $emuDir = Join-Path $RepoRoot "emulators"
    if (-not (Test-Path -LiteralPath $emuDir)) { New-Item -ItemType Directory -Path $emuDir -Force | Out-Null }
    $emuLocal = Join-Path $emuDir $certRpcs3
    if (-not (Test-Path -LiteralPath $emuLocal)) {
        # [B] heartbeat: ~78 MB over the internet, no seam inside it.
        Invoke-RigToast 62 "RPCS3 core staging" $script:ToastBulkMs
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
    # [B] heartbeat: ~78 MB host -> rig, no seam inside it either.
    Invoke-RigToast 65 "RPCS3 core staging" $script:ToastBulkMs
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
Invoke-RigToast 82 "Support services"
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
Invoke-RigToast 84 "Support services"
$pwrBody = Get-Heredoc -Path $InstallSh -Marker "POWERREMOTE"
Invoke-RigBash -Script $pwrBody | Out-Null
Write-Ok "POWER applier deployed (etk-power.service - self-skips until a profile is set)."

# --- STEP 6.65: PANIC BLACK BOX (read side)  (install.sh Step 6.65) -----
# pstore harvester + kmsg flight recorder unit. The WRITE side (ramoops
# grub token) stays operator-armed via scripts/arm_blackbox.sh — never
# auto-applied. The body also reports grub-drift warnings; print them.
Invoke-RigToast 86 "Support services"
$bbBody = Get-Heredoc -Path $InstallSh -Marker "BLACKBOXREMOTE"
$bbOut = Invoke-RigBash -Script $bbBody
if ($bbOut) { $bbOut | ForEach-Object { Write-Note $_ } }
Write-Ok "Panic Black Box read-side deployed (write-side stays operator-armed)."

# --- STEP 6.7: DP-MIRROR DAEMON  (install.sh Step 6.7) ------------------
# Mirrors the internal panel to an external DisplayPort for capture/OBS.
# Idle until a DP sink links; toggled by ETK_DP_MIRROR in the generated
# etk.conf (Step 3). DPMIRRORREMOTE verbatim.
Invoke-RigToast 88 "Support services"
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
Invoke-RigToast 90 "Stability harness"
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
Invoke-RigToast 94 "Storage rebind"
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
Invoke-RigToast 96 "Paddock link"
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

# ==========================================================
# BEACON: the COMPLETE verdict. Full bar, then the card goes - a progress card
# left to fade keeps reporting work that is already over - then one line on the
# ETK verdict surface so the handheld shows an unambiguous end state. Setting
# the double-fire guard here also disarms the STOPPED path for good, so nothing
# produced downstream (the finally below, PowerShell's own teardown) can
# contradict a successful install.
# ==========================================================
Invoke-RigToast 100 "Complete"
$script:ToastFired = $true
# Gated on a resolved SENDER, never on a latched id: an id that failed to latch
# (an ssh banner on stdout, say) would otherwise cost the operator the one toast
# that says the install is over. `--close 0` is a no-op in the sender, so the
# close simply does nothing when there is no card to close. One ssh, same
# reasoning as the STOPPED path.
if ($script:ToastSh -ne "" -and $script:ToastSh -ne "-") {
    $doneId = "0"
    if ($script:ToastId) { $doneId = $script:ToastId }
    try {
        Invoke-Bounded -Exe "ssh" `
            -Arguments ($script:SshBase + @($RigSsh, "sh $($script:ToastSh) --close $doneId; sh $($script:ToastSh) 'ETK INSTALL COMPLETE'")) `
            -TimeoutSec 20 -Quiet -What "beacon verdict" | Out-Null
    } catch { }
}

Write-Host ""
Write-Host ">>> DEPLOYMENT COMPLETE. REBOOT THE DEVICE TO ACTIVATE ETK PITSTOP IN ROCKNIX TOOLS." -ForegroundColor Green
Write-Note "(EmulationStation reads the Tools gamelist at startup, so the polished"
Write-Note " ETK Pitstop entry appears after a reboot - Update Gamelists does not refresh it.)"
Write-Note "(The reboot also loads any newly selected Turnip driver build and the"
Write-Note " custom RPCS3 GTK Edition bind - both are boot-gated by design.)"
Write-Note "Confirm sentry health: ssh $RigSsh 'systemctl status etk.service'"
Write-Host ""

} finally {
    # Reached on a throw, on the explicit `exit 1`, and on a clean finish. The
    # double-fire guard makes it a no-op after the COMPLETE verdict above.
    Send-RigToastStopped
}
