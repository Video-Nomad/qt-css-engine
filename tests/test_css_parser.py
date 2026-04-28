# pyright: reportPrivateUsage=false
# pyright: reportUnknownMemberType=false

import pytest

from qt_css_engine.css_parser import StyleRule, extract_rules

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_rule(rules: list[StyleRule], selector: str) -> StyleRule:
    for r in rules:
        if r.selector == selector:
            return r
    raise KeyError(f"No rule with selector {selector!r}")


def cleaned_block(cleaned_qss: str, selector: str) -> str:
    """Return the body text of a selector block in the cleaned QSS."""
    start = cleaned_qss.find(selector)
    assert start != -1, f"{selector!r} not found in cleaned QSS"
    open_brace = cleaned_qss.index("{", start)
    close_brace = cleaned_qss.index("}", open_brace)
    return cleaned_qss[open_brace + 1 : close_brace].strip()


# ---------------------------------------------------------------------------
# Transition extraction
# ---------------------------------------------------------------------------


def test_transition_extracted_from_base_rule() -> None:
    css = ".btn { background-color: steelblue; transition: background-color 300ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert len(rule.transitions) == 1
    t = rule.transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert t.easing == "ease"


def test_transition_extracted_from_pseudo_rule() -> None:
    css = ".btn:hover { background-color: royalblue; transition: background-color 400ms ease-in; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:hover")
    assert rule.base_selector == ".btn"
    assert len(rule.transitions) == 1
    t = rule.transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 400
    assert t.easing == "ease-in"


def test_transition_duration_in_seconds() -> None:
    css = ".box { transition: opacity 0.5s linear; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.transitions[0].duration_ms == 500


def test_multiple_transition_lines_in_same_block() -> None:
    css = """
    .btn:pressed {
        color: white;
        transition: color 400ms;
        transition: background-color 500ms;
        transition: border-color 600ms ease;
    }
    """
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:pressed")
    props = {t.prop: t for t in rule.transitions}
    assert set(props) == {
        "color",
        "background-color",
        "border-top-color",
        "border-right-color",
        "border-bottom-color",
        "border-left-color",
    }
    assert props["color"].duration_ms == 400
    assert props["background-color"].duration_ms == 500
    assert props["border-top-color"].duration_ms == 600
    assert props["border-top-color"].easing == "ease"


def test_easing_defaults_to_ease() -> None:
    css = ".box { transition: opacity 200ms; }"
    _, rules = extract_rules(css)
    assert rules[0].transitions[0].easing == "ease"


def test_delay_defaults_to_zero() -> None:
    css = ".box { transition: opacity 200ms ease; }"
    _, rules = extract_rules(css)
    assert rules[0].transitions[0].delay_ms == 0


def test_delay_parsed_after_easing() -> None:
    css = ".box { transition: background-color 300ms ease 100ms; }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert t.easing == "ease"
    assert t.delay_ms == 100


def test_delay_without_easing() -> None:
    """transition: prop duration delay (no timing-function)"""
    css = ".box { transition: width 400ms 200ms; }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.duration_ms == 400
    assert t.easing == "ease"
    assert t.delay_ms == 200


def test_delay_in_seconds() -> None:
    css = ".box { transition: width 0.3s ease 0.1s; }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.duration_ms == 300
    assert t.delay_ms == 100


def test_delay_before_easing() -> None:
    """CSS spec allows delay before timing-function: `duration delay easing`."""
    css = ".box { transition: all 300ms 300ms cubic-bezier(.53, -0.98, .52, 1.66); }"
    _, rules = extract_rules(css)
    # Find a non-'all' prop (all expands to longhands for animated props, but for a rule with
    # no other properties just check the first transition spec)
    t = rules[0].transitions[0]
    assert t.duration_ms == 300
    assert t.delay_ms == 300
    assert t.easing.startswith("cubic-bezier")


# ---------------------------------------------------------------------------
# Comma-separated transition shorthand
# ---------------------------------------------------------------------------


def test_comma_shorthand_extracts_all_parts() -> None:
    css = ".btn { transition: background-color 300ms ease, color 200ms linear, border-color 150ms; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    props = {t.prop: t for t in rule.transitions}
    assert set(props) == {
        "background-color",
        "color",
        "border-top-color",
        "border-right-color",
        "border-bottom-color",
        "border-left-color",
    }
    assert props["background-color"].duration_ms == 300
    assert props["background-color"].easing == "ease"
    assert props["color"].duration_ms == 200
    assert props["color"].easing == "linear"
    assert props["border-top-color"].duration_ms == 150
    assert props["border-top-color"].easing == "ease"  # default


def test_comma_shorthand_single_entry_still_works() -> None:
    css = ".box { transition: opacity 500ms ease-in-out; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert len(rule.transitions) == 1
    assert rule.transitions[0].prop == "opacity"


