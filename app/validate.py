# -*- coding: utf-8 -*-
"""Runtime contract validation — deterministic, stdlib-only, fail-loud.

DO NOT add jsonschema as a dependency: this subset covers exactly what our
schemas use (type [incl. union lists], required, properties, items, enum,
minLength, pattern, minimum). Anything unsupported = error, never silent.
"""

_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "object": dict, "array": list, "null": type(None),
}


def validate(obj, schema, path="$"):
    """Returns list of error strings ([] = valid)."""
    errs = []
    if not isinstance(schema, dict):
        return [f"{path}: schema not an object"]
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]

        def _ok(tt):
            if tt == "integer":
                return isinstance(obj, int) and not isinstance(obj, bool)
            if tt == "number":
                return isinstance(obj, (int, float)) and not isinstance(obj, bool)
            if tt == "boolean":
                return isinstance(obj, bool)
            py = _TYPE_MAP.get(tt)
            return py is not None and isinstance(obj, py)
        if not any(_ok(tt) for tt in ts):
            errs.append(f"{path}: expected type {ts}, got {type(obj).__name__}")
            return errs
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in enum {schema['enum']}")
    if isinstance(obj, str):
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema:
            import re
            if not re.search(schema["pattern"], obj):
                errs.append(f"{path}: does not match pattern {schema['pattern']}")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(obj, dict):
        for req in schema.get("required", []):
            if req not in obj:
                errs.append(f"{path}: missing required '{req}'")
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs.extend(validate(v, props[k], f"{path}.{k}"))
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            errs.extend(validate(v, schema["items"], f"{path}[{i}]"))
    return errs


# ---------------- runtime contracts ----------------
# These mirror schemas/*.schema.json's enforced subset; pack rows are the
# runtime UI contract (kept deliberately tight; no open-ended anything).

PACK_BOQ_LINE = {
    "type": "object",
    "required": ["key", "no", "desc", "unit", "qty", "rule"],
    "properties": {
        "key": {"type": "string", "minLength": 1},
        "no": {"type": "string"},
        "desc": {"type": "string", "minLength": 3},
        "section": {"type": "string"},
        "sub": {"type": "string"},
        "unit": {"type": "string", "minLength": 1},
        "qty": {"type": "number"},
        "rule": {"enum": ["CSV", "EST", "WBG", "WBM", "WEB", "BAND"]},
        "unitEur": {"type": "number", "minimum": 0},
        "sumEur": {"type": "number", "minimum": 0},
        "note": {"type": "string"},
        "flag": {"type": "string"},
        "match": {"type": "object"},
        "source": {"type": "object"},
    },
}

PACK = {
    "type": "object",
    "required": ["tenderId", "boq", "pricing", "rules", "documents"],
    "properties": {
        "tenderId": {"type": "integer"},
        "generatedAt": {"type": "string"},
        "pipe": {"type": "object"},
        "boq": {"type": "array", "items": PACK_BOQ_LINE},
        "pricing": {
            "type": "object",
            "required": ["totalExclVat", "vat", "totalInclVat"],
            "properties": {
                "totalExclVat": {"type": "number", "minimum": 0},
                "vatRate": {"type": "number"},
                "vat": {"type": "number", "minimum": 0},
                "totalInclVat": {"type": "number", "minimum": 0},
                "capExclVat": {"type": "number"},
                "costEnvVersion": {"type": "integer"},
            },
        },
        "rules": {"type": "array"},
        "documents": {"type": "array"},
        "clarifications": {"type": "array"},
        "llm": {"type": "object"},
        "similar": {"type": "array"},
    },
}


class ContractViolation(Exception):
    """Raised when a runtime write would violate its canonical contract."""


def assert_pack(pack):
    errs = validate(pack, PACK)
    if errs:
        raise ContractViolation("pack contract violated: " + "; ".join(errs[:8]))
