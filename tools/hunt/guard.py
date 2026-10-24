#!/usr/bin/env python3
"""guard.py -- PreToolUse hook: bytes-to-atoms controls stay the operator's (docs/AUTONOMY_SPEC.md §3.2).

Registered in .claude/settings.local.json on matcher Bash|Edit|Write|MultiEdit|NotebookEdit.
It sees only the Engineer's tool calls; the operator's own commands (shell mode, the Run
button's terminal) never pass through it.

Always denied (TRACK_MANUAL §1.1, §1.5), grant or not:
  running forge.sh, tools/forge/lane_*.sh, tools/rocknix-bin/build_*.sh, install.sh,
  uninstall.sh, etk-install.ps1 (a `bash -n` syntax check is fine; reading them is fine)
  gh release create|upload|edit|delete|delete-asset · creating/deleting a git tag ·
  pushing tags · reboot/poweroff/halt (locally or over ssh) · grant.sh issue / ./hunt start ·
  ssh/scp/rsync naming the car's hunt state (etk-autonomy, etk-hunt.grant, hunt/ dirs)
hunt.py: status/audit/check and the undo commands (unpin/rollback/end) always; anything else
only under a valid grant. `pitlink.py garage put|pin` always: those go through hunt.py.
While a grant is valid, the enforcement surface is frozen: no edits to tools/hunt/,
.claude/ settings or ~/.claude/hooks, and no git command that restores etk files from local history.

Belt and braces: the root-owned grant is the lock and the auto-mode classifier still runs.

CAR8 LIFT ACCESS (operator, 2026-10-24): car8 is a headless test car -- dead panel and touch,
nobody can see or change Pitstop on it, nothing on it to protect -- so it has standing,
unconditional access, with no grant and no signing step. A command whose ssh/scp/rsync target is
car8 (LIFT_HOSTS: its pinned ssh names; known_hosts holds car8's key, so another car at that
address fails the handshake before anything runs) may reboot it, power it off, and write any of
its state, hunt state included. install.sh / uninstall.sh / forge.sh / the lanes run freely while
this checkout targets car8 (etk.conf CAR="car8"; install.sh's own car check refuses any other unit).
Unchanged: publish (releases, tags -- other humans' machines), and power actions on this host or
any other car.
A crash here allows the call (a non-2 exit is a non-blocking hook error), never blocks it.
"""
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
HOME = os.path.expanduser("~")
ATOMS = re.compile(r"^(forge\.sh|install\.sh|uninstall\.sh|lane_[\w.-]+\.sh|build_[\w.-]+\.sh|etk-install\.ps1)$")
POWER = {"reboot", "poweroff", "halt", "shutdown", "kexec"}
WRAPPERS = {"sudo", "env", "nohup", "time", "exec", "command", "builtin", "setsid", "nice", "ionice",
            "stdbuf", "timeout", "xargs", "doas", "chrt", "taskset", "watch", "unbuffer"}
SHELLS = {"bash", "sh", "dash", "zsh", "ksh", "busybox", "pwsh", "powershell"}
PYTHONS = re.compile(r"^python[\d.]*$")
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;", "{", "}", "!"}
SSH_ARG_OPTS = set("bcDEeFIiJLlmOopQRSWw")
# The car's hunt state changes only through the garage daemon (validated) and Pitstop (the
# operator's switch): a remote shell that names it -- to flip Autonomy on, rewrite the rig
# grant, drop a core into hunt/ -- is self-escalation at the car.
CAR_HUNT_STATE = ("etk-autonomy", "etk-hunt.grant", "emulators/hunt", "drivers/hunt", "override.tsv")
REMOTE = {"ssh", "scp", "rsync", "sftp"}
# car8 lift access (docstring): the car, and the ssh names that reach it and only it
LIFT_CAR = "car8"
LIFT_HOSTS = {"etk-rig-usb", "etk-rig", "169.254.170.2", "sm8250.local"}


def conf_car(path=None):
    """etk.conf's CAR= (the car this checkout targets), or None."""
    try:
        text = open(path or os.path.join(ETK, "etk.conf")).read()
    except OSError:
        return None
    m = re.findall(r'^\s*CAR\s*=\s*"?([\w-]+)"?', text, re.M)
    return m[-1] if m else None


