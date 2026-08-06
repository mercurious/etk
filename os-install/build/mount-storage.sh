# ETK hostless activation hook — ROCKNIX init sources this as /flash/mount-storage.sh
# (init:590, INSIDE mount_storage(); $disk + mount_part() are in scope). REPLACES
# ROCKNIX's default `mount_part "$disk" "/storage" "rw,noatime"`, so:
#   LAW 1: do the mount FIRST and EXACTLY as stock (a bug here costs /storage).
#   LAW 2: all ETK logic is fail-silent and creates no dependency.
#   LAW 3: NO `exit`/`return` — we are sourced into init; either would abort the boot.
#
# fs-resize is a POST-PIVOT oneshot, so it runs AFTER this hook each boot:
#   boot 1: .please_resize_me PRESENT -> hook only logs; fs-resize resizes + reboots.
#   boot 2: marker GONE, ETK not yet active -> install $ETK_ROOT/.seed_config into
#           /storage/.config (chksysconfig later merges ROCKNIX defaults on top, no
#           --delete) -> systemd starts the ETK services -> Pitstop in Tools. HOSTLESS.
#   boot 3+: etk.service already present -> no-op.
# .seed_config lives under $ETK_ROOT (NOT /storage/.config), so it never trips
# fs-resize's "already initialised" guard.

mount_part "$disk" "/storage" "rw,noatime"

if grep -q " /storage " /proc/mounts 2>/dev/null; then
  ETKR=/storage/games-internal/roms/etk
  ( U=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)
    if [ ! -e /storage/.please_resize_me ] \
       && [ ! -e /storage/.config/system.d/etk.service ] \
       && [ -d "$ETKR/.seed_config" ]; then
      mkdir -p /storage/.config
      cp -a "$ETKR/.seed_config/." /storage/.config/
      echo "etk-hook: ACTIVATED ETK .config (uptime=$U)" >> /storage/.etk_hook_log
    else
      R=$([ -e /storage/.please_resize_me ] && echo PRESENT || echo GONE)
      E=$([ -e /storage/.config/system.d/etk.service ] && echo active || echo staged)
      echo "etk-hook: uptime=$U resize=$R etk=$E" >> /storage/.etk_hook_log
    fi
  ) 2>/dev/null
fi
