# ETK Autonomy — Hunt Grants (spec v0.6 — APPROVED 2026-10-10; P1–P3 BUILT 2026-10-10: grant, guard, mint, car)

> Goal (operator, 2026-10-10): point the Engineer at one game and let it run trial-and-error
> crash hunting with full autonomy overnight, so a core or driver regression is fixed by
> morning. Until now this took many hours of supervised track debugging with the human
> operator at the controls.

Pitlink 0.10.0 made the inner loop autonomous: launch from the game menu, drive, sense a
system event, harvest evidence, recover via R3, relaunch, and get a verdict in ~2 min
(`gt6_trial.py`). Two human gates remain per iteration: **mint** (a new core on etk-cloud)
and **deploy** (`install.sh`). This spec turns them into one **grant**, issued by the human,
which is scoped, budgeted in hours, expiring and revocable at the car.

## 1. Principle — the law stays; the human moment moves

Manual §1.1 (bytes-to-atoms) is unchanged in spirit. A human is still accountable for every
atom: but for a **hunt** the operator signs once for a bounded envelope (one game, one rig,
H hours, these paths) instead of pressing each control. **Publish is never
grantable.** Nothing a grant covers reaches another human's machine.

The guarantee against self-escalation: a grant is a **root-owned file created with `sudo`**.
The Engineer has no sudo (it asks for a password), so it can read a grant but never create,
extend or widen one. **Root runs no repo code** (P1): `grant.sh` runs as the operator, probes
read-only, prints the envelope and its sha256, asks the operator to type the grant id, and
only then `sudo install`s that exact file; it re-reads the installed bytes and removes the grant
if they differ from what was printed. A repo script under `sudo` would hand root to whoever last
edited it.

### 1.1 Why the guardrails can relax now: the two facts that changed the atoms (operator, 2026-10-10)

§1.1's test asks two questions of mint and deploy: *could it spend someone's money?* and
*could it brick hardware?* As of 0.10.0 the garage answers both differently. The grant
relaxes the guardrails **only while both facts hold**, inside the confined harness this spec
describes:

| Fact | What it changes | The condition it rests on, enforced by the harness |
|---|---|---|
| **etk-cloud costs nothing.** The account is past its trial and is pay-as-you-go, but the node is sized inside the always-free tier. | A mint no longer turns into money, so it stops being an atom on the cost axis. | The node stays always-free sized. `grant.sh issue` records the node's shape fingerprint (CPU count, memory, disk, image); `hunt.py mint` re-reads it at each preflight and **refuses** if it changed. A resize, or leaving the free tier, voids every mint grant until the operator re-issues. |
| **The garage has a second car.** car12 can carry the project if car8 is taken out of commission by a serious model error or a track disaster. | Losing the hunt car is survivable, so the rig stops being an irreplaceable atom. | A grant names **exactly one** car (the hunt car). The other is the **reserve** and is never a hunt target. Overnight grants are issued only while the reserve is verified bootable (last cold boot plus Pitstop reachable, checked at issue). With one car in service, grants are supervised-only. |