def test_comma_shorthand_all_props_stripped_from_pseudo_block() -> None:
    css = """
    .btn { background-color: steelblue; color: white; border-color: gray; }
    .btn:hover { background-color: royalblue; color: black; border-color: blue;
                 transition: background-color 300ms, color 200ms, border-color 150ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".btn:hover")
    assert "background-color" not in hover_body
    assert "color" not in hover_body
    assert "border-color" not in hover_body
    assert "transition" not in hover_body


def test_comma_shorthand_transition_removed_from_cleaned_qss() -> None:
    css = ".btn { transition: background-color 300ms, color 200ms; }"
    cleaned, _ = extract_rules(css)
    assert "transition" not in cleaned


def test_comma_shorthand_multiline() -> None:
    css = """.btn {
        transition:
            background-color 300ms ease,
            color 200ms linear;
    }"""
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    props = {t.prop: t for t in rule.transitions}
    assert set(props) == {"background-color", "color"}
    assert props["background-color"].duration_ms == 300
    assert props["color"].duration_ms == 200


# ---------------------------------------------------------------------------
# Cleaned QSS
# ---------------------------------------------------------------------------


def test_transition_stripped_from_cleaned_qss() -> None:
    css = ".btn { background-color: steelblue; transition: background-color 300ms; }"
    cleaned, _ = extract_rules(css)
    assert "transition" not in cleaned


def test_animated_props_stripped_from_pseudo_block() -> None:
    css = """
    .btn { background-color: steelblue; }
    .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".btn:hover")
    assert "background-color" not in hover_body


def test_base_props_NOT_stripped_from_base_block() -> None:
    css = """
    .btn { background-color: steelblue; }
    .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    base_body = cleaned_block(cleaned, ".btn {")
    assert "background-color" in base_body


def test_non_animated_pseudo_props_preserved() -> None:
    """Props in :hover that are NOT animated should survive the strip pass."""
    css = """
    .btn { background-color: steelblue; border-radius: 4px; }
    .btn:hover { background-color: royalblue; border-radius: 8px; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".btn:hover")
    # background-color is animated → stripped
    assert "background-color" not in hover_body
    # border-radius is NOT animated → kept
    assert "border-radius" in hover_body


def test_empty_pseudo_block_preserved_for_wa_hover() -> None:
    """An all-animated :hover block must stay as an empty rule (not removed) so Qt sets WA_Hover."""
    css = """
    .btn { background-color: steelblue; }
    .btn:hover { background-color: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    assert ".btn:hover" in cleaned


# ---------------------------------------------------------------------------
# StyleRule fields
# ---------------------------------------------------------------------------


def test_base_selector_and_pseudo_split() -> None:
    css = "#box:hover { color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#box:hover")
    assert rule.base_selector == "#box"
    assert ":hover" in rule.pseudo_set


def test_segments_for_nested_selector() -> None:
    css = ".sidebar .action { color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".sidebar .action")
    assert rule.segments == [".sidebar", ".action"]


def test_properties_exclude_transition() -> None:
    css = ".btn { color: white; transition: color 200ms; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert "color" in rule.properties
    assert "transition" not in rule.properties


# ---------------------------------------------------------------------------
# Property alias: background → background-color
# ---------------------------------------------------------------------------


def test_background_prop_normalized_to_background_color() -> None:
    css = ".btn { background: steelblue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert "background-color" in rule.properties
    assert "background" not in rule.properties


def test_background_transition_normalized_to_background_color() -> None:
    css = ".btn { transition: background 300ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert rule.transitions[0].prop == "background-color"


def test_background_stripped_from_pseudo_block_via_alias() -> None:
    """background: in :hover should be stripped when transition: background-color is declared."""
    css = """
    .btn { background: steelblue; }
    .btn:hover { background: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".btn:hover")
    assert "background" not in hover_body


# ---------------------------------------------------------------------------
# Property alias: text-shadow → box-shadow
# ---------------------------------------------------------------------------


def test_text_shadow_prop_normalized_to_box_shadow() -> None:
    css = ".btn { text-shadow: 2px 2px 4px black; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert "box-shadow" in rule.properties
    assert "text-shadow" not in rule.properties


def test_text_shadow_transition_normalized_to_box_shadow() -> None:
    css = ".btn { transition: text-shadow 300ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert rule.transitions[0].prop == "box-shadow"


# ---------------------------------------------------------------------------
# Shorthand property expansion
# ---------------------------------------------------------------------------


def test_padding_single_value_expands_to_four_sides() -> None:
    css = ".box { padding: 10px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "padding-top": "10px",
        "padding-right": "10px",
        "padding-bottom": "10px",
        "padding-left": "10px",
    }


def test_padding_two_values_expands_correctly() -> None:
    css = ".box { padding: 10px 20px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "padding-top": "10px",
        "padding-right": "20px",
        "padding-bottom": "10px",
        "padding-left": "20px",
    }


def test_padding_three_values_expands_correctly() -> None:
    css = ".box { padding: 10px 20px 30px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "padding-top": "10px",
        "padding-right": "20px",
        "padding-bottom": "30px",
        "padding-left": "20px",
    }


def test_padding_four_values_expands_correctly() -> None:
    css = ".box { padding: 10px 20px 30px 40px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "padding-top": "10px",
        "padding-right": "20px",
        "padding-bottom": "30px",
        "padding-left": "40px",
    }


def test_margin_two_values_expands_correctly() -> None:
    css = ".box { margin: 5px 10px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "margin-top": "5px",
        "margin-right": "10px",
        "margin-bottom": "5px",
        "margin-left": "10px",
    }


