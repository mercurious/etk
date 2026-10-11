# ETK Autonomy — Hunt Grants (spec v0.1, **PROPOSED** — operator decision pending)

> Goal (operator, 2026-10-10): point the Engineer at one game and let it run trial-and-error
> crash hunting with full autonomy overnight, so a core or driver regression is fixed by
> morning. Until now this took many hours of supervised track debugging with the human
> operator at the controls.

Pitlink 0.10.0 made the inner loop autonomous: launch from the game menu, drive, sense a
system event, harvest evidence, recover via R3, relaunch, and get a verdict in ~2 min
(`gt6_trial.py`). Two human gates remain per iteration: **mint** (a new core on etk-cloud)
and **deploy** (`install.sh`). This spec turns them into one **grant**, issued by the human,
which is scoped, budgeted, expiring and revocable at the car.

## 1. Principle — the law stays; the human moment moves

Manual §1.1 (bytes-to-atoms) is unchanged in spirit. A human is still accountable for every
atom: but for a **hunt** the operator signs once for a bounded envelope (one game, one rig,
N mints, H hours, these paths) instead of pressing each control. **Publish is never
grantable.** Nothing a grant covers reaches another human's machine.

The guarantee against self-escalation: a grant is a **root-owned file created with `sudo`**.
The Engineer has no sudo (it asks for a password), so it can read a grant but never create,
extend or widen one.

## 2. The grant

`/etc/etk/grants/hunt.json` (root:root 0644), created by the operator:

```bash
sudo /home/dave/etk/tools/hunt/grant.sh issue --game BCUS98296 --hours 10 --mints 12 --node-hours 8
```

The script prints the envelope and requires the password: **that's the human moment**. Fields:

| Field | Example | Meaning |
|---|---|---|
| `id` | `hunt-20261011-gt6` | names the fork branch `hunt/<id>`, the audit log and the report |
| `issued_at` / `expires_at` | +10 h (hard cap 12 h) | after expiry every layer refuses |
| `game` | `BCUS98296` | the only title that may be pinned or overridden |
| `rig` | car8 (USB serial `32906f627cfd…`) | the only car the hunt may touch |
| `mint` | lanes `rpcs3` (+ `turnip` if granted), `max_mints`, `max_node_hours` | `kernel` and `image` lanes are **never** grantable (brick risk) |
| `inject` | `emulators/hunt/`, `drivers/hunt/`, `debug_env` | the only rig paths a hunt may write |
| `never` | publish, tags, `garage` remote, rig reboot, CERT pins, kernel/DTB/firmware, `/flash`, install/uninstall | fixed, not configurable |

At issue, `grant.sh` also mirrors a **rig grant** (id + expiry + game) to
`/storage/.config/etk-hunt.grant` over the operator's own ssh. The car checks it
independently, so expiry and revocation hold even if the host is wrong.

## 3. Enforcement (defence in depth)

1. **One entry point.** `tools/hunt/hunt.py` is the only command the Engineer runs for a hunt
   (`status · mint · put · pin · unpin · trial · recover · rollback · end · report`). Every
   subcommand first validates the grant (root-owned, not dave-writable, unexpired, budget
   left, in scope), then appends a **hash-chained** line to `state/hunt/<id>/audit.jsonl`
   (time, action, args, result, mints and node-minutes used).
2. **Claude Code permissions.** The only new allow rule is
   `Bash(python3 /home/dave/etk/tools/hunt/hunt.py:*)`. Raw `forge.sh`, `lane_*.sh`,
   `install.sh`, `uninstall.sh`, `gh release`, `git tag` and ssh writes stay gated as today.
   A PreToolUse guard (next to the identity firewall) denies those commands outright and
   denies `hunt.py` when no valid grant exists. That's belt-and-braces; the root-owned grant
   is the real lock.
3. **The car.** New garage ops `put` (chunked over the USB link, sha256-verified, path
   allow-list, size cap), `pin` and `unpin` (hunt override only) work only while the rig
   grant is valid **and** Pitstop **TOOLS → Autonomy** is on. The daemon never writes
   outside `emulators/hunt/`, `drivers/hunt/` and the 099 debug env.
