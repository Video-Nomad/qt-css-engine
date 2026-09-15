# Attribute selectors ([active=true]) — compiler, parser, matcher, cascade, routing.
#
# Qt QSS supports dynamic-property attribute selectors widely
# (e.g. `.item[active=true]` with `setProperty("active", True)`).
# The engine must parse, match, cascade and re-evaluate them.

from qt_css_engine import TransitionEngine
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.css.model import StyleRule
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.engine.event_router import EventRouter
from qt_css_engine.matching.compiler import (
    attr_matches,
    compile_segment,
    parse_attr_conditions,
    stringify_property,
)
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtCore import QCoreApplication
from qt_css_engine.qt_compat.QtWidgets import QApplication, QWidget


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def make_matcher(css: str) -> tuple[RuleMatcher, list[StyleRule]]:
    _, rules = extract_rules(css)
    return RuleMatcher(rules), rules


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def test_parse_single_attr_condition() -> None:
    (cond,) = parse_attr_conditions(".item[active=true]")
    assert cond.name == "active"
    assert cond.op == "="
    assert cond.value == "true"


def test_parse_quoted_attr_values() -> None:
    (cond,) = parse_attr_conditions('.item[state="selected"]')
    assert (cond.name, cond.op, cond.value) == ("state", "=", "selected")
    (cond2,) = parse_attr_conditions(".item[state='selected']")
    assert (cond2.name, cond2.op, cond2.value) == ("state", "=", "selected")


def test_parse_existence_attr() -> None:
    (cond,) = parse_attr_conditions(".item[active]")
    assert (cond.name, cond.op, cond.value) == ("active", "exists", None)


def test_parse_multiple_attrs() -> None:
    conds = parse_attr_conditions('.item[a=1][b="x"]')
    assert [(c.name, c.op, c.value) for c in conds] == [("a", "=", "1"), ("b", "=", "x")]


def test_compile_segment_strips_attrs_from_structural_parts() -> None:
    seg = compile_segment(".item[active=true]")
    assert seg.classes == frozenset({"item"})
    assert seg.tag is None
    assert seg.obj_name is None
    assert len(seg.attrs) == 1


def test_compile_segment_tag_and_id_with_attrs() -> None:
    tag_seg = compile_segment("QPushButton[flat=true]")
    assert tag_seg.tag == "QPushButton"
    assert tag_seg.classes == frozenset()
    id_seg = compile_segment("#ok[enabled=true].primary")
    assert id_seg.obj_name == "ok"
    assert id_seg.classes == frozenset({"primary"})


def test_stringify_property_bool_int_str() -> None:
    assert stringify_property(True) == "true"
    assert stringify_property(False) == "false"
    assert stringify_property(None) is None
    assert stringify_property(1) == "1"
    assert stringify_property("selected") == "selected"


def test_attr_matches_value_and_existence() -> None:
    (eq,) = parse_attr_conditions("[active=true]")
    assert attr_matches(True, eq) is True
    assert attr_matches("true", eq) is True
    assert attr_matches(False, eq) is False
    assert attr_matches(None, eq) is False
    (exists,) = parse_attr_conditions("[active]")
    assert attr_matches(True, exists) is True
    assert attr_matches(False, exists) is True  # exists = property present
    assert attr_matches(None, exists) is False


def test_attr_matches_unsupported_operator_never_matches() -> None:
    (cond,) = parse_attr_conditions("[state~=selected]")
    assert attr_matches("selected", cond) is False


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_marks_attr_rules() -> None:
    _, rules = extract_rules(".a { color: red; } .a[x=1] { color: blue; }")
    by_selector = {r.selector: r for r in rules}
    assert by_selector[".a"].has_attrs is False
    assert by_selector[".a[x=1]"].has_attrs is True


def test_parser_strips_animated_prop_from_attr_block_keeps_rest() -> None:
    cleaned, _ = extract_rules("""
    .item { min-height: 35px; transition: background 300ms ease-out; }
    .item[active=true] { background: red; font-size: 14px; min-height: 80px; }
    """)
    attr_body = cleaned[cleaned.index(".item[active=true]") :]
    assert "background" not in attr_body.split("}")[0]
    assert "font-size: 14px" in attr_body
    assert "min-height: 80px" in attr_body


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def test_matcher_structural_match_includes_attr_rule(_app: QApplication) -> None:
    matcher, rules = make_matcher("""
    .menu .item { min-height: 35px; }
    .menu .item[active=true] { background: red; }
    """)
    parent = QWidget()
    parent.setProperty("class", "menu")
    child = QWidget(parent)
    child.setProperty("class", "item")
    # Structural candidates include the attr rule even while inactive (like :hover).
    assert {r.selector for r in matcher.matching_rules(child)} == {
        ".menu .item",
        ".menu .item[active=true]",
    }
    assert matcher.matches(child, rules[1]) is False
    child.setProperty("active", True)
    assert matcher.matches(child, rules[1]) is True
    assert matcher.rule_attrs_match(child, rules[1]) is True