What does **not** relax: **publish** (other humans' machines; no garage fact changes that),
the kernel and image lanes (bricking the reserve-protected car is survivable, but there's no
reason to take that risk), certified pins, and install/uninstall. The harness stays
confined to user-space payloads under `hunt/` paths, R3-only recovery, and no reboots.

## 2. The grant

`/etc/etk/grants/hunt.json` (root:root 0644), created by the operator at a terminal, as
themself (not under `sudo`; the script refuses root):

```bash
/home/dave/etk/tools/hunt/grant.sh issue --game BCUS98296 --hours 10 [--lanes rpcs3,turnip] [--supervised]
```

The script prints the envelope, the operator types the grant id, and `sudo` asks for the
password: **that's the human moment**. `grant.sh show` prints the current grant; `grant.sh
revoke` removes it (host and rig). Before the signature, `issue` refuses unless: the guard is a
registered PreToolUse hook; etk-cloud's OCI shape is inside always-free; the hunt car passes
`scripts/etk_car.sh verify` and its USB gadget serial is one the host sees on 1d6b:0104; no
other grant is valid. Fields:

| Field | Example | Meaning |
|---|---|---|
| `id` | `hunt-20261011-gt6` | names the fork branch `hunt/<id>`, the audit log and the report (`--name gt6`; default the serial) |
| `issued_at` / `expires_at` | +10 h (hard cap 12 h) | after expiry every layer refuses |
| `game` | `BCUS98296` | the only title that may be pinned or overridden |
| `rig` | car8 (USB serial `32906f627cfd…`) | the only car the hunt may touch; the other car is the reserve |
| `mode` | `overnight` / `supervised` | overnight only with a verified reserve; otherwise `issue` refuses unless `--supervised` |
| `reserve` | car12 (`flip2-12g`), last verified bootable | **verified** = passes `etk_car.sh verify` with its name assigned, reports its boot time, and carries Pitstop (`roms/etk/bin/etk_pitstop.py`). Required for an overnight grant (§1.1) |
| `node` | etk-cloud shape fingerprint at issue | `mint` refuses if the node changed (always-free sizing, §1.1) |
| `mint` | lanes `rpcs3` (default) + `turnip` when `--lanes` names it | core-oriented, Turnip-capable. No mint-count or node-hour budget, since compute is free (§1.1); one mint at a time. `kernel` and `image` lanes are **never** grantable (brick risk) |
| `inject` | `emulators/hunt/`, `drivers/hunt/`, `debug_env` | the only rig paths a hunt may write |
| `never` | publish, tags, `garage` remote, rig reboot, CERT pins, kernel/DTB/firmware, `/flash`, install/uninstall | fixed, not configurable |
| `tools` | `tools/hunt` commit, clean or not | what the operator trusted; the envelope flags uncommitted changes |

At issue, `grant.sh` also mirrors a **rig grant** (id, game, expiry, USB serial, lanes as
`key=value` lines) to `/storage/.config/etk-hunt.grant` over the operator's own ssh. The car checks it
independently, so expiry and revocation hold even if the host is wrong.

## 3. Enforcement (defence in depth)

1. **One entry point.** `tools/hunt/hunt.py` is the only command the Engineer runs for a hunt
   (`status · check · audit · mint · put · pin · unpin · trial · recover · rollback · end · report`).
   Every acting subcommand first validates the grant (a regular file, root-owned, writable by
   no one else, in root-owned directories; unexpired; at most 12 h; the node fingerprint's sha
   matches its fields and those fields are always-free; in scope), refuses on a broken audit
   chain, then appends a **hash-chained** line to `state/hunt/<id>/audit.jsonl` (time, action,
   args, result; mints and node-minutes are recorded, not budgeted). The chain's first link is
   the sha256 of the grant bytes, so a log only verifies under the grant it ran under. It is
   tamper-evident, not tamper-proof: the file is the Engineer's. P1 builds `status [--probe]`,
   `check [--game --lane]` and `audit`; the rest validate, audit the attempt and exit 3 naming
   their phase.
2. **Claude Code permissions.** The only new allow rule is
   `Bash(python3 /home/dave/etk/tools/hunt/hunt.py:*)` (the operator adds it). Raw `forge.sh`,
   `lane_*.sh`, `install.sh`, `uninstall.sh`, `gh release`, `git tag` and ssh writes stay gated
   as today. The PreToolUse guard `tools/hunt/guard.py` (registered in
   `.claude/settings.local.json` on `Bash|Edit|Write|MultiEdit|NotebookEdit`) parses each
   command (wrappers, `bash -c`, `ssh HOST '…'`, heredocs that feed a shell) and **always**
   denies: running `forge.sh`, `lane_*.sh`, `build_*.sh`, `install.sh`, `uninstall.sh`,
   `etk-install.ps1` (`--dry-run`/`--status`/`--help` included, per §1.1); `gh release
   create|upload|edit|delete`; creating, deleting or pushing a tag; reboot/poweroff/halt,
   locally or over ssh; `grant.sh issue`. `hunt.py` beyond `status/check/audit` is denied
   without a valid grant. **While a grant is valid the enforcement surface is frozen:** no
   edit or shell write to `tools/hunt/`, `.claude/` or `~/.claude/` settings and hooks, and no
   `git checkout/reset/restore/stash/apply/cherry-pick/revert/clean/rm/mv` in the etk repo
   (pull/rebase stay open). It sees only the Engineer's tool calls; the operator's shell mode
   and Run button never pass through it. A crash in it allows the call (a non-blocking hook
   error). Replayed over this project's 3,576 recorded Engineer commands, it denies 13: real
   mints (`forge.sh`, incl. `--status`), two `gh release create`, one `install.sh --help`, and
   the probe that proved it live. That's belt-and-braces; the root-owned grant is
   the real lock.
3. **The car.** New garage ops `put` (chunked over the USB link, sha256-verified, path
   allow-list, size cap), `pin` and `unpin` (hunt override only) work only while the rig
   grant is valid **and** Pitstop **TOOLS → Autonomy** is on. The daemon never writes
   outside `emulators/hunt/`, `drivers/hunt/` and the 099 debug env. **As built (P3,
   `bin/etk_pitlink_usbd.py`):**
   - The car's gate, checked at every `put`/`pin`: a rig grant exists; its `expires_epoch`
     is in the future by the car's clock; the car's clock is not behind `issued_epoch`
     (an unsynced clock can't judge expiry); its `usb_serial` is this gadget's own; Autonomy
     is exactly `on`; the pin's game is the grant's.
   - `put {kind core|driver, name, total, sha256, offset, data}`: names must match
     `rpcs3-etk_hunt-*.AppImage` / `etk_turnip_hunt-*.so` (no paths); sequential chunks
     (≤ 1 MiB) into a dot-`.part`; the last chunk verifies sha256 and renames; caps of
     512 MiB (core) and 128 MiB (driver); a free-space check; artifacts are never replaced
     (an identical re-put is a no-op).
   - `pin {game, core?, driver?}` re-hashes the artifact against its `.sha256` and writes
     `emulators/hunt/override.tsv` (`game<TAB>core<TAB>driver`, `-` = none).
   - `unpin {game | all}` and `hunt_end` need neither grant nor switch: undoing is always
     allowed. `hunt_end` empties both hunt dirs, clears the debug env and deletes the rig
     grant. `hunt_status` reports the grant, the refusals, the switch, the override and the
     files.
   - Host side: `hunt.py put|pin mNN` only accept an artifact **minted under this grant**
     whose local sha still matches the audit; `put` streams it in 768 KiB chunks over one
     `client.GarageSession`. `unpin`, `rollback` (unpin all) and `end` (`hunt_end`) run
     without a valid grant. `status --probe` adds the car's view. The guard denies
     `pitlink.py garage put|pin` (they go through `hunt.py`) and any ssh/scp/rsync naming
     `etk-autonomy`, `etk-hunt.grant` or the hunt dirs (no self-escalation at the car).
4. **The forge.** `forge.sh --hunt <id>` builds only the granted lanes from the fork branch
   `hunt/<id>`. Artifacts go to `emulators/hunt/` (invisible to `release_sanity`'s core cap
   and to install's staging loop, like `retired/`). There's no crowning and no catalog
   staging. It records mints and node-minutes in the audit and refuses after expiry or on a
   changed node fingerprint. **As built (P2):**
   - `hunt.py mint --base <sha> [--patch patches/X.patch] [--marker SYM] [--label T] [--dry-run]`
     is the Engineer's only way in. It takes the patch, `package-appimage.sh` and
     `verify-markers.sh` from the **committed** content of fork branch `hunt/<id>` (never the
     working tree), names the artifact `rpcs3-etk_<id>-mNN_armsx3-<base9>_linux_aarch64.AppImage`,
     holds a per-hunt lock (one mint at a time), runs `forge.sh --hunt <id> rpcs3 --verbose`
     with `HUNT_*` inputs (they override `etk.conf`, which forge sources first), and audits
     `mint` (inputs, branch and patch shas) → `minted` (sha256, node-minutes) or `mint failed`
     (log tail). Logs: `state/hunt/<id>/mints/mNN/`.
   - `forge.sh --hunt` re-checks the grant itself (`hunt.py check --node-host --id`: the grant
     is valid and in scope, and etk-cloud's shape still matches it and is free) **before any
     ssh**, and again before staging: a grant that ends mid-build stages nothing. It refuses
     `--local` and every lane but rpcs3. It builds in `~/rpcs3-hunt`, a `git worktree` of the
     certified tree (shared objects, so any fetched ARMSX3 commit is a base; the certified
     tree's resting state never moves; the hunt tree's prior state is banked by the lane, not
     a preflight failure). It stages node-side to `~/etk/emulators/hunt/` (lane `STAGE`),
     host-side to `emulators/hunt/`, keeps status/fingerprints/logs under
     `state/hunt/<id>/forge/`, and uses its own reattach marker (`active_hunt_rpcs3`). A hunt
     never runs beside a certified build and vice versa (`node-busy`). `release_sanity` is
     skipped: the hunt stages outside the release catalog.
   - The lane's own gates still apply to a hunt build: the `MARKER` symbol and the
     `GTK Edition` literal. A bisect patch on an intermediate ARMSX3 commit must carry both
     (P4's patch work).
   - **Not built:** the turnip hunt lane (`drivers/hunt/`); `--hunt` refuses it for now.

## 4. Inject without install — what a hunt may change on the car

| Payload | Where | How it takes effect |
|---|---|---|
| core AppImage | `$ETK_ROOT/emulators/hunt/` | `pin` writes `emulators/hunt/override.tsv` (game → core); the launch wrapper honours it only while the rig grant is valid. **Built (P3):** install.sh STEP 6.55's wrapper, after the `core_map.tsv` pick, applies the same gate as the daemon (grant names this title, unexpired, clock not behind, this car's serial, Autonomy exactly `on`) and runs `emulators/hunt/<core>`; the ledger token reads `hunt:<core>`. Any miss leaves the pinned/certified choice |
| Turnip `.so` | `$ETK_ROOT/drivers/hunt/` | same override file; the wrapper points the title's Vulkan ICD at it. **Built (P3):** it copies the system `freedreno_icd*.json` with `library_path` swapped to the hunt `.so` into `/tmp/etk-hunt-icd.json` and exports `VK_ICD_FILENAMES` / `VK_DRIVER_FILES` for that launch only (token `+hunt:<so>`). Unexercised until the turnip hunt lane exists |
| diagnostics env | profile.d `099-etk-debug-env` | exists today (`garage debug_env`, ARMSX3_* allow-list) |

Never injectable: kit scripts, daemons, Pitstop, install payloads (those stay `install.sh`),
kernel, DTB, firmware, `/flash`, boot partitions, certified pins (`CERT_RPCS3`, Turnip
default). Enabling the wrapper override is **one ordinary install**, done once.

**Auto-rollback:** after expiry or revocation the wrapper ignores `override.tsv`, so the next
launch runs the certified or pinned core. `hunt.py end` deletes `hunt/` contents, clears the
debug env and removes the rig grant. The morning state is the certified rig plus evidence.

## 5. Revocation — any one, immediate

- `tools/hunt/grant.sh revoke` (it asks for sudo): deletes the host grant and the rig grant,
  and audits the revocation.
- **Pitstop TOOLS → Autonomy: off**, at the car: the daemon refuses every hunt op and the
  wrapper drops the overrides. (P3: the row `Autonomy (Engineer hunts): on|off` follows
  Pitlink; `/storage/.config/etk-autonomy` holding exactly `on` = on, absent = off, the
  default. It takes effect at the next request/launch and survives a cold boot.
  `uninstall.sh` removes it, the rig grant and the hunt dirs.)
- Remove the allow rule from `.claude/settings.local.json`.
- Expiry. Hours are the budget (hard cap 12 h).

## 6. The overnight loop

```
grant → hunt.py status → [ hypothesis → commit on fork branch hunt/<id> → hunt.py mint →
        hunt.py put + pin → hunt.py trial ×N (noise floor: N ≥ 3 for timing claims) →
        evidence + verdict → next hypothesis ] → hunt.py report → hunt.py end
```

- **Recovery** is the R3 path only (`recovery.sh`). **Never a reboot.** If the rig is
  unreachable for more than 10 min, or the kernel panics, the hunt **stops**, writes its
  report and state, and waits for a human (no notification channel; operator, 2026-10-10).
- **Morning deliverable:** a dossier with the verdict table and the convicting commit or
  function, the fork branch `hunt/<id>` pushed (an ordinary dev push), a candidate fix
  **unminted for release**, and the rig rolled back to certified. Publishing stays a human
  act, as always.

## 7. Defaults (settled 2026-10-10)

Budget = hours (default 10, hard cap 12); no mint or node-hour budget (compute is free); 1 game,
1 rig (car8; car12 the reserve), lane `rpcs3` (+ `turnip` per grant), one mint at a time,
trial timeout 10 min, N=1 for pass/fail verdicts (deterministic bugs) and N≥3 for anything
timing-based.

## 8. Manual amendment (apply only on approval) — §1.1, after the three moments

> **HUNT GRANTS (operator, 2026-10-10).** Because etk-cloud is sized inside the always-free
> tier (a mint costs no money) and the garage holds a reserve car (losing the hunt car is
> survivable), the operator may, for an autonomous crash hunt, sign
> one grant (`tools/hunt/grant.sh issue`, signed with `sudo`) that moves the human moment from each mint
> and deploy to the grant itself: one game, one rig, bounded in hours, the rpcs3 lane
> (turnip when granted), injection only into `hunt/` paths and the debug env, R3 recovery
> only. **Publish, tags, reboots, kernel/image lanes, certified pins and install/uninstall
> are never grantable.** Revocation: `grant.sh revoke`, Pitstop Autonomy off, or expiry.

## 9. Operator decisions

All settled by the operator, 2026-10-10:

1. **Concept: approved**, on the two facts in §1.1 (always-free etk-cloud; a reserve car). The
   amendment lands in TRACK_MANUAL §1.1 **together with P1**, so the law never names a
   mechanism that doesn't exist yet.
2. **Budget: hours only.** Compute is free, so there's no mint-count or node-hour budget; the
   node fingerprint still guards the "free" condition.
3. **Signature: `sudo`.** The root-owned grant is the human signature.
4. **Kill switch: yes.** Pitstop **TOOLS → Autonomy** at the car.
5. **Lanes: core-oriented, Turnip-capable.** `rpcs3` by default; `turnip` when a grant names it.
6. **Notification: none.** A stopped hunt writes its report and state and waits.

## 10. Build plan

| Phase | What | Needs |
|---|---|---|
| P1 | **BUILT 2026-10-10.** `tools/hunt/grant.sh` (→ `grantctl.py`; sudo-signed; node fingerprint + always-free judge, car + USB serial binding, reserve check, `--lanes`, `--supervised`), `grantlib.py` (validation, audit chain), `hunt.py` (status / check / audit; later subcommands refuse with their phase), `guard.py` (registered); `tools/hunt/test_hunt.py` (45 tests, incl. mutants); TRACK_MANUAL §1.1 amendment. **Validated end to end 2026-10-10** with a 1 h supervised grant `hunt-20261011-p1check`: sudo-signed root:root 0644, rig copy written and read back, `status --probe` VALID (node unchanged, car8 on USB), `mint` stub audited (exit 3), the guard froze an Edit to `tools/hunt/`, `revoke` removed both copies and audited it | nothing (no atoms) |
| P2 | **BUILT 2026-10-10 (rpcs3 lane).** `hunt.py mint` + `forge.sh --hunt` (§3.4); `lane_rpcs3.sh` gains `STAGE`. Tests: `tools/hunt/test_hunt.py` (52, incl. mint) and `tools/hunt/test_forge_hunt.py` (8: the real forge.sh + lane in a sandbox with fake ssh/rsync/docker and a real git node tree; 7 fail against the pre-P2 forge, the 8th is the certified-mint no-regression check). Turnip hunt lane not built | the operator's review of the `forge.sh` diff; the first hunt mint runs under a grant (P4) |
| P3 | **BUILT 2026-10-10.** daemon `hunt_status`/`put`/`pin`/`unpin`/`hunt_end` (§3.3), launch-wrapper override (core + Turnip ICD, §4), Pitstop **TOOLS → Autonomy** (§5), `hunt.py put/pin/unpin/rollback/end`, `client.GarageSession`, guard rules, uninstall coverage. Tests: `tools/hunt/test_car_hunt.py` (18, all fail on the pre-P3 daemon), `tools/hunt/test_wrapper_hunt.py` (12; runs the generated wrapper: HONOUR fails pre-P3), `tools/test_pitstop_autonomy.py` (incl. a CONTRACT suite: Pitstop, daemon and wrapper read one path with one test), `test_hunt.py` 57; `test_pitstop_pitlink`, `test_cache_screen`, `test_plusb`, `test_installers` still pass. TOOLS now has ten rows: the breathing line above the title gives way so all ten + help fit the rig's 22 rows. **Validated on car8 2026-10-10** (install + cold boot): TOOLS shows `Autonomy (Engineer hunts): off`; `garage hunt_status` = no grant, autonomy off, own serial, no override, no files | done |
| P4 | first **supervised** hunt with the operator awake: the GT6 commit bisect (`GT6Deadlock_0.10.0_20261010.md`) | a grant |
| P5 | first overnight hunt | a grant |
