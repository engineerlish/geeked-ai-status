"""A small JSON Schema validator for the subset that `schema.json` uses.

The collector must run on a bare Python install, so this avoids third-party packages. It
supports: type (single or list), enum, const, required, properties, additionalProperties
(boolean or schema), items, pattern, minimum, oneOf, and local `$ref` into `$defs`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.json"


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    raise ValueError(f"unsupported type in schema: {expected}")


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local references are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]) -> None:
    if "$ref" in schema:
        _validate(value, _resolve(schema["$ref"], root), root, path, errors)
        # Sibling keywords (like "description") are annotations only.
        return

    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            trial: list[str] = []
            _validate(value, option, root, path, trial)
            if not trial:
                matches += 1
        if matches != 1:
            errors.append(f"{path or '$'}: must match exactly one of the allowed shapes (matched {matches})")
        return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path or '$'}: must equal {schema['const']!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path or '$'}: {value!r} is not one of {schema['enum']}")
        return

    if "type" in schema:
        expected = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in expected):
            errors.append(f"{path or '$'}: expected {' or '.join(expected)}, got {type(value).__name__}")
            return

    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path or '$'}: {value!r} does not match {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path or '$'}: {value} is below the minimum {schema['minimum']}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path or '$'}: missing required property {name!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if key in properties:
                _validate(item, properties[key], root, child, errors)
            elif additional is False:
                errors.append(f"{child}: unexpected property")
            elif isinstance(additional, dict):
                _validate(item, additional, root, child, errors)

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], root, f"{path}[{index}]", errors)


def validate(instance: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """Returns a list of human-readable problems; an empty list means the instance is valid."""
    root = schema if schema is not None else load_schema()
    errors: list[str] = []
    _validate(instance, root, root, "", errors)
    return errors
