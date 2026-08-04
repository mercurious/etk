#!/usr/bin/env python3
"""ETK patch plumbing — dependency-free readers/writers for RPCS3's community
patch machinery (multigame lane, PostReleaseLane_MultiGameCommunity_20260803 §3).

NOT a new patch system: RPCS3 already ships the framework. This module only
surfaces it — parse the community patch.yml for the selected title, read/write
the engine's own patch_config.yml enablement file (the same file the desktop
GUI writes), and maintain the ETK per-serial pin TSV the launch wrapper stamps
into the ledger's tune_tag.

Ground truth (verified against the 0.8.1 fork source, Utilities/bin_patch.*):
  patches file : <rpcs3-config>/patches/patch.yml   (append_global_patches)
  enablement   : <rpcs3-config>/patch_config.yml    (get_patch_config_path;
                 the bool(true) Linux subdir arg is [[maybe_unused]])
  schema       : {hash: {description: {title: {serial: {app_ver:
                 {Enabled: bool, Configurable Values: {...}}}}}}}
  wildcards    : "All" (patch_key::all) for title/serial/app_version
  engine ver   : "1.2" (patch_engine_version)

The rig ships no PyYAML, and both files are machine-generated with regular
2-space indentation — so these are targeted indent-walking parsers, defensive
everywhere: a patch entry that doesn't parse is SKIPPED (fail-soft, logged by
the caller), never a crash. Patches apply at game boot, so every toggle here
is launch-cadence — no reboot, the profile.d rhythm.

ANTI-TRAP (dossier §3): upstream silently no-ops a patch enabled under the
wrong app version. We defuse it structurally: enabling a patch writes an
Enabled leaf under EVERY app version the patch declares for the serial (plus
"All" when declared), so whichever version the rig actually boots, the engine
finds its key. The engine only ever applies the version it matched.
"""

import os
import re


PATCH_ENGINE_VERSION = "1.2"
ALL_KEY = "All"

# Top-level keys in patch.yml that are not patch hashes.
_NON_HASH_KEYS = {"Version", "Anchors"}
# Keys inside a patch entry that are metadata, not game titles.
_PATCH_META_KEYS = {"Games", "Author", "Notes", "Patch Version", "Group",
                    "Patch", "Configurable Values"}


def _dequote(s):
    """Strip one layer of YAML quoting from a scalar/key."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    return s


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _split_key(line):
    """Split a 'key: value' line into (key, value) with quote awareness —
    a ':' inside a quoted key must not split it. Returns (None, None) when
    the line isn't a mapping entry."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None, None
    if s[0] in ("'", '"'):
        q = s[0]
        i = 1
        while i < len(s):
            if q == '"' and s[i] == "\\":
                i += 2
                continue
            if s[i] == q:
                # single-quote escaping doubles the quote
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                break
            i += 1
        rest = s[i + 1:].lstrip()
        if not rest.startswith(":"):
            return None, None
        return _dequote(s[:i + 1]), rest[1:].strip()
    if ":" not in s:
        return None, None
    k, v = s.split(":", 1)
    return k.strip(), v.strip()


def _parse_inline_list(v):
    """'[ 01.00, All ]' -> ['01.00', 'All']. Returns None if not inline."""
    v = v.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return None
    inner = v[1:-1].strip()
    if not inner:
        return []
    return [_dequote(x) for x in inner.split(",")]


