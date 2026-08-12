#!/bin/bash
# ==========================================================
# ETK BOOST TEST HARNESS  (DISPOSABLE / NOT WIRED INTO ETK)
# ==========================================================
# PURPOSE: Cheaply prove (or kill) the hypothesis that
#   TU_DEBUG=noconstcheck  lets GT5/GT6 blast past the
#   menu -> track shader-compile bottleneck on first run,
#   BEFORE committing to the locked-down install.sh/Sentry
#   integration (TRACK_MANUAL §1).
#
# BLAST RADIUS: writes ONLY to
#   /storage/.config/profile.d/099-etk-boost   (writable, regenerated each boot)
#   /dev/shm/etk_shm/boost_tu_noconstcheck      (tmpfs, gone every reboot)
# It does NOT touch install.sh, the Sentry, env.sh, or Pitstop.
# Full backout: `boost_test.sh off` then `rm scripts/boost_test.sh`.
#
# MECHANISM (identical to the proposed real design, so the test
# is representative): /etc/profile sources every file in
# /storage/.config/profile.d/* with `. ` INTO the same shell that
# start_rpcs3.sh later uses to exec /usr/bin/rpcs3-sa, so an
# export there is inherited by the emulator. The export is gated
# on a volatile SHM flag, so `off` is instant and reboot = OFF.
# ==========================================================

source "$(dirname "$0")/env.sh" 2>/dev/null

SNIPPET="/storage/.config/profile.d/099-etk-boost"
FLAG="/dev/shm/etk_shm/boost_tu_noconstcheck"
CMD="${1:-status}"

case "$CMD" in
  on)
    ssh "$RIG_SSH" "
      mkdir -p /storage/.config/profile.d /dev/shm/etk_shm 2>/dev/null
      printf '%s\n' \
        '# ETK BOOST TEST (disposable) - back out: boost_test.sh off' \
        '[ -f $FLAG ] && export TU_DEBUG=noconstcheck' > '$SNIPPET'
      : > '$FLAG'
    " && {
      echo "[ON ] snippet + flag installed on rig."
      echo "      >>> QUIT and RELAUNCH GT5/GT6 now - env is fixed at exec time,"
      echo "          so a game already running will NOT pick this up."
      echo "      Then: $0 status   (proves it actually reached rpcs3)"
    }
    ;;
  off)
    # Instant disable = drop the flag. Also remove the snippet so a
    # reboot / profile.d regeneration leaves zero trace.
    ssh "$RIG_SSH" "rm -f '$FLAG' '$SNIPPET'" && \
      echo "[OFF] flag + snippet removed. Relaunch the game for a clean (control) run."
    ;;
  status)
    ssh "$RIG_SSH" "
      printf 'snippet : '; [ -f '$SNIPPET' ] && echo PRESENT || echo 'MISSING (run: on - note a reboot regenerates profile.d and wipes it)'
      printf 'flag    : '; [ -f '$FLAG' ] && echo 'ON' || echo 'off'
      # Match the REAL emulator only: argv0 must be the rpcs3-sa binary.
      # pgrep -f also matches the 'sh -c ...--core=rpcs3-sa' ES wrapper
      # and runemu.sh, which never source our snippet - measuring those
      # gave false 'WITHOUT TU_DEBUG' negatives.
      P=''
      for C in \$(pgrep rpcs3-sa 2>/dev/null) \$(pgrep -f rpcs3 2>/dev/null); do
        A0=\$(tr '\0' '\n' < /proc/\$C/cmdline 2>/dev/null | head -n1)
        case \"\$A0\" in */rpcs3-sa) P=\$C; break;; esac
      done
      if [ -n \"\$P\" ]; then
        if tr '\0' '\n' < /proc/\$P/environ 2>/dev/null | grep -q '^TU_DEBUG=noconstcheck'; then
          echo \"verify  : CONFIRMED - rpcs3-sa pid \$P has TU_DEBUG=noconstcheck (BOOST IS LIVE)\"
        else
          echo \"verify  : rpcs3-sa pid \$P running WITHOUT TU_DEBUG (launched before 'on' - relaunch needed)\"
        fi
      else
        echo 'verify  : rpcs3-sa binary not running - launch the game, then re-run: status'
      fi
    "
    ;;
  *)
    echo "usage: $0 {on|off|status}"
    echo "  on      install snippet + flag (then relaunch the game)"
    echo "  off     remove both (then relaunch for a clean control run)"
    echo "  status  show state + PROVE whether the running rpcs3 actually has it"
    exit 1
    ;;
esac
