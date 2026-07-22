# ==========================================================
# ETK WINDOWS HOST - SHARED HELPERS
# ==========================================================
# Dot-sourced by etk-install.ps1 and etk-uninstall.ps1 AFTER etk-env.ps1.
# Everything here is host-side plumbing. No rig logic lives in this file.
# ==========================================================

# --- shared ssh/scp transport options ---
# ConnectTimeout: a fresh TCP connect must succeed in 10 s or fail loudly —
# without it, an SM8250.local mDNS stall or a rig WiFi nap (the known iwd
# churn) hangs a step indefinitely (field-hit 2026-07-22: install froze at
# step 0 with the connection test having passed moments earlier).
# ServerAlive*: an ESTABLISHED session that goes quiet dies in ~15 s
# instead of hanging a mid-step transfer forever.
# accept-new: first contact with a session-pinned IP must not stall on a
# hidden fingerprint prompt inside a piped call; a CHANGED key still refuses.
$script:SshBase = @("-o","ConnectTimeout=10",
                    "-o","ServerAliveInterval=5","-o","ServerAliveCountMax=3",
                    "-o","StrictHostKeyChecking=accept-new")

# --- scp argument base, derived from env toggles ---
$script:ScpBase = @() + $script:SshBase
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

# --- resolve a .local rig name ONCE, pin the session's wire address ---
# Windows mDNS can work beautifully — when it works. Every ssh/scp call
# opens a fresh connection and would re-resolve the .local name each time;
# one stall mid-install freezes a step (field-hit twice, 2026-07-22).
# So: consult mDNS exactly once per run (with retries, so a momentary
# blip doesn't force IP-pinning on the user), then pin the TRANSPORT via
# `-o HostName=<ip>` on the shared option bases. The .local name stays the
# ssh-visible identity — config-block matching (Host SM8250.local ...),
# known_hosts keying, and pairing all keep working across DHCP drift.
# Zero configuration kept. Fail-soft: if it never resolves, continue with
# the name and let the per-call ConnectTimeout surface problems loudly.
function Resolve-RigHost {
    $rigHost = ($RigSsh -split '@', 2)[-1]
    if ($rigHost -notmatch '\.local$') { return }   # literal IP/DNS name: nothing to do
    for ($i = 1; $i -le 3; $i++) {
        try {
            # Async + bounded wait: the resolver itself is what stalls on a
            # bad mDNS day, so each attempt gets 5 s, not the system default.
            $task = [System.Net.Dns]::GetHostAddressesAsync($rigHost)
            if (-not $task.Wait(5000)) { throw "resolve timeout" }
            $v4 = $task.Result |
                  Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                  Select-Object -First 1
            if ($v4) {
                $ip = $v4.IPAddressToString
                $script:SshBase += @("-o","HostName=$ip")
                $script:ScpBase += @("-o","HostName=$ip")
                Write-Note "Resolved $rigHost -> $ip (wire address pinned for this session)"
                return
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-Warn "$rigHost did not resolve after 3 tries - continuing with the name; if a step fails to connect, check the rig's WiFi (or set an IP in etk-env.ps1 as a last resort)."
}

# --- confirm the rig is reachable over SSH before doing anything ---
function Assert-RigConnection {
    Write-Note "Checking SSH connectivity to $RigSsh ..."
    $reply = & ssh -o ConnectTimeout=8 @SshBase $RigSsh "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -ne 0 -or "$reply" -notmatch "ETK_OK") {
        throw "Cannot reach the rig at '$RigSsh'. Check the device is on, on the same network, RigSsh in etk-env.ps1 is correct, and your SSH key/password works (try: ssh $RigSsh)."
    }
    Write-Ok "Rig reachable."
}

# --- run a remote command, return its stdout (caller may check $LASTEXITCODE) ---
function Invoke-Rig {
    param([Parameter(Mandatory)][string]$Command)
    # .ps1 files are CRLF (enforced by .gitattributes: *.ps1 eol=crlf), so any
    # MULTI-LINE command literal (e.g. a here-string for/sed loop) carries \r.
    # Sent verbatim, the rig's sh chokes ("syntax error near `do\r'") and a
    # trailing \r turns a path into 'file.sh\r' -> "No such file or directory".
    # Strip CR so multi-line remote commands run; single-line commands are
    # unaffected. (Send-Text/Invoke-RigBash already LF-normalise via temp file.)
    $Command = $Command -replace "`r", ""
    return (& ssh @SshBase $RigSsh $Command)
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
    if ($Executable) { & ssh @SshBase $RigSsh "chmod +x '$RemotePath'" | Out-Null }
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
    if ($Mirror) { & ssh @SshBase $RigSsh "rm -rf '$RemoteParent/$leaf'" | Out-Null }
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
    $out = & ssh @SshBase $RigSsh ("{0}bash {1}" -f $prefix, $remote)
    $rc = $LASTEXITCODE
    & ssh @SshBase $RigSsh "rm -f $remote" | Out-Null
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

# ==========================================================
# SSH PAIRING (host -> rig) — PowerShell mirror of scripts/etk_pair.sh
# ==========================================================
# Establishes passwordless auth BEFORE any deploy. Test-first + idempotent:
# an already-reachable host pays ZERO passwords; a fresh/reflashed rig pairs
# with exactly one. The rig-side key-install logic is NOT duplicated here —
# it is pulled verbatim from scripts/etk_pair.sh's ETKPAIRKEY heredoc via
# Get-Heredoc (the single source of truth shared with install.sh) and shipped
# in ONE ssh call, base64-wrapped so no PowerShell newline/quoting/CRLF can
# corrupt it in transit. The pubkey arrives as the $ETK_PUBKEY env var, which
# is exactly how the bash path feeds the same body.
# ==========================================================

# Append the marker-guarded ETK Host block (never clobbers; honours §G.4).
# The multi-pattern `Host <host> etk-rig` is what makes the BARE target
# (root@<host>, which install.sh/the PS port actually call) offer the
# dedicated key. ~/.ssh/etk_rig is portable across Windows OpenSSH, Git's
# ssh, and Mac/Linux.
function Write-EtkSshConfigBlock {
    param(
        [Parameter(Mandatory)][string]$CfgFile,
        [Parameter(Mandatory)][string]$Marker,
        [Parameter(Mandatory)][string]$RigHost,
        [Parameter(Mandatory)][string]$RigUser
    )
    $sshDir = Split-Path $CfgFile -Parent
    if (-not (Test-Path -LiteralPath $sshDir)) {
        New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
    }
    $existing = ""
    if (Test-Path -LiteralPath $CfgFile) {
        $existing = [System.IO.File]::ReadAllText($CfgFile)
        if ($existing.Contains($Marker)) { return }
    }
    $block = @(
        $Marker,
        "Host $RigHost etk-rig",
        "    HostName $RigHost",
        "    User $RigUser",
        "    IdentityFile ~/.ssh/etk_rig",
        "    IdentitiesOnly yes"
    ) -join "`n"
    $sep = ""
    if ($existing.Length -gt 0) { $sep = "`n" }
    $out = $existing + $sep + $block + "`n"
    [System.IO.File]::WriteAllText($CfgFile, $out, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "Added ETK block to $CfgFile (routes $RigHost -> etk_rig)."
}

function Invoke-EtkPair {
    param([string]$Target = $RigSsh)

    # ssh.exe writes to stderr on a (deliberately) failed BatchMode probe.
    # Under the caller's $ErrorActionPreference='Stop', PowerShell turns that
    # native stderr into a TERMINATING NativeCommandError even with 2>$null —
    # which would abort the wizard at its first probe. We drive all control
    # flow off $LASTEXITCODE and explicit throws, so relax EAP here. This is
    # function-scoped (auto-restored on return) and 'throw' still terminates.
    $ErrorActionPreference = 'Continue'

    if ([string]::IsNullOrWhiteSpace($Target)) {
        throw "No rig target. Set `$RigSsh in windows_installer\etk-env.ps1 (e.g. root@SM8250.local)."
    }

    # Normalise user@host; default user root if a bare host was given.
    if ($Target -match '@') {
        $parts = $Target -split '@', 2
        $rigUser = $parts[0]; $rigHost = $parts[1]
    } else {
        $rigUser = 'root'; $rigHost = $Target; $Target = "root@$rigHost"
    }

    $sshDir  = Join-Path $env:USERPROFILE ".ssh"
    $keyFile = Join-Path $sshDir "etk_rig"
    $pubFile = "$keyFile.pub"
    $cfgFile = Join-Path $sshDir "config"
    $marker  = "# ETK pairing (etk_pair.sh) -- do not edit by hand"
    # No -i / IdentitiesOnly on the bare probe: it must mirror exactly what the
    # installer runs (ssh root@host ...), letting ~/.ssh/config + default keys
    # decide. accept-new suppresses the first-connect fingerprint prompt.
    $probe   = @("-o","BatchMode=yes","-o","ConnectTimeout=6","-o","StrictHostKeyChecking=accept-new")

    Write-Host ">>> ETK SSH pairing - target: $Target" -ForegroundColor Cyan

    # STEP 1: test-first (the bare-target path the installer actually uses).
    # @SshBase after @probe: first-obtained-value wins in OpenSSH, so the
    # probe keeps its tighter timeouts while inheriting the pinned HostName.
    $r = & ssh @probe @SshBase $Target "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$r" -match "ETK_OK") {
        Write-Ok "Already reachable passwordlessly - nothing to do."
        return
    }

    # STEP 2: ensure the dedicated, no-passphrase key (never clobbers id_*).
    if (-not (Test-Path -LiteralPath $keyFile)) {
        if (-not (Test-Path -LiteralPath $sshDir)) {
            New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
        }
        Write-Note "Generating dedicated key $keyFile (no passphrase)..."
        & ssh-keygen -t ed25519 -N '""' -C "etk-host" -f $keyFile -q
        if (-not (Test-Path -LiteralPath $keyFile)) { throw "ssh-keygen failed (no key produced)." }
    }
    if (-not (Test-Path -LiteralPath $pubFile)) { throw "Public key $pubFile missing after keygen." }

    # STEP 3: key already accepted (so STEP 1 only failed because the bare
    # target wasn't routed yet)? Then skip the password, just fix the config.
    $o = & ssh @probe @SshBase -o IdentitiesOnly=yes -i $keyFile $Target "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$o" -match "ETK_OK") {
        Write-Ok "Dedicated key already accepted by rig - no password needed."
    } else {
        # THE one password. Pull the SHARED rig-side body; ship it in ONE ssh,
        # base64-wrapped (transport-safe). The pubkey rides as $ETK_PUBKEY.
        $pub = (Get-Content -LiteralPath $pubFile -TotalCount 1).Trim()
        if ([string]::IsNullOrWhiteSpace($pub)) { throw "Empty public key." }
        $repoRoot = Split-Path $PSScriptRoot -Parent
        $pairSh   = Join-Path $repoRoot "scripts\etk_pair.sh"
        $body     = Get-Heredoc -Path $pairSh -Marker "ETKPAIRKEY"
        $full     = "ETK_PUBKEY='$pub'`n$body"
        $b64      = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($full))
        Write-Host "    >>> Pairing - you will be asked for the rig password ONCE" -ForegroundColor Yellow
        Write-Host "        (Rocknix default is 'rocknix' unless you changed it)." -ForegroundColor Yellow
        $pairOut = & ssh @SshBase $Target "echo $b64 | base64 -d | bash"
        if ($LASTEXITCODE -ne 0 -or "$pairOut" -notmatch "ETK_PAIR_OK") {
            if ($pairOut) { Write-Note "rig said: $pairOut" }
            throw "Key install did not complete (wrong password, or no SSH route to $Target)."
        }
        Write-Ok "Key installed on rig."
    }

    # STEP 4: config block so the BARE target offers the dedicated key.
    Write-EtkSshConfigBlock -CfgFile $cfgFile -Marker $marker -RigHost $rigHost -RigUser $rigUser

    # STEP 5: verify the real path — bare target, passwordless.
    $v = & ssh @probe @SshBase $Target "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$v" -match "ETK_OK") {
        Write-Ok "Verified: 'ssh $Target' is passwordless. Pairing complete."
        return
    }
    # Diagnose precisely (§G.7).
    $o2 = & ssh @probe @SshBase -o IdentitiesOnly=yes -i $keyFile $Target "echo ETK_OK" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$o2" -match "ETK_OK") {
        Write-Warn "The key works, but the bare target isn't routed to it. Check the ETK block in $cfgFile."
    } else {
        Write-Warn "The rig is not accepting the key (perms/path). See WINDOWS_HOST_README.md 'First-time SSH handshake'."
    }
    throw "SSH pairing verification failed - 'ssh $Target' still needs a password."
}
