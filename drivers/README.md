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