def parse_patch_yml(path, serial):
    """Return the list of community patches that declare `serial` explicitly.

    Each entry: {hash, description, title, versions: [app_ver, ...],
                 notes, author, patch_version, has_config_values}.
    Missing file / no matches / malformed entries -> [] or fewer entries;
    never raises for content reasons (only propagates OSError on open so the
    caller can distinguish 'no file yet' from 'unreadable')."""
    if not os.path.isfile(path):
        return []
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()

    patches = []
    cur_hash = None
    cur = None            # patch entry being accumulated
    in_games = False
    cur_title = None
    games_indent = title_indent = serial_indent = None
    pending_serial = None  # serial awaiting a block-list of versions

    def flush():
        nonlocal cur
        if cur and cur.get("versions"):
            patches.append(cur)
        cur = None

    for raw in lines:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        ind = _indent_of(raw)
        key, val = _split_key(raw)

        # Block-list continuation for a matched serial's app versions
        # ("- 01.00" lines directly under "SERIAL:").
        if pending_serial is not None:
            if raw.strip().startswith("- ") and ind > (serial_indent or 0):
                if cur is not None:
                    v = _dequote(raw.strip()[2:])
                    if v not in cur["versions"]:
                        cur["versions"].append(v)
                continue
            pending_serial = None

        if ind == 0:
            flush()
            cur_hash = None
            in_games = False
            cur_title = None
            if key is not None and key not in _NON_HASH_KEYS and val == "":
                cur_hash = key
            continue

        if cur_hash is None or key is None:
            continue

        if ind == 2 and val == "":
            # New patch description under the current hash.
            flush()
            in_games = False
            cur_title = None
            cur = {"hash": cur_hash, "description": key, "title": None,
                   "versions": [], "notes": "", "author": "",
                   "patch_version": "", "has_config_values": False}
            continue

        if cur is None:
            continue

        if ind == 4:
            in_games = (key == "Games" and val == "")
            games_indent = ind if in_games else None
            if key == "Notes":
                cur["notes"] = _dequote(val)
            elif key == "Author":
                cur["author"] = _dequote(val)
            elif key == "Patch Version":
                cur["patch_version"] = _dequote(val)
            elif key == "Configurable Values":
                cur["has_config_values"] = True
            continue

        if in_games and ind == 6 and val == "":
            cur_title = key
            title_indent = ind
            continue

        if in_games and cur_title is not None and ind == 8:
            # SERIAL: [ versions ]  |  SERIAL:   (block list follows).
            # Serial keys in patch.yml are undashed; tolerate a dashed one
            # anyway (community files are hand-edited upstream). Serial-
            # explicit matches ONLY — "ALL"-serial global patches are out of
            # scope for the per-title surface (v1, deliberate).
            if key is None or key.replace("-", "") != serial:
                continue
            if cur.get("title") is None:
                cur["title"] = cur_title
            vers = _parse_inline_list(val) if val else None
            if vers is not None:
                cur["versions"].extend(v for v in vers
                                       if v not in cur["versions"])
            elif val == "":
                serial_indent = ind
                pending_serial = key
            continue

    flush()
    return patches


def patch_slug(description, max_len=20):
    """Compact ledger token for a patch description — MUST stay stable
    (tune_tag groups by it): lowercase alnum words joined by '_', truncated
    at a word boundary so the tag never ends mid-token."""
    words = [w for w in
             re.sub(r"[^a-z0-9]+", "_", description.lower()).split("_") if w]
    out = ""
    for w in words:
        cand = f"{out}_{w}" if out else w
        if len(cand) > max_len:
            break
        out = cand
    if not out:
        out = words[0][:max_len] if words else "patch"
    return out


# --- patch_config.yml (the engine's own enablement file) ---

def read_patch_config(path):
    """Parse the Enabled leaves of patch_config.yml into
    {hash: {description: {title: {serial: {app_ver: True}}}}}.

    Configurable Values subtrees are NOT preserved (on a ROCKNIX rig the Qt
    patch manager that writes them is never used; the caller logs when any
    are dropped). Returns ({}, dropped_count) on missing file."""
    tree = {}
    dropped = 0
    if not os.path.isfile(path):
        return tree, dropped
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()

    # Walk the fixed 5-deep shape: hash/desc/title/serial/app_ver/Enabled.
    stack = [None] * 5  # hash, desc, title, serial, app_ver
    for raw in lines:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        ind = _indent_of(raw)
        key, val = _split_key(raw)
        if key is None:
            continue
        depth = ind // 2
        if depth <= 4:
            if val == "":
                if 0 <= depth < 5:
                    stack[depth] = key
                    for d in range(depth + 1, 5):
                        stack[d] = None
                continue
            # scalar at unexpected depth (e.g. "Version:") — ignore
            continue
        if depth == 5:
            if key == "Enabled" and val.lower() == "true" and all(stack):
                h, d, t, s, a = stack
                tree.setdefault(h, {}).setdefault(d, {}) \
                    .setdefault(t, {}).setdefault(s, {})[a] = True
            elif key == "Configurable Values":
                dropped += 1
        # depth > 5 = config value leaves — counted via their parent above
    return tree, dropped