def test_border_radius_two_values_expands_correctly() -> None:
    css = ".box { border-radius: 4px 8px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "border-top-left-radius": "4px",
        "border-top-right-radius": "8px",
        "border-bottom-right-radius": "4px",
        "border-bottom-left-radius": "8px",
    }


def test_border_width_single_value_expands_to_four_sides() -> None:
    css = ".box { border-width: 2px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {
        "border-top-width": "2px",
        "border-right-width": "2px",
        "border-bottom-width": "2px",
        "border-left-width": "2px",
    }


def test_transition_padding_shorthand_expands_to_longhands() -> None:
    css = ".box { transition: padding 200ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    trans = {t.prop: t for t in rule.transitions}
    assert set(trans) == {"padding-top", "padding-right", "padding-bottom", "padding-left"}
    for t in trans.values():
        assert t.duration_ms == 200
        assert t.easing == "ease"


def test_shorthand_in_pseudo_block_stripped_when_longhands_animated() -> None:
    """padding: shorthand in :hover should be stripped when transition animates padding longhands."""
    css = """
    .box { padding: 10px 20px; }
    .box:hover { padding: 5px 15px; transition: padding 200ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".box:hover")
    assert "padding" not in hover_body


def test_shorthand_in_base_block_preserved() -> None:
    """padding: in the base block must NOT be stripped — only pseudo blocks are cleaned."""
    css = """
    .box { padding: 10px 20px; }
    .box:hover { padding: 5px 15px; transition: padding 200ms; }
    """
    cleaned, _ = extract_rules(css)
    base_body = cleaned_block(cleaned, ".box {")
    assert "padding" in base_body


# ---------------------------------------------------------------------------
# border: compound shorthand expansion
# ---------------------------------------------------------------------------


def test_border_shorthand_expands_to_all_components() -> None:
    css = ".box { border: 3px solid white; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties["border-top-width"] == "3px"
    assert rule.properties["border-right-width"] == "3px"
    assert rule.properties["border-bottom-width"] == "3px"
    assert rule.properties["border-left-width"] == "3px"
    assert rule.properties["border-style"] == "solid"
    assert rule.properties["border-top-color"] == "white"
    assert rule.properties["border-right-color"] == "white"
    assert rule.properties["border-bottom-color"] == "white"
    assert rule.properties["border-left-color"] == "white"


def test_border_shorthand_width_only() -> None:
    css = ".box { border: 2px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties["border-top-width"] == "2px"
    assert "border-style" not in rule.properties
    assert "border-color" not in rule.properties


def test_border_shorthand_style_color_no_width() -> None:
    css = ".box { border: solid red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties["border-style"] == "solid"
    assert rule.properties["border-top-color"] == "red"
    assert rule.properties["border-right-color"] == "red"
    assert rule.properties["border-bottom-color"] == "red"
    assert rule.properties["border-left-color"] == "red"
    assert "border-top-width" not in rule.properties


def test_border_shorthand_keyword_width() -> None:
    css = ".box { border: thin solid blue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties["border-top-width"] == "thin"
    assert rule.properties["border-style"] == "solid"
    assert rule.properties["border-top-color"] == "blue"
    assert rule.properties["border-bottom-color"] == "blue"


def test_transition_border_expands_to_animatable_longhands() -> None:
    """transition: border should cover width sides + border-color, but NOT border-style."""
    css = ".box { transition: border 200ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    trans = {t.prop: t for t in rule.transitions}
    assert "border-top-width" in trans
    assert "border-right-width" in trans
    assert "border-bottom-width" in trans
    assert "border-left-width" in trans
    assert "border-top-color" in trans
    assert "border-right-color" in trans
    assert "border-bottom-color" in trans
    assert "border-left-color" in trans
    assert "border-style" not in trans
    for t in trans.values():
        assert t.duration_ms == 200
        assert t.easing == "ease"


def test_border_shorthand_stripped_from_pseudo_block_when_animated() -> None:
    css = """
    .box { border: 1px solid gray; }
    .box:hover { border: 3px solid white; transition: border 200ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".box:hover")
    assert "border" not in hover_body


def test_border_shorthand_in_base_block_preserved() -> None:
    css = """
    .box { border: 1px solid gray; }
    .box:hover { border: 3px solid white; transition: border 200ms; }
    """
    cleaned, _ = extract_rules(css)
    base_body = cleaned_block(cleaned, ".box {")
    assert "border" in base_body


# ---------------------------------------------------------------------------
# height / width → min-/max- expansion
# ---------------------------------------------------------------------------


def test_height_kept_as_is_in_rule_properties() -> None:
    css = ".box { height: 100px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {"height": "100px"}


def test_width_kept_as_is_in_rule_properties() -> None:
    css = ".box { width: 50px; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert rule.properties == {"width": "50px"}


def test_height_in_cleaned_qss_kept_as_is() -> None:
    css = ".box { height: 100px; }"
    cleaned, _ = extract_rules(css)
    body = cleaned_block(cleaned, ".box {")
    assert "height: 100px" in body


def test_width_in_cleaned_qss_kept_as_is() -> None:
    css = ".box { width: 200px; }"
    cleaned, _ = extract_rules(css)
    body = cleaned_block(cleaned, ".box {")
    assert "width: 200px" in body