def remote_hosts(argv):
    """The hosts an ssh/scp/rsync/sftp argv reaches, lowercased, without user@."""
    b = os.path.basename(argv[0]) if argv else ""
    if b not in REMOTE:
        return []
    if b in ("ssh", "sftp"):
        i = 1
        while i < len(argv) and argv[i].startswith("-"):
            i += 2 if len(argv[i]) == 2 and argv[i][1] in SSH_ARG_OPTS else 1
        hosts = argv[i:i + 1]
    else:
        hosts = [a.split(":", 1)[0] for a in argv[1:] if not a.startswith("-") and ":" in a]
    return [h.rpartition("@")[2].lower() for h in hosts if h]


def lifted(argv):
    """True when every host this remote argv reaches is car8."""
    hosts = remote_hosts(argv)
    return bool(hosts) and all(h in LIFT_HOSTS for h in hosts)
HUNT_NO_GRANT = {"status", "audit", "check", "unpin", "rollback", "end", "-h", "--help", None}   # read or undo
# rewrite local files from local history; pull/merge/rebase (integrating origin, §1.6) stay open
GIT_TREE_WRITERS = {"checkout", "switch", "reset", "restore", "stash", "apply", "am", "cherry-pick",
                    "revert", "clean", "rm", "mv"}
FILE_WRITERS = {"cp", "mv", "rm", "tee", "truncate", "chmod", "chown", "ln", "install", "dd", "rsync",
                "touch", "unlink", "shred", "patch"}
PROTECTED = (os.path.join(ETK, "tools", "hunt") + os.sep, os.path.join(ETK, ".claude") + os.sep,
             os.path.join(HOME, ".claude", "hooks") + os.sep, os.path.join(HOME, ".claude", "settings.json"),
             os.path.join(HOME, ".claude", "settings.local.json"))


def protected(path, cwd):
    path = os.path.expanduser(path)
    if cwd is None and not os.path.isabs(path):
        return False                         # after `cd $VAR` a relative path is unknowable
    p = os.path.normpath(os.path.join(cwd or "/", path))
    return any(p == x.rstrip(os.sep) or p.startswith(x) for x in PROTECTED)


HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_]\w*)\2")   # not a <<< here-string


def split_heredocs(cmd):
    """-> (the command without heredoc bodies, [(opener line, body)]). A body is data unless
    it feeds a shell or ssh, so `git commit -F- <<EOF` text never reads as commands."""
    lines, main, docs, i = cmd.split("\n"), [], [], 0
    while i < len(lines):
        ln = lines[i]
        main.append(ln)
        i += 1
        for m in HEREDOC.finditer(ln):
            body = []
            while i < len(lines) and (lines[i].lstrip("\t") if m.group(1) else lines[i]) != m.group(3):
                body.append(lines[i])
                i += 1
            i += 1
            docs.append((ln, "\n".join(body)))
    return "\n".join(main), docs


def repo_root(d):
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        if d in ("/", ""):
            return None
        d = os.path.dirname(d)


def segments(cmd):
    """Simple commands of a shell string as token lists; a '>' token marks a write target."""
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    seg, out = [], []
    for tok in lex:
        if tok in SEPARATORS or tok == "$":
            if seg:
                out.append(seg)
            seg = []
        elif set(tok) <= set("<>&") and ">" in tok:
            if seg and seg[-1].isdigit():
                seg.pop()                    # the fd of 2>/dev/null is not an argument
            seg.append(">")
        elif set(tok) <= set("<&"):
            if seg and seg[-1].isdigit():
                seg.pop()
        else:
            seg.append(tok)
    if seg:
        out.append(seg)
    return out


VALUE_OPTS = {"-u", "-g", "-n", "-C", "-p", "-s", "-k", "-c", "-I", "-o", "-S", "-h", "-t", "-U", "-D"}


def unwrap(argv):
    """Strip env assignments and wrappers (sudo -u x, timeout 10, env A=b, ...) -> the argv run."""
    i = 0
    while i < len(argv):
        t = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", t):
            i += 1
        elif os.path.basename(t) in WRAPPERS:
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or re.match(r"^[A-Za-z_]\w*=", argv[i])
                                     or re.match(r"^\d+(\.\d+)?[smhd]?$", argv[i])):
                i += 2 if argv[i] in VALUE_OPTS else 1
        else:
            break
    return argv[i:]


