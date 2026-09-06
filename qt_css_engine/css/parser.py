"""Style sheet parser — extract_rules orchestration.

Thin orchestrator that delegates shorthand/selector helpers to submodules.
Keeps the two-pass algorithm from the original css_parser.py.
"""

import re
from typing import Any

import tinycss2
from tinycss2.ast import Node

from qt_css_engine.css.gradients import translate_gradients
from qt_css_engine.css.model import StyleRule, TransitionSpec
from qt_css_engine.css.selector import split_selector
from qt_css_engine.css.shorthand import expand_shorthand, should_strip_prop, transition_longhands

_GRADIENT_VALUE_RE = re.compile(
    r"\b(?:q(?:linear|radial|conical)gradient|(?:linear|radial|conic)-gradient)\s*\(",
    re.IGNORECASE,
)


def _normalize_prop(name: str) -> str:
    from qt_css_engine.constants import PROP_ALIASES

    return PROP_ALIASES.get(name, name)


def _is_static_gradient_prop(prop: str, value: str) -> bool:
    return prop == "background-color" and _GRADIENT_VALUE_RE.search(value) is not None


def _serialize_value(tokens: list[Node]) -> str:
    return tinycss2.serialize(tokens).strip()


def _split_by_comma(tokens: list[Any]) -> list[list[Any]]:
    parts: list[list[Any]] = []
    current: list[Any] = []
    for tok in tokens:
        if tok.type == "literal" and tok.value == ",":
            parts.append(current)
            current = []
        else:
            current.append(tok)
    parts.append(current)
    return parts


def _parse_transition_property_list(tokens: list[Any]) -> list[str]:
    result: list[str] = []
    for segment in _split_by_comma(tokens):
        significant = [t for t in segment if t.type != "whitespace"]
        if not significant or significant[0].type != "ident":
            continue
        val = significant[0].value.lower()
        if val == "none":
            return []
        result.append(significant[0].value)
    return result


def _parse_time_list(tokens: list[Any]) -> list[int]:
    result: list[int] = []
    for segment in _split_by_comma(tokens):
        significant = [t for t in segment if t.type != "whitespace"]
        if not significant:
            continue
        tok = significant[0]
        if tok.type == "dimension" and tok.unit in ("ms", "s"):
            result.append(int(tok.value * (1000 if tok.unit == "s" else 1)))
        elif tok.type == "number" and tok.value == 0:
            result.append(0)
    return result


def _parse_easing_list(tokens: list[Any]) -> list[str]:
    result: list[str] = []
    for segment in _split_by_comma(tokens):
        significant = [t for t in segment if t.type != "whitespace"]
        if not significant:
            continue
        tok = significant[0]
        if tok.type == "ident":
            result.append(tok.value)
        elif tok.type == "function" and tok.name.lower() in ("cubic-bezier", "steps"):
            result.append(_serialize_value([tok]))
    return result


def _combine_transition_longhands(
    props: list[str] | None,
    durations: list[int] | None,
    easings: list[str] | None,
    delays: list[int] | None,
) -> list[TransitionSpec]:
    if not props or not durations:
        return []

    def _get(lst: list[Any] | None, i: int, default: Any) -> Any:
        if not lst:
            return default
        return lst[i % len(lst)]

    result: list[TransitionSpec] = []
    for i, prop in enumerate(props):
        dur: int = _get(durations, i, 0)
        easing: str = _get(easings, i, "ease")
        delay: int = _get(delays, i, 0)
        norm_prop = _normalize_prop(prop)
        for lh in transition_longhands(norm_prop):
            result.append(TransitionSpec(lh, dur, easing, delay))
    return result


def _parse_transition_segment(tokens: list[Any]) -> tuple[str, int, str, int] | None:
    significant = [t for t in tokens if t.type != "whitespace"]
    if len(significant) < 2:
        return None
    prop_tok = significant[0]
    dur_tok = significant[1]
    if prop_tok.type != "ident":
        return None
    if dur_tok.type != "dimension" or dur_tok.unit not in ("ms", "s"):
        return None
    duration_ms = int(dur_tok.value * (1000 if dur_tok.unit == "s" else 1))
    easing = "ease"
    delay_ms = 0
    for tok in significant[2:]:
        if tok.type == "dimension" and tok.unit in ("ms", "s"):
            delay_ms = int(tok.value * (1000 if tok.unit == "s" else 1))
        elif tok.type == "ident" and tok.value not in ("normal", "allow-discrete"):
            easing = tok.value
        elif tok.type == "function" and tok.name.lower() in ("cubic-bezier", "steps"):
            easing = _serialize_value([tok])
    return prop_tok.value, duration_ms, easing, delay_ms