def test_transition_height_registers_as_height() -> None:
    css = ".box { transition: height 300ms ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    trans = {t.prop: t for t in rule.transitions}
    assert set(trans) == {"height"}
    assert trans["height"].duration_ms == 300
    assert trans["height"].easing == "ease"


def test_transition_width_registers_as_width() -> None:
    css = ".box { transition: width 200ms linear; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    trans = {t.prop: t for t in rule.transitions}
    assert set(trans) == {"width"}
    assert trans["width"].duration_ms == 200
    assert trans["width"].easing == "linear"


def test_height_in_pseudo_block_stripped_when_animated() -> None:
    css = """
    .box { height: 40px; }
    .box:hover { height: 80px; transition: height 300ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".box:hover")
    # height: in pseudo block must be stripped (its longhands min-height/max-height are animated)
    assert "height" not in hover_body


def test_width_in_pseudo_block_stripped_when_animated() -> None:
    css = """
    .box { width: 100px; }
    .box:hover { width: 200px; transition: width 300ms; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".box:hover")
    assert "width" not in hover_body


def test_height_in_base_block_kept_in_cleaned_qss() -> None:
    css = """
    .box { height: 40px; }
    .box:hover { height: 80px; transition: height 300ms; }
    """
    cleaned, _ = extract_rules(css)
    base_body = cleaned_block(cleaned, ".box {")
    assert "height: 40px" in base_body


# ---------------------------------------------------------------------------
# Comma-separated selectors
# ---------------------------------------------------------------------------


def test_comma_selector_produces_separate_rules() -> None:
    css = "#btn1, #btn2 { background-color: steelblue; }"
    _, rules = extract_rules(css)
    selectors = [r.selector for r in rules]
    assert "#btn1" in selectors
    assert "#btn2" in selectors


def test_comma_selector_rules_have_same_properties() -> None:
    css = "#btn1, #btn2 { background-color: steelblue; color: white; }"
    _, rules = extract_rules(css)
    r1 = get_rule(rules, "#btn1")
    r2 = get_rule(rules, "#btn2")
    assert r1.properties == r2.properties
    assert r1.properties["background-color"] == "steelblue"


def test_comma_selector_rules_have_same_transitions() -> None:
    css = "#btn1, #btn2 { background-color: steelblue; transition: background-color 300ms ease; }"
    _, rules = extract_rules(css)
    r1 = get_rule(rules, "#btn1")
    r2 = get_rule(rules, "#btn2")
    assert len(r1.transitions) == 1
    assert len(r2.transitions) == 1
    assert r1.transitions[0].prop == "background-color"
    assert r2.transitions[0].prop == "background-color"
    assert r1.transitions[0].duration_ms == 300


def test_comma_selector_base_selector_and_pseudo_split() -> None:
    css = "#btn1:hover, #btn2:hover { background-color: royalblue; }"
    _, rules = extract_rules(css)
    r1 = get_rule(rules, "#btn1:hover")
    r2 = get_rule(rules, "#btn2:hover")
    assert r1.base_selector == "#btn1"
    assert r2.base_selector == "#btn2"


def test_comma_selector_segments_per_rule() -> None:
    css = "#btn1, #btn2 { color: red; }"
    _, rules = extract_rules(css)
    r1 = get_rule(rules, "#btn1")
    r2 = get_rule(rules, "#btn2")
    assert r1.segments == ["#btn1"]
    assert r2.segments == ["#btn2"]


def test_comma_selector_three_parts() -> None:
    css = ".a, .b, .c { color: red; }"
    _, rules = extract_rules(css)
    selectors = [r.selector for r in rules]
    assert ".a" in selectors
    assert ".b" in selectors
    assert ".c" in selectors


def test_comma_selector_animated_prop_stripped_from_pseudo_blocks() -> None:
    """Each selector's :hover block should have animated props stripped independently."""
    css = """
    #btn1, #btn2 { background-color: steelblue; }
    #btn1:hover, #btn2:hover { background-color: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    for sel in ("#btn1:hover", "#btn2:hover"):
        body = cleaned_block(cleaned, sel)
        assert "background-color" not in body


def test_comma_selector_cleaned_qss_contains_each_block() -> None:
    css = "#btn1, #btn2 { background-color: steelblue; }"
    cleaned, _ = extract_rules(css)
    # The original comma-grouped block should appear in the cleaned output
    assert "steelblue" in cleaned


# ---------------------------------------------------------------------------
# Multiple pseudo-classes on one selector  (#btn:hover:focus etc.)
# ---------------------------------------------------------------------------


def test_multi_pseudo_hover_focus_base_selector_stripped() -> None:
    """All trailing pseudos are stripped; base_selector has none of them."""
    css = "#btn:hover:focus { background-color: royalblue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:hover:focus")
    assert rule.base_selector == "#btn"


def test_multi_pseudo_hover_focus_pseudo_set_contains_both() -> None:
    css = "#btn:hover:focus { background-color: royalblue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:hover:focus")
    assert rule.pseudo_set == frozenset({":hover", ":focus"})


def test_multi_pseudo_focus_hover_pseudo_set_contains_both() -> None:
    css = "#btn:focus:hover { background-color: royalblue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:focus:hover")
    assert rule.pseudo_set == frozenset({":hover", ":focus"})


def test_multi_pseudo_pressed_focus_pseudo_set_contains_both() -> None:
    css = "#btn:pressed:focus { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:pressed:focus")
    assert rule.pseudo_set == frozenset({":pressed", ":focus"})


def test_focus_pseudo_is_recognised_as_animation_pseudo() -> None:
    """:focus is a first-class animation pseudo."""
    css = "#btn:focus { background-color: royalblue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:focus")
    assert ":focus" in rule.pseudo_set
    assert rule.base_selector == "#btn"


def test_multi_pseudo_segments_use_base_selector() -> None:
    css = "#btn:hover:focus { color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "#btn:hover:focus")
    assert rule.segments == ["#btn"]


def test_multi_pseudo_full_selector_preserved_in_cleaned_qss() -> None:
    """Qt must receive the full selector including non-animation pseudos."""
    css = "#btn:hover:focus { color: red; }"
    cleaned, _ = extract_rules(css)
    assert "#btn:hover:focus" in cleaned


def test_multi_pseudo_animated_prop_stripped_from_pseudo_block() -> None:
    css = """
    #btn { background-color: steelblue; }
    #btn:hover:focus { background-color: royalblue; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    body = cleaned_block(cleaned, "#btn:hover:focus")
    assert "background-color" not in body