def test_matcher_tracks_attr_names() -> None:
    matcher, _ = make_matcher(".a[active=1] { color: red; } .b { color: blue; }")
    assert matcher.tracked_attrs == {"active"}


def test_matcher_ancestor_attr_gates_rule(_app: QApplication) -> None:
    matcher, rules = make_matcher(".parent[level=1] .child { color: red; }")
    parent = QWidget()
    parent.setProperty("class", "parent")
    child = QWidget(parent)
    child.setProperty("class", "child")
    assert matcher.matches(child, rules[0]) is False
    parent.setProperty("level", 1)
    assert matcher.matches(child, rules[0]) is True


def test_matcher_quoted_and_int_values(_app: QApplication) -> None:
    matcher, rules = make_matcher('.a[state="on"] { color: red; } .b[count=2] { color: blue; }')
    a = QWidget()
    a.setProperty("class", "a")
    a.setProperty("state", "on")
    assert matcher.matches(a, rules[0]) is True
    a.setProperty("state", "off")
    assert matcher.matches(a, rules[0]) is False
    b = QWidget()
    b.setProperty("class", "b")
    b.setProperty("count", 2)
    assert matcher.matches(b, rules[1]) is True


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


def test_cascade_target_follows_attr_state(_app: QApplication) -> None:
    engine = make_engine("""
    .menu .item { min-height: 35px; transition: background 300ms ease-out; }
    .menu .item[active=true] { background: red; font-size: 14px; }
    """)
    parent = QWidget()
    parent.setProperty("class", "menu")
    child = QWidget(parent)
    child.setProperty("class", "item")
    ctx = engine.get_context(child)

    inactive = engine.cascade.collect(child, ctx)
    assert "background-color" not in inactive.target_props
    # Attr-conditional rules are target-only: the resting base must not move.
    assert "background-color" not in inactive.base_props

    child.setProperty("active", True)
    active = engine.cascade.collect(child, ctx)
    assert active.target_props["background-color"] == "red"
    assert active.target_props["font-size"] == "14px"
    assert "background-color" not in active.base_props
    assert active.transitions["background-color"].duration_ms == 300


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


def test_event_router_routes_tracked_attr_change(_app: QApplication) -> None:
    engine = make_engine(".a[active=true] { background: red; }")
    calls: list[str] = []
    engine.on_attr_change = lambda w: calls.append("attr")  # type: ignore[method-assign]
    engine.on_class_change = lambda w: calls.append("class")  # type: ignore[method-assign]
    widget = QWidget()

    class _FakeName:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def data(self) -> bytes:
            return self._raw

    class _FakeEvent:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def propertyName(self) -> _FakeName:
            return _FakeName(self._raw)

    from qt_css_engine.qt_compat.QtCore import QEvent

    assert EventRouter.changed_property_name(_FakeEvent(b"active")) == "active"  # type: ignore[arg-type]
    EventRouter.dispatch(engine, widget, _FakeEvent(b"active"), QEvent.Type.DynamicPropertyChange)  # type: ignore[arg-type]
    EventRouter.dispatch(engine, widget, _FakeEvent(b"class"), QEvent.Type.DynamicPropertyChange)  # type: ignore[arg-type]
    EventRouter.dispatch(engine, widget, _FakeEvent(b"other"), QEvent.Type.DynamicPropertyChange)  # type: ignore[arg-type]
    assert calls == ["attr", "class"]


# ---------------------------------------------------------------------------
# End to end: setProperty drives the transition through the real event filter
# ---------------------------------------------------------------------------


def test_attr_change_animates_background_end_to_end(_app: QApplication) -> None:
    engine = make_engine("""
    .menu .item { min-height: 35px; transition: background 300ms ease-out; }
    .menu .item[active=true] { background: red; }
    """)
    _app.installEventFilter(engine)
    try:
        parent = QWidget()
        parent.setProperty("class", "menu")
        child = QWidget(parent)
        child.setProperty("class", "item")
        engine.evaluate_widget_state(child, cause=EvaluationCause.POLISH)
        ctx = engine.get_context(child)

        child.setProperty("active", True)
        QCoreApplication.processEvents()
        anim = ctx.active_animations.get("background-color")
        assert isinstance(anim, ColorAnimation)
        assert anim.end_color.name() == "#ff0000"

        child.setProperty("active", False)
        QCoreApplication.processEvents()
        anim = ctx.active_animations.get("background-color")
        assert isinstance(anim, ColorAnimation)
        assert anim.end_color.name() == "#000000"  # transparent fallback
    finally:
        _app.removeEventFilter(engine)
