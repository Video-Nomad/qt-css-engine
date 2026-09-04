"""Selector compilation — single segment → objectName/tag/classes."""

from typing import NamedTuple


class CompiledSegment(NamedTuple):
    obj_name: str | None
    tag: str | None
    classes: frozenset[str]


class WidgetIdentity(NamedTuple):
    tag: str
    obj_name: str
    classes: frozenset[str]


def compile_segment(segment: str) -> CompiledSegment:
    parts = segment.split(".")
    head = parts[0]
    classes = frozenset(parts[1:])
    if head.startswith("#"):
        return CompiledSegment(head[1:], None, classes)
    return CompiledSegment(None, head or None, classes)
