#requires -Version 5.1
# ==========================================================
# ETK WINDOWS HOST - STANDALONE SSH PAIRING
# ==========================================================
# Mirror of `./install.sh --pair`: establish (or re-establish) passwordless
# SSH from this PC to the rig, without running a full install. Idempotent and
# test-first -- an already-paired host pays ZERO passwords; a fresh or
# reflashed rig pairs with exactly one. Use after a card reflash invalidates
# the rig's host key / authorized_keys, or to pair ahead of the first install.
#
# The rig-side key-install logic is shared verbatim with install.sh via
# Get-Heredoc (scripts/etk_pair.sh, marker ETKPAIRKEY) -- no second copy.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-pair.ps1
#   powershell -ExecutionPolicy Bypass -File .\windows_installer\etk-pair.ps1 -RigSshOverride root@192.168.1.53
# ==========================================================

param([string]$RigSshOverride)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\etk-env.ps1"
. "$PSScriptRoot\etk-common.ps1"

# -RigSshOverride wins over the $RigSsh baked into etk-env.ps1 (handy when
# mDNS is flaky and you want to pair against a literal IP). Applied AFTER the
# dot-source so it isn't overwritten.
if ($RigSshOverride) { $RigSsh = $RigSshOverride }

Assert-Tooling
Invoke-EtkPair -Target $RigSsh