def test_multi_pseudo_non_animated_prop_preserved_in_cleaned_qss() -> None:
    css = """
    #btn { background-color: steelblue; }
    #btn:hover:focus { background-color: royalblue; color: white; transition: background-color 300ms; }
    """
    cleaned, _ = extract_rules(css)
    body = cleaned_block(cleaned, "#btn:hover:focus")
    assert "background-color" not in body
    assert "color" in body


# ---------------------------------------------------------------------------
# Compound pseudo-classes (e.g. :checked:pressed)
# ---------------------------------------------------------------------------


def test_compound_pseudo_checked_pressed_pseudo_set() -> None:
    css = ".btn:checked:pressed { background-color: orange; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:checked:pressed")
    assert rule.pseudo_set == frozenset({":checked", ":pressed"})


def test_compound_pseudo_checked_pressed_pseudo_set_contains_both() -> None:
    css = ".btn:checked:pressed { background-color: orange; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:checked:pressed")
    assert rule.pseudo_set == frozenset({":checked", ":pressed"})


def test_compound_pseudo_base_selector_stripped() -> None:
    css = ".btn:checked:pressed { background-color: orange; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:checked:pressed")
    assert rule.base_selector == ".btn"


def test_compound_pseudo_animated_prop_stripped_from_cleaned_qss() -> None:
    css = """
    .btn { background-color: red; transition: background-color 200ms; }
    .btn:checked:pressed { background-color: orange; }
    """
    cleaned, _ = extract_rules(css)
    body = cleaned_block(cleaned, ".btn:checked:pressed")
    assert "background-color" not in body


def test_single_pseudo_set_for_simple_pseudo() -> None:
    css = ".btn:hover { background-color: blue; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:hover")
    assert rule.pseudo_set == frozenset({":hover"})


def test_base_rule_has_empty_pseudo_set() -> None:
    css = ".btn { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert rule.pseudo_set == frozenset()


# ---------------------------------------------------------------------------
# Subcontrol selectors: ::item, ::handle, etc.
# ---------------------------------------------------------------------------


def test_subcontrol_base_rule_includes_subcontrol_in_base_selector() -> None:
    """::item must stay in base_selector, not be treated as a pseudo-class."""
    css = ".list-view::item { background-color: transparent; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".list-view::item")
    assert rule.base_selector == ".list-view::item"
    assert rule.pseudo_set == frozenset()


def test_subcontrol_hover_rule_base_selector_includes_subcontrol() -> None:
    css = ".list-view::item:hover { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".list-view::item:hover")
    assert rule.base_selector == ".list-view::item"
    assert ":hover" in rule.pseudo_set


def test_subcontrol_rule_flagged_as_subcontrol() -> None:
    css = ".list-view::item { background-color: transparent; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".list-view::item")
    assert rule.subcontrol is True


def test_non_subcontrol_rule_not_flagged() -> None:
    css = ".btn:hover { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:hover")
    assert rule.subcontrol is False


def test_subcontrol_transitions_zeroed_out() -> None:
    """transition: on a subcontrol rule must be ignored — subcontrols are not real widgets."""
    css = ".list-view::item { background-color: transparent; transition: background 0.3s ease; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".list-view::item")
    assert rule.transitions == []


def test_subcontrol_hover_rule_transitions_zeroed_out() -> None:
    css = ".list-view::item:hover { background-color: red; transition: background 0.3s; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".list-view::item:hover")
    assert rule.transitions == []


def test_subcontrol_hover_props_not_stripped_from_cleaned_qss() -> None:
    """
    The ::item:hover background-color must survive the second pass unchanged.

    Without subcontrol support, the engine would strip it thinking it could
    animate it — but subcontrol items are not real widgets.
    """
    css = """
    .list-view::item { background-color: transparent; transition: background 0.3s ease; }
    .list-view::item:hover { background-color: red; }
    """
    cleaned, _ = extract_rules(css)
    hover_body = cleaned_block(cleaned, ".list-view::item:hover")
    assert "background-color" in hover_body
    assert "red" in hover_body


