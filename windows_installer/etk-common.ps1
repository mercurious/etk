# ==========================================================
# ETK WINDOWS HOST — SHARED HELPERS
# ==========================================================
# Dot-sourced by etk-install.ps1 and etk-uninstall.ps1 AFTER etk-env.ps1.
# Everything here is host-side plumbing. No rig logic lives in this file.
# ==========================================================

# --- scp argument base, derived from env toggles ---
$script:ScpBase = @()
if ($EtkScpLegacy -eq "1") { $script:ScpBase += "-O" }
if ($EtkVerbose   -eq "1") { $script:ScpBase += "-v" } else { $script:ScpBase += "-q" }

# --- coloured console output ---
function Write-Step($n, $total, $msg) { Write-Host ">>> [$n/$total] $msg" -ForegroundColor Cyan }
function Write-Ok  ($msg) { Write-Host "    [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Write-ErrLine($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }
function Write-Note($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

# --- ensure ssh.exe / scp.exe exist (OpenSSH Client) ---
function Assert-Tooling {
    foreach ($t in @("ssh","scp")) {
        if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
            throw "'$t' not found. Install the Windows 'OpenSSH Client' optional feature (Settings > Apps > Optional Features), then re-run."
        }
    }
}

# --- confirm the rig is reachable over SSH before doing anything ---
function Assert-RigConnection {
    Write-Note "Checking SSH connectivity to $RigSsh ..."
    $reply = & ssh -o ConnectTimeout=8 $RigSsh "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -ne 0 -or "$reply" -notmatch "ETK_OK") {
        throw "Cannot reach the rig at '$RigSsh'. Check the device is on, on the same network, RigSsh in etk-env.ps1 is correct, and your SSH key/password works (try: ssh $RigSsh)."
    }
    Write-Ok "Rig reachable."
}

# --- run a remote command, return its stdout (caller may check $LASTEXITCODE) ---
function Invoke-Rig {
    param([Parameter(Mandatory)][string]$Command)
    return (& ssh $RigSsh $Command)
}

# --- normalise text to LF + UTF8-no-BOM in a local temp file; return its path ---
function New-LfTempFile {
    param([Parameter(Mandatory)][string]$Content)
    $tmp = New-TemporaryFile
    $lf  = ($Content -replace "`r`n","`n") -replace "`r","`n"
    [System.IO.File]::WriteAllText($tmp.FullName, $lf, (New-Object System.Text.UTF8Encoding($false)))
    return $tmp.FullName
}

# --- push literal text to a remote path (LF-normalised), optionally chmod +x ---
function Send-Text {
    param(
        [Parameter(Mandatory)][string]$Content,
        [Parameter(Mandatory)][string]$RemotePath,
        [switch]$Executable
    )
    $tmp = New-LfTempFile -Content $Content
    try {
        & scp @ScpBase $tmp "$($RigSsh):$RemotePath"
        if ($LASTEXITCODE -ne 0) { throw "scp to '$RemotePath' failed (exit $LASTEXITCODE)." }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    if ($Executable) { & ssh $RigSsh "chmod +x '$RemotePath'" | Out-Null }
}

# --- push a real local file to a remote path ---
function Send-File {
    param(
        [Parameter(Mandatory)][string]$LocalPath,
        [Parameter(Mandatory)][string]$RemotePath
    )
    if (-not (Test-Path -LiteralPath $LocalPath)) { throw "Local file not found: $LocalPath" }
    & scp @ScpBase $LocalPath "$($RigSsh):$RemotePath"
    if ($LASTEXITCODE -ne 0) { throw "scp '$LocalPath' -> '$RemotePath' failed (exit $LASTEXITCODE)." }
}

# --- push a local directory under a remote parent. -Mirror removes the
#     remote copy first (replicates rsync --delete for that one tree). ---
function Push-Dir {
    param(
        [Parameter(Mandatory)][string]$LocalDir,
        [Parameter(Mandatory)][string]$RemoteParent,
        [switch]$Mirror
    )
    if (-not (Test-Path -LiteralPath $LocalDir)) { throw "Local directory not found: $LocalDir" }
    $leaf = Split-Path $LocalDir -Leaf
    if ($Mirror) { & ssh $RigSsh "rm -rf '$RemoteParent/$leaf'" | Out-Null }
    & scp @ScpBase -r $LocalDir "$($RigSsh):$RemoteParent/"
    if ($LASTEXITCODE -ne 0) { throw "scp -r '$LocalDir' failed (exit $LASTEXITCODE)." }
}

# --- run a block of bash on the rig by shipping it as a temp script.
#     EnvVars are exported as a KEY='val' prefix (used for the unquoted
#     CLEAN heredoc, which expects $ETK_ROOT / $ZAP_VAULT). Returns stdout. ---
function Invoke-RigBash {
    param(
        [Parameter(Mandatory)][string]$Script,
        [hashtable]$EnvVars
    )
    $tmp = New-LfTempFile -Content $Script
    $remote = "/tmp/etk_$([guid]::NewGuid().ToString('N')).sh"
    try {
        & scp @ScpBase $tmp "$($RigSsh):$remote"
        if ($LASTEXITCODE -ne 0) { throw "Could not stage remote script (scp exit $LASTEXITCODE)." }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    $prefix = ""
    if ($EnvVars) { foreach ($k in $EnvVars.Keys) { $prefix += ("{0}='{1}' " -f $k, $EnvVars[$k]) } }
    $out = & ssh $RigSsh ("{0}bash {1}" -f $prefix, $remote)
    $rc = $LASTEXITCODE
    & ssh $RigSsh "rm -f $remote" | Out-Null
    if ($rc -ne 0) { Write-Warn "Remote block exited with code $rc." }
    return $out
}

# --- extract a heredoc body from a bash file, between an opening
#     `<< 'MARKER'` (or `<<MARKER`, quoted or not) and a closing line
#     that is exactly MARKER. This is how the rig-side blobs (SENTRY,
#     SVC, PKGREADME, ETKMAKO, STOP, HW, CLEAN) are pulled verbatim from
#     install.sh / uninstall.sh so there is no second copy to maintain. ---
function Get-Heredoc {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Marker
    )
    if (-not (Test-Path -LiteralPath $Path)) { throw "Source script not found: $Path (run this from the repo root)." }
    $lines = Get-Content -LiteralPath $Path
    $esc = [regex]::Escape($Marker)
    $open = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "<<\s*'?$esc'?(\s|$)") { $open = $i; break }
    }
    if ($open -lt 0) { throw "Opening heredoc <<$Marker not found in $Path." }
    $body = New-Object System.Collections.Generic.List[string]
    for ($j = $open + 1; $j -lt $lines.Count; $j++) {
        if ($lines[$j] -eq $Marker) { return ($body -join "`n") }
        $body.Add($lines[$j]) | Out-Null
    }
    throw "Closing marker '$Marker' (at column 0) not found in $Path."
}

# --- small helper: run a bash command template that contains BOTH a host
#     value and literal rig-side '$'. Pass the template with __ETKROOT__ as
#     the placeholder; it is replaced literally so no PS escaping is needed. ---
function Invoke-RigTemplate {
    param([Parameter(Mandatory)][string]$Template)
    $cmd = $Template -replace "__ETKROOT__", $EtkRoot
    return (Invoke-Rig $cmd)
}