def program(argv):
    """-> (argv, inline_script or None). Resolves `bash X`, `bash -c '...'`, `. X`, `source X`."""
    argv = unwrap(argv)
    if not argv:
        return argv, None
    b = os.path.basename(argv[0])
    if b in SHELLS or b in (".", "source"):
        rest = argv[1:]
        if b in SHELLS:
            flags = [k for k, r in enumerate(rest) if re.match(r"^-[a-zA-Z]+$", r)]
            if any("c" in rest[k] for k in flags):   # -c, -lc, -ec: the next word is the script
                j = next(k for k in flags if "c" in rest[k])
                return [], rest[j + 1] if j + 1 < len(rest) else None
            if any("n" in rest[k] for k in flags):
                return [], None                      # syntax check: nothing runs
            rest = [r for r in rest if not r.startswith("-")]
        return rest, None
    return argv, None


def ssh_inner(argv):
    if not argv or os.path.basename(argv[0]) != "ssh":
        return None
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if len(argv[i]) == 2 and argv[i][1] in SSH_ARG_OPTS else 1
    return " ".join(argv[i + 1:]) if i < len(argv) - 1 else None


def judge_argv(argv, cwd, grant_ok, depth, onrig=False, car8=lambda: False):
    """-> deny reason or None for one simple command. onrig: it runs ON car8 (reached over ssh);
    car8(): this checkout targets car8 -- the two halves of car8 lift access."""
    redirs = [argv[k + 1] for k, t in enumerate(argv[:-1]) if t == ">"]
    argv = [t for k, t in enumerate(argv) if t != ">" and (k == 0 or argv[k - 1] != ">")]
    argv, inline = program(argv)
    if inline is not None:
        return judge(inline, cwd, grant_ok, depth + 1, onrig, car8)
    if not argv:
        return None
    b = os.path.basename(argv[0])
    if b in REMOTE and not lifted(argv) and any(x in t for t in argv[1:] for x in CAR_HUNT_STATE):
        return ("the car's hunt state (Autonomy switch, rig grant, hunt/ files) changes only through "
                "tools/hunt/hunt.py and Pitstop; read it with `hunt.py status --probe`")
    inner = ssh_inner(argv)
    if inner:
        return judge(inner, "/", grant_ok, depth + 1, onrig or lifted(argv), car8)
    if ATOMS.match(b) and not (onrig or car8()):
        return (f"{b} is a bytes-to-atoms control (TRACK_MANUAL §1.1): the operator runs it. "
                f"Hand it over as a ```bash block")
    if (b in POWER or (b == "systemctl" and any(x in POWER or x == "soft-reboot" for x in argv[1:]))) \
            and not {"-h", "--help", "-v", "--version"} & set(argv[1:]) and not onrig:
        return "no reboots or power actions from the Engineer (TRACK_MANUAL §1.5); ask the operator"
    if b == "gh" and argv[1:2] == ["release"] and len(argv) > 2 and argv[2] in (
            "create", "upload", "edit", "delete", "delete-asset"):
        return "gh release is publish (TRACK_MANUAL §1.1): the operator runs it"
    if b == "git":
        j = 1
        while j < len(argv) and argv[j].startswith("-"):
            j += 2 if argv[j] in ("-C", "-c") else 1
        verb, rest = (argv[j], argv[j + 1:]) if j < len(argv) else (None, [])
        if verb == "tag":
            listing = any(r.split("=")[0] in ("-l", "--list", "--contains", "--no-contains", "--points-at",
                                              "--merged", "--no-merged", "-v", "--verify") or r.startswith("-n")
                          for r in rest)
            writes = any(r.split("=")[0] in ("-a", "--annotate", "-s", "--sign", "-u", "--local-user", "-f",
                                             "--force", "-m", "--message", "-F", "--file", "-d", "--delete")
                         for r in rest)
            if writes or (not listing and any(not r.startswith("-") for r in rest)):
                return "creating, moving or deleting a git tag is publish (TRACK_MANUAL §1.1): the operator does it"
        if verb == "push" and any(r in ("--tags", "--follow-tags", "--mirror") or "refs/tags/" in r
                                  or re.match(r"^:?v\d+\.\d+", r) for r in rest):
            return "pushing a tag is publish (TRACK_MANUAL §1.1): the operator does it"
        if verb in GIT_TREE_WRITERS and not (verb == "stash" and rest[:1] in (["list"], ["show"])) \
                and grant_ok():
            gdir = argv[argv.index("-C") + 1] if "-C" in argv[:j] else "."
            if (cwd is not None or os.path.isabs(gdir)) and \
                    repo_root(os.path.normpath(os.path.join(cwd or "/", os.path.expanduser(gdir)))) == ETK:
                return f"a hunt grant is active: `git {verb}` in the etk tree could rewrite the hunt's own guard"
    if b == "grant.sh" and "issue" in argv[1:]:
        return "only the operator signs a hunt grant (tools/hunt/grant.sh issue, at their terminal)"
    if PYTHONS.match(b):
        script = next((k for k, x in enumerate(argv[1:], 1) if not x.startswith("-")), None)
        if script is not None and "-c" not in argv[1:script] and "-m" not in argv[1:script]:
            argv, b = argv[script:], os.path.basename(argv[script])
    if b == "hunt" and next((x for x in argv[1:] if not x.startswith("-")), None) == "start":
        return "only the operator signs a hunt grant (./hunt start runs grant.sh issue, at their terminal)"
    if b == "hunt.py":
        sub = next((x for x in argv[1:] if not x.startswith("-")), None)
        if sub not in HUNT_NO_GRANT and not grant_ok() and not {"-h", "--help"} & set(argv[1:]):
            return f"hunt.py {sub} needs a valid hunt grant (tools/hunt/hunt.py status says why there is none)"
    if b == "pitlink.py" and argv[1:2] == ["garage"] and len(argv) > 2 and argv[2] in ("put", "pin") \
            and not car8():
        return f"garage {argv[2]} is a hunt act: run it as tools/hunt/hunt.py {argv[2]} (validated + audited)"
    if grant_ok():
        targets = list(redirs)
        if b in FILE_WRITERS or (b in ("sed", "perl") and any(x.startswith("-i") for x in argv[1:])):
            targets += [x for x in argv[1:] if not x.startswith("-")]
        if any(protected(t, cwd) for t in targets):
            return "a hunt grant is active: the hunt's guard, grant tools and settings are frozen until it ends"
    return None


