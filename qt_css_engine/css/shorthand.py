"""Shorthand expansion — border / padding / margin / border-radius."""

import re

import tinycss2
from tinycss2.ast import Node

from qt_css_engine.constants import BORDER_STYLE_KEYWORDS, BORDER_WIDTH_KEYWORDS, SHORTHAND_SIDES
from qt_css_engine.css.properties import is_animatable

_LEADING_DIGIT_RE = re.compile(r"^\d")


def _serialize_value(tokens: list[Node]) -> str:
    return tinycss2.serialize(tokens).strip()


def _split_css_components(value: str) -> list[str]:
    tokens = tinycss2.parse_component_value_list(value, skip_comments=True)
    return [_serialize_value([tok]) for tok in tokens if tok.type != "whitespace"]


def _classify_border_token(token: str) -> str:
    if token in BORDER_STYLE_KEYWORDS:
        return "style"
    if token in BORDER_WIDTH_KEYWORDS or _LEADING_DIGIT_RE.match(token):
        return "width"
    return "color"


def _expand_border(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in _split_css_components(value):
        kind = _classify_border_token(token)
        if kind == "width":
            result["border-width"] = token
        elif kind == "style":
            result["border-style"] = token
        else:
            result["border-color"] = token
    return result


def expand_shorthand(prop: str, value: str) -> dict[str, str]:
    """Expand a shorthand property to its longhand equivalents."""
    if prop == "border":
        result: dict[str, str] = {}
        for sub_prop, sub_val in _expand_border(value).items():
            result.update(expand_shorthand(sub_prop, sub_val))
        return result

    longhands = SHORTHAND_SIDES.get(prop)
    if not longhands:
        return {prop: value}
    parts = _split_css_components(value)
    n = len(parts)
    if n == 1:
        expanded = [parts[0]] * 4
    elif n == 2:
        expanded = [parts[0], parts[1], parts[0], parts[1]]
    elif n == 3:
        expanded = [parts[0], parts[1], parts[2], parts[1]]
    else:
        expanded = parts[:4]
    return dict(zip(longhands, expanded))


def transition_longhands(prop: str) -> list[str]:
    if prop == "border":
        return [*SHORTHAND_SIDES["border-width"], *SHORTHAND_SIDES["border-color"]]
    longhands = SHORTHAND_SIDES.get(prop)
    if longhands:
        return list(longhands)
    return [prop]


def static_declarations(prop: str, value: str, animated_props: set[str]) -> dict[str, str]:
    """Keep Qt-owned components, expanding a shorthand only when partially stripped."""
    expanded = expand_shorthand(prop, value)
    remaining = {
        name: val
        for name, val in expanded.items()
        if not (is_animatable(name) and ("all" in animated_props or name in animated_props))
    }
    if len(remaining) == len(expanded):
        return {prop: value}
    return remaining