def test_subcontrol_base_props_preserved_in_cleaned_qss() -> None:
    css = """
    .list-view::item { background-color: transparent; transition: background 0.3s ease; }
    .list-view::item:hover { background-color: red; }
    """
    cleaned, _ = extract_rules(css)
    base_body = cleaned_block(cleaned, ".list-view::item {")
    assert "background-color" in base_body
    assert "transparent" in base_body


def test_subcontrol_transition_stripped_from_cleaned_qss() -> None:
    """transition: lines must still be removed from subcontrol rules (Qt ignores them anyway)."""
    css = ".list-view::item { background-color: transparent; transition: background 0.3s ease; }"
    cleaned, _ = extract_rules(css)
    assert "transition" not in cleaned


def test_subcontrol_segments_include_subcontrol_part() -> None:
    css = ".popup .list-view::item { background-color: transparent; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".popup .list-view::item")
    assert rule.segments == [".popup", ".list-view::item"]


# ---------------------------------------------------------------------------
# cubic-bezier easing
# ---------------------------------------------------------------------------


def test_cubic_bezier_easing_stored_in_transition_spec() -> None:
    css = ".btn { transition: background-color 300ms cubic-bezier(0.4, 0, 0.2, 1); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert "cubic-bezier" in t.easing


def test_cubic_bezier_four_values_preserved() -> None:
    import re

    css = ".btn { transition: color 200ms cubic-bezier(0.25, 0.1, 0.25, 1.0); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    m = re.search(r"cubic-bezier\(([^)]+)\)", t.easing)
    assert m is not None
    nums = [float(x.strip()) for x in m.group(1).split(",")]
    assert nums == pytest.approx([0.25, 0.1, 0.25, 1.0])


def test_cubic_bezier_y_value_above_one_preserved() -> None:
    """CSS allows y values outside [0, 1] for overshoot/bounce effects."""
    import re

    css = ".btn { transition: height 400ms cubic-bezier(0.34, 1.56, 0.64, 1); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    m = re.search(r"cubic-bezier\(([^)]+)\)", t.easing)
    assert m is not None
    nums = [float(x.strip()) for x in m.group(1).split(",")]
    assert nums == pytest.approx([0.34, 1.56, 0.64, 1.0])


def test_cubic_bezier_all_zeros_and_ones() -> None:
    css = ".btn { transition: opacity 500ms cubic-bezier(0, 0, 1, 1); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert "cubic-bezier" in t.easing


def test_cubic_bezier_in_comma_shorthand() -> None:
    css = ".btn { transition: background-color 300ms ease, color 200ms cubic-bezier(0.4, 0, 0.2, 1); }"
    _, rules = extract_rules(css)
    trans = {t.prop: t for t in get_rule(rules, ".btn").transitions}
    assert trans["background-color"].easing == "ease"
    assert "cubic-bezier" in trans["color"].easing


def test_cubic_bezier_duration_in_seconds() -> None:
    css = ".btn { transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert t.duration_ms == 300
    assert "cubic-bezier" in t.easing


# ---------------------------------------------------------------------------
# steps() easing
# ---------------------------------------------------------------------------


def test_steps_easing_stored_in_transition_spec() -> None:
    css = ".btn { transition: background-color 300ms steps(4); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert "steps" in t.easing


def test_steps_with_jump_start_stored() -> None:
    css = ".btn { transition: color 200ms steps(3, jump-start); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert "steps" in t.easing
    assert "jump-start" in t.easing


def test_steps_with_jump_end_stored() -> None:
    css = ".btn { transition: color 200ms steps(3, jump-end); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert "steps" in t.easing
    assert "jump-end" in t.easing


def test_steps_with_jump_none_stored() -> None:
    css = ".btn { transition: width 400ms steps(5, jump-none); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert "steps" in t.easing
    assert "jump-none" in t.easing


def test_steps_with_jump_both_stored() -> None:
    css = ".btn { transition: height 400ms steps(5, jump-both); }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert "steps" in t.easing
    assert "jump-both" in t.easing


def test_step_start_ident_stored() -> None:
    css = ".btn { transition: opacity 200ms step-start; }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert t.easing == "step-start"


def test_step_end_ident_stored() -> None:
    css = ".btn { transition: opacity 200ms step-end; }"
    _, rules = extract_rules(css)
    t = get_rule(rules, ".btn").transitions[0]
    assert t.easing == "step-end"


def test_steps_in_comma_shorthand() -> None:
    css = ".btn { transition: background-color 300ms ease, color 200ms steps(4, jump-start); }"
    _, rules = extract_rules(css)
    trans = {t.prop: t for t in get_rule(rules, ".btn").transitions}
    assert trans["background-color"].easing == "ease"
    assert "steps" in trans["color"].easing


def test_steps_longhand_timing_function() -> None:
    css = ".box { transition-property: opacity; transition-duration: 300ms; transition-timing-function: steps(4, jump-end); }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert "steps" in t.easing
    assert "jump-end" in t.easing


