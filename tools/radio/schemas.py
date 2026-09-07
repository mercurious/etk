#!/usr/bin/env python3
"""ETK RADIO — the data contracts and a stdlib validator for them.

RADIO spec (docs/RADIO_SPEC.md) 3: the pack the rig sends and the debrief the node
sends back are CONTRACTS, and both ends check them. The node also hands the debrief
schema to Ollama as `format:` for constrained decoding, so the same file that
validates is the file that constrains. There is no jsonschema on the rig, none on the
node's stdlib service, and none in this repo's dependency budget (python stdlib only),
so this is a small validator over exactly the vocabulary the two schemas use.

API (kept tiny on purpose - service.py imports it):

    load(name)                  -> schema dict; name is "pack.v1" or "debrief.v1"
                                   (a bare short name, no path, no .json)
    validate(obj, schema)       -> list[str]; EMPTY means valid. Each entry reads
                                   "<json path>: <what is wrong>", e.g.
                                   "$.findings[0].kind: not one of
                                   ['observation', 'mechanism', 'anomaly']".
    SCHEMA_DIR                  -> the directory the JSON files live in.

Supported draft-07 vocabulary (everything the two schemas use, and nothing else -
an unknown keyword is IGNORED, never an error, so a schema can carry `title`,
`description` and `$comment` for the humans):

    type (string or list, incl. "null" for nullable)   required
    properties      additionalProperties (bool)        enum
    maxLength       minLength                          pattern (re.search)
    minimum         maximum                            items (single subschema)
    minItems        maxItems                           $ref -> "#/definitions/X"
                                                              or "#/$defs/X"

Validation is EXHAUSTIVE, not fail-fast: every error in the document comes back, so
one call tells the whole story. Callers that only want a headline take errs[0].

Notes on the type checks, because JSON and python disagree in two places:
  - `True` is an `int` in python; it is NOT accepted for "integer"/"number" here.
  - "integer" accepts a float that is integral (1.0), the way JSON Schema does.

Self-test:  python3 tools/radio/schemas.py --selftest
"""
import json
import os
import re
import sys

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
}

_cache = {}


def load(name):
    """Return the schema registered under a short name ("pack.v1", "debrief.v1")."""
    if name in _cache:
        return _cache[name]
    if not re.match(r"^[A-Za-z0-9_.-]+$", name) or "/" in name:
        raise ValueError("bad schema name: %r" % (name,))
    path = os.path.join(SCHEMA_DIR, name + ".json")
    with open(path, encoding="utf-8") as fh:
        _cache[name] = json.load(fh)
    return _cache[name]


def _resolve(ref, root):
    """#/definitions/<name> or #/$defs/<name> -> the subschema."""
    if not ref.startswith("#/"):
        raise ValueError("only local $ref is supported: %r" % (ref,))
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValueError("unresolvable $ref: %r" % (ref,))
        node = node[part]
    return node


