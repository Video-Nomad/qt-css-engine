"""Selector compilation — single segment → objectName/tag/classes/attrs."""

import re
from typing import NamedTuple


class AttrCondition(NamedTuple):
    """One `[name]` / `[name=value]` condition inside a selector segment."""

    name: str
    op: str  # "=" or "exists" (other operators parse but never match)
    value: str | None


class CompiledSegment(NamedTuple):
    obj_name: str | None
    tag: str | None
    classes: frozenset[str]
    attrs: tuple[AttrCondition, ...] = ()


class WidgetIdentity(NamedTuple):
    tag: str
    obj_name: str
    classes: frozenset[str]


_ATTR_RE = re.compile(r"\[([^\]]+)\]")
# name, optional operator (~=, |=, ^=, $=, *=, =, !=), optional value (quoted or bare)
_ATTR_CONTENT_RE = re.compile(
    r"""^\s*([A-Za-z_][\w\-.]*)          # property name
        (?:\s*([~\|\^\$\*!]?=)\s*         # optional operator
        (?:\"([^\"]*)\"|'([^']*)'|([^\s'\"\]]+))  # double-quoted | single-quoted | bare
        \s*)?$""",
    re.VERBOSE,
)


def parse_attr_conditions(segment: str) -> tuple[AttrCondition, ...]:
    """Extract `[name]` / `[name=value]` conditions from one selector segment."""
    conditions: list[AttrCondition] = []
    for m in _ATTR_RE.finditer(segment):
        content = m.group(1)
        parsed = _ATTR_CONTENT_RE.match(content)
        if parsed is None:
            continue
        name, op, dq, sq, bare = parsed.groups()
        if op is None:
            conditions.append(AttrCondition(name, "exists", None))
        elif op == "=":
            value = dq if dq is not None else (sq if sq is not None else bare)
            conditions.append(AttrCondition(name, "=", value if value is not None else ""))
        else:
            # Unsupported operator (~=, |=, ^=, $=, *=, !=): parse but never match.
            conditions.append(AttrCondition(name, op, None))
    return tuple(conditions)


def stringify_property(value: object) -> str | None:
    """Convert a Qt dynamic-property value to its QSS comparison string."""
    if value is None:
        return None
    # QVariant-invalid maps to None in PyQt; bool must precede int (bool subclasses int).
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return None
    return str(value)


def attr_matches(actual: object, cond: AttrCondition) -> bool:
    """Evaluate one AttrCondition against a live widget.property() value."""
    if cond.op == "exists":
        return actual is not None
    if cond.op != "=":
        return False
    if actual is None:
        return False
    actual_str = stringify_property(actual)
    return actual_str is not None and actual_str == (cond.value or "")


def compile_segment(segment: str) -> CompiledSegment:
    stripped = _ATTR_RE.sub("", segment)
    attrs = parse_attr_conditions(segment)
    parts = stripped.split(".")
    head = parts[0]
    classes = frozenset(c for c in parts[1:] if c)
    if head.startswith("#"):
        return CompiledSegment(head[1:] or None, None, classes, attrs)
    return CompiledSegment(None, head or None, classes, attrs)