def test_subcontrol_real_world_example() -> None:
    """The motivating use-case: .popup .results-list-view::item with hover styling."""
    css = """
    .quick-launch-popup .results-list-view::item {
        background-color: transparent;
        transition: background 0.3s ease
    }

    .quick-launch-popup .results-list-view::item:hover {
        background-color: rgba(128, 130, 158, 0.1);
    }
    """
    cleaned, rules = extract_rules(css)

    base_rule = get_rule(rules, ".quick-launch-popup .results-list-view::item")
    assert base_rule.subcontrol is True
    assert base_rule.transitions == []
    assert base_rule.pseudo_set == frozenset()

    hover_rule = get_rule(rules, ".quick-launch-popup .results-list-view::item:hover")
    assert hover_rule.subcontrol is True
    assert ":hover" in hover_rule.pseudo_set
    assert hover_rule.base_selector == ".quick-launch-popup .results-list-view::item"

    # Both blocks must appear intact in cleaned QSS; only transition: is stripped
    assert "transition" not in cleaned
    assert "transparent" in cleaned_block(cleaned, ".quick-launch-popup .results-list-view::item {")
    hover_body = cleaned_block(cleaned, ".quick-launch-popup .results-list-view::item:hover")
    assert "rgba(128, 130, 158, 0.1)" in hover_body


# ---------------------------------------------------------------------------
# cursor property
# ---------------------------------------------------------------------------


def test_cursor_parsed_into_properties() -> None:
    css = "QPushButton { background-color: steelblue; cursor: pointer; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, "QPushButton")
    assert rule.properties.get("cursor") == "pointer"


def test_cursor_stripped_from_cleaned_qss() -> None:
    """cursor is engine-managed; Qt QSS doesn't know it and would warn."""
    css = "QPushButton { background-color: steelblue; cursor: pointer; }"
    cleaned, _ = extract_rules(css)
    assert "cursor" not in cleaned


def test_cursor_on_pseudo_parsed_and_stripped() -> None:
    css = "QPushButton:hover { background-color: royalblue; cursor: pointer; }"
    cleaned, rules = extract_rules(css)
    rule = get_rule(rules, "QPushButton:hover")
    assert rule.properties.get("cursor") == "pointer"
    assert "cursor" not in cleaned


def test_cursor_alongside_transition_stripped() -> None:
    """cursor must be stripped even when the block also contains a transition."""
    css = """
        QPushButton { background-color: steelblue; transition: background-color 200ms; }
        QPushButton:hover { background-color: royalblue; cursor: pointer; }
    """
    cleaned, _ = extract_rules(css)
    assert "cursor" not in cleaned


# ---------------------------------------------------------------------------
# transition-* longhand properties
# ---------------------------------------------------------------------------


def test_transition_property_and_duration_longhands() -> None:
    css = ".btn { background-color: steelblue; transition-property: background-color; transition-duration: 300ms; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn")
    assert len(rule.transitions) == 1
    t = rule.transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert t.easing == "ease"
    assert t.delay_ms == 0


def test_transition_longhands_all_four() -> None:
    css = """
    .box {
        transition-property: width;
        transition-duration: 400ms;
        transition-timing-function: ease-in-out;
        transition-delay: 100ms;
    }
    """
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    assert len(rule.transitions) == 1
    t = rule.transitions[0]
    assert t.prop == "width"
    assert t.duration_ms == 400
    assert t.easing == "ease-in-out"
    assert t.delay_ms == 100


def test_transition_longhands_multiple_properties_cycling_duration() -> None:
    """CSS cycling: shorter lists repeat. One duration shared by all properties."""
    css = """
    .box {
        transition-property: color, background-color, width;
        transition-duration: 200ms;
    }
    """
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    props = {t.prop: t for t in rule.transitions}
    assert set(props) == {"color", "background-color", "width"}
    assert all(t.duration_ms == 200 for t in props.values())


def test_transition_longhands_multiple_properties_multiple_durations() -> None:
    css = """
    .box {
        transition-property: color, width;
        transition-duration: 200ms, 500ms;
        transition-timing-function: linear, ease-in;
        transition-delay: 0ms, 100ms;
    }
    """
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    props = {t.prop: t for t in rule.transitions}
    assert props["color"].duration_ms == 200
    assert props["color"].easing == "linear"
    assert props["color"].delay_ms == 0
    assert props["width"].duration_ms == 500
    assert props["width"].easing == "ease-in"
    assert props["width"].delay_ms == 100


def test_transition_longhands_duration_in_seconds() -> None:
    css = ".box { transition-property: opacity; transition-duration: 0.5s; }"
    _, rules = extract_rules(css)
    assert rules[0].transitions[0].duration_ms == 500


def test_transition_longhands_delay_in_seconds() -> None:
    css = ".box { transition-property: opacity; transition-duration: 200ms; transition-delay: 0.1s; }"
    _, rules = extract_rules(css)
    assert rules[0].transitions[0].delay_ms == 100


def test_transition_property_none_produces_no_transitions() -> None:
    css = ".box { transition-property: none; transition-duration: 300ms; }"
    _, rules = extract_rules(css)
    assert rules[0].transitions == []