def extract_rules(stylesheet: str) -> tuple[str, list[StyleRule]]:
    """Parse a stylesheet into cleaned QSS and a StyleRule list."""
    raw_rules = tinycss2.parse_stylesheet(stylesheet, skip_comments=True, skip_whitespace=True)
    rules: list[StyleRule] = []

    for raw_rule in raw_rules:
        if raw_rule.type != "qualified-rule":
            continue
        raw_selector: str = tinycss2.serialize(raw_rule.prelude).strip()
        decls = tinycss2.parse_blocks_contents(raw_rule.content, skip_comments=True, skip_whitespace=True)

        transitions: list[TransitionSpec] = []
        props: dict[str, str] = {}
        _t_props: list[str] | None = None
        _t_durations: list[int] | None = None
        _t_easings: list[str] | None = None
        _t_delays: list[int] | None = None

        for decl in decls:
            if decl.type != "declaration":
                continue
            name = decl.name.lower()
            if name == "transition":
                for segment in _split_by_comma(decl.value):
                    result = _parse_transition_segment(segment)
                    if result:
                        prop, duration_ms, easing, delay_ms = result
                        norm_prop = _normalize_prop(prop)
                        for lh in transition_longhands(norm_prop):
                            transitions.append(TransitionSpec(lh, duration_ms, easing, delay_ms))
            elif name == "transition-property":
                _t_props = _parse_transition_property_list(decl.value)
            elif name == "transition-duration":
                _t_durations = _parse_time_list(decl.value)
            elif name == "transition-timing-function":
                _t_easings = _parse_easing_list(decl.value)
            elif name == "transition-delay":
                _t_delays = _parse_time_list(decl.value)
            else:
                norm = _normalize_prop(name)
                props.update(expand_shorthand(norm, translate_gradients(_serialize_value(decl.value))))

        if _t_props is not None or _t_durations is not None or _t_easings is not None or _t_delays is not None:
            transitions = _combine_transition_longhands(_t_props, _t_durations, _t_easings, _t_delays)

        for selector in (s.strip() for s in raw_selector.split(",")):
            base, pseudo_set = split_selector(selector)
            is_subcontrol = "::" in base
            rules.append(
                StyleRule(
                    selector=selector,
                    base_selector=base,
                    properties=props,
                    pseudo_set=pseudo_set,
                    transitions=[] if is_subcontrol else transitions,
                    segments=base.split(),
                    subcontrol=is_subcontrol,
                    has_attrs="[" in base,
                )
            )

    animated_map: dict[str, set[str]] = {}
    for rule in rules:
        if rule.transitions:
            if rule.base_selector not in animated_map:
                animated_map[rule.base_selector] = set()
            for t in rule.transitions:
                animated_map[rule.base_selector].add(t.prop)

    cleaned_parts: list[str] = []
    for raw_rule in raw_rules:
        if raw_rule.type != "qualified-rule":
            continue
        selector: str = tinycss2.serialize(raw_rule.prelude).strip()
        decls = tinycss2.parse_declaration_list(raw_rule.content, skip_comments=True, skip_whitespace=True)
        _, pseudo_set = split_selector(selector.split(",")[0].strip())
        # Attr-conditional blocks ([active=true]) are engine-managed like pseudo
        # blocks: animated props are stripped so Qt doesn't fight the transition.
        has_attr_block = "[" in selector
        animated_props: set[str] = set()
        for sel_part in (s.strip() for s in selector.split(",")):
            base_part, _ = split_selector(sel_part)
            animated_props |= animated_map.get(base_part, set())
            if "[" in base_part:
                # Attr-conditional base (`.item[active=true]`) inherits the
                # transitions declared on its attr-stripped base (`.item`),
                # mirroring how `:hover` reuses the base rule's transitions.
                stripped = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", base_part)).strip()
                animated_props |= animated_map.get(stripped, set())
        new_body_lines: list[str] = []
        for decl in decls:
            if decl.type != "declaration":
                continue
            name = decl.name.lower()
            if name == "transition" or name.startswith("transition-"):
                continue
            p_name = _normalize_prop(name)
            p_val = translate_gradients(_serialize_value(decl.value))
            if p_name in ("box-shadow", "cursor"):
                continue
            if (
                (pseudo_set or has_attr_block)
                and not _is_static_gradient_prop(p_name, p_val)
                and ("all" in animated_props or should_strip_prop(p_name, animated_props))
            ):
                continue
            new_body_lines.append(f"    {p_name}: {p_val};")
        if new_body_lines:
            cleaned_parts.append(f"{selector} {{\n" + "\n".join(new_body_lines) + "\n}")
        else:
            cleaned_parts.append(f"{selector} {{ }}")
    return "\n\n".join(cleaned_parts), rules
