import re
from dataclasses import dataclass, field
from typing import Any

import tinycss2
from tinycss2.ast import Node

from .constants import (
    ANIMATION_PSEUDOS,
    BORDER_STYLE_KEYWORDS,
    BORDER_WIDTH_KEYWORDS,
    PROP_ALIASES,
    PSEUDO_ALIASES,
    SHORTHAND_SIDES,
)
from .gradients import translate_gradients


def _normalize_prop(name: str) -> str:
    """Map an aliased property name to its canonical form."""
    return PROP_ALIASES.get(name, name)


def _classify_border_token(token: str) -> str:
    """Classify a token from a `border:` value as 'width', 'style', or 'color'."""
    if token in BORDER_STYLE_KEYWORDS:
        return "style"
    if token in BORDER_WIDTH_KEYWORDS or re.match(r"^\d", token):
        return "width"
    return "color"


def _expand_border(value: str) -> dict[str, str]:
    """Parse `border: <width> <style> <color>` (any token order) into component properties."""
    result: dict[str, str] = {}
    for token in value.split():
        kind = _classify_border_token(token)
        if kind == "width":
            result["border-width"] = token
        elif kind == "style":
            result["border-style"] = token
        else:
            result["border-color"] = token
    return result


def _expand_shorthand(prop: str, value: str) -> dict[str, str]:
    """Expand a shorthand property to its longhand equivalents (recursively for compound shorthands)."""
    if prop == "border":
        result: dict[str, str] = {}
        for sub_prop, sub_val in _expand_border(value).items():
            result.update(_expand_shorthand(sub_prop, sub_val))
        return result

    longhands = SHORTHAND_SIDES.get(prop)
    if not longhands:
        return {prop: value}
    parts = value.split()
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


def _transition_longhands(prop: str) -> list[str]:
    """Animatable longhands to register when `transition: <prop>` is declared."""
    if prop == "border":
        # border-style is not animatable
        return [*SHORTHAND_SIDES["border-width"], *SHORTHAND_SIDES["border-color"]]
    longhands = SHORTHAND_SIDES.get(prop)
    if longhands:
        return list(longhands)
    return [prop]


def _should_strip_prop(prop: str, animated_props: set[str]) -> bool:
    """True if this property (or any of its longhands) is being animated."""
    if prop in animated_props:
        return True
    if prop == "border":
        border_longhands = [
            *SHORTHAND_SIDES["border-width"],
            "border-style",
            "border-color",
            *SHORTHAND_SIDES["border-color"],
        ]
        return any(lh in animated_props for lh in border_longhands)
    longhands: list[str] | None = SHORTHAND_SIDES.get(prop)
    return bool(longhands and any(lh in animated_props for lh in longhands))


def _split_selector(selector: str) -> tuple[str, frozenset[str]]:
    """
    Return ``(base_selector, pseudo_set)`` for a single selector string.

    ``base_selector`` has all trailing pseudo-classes stripped (used for widget matching).
    ``pseudo_set`` contains every recognized animation pseudo-class in the trailing
    position after aliasing any alternate names (see ``PSEUDO_ALIASES``).
    Compound selectors like ``:checked:pressed`` yield a set with both members.

    ``::subcontrol`` pseudo-elements (e.g. ``::item``, ``::handle``) are treated
    as part of the base selector and are never mistaken for pseudo-classes.  The
    negative lookbehind ``(?<!:)`` ensures only single-colon pseudo-classes match.
    """
    m = re.search(r"((?:(?<!:):[a-z-]+)+)$", selector)
    if not m:
        return selector, frozenset()
    base = selector[: m.start()]
    found = [PSEUDO_ALIASES.get(p, p) for p in re.findall(r":[a-z-]+", m.group(1))]
    return base, frozenset(p for p in found if p in ANIMATION_PSEUDOS)


def _serialize_value(tokens: list[Node]) -> str:
    """Serialize a tinycss2 token list to a CSS value string."""
    return tinycss2.serialize(tokens).strip()


def _split_by_comma(tokens: list[Any]) -> list[list[Any]]:
    """Split a tinycss2 token list by comma literals into segments."""
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
    """Parse `transition-property` value → list of property names."""
    result: list[str] = []
    for segment in _split_by_comma(tokens):
        significant = [t for t in segment if t.type != "whitespace"]
        if not significant or significant[0].type != "ident":
            continue
        val = significant[0].value.lower()
        if val == "none":
            return []  # transition-property: none → no transitions
        result.append(significant[0].value)
    return result


def _parse_time_list(tokens: list[Any]) -> list[int]:
    """Parse comma-separated `<time>` list → milliseconds integers."""
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
    """Parse comma-separated timing-function list → easing strings."""
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
    """Build TransitionSpec list from longhand lists using CSS positional/cycling rules."""
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
        for lh in _transition_longhands(norm_prop):
            result.append(TransitionSpec(lh, dur, easing, delay))
    return result


def _parse_transition_segment(tokens: list[Any]) -> tuple[str, int, str, int] | None:
    """
    Parse one transition segment's tokens into (property, duration_ms, easing).

    Expected token order (ignoring whitespace): <ident> <dimension> [<ident>]
    Returns None if the segment is missing the required property or duration.
    """
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

    # Parse optional timing-function and/or delay from remaining tokens.
    # CSS spec `<single-transition>` syntax: each of <easing-function> and <time> (delay) is
    # optional and may appear in any order after the duration.  Scan all remaining tokens and
    # classify each independently so we handle both `duration easing delay` and
    # `duration delay easing` without dropping the easing when delay precedes it.
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


