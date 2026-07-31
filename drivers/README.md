# ETK Turnip driver catalog (staging)

This directory stages the Mesa/Turnip (and other Vulkan) driver builds ETK can
test. **Binaries here are never committed** (see `.gitignore`) — this is a local
staging area, same precedent as `vault/`. Only this README is tracked.

## The hard split: ROCKNIX glibc/msm vs Android bionic/kgsl

A Vulkan driver `.so` is built against **one** OS/ABI and cannot cross over. ETK
spans two targets, so the catalog is split:

```
drivers/
├── *.so            ← ROCKNIX ICDs (glibc + Wayland/DRM/msm). The Pitstop
│                      DRIVER-BUILD selector binds ONE of these over
│                      /usr/lib/libvulkan_freedreno.so on the RPCS3 rig.
│                      SONAME: libvulkan_freedreno.so
│                      NEEDED: libc.so.6, ld-linux-aarch64.so.1, libwayland-*, libdrm.so.2
└── android/        ← Android adrenotools packages (.adpkg.zip; bionic + kgsl).
                       For the aPS3e-on-Android side ONLY — loaded via aPS3e's
                       in-app adrenotools driver picker, NOT this selector.
                       NEEDED: libc.so, libcutils.so, libhardware.so, liblog.so,
                               libnativewindow.so  (Android framework libs)
```

**Why they don't cross:** the Android forks link Android framework libraries
(`libcutils`, `libhardware`, `libnativewindow`, `liblog`, bare `libc.so`) that
do not exist on ROCKNIX; the ROCKNIX driver links glibc + Wayland/X11/DRM that
do not exist on Android. Dropping an `android/*.so` into the ROCKNIX bind catalog
would fail to load — a *wrong-OS* failure, not a *bad-for-PS3* one. Keep them
segregated so a benchmark never confuses the two failure modes.

The install.sh staging glob is `drivers/*.so` (top level only), so `android/`
is never swept into the ROCKNIX catalog.

## This directory IS the catalog

Drop a ROCKNIX glibc/msm `.so` in here and `install.sh` stages it — no allowlist
to edit, no sha to register. The on-rig catalog **mirrors** this directory, so
deleting a build here also retires it from the rig (the DRIVER tab's current
selection is never pruned out from under you).

Use this rather than `scp`: hand-pushed files are wiped by the next reinstall.

`CERTIFIED_BUILDS` in `install.sh` is a **bootstrap manifest, not a gate**. This
directory is gitignored, so a fresh clone starts empty and those builds are
fetched from the latest GitHub release and sha256-verified (a *download* can
truncate or be swapped — a local build you made is never checked or touched).
A normal user install therefore still ends up as "stock + the proven fork";
an operator with local builds just has more in the catalog.

> This was previously an allowlist that pruned anything uncertified off the rig.
> That came from the 0.5.0 productization commit and was a *release-packaging*
> concern applied at install time, which also blocked local experimentation —
> backwards, since a build has to run on the rig before it can earn
> certification. Curate the shipped catalog via what goes in the release.

## Current catalog

| Build | Base | Status | Notes |
|---|---|---|---|
| `etk_turnip_rocknix_26.1.3_gtk_0.4.so` | mesa-26.1.3 | **proven** | The track-validated fork; shipped in the release, and `TURNIP_SO` (default pick). |
| `etk_turnip_rocknix_26.1.6_gtk_0.6.so` | mesa-26.1.6 | unvalidated | **Race this one.** 26.1.6 rebase + 2 upstream backports + `zlatez`. |
| `etk_turnip_rocknix_26.2.0-rc3_gtk_0.6.so` | mesa-26.2.0-rc3 | unvalidated | Same series on the pre-release base (carries both backports natively). |

Retired (deleted 2026-07-30, so the rig catalog stays readable): `gtk_0.1`, `gtk_0.2`, and both
`gtk_0.5` builds. The 0.5 pair shipped with `TU_ETK_QUERY_SURVIVE` defaulted OFF — see the fork's
`PATCHES.md`. One ledger row (2026-07-30) still names `build=26.1.6_gtk_0.5`; the row stays
meaningful and correctly attributed, it just no longer has a binary to re-run.

> **Why the 0.5 → 0.6 bump rather than rebuilding 0.5 in place.** That ledger row already attributes
> a session to `26.1.6_gtk_0.5`. Reusing the name for a binary that behaves differently would make
> the recorded row ambiguous — exactly the problem the `build=` tag exists to prevent. Version
> numbers are cheap; ambiguous history isn't.

Every build from 0.4 on self-identifies — `vulkaninfo | grep driverInfo` reports
`Mesa <ver> (git-<sha>) ETK-GTK`, and `session_postmortem.sh` folds the loaded
build into the ledger's `tune_tag` (`build=26.1.6_gtk_0.5;tu_debug=…`) so
`etk_dyno` scores each driver as its own arm rather than pooling them.

## What belongs where

| Slot | Target | ABI | How to get it |
|---|---|---|---|
| ROCKNIX stock Turnip | RPCS3 rig | glibc/msm | already on-device; the selector's synthetic `stock` (no file) |
| Official Mesa Turnip (bumped) | RPCS3 rig | glibc/msm | build from Mesa source for ROCKNIX (`scripts/build_rocknix.sh`) |
| ETK GT/LSD patch forks | RPCS3 rig | glibc/msm | our builds → `drivers/*.so` |
| MrPurple / K11MCH1 / Whitebelyash forks | aPS3e Android | bionic/kgsl | `drivers/android/` (see its MANIFEST) |

To put the popular Android forks on the **same** RPCS3-rig footing as our fork,
their Mesa source revision must be **re-compiled for ROCKNIX glibc/msm** — which
nobody ships (the whole reason a PS3/GT-tuned ROCKNIX Turnip fork is warranted).
