"""A small JSON Schema checker for tool arguments.

The subset implemented here is the one the tool manifests use: object
properties with types, required fields, string patterns, enums, numeric
bounds and string lengths. Anything richer belongs in a full validator; the
point of this module is that bad arguments are rejected *before* the
handler runs, with a message the user can act on.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["validate_params", "format_violations"]

_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "null": lambda value: value is None,
}


def format_violations(violations: list[tuple[str, str]]) -> str:
    """Render violations into one sentence the user can read."""
    if not violations:
        return ""
    return "；".join(f"{field}: {reason}" for field, reason in violations)


def validate_params(
    schema: dict[str, Any], args: dict[str, Any]
) -> list[tuple[str, str]]:
    """Check ``args`` against ``schema``; returns (field, reason) violations."""
    if not schema:
        return []
    violations: list[tuple[str, str]] = []
    properties: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    for name in required:
        if name not in args or args[name] is None:
            hint = _hint(properties.get(name))
            violations.append((name, hint or "缺少必填参数"))

    if schema.get("additionalProperties") is False:
        for name in args:
            if name not in properties:
                violations.append((name, "不支持的参数"))

    for name, value in args.items():
        spec = properties.get(name)
        if spec is None:
            continue
        violations.extend(_check_value(name, value, spec))
    return violations


def _check_value(name: str, value: Any, spec: dict[str, Any]) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    if value is None:
        return violations
    expected = spec.get("type")
    if isinstance(expected, str):
        check = _TYPE_CHECKS.get(expected)
        if check is not None and not check(value):
            violations.append((name, _hint(spec) or f"类型应为 {expected}"))
    if "enum" in spec and value not in spec["enum"]:
        allowed = "、".join(str(item) for item in spec["enum"])
        violations.append((name, _hint(spec) or f"取值应为 {allowed}"))
    if isinstance(value, str):
        pattern = spec.get("pattern")
        if pattern and not re.search(pattern, value):
            violations.append((name, _hint(spec) or "格式不正确"))
        if "maxLength" in spec and len(value) > int(spec["maxLength"]):
            violations.append((name, _hint(spec) or f"长度不能超过 {spec['maxLength']}"))
        if "minLength" in spec and len(value) < int(spec["minLength"]):
            violations.append((name, _hint(spec) or f"长度不能少于 {spec['minLength']}"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "maximum" in spec and value > spec["maximum"]:
            violations.append((name, _hint(spec) or f"不能大于 {spec['maximum']}"))
        if "minimum" in spec and value < spec["minimum"]:
            violations.append((name, _hint(spec) or f"不能小于 {spec['minimum']}"))
    if isinstance(value, list):
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for index, item in enumerate(value):
                violations.extend(_check_value(f"{name}[{index}]", item, item_spec))
    if isinstance(value, dict):
        nested = spec.get("properties")
        if isinstance(nested, dict):
            for key, nested_spec in nested.items():
                if key in value:
                    violations.extend(_check_value(f"{name}.{key}", value[key], nested_spec))
    return violations


def _hint(spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    hint = spec.get("hint")
    return str(hint) if isinstance(hint, str) else ""
