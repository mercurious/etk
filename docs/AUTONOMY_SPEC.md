# ETK Autonomy — Hunt Grants (spec v0.3 — APPROVED 2026-10-10, all decisions settled; mechanism not built)

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
extend or widen one.

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

`/etc/etk/grants/hunt.json` (root:root 0644), created by the operator:

```bash
sudo /home/dave/etk/tools/hunt/grant.sh issue --game BCUS98296 --hours 10 [--lanes rpcs3,turnip]
```

The script prints the envelope and requires the password: **that's the human moment**. Fields:

| Field | Example | Meaning |
|---|---|---|
| `id` | `hunt-20261011-gt6` | names the fork branch `hunt/<id>`, the audit log and the report |
| `issued_at` / `expires_at` | +10 h (hard cap 12 h) | after expiry every layer refuses |
| `game` | `BCUS98296` | the only title that may be pinned or overridden |
| `rig` | car8 (USB serial `32906f627cfd…`) | the only car the hunt may touch; the other car is the reserve |
| `reserve` | car12 (`flip2-12g`), last verified bootable | required for an overnight grant (§1.1); absent = supervised-only |
| `node` | etk-cloud shape fingerprint at issue | `mint` refuses if the node changed (always-free sizing, §1.1) |
| `mint` | lanes `rpcs3` (default) + `turnip` when `--lanes` names it | core-oriented, Turnip-capable. No mint-count or node-hour budget, since compute is free (§1.1); one mint at a time. `kernel` and `image` lanes are **never** grantable (brick risk) |
| `inject` | `emulators/hunt/`, `drivers/hunt/`, `debug_env` | the only rig paths a hunt may write |
| `never` | publish, tags, `garage` remote, rig reboot, CERT pins, kernel/DTB/firmware, `/flash`, install/uninstall | fixed, not configurable |

At issue, `grant.sh` also mirrors a **rig grant** (id + expiry + game) to
`/storage/.config/etk-hunt.grant` over the operator's own ssh. The car checks it
independently, so expiry and revocation hold even if the host is wrong.

## 3. Enforcement (defence in depth)

1. **One entry point.** `tools/hunt/hunt.py` is the only command the Engineer runs for a hunt
   (`status · mint · put · pin · unpin · trial · recover · rollback · end · report`). Every
   subcommand first validates the grant (root-owned, not dave-writable, unexpired, node
   fingerprint unchanged, in scope), then appends a **hash-chained** line to `state/hunt/<id>/audit.jsonl`
   (time, action, args, result; mints and node-minutes are recorded, not budgeted).
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
   staging. It records mints and node-minutes in the audit and refuses after expiry or on a
   changed node fingerprint.

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
> one grant (`sudo tools/hunt/grant.sh issue`) that moves the human moment from each mint
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
| P1 | `grant.sh` (sudo; node fingerprint + reserve check; `--lanes`), `hunt.py` skeleton (status / audit / validation), the PreToolUse guard; host tests; TRACK_MANUAL §1.1 amendment | nothing (no atoms) |
| P2 | `forge.sh --hunt`, building from the fork branch into `emulators/hunt/` | a review; the first hunt mint runs under a grant |
| P3 | daemon `put`/`pin`/`unpin`, launch-wrapper override (core + Turnip ICD), Pitstop **Autonomy** kill switch | one ordinary install |
| P4 | first **supervised** hunt with the operator awake: the GT6 commit bisect (`GT6Deadlock_0.10.0_20261010.md`) | a grant |
| P5 | first overnight hunt | a grant |
