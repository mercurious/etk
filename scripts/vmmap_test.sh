#!/bin/bash
# ==========================================================
# ETK vm.max_map_count TEST HARNESS  (DISPOSABLE / NOT WIRED INTO ETK)
# ==========================================================
# PURPOSE: Properly evaluate whether raising vm.max_map_count
#   1048576 (vs the stock 65530 default) helps or hurts GT5/6
#   track stability, BEFORE re-committing the change to
#   thermal_d.sh (which was reverted in 0be4f3b after a
#   suspected stability regression).
#
# BLAST RADIUS: writes ONLY to /proc/sys/vm/max_map_count via
#   sysctl on the rig. No files touched. Value resets to the
#   kernel default on reboot OR via `off`. Does NOT touch
#   thermal_d.sh, install.sh, the Sentry, env.sh, or Pitstop.
#   Full backout: `vmmap_test.sh off` (or just reboot).
#
# WHY THIS IS A BETTER A/B THAN THE noconstcheck HARNESS:
#   max_map_count is a kernel sysctl, checked on every mmap()
#   call. Unlike TU_DEBUG (env, fixed at exec), this can be
#   flipped LIVE while RPCS3 is running - no relaunch needed.
#   Run a track with `off`, observe; flip to `on` mid-session,
#   keep playing; compare. The kernel cannot lie about the
#   value, so `status` reads ground truth.
# ==========================================================

source "$(dirname "$0")/env.sh" 2>/dev/null

RAISED=1048576
DEFAULT=65530   # stock Linux default; confirmed on rig 2026-05-19
CMD="${1:-status}"

case "$CMD" in
  on)
    ssh "$RIG_SSH" "sysctl -w vm.max_map_count=$RAISED" && {
      echo "[ON ] vm.max_map_count raised to $RAISED on rig."
      echo "      Live - takes effect on the next mmap() call."
      echo "      Game does NOT need to relaunch. Then: $0 status"
    }
    ;;
  off)
    ssh "$RIG_SSH" "sysctl -w vm.max_map_count=$DEFAULT" && \
      echo "[OFF] vm.max_map_count reset to default $DEFAULT. Live."
    ;;
  status)
    ssh "$RIG_SSH" "
      V=\$(cat /proc/sys/vm/max_map_count)
      printf 'vm.max_map_count : %s' \"\$V\"
      if [ \"\$V\" = '$RAISED' ];  then echo '   [RAISED - boost on]'
      elif [ \"\$V\" = '$DEFAULT' ]; then echo '   [DEFAULT - control]'
      else echo \"   [unknown - neither raised ($RAISED) nor default ($DEFAULT)]\"; fi

      P=''
      for C in \$(pgrep rpcs3-sa 2>/dev/null) \$(pgrep -f rpcs3 2>/dev/null); do
        A0=\$(tr '\0' '\n' < /proc/\$C/cmdline 2>/dev/null | head -n1)
        case \"\$A0\" in */rpcs3-sa) P=\$C; break;; esac
      done
      if [ -n \"\$P\" ]; then
        N=\$(wc -l < /proc/\$P/maps 2>/dev/null)
        printf 'rpcs3-sa pid %s  : %s VMAs in use' \"\$P\" \"\$N\"
        # Headroom telltale - is the rig anywhere near the limit?
        # If utilization is <10%% of the default, the raise is mostly
        # theoretical for RPCS3 on this title.
        PCT=\$(( N * 100 / V ))
        printf '   (%s%% of current ceiling)\n' \"\$PCT\"
        if [ \"\$PCT\" -lt 10 ]; then
          echo \"  hint: <10%% utilization - raise is mostly theoretical headroom\"
        elif [ \"\$N\" -gt $DEFAULT ]; then
          echo \"  NOTE: rpcs3 has MORE VMAs than the default ceiling ($DEFAULT)\"
          echo \"        - flipping 'off' RIGHT NOW would not affect existing mappings,\"
          echo \"        - but the next mmap() would fail with ENOMEM. Be aware.\"
        fi
      else
        echo 'rpcs3-sa not running - launch the game and re-run: status'
      fi
    "
    ;;
  *)
    echo "usage: $0 {on|off|status}"
    echo "  on      raise vm.max_map_count to $RAISED on rig (live)"
    echo "  off     reset to default $DEFAULT (live)"
    echo "  status  show current value + rpcs3-sa VMA utilization"
    exit 1
    ;;
esac