def _yaml_key(s):
    """Emit a mapping key, quoted whenever it isn't a plainly safe scalar.
    Numeric-looking keys (app versions like 01.00) are ALWAYS quoted so no
    YAML implementation can resolve them to a number — the engine matches
    app versions as strings."""
    if (re.fullmatch(r"[A-Za-z0-9._][A-Za-z0-9._ \-]*", s)
            and not s.endswith(" ")
            and not re.fullmatch(r"[0-9.]+", s)
            and s.lower() not in ("true", "false", "null", "yes", "no", "on", "off")):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_patch_config(path, tree):
    """Emit the enablement tree wholesale (atomic tmp+mv). Empty branches are
    pruned; an empty tree still writes a valid (empty) file so a disable-all
    round-trips. Returns True when a read-back reproduces the tree."""
    out = []
    for h in sorted(tree):
        body_h = []
        for d in sorted(tree[h]):
            body_d = []
            for t in sorted(tree[h][d]):
                body_t = []
                for s in sorted(tree[h][d][t]):
                    body_s = []
                    for a in sorted(tree[h][d][t][s]):
                        if tree[h][d][t][s][a]:
                            body_s.append(f"        {_yaml_key(a)}:")
                            body_s.append("          Enabled: true")
                    if body_s:
                        body_t.append(f"      {_yaml_key(s)}:")
                        body_t.extend(body_s)
                if body_t:
                    body_d.append(f"    {_yaml_key(t)}:")
                    body_d.extend(body_t)
            if body_d:
                body_h.append(f"  {_yaml_key(d)}:")
                body_h.extend(body_d)
        if body_h:
            out.append(f"{_yaml_key(h)}:")
            out.extend(body_h)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out) + ("\n" if out else ""))
    os.replace(tmp, path)

    got, _ = read_patch_config(path)
    want = {h: {d: {t: {s: {a: True
                            for a, on in av.items() if on}
                        for s, av in ts.items()
                        if any(av.values())}
                    for t, ts in dt.items()
                    if any(any(av.values()) for av in ts.values())}
                for d, dt in hd.items()
                if any(any(any(av.values()) for av in ts.values())
                       for ts in dt.values())}
            for h, hd in tree.items()
            if any(any(any(any(av.values()) for av in ts.values())
                       for ts in dt.values()) for dt in hd.values())}
    return got == want


def set_patch_enabled(tree, patch, serial, enabled):
    """Apply one toggle to the enablement tree, in place. Writes an Enabled
    leaf under EVERY app version the patch declares for this serial (plus
    All when declared) — the version anti-trap defusal; disable prunes the
    (hash, description, title, serial) branch entirely."""
    h, d, t = patch["hash"], patch["description"], patch["title"] or ALL_KEY
    if enabled:
        for a in (patch["versions"] or [ALL_KEY]):
            tree.setdefault(h, {}).setdefault(d, {}) \
                .setdefault(t, {}).setdefault(serial, {})[a] = True
    else:
        try:
            del tree[h][d][t][serial]
            if not tree[h][d][t]:
                del tree[h][d][t]
            if not tree[h][d]:
                del tree[h][d]
            if not tree[h]:
                del tree[h]
        except KeyError:
            pass


def patch_is_enabled(tree, patch, serial):
    """True when ANY app-version leaf for (patch, serial) is enabled."""
    t = patch["title"] or ALL_KEY
    av = tree.get(patch["hash"], {}).get(patch["description"], {}) \
             .get(t, {}).get(serial, {})
    return any(av.values())


# --- ETK per-serial pin TSV (the wrapper's tune_tag feed) ---

def write_patch_pins(path, serial, slugs):
    """Upsert serial -> comma-joined slugs (empty slug list removes the
    row). Atomic; same exception-list shape as core_map.tsv."""
    rows = {}
    try:
        with open(path) as f:
            for ln in f:
                if ln.startswith("#"):
                    continue
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 2 and parts[0] and parts[1]:
                    rows[parts[0]] = parts[1]
    except OSError:
        pass
    if slugs:
        rows[serial] = ",".join(slugs)
    else:
        rows.pop(serial, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("# serial<TAB>patch-slugs — written by Pitstop TUNING > PATCH;"
                " read by etk-rpcs3-launch.sh for the ledger patches= tag\n")
        for s in sorted(rows):
            f.write(f"{s}\t{rows[s]}\n")
    os.replace(tmp, path)
    return True