@dataclass
class TransitionSpec:
    """Parsed CSS transition declaration for one property."""

    prop: str
    duration_ms: int
    easing: str = "ease"
    delay_ms: int = 0


@dataclass
class StyleRule:
    """One parsed CSS rule block with selector metadata and transition specs."""

    selector: str
    base_selector: str
    properties: dict[str, str]
    pseudo_set: frozenset[str] = field(default_factory=frozenset)  # All pseudos in compound selector
    transitions: list[TransitionSpec] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    subcontrol: bool = False  # True when selector targets a ::subcontrol (::item, ::handle, …)


def extract_rules(stylesheet: str) -> tuple[str, list[StyleRule]]:
    """Parse a stylesheet into cleaned QSS (transitions stripped) and a StyleRule list."""
    raw_rules = tinycss2.parse_stylesheet(stylesheet, skip_comments=True, skip_whitespace=True)

    rules: list[StyleRule] = []

    # First pass: Parse everything into StyleRule objects
    for raw_rule in raw_rules:
        if raw_rule.type != "qualified-rule":
            continue

        raw_selector: str = tinycss2.serialize(raw_rule.prelude).strip()
        decls = tinycss2.parse_blocks_contents(raw_rule.content, skip_comments=True, skip_whitespace=True)

        transitions: list[TransitionSpec] = []
        props: dict[str, str] = {}

        # Accumulated longhand transition state (None = not declared in this block).
        # If any longhand is present after parsing, they override the shorthand channel.
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
                        for lh in _transition_longhands(norm_prop):
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
                props.update(_expand_shorthand(norm, translate_gradients(_serialize_value(decl.value))))

        # If any longhand was declared, build transitions from them (overrides shorthand).
        if _t_props is not None or _t_durations is not None or _t_easings is not None or _t_delays is not None:
            transitions = _combine_transition_longhands(_t_props, _t_durations, _t_easings, _t_delays)

        for selector in (s.strip() for s in raw_selector.split(",")):
            base, pseudo_set = _split_selector(selector)
            is_subcontrol = "::" in base

            rules.append(
                StyleRule(
                    selector=selector,
                    base_selector=base,
                    properties=props,
                    pseudo_set=pseudo_set,
                    # Subcontrol items (::item, ::handle, …) are not real widgets; the engine
                    # cannot intercept their hover events, so we never animate them.  Zeroing
                    # transitions here also prevents the second pass from stripping their
                    # pseudo-state properties, so Qt renders the native styles correctly.
                    transitions=[] if is_subcontrol else transitions,
                    segments=base.split(),
                    subcontrol=is_subcontrol,
                )
            )

    # Identify which properties are animated for each base selector
    animated_map: dict[str, set[str]] = {}  # base_selector -> set(props)
    for rule in rules:
        if rule.transitions:
            if rule.base_selector not in animated_map:
                animated_map[rule.base_selector] = set()
            for t in rule.transitions:
                animated_map[rule.base_selector].add(t.prop)

    # Second pass: Rebuild the stylesheet stripping transitions and animated pseudo-props
    cleaned_parts: list[str] = []
    for raw_rule in raw_rules:
        if raw_rule.type != "qualified-rule":
            continue

        selector: str = tinycss2.serialize(raw_rule.prelude).strip()
        decls = tinycss2.parse_declaration_list(raw_rule.content, skip_comments=True, skip_whitespace=True)

        # For comma-grouped selectors, derive pseudo_set from the first part and union animated props.
        _, pseudo_set = _split_selector(selector.split(",")[0].strip())

        animated_props: set[str] = set()
        for sel_part in (s.strip() for s in selector.split(",")):
            base_part, _ = _split_selector(sel_part)
            animated_props |= animated_map.get(base_part, set())

        new_body_lines: list[str] = []
        for decl in decls:
            if decl.type != "declaration":
                continue
            name = decl.name.lower()
            if name == "transition" or name.startswith("transition-"):
                continue  # Strip transition declarations and longhands

            p_name = _normalize_prop(name)
            p_val = translate_gradients(_serialize_value(decl.value))

            # Properties the engine handles out-of-band — Qt doesn't know them and would warn.
            if p_name in ("box-shadow", "cursor"):
                continue

            # Strip if pseudo-state block AND (transition: all covers everything, or prop is animated)
            if pseudo_set and ("all" in animated_props or _should_strip_prop(p_name, animated_props)):
                continue  # Strip!

            new_body_lines.append(f"    {p_name}: {p_val};")

        if new_body_lines:
            cleaned_parts.append(f"{selector} {{\n" + "\n".join(new_body_lines) + "\n}")
        else:
            # If the block is now empty (e.g. only had an animated property), keep the selector but empty body
            # Or we could omit it entirely if it's not needed for other things.
            # Keeping it empty is safer for specificity/structure.
            cleaned_parts.append(f"{selector} {{ }}")

    return "\n\n".join(cleaned_parts), rules