def judge(cmd, cwd, grant_ok, depth=0, onrig=False, car8=lambda: False):
    if depth > 4:
        return None
    cmd, docs = split_heredocs(cmd)
    for opener, body in docs:
        head = opener.split("<<")[0]
        if re.search(r"(^|[\s;&|(/])(ssh|bash|sh|dash|zsh|busybox)\b", head):
            try:   # a heredoc fed to `ssh car8 …` runs ON car8: lifted like the ssh itself
                body_onrig = onrig or any(lifted(unwrap(s)) for s in segments(head) if s)
            except ValueError:
                body_onrig = onrig
            r = judge(body, "/", grant_ok, depth + 1, body_onrig, car8)
            if r:
                return r
    try:
        segs = segments(cmd)
    except ValueError:
        if re.search(r"(^|[;&|(]|\n)\s*(sudo\s+|bash\s+|sh\s+|\.\s+|source\s+)?[\w./~-]*"
                     r"(forge|install|uninstall|lane_\w+|build_\w+)\.sh\b", cmd):
            return "could not parse this command and it names a bytes-to-atoms script; rephrase it"
        return None
    for seg in segs:
        if seg and seg[0] == "cd":
            to = seg[1] if len(seg) > 1 else "~"
            cwd = None if "$" in to or "`" in to or (cwd is None and not os.path.isabs(os.path.expanduser(to))) \
                else os.path.normpath(os.path.join(cwd or "/", os.path.expanduser(to)))
            continue
        r = judge_argv(seg, cwd, grant_ok, depth, onrig, car8)
        if r:
            return r
    return None


def decide(payload, grant_ok, car8=None):
    """payload = the hook's stdin JSON; grant_ok() -> bool (lazy); car8() -> bool: this checkout
    targets car8 (default: etk.conf CAR=). -> deny reason or None."""
    if car8 is None:
        car8 = lambda: conf_car() == LIFT_CAR  # noqa: E731
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool == "Bash":
        return judge(ti.get("command", ""), cwd, grant_ok, car8=car8)
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if path and protected(path, cwd) and grant_ok():
            return "a hunt grant is active: the hunt's guard, grant tools and settings are frozen until it ends"
    return None


def real_grant_ok():
    sys.path.insert(0, HERE)
    import grantlib
    memo = []

    def ok():
        if not memo:
            g, _, p = grantlib.load_grant()
            memo.append(g is not None and not p)
        return memo[0]
    return ok


def main():
    payload = json.load(sys.stdin)
    reason = decide(payload, real_grant_ok())
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "ETK hunt guard: " + reason}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never block on our own bug
        print(f"ETK hunt guard error (call allowed): {e}", file=sys.stderr)
        sys.exit(1)
