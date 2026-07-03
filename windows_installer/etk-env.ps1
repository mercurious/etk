# ==========================================================
# ETK WINDOWS HOST CONFIG  (mirror of scripts/env.sh + etk.conf)
# ==========================================================
# These values MUST match your bash scripts/env.sh. This file is the
# ONLY place the Windows port duplicates configuration - everything
# that runs on the rig is read straight out of install.sh / uninstall.sh
# at runtime, so there is no second copy of the Sentry to keep in sync.
#
# Fill each value from the matching variable in scripts/env.sh.
# Anything marked  <<< VERIFY  is a best guess from the repo and should
# be confirmed against your env.sh before you trust an install.
# ==========================================================

# --- CONNECTION -------------------------------------------------------
# Rig SSH target (mirrors RIG_SSH in env.sh / etk.conf.example). Defaults to
# the mDNS name virtually every stock Rocknix device publishes, so MOST setups
# need no change. Edit ONLY if your handheld is a different SoC (e.g.
# "root@SM8550.local") or you must use a literal IP (e.g. "root@192.168.1.53").
$RigSsh = "root@SM8250.local"

# --- CORE PATHS (on the rig) -----------------------------------------
# ETK_ROOT in env.sh. The Sentry sources env.sh from here, so it must
# match exactly. From the repo the Sentry uses:
#   /storage/games-internal/roms/etk
$EtkRoot = "/storage/games-internal/roms/etk"   # <<< VERIFY against ETK_ROOT

# CHIPSET in env.sh (used to build the vault path). e.g. "SM8250"
$Chipset = "SM8250"                      # <<< VERIFY against CHIPSET

# ID_FILE in env.sh - file on the rig holding the active game ID.
# env.sh: ID_FILE="$SHM_DIR/active_id.txt", SHM_DIR=/dev/shm/etk_shm
$IdFile = "/dev/shm/etk_shm/active_id.txt"   # resolved from env.sh 2026-05-28

# PKG_STAGING_DIR in env.sh - the .pkg/.rap drop folder (under ETK_ROOT).
$PkgStaging = "$EtkRoot/pkg_install_drop"    # resolved from env.sh 2026-05-28

# TELEMETRY_DIR in env.sh - session/career ledgers (under ETK_ROOT).
$TelemetryDir = "$EtkRoot/etk_telemetry"     # resolved from env.sh 2026-05-28

# --- RPCS3 PATHS (provisioned in install Step 1) ---------------------
# env.sh roots all RPCS3 state under bios/rpcs3 - NOT /storage/.config/rpcs3.
$Rpcs3DevHdd0       = "/storage/games-internal/roms/bios/rpcs3/dev_hdd0"  # RPCS3_DEV_HDD0
$Rpcs3CustomConfigs = "/storage/games-internal/roms/bios/rpcs3/custom_configs"  # RPCS3_CUSTOM_CONFIGS
$Rpcs3GameDir       = "$Rpcs3DevHdd0/game"                  # RPCS3_GAME_DIR
$Rpcs3HomeDir       = "$Rpcs3DevHdd0/home"                  # RPCS3_HOME_DIR
$Rpcs3ExdataDir     = "$Rpcs3DevHdd0/home/00000001/exdata"  # RPCS3_EXDATA_DIR
$Ps3LauncherDir     = "/storage/games-internal/roms/ps3"   # PS3_LAUNCHER_DIR (resolved from env.sh 2026-05-28)

# --- OPERATOR CONFIG (mirror of etk.conf; generated ONTO the rig) ------
# The bash installer pushes the repo-root etk.conf so the RIG's env.sh picks
# these up (build tier, HUD mode, DP mirror). The Windows port has no
# etk.conf, so the installer GENERATES a minimal one on the rig from here.
$EtkBuildType  = "FULL"    # FULL | LITE | RAW    (ETK_BUILD_TYPE)
$DefaultMode   = "RACE"    # RACE | PIT           (DEFAULT_MODE)
$EtkHudMode    = "BASIC"   # BASIC | GINSTR       (ETK_HUD_MODE)
$EtkDpMirror   = "1"       # 1 = DP capture mirror on (ETK_DP_MIRROR)
$HudHeaderHold = "15"      # seconds              (HUD_HEADER_HOLD_S)

# --- CUSTOM TURNIP DRIVER (Stage IV, install Step 6.5) -----------------
# Certified GTK fork builds are fetched from the latest ETK GitHub release
# and sha256-verified automatically - no value needed here. To ALSO stage a
# local out-of-tree .so and make it the DEFAULT pick on a rig that has no
# on-rig selection yet, point this at the file (mirrors etk.conf TURNIP_SO).
$TurnipSo = ""             # e.g. "C:\etk\drivers\my_dev_build.so"

# --- CUSTOM RPCS3 BUILD (GTK Edition, install Steps 6.55/6.56) ---------
# Mirrors etk.conf RPCS3_APPIMAGE / RPCS3_ENV_FLAGS. Download the AppImage
# from the ETK GitHub release, then point this at the downloaded file, e.g.
#   $Rpcs3AppImage = "$env:USERPROFILE\Downloads\rpcs3-etk_gtk-edition-0.6.0_v0.0.41-19544-60c9705a_linux_aarch64.AppImage"
# Empty = stock RPCS3 (any previously staged custom build is removed).
# A Windows download has no Linux exec bit - the installer sets it on the
# rig copy (an AppImage staged without +x fails as a silent exit-126
# "quits on launch", not a crash).
$Rpcs3AppImage = ""
# Space-separated VAR=value pairs exported into the RPCS3 runtime at every
# game launch (profile.d vector). For the GTK Edition build set:
#   $Rpcs3EnvFlags = "GTK_REMAP0_ONE=1"     # the #11912 road-flicker fix
$Rpcs3EnvFlags = ""

# --- PRIVATE PADDOCK (0.3.0, optional) --------------------------------
# Windows mirror of etk.conf's PADDOCK_TOKEN / PADDOCK_REPO. To get private
# shader storage, set your GitHub token here BEFORE running the installer.
# Preferred: fine-grained PAT scoped to ONE repo (<you>/etk-paddock),
# contents read/write — create the private repo on github.com first
# (fine-grained tokens can't create repos; a classic `repo`-scope PAT can,
# and the installer will then create it for you). Empty = feature off,
# the PADDOCK tab never appears. ETK never shares these bytes.
$PaddockToken = ""
$PaddockRepo  = ""    # optional; default <token-owner>/etk-paddock

# --- BEHAVIOUR TOGGLES ------------------------------------------------
# "1" = verbose scp (-v). Anything else = quiet (-q). Mirrors ETK_VERBOSE.
$EtkVerbose = "0"

# "1" = force legacy SCP wire protocol (-O). Set this to "1" if scp fails
# with "subsystem request failed" / sftp errors against the rig's SSH
# server (newer OpenSSH scp defaults to SFTP; some embedded servers need
# the old protocol). Harmless to leave at "0" unless you hit that error.
$EtkScpLegacy = "0"
