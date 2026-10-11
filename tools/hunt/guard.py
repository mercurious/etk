#!/usr/bin/env python3
"""guard.py -- PreToolUse hook: bytes-to-atoms controls stay the operator's (docs/AUTONOMY_SPEC.md §3.2).

Registered in .claude/settings.local.json on matcher Bash|Edit|Write|MultiEdit|NotebookEdit.
It sees only the Engineer's tool calls; the operator's own commands (shell mode, the Run
button's terminal) never pass through it.

Always denied (TRACK_MANUAL §1.1, §1.5), grant or not:
  running forge.sh, tools/forge/lane_*.sh, tools/rocknix-bin/build_*.sh, install.sh,
  uninstall.sh, etk-install.ps1 (a `bash -n` syntax check is fine; reading them is fine)
  gh release create|upload|edit|delete|delete-asset · creating/deleting a git tag ·
  pushing tags · reboot/poweroff/halt (locally or over ssh) · grant.sh issue
hunt.py: status/audit/check always; anything else only under a valid grant.
While a grant is valid, the enforcement surface is frozen: no edits to tools/hunt/,
.claude/ settings or ~/.claude/hooks, and no git command that restores etk files from local history.

Belt and braces: the root-owned grant is the lock and the auto-mode classifier still runs.
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
HUNT_READ_ONLY = {"status", "audit", "check", "-h", "--help", None}
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


def judge_argv(argv, cwd, grant_ok, depth):
    """-> deny reason or None for one simple command."""
    redirs = [argv[k + 1] for k, t in enumerate(argv[:-1]) if t == ">"]
    argv = [t for k, t in enumerate(argv) if t != ">" and (k == 0 or argv[k - 1] != ">")]
    argv, inline = program(argv)
    if inline is not None:
        return judge(inline, cwd, grant_ok, depth + 1)
    if not argv:
        return None
    b = os.path.basename(argv[0])
    inner = ssh_inner(argv)
    if inner:
        return judge(inner, "/", grant_ok, depth + 1)
    if ATOMS.match(b):
        return (f"{b} is a bytes-to-atoms control (TRACK_MANUAL §1.1): the operator runs it. "
                f"Hand it over as a ```bash block")
    if (b in POWER or (b == "systemctl" and any(x in POWER or x == "soft-reboot" for x in argv[1:]))) \
            and not {"-h", "--help", "-v", "--version"} & set(argv[1:]):
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
    if b == "hunt.py":
        sub = next((x for x in argv[1:] if not x.startswith("-")), None)
        if sub not in HUNT_READ_ONLY and not grant_ok() and not {"-h", "--help"} & set(argv[1:]):
            return f"hunt.py {sub} needs a valid hunt grant (tools/hunt/hunt.py status says why there is none)"
    if grant_ok():
        targets = list(redirs)
        if b in FILE_WRITERS or (b in ("sed", "perl") and any(x.startswith("-i") for x in argv[1:])):
            targets += [x for x in argv[1:] if not x.startswith("-")]
        if any(protected(t, cwd) for t in targets):
            return "a hunt grant is active: the hunt's guard, grant tools and settings are frozen until it ends"
    return None


def judge(cmd, cwd, grant_ok, depth=0):
    if depth > 4:
        return None
    cmd, docs = split_heredocs(cmd)
    for opener, body in docs:
        if re.search(r"(^|[\s;&|(/])(ssh|bash|sh|dash|zsh|busybox)\b", opener.split("<<")[0]):
            r = judge(body, "/", grant_ok, depth + 1)
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
        r = judge_argv(seg, cwd, grant_ok, depth)
        if r:
            return r
    return None


def decide(payload, grant_ok):
    """payload = the hook's stdin JSON; grant_ok() -> bool (lazy). -> deny reason or None."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool == "Bash":
        return judge(ti.get("command", ""), cwd, grant_ok)
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