def _check(obj, schema, path, root, errs):
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        _check(obj, _resolve(schema["$ref"], root), path, root, errs)
        return

    t = schema.get("type")
    if t is not None:
        allowed = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(a, lambda v: True)(obj) for a in allowed):
            errs.append("%s: expected %s, got %s" % (path, "|".join(allowed),
                                                     _name_of(obj)))
            return                      # every other keyword assumes the type held

    if "enum" in schema and obj not in schema["enum"]:
        errs.append("%s: not one of %s" % (path, schema["enum"]))

    if isinstance(obj, str):
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append("%s: %d chars, max %d" % (path, len(obj), schema["maxLength"]))
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append("%s: %d chars, min %d" % (path, len(obj), schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append("%s: does not match %s" % (path, schema["pattern"]))

    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append("%s: %s below minimum %s" % (path, obj, schema["minimum"]))
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append("%s: %s above maximum %s" % (path, obj, schema["maximum"]))

    if isinstance(obj, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in obj:
                errs.append("%s: missing required property '%s'" % (path, key))
        extra_ok = schema.get("additionalProperties", True)
        for key, val in obj.items():
            child = "%s.%s" % (path, key)
            if key in props:
                _check(val, props[key], child, root, errs)
            elif extra_ok is False:
                errs.append("%s: property not in the contract" % child)
            elif isinstance(extra_ok, dict):
                _check(val, extra_ok, child, root, errs)

    if isinstance(obj, list):
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append("%s: %d items, min %d" % (path, len(obj), schema["minItems"]))
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append("%s: %d items, max %d" % (path, len(obj), schema["maxItems"]))
        item = schema.get("items")
        if isinstance(item, dict):
            for i, v in enumerate(obj):
                _check(v, item, "%s[%d]" % (path, i), root, errs)


def _name_of(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, str):
        return "string"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def validate(obj, schema, path="$"):
    """Validate `obj` against `schema`. Returns a list of messages; [] means valid."""
    errs = []
    _check(obj, schema, path, schema, errs)
    return errs


# --------------------------------------------------------------------------- selftest
def _selftest():
    """Every check needs an exemplar that passes and a counter that fails."""
    fails = []

    def case(label, obj, schema, want_ok, needle=""):
        errs = validate(obj, schema)
        ok = not errs
        if ok != want_ok or (needle and not any(needle in e for e in errs)):
            fails.append(label)
            print("  FAIL %-46s %s" % (label, errs or "(no errors)"))
        else:
            print("  ok   %-46s %s" % (label, "" if ok else errs[0]))

    s = {"type": "object", "additionalProperties": False,
         "required": ["a"],
         "properties": {
             "a": {"type": "string", "maxLength": 3, "pattern": "^[ -~]*$"},
             "n": {"type": ["integer", "null"], "minimum": 0, "maximum": 10},
             "e": {"enum": ["x", "y"]},
             "l": {"type": "array", "items": {"$ref": "#/definitions/leaf"},
                   "maxItems": 2},
         },
         "definitions": {"leaf": {"type": "object", "required": ["k"],
                                  "properties": {"k": {"type": "boolean"}},
                                  "additionalProperties": False}}}

    case("exemplar passes", {"a": "abc", "n": 3, "e": "x",
                             "l": [{"k": True}]}, s, True)
    case("missing required", {"n": 1}, s, False, "missing required property 'a'")
    case("foreign key", {"a": "ab", "z": 1}, s, False, "$.z")
    case("maxLength", {"a": "abcd"}, s, False, "max 3")
    case("non-ASCII pattern", {"a": "abé"}, s, False, "does not match")
    case("wrong type", {"a": 7}, s, False, "expected string")
    case("nullable accepts null", {"a": "ab", "n": None}, s, True)
    case("minimum", {"a": "ab", "n": -1}, s, False, "below minimum")
    case("maximum", {"a": "ab", "n": 99}, s, False, "above maximum")
    case("bad enum", {"a": "ab", "e": "z"}, s, False, "not one of")
    case("$ref item checked", {"a": "ab", "l": [{"k": "no"}]}, s, False,
         "$.l[0].k: expected boolean")
    case("$ref foreign key", {"a": "ab", "l": [{"k": True, "q": 1}]}, s, False,
         "$.l[0].q")
    case("maxItems", {"a": "ab", "l": [{"k": True}, {"k": True}, {"k": True}]}, s,
         False, "max 2")
    case("bool is not an integer", {"a": "ab", "n": True}, s, False,
         "expected integer|null")

    for name in ("pack.v1", "debrief.v1"):
        try:
            sch = load(name)
            ok = isinstance(sch, dict) and sch.get("type") == "object"
        except Exception as e:                                  # noqa: BLE001
            ok, sch = False, e
        print("  %s %-46s %s" % ("ok  " if ok else "FAIL", "load(%r)" % name,
                                 "" if ok else sch))
        if not ok:
            fails.append("load %s" % name)

    print()
    if fails:
        print("FAILED: %d check(s) -> %s" % (len(fails), fails))
        return 1
    print("ALL SCHEMAS SELF-TEST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit("usage: schemas.py --selftest   (this module is a library; see __doc__)")