def test_transition_longhands_expand_shorthand_prop() -> None:
    """transition-property: border-color expands to four longhands."""
    css = ".box { transition-property: border-color; transition-duration: 200ms; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    props = {t.prop for t in rule.transitions}
    assert props == {"border-top-color", "border-right-color", "border-bottom-color", "border-left-color"}
    assert all(t.duration_ms == 200 for t in rule.transitions)


def test_transition_longhands_override_shorthand() -> None:
    """Longhands declared after shorthand override the shorthand result."""
    css = """
    .box {
        transition: background-color 100ms;
        transition-property: width;
        transition-duration: 400ms;
    }
    """
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".box")
    props = {t.prop for t in rule.transitions}
    assert props == {"width"}
    assert rule.transitions[0].duration_ms == 400


def test_transition_longhands_stripped_from_cleaned_qss() -> None:
    css = """
    .btn { color: white; transition-property: color; transition-duration: 200ms; }
    .btn:hover { color: black; }
    """
    cleaned, _ = extract_rules(css)
    assert "transition-property" not in cleaned
    assert "transition-duration" not in cleaned


def test_transition_longhands_timing_function_cubic_bezier() -> None:
    css = ".box { transition-property: opacity; transition-duration: 300ms; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.easing.startswith("cubic-bezier")


# ---------------------------------------------------------------------------
# transition-behavior (ignored, not corrupting)
# ---------------------------------------------------------------------------


def test_transition_behavior_in_shorthand_does_not_corrupt_easing() -> None:
    """allow-discrete in shorthand must not overwrite the easing function."""
    css = ".box { transition: color 200ms ease allow-discrete; }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.easing == "ease"
    assert t.duration_ms == 200


def test_transition_behavior_normal_in_shorthand_ignored() -> None:
    css = ".box { transition: opacity 300ms linear normal; }"
    _, rules = extract_rules(css)
    t = rules[0].transitions[0]
    assert t.easing == "linear"


def test_transition_behavior_longhand_stripped_from_cleaned_qss() -> None:
    css = ".box { color: red; transition: color 200ms; transition-behavior: allow-discrete; }"
    cleaned, _ = extract_rules(css)
    assert "transition-behavior" not in cleaned


# ---------------------------------------------------------------------------
# :clicked pseudo-class — parser
# ---------------------------------------------------------------------------


def test_clicked_pseudo_recognised_in_pseudo_set() -> None:
    css = ".btn { background-color: blue; } .btn:clicked { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:clicked")
    assert ":clicked" in rule.pseudo_set


def test_clicked_base_selector_stripped_of_pseudo() -> None:
    css = ".btn:clicked { background-color: red; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:clicked")
    assert rule.base_selector == ".btn"


def test_clicked_transition_extracted() -> None:
    css = ".btn { background-color: blue; transition: background-color 400ms ease; } .btn:clicked { background-color: red; }"
    _, rules = extract_rules(css)
    base = get_rule(rules, ".btn")
    assert any(t.prop == "background-color" and t.duration_ms == 400 for t in base.transitions)


def test_clicked_transition_in_pseudo_block_extracted() -> None:
    css = ".btn { background-color: blue; } .btn:clicked { background-color: red; transition: background-color 300ms ease-in; }"
    _, rules = extract_rules(css)
    rule = get_rule(rules, ".btn:clicked")
    assert len(rule.transitions) == 1
    t = rule.transitions[0]
    assert t.prop == "background-color"
    assert t.duration_ms == 300
    assert t.easing == "ease-in"


def test_clicked_props_stripped_from_cleaned_qss_when_animated() -> None:
    """background-color in :clicked block must be stripped so Qt doesn't apply it statically."""
    css = """
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """
    cleaned, _ = extract_rules(css)
    clicked_block = cleaned_block(cleaned, ".btn:clicked")
    assert "background-color" not in clicked_block


def test_clicked_base_block_not_stripped() -> None:
    """background-color in base block must survive — engine reads it as the 'from' value."""
    css = """
        .btn { background-color: blue; transition: background-color 300ms; }
        .btn:clicked { background-color: red; }
    """
    cleaned, _ = extract_rules(css)
    base_block = cleaned_block(cleaned, ".btn")
    assert "background-color" in base_block


def test_clicked_and_hover_both_in_same_stylesheet() -> None:
    """Parser must produce separate rules for :clicked and :hover on the same selector."""
    css = """
        .btn { background-color: blue; transition: background-color 200ms; }
        .btn:hover { background-color: green; }
        .btn:clicked { background-color: red; }
    """
    _, rules = extract_rules(css)
    get_rule(rules, ".btn:hover")  # must exist
    get_rule(rules, ".btn:clicked")  # must exist
    clicked = get_rule(rules, ".btn:clicked")
    assert ":clicked" in clicked.pseudo_set
    assert ":hover" not in clicked.pseudo_set


def test_clicked_not_confused_with_subcontrol() -> None:
    """::clicked (double-colon) is a subcontrol, not the :clicked pseudo — must not match."""
    css = ".btn::clicked { color: red; }"
    _, rules = extract_rules(css)
    rule = rules[0]
    assert rule.subcontrol is True
    assert ":clicked" not in rule.pseudo_set
