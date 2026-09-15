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
_ID_RE = re.compile(r"#([^.#\s\[:]+)")
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
    # Split off `#id` first so `Type#id`, `#id.class` and `.class#id` all work.
    # Only the first `#id` is significant (Qt objectNames are unique per widget).
    id_match = _ID_RE.search(stripped)
    obj_name: str | None = id_match.group(1) or None if id_match else None
    if id_match:
        stripped = stripped[: id_match.start()] + stripped[id_match.end() :]
    parts = stripped.split(".")
    head = parts[0]
    classes = frozenset(c for c in parts[1:] if c)
    if not head or head == "*":
        return CompiledSegment(obj_name, None, classes, attrs)
    return CompiledSegment(obj_name, head, classes, attrs)


def compute_specificity(base_selector: str, pseudo_set: frozenset[str]) -> tuple[int, int, int]:
    """CSS2 a-b-c specificity for one rule (Qt docs follow CSS2).

    a = #ID selectors, b = .classes + [attrs] + :pseudos, c = Type names.
    Sub-controls (`::...`) and universal `*` contribute nothing.
    """
    a = 0
    b = len(pseudo_set)
    c = 0
    for segment in base_selector.split():
        # One [attr] block = one b, even for unsupported operators.
        b += len(parse_attr_conditions(segment))
        no_attrs = _ATTR_RE.sub("", segment)
        # IDs (after Type#id fix there is at most one per segment, but count all).
        ids = _ID_RE.findall(no_attrs)
        a += len(ids)
        no_id = _ID_RE.sub("", no_attrs)
        # Sub-control name after `::` is a pseudo-element: ignored.
        no_sub = no_id.split("::", 1)[0]
        # Single-colon pseudos should already be split off, but be defensive:
        # anything after a leftover `:` is not a type name.
        head = no_sub.split(":", 1)[0]
        parts = head.split(".")
        tag = parts[0].strip()
        if tag and tag != "*":
            c += 1
        b += sum(1 for cls in parts[1:] if cls.strip())
    return (a, b, c)
