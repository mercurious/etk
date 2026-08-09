# Security Policy

ETK is a tuning kit for ROCKNIX handhelds: shell/python daemons deployed to your own
device (as root, by you) plus companion kernel/driver forks with published patch series.
There are no servers, no accounts, no telemetry leaving your hardware, and no secrets in
this repo. The security surface that matters here is **supply-chain integrity** (what you
run came from this repo, unmodified) and **device safety** (nothing bricks a rig).

## Reporting a vulnerability

Use **GitHub → Security → Report a vulnerability** (private vulnerability reporting) on
this repo. Reports are acknowledged on a best-effort basis, normally within **7 days**;
this is a solo-maintained project, so no formal SLA — but boot-safety and
supply-chain-class reports jump the queue.

Please report privately first for anything that could harm users' devices or let a third
party alter what users install. Coordinated disclosure: give us a reasonable window to
ship a fix before publishing details; credit is given unless you prefer otherwise.

## Scope

**In scope**
- Anything that lets untrusted input execute or escalate through ETK's scripts/daemons
  (`install.sh`, `bin/`, `scripts/`, the update path).
- Integrity of published artifacts: kernel images, driver builds, and their `.sha256`
  sidecars; anything that could make a tampered artifact pass our documented checks.
- **Bricking-class defects** — failures that could leave a device unbootable past the
  documented fallbacks. We treat these with security-report seriousness even when there
  is no attacker.

**Out of scope**
- Vulnerabilities in ROCKNIX, the mainline kernel, emulators, or other upstreams
  (report upstream; we do carry-forward fixes when relevant).
- Attacks requiring physical possession of an unlocked device, or root on the device
  (the kit itself runs as root by design on hardware you own).
- The rig's LAN services as configured by ROCKNIX itself.

## How to verify what you run

- Kernel/driver artifacts publish **sha256 sidecars**; the exact build recipe, source
  tarball hashes, and full patch series are documented in the companion repos
  (readable unified diffs — no opaque binaries).
- Proprietary firmware blobs are **never redistributed**: the recipes pull them from
  your own device.
- Deployments are designed to be reversible: stock kernel stays one boot-menu pick
  away, and every install step is idempotent and inspectable shell.

## Supported versions

Rolling `main` only. Fixes land on `main`; there are no maintained release branches.

## Development disclosure

Portions of this project are developed with AI assistance; that is disclosed in the
relevant patch/commit records. All changes are validated on real hardware before they
are documented as working — see the companion repos' `VALIDATION.md` discipline.