4. **The forge.** `forge.sh --hunt <id>` builds only the granted lanes from the fork branch
   `hunt/<id>`. Artifacts go to `emulators/hunt/` (invisible to `release_sanity`'s core cap
   and to install's staging loop, like `retired/`). There's no crowning and no catalog
   staging. It counts mints and node-minutes into the audit and refuses at budget.

## 4. Inject without install — what a hunt may change on the car

| Payload | Where | How it takes effect |
|---|---|---|
| core AppImage | `$ETK_ROOT/emulators/hunt/` | `pin` writes `emulators/hunt/override.tsv` (game → core); the launch wrapper honours it only while the rig grant is valid |
| Turnip `.so` | `$ETK_ROOT/drivers/hunt/` | same override file; the wrapper points the title's Vulkan ICD at it |
| diagnostics env | profile.d `099-etk-debug-env` | exists today (`garage debug_env`, ARMSX3_* allow-list) |

Never injectable: kit scripts, daemons, Pitstop, install payloads (those stay `install.sh`),
kernel, DTB, firmware, `/flash`, boot partitions, certified pins (`CERT_RPCS3`, Turnip
default). Enabling the wrapper override is **one ordinary install**, done once.

**Auto-rollback:** after expiry or revocation the wrapper ignores `override.tsv`, so the next
launch runs the certified or pinned core. `hunt.py end` deletes `hunt/` contents, clears the
debug env and removes the rig grant. The morning state is the certified rig plus evidence.

## 5. Revocation — any one, immediate

- `sudo tools/hunt/grant.sh revoke`: deletes the host grant and pushes the rig revoke.
- **Pitstop TOOLS → Autonomy: off**, at the car: the daemon refuses every hunt op and the
  wrapper drops the overrides.
- Remove the allow rule from `.claude/settings.local.json`.
- Expiry (hard cap 12 h) or budget exhaustion.

## 6. The overnight loop

```
grant → hunt.py status → [ hypothesis → commit on fork branch hunt/<id> → hunt.py mint →
        hunt.py put + pin → hunt.py trial ×N (noise floor: N ≥ 3 for timing claims) →
        evidence + verdict → next hypothesis ] → hunt.py report → hunt.py end
```

- **Recovery** is the R3 path only (`recovery.sh`). **Never a reboot.** If the rig is
  unreachable for more than 10 min, or the kernel panics, the hunt **stops**, sends a phone
  notification, and waits for a human.
- **Morning deliverable:** a dossier with the verdict table and the convicting commit or
  function, the fork branch `hunt/<id>` pushed (an ordinary dev push), a candidate fix
  **unminted for release**, and the rig rolled back to certified. Publishing stays a human
  act, as always.

## 7. Defaults (proposal)

12 h cap, 12 mints, 8 node-hours, 1 game, 1 rig (car8), lanes `rpcs3` (+ `turnip` on request),
trial timeout 10 min, N=1 for pass/fail verdicts (deterministic bugs) and N≥3 for anything
timing-based.

## 8. Manual amendment (apply only on approval) — §1.1, after the three moments

> **HUNT GRANTS (operator, 2026-10-xx).** For an autonomous crash hunt the operator may sign
> one grant (`sudo tools/hunt/grant.sh issue`) that moves the human moment from each mint
> and deploy to the grant itself: one game, one rig, bounded mints, node-hours and hours,
> rpcs3/turnip lanes only, injection only into `hunt/` paths and the debug env, R3 recovery
> only. **Publish, tags, reboots, kernel/image lanes, certified pins and install/uninstall
> are never grantable.** Revocation: `grant.sh revoke`, Pitstop Autonomy off, or expiry.

## 9. Operator decisions

1. Approve the concept and the §1.1 amendment?
2. Budgets: hours, mints, node-hours (cost ceiling on etk-cloud).
3. Is a root-owned grant via `sudo` acceptable as the human signature?
4. A Pitstop **Autonomy** switch as the physical kill at the car?
5. Is the `turnip` lane grantable, or core-only for now?
6. The notification channel for a stop: a phone push.

## 10. Build plan

| Phase | What | Needs |
|---|---|---|
| P1 | `grant.sh`, `hunt.py` skeleton (status / audit / validation), the PreToolUse guard; host tests | nothing (no atoms) |
| P2 | `forge.sh --hunt`, building from the fork branch into `emulators/hunt/` | a review; the first hunt mint runs under a grant |
| P3 | daemon `put`/`pin`/`unpin`, launch-wrapper override, Pitstop Autonomy switch | one ordinary install |
| P4 | first **supervised** hunt with the operator awake: the GT6 commit bisect (`GT6Deadlock_0.10.0_20261010.md`) | a grant |
| P5 | first overnight hunt | a grant |
